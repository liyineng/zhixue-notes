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
let notebook, releaseAnswer;
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const api = async (url, method = 'GET', data) => {
  const response = await context.request.fetch(base + url, { method, data });
  assert(response.ok(), `${method} ${url}: ${response.status()}`);
  return response.json();
};
function record(id, operation, extra = {}) {
  execFileSync(path.resolve('.venv/bin/python'), ['-c', `
import json,sys
from datetime import datetime,UTC,timedelta
from app import db
d=json.load(sys.stdin)
if d['operation']=='start':
    db.start_exchange(d['id'],d['notebook'],'解释网络协议')
    with db.connection() as conn:
        conn.execute('UPDATE chat_exchanges SET created_at=? WHERE id=?',((datetime.now(UTC)-timedelta(seconds=35)).isoformat(),d['id']))
elif d['operation']=='progress':
    db.set_generation_progress(d['id'],d['stage'],d['label'],d['detail'],activity=d.get('activity'))
else:
    db.finish_pending_exchange(d['id'],d['result'])
`], { input: JSON.stringify({ id, notebook: notebook.id, operation, ...extra }) });
}
try {
  notebook = (await api('/api/notebooks', 'POST', { name: '生成状态验收' })).notebook;
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.locator('#notebook-select').selectOption(notebook.id);
  await page.waitForFunction(() => !document.querySelector('#notebook-select').disabled);
  let jobId;
  const held = new Promise(resolve => { releaseAnswer = resolve; });
  await page.route('**/api/ask', async route => {
    jobId = route.request().postDataJSON().request_id;
    record(jobId, 'start');
    record(jobId, 'progress', { stage: 'analysis', label: '规划检索', detail: '正在分析问题。' });
    await held;
    const result = { chat_id: jobId, answer: '协议规定通信规则。', citations: [], mode: 'model' };
    record(jobId, 'finish', { result });
    await route.fulfill({ json: result });
  });
  await page.locator('#question-input').fill('解释网络协议');
  await page.locator('#question-input').press('Enter');
  await page.locator('.generation-steps .current').filter({ hasText: '规划检索' }).waitFor();
  assert.match(await page.locator('.generation-timing').innerText(), /仍在处理/);
  // A transient status failure must not replace progress with a failed answer.
  await page.route('**/status', route => route.fulfill({ status: 503, json: { detail: '临时状态错误' } }), { times: 1 });
  record(jobId, 'progress', { stage: 'evidence', label: '核对候选原文', detail: '检索到 4 个候选片段，正在核对原文。',
    activity: { query: '网桥 自学习 转发表', source_count: 1, sources: [{ name: '网络课件 L04.pdf', pages: [28, 50], page_count: 2, fragments: 4 }] } });
  await page.locator('.generation-detail').filter({ hasText: '4 个候选片段' }).waitFor();
  assert.equal(await page.locator('.generation-steps .completed').count(), 1);
  assert.match(await page.locator('.generation-query').innerText(), /网桥 自学习 转发表/);
  assert.match(await page.locator('.generation-sources').innerText(), /网络课件 L04.pdf/);
  assert.match(await page.locator('.generation-sources').innerText(), /28、50/);
  record(jobId, 'progress', { stage: 'answer', label: '生成回答', detail: '已整理 2 处资料依据，正在组织回答与引用。',
    activity: { source_count: 1, sources: [{ name: '网络课件 L04.pdf', pages: [50], page_count: 1, fragments: 2 }] } });
  await page.locator('.generation-steps .current').filter({ hasText: '生成回答' }).waitFor();
  await fs.mkdir('artifacts/generation-progress', { recursive: true });
  await page.screenshot({ path: 'artifacts/generation-progress/desktop.png', fullPage: true });
  releaseAnswer();
  await page.waitForFunction(() => !document.querySelector('#send-button').disabled);
  assert.equal(await page.locator('.generation-progress').count(), 0);
  assert.match(await page.locator('.message.assistant').last().innerText(), /协议规定通信规则/);
  // Refresh follows the same persisted status without the original POST connection.
  const restoredId = randomUUID();
  record(restoredId, 'start');
  record(restoredId, 'progress', { stage: 'sources', label: '检查资料', detail: '正在识别《网络课件 L04.pdf》第 28 页的图片文字。',
    activity: { source_count: 1, sources: [{ name: '网络课件 L04.pdf', pages: [28], page_count: 1, fragments: 0 }] } });
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.generation-steps .current').filter({ hasText: '检查资料' }).waitFor();
  assert.match(await page.locator('.generation-sources').innerText(), /第 28 页/);
  record(restoredId, 'progress', { stage: 'answer', label: '生成回答', detail: '正在结合下列资料组织回答与引用。',
    activity: { source_count: 1, sources: [{ name: '网络课件 L04.pdf', pages: [28, 50], page_count: 2, fragments: 2 }] } });
  await page.locator('.generation-steps .current').filter({ hasText: '生成回答' }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: 'artifacts/generation-progress/mobile.png', fullPage: true });
  await page.locator('#cancel-generation').click();
  await page.waitForFunction(() => document.querySelector('#cancel-generation').hidden);
  assert.equal(await page.locator('.generation-progress').count(), 0);
  assert.match(await page.locator('.message.assistant').last().innerText(), /已取消/);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, liveStages: true, transientRetry: true, restoredAfterRefresh: true,
    finishedAndCancelledClearProgress: true, mobile: true, errors }));
} finally {
  releaseAnswer?.();
  if (notebook) {
    await api(`/api/notebooks/${notebook.id}/chat`, 'DELETE');
    await api(`/api/notebooks/${notebook.id}`, 'DELETE');
  }
  await browser.close();
}
