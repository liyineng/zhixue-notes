// Mocked notebook + preview response: never uploads fixtures or creates user data.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
const modules = process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const output = path.resolve('artifacts/presentation-preview');
await fs.mkdir(output, { recursive: true });
const pdf = execFileSync(process.env.PYTHON || path.resolve('.venv/bin/python'), ['-c', `
import sys,pymupdf
with pymupdf.open() as pdf:
 for label in ['SLIDE ONE', 'SLIDE TWO']:
  page=pdf.new_page(width=720,height=405)
  page.insert_text((60,90),label,fontsize=30)
 sys.stdout.buffer.write(pdf.tobytes())
`]);
let fail = false;
let delayed = null;
let previewRequests = 0;
const doc = { id: 'preview-fixture', name: 'preview-fixture.pptx', kind: 'pptx', notebook_id: 'default', page_count: 2, chunk_count: 2, size_bytes: 1000 };
const otherDoc = { ...doc, id: 'preview-other', name: 'other-preview.pptx' };
await page.route('**/api/notebooks', route => route.fulfill({ json: { notebooks: [{ id: 'default', name: 'Preview tests', document_count: 2 }] } }));
await page.route('**/api/documents?*', route => route.fulfill({ json: { documents: [doc, otherDoc] } }));
await page.route('**/api/sync', route => route.fulfill({ json: { enabled: false, available: false, conflicts: [], revision: '', folder: '' } }));
await page.route('**/api/notebooks/default/chat', route => route.fulfill({ json: { exchanges: [{
  id: 'preview-chat', question: 'Explain the second slide', status: 'complete', created_at: '2026-01-01', images: [], reply_to: {},
  result: { answer: 'Slide two [1] and [2], unmatched [3], first page [4], another document [5].', mode: 'model', source_mode: 'knowledge_base', citations: [{
    index: 1, document_id: doc.id, document_name: doc.name, document_kind: 'pptx', page: 2, text: 'SLIDE TWO', lines: [{ page: 2, text: 'SLIDE TWO', bbox: null }],
  }, { index: 2, document_id: doc.id, document_name: doc.name, document_kind: 'pptx', page: 2, text: 'SLIDE TWO', lines: [] },
  { index: 3, document_id: doc.id, document_name: doc.name, document_kind: 'pptx', page: 2, text: 'Unmatched quote in citation', lines: [] },
  { index: 4, document_id: doc.id, document_name: doc.name, document_kind: 'pptx', page: 1, text: 'SLIDE ONE', lines: [] },
  { index: 5, document_id: otherDoc.id, document_name: otherDoc.name, document_kind: 'pptx', page: 1, text: 'SLIDE ONE', lines: [] }] },
}], deleted_count: 0 } }));
await page.route('**/api/documents/preview-fixture/chunks', route => route.fulfill({ json: { chunks: [{ page: 1, text: 'Extracted slide text', index: 1 }] } }));
await page.route('**/api/documents/preview-fixture/preview-lines?*', route => route.fulfill({ json: { lines: [{ page: 2, text: 'SLIDE TWO', bbox: [60, 57, 260, 100] }] } }));
await page.route('**/api/documents/preview-fixture/preview', async route => {
  previewRequests++;
  if (delayed) await delayed;
  if (fail) await route.fulfill({ status: 503, json: { detail: 'PPT 原页预览需要安装 LibreOffice。' } });
  else await route.fulfill({ contentType: 'application/pdf', body: pdf });
});
await page.route('**/api/documents/preview-other/preview', route => route.fulfill({ contentType: 'application/pdf', body: pdf }));
await page.route('**/api/documents/preview-other/preview-lines?*', route => route.fulfill({ json: { lines: [] } }));
const errors = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.locator('.document-name').first().click();
  await page.waitForFunction(() => document.querySelector('#page-total').textContent === '/ 2');
  await page.locator('#next-page').click();
  await page.waitForFunction(() => document.querySelector('#page-input').value === '2');
  assert(await page.locator('#pdf-canvas').evaluate(canvas => canvas.width > 0));
  await page.locator('#prev-page').click();
  await page.locator('.citation-ref').first().click();
  await page.waitForFunction(() => document.querySelector('#page-input').value === '2');
  await page.locator('.highlight-box[data-citations="1,2"]').waitFor();
  assert.equal(await page.locator('.highlight-box .highlight-label').count(), 2);
  assert.match(await page.locator('.preview-location-note').innerText(), /\[3\]/);
  assert.notEqual(await page.locator('.highlight-label').nth(0).evaluate(el => el.style.getPropertyValue('--citation-color')), await page.locator('.highlight-label').nth(1).evaluate(el => el.style.getPropertyValue('--citation-color')));
  await page.locator('.citation-ref').nth(1).click();
  await page.waitForFunction(() => document.querySelector('#highlight-legend .citation-ref[aria-label="查看引用 2"]').getAttribute('aria-pressed') === 'true');
  await page.screenshot({ path: path.join(output, 'preview.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.highlight-box').first().waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: path.join(output, 'highlights-mobile.png') });
  await page.setViewportSize({ width: 1440, height: 960 });
  // Force a fresh load so a missing converter can be simulated independently.
  fail = true;
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.document-name').first().click();
  await page.locator('.preview-warning').waitFor();
  assert.match(await page.locator('.preview-warning').innerText(), /LibreOffice/);
  await page.waitForFunction(() => document.querySelector('#source-text').textContent.includes('Extracted slide text'));
  const popupPromise = page.waitForEvent('popup');
  await page.locator('#open-file').click();
  const popup = await popupPromise;
  assert.match(popup.url(), /\/api\/documents\/preview-fixture\/file/);
  await popup.close();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, 'fallback-mobile.png') });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  // Failure preserves the selected quote and page, with a direct retry there.
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator('#messages .citation-ref').nth(2).click();
  await page.locator('#preview-recovery').waitFor();
  assert.match(await page.locator('#source-text').innerText(), /\[3\].*第 2 页/);
  assert.match(await page.locator('#source-text').innerText(), /Unmatched quote in citation/);
  assert.doesNotMatch(await page.locator('#source-text').innerText(), /Extracted slide text/);
  assert.equal(await page.locator('#page-input').inputValue(), '2');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.locator('#retry-preview').isVisible(), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  fail = false;
  await page.locator('#retry-preview').click();
  await page.waitForFunction(() => document.querySelector('#page-total').textContent === '/ 2'
    && document.querySelector('#page-input').value === '2'
    && document.querySelector('#pdf-canvas').width > 0);
  assert.equal(await page.locator('#viewer-stage').isVisible(), true, 'Recovered citation must show its original page after a text fallback');
  assert.equal(await page.locator('#pdf-canvas').isVisible(), true);
  assert.equal(await page.locator('#viewer-document').evaluate(el => el.classList.contains('is-text')), false);
  assert.equal(await page.locator('#preview-recovery').isVisible(), false);
  await page.locator('.highlight-box[data-citations="1,2"]').waitFor();
  await page.screenshot({ path: path.join(output, 'fallback-recovered.png') });

  // Multiple citations of a document still loading share its request; latest page wins.
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.reload({ waitUntil: 'networkidle' });
  let release;
  delayed = new Promise(resolve => { release = resolve; });
  const before = previewRequests;
  const sameRequest = page.waitForRequest(request => request.url().endsWith('/preview-fixture/preview'));
  await page.locator('#messages .citation-ref').nth(3).click();
  await sameRequest;
  await page.locator('#messages .citation-ref').first().click();
  assert.equal(previewRequests - before, 1, 'Same-document citations must share the pending preview');
  release(); delayed = null;
  await page.waitForFunction(() => document.querySelector('#page-input').value === '2'
    && document.querySelector('#pdf-canvas').width > 0);
  await page.locator('.highlight-box[data-citations="1,2"]').waitFor();
  assert.match(await page.locator('#source-text').innerText(), /\[1\].*第 2 页/);
  assert.equal(previewRequests - before, 1);

  // A late document response cannot replace the more recently selected source.
  await page.reload({ waitUntil: 'networkidle' });
  delayed = new Promise(resolve => { release = resolve; });
  const staleRequest = page.waitForRequest(request => request.url().endsWith('/preview-fixture/preview'));
  await page.locator('#messages .citation-ref').first().click();
  await staleRequest;
  await page.locator('#messages .citation-ref').nth(4).click();
  await page.waitForFunction(() => document.querySelector('#viewer-name').textContent === 'other-preview.pptx'
    && document.querySelector('#page-input').value === '1'
    && document.querySelector('#pdf-canvas').width > 0);
  const staleResponse = page.waitForResponse(response => response.url().endsWith('/preview-fixture/preview'));
  release(); delayed = null;
  await staleResponse;
  await page.waitForLoadState('networkidle');
  assert.equal(await page.locator('#viewer-name').innerText(), otherDoc.name);
  assert.match(await page.locator('#source-text').innerText(), /\[5\].*other-preview.pptx/);
  assert.equal(await page.locator('#page-input').inputValue(), '1');
  assert.equal(await page.locator('#pdf-canvas').isVisible(), true);
  assert.deepEqual(errors, []);
  console.log('PPT preview, paging, preserved citation retry, shared pending requests, latest-source guard, original download and mobile layout passed.');
} catch (error) {
  console.error({ browserErrors: errors, toast: await page.locator('#toast').textContent().catch(() => '') });
  throw error;
} finally { await browser.close(); }
