// Isolated quiz component: mocked persistence, no application data or model calls.
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const errors = []; page.on('pageerror', e => errors.push(e.message));
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const initial = { chat_id: 'draft-fixture', citations: [], quiz: { attempt: 1, answers: {}, topic: '草稿验收', questions: [
  { id: 'one', prompt: '第一题', options: ['甲', '乙', '丙', '丁'], answer: 0, explanation: '解析', sources: [] },
  { id: 'two', prompt: '第二题', options: ['甲', '乙', '丙', '丁'], answer: 1, explanation: '解析', sources: [] },
] } };
await page.route('**/__quiz-draft-test', r => r.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><link rel="stylesheet" href="/static/styles.css"></head><body><main id="quiz"></main></body></html>' }));
const mount = async (notebook = 'fixture-a', result = initial) => page.evaluate(async ({ notebook, result }) => {
  const { renderQuiz } = await import('/static/quizzes.js');
  window.quizResult = structuredClone(result); window.calls = 0;
  window.mountQuiz = () => renderQuiz(document.querySelector('#quiz'), window.quizResult, { notebookId: notebook,
    onCitation() {}, onAnswer: async (id, option) => {
      window.calls++;
      if (window.holdAnswer) await new Promise(resolve => { window.releaseAnswer = resolve; });
      if (window.failAnswer) throw new Error('模拟连接中断');
      window.quizResult.quiz.answers[id] ??= option;
      return structuredClone(window.quizResult);
    }, onReset: async () => { window.quizResult.quiz.attempt++; window.quizResult.quiz.answers = {}; return structuredClone(window.quizResult); },
  });
  window.mountQuiz();
}, { notebook, result });
try {
  await page.goto(`${base}/__quiz-draft-test`); await mount();
  await page.locator('.quiz-option').nth(2).click();
  assert.match(await page.locator('.quiz-saved').innerText(), /待提交.*本机草稿已保存/);
  assert.equal(await page.evaluate(() => calls), 0);
  await page.getByText('下一题', { exact: true }).click();
  await page.locator('.quiz-option').nth(3).click();
  await page.getByText('上一题', { exact: true }).click();
  assert.equal(await page.locator('.quiz-option').nth(2).getAttribute('aria-pressed'), 'true');
  await page.evaluate(() => { document.querySelector('#quiz').replaceChildren(); mountQuiz(); });
  assert.equal(await page.locator('.quiz-option').nth(2).getAttribute('aria-pressed'), 'true', 'reopen keeps choice');
  await page.reload(); await mount();
  assert.equal(await page.locator('.quiz-option').nth(2).getAttribute('aria-pressed'), 'true', 'refresh keeps choice');
  await mount('fixture-b');
  assert.equal(await page.locator('.quiz-option.selected').count(), 0, 'notebook drafts are isolated');
  await mount('fixture-a');
  await page.evaluate(() => { window.holdAnswer = true; window.failAnswer = true; });
  await page.locator('.quiz-check').click();
  assert.equal(await page.locator('.quiz-saved').innerText(), '保存中…');
  await page.evaluate(() => { releaseAnswer(); });
  await page.locator('.feature-error').waitFor({ state: 'visible' });
  assert.match(await page.locator('.quiz-saved').innerText(), /保存失败.*草稿保留在本机/);
  assert.equal(await page.locator('.quiz-option').nth(2).getAttribute('aria-pressed'), 'true');
  await page.evaluate(() => { window.holdAnswer = false; window.failAnswer = false; });
  await page.locator('.quiz-check').click(); await page.locator('.quiz-feedback').waitFor();
  assert.equal(await page.locator('.quiz-saved').innerText(), '已保存');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('zhixue-quiz-drafts:v1:fixture-a:draft-fixture:1')).one), undefined);
  await page.getByText('下一题', { exact: true }).click();
  assert.equal(await page.locator('.quiz-option').nth(3).getAttribute('aria-pressed'), 'true', 'submitting another question preserves this draft');
  // A saved server answer always wins over stale local data.
  const submitted = structuredClone(initial); submitted.quiz.answers = { one: 1 }; await mount('fixture-a', submitted);
  await page.getByText('上一题', { exact: true }).click();
  assert.equal(await page.locator('.quiz-option').nth(1).getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('.quiz-option').nth(1).isDisabled(), true);
  page.once('dialog', dialog => dialog.accept()); await page.locator('.quiz-reset').click();
  await page.waitForFunction(() => document.querySelector('.quiz-status').textContent.includes('已答 0'));
  assert.equal(await page.locator('.quiz-option.selected').count(), 0);
  assert.equal(await page.evaluate(() => localStorage.getItem('zhixue-quiz-drafts:v1:fixture-a:draft-fixture:1')), null);
  await page.getByText('下一题', { exact: true }).click();
  assert.equal(await page.locator('.quiz-option.selected').count(), 0, 'new attempt ignores old drafts');
  // Storage rejection retains in-memory navigation and never claims a saved draft.
  await page.evaluate(() => { window.originalSetItem = Storage.prototype.setItem; Storage.prototype.setItem = function(k, v) { if (k.startsWith('zhixue-quiz-drafts:')) throw new DOMException('Quota exceeded'); return originalSetItem.call(this, k, v); }; });
  await page.locator('.quiz-option').nth(0).click();
  assert.match(await page.locator('.quiz-saved').innerText(), /草稿保存失败/);
  await page.getByText('上一题', { exact: true }).click(); await page.getByText('下一题', { exact: true }).click();
  assert.equal(await page.locator('.quiz-option').nth(0).getAttribute('aria-pressed'), 'true');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, unsubmittedChoices: true, navigation: true, reopen: true, refresh: true, notebookScope: true, attemptScope: true, serverPrecedence: true, saveStates: true, failedStorage: true }));
} finally { await browser.close(); }
