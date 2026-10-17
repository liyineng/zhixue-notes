import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { fileURLToPath, pathToFileURL } from "node:url";

const runtimeRoot =
  process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules");
const { chromium } = await import(
  pathToFileURL(`${runtimeRoot}/playwright/index.mjs`).href
);

const baseUrl = process.env.APP_URL || "http://127.0.0.1:8765";
const outputDir = fileURLToPath(new URL("../artifacts/", import.meta.url));
await fs.mkdir(outputDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(`console: ${message.text()}`);
});

await page.goto(baseUrl, { waitUntil: "networkidle" });
await page.locator(".document-item").first().waitFor({ timeout: 15000 });
await page.screenshot({
  path: path.join(outputDir, "ui-01-home.png"),
  fullPage: true,
});

await page.locator("#question-input").fill("网络体系结构是什么？");
await page.locator("#ask-form").evaluate((form) => form.requestSubmit());
await page.locator(".message.assistant .citation-ref").first().waitFor({
  timeout: 20000,
});
await page.screenshot({
  path: path.join(outputDir, "ui-02-answer.png"),
  fullPage: true,
});

await page.locator(".message.assistant .citation-ref").first().click();
await page.locator("#viewer-scroll:not([hidden])").waitFor({ timeout: 15000 });
await page.locator(".highlight-box").first().waitFor({ timeout: 20000 });
await page.waitForTimeout(800);
await page.screenshot({
  path: path.join(outputDir, "ui-03-highlight.png"),
  fullPage: true,
});

const evidence = {
  documentCount: await page.locator(".document-item").count(),
  citationCount: await page.locator(".citation-card").count(),
  highlightCount: await page.locator(".highlight-box").count(),
  pageNumber: await page.locator("#page-input").inputValue(),
  viewerVisible: await page.locator("#viewer-scroll").isVisible(),
  errors,
};

await page.setViewportSize({ width: 390, height: 844 });
await page.screenshot({
  path: path.join(outputDir, "ui-04-mobile.png"),
  fullPage: true,
});

console.log(JSON.stringify(evidence, null, 2));
await browser.close();
