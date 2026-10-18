import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
const modules = process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const errors = []; page.on('pageerror', error => errors.push(error.message));
let notebook;
const docs = [];
const api = async (url, method = 'GET', data) => {
  const response = await context.request.fetch(base + url, { method, data });
  assert(response.ok(), `${method} ${url}: ${response.status()}`);
  return response.json();
};
try {
  notebook = (await api('/api/notebooks', 'POST', { name: '逐份来源核对验收' })).notebook.id;
  for (const name of ['A课程.md', 'B课程.md', 'C课程.md']) {
    const response = await context.request.post(base + `/api/documents?notebook_id=${notebook}`, {
      multipart: { file: { name, mimeType: 'text/markdown', buffer: Buffer.from('网络协议规定通信规则。') } },
    });
    assert(response.ok()); docs.push((await response.json()).document);
  }
  const chunk = (await api(`/api/documents/${docs[0].id}/chunks`)).chunks[0];
  const citation = { index: 1, document_id: docs[0].id, document_name: docs[0].name,
    document_kind: 'md', page: 1, chunk_id: chunk.id, text: chunk.text, lines: chunk.lines };
  const coverage = { documents: docs.slice(0, 2).map(doc => doc.name), selected_documents: docs.map(doc => doc.name), pages: 4, sources: 4,
    read_document_ids: docs.slice(0, 2).map(doc => doc.id), document_details: docs.map((doc, index) => ({
      id: doc.id, name: doc.name, read_pages: index < 2 ? 2 : 0, read_sources: index < 2 ? 2 : 0,
    })) };
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.locator('#notebook-select').selectOption(notebook);
  await page.waitForFunction(() => !document.querySelector('#notebook-select').disabled);
  await page.route('**/api/summary', async route => {
    const payload = route.request().postDataJSON();
    const result = { chat_id: payload.request_id, answer: '# A课程\n\n课程重点 [1]', citations: [citation], mode: 'model', summary_coverage: coverage };
    execFileSync(path.resolve('.venv/bin/python'), ['-c', `
import sys,json
from app import db
d=json.load(sys.stdin)
db.start_exchange(d['result']['chat_id'],d['notebook'],'来源覆盖率显示测试')
db.finish_pending_exchange(d['result']['chat_id'],d['result'])
`], { input: JSON.stringify({ notebook, result }) });
    await route.fulfill({ json: result });
  });
  await page.locator('#summary-button').click();
  await page.locator('#feature-generate').click();
  await page.locator('.summary-coverage').waitFor();
  assert.match(await page.locator('.summary-coverage').innerText(), /读取范围：2\/3/);
  assert.match(await page.locator('.summary-coverage').innerText(), /回答引用 1 份/);
  assert.match(await page.locator('.summary-coverage').innerText(), /不代表知识点已全部覆盖/);
  await page.locator('.summary-source-coverage summary').click();
  const detail = await page.locator('.summary-source-coverage').innerText();
  assert.match(detail, /A课程.md：读取 2 页；引用 1 页、1 处/);
  assert.match(detail, /B课程.md：读取 2 页；本回答未引用该来源/);
  assert.match(detail, /C课程.md：无可用文字；本回答未引用该来源/);
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.summary-source-coverage summary').click();
  assert.equal(await page.locator('.summary-source-coverage').innerText(), detail);
  await fs.mkdir('artifacts/summary-coverage', { recursive: true });
  await page.screenshot({ path: 'artifacts/summary-coverage/desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: 'artifacts/summary-coverage/mobile.png', fullPage: true });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, readAndCitedSeparated: true, missingSourcesVisible: true, restoredAfterRefresh: true, mobile: true, errors }));
} finally {
  if (notebook) {
    await api(`/api/notebooks/${notebook}/chat`, 'DELETE');
    for (const doc of docs) await api(`/api/documents/${doc.id}`, 'DELETE');
    await api(`/api/notebooks/${notebook}`, 'DELETE');
  }
  await browser.close();
}
