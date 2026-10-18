import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
const modules = process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();
const notebooks = [], errors = [];
page.on('pageerror', error => errors.push(error.message));
const api = async (url, method = 'GET', data) => {
  const response = await context.request.fetch(base + url, { method, data });
  assert(response.ok(), `${method} ${url}: ${response.status()}`);
  return response.json();
};
function seed(notebook, id, result, question = '阅读稳定性验收') {
  execFileSync(path.resolve('.venv/bin/python'), ['-c', `
import json,sys
from app import db
d=json.load(sys.stdin)
if not db.exchange_exists(d['id']): db.start_exchange(d['id'],d['notebook'],d['question'])
if d['result'] is not None: db.finish_pending_exchange(d['id'],d['result'])
`], { input: JSON.stringify({ notebook, id, question, result }) });
}
try {
  const notebook = (await api('/api/notebooks', 'POST', { name: '阅读与布局验收' })).notebook.id;
  const other = (await api('/api/notebooks', 'POST', { name: '勾选隔离验收' })).notebook.id;
  notebooks.push(notebook, other);
  const documents = [];
  for (const name of ['协议.md', '窗口.md']) {
    const response = await context.request.post(base + `/api/documents?notebook_id=${notebook}`, {
      multipart: { file: { name, mimeType: 'text/markdown', buffer: Buffer.from('网络协议规定通信规则，滑动窗口控制未确认的数据帧。') } },
    });
    assert(response.ok()); documents.push((await response.json()).document);
  }
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.locator('#notebook-select').selectOption(notebook);
  await page.waitForFunction(() => !document.querySelector('#notebook-select').disabled);
  const box = id => page.locator(`[data-document-id="${id}"] input[type="checkbox"]`);
  await box(documents[1].id).uncheck();
  await page.reload({ waitUntil: 'networkidle' });
  assert(await box(documents[0].id).isChecked());
  assert.equal(await box(documents[1].id).isChecked(), false);
  await page.locator('#select-all-sources').check();
  await page.locator('#select-all-sources').uncheck();
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.locator('.document-item input:checked').count(), 0);
  await page.route('**/api/ask', async route => {
    const payload = route.request().postDataJSON();
    assert.deepEqual(payload.document_ids, []);
    await route.fulfill({ json: { chat_id: payload.request_id, answer: '普通问答正常回复', citations: [], mode: 'model', source_mode: 'general_knowledge' } });
  });
  await page.locator('#question-input').fill('不使用资料时也能聊天吗');
  await page.locator('#question-input').press('Enter');
  await page.locator('.message.assistant').filter({ hasText: '普通问答正常回复' }).waitFor();
  await page.unroute('**/api/ask');
  await page.locator('#notebook-select').selectOption(other);
  await page.waitForFunction(() => !document.querySelector('#notebook-select').disabled);
  await page.locator('#notebook-select').selectOption(notebook);
  await page.waitForFunction(() => !document.querySelector('#notebook-select').disabled);
  assert.equal(await page.locator('.document-item input:checked').count(), 0);
  await page.locator('#select-all-sources').check();
  const document = documents[0];
  let releaseSearch, searchStarted;
  const heldSearch = new Promise(resolve => { releaseSearch = resolve; });
  const searching = new Promise(resolve => { searchStarted = resolve; });
  await page.route('**/api/sources/search?**', async route => {
    searchStarted(); await heldSearch;
    await route.fulfill({ json: { documents: [document], results: [] } });
  });
  await page.locator('#source-search').fill('网络');
  await page.locator('#source-search-form').evaluate(form => form.requestSubmit());
  await searching;
  await page.locator('#notebook-select').selectOption(other);
  await page.waitForFunction(() => !document.querySelector('#notebook-select').disabled);
  const staleSearch = page.waitForResponse(response => response.url().includes('/api/sources/search?'));
  releaseSearch(); await staleSearch;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('#search-results').isVisible(), false);
  await page.unroute('**/api/sources/search?**');
  await page.locator('#notebook-select').selectOption(notebook);
  await page.waitForFunction(() => !document.querySelector('#notebook-select').disabled);
  let releaseSource, sourceStarted;
  const heldSource = new Promise(resolve => { releaseSource = resolve; });
  const readingSource = new Promise(resolve => { sourceStarted = resolve; });
  await page.route(`**/api/documents/${document.id}/chunks`, async route => {
    sourceStarted(); await heldSource;
    await route.fulfill({ json: { chunks: [{ text: 'STALE_CONTENT_SHOULD_NOT_APPEAR', index: 1 }] } });
  });
  await page.locator(`[data-document-id="${document.id}"] .document-name`).click();
  await readingSource;
  await page.locator(`[data-document-id="${documents[1].id}"] .document-name`).click();
  await page.locator('#source-text').filter({ hasText: '网络协议规定通信规则' }).waitFor();
  const staleSource = page.waitForResponse(response => response.url().endsWith(`/api/documents/${document.id}/chunks`));
  releaseSource(); await staleSource;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert(!((await page.locator('#source-text').innerText()).includes('STALE_CONTENT')));
  await page.unroute(`**/api/documents/${document.id}/chunks`);
  const chunk = (await api(`/api/documents/${document.id}/chunks`)).chunks[0];
  const citation = { index: 1, chunk_id: chunk.id, document_id: document.id, document_name: document.name,
    document_kind: 'md', page: chunk.page, text: chunk.text, lines: chunk.lines };
  const completed = randomUUID(), pending = randomUUID();
  seed(notebook, completed, { answer: Array.from({ length: 24 }, (_, i) => `第 ${i + 1} 段复习内容：协议规定格式与交换规则。[1]`).join('\n\n'), citations: [citation], mode: 'model' });
  seed(notebook, pending, null);
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.generation-progress').waitFor();
  const oldAnswer = page.locator(`article[data-message-id="${completed}"]`);
  await oldAnswer.locator('.citation-disclosure summary').click();
  await page.locator('#question-input').fill('阅读时保留的下一条草稿');
  await page.evaluate(id => {
    window.__preservedAnswer = document.querySelector(`[data-message-id="${id}"]`);
    document.querySelector('#messages').scrollTop = 100;
  }, completed);
  const failedPoll = page.waitForResponse(response => response.url().endsWith(`/api/notebooks/${notebook}/chat`) && response.status() === 503);
  await page.route(`**/api/notebooks/${notebook}/chat`, route => route.fulfill({ status: 503, json: { detail: '模拟临时断网' } }), { times: 1 });
  await failedPoll;
  assert(await page.locator('#send-button').isDisabled());
  seed(notebook, pending, { answer: '恢复连接后正常完成', citations: [], mode: 'model', image_question_text: '恢复后收到的题干', image_recognition_mode: 'manual' });
  await page.locator(`article[data-message-id="${pending}"]`).filter({ hasText: '恢复连接后正常完成' }).waitFor();
  assert(await page.evaluate(id => window.__preservedAnswer === document.querySelector(`[data-message-id="${id}"]`), completed));
  assert.equal(await oldAnswer.locator('.citation-disclosure').getAttribute('open'), '');
  assert(Math.abs(await page.locator('#messages').evaluate(element => element.scrollTop) - 100) < 3);
  assert.equal(await page.locator('#question-input').inputValue(), '阅读时保留的下一条草稿');
  await page.locator(`[data-message-id="user-${pending}"] .image-recognition summary`).click();
  assert.match(await page.locator(`[data-message-id="user-${pending}"] .image-recognition`).innerText(), /恢复后收到的题干/);
  await fs.mkdir('artifacts/reading-polish', { recursive: true });
  await page.screenshot({ path: 'artifacts/reading-polish/desktop.png', fullPage: true });
  for (const width of [320, 390, 768, 1100, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `horizontal overflow at ${width}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const inputBox = await page.locator('#question-input').boundingBox();
  assert(inputBox.y + inputBox.height <= 844 && inputBox.y >= 0);
  await page.locator('.mobile-tab[data-pane="sources"]').click();
  assert(await page.locator('#notebook-select').isVisible());
  await page.locator('#notes-tab').click();
  assert(await page.locator('#new-note').isVisible());
  await page.locator('#sources-tab').click();
  await page.locator('.document-name').first().click();
  assert.equal(await page.locator('.mobile-tab[data-pane="viewer"]').getAttribute('aria-pressed'), 'true');
  assert(await page.locator('#source-text').isVisible());
  await page.locator('.mobile-tab[data-pane="chat"]').click();
  assert(await page.locator('#question-input').isVisible());
  await page.screenshot({ path: 'artifacts/reading-polish/mobile.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, savedSourceSelection: true, emptySelectionPreserved: true,
    stableReadingDuringPolling: true, retryAfterDisconnect: true, retainedDraft: true, responsiveViews: true, errors }));
} finally {
  for (const notebook of notebooks) {
    await api(`/api/notebooks/${notebook}/chat`, 'DELETE');
    for (const doc of (await api(`/api/documents?notebook_id=${notebook}`)).documents) await api(`/api/documents/${doc.id}`, 'DELETE');
    await api(`/api/notebooks/${notebook}`, 'DELETE');
  }
  await browser.close();
}
