// Isolated status panel: mocked API and recognition, no user data or model calls.
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = []; page.on('pageerror', error => errors.push(error.message));
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
await page.route('**/__document-status-test', r => r.fulfill({contentType:'text/html',body:'<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/static/styles.css"></head><body></body></html>'}));
try {
  await page.goto(`${base}/__document-status-test`);
  await page.evaluate(async () => {
    const { initializeDocumentStatus } = await import('/static/document-status.js');
    window.scope = 'fixture-notebook'; window.calls = []; window.updated = 0; window.failRetry = true;
    window.report = {document_id:'fixture-doc',kind:'pdf',name:'课程.pdf',available:true,page_count:3,notice:'图片候选可能是图表，不代表完整知识覆盖。',error:'',
      summary:{indexed_text_pages:[1],recognized_image_pages:[],recognized_formula_pages:[],pending_pages:[2]}, pages:[
        {page:1,indexed_text:true,status:'text_ready',label:'文字已提取',detail:'可用于检索。',retryable:false},
        {page:2,image_candidate:true,status:'pending',label:'图片/公式待核验',detail:'该页图片尚未识别。',retryable:true},
        {page:3,status:'empty',label:'未提取到文字',detail:'请结合原文检查。',retryable:false},
      ]};
    window.panel = initializeDocumentStatus({notebookId:()=>scope,toast(){},onUpdated(){updated++},api:async (url,options) => {
      calls.push({url,body:options?.body ? JSON.parse(options.body) : null});
      if (window.holdRequest) await new Promise(resolve => { window.releaseRequest = resolve; });
      if (options?.method === 'POST') {
        if (failRetry) throw new Error('模拟视觉模型连接中断');
        const result = structuredClone(report); result.pages[1] = {...result.pages[1],status:'recognized',label:'已缓存识别结果',recognized_images:true,recognition_methods:['vision'],retryable:false};
        result.summary.pending_pages = []; result.summary.recognized_image_pages = [2]; result.retry = {requested_pages:[2],added_pages:[2],unread_pages:[]}; return result;
      }
      return structuredClone(report);
    }});
    panel.open({id:'fixture-doc',kind:'pdf',name:'课程.pdf'});
  });
  const dialog = page.locator('.document-status-dialog');
  await page.locator('.document-status-page').nth(2).waitFor();
  assert.equal(await page.evaluate(() => calls.length), 1, 'opening is read-only');
  assert.match((await page.evaluate(() => calls[0].url)), /notebook_id=fixture-notebook/);
  assert.equal(await page.locator('.document-status-page input:disabled').count(), 2);
  await page.getByText('全选待识别页', {exact:true}).click();
  assert.equal(await page.locator('.document-status-page input:checked').count(), 1);
  await page.getByText('全不选', {exact:true}).click();
  assert.equal(await page.locator('.document-status-page input:checked').count(), 0);
  await page.getByRole('checkbox', {name:'选择第 2 页',exact:true}).check();
  await page.getByRole('button', {name:'识别所选 1 页',exact:true}).click();
  await page.waitForFunction(() => document.querySelector('.document-status-message').textContent.includes('模拟视觉模型'));
  assert.equal(await page.locator('.document-status-page input:checked').count(), 1, 'retry failure preserves targeted pages');
  assert.deepEqual(await page.evaluate(() => calls[1].body), {notebook_id:'fixture-notebook',pages:[2]});
  await page.evaluate(() => { failRetry = false; });
  await page.getByRole('button', {name:'识别所选 1 页',exact:true}).click();
  await page.waitForFunction(() => window.updated === 1);
  assert.equal(await page.locator('.document-status-page input:checked').count(), 0);
  assert.equal(await page.locator('.document-status-page input:disabled').count(), 3);
  assert.match(await page.locator('.document-status-message').innerText(), /图片已识别 1 页.*所选页识别已完成/);
  assert.match(await page.locator('.document-status-original').nth(1).getAttribute('href'), /fixture-doc\/file#page=2/);
  const output = path.resolve('artifacts/document-status'); await fs.mkdir(output,{recursive:true});
  await page.screenshot({path:path.join(output,'desktop.png')});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await dialog.evaluate(el => el.scrollWidth > el.clientWidth), false);
  const retry = await page.getByRole('button',{name:'识别所选页',exact:true}).boundingBox();
  assert(retry.x >= 0 && retry.x + retry.width <= 390);
  await page.screenshot({path:path.join(output,'mobile.png')});
  // A late report cannot replace another document/notebook's panel.
  await page.getByRole('button',{name:'关闭资料解析情况'}).click();
  await page.evaluate(() => { holdRequest = true; panel.open({id:'old-doc',kind:'pdf',name:'旧资料.pdf'}); });
  await page.waitForFunction(() => typeof window.releaseRequest === 'function');
  await page.evaluate(() => { panel.close(); scope = 'other-notebook'; holdRequest = false; panel.open({id:'new-doc',kind:'pdf',name:'新资料.pdf'}); });
  await page.locator('.document-status-page').nth(2).waitFor();
  await page.evaluate(() => releaseRequest());
  assert.match(await page.locator('#document-status-title').innerText(), /新资料/);
  assert.match(await page.evaluate(() => calls.at(-1).url), /new-doc\/status\?notebook_id=other-notebook/);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,readOnlyInspection:true,pageScopedRetry:true,failureRetry:true,sourceScope:true,mobile:true,staleResponseGuard:true}));
} finally { await browser.close(); }
