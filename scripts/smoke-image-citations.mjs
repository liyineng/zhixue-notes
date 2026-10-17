import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

const modules = process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules");
const { chromium } = await import(pathToFileURL(path.join(modules, "playwright/index.mjs")).href);
const python = process.env.PYTHON || path.resolve(".venv/bin/python");
const base = process.env.APP_URL || "http://127.0.0.1:8765";
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();
const api = async (url, method = "GET", data) => {
  const response = await context.request.fetch(base + url, { method, data });
  assert(response.ok(), `${method} ${url}: ${response.status()} ${await response.text()}`);
  return response.json();
};
const notebook = (await api("/api/notebooks", "POST", { name: "图片与颜色验收" })).notebook;
let documentId;
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  const fixtures = JSON.parse(execFileSync(python, ["-c", `
import pymupdf,json,base64
doc=pymupdf.open();page=doc.new_page(width=612,height=792)
page.insert_text((72,120),"FIRST: network protocol defines data exchange rules.",fontsize=14)
page.insert_text((72,230),"SECOND: network layers reduce complexity.",fontsize=14)
question=pymupdf.open();p=question.new_page(width=900,height=220)
p.insert_text((36,90),"What is a network protocol?",fontsize=32)
print(json.dumps({"pdf":base64.b64encode(doc.tobytes()).decode(),"png":base64.b64encode(p.get_pixmap().tobytes("png")).decode()}))
`], { encoding: "utf8" }));
  const uploaded = await context.request.post(base + `/api/documents?notebook_id=${notebook.id}`, {
    multipart: { file: { name: "课程依据.pdf", mimeType: "application/pdf", buffer: Buffer.from(fixtures.pdf, "base64") } },
  });
  assert(uploaded.ok());
  documentId = (await uploaded.json()).document.id;
  const chunks = (await api(`/api/documents/${documentId}/chunks`)).chunks;
  const chunk = chunks[0];
  const citations = ["FIRST:", "SECOND:"].map((prefix, i) => ({
    index: i + 1, chunk_id: chunk.id, document_id: documentId, document_name: "课程依据.pdf", document_kind: "pdf", page: 1,
    text: chunk.lines.find((line) => line.text.startsWith(prefix)).text,
    lines: chunk.lines.filter((line) => line.text.startsWith(prefix)), score: 1,
  }));
  citations.push({ ...citations[0], index: 3 });
  await page.goto(base, { waitUntil: "networkidle" });
  await page.locator("#notebook-select").selectOption(notebook.id);
  await page.waitForFunction(() => !document.querySelector("#notebook-select").disabled);
  const imageResponse = page.waitForResponse((response) => response.url().includes("/api/question-images?") && response.request().method() === "POST");
  await page.locator("#question-image-input").setInputFiles({ name: "题目.png", mimeType: "image/png", buffer: Buffer.from(fixtures.png, "base64") });
  const image = (await (await imageResponse).json()).image;
  assert.match(image.ocr_text.toLowerCase(), /network protocol/);
  await page.waitForFunction(() => !document.querySelector("#send-button").disabled);
  assert.equal(await page.locator(".draft-image").count(), 1);
  await page.locator("#question-image-draft summary").click();
  await page.locator("#question-image-text").fill("What does a network protocol specify?");
  await page.route("**/api/ask", async (route) => {
    const payload = route.request().postDataJSON();
    assert.equal(payload.question, "");
    assert.deepEqual(payload.image_ids, [image.id]);
    assert.equal(payload.notebook_id, notebook.id);
    assert.match(payload.image_text, /protocol/);
    const result = { answer: "- 协议定义数据交换规则 [1]。\n- 分层降低复杂性 [2]。\n- 规则也约束通信双方 [3]。\n\n## 最终结果\n\n| 主机 | B1 端口 | B2 端口 |\n|---|---|---|\n| **H1** | 1 | 1 |\n| H5 | 2 | 2 |\n\n1. 学习源地址\n2. 查目的地址\n\n`[99]` <script>throw Error(1)</script>", citations, mode: "model", source_mode: "knowledge_base", image_question_text: payload.image_text, image_recognition_mode: "manual" };
    execFileSync(python, ["-c", `
import sys,json
from uuid import uuid4
from app import db
d=json.load(sys.stdin);i=uuid4().hex
db.start_exchange(i,d["notebook_id"],"请解答图片中的题目。",d["images"])
db.finish_exchange(i,d["result"])
`], { input: JSON.stringify({ notebook_id: notebook.id, images: [image], result }) });
    await route.fulfill({ json: result });
  });
  await page.locator("#question-input").press("Enter");
  const refs = page.locator(".message.assistant .message-body > ul .citation-ref");
  await refs.nth(1).waitFor();
  assert.equal(await page.locator('.message.assistant table tbody tr').count(), 2);
  assert.equal(await page.locator('.message.assistant ol li').count(), 2);
  assert.equal(await page.locator('.message.assistant .citation-ref[data-citation="99"]').count(), 0);
  assert.equal(await page.locator('.message.assistant script').count(), 0);
  const colors = await refs.evaluateAll((buttons) => buttons.map((button) => getComputedStyle(button).color));
  assert.notEqual(colors[0], colors[1]);
  await refs.first().click();
  await page.locator(".highlight-box").nth(1).waitFor();
  assert.equal(await page.locator("#page-input").inputValue(), "1");
  assert.equal(await page.locator("#highlight-legend button").count(), 3);
  const highlights = await page.locator(".highlight-box").evaluateAll((boxes) => boxes.map((box) => ({ color: box.style.getPropertyValue("--citation-color"), ids: box.dataset.citations })));
  assert.deepEqual(highlights.map((h) => h.ids).sort(), ["1,3", "2"]);
  assert.equal(await page.locator('.highlight-box[data-citations="1,3"] .highlight-label').count(), 2);
  assert.match(await page.locator('.highlight-box[data-citations="1,3"]').evaluate((el) => el.style.background), /gradient/);
  assert.notEqual(highlights[0].color, highlights[1].color);
  await refs.nth(1).click();
  assert.match(await page.locator("#source-text").innerText(), /\[2\]/);
  await page.waitForFunction(() => document.querySelector('#highlight-legend button[aria-pressed="true"]')?.textContent === "[2]");
  await fs.mkdir("artifacts/image-citations", { recursive: true });
  await page.screenshot({ path: "artifacts/image-citations/desktop.png", fullPage: true });
  await page.reload({ waitUntil: "networkidle" });
  await page.locator(".message.user img").waitFor();
  assert.equal(await page.locator(".message.user img").getAttribute("src"), image.url);
  assert.match(await page.locator(".image-recognition").innerText(), /已校对/);
  await page.locator(".message.assistant .citation-ref").nth(1).click();
  await page.locator(".highlight-box").nth(1).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.mobile-tab[data-pane="chat"]').click();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: "artifacts/image-citations/mobile.png", fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, localOCR: true, imageOnlyQuestion: true, editableText: true, restoredImage: true, samePageCitationColors: true, errors }));
} finally {
  await api(`/api/notebooks/${notebook.id}/chat`, "DELETE");
  if (documentId) await api(`/api/documents/${documentId}`, "DELETE");
  await api(`/api/notebooks/${notebook.id}`, "DELETE");
  await browser.close();
}
