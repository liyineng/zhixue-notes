// Reproduce optimistic messages being rendered twice after a cloud revision.
// All APIs are fixtures; no actual sync, uploads or model calls.
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
let entries = [], revision = 'initial', posts = 0;
let releaseAnswer;
const gate = new Promise(resolve => { releaseAnswer = resolve; });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const doc = { id: 'course', name: '课程.md', kind: 'md', size_bytes: 100, page_count: 1, chunk_count: 1 };
await page.route('**/api/**', async route => {
  const req = route.request(), p = new URL(req.url()).pathname;
  const fulfill = json => route.fulfill({ json });
  if (p === '/api/settings') return fulfill({ model_configured: true, model: 'mock' });
  if (p === '/api/sync') return fulfill({ enabled: true, available: true, conflicts: [], revision, folder: '', devices: 1 });
  if (p === '/api/notebooks') return fulfill({ notebooks: [{ id: 'default', name: '重复显示验收', document_count: 1 }] });
  if (p === '/api/documents') return fulfill({ documents: [doc] });
  if (p === '/api/notes') return fulfill({ notes: [] });
  if (p === '/api/notebooks/default/chat') return fulfill({ exchanges: entries, deleted_count: 0 });
  if (p.endsWith('/status')) return fulfill({ status: 'pending', progress: {}, created_at: '2026-01-01' });
  if (p === '/api/ask') {
    posts++; const data = req.postDataJSON();
    await gate;
    const result = { chat_id: data.request_id, answer: '只显示一次的完整回答。[1]', mode: 'model', source_mode: 'knowledge_base', citations: [{ index: 1, document_id: doc.id, document_name: doc.name, document_kind: doc.kind, page: 1, text: '真实依据', lines: [] }] };
    entries = [{ id: data.request_id, question: data.question, images: [], reply_to: {}, status: 'complete', result, created_at: '2026-01-01' }];
    return fulfill(result);
  }
  errors.push(`Unexpected API ${req.method()} ${p}`);
  return route.fulfill({ status: 404, json: { detail: 'Unexpected mocked API' } });
});
try {
  await page.goto(process.env.APP_URL || 'http://127.0.0.1:8765', { waitUntil: 'networkidle' });
  await page.locator('#question-input').fill('详细解释 W6-04');
  await page.locator('#question-input').press('Enter');
  await page.waitForFunction(() => document.querySelector('#send-button').disabled);
  await page.locator('#ask-form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
  releaseAnswer();
  await page.locator('.message.assistant').filter({ hasText: '完整回答' }).waitFor();
  assert.equal(posts, 1);
  await page.locator('.citation-disclosure').evaluate(e => { e.open = true; });
  await page.evaluate(() => { window.__originalAnswer = document.querySelector('.message.assistant'); });
  for (const value of ['updated', 'updated-again']) {
    revision = value;
    const refreshed = page.waitForResponse(response => response.url().endsWith('/api/notebooks/default/chat'));
    await page.locator('#sync-button').click();
    await refreshed;
    await page.waitForFunction(() => !document.querySelector('#send-button').disabled);
    await page.locator('#close-sync').click();
    assert.equal(await page.locator('.message.user').count(), 1);
    assert.equal(await page.locator('.message.assistant').count(), 1);
    assert.equal(await page.evaluate(() => window.__originalAnswer === document.querySelector('.message.assistant')), true);
    assert.equal(await page.locator('.citation-disclosure').evaluate(e => e.open), true);
  }
  assert.equal(posts, 1); assert.deepEqual(errors, []);
  console.log('Single submit, stable message identity, no duplicates after sync, retained answer DOM and open citations passed.');
} finally { await browser.close(); }
