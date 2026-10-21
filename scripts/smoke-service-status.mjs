// Isolated service monitor: mocked health responses only, no models or data writes.
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let mode = 'healthy', gate;
await page.route('**/api/health', async route => {
  if (mode === 'delayed') await new Promise(resolve => { gate = resolve; });
  if (mode === 'down') return route.abort('connectionrefused');
  if (mode === 'other') return route.fulfill({ json: { ok: true, service: 'another-service' } });
  return route.fulfill({ json: { ok: true, service: 'zhixue-notes' } });
});
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
await page.route(base + '/connection-monitor-test', route => route.fulfill({
  contentType: 'text/html', body: '<!doctype html><html><head><meta charset="UTF-8"><link rel="stylesheet" href="/static/styles.css"></head><body><main class="app-shell"><textarea id="draft">未发送的内容</textarea><article id="answer">已完成回答<details open><summary>引用</summary>保留原文</details></article></main></body></html>',
}));
try {
  await page.goto(base + '/connection-monitor-test');
  await page.evaluate(async () => {
    const { initializeServiceStatus } = await import('/static/service-status.js');
    window.__answer = document.getElementById('answer');
    window.__generating = false;
    window.__recovered = 0;
    window.__monitor = initializeServiceStatus({
      isGenerating: () => window.__generating,
      onReconnect: () => window.__recovered++,
      intervalMs: 30, slowGenerationMs: 60, timeoutMs: 300,
    });
    await window.__monitor.check();
  });
  assert.equal(await page.locator('#service-status').isVisible(), false);
  assert.equal(await page.evaluate(() => window.__monitor.getStatus().state), 'connected');
  await page.evaluate(() => window.__generating = true);
  await page.waitForFunction(() => !document.getElementById('service-status').hidden);
  assert.match(await page.locator('#service-status').innerText(), /连接正常[\s\S]*当前任务仍在处理/);
  mode = 'down';
  await page.evaluate(() => window.__monitor.check());
  await page.waitForFunction(() => document.getElementById('service-status').dataset.state === 'disconnected');
  assert.match(await page.locator('#service-status').innerText(), /连接已断开[\s\S]*start.command[\s\S]*start.bat/);
  assert.equal(await page.locator('#draft').inputValue(), '未发送的内容');
  assert.equal(await page.evaluate(() => window.__answer === document.getElementById('answer') && document.querySelector('details').open), true);
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(() => document.querySelector('.app-shell').getBoundingClientRect().top >= document.getElementById('service-status').getBoundingClientRect().bottom);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  mode = 'other';
  await page.evaluate(() => window.__monitor.check());
  assert.equal(await page.evaluate(() => window.__monitor.getStatus().state), 'disconnected');
  mode = 'delayed';
  await page.locator('#service-status button').evaluate(button => button.click());
  await page.waitForFunction(() => document.getElementById('service-status').dataset.state === 'reconnecting');
  assert.equal(await page.locator('#service-status button').isDisabled(), true);
  mode = 'healthy';
  gate?.();
  await page.waitForFunction(() => window.__monitor.getStatus().state === 'connected');
  assert.equal(await page.evaluate(() => window.__recovered), 1);
  await page.evaluate(() => window.__generating = false);
  await page.waitForFunction(() => document.getElementById('service-status').hidden);
  assert.equal(await page.locator('#draft').inputValue(), '未发送的内容');
  assert.equal(await page.evaluate(() => window.__answer === document.getElementById('answer') && document.querySelector('details').open), true);
  await page.evaluate(() => window.__monitor.stop());
  assert.equal(await page.locator('#service-status').count(), 0);
  assert.deepEqual(errors, []);
  console.log('Healthy/slow generation/disconnected/reconnecting/service identity and retained drafts/answer DOM passed.');
} finally { await browser.close(); }
