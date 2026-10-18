// UI-only mock transport: never sends test data to the user's real iCloud folder.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const modules = process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const output = path.resolve('artifacts/cloud-sync');
await fs.mkdir(output, { recursive: true });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
let data = { enabled: false, available: true, folder: '/iCloud Drive/智学笔记同步', conflicts: [], revision: '', devices: 0, running: false };
let resolved;
const version = 'a'.repeat(64);
await page.route('**/api/sync{,/**}', async route => {
  const request = route.request();
  if (request.method() === 'PUT') data.enabled = request.postDataJSON().enabled;
  if (request.url().endsWith('/run')) {
    data = { ...data, revision: 'updated', devices: 2, last_sync: new Date().toISOString(), conflicts: [{
      key: 'notes/example', kind: 'notes', can_keep_both: true,
      choices: [{ version, title: '复习笔记', preview: '<script>unsafe()</script>设备 A 的修改', deleted: false },
        { version: 'b'.repeat(64), title: '复习笔记', preview: '设备 B 的修改', deleted: false }],
    }] };
  }
  if (request.url().endsWith('/resolve')) {
    resolved = request.postDataJSON();
    data = { ...data, revision: 'resolved', conflicts: [] };
  }
  await route.fulfill({ json: data });
});
try {
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.locator('#notebook-select:not([disabled])').waitFor();
  await page.locator('#sync-button').click();
  await page.locator('#sync-enabled').check();
  await page.locator('.sync-conflict').waitFor();
  await page.waitForFunction(() => document.querySelector('#sync-status').textContent.includes('本机合并完成'));
  assert.match(await page.locator('#sync-status').innerText(), /本机合并完成/);
  assert.match(await page.locator('.sync-preview').innerText(), /<script>/);
  assert.equal(await page.locator('.sync-preview script').count(), 0);
  await page.locator('.sync-conflict select').selectOption('b'.repeat(64));
  assert.equal(await page.locator('.sync-preview').innerText(), '设备 B 的修改');
  await page.screenshot({ path: path.join(output, 'desktop.png') });
  await page.getByRole('button', { name: '全部保留为笔记' }).click();
  await page.waitForFunction(() => !document.querySelector('.sync-conflict'));
  assert.equal(resolved.keep_both, true);
  assert.equal(resolved.version, 'b'.repeat(64));
  await page.locator('#sync-enabled').uncheck();
  await page.waitForFunction(() => document.querySelector('#sync-button').textContent.includes('未开启'));
  await page.locator('#close-sync').click();
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await page.locator('[data-pane="sources"]').click();
    await page.locator('#sync-button').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await page.locator('#sync-dialog').evaluate(el => el.scrollWidth > el.clientWidth), false);
    if (width === 390) await page.screenshot({ path: path.join(output, 'mobile.png') });
    await page.locator('#close-sync').click();
  }
  assert.deepEqual(errors, []);
  console.log('Cloud sync UI smoke passed; no real iCloud writes.');
} finally { await browser.close(); }
