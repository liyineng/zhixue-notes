// Mock upload/model APIs so no personal files, notebooks or paid requests change.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
const errors = [], uploads = [];
let docs = [{ id: 'old', name: '原有笔记.md', kind: 'md', page_count: 1, chunk_count: 1, size_bytes: 100 }], asked;
let releaseUpload;
const uploadGate = new Promise(resolve => { releaseUpload = resolve; });
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1sAAAAASUVORK5CYII=', 'base64');
page.on('pageerror', error => errors.push(error.message));
await page.route('**/api/**', async route => {
  const req = route.request(), url = new URL(req.url()), p = url.pathname;
  const fulfill = json => route.fulfill({ json });
  if (p === '/api/settings') return fulfill({ model: 'mock', model_configured: true });
  if (p === '/api/sync') return fulfill({ enabled: false, available: false, conflicts: [] });
  if (p === '/api/notebooks') return fulfill({ notebooks: [{ id: 'default', name: '资料上传验收', document_count: docs.length }] });
  if (p === '/api/notes') return fulfill({ notes: [] });
  if (p === '/api/notebooks/default/chat') return fulfill({ exchanges: [], deleted_count: 0 });
  if (p === '/api/documents' && req.method() === 'GET') return fulfill({ documents: docs });
  if (req.method() === 'POST' && ['/api/documents', '/api/question-images'].includes(p)) {
    assert.equal(url.searchParams.get('notebook_id'), 'default');
    const name = req.postDataBuffer().toString('utf8').match(/filename="([^"]+)"/)[1];
    uploads.push({ p, name });
    if (name === '课程.pdf') await uploadGate;
    if (name === '损坏.pdf') return route.fulfill({ status: 400, json: { detail: '测试文件解析失败' } });
    if (p === '/api/question-images') return fulfill({ image: { id: 'image-1', name, url: '/api/question-images/image-1/file', ocr_text: '题目内容' } });
    const doc = { id: `doc-${docs.length}`, name, kind: name.split('.').at(-1).toLowerCase(), page_count: 1, chunk_count: 1, size_bytes: 100 };
    docs.push(doc); return fulfill({ document: doc });
  }
  if (p === '/api/question-images/image-1/file') return route.fulfill({ contentType: 'image/png', body: pixel });
  if (p === '/api/ask') { asked = req.postDataJSON(); return fulfill({ chat_id: asked.request_id, answer: '资料与图片联合问答成功。', citations: [], mode: 'model', source_mode: 'knowledge_base' }); }
  errors.push(`Unexpected API ${req.method()} ${p}`);
  return route.fulfill({ status: 404, json: { detail: 'Unexpected mocked API' } });
});
const file = (name, mimeType = 'text/plain') => ({ name, mimeType, buffer: name.endsWith('.png') ? pixel : Buffer.from('mock upload fixture') });
try {
  await page.goto(process.env.APP_URL || 'http://127.0.0.1:8765', { waitUntil: 'networkidle' });
  await page.locator('[data-document-id="old"] input').uncheck();
  assert.equal(await page.locator('#attach-question-image').innerText(), '上传资料');
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#attach-question-image').click();
  const picker = await chooser;
  const accept = await picker.element().getAttribute('accept');
  for (const suffix of ['.pdf', '.pptx', '.docx', '.txt', '.md', '.png', '.jpg', '.webp']) assert(accept.includes(suffix));
  await picker.setFiles([file('课程.pdf', 'application/pdf'), file('题目.png', 'image/png'), file('课件.PPTX'), file('讲义.docx'), file('知识.md'), file('补充.txt')]);
  await page.waitForFunction(() => document.querySelector('#send-button').disabled);
  assert(await page.locator('#notebook-select').isDisabled());
  releaseUpload();
  await page.waitForFunction(() => !document.querySelector('#send-button').disabled && document.querySelectorAll('.document-item').length === 6);
  assert.equal(await page.locator('.draft-image').count(), 1);
  assert.equal(await page.locator('[data-document-id="old"] input').isChecked(), false);
  assert.equal(await page.locator('.document-item input:checked').count(), 5);
  assert.equal(uploads.filter(x => x.p === '/api/question-images').length, 1);
  assert.equal(uploads.filter(x => x.p === '/api/documents').length, 5);
  // A later failed document must not hide an earlier successful import or remove the image draft.
  await page.locator('#question-image-input').setInputFiles([file('先成功.txt'), file('损坏.pdf')]);
  await page.locator('#toast').filter({ hasText: '测试文件解析失败' }).waitFor();
  assert.equal(await page.locator('.document-item').count(), 7);
  assert.equal(await page.locator('.draft-image').count(), 1);
  assert.equal(await page.locator('#send-button').isDisabled(), false);
  const before = uploads.length;
  await page.locator('#question-image-input').setInputFiles([file('不支持.zip')]);
  await page.locator('#toast').filter({ hasText: '不支持 不支持.zip' }).waitFor();
  assert.equal(uploads.length, before);
  await page.locator('#question-image-input').setInputFiles([file('超量1.png'), file('超量2.png'), file('超量3.png'), file('不应导入.txt')]);
  await page.locator('#toast').filter({ hasText: '最多附带 3 张' }).waitFor();
  assert.equal(uploads.length, before);
  for (const [eventType, name] of [['drop', '拖入资料.txt'], ['paste', '粘贴资料.txt']]) {
    await page.locator('#question-input').evaluate((element, { eventType, name }) => {
      const data = new DataTransfer(); data.items.add(new File(['课程资料'], name, { type: 'text/plain' }));
      element.dispatchEvent(eventType === 'drop'
        ? new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: data })
        : new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: data }));
    }, { eventType, name });
    await page.locator('.document-name', { hasText: name }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#send-button').disabled);
  }
  await page.locator('#file-input').setInputFiles(file('侧栏资料.txt'));
  await page.locator('.document-name', { hasText: '侧栏资料.txt' }).waitFor();
  await page.waitForFunction(() => !document.querySelector('#send-button').disabled);
  await page.locator('#question-input').fill('结合资料解答图片中的题目');
  await fs.mkdir('artifacts/material-upload', { recursive: true });
  await page.screenshot({ path: 'artifacts/material-upload/desktop.png' });
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 });
    const button = await page.locator('#attach-question-image').boundingBox();
    assert(button.x >= 0 && button.x + button.width <= width);
    assert(await page.locator('#attach-question-image').evaluate(e => e.scrollWidth <= e.clientWidth));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  }
  await page.screenshot({ path: 'artifacts/material-upload/mobile.png' });
  await page.locator('#question-input').press('Enter');
  await page.locator('.message.assistant').filter({ hasText: '联合问答成功' }).waitFor();
  assert.deepEqual(asked.document_ids, docs.filter(d => d.id !== 'old').map(d => d.id));
  assert.deepEqual(asked.image_ids, ['image-1']);
  assert.equal(asked.notebook_id, 'default');
  assert.deepEqual(errors, []);
  console.log('Mixed material upload, picker/sidebar/drop/paste, notebook scope, retained selections, partial failure, validation, mobile layout and image question passed.');
} catch (error) { await fs.mkdir('artifacts/material-upload', { recursive: true }); await page.screenshot({ path: 'artifacts/material-upload/failure.png' }); throw error; }
finally { await browser.close(); }
