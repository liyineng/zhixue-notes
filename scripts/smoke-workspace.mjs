import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

const modules = process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules");
const { chromium } = await import(pathToFileURL(path.join(modules, "playwright/index.mjs")).href);
const base = process.env.APP_URL || "http://127.0.0.1:8765";
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();
const errors = [];
const createdNotebooks = [];
const createdDocuments = [];
let pdfDocument;
const api = async (url, method = "GET", data) => {
  const response = await context.request.fetch(base + url, { method, data });
  assert(response.ok(), `${method} ${url}: ${response.status()} ${await response.text()}`);
  return response.json();
};
const output = path.resolve("artifacts/workspace");
await fs.mkdir(output, { recursive: true });
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });

try {
  await page.goto(base, { waitUntil: "networkidle" });
  await page.locator("#notebook-select option").first().waitFor({ state: "attached" });
  page.once("dialog", (dialog) => dialog.accept("UI验收·网络课程"));
  await page.locator("#new-notebook").click();
  await page.waitForFunction(() => document.querySelector("#notebook-title").textContent === "UI验收·网络课程");
  const notebookId = await page.locator("#notebook-select").inputValue();
  createdNotebooks.push(notebookId);
  await page.locator("#file-input").setInputFiles({ name: "验收资料.md", mimeType: "text/markdown", buffer: Buffer.from("# 网络体系结构\n\n网络分层可以降低网络设计的复杂性。协议定义数据交换的规则。\n\n蓝色雨伞是这份资料的独有测试关键词。") });
  await page.locator(".document-item").first().waitFor();
  const docs = (await api(`/api/documents?notebook_id=${notebookId}`)).documents;
  createdDocuments.push(...docs.map((doc) => doc.id));
  await page.locator("#source-search").fill("蓝色雨伞");
  await page.locator("#source-search-form").evaluate((form) => form.requestSubmit());
  await page.locator(".source-result").first().waitFor();
  await page.locator(".source-result").first().click();
  await page.waitForFunction(() => document.querySelector("#source-text").textContent.includes("蓝色雨伞"));
  await page.locator("#source-search").fill("");
  await page.locator("#select-all-sources").uncheck();
  assert.match(await page.locator("#selection-status").innerText(), /0 \/ 1/);
  await page.locator("#select-all-sources").check();

  const citation = (await api(`/api/sources/search?notebook_id=${notebookId}&q=网络分层`)).results[0];
  await page.route("**/api/ask", async (route) => {
    const payload = route.request().postDataJSON();
    assert.equal(payload.notebook_id, notebookId);
    assert.deepEqual(payload.document_ids, [docs[0].id]);
    await route.fulfill({ json: { answer: "【资料依据】\n网络分层可以降低网络设计的复杂性 [1]。", citations: [citation], mode: "model", source_mode: "knowledge_base" } });
  });
  await page.locator("#question-input").fill("网络分层有什么作用？");
  await page.locator("#ask-form").evaluate((form) => form.requestSubmit());
  await page.locator(".save-answer").click();
  await page.waitForFunction(() => document.querySelector(".save-answer").textContent === "已保存到笔记");
  await page.locator("#notes-tab").click();
  await page.locator(".note-item").first().click();
  await page.locator("#note-title").fill("网络分层复习笔记");
  await page.locator("#note-content").fill("分层降低复杂性 [1]。\n\n- 复习重点：协议与层次");
  const downloadEvent = page.waitForEvent("download");
  await page.locator("#export-note").click();
  const download = await downloadEvent;
  const downloaded = await fs.readFile(await download.path(), "utf8");
  assert.match(downloaded, /# 网络分层复习笔记/);
  assert.match(downloaded, /参考来源/);
  assert.match(downloaded, /验收资料.md/);
  await page.locator("#close-note").click();
  await page.locator(".note-item").first().click();
  await page.locator("#note-content").fill("尚未保存的修改");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#note-dialog").evaluate((dialog) => dialog.open), true);
  assert.equal(await page.locator("#note-content").inputValue(), "尚未保存的修改");
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#close-note").click();
  await page.reload({ waitUntil: "networkidle" });
  await page.locator("#notes-tab").click();
  await page.locator(".note-item").first().waitFor();
  assert.match(await page.locator(".note-item").innerText(), /网络分层复习笔记/);
  await page.screenshot({ path: path.join(output, "notes-desktop.png"), fullPage: true });

  const other = (await api("/api/notebooks", "POST", { name: "UI验收·空笔记本" })).notebook.id;
  createdNotebooks.push(other);
  await page.reload({ waitUntil: "networkidle" });
  await page.locator("#notebook-select").selectOption(other);
  await page.waitForFunction(() => document.querySelector("#notebook-title").textContent === "UI验收·空笔记本");
  await page.waitForFunction(() => document.querySelector("#notebook-select").disabled === false);
  assert.equal(await page.locator(".document-item").count(), 0);
  assert.equal(await page.locator(".note-item").count(), 0);
  await page.locator("#source-search").fill("蓝色雨伞");
  await page.locator("#source-search-form").evaluate((form) => form.requestSubmit());
  await page.waitForFunction(() => document.querySelector("#search-results").textContent.includes("没有匹配"));
  await page.locator("#notebook-select").selectOption(notebookId);
  await page.locator(".document-item").first().waitFor();
  await page.locator(".document-move").first().click();
  await page.locator("#move-target").selectOption(other);
  await page.locator("#move-form").evaluate((form) => form.requestSubmit());
  await page.waitForFunction(() => document.querySelectorAll(".document-item").length === 0);
  await page.locator("#notebook-select").selectOption(other);
  await page.locator(".document-item").first().waitFor();
  assert.match(await page.locator(".document-name").innerText(), /验收资料/);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, "mobile.png"), fullPage: true });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));

  // A PDF fixture catches both wrong-page labels and vertically mirrored highlight boxes.
  const pdfBytes = execFileSync(process.env.PYTHON || path.resolve(".venv/bin/python"), ["-c", `
import pymupdf,sys
doc=pymupdf.open()
page=doc.new_page(width=612,height=792)
page.insert_text((72,100),"Introduction only")
page=doc.new_page(width=612,height=792)
page.insert_text((72,100),"FOCUSPHRASE network protocol",fontsize=14)
sys.stdout.buffer.write(doc.tobytes())
`]);
  const uploaded = await context.request.post(base + `/api/documents?notebook_id=${other}`, {
    multipart: { file: { name: "定位回归.pdf", mimeType: "application/pdf", buffer: pdfBytes } },
  });
  assert(uploaded.ok());
  const fixture = (await uploaded.json()).document;
  createdDocuments.push(fixture.id);
  await page.reload({ waitUntil: "networkidle" });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator("#source-search").fill("FOCUSPHRASE");
  await page.locator("#source-search-form").evaluate((form) => form.requestSubmit());
  const exactResult = page.locator(".source-result").filter({ has: page.locator("span") }).first();
  await exactResult.waitFor();
  assert.match(await exactResult.innerText(), /第 2 页/);
  await exactResult.click();
  await page.locator(".highlight-box").first().waitFor();
  assert.equal(await page.locator("#page-input").inputValue(), "2");
  const highlightRatio = await page.locator(".highlight-box").first().evaluate((box) => parseFloat(box.style.top) / box.parentElement.clientHeight);
  assert(highlightRatio > 0.09 && highlightRatio < 0.15, `highlight top ratio: ${highlightRatio}`);

  // Exercise actual PDF search and jump/highlights, without calling a model.
  const defaultDocs = (await api("/api/documents?notebook_id=default")).documents;
  pdfDocument = defaultDocs.find((doc) => doc.kind === "pdf");
  if (pdfDocument) {
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.locator("#notebook-select").selectOption("default");
    await page.waitForFunction(() => document.querySelector("#notebook-select").disabled === false);
    await page.locator("#source-search").fill("网络体系结构");
    await page.locator("#source-search-form").evaluate((form) => form.requestSubmit());
    await page.locator(".source-result").first().waitFor();
    const pdfResult = page.locator(".source-result").filter({ has: page.locator("span") }).first();
    const expectedPage = (await pdfResult.innerText()).match(/第\s*(\d+)\s*页/)[1];
    await pdfResult.click();
    await page.locator(".highlight-box").first().waitFor({ timeout: 20000 });
    assert.equal(await page.locator("#page-input").inputValue(), expectedPage);
    assert.notEqual(await page.locator("#page-total").innerText(), "/ 0");
    assert(await page.locator("#pdf-canvas").evaluate((canvas) => canvas.width > 0));
    await page.screenshot({ path: path.join(output, "source-search.png"), fullPage: true });
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, notesPersisted: true, markdownWithCitations: true, notebookIsolation: true, sourceMove: true, pdfHighlight: !!pdfDocument, errors }));
} finally {
  for (const id of createdNotebooks) {
    const notes = (await api(`/api/notes?notebook_id=${id}`)).notes;
    for (const note of notes) await api(`/api/notes/${note.id}`, "DELETE");
  }
  for (const id of createdDocuments) await api(`/api/documents/${id}`, "DELETE");
  for (const id of createdNotebooks) await api(`/api/notebooks/${id}`, "DELETE");
  await browser.close();
}
