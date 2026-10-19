// All APIs are mocked: no user data, cloud writes or paid model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const output = path.resolve('artifacts/quizzes'); await fs.mkdir(output, { recursive: true });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const pdf = execFileSync(path.resolve('.venv/bin/python'), ['-c', `
import sys,pymupdf
with pymupdf.open() as pdf:
 page=pdf.new_page(width=600,height=720)
 page.insert_text((50,70),'Series resonance: inductive and capacitive reactances cancel.')
 sys.stdout.buffer.write(pdf.tobytes())
`]);
const docs = [{ id: 'course', name: 'L02-谐振回路.pdf', kind: 'pdf', page_count: 1, chunk_count: 1, size_bytes: 300 },
  { id: 'other', name: 'L03-放大电路.md', kind: 'md', page_count: 1, chunk_count: 1, size_bytes: 300 }];
let exchanges = [], deleted = [], configuration, summaryConfiguration;
const citation = { index: 1, document_id: 'course', document_kind: 'pdf', document_name: docs[0].name, page: 1,
  text: 'Series resonance: inductive and capacitive reactances cancel.', lines: [{page:1,text:'Series resonance: inductive and capacitive reactances cancel.',bbox:[50,58,500,75]}] };
exchanges.push({ id: 'fixture-conversation', status: 'complete', question: '解释串联谐振', images: [], reply_to: {}, created_at: '2026-01-01',
  result: { answer: '## 章节复习\n\n串联谐振时感抗与容抗抵消，回路呈纯电阻性。[1]', citations: [citation], mode: 'model', source_mode: 'knowledge_base' } });
const quizResult = id => ({ chat_id: id, answer: '已生成 2 道测验。', mode: 'model', source_mode: 'knowledge_base', citations: [citation], warnings: [], quiz: {
  topic: '第二章：谐振回路', requested_count: 5, difficulty: 'hard', language: 'zh-CN', attempt: 1, answers: {}, questions: [
    { id: 'q1', prompt: '串联谐振时，下列哪个说法正确？', options: ['感抗与容抗抵消', '电感为零', '电容为零', '电阻为零'], answer: 0,
      explanation: '谐振时感抗与容抗抵消，回路呈纯电阻性。', sources: [1] },
    { id: 'q2', prompt: '谐振角频率的正确表达式是？', options: [String.raw`\(\omega_0=\frac{1}{\sqrt{LC}}\)`, String.raw`\(\omega_0=LC\)`, String.raw`\(\omega_0=L+C\)`, String.raw`\(\omega_0=0\)`], answer: 0,
      explanation: String.raw`由谐振条件可得 \[\omega_0=\frac{1}{\sqrt{LC}}\]`, sources: [1] },
  ] } });
await page.route(url => url.pathname.startsWith('/quiz/'), async route => route.fulfill({ contentType: 'text/html', body: await fs.readFile('app/static/quiz.html', 'utf8') }));
await page.route('**/api/**', async route => {
  const request = route.request(), url = new URL(request.url()), p = url.pathname, method = request.method();
  const fulfill = json => route.fulfill({ json });
  if (p === '/api/settings') return fulfill({ model: 'mock', model_configured: true });
  if (p === '/api/sync') return fulfill({ enabled: false, available: false, conflicts: [], revision: '' });
  if (p === '/api/notebooks') return fulfill({ notebooks: [{ id: 'default', name: '电路复习 · 测验验收', document_count: 2 }] });
  if (p === '/api/documents') return fulfill({ documents: docs });
  if (p === '/api/documents/course/file') return route.fulfill({ contentType: 'application/pdf', body: pdf });
  if (p === '/api/notes') return fulfill({ notes: [] });
  if (p === '/api/notebooks/default/chat' && method === 'GET') return fulfill({ exchanges, deleted_count: deleted.length });
  if (p === '/api/notebooks/default/chat' && method === 'DELETE') {
    exchanges = exchanges.filter(e => e.result.quiz); deleted = []; return fulfill({ deleted: true, deleted_image_ids: [] });
  }
  if (p === '/api/notebooks/default/chat/undo') { const restored = deleted.pop(); exchanges.push(restored); return fulfill({ restored: true, chat_id: restored.id, deleted_count: deleted.length }); }
  if (/\/chat\/[^/]+$/.test(p) && method === 'DELETE') {
    const id = p.split('/').at(-1); deleted.push(exchanges.find(e => e.id === id)); exchanges = exchanges.filter(e => e.id !== id);
    return fulfill({ deleted: true, deleted_count: deleted.length, deleted_image_ids: [] });
  }
  if (/\/quiz\/[^/]+$/.test(p) && method === 'GET') {
    const exchange = exchanges.find(e => e.id === p.split('/').at(-1));
    return fulfill({ ...exchange.result, title: exchange.question });
  }
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
  // Resized chat columns must retain a readable notebook title and reachable actions.
  for (const width of [320, 390, 820, 1180, 1920]) {
    await page.setViewportSize({ width, height: 844 });
    // Panel widths are recalculated on animation frames after a resize.
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const heading = await page.locator('.chat-heading').boundingBox();
    const title = await page.locator('#notebook-title').boundingBox();
    assert(title.width >= 120, `notebook title squeezed at ${width}`);
    for (const selector of ['#clear-chat', '#undo-chat-delete']) {
      const button = await page.locator(selector).boundingBox();
      assert(button.x >= heading.x && button.x + button.width <= heading.x + heading.width + 1, `chat action clipped at ${width}`);
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  }
  await page.screenshot({ path: path.join(output, 'chat-wide.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, 'chat-mobile.png') });
  await page.setViewportSize({ width: 1440, height: 1000 });
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
  await page.locator('.application-card').waitFor();
  assert.deepEqual(configuration.document_ids, ['course']);
  assert.equal(configuration.count, 'less'); assert.equal(configuration.difficulty, 'hard');
  assert.equal(await page.locator('#applications-tab').getAttribute('aria-selected'), 'true');
  assert.equal(await page.locator('#messages .quiz-card').count(), 0);
  const sidebar = page.locator('.sidebar');
  const initialWidth = (await sidebar.boundingBox()).width;
  const handle = await page.locator('#sidebar-resizer').boundingBox();
  await page.mouse.move(handle.x + 4, handle.y + 100); await page.mouse.down();
  await page.mouse.move(handle.x + 64, handle.y + 100); await page.mouse.up();
  const changedWidth = (await sidebar.boundingBox()).width;
  assert(changedWidth > initialWidth + 40);
  const initialChat = (await page.locator('.chat-pane').boundingBox()).width;
  const split = await page.locator('#workspace-resizer').boundingBox();
  await page.mouse.move(split.x + 4, split.y + 100); await page.mouse.down();
  await page.mouse.move(split.x + 64, split.y + 100); await page.mouse.up();
  const changedChat = (await page.locator('.chat-pane').boundingBox()).width;
  assert(changedChat > initialChat + 40);
  await page.reload({ waitUntil: 'networkidle' });
  assert(Math.abs((await sidebar.boundingBox()).width - changedWidth) < 2);
  assert(Math.abs((await page.locator('.chat-pane').boundingBox()).width - changedChat) < 2);
  await page.locator('#sidebar-resizer').focus(); await page.keyboard.press('ArrowLeft');
  assert((await sidebar.boundingBox()).width < changedWidth - 10);
  await page.locator('#sidebar-resizer').dblclick(); await page.locator('#workspace-resizer').dblclick();
  assert.equal(Math.round((await sidebar.boundingBox()).width), 276);
  assert.equal(await page.locator('#sources-tab').getAttribute('aria-selected'), 'true');
  await page.locator('#applications-tab').click();
  await page.screenshot({ path: path.join(output, 'application-library-desktop.png') });
  const conversation = page.locator('article[data-message-id="fixture-conversation"]');
  await conversation.evaluate(element => element.dataset.preserved = 'yes');
  await conversation.locator('details').first().evaluate(element => element.open = true);
  await page.locator('#question-input').fill('还没有发送的追问');
  await page.locator('.application-open').click();
  await page.locator('.quiz-card').waitFor();
  assert(!new URL(page.url()).pathname.startsWith('/quiz/'));
  assert.equal(await conversation.getAttribute('data-preserved'), 'yes');
  assert.equal(await conversation.locator('details').first().evaluate(element => element.open), true);
  assert.equal(await page.locator('#question-input').inputValue(), '还没有发送的追问');
  assert.equal(await page.locator('.chat-pane').isVisible(), false);
  assert.equal(await page.locator('.sidebar').isVisible(), false);
  await page.locator('#study-show-chat').click();
  assert.equal(await page.locator('.chat-pane').isVisible(), true);
  assert.equal(await page.locator('#question-input').inputValue(), '还没有发送的追问');
  await page.locator('#question-input').fill('');
  await page.locator('#study-close-chat').click();
  await page.locator('#study-show-library').click();
  assert.equal(await page.locator('.sidebar').isVisible(), true);
  await page.locator('#study-close-library').click();
  assert.equal(await page.locator('#viewer-document').isVisible(), true);
  assert.equal(await page.locator('#quiz-workspace').isVisible(), true);
  await page.locator('#viewer-scroll').waitFor({ state: 'visible' });
  const original = await page.locator('#viewer-document').boundingBox();
  const practice = await page.locator('#quiz-workspace').boundingBox();
  assert(original.x + original.width <= practice.x + 2);
  assert(original.width >= 500 && practice.width >= 500);
  const studyHandle = await page.locator('#study-resizer').boundingBox();
  await page.mouse.move(studyHandle.x + 4, studyHandle.y + 80); await page.mouse.down();
  await page.mouse.move(studyHandle.x + 24, studyHandle.y + 80); await page.mouse.up();
  assert((await page.locator('#viewer-document').boundingBox()).width > original.width + 10);
  assert.equal(await page.locator('.quiz-explanation').count(), 0);
  assert.equal(await page.locator('.quiz-check').isDisabled(), true);
  await page.locator('.quiz-option').nth(1).click();
  await page.locator('.quiz-check').click();
  await page.locator('.quiz-feedback.is-incorrect').waitFor();
  assert.match(await page.locator('.quiz-status').innerText(), /已答 1 \/ 2.*答对 0/);
  await page.locator('.quiz-source').click();
  await page.waitForFunction(() => document.querySelector('#source-text').textContent.includes('Series resonance'));
  assert.equal(await page.locator('#quiz-workspace').isVisible(), true);
  assert.equal(await page.locator('#viewer-scroll').isVisible(), true);
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
  for (const width of [1180, 960]) {
    await page.setViewportSize({ width, height: 900 });
    const reading = await page.locator('#viewer-document').boundingBox();
    const solving = await page.locator('#quiz-workspace').boundingBox();
    assert(reading.width >= 360 && solving.width >= 360);
    assert(reading.x + reading.width <= solving.x + 2);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector('.viewer-pane').dataset.studyLayout === 'rows');
  assert.equal(await page.locator('#viewer-document').isVisible(), true);
  assert.equal(await page.locator('#quiz-workspace').isVisible(), true);
  const mobileOriginal = await page.locator('#viewer-document').boundingBox();
  const mobilePractice = await page.locator('#quiz-workspace').boundingBox();
  assert(mobileOriginal.y + mobileOriginal.height <= mobilePractice.y + 2);
  assert(mobileOriginal.height >= 100 && mobilePractice.height >= 150);
  assert.equal(await page.locator('#study-resizer').getAttribute('aria-orientation'), 'horizontal');
  await page.locator('#study-resizer').focus(); await page.keyboard.press('ArrowUp');
  assert((await page.locator('#viewer-document').boundingBox()).height < mobileOriginal.height);
  await page.screenshot({ path: path.join(output, 'quiz-mobile.png') });
  // Long explanations should leave navigation reachable inside the quiz, without
  // moving the original document or overflowing a short laptop/mobile viewport.
  await page.locator('.quiz-explanation').evaluate(element => {
    for (let i = 0; i < 24; i++) {
      const paragraph = document.createElement('p');
      paragraph.textContent = '长解析布局验收：结合原文检查公式中的每个物理量与适用条件。';
      element.append(paragraph);
    }
  });
  for (const viewport of [{ width: 390, height: 844 }, { width: 1180, height: 650 }]) {
    await page.setViewportSize(viewport);
    await page.locator('#quiz-workspace').evaluate(element => { element.scrollTop = 0; });
    const practice = await page.locator('#quiz-workspace').boundingBox();
    const nav = await page.locator('.quiz-navigation').boundingBox();
    assert(nav.y >= practice.y && nav.y + nav.height <= practice.y + practice.height + 1, 'quiz navigation outside panel');
    const originalScroll = await page.locator('#viewer-scroll').evaluate(element => element.scrollTop);
    await page.locator('#quiz-workspace').evaluate(element => { element.scrollTop = element.scrollHeight; });
    assert.equal(await page.locator('#viewer-scroll').evaluate(element => element.scrollTop), originalScroll);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  }
  await page.screenshot({ path: path.join(output, 'quiz-short-laptop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  page.once('dialog', dialog => dialog.accept()); await page.locator('.quiz-reset').click();
  await page.waitForFunction(() => document.querySelector('.quiz-status').textContent.includes('已答 0'));
  assert.equal(await page.locator('.quiz-feedback').count(), 0);
  assert.match(await page.locator('.quiz-question-number').innerText(), /第 1/);
  await page.locator('#close-quiz-panel').click();
  await page.locator('[data-pane="sources"]').click();
  await page.locator('#applications-tab').click();
  await page.locator('.application-card').waitFor();
  assert.match(await page.locator('.application-status').innerText(), /已答 0/);
  assert.equal(await page.locator('#sidebar-resizer').isVisible(), false);
  assert.equal(await page.locator('#workspace-resizer').isVisible(), false);
  await page.screenshot({ path: path.join(output, 'application-library-mobile.png') });
  await page.locator('#summary-button').click();
  await page.screenshot({ path: path.join(output, 'quiz-settings-mobile.png') });
  assert.equal(await page.locator('#features-dialog').evaluate(e => e.scrollWidth > e.clientWidth), false);
  await page.locator('[data-feature="summary"]').click();
  await page.locator('#feature-topic').fill('第二章的基本概念');
  await page.locator('#feature-generate').click();
  await page.locator('.message-body h3', { hasText: '本章摘要' }).waitFor();
  assert.equal(summaryConfiguration.focus, '第二章的基本概念');
  await page.locator('[data-pane="sources"]').click();
  await page.locator('#applications-tab').click();
  page.once('dialog', dialog => dialog.accept()); await page.locator('.delete-application').click();
  await page.waitForFunction(() => document.querySelector('#application-count').textContent === '0');
  await page.locator('[data-pane="chat"]').click();
  await page.locator('#undo-chat-delete').click();
  await page.locator('[data-pane="sources"]').click();
  await page.locator('.application-card').waitFor();
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, settings: true, selectedSourcesOnly: true, grading: true, citations: true,
    persistedAfterReload: true, reset: true, math: true, summary: true, mobile: true, applicationLibrary: true,
    embeddedQuizPanel: true, simultaneousOriginalAndQuiz: true, responsiveStudyLayout: true,
    focusReadingLayout: true, contextDrawers: true, conversationPreserved: true, draggableColumns: true, layoutPersistence: true, applicationDeleteUndo: true,
    readableNarrowHeaders: true, stickyQuizNavigation: true, independentReadingScroll: true }));
} catch (error) {
  await page.screenshot({ path: path.join(output, 'failure.png') });
  console.error(JSON.stringify({ errors, url: page.url() }));
  throw error;
} finally { await browser.close(); }
