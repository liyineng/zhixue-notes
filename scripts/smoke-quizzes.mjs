// All APIs are mocked: no user data, cloud writes or paid model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const output = path.resolve('artifacts/quizzes'); await fs.mkdir(output, { recursive: true });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const docs = [{ id: 'course', name: 'L02-谐振回路.md', kind: 'md', page_count: 1, chunk_count: 1, size_bytes: 300 },
  { id: 'other', name: 'L03-放大电路.md', kind: 'md', page_count: 1, chunk_count: 1, size_bytes: 300 }];
let exchanges = [], configuration, summaryConfiguration;
const citation = { index: 1, document_id: 'course', document_kind: 'md', document_name: docs[0].name, page: null, text: '串联谐振时，感抗与容抗抵消。', lines: [] };
const quizResult = id => ({ chat_id: id, answer: '已生成 2 道测验。', mode: 'model', source_mode: 'knowledge_base', citations: [citation], warnings: [], quiz: {
  topic: '第二章：谐振回路', requested_count: 5, difficulty: 'hard', language: 'zh-CN', attempt: 1, answers: {}, questions: [
    { id: 'q1', prompt: '串联谐振时，下列哪个说法正确？', options: ['感抗与容抗抵消', '电感为零', '电容为零', '电阻为零'], answer: 0,
      explanation: '谐振时感抗与容抗抵消，回路呈纯电阻性。', sources: [1] },
    { id: 'q2', prompt: '谐振角频率的正确表达式是？', options: [String.raw`\(\omega_0=\frac{1}{\sqrt{LC}}\)`, String.raw`\(\omega_0=LC\)`, String.raw`\(\omega_0=L+C\)`, String.raw`\(\omega_0=0\)`], answer: 0,
      explanation: String.raw`由谐振条件可得 \[\omega_0=\frac{1}{\sqrt{LC}}\]`, sources: [1] },
  ] } });
await page.route('**/api/**', async route => {
  const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
  const fulfill = json => route.fulfill({ json });
  if (p === '/api/settings') return fulfill({ model: 'mock', model_configured: true });
  if (p === '/api/sync') return fulfill({ enabled: false, available: false, conflicts: [], revision: '' });
  if (p === '/api/notebooks') return fulfill({ notebooks: [{ id: 'default', name: '电路复习 · 测验验收', document_count: 2 }] });
  if (p === '/api/documents') return fulfill({ documents: docs });
  if (p === '/api/notes') return fulfill({ notes: [] });
  if (p === '/api/notebooks/default/chat') return fulfill({ exchanges, deleted_count: 0 });
  if (p === '/api/quiz' && method === 'POST') {
    configuration = request.postDataJSON();
    const result = quizResult(configuration.request_id);
    exchanges.push({ id: result.chat_id, status: 'complete', question: '生成交互式测验', images: [], reply_to: {}, created_at: new Date().toISOString(), result });
    return fulfill(result);
  }
  if (/\/quiz\/[^/]+\/answer$/.test(p)) {
    const result = exchanges.find(e => e.id === p.split('/').at(-2)).result;
    const answer = request.postDataJSON(); result.quiz.answers[answer.question_id] ??= answer.option;
    return fulfill(result);
  }
  if (/\/quiz\/[^/]+\/reset$/.test(p)) {
    const result = exchanges.find(e => e.id === p.split('/').at(-2)).result;
    result.quiz.answers = {}; result.quiz.attempt++; return fulfill(result);
  }
  if (p === '/api/summary' && method === 'POST') {
    summaryConfiguration = request.postDataJSON();
    return fulfill({ chat_id: summaryConfiguration.request_id, answer: '## 本章摘要\n\n串联谐振回路的主要性质 [1]', citations: [citation], mode: 'model' });
  }
  if (p === '/api/documents/course/chunks') return fulfill({ chunks: [{ text: citation.text, page: null, lines: [] }] });
  errors.push(`Unexpected API: ${method} ${p}`);
  return route.fulfill({ status: 404, json: { detail: 'Unexpected mocked API' } });
});
try {
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.locator('#summary-button').waitFor({ state: 'visible' });
  await page.waitForFunction(() => !document.querySelector('#summary-button').disabled);
  assert.match(await page.locator('#summary-button').innerText(), /功能选择/);
  await page.locator('#summary-button').click();
  await page.locator('[data-feature="quiz"]').click();
  await page.locator('#quiz-count').selectOption('less');
  await page.locator('#quiz-difficulty').selectOption('hard');
  await page.locator('#feature-topic').fill('第二章：谐振回路');
  await page.locator('#feature-source-count').click();
  for (const checkbox of await page.locator('#feature-source-list input').all()) await checkbox.uncheck();
  await page.locator('#feature-generate').click();
  await page.locator('#feature-error').waitFor({ state: 'visible' });
  assert.equal(configuration, undefined);
  await page.locator('#feature-source-list input[value="course"]').check();
  await page.screenshot({ path: path.join(output, 'quiz-settings-desktop.png') });
  await page.locator('#feature-generate').click();
  await page.locator('.quiz-card').waitFor();
  assert.deepEqual(configuration.document_ids, ['course']);
  assert.equal(configuration.count, 'less'); assert.equal(configuration.difficulty, 'hard');
  assert.equal(await page.locator('.quiz-explanation').count(), 0);
  assert.equal(await page.locator('.quiz-check').isDisabled(), true);
  await page.locator('.quiz-option').nth(1).click();
  await page.locator('.quiz-check').click();
  await page.locator('.quiz-feedback.is-incorrect').waitFor();
  assert.match(await page.locator('.quiz-status').innerText(), /已答 1 \/ 2.*答对 0/);
  await page.locator('.quiz-source').click();
  await page.waitForFunction(() => document.querySelector('#source-text').textContent.includes('感抗与容抗抵消'));
  await page.locator('.quiz-navigation button', { hasText: '下一题' }).click();
  assert.equal(await page.locator('.quiz-option .katex').count(), 4);
  assert.equal(await page.locator('.katex-error').count(), 0);
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.quiz-card').waitFor();
  assert.match(await page.locator('.quiz-question-number').innerText(), /第 2/);
  assert.match(await page.locator('.quiz-status').innerText(), /已答 1 \/ 2/);
  await page.locator('.quiz-option').first().click(); await page.locator('.quiz-check').click();
  await page.locator('.quiz-finished').waitFor();
  assert.match(await page.locator('.quiz-status').innerText(), /答对 1/);
  await page.screenshot({ path: path.join(output, 'quiz-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('[data-pane="chat"]').click();
  await page.screenshot({ path: path.join(output, 'quiz-mobile.png') });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  page.once('dialog', dialog => dialog.accept()); await page.locator('.quiz-reset').click();
  await page.waitForFunction(() => document.querySelector('.quiz-status').textContent.includes('已答 0'));
  assert.equal(await page.locator('.quiz-feedback').count(), 0);
  assert.match(await page.locator('.quiz-question-number').innerText(), /第 1/);
  await page.locator('[data-pane="sources"]').click();
  await page.locator('#summary-button').click();
  await page.screenshot({ path: path.join(output, 'quiz-settings-mobile.png') });
  assert.equal(await page.locator('#features-dialog').evaluate(e => e.scrollWidth > e.clientWidth), false);
  await page.locator('[data-feature="summary"]').click();
  await page.locator('#feature-topic').fill('第二章的基本概念');
  await page.locator('#feature-generate').click();
  await page.locator('.message-body h3', { hasText: '本章摘要' }).waitFor();
  assert.equal(summaryConfiguration.focus, '第二章的基本概念');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, settings: true, selectedSourcesOnly: true, grading: true, citations: true,
    persistedAfterReload: true, reset: true, math: true, summary: true, mobile: true }));
} catch (error) {
  await page.screenshot({ path: path.join(output, 'failure.png') });
  console.error(JSON.stringify({ errors, url: page.url() }));
  throw error;
} finally { await browser.close(); }
