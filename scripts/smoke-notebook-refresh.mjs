// Browser-only fixtures: no changes to local notebooks, sources, or model settings.
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.route('**/api/**', route => {
  const p = new URL(route.request().url()).pathname;
  const fulfill = json => route.fulfill({ json });
  if (p === '/api/notebooks') return fulfill({ notebooks: [{ id: 'default', name: '通信电子线路', document_count: 0 }, { id: 'networks', name: '计算机网络', document_count: 0 }] });
  if (p === '/api/documents') return fulfill({ documents: [] });
  if (p === '/api/notes') return fulfill({ notes: [] });
  if (p === '/api/settings') return fulfill({ model_configured: true, model: 'mock' });
  if (p === '/api/sync') return fulfill({ enabled: false, available: false, conflicts: [] });
  if (/\/api\/notebooks\/[^/]+\/chat$/.test(p)) return fulfill({ exchanges: [], deleted_count: 0 });
  errors.push(`Unexpected API ${p}`);
  return route.fulfill({ status: 404, json: { detail: 'Unexpected mocked API' } });
});
const ready = () => page.waitForFunction(() => document.querySelector('#notebook-select').options.length === 2 && !document.querySelector('#notebook-select').disabled);
const switchTo = async id => { await page.locator('#notebook-select').selectOption(id); await ready(); };
try {
  // A course link must honor its explicit target on initial navigation.
  await page.goto(`${base}/?notebook_id=default&keep=value#reading`, { waitUntil: 'networkidle' });
  await ready();
  assert.equal(await page.locator('#notebook-select').inputValue(), 'default');
  const historyLength = await page.evaluate(() => history.length);
  await switchTo('networks');
  await page.reload({ waitUntil: 'networkidle' }); await ready();
  assert.equal(await page.locator('#notebook-select').inputValue(), 'networks', 'refresh must retain latest notebook instead of stale URL target');
  const url = new URL(page.url());
  assert.equal(url.searchParams.get('notebook_id'), 'networks');
  assert.equal(url.searchParams.get('keep'), 'value'); assert.equal(url.hash, '#reading');
  assert.equal(await page.evaluate(() => history.length), historyLength);
  assert.equal(await page.locator('#notebook-title').innerText(), '计算机网络');
  // URL-free entry points continue restoring local preferences without adding parameters.
  await page.goto(base, { waitUntil: 'networkidle' }); await ready();
  assert.equal(await page.locator('#notebook-select').inputValue(), 'networks');
  await switchTo('default');
  await page.reload({ waitUntil: 'networkidle' }); await ready();
  assert.equal(await page.locator('#notebook-select').inputValue(), 'default');
  assert.equal(new URL(page.url()).search, '');
  // Opening a different explicit link overrides the preference, while an invalid
  // link is normalized to the actual available notebook after loading.
  await page.goto(`${base}/?notebook_id=networks`, { waitUntil: 'networkidle' }); await ready();
  assert.equal(await page.locator('#notebook-select').inputValue(), 'networks');
  await page.goto(`${base}/?notebook_id=missing`, { waitUntil: 'networkidle' }); await ready();
  assert.equal(new URL(page.url()).searchParams.get('notebook_id'), await page.locator('#notebook-select').inputValue());
  // Quiz return links open applications once. Ordinary refresh always starts
  // at sources, even if a stored library preference still says applications.
  await page.goto(`${base}/?notebook_id=default&library=applications&keep=value#reading`, { waitUntil: 'networkidle' }); await ready();
  assert.equal(await page.locator('#applications-tab').getAttribute('aria-selected'), 'true');
  assert.equal(new URL(page.url()).searchParams.has('library'), false);
  assert.equal(new URL(page.url()).searchParams.get('keep'), 'value');
  assert.equal(new URL(page.url()).hash, '#reading');
  await page.reload({ waitUntil: 'networkidle' }); await ready();
  assert.equal(await page.locator('#sources-tab').getAttribute('aria-selected'), 'true');
  await page.locator('#applications-tab').click();
  await page.reload({ waitUntil: 'networkidle' }); await ready();
  assert.equal(await page.locator('#sources-tab').getAttribute('aria-selected'), 'true');
  assert.deepEqual(errors, []);
  console.log('Notebook refresh, course links, one-time quiz return tab, default sources, URL/hash preservation and invalid-target fallback passed.');
} finally { await browser.close(); }
