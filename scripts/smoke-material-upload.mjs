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
const errors = [], uploads = [], attachments = [];
let history = [];
let docs = [{ id: 'old', name: '原有笔记.md', kind: 'md', page_count: 1, chunk_count: 1, size_bytes: 100 }], asked;
let releaseUpload;
const uploadGate = new Promise(resolve => { releaseUpload = resolve; });
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1sAAAAASUVORK5CYII=', 'base64');
page.on('pageerror', error => errors.push(error.message));
await page.route('**/api/**', async route => {
  const req = route.request(), url = new URL(req.url()), p = url.pathname;
  const fulfill = json => route.fulfill({ json });
  if (p === '/api/health') return fulfill({ ok: true, service: 'zhixue-notes' });
  if (p === '/api/settings') return fulfill({ model: 'mock', model_configured: true });
  if (p === '/api/sync') return fulfill({ enabled: false, available: false, conflicts: [] });
  if (p === '/api/notebooks') return fulfill({ notebooks: [{ id: 'default', name: '资料上传验收', document_count: docs.length }] });
  if (p === '/api/notes') return fulfill({ notes: [] });
  if (p === '/api/notebooks/default/chat') return fulfill({ exchanges: history, deleted_count: 0 });
  if (p === '/api/documents' && req.method() === 'GET') return fulfill({ documents: docs });
  if (req.method() === 'POST' && ['/api/documents', '/api/question-images', '/api/question-attachments'].includes(p)) {
    assert.equal(url.searchParams.get('notebook_id'), 'default');
    const name = req.postDataBuffer().toString('utf8').match(/filename="([^"]+)"/)[1];
    uploads.push({ p, name });
    if (name === '课程.pdf') await uploadGate;
    if (name === '损坏.pdf') return route.fulfill({ status: 400, json: { detail: '测试文件解析失败' } });
    if (p !== '/api/documents') {
      const id = p === '/api/question-images' ? 'image-1' : `attachment-${attachments.length}`;
      const image = { id, name, kind: p === '/api/question-images' ? 'image' : 'document', url: `/api/question-images/${id}/file`, ocr_text: '题目内容' };
      attachments.push(image); return fulfill({ image });
    }
    const doc = { id: `doc-${docs.length}`, name, kind: name.split('.').at(-1).toLowerCase(), page_count: 1, chunk_count: 1, size_bytes: 100 };
    docs.push(doc); return fulfill({ document: doc });
  }
  if (p === '/api/question-images/image-1/file') return route.fulfill({ contentType: 'image/png', body: pixel });
  if (p === '/api/ask') {
    asked = req.postDataJSON();
    const result = { chat_id: asked.request_id, answer: '资料与图片联合问答成功。', citations: [], mode: 'model', source_mode: 'knowledge_base' };
    history = [{ id: asked.request_id, question: asked.question, images: attachments, reply_to: {}, status: 'complete', result, created_at: '2026-01-01' }];
    return fulfill(result);
  }
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
  await page.waitForFunction(() => !document.querySelector('#send-button').disabled && document.querySelectorAll('.draft-image').length === 6);
  assert.equal(await page.locator('.draft-image').count(), 6);
  assert.equal(await page.locator('[data-document-id="old"] input').isChecked(), false);
  assert.equal(await page.locator('.document-item input:checked').count(), 0);
  assert.equal(uploads.filter(x => x.p === '/api/question-images').length, 1);
  assert.equal(uploads.filter(x => x.p === '/api/documents').length, 0);
  // Attachments stay outside sources, including partial failure, drop and paste.
  await page.locator('#question-image-input').setInputFiles([file('先成功.txt'), file('损坏.pdf')]);
  await page.locator('#toast').filter({ hasText: '测试文件解析失败' }).waitFor();
  assert.equal(await page.locator('.document-item').count(), 1);
  assert.equal(await page.locator('.draft-image').count(), 7);
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
    await page.locator('.draft-document a', { hasText: name }).waitFor();
    await page.waitForFunction(() => !document.querySelector('#send-button').disabled);
  }
  await page.locator('#file-input').setInputFiles(file('侧栏资料.txt'));
  await page.locator('.document-name', { hasText: '侧栏资料.txt' }).waitFor();
  await page.waitForFunction(() => !document.querySelector('#send-button').disabled);
  await page.locator('#question-input').fill('结合资料解答图片中的题目');
  await fs.mkdir('artifacts/material-upload', { recursive: true });
  await page.screenshot({ path: 'artifacts/material-upload/desktop.png' });
  assert.equal(await page.locator('#question-attachment-count').innerText(), '9 个附件');
  assert(await page.locator('#question-image-list').evaluate(e => e.scrollWidth > e.clientWidth));
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 });
    const button = await page.locator('#attach-question-image').boundingBox();
    assert(button.x >= 0 && button.x + button.width <= width);
    assert(await page.locator('#attach-question-image').evaluate(e => e.scrollWidth <= e.clientWidth));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const tray = await page.locator('#question-image-draft').boundingBox();
    const input = await page.locator('#question-input').boundingBox();
    assert(tray.y + tray.height <= input.y, 'attachments must stay above question input');
    assert(input.y + input.height <= 844, 'attachment tray must not push input off screen');
    assert(tray.height <= 260, 'multiple attachments must stay compact');
  }
  await page.screenshot({ path: 'artifacts/material-upload/mobile.png' });
  await page.locator('#question-input').press('Enter');
  await page.locator('.message.assistant').filter({ hasText: '联合问答成功' }).waitFor();
  assert.equal(await page.locator('#question-image-draft').isVisible(), false);
  assert.deepEqual(asked.document_ids, docs.filter(d => d.id !== 'old').map(d => d.id));
  assert.deepEqual(asked.image_ids, attachments.map(item => item.id));
  assert.equal(asked.notebook_id, 'default');
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.message.assistant').filter({ hasText: '联合问答成功' }).waitFor();
  assert.equal(await page.locator('.message.user .message-attachment').count(), 8);
  assert.equal(await page.locator('.message.user img').count(), 1);
  assert.equal(await page.locator('.document-item').count(), 2);
  assert.deepEqual(errors, []);
  console.log('Mixed material upload, picker/sidebar/drop/paste, notebook scope, no source pollution, retained selections, partial failure, history restoration and mobile layout passed.');
} catch (error) { await fs.mkdir('artifacts/material-upload', { recursive: true }); await page.screenshot({ path: 'artifacts/material-upload/failure.png' }); throw error; }
finally { await browser.close(); }
