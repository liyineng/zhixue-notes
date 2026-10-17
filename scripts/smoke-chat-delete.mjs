import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
const modules = process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const { chromium } = await import(pathToFileURL(path.join(modules,'playwright/index.mjs')).href);
const python = process.env.PYTHON || path.resolve('.venv/bin/python');
const base = process.env.APP_URL || 'http://127.0.0.1:8765';
const browser = await chromium.launch({headless:true});
const context = await browser.newContext({viewport:{width:1440,height:960}});
const page = await context.newPage();
const request = async (url, method='GET', data) => {
 const response=await context.request.fetch(base+url,{method,data});
 assert(response.ok(),`${method} ${url}: ${response.status()} ${await response.text()}`);
 return response.json();
};
let notebook;
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const seed=(id, question, result, error=false)=>execFileSync(python,['-c',`
import json,sys
from app import db
d=json.load(sys.stdin)
db.start_exchange(d['id'],d['notebook'],d['question'])
db.finish_exchange(d['id'],d['result'],error=d['error'])
`],{input:JSON.stringify({id,notebook:notebook.id,question,result,error})});
try {
 notebook=(await request('/api/notebooks','POST',{name:'删除与撤销验收'})).notebook;
 const first=randomUUID(),failed=randomUUID();
 seed(first,'保留和恢复的问题',{answer:'可恢复的回答',citations:[],mode:'extractive'});
 seed(failed,'失败的问题',{detail:'模型暂不可用'},true);
 await page.goto(base,{waitUntil:'networkidle'});
 await page.locator('#notebook-select').selectOption(notebook.id);
 await page.waitForFunction(()=>!document.querySelector('#notebook-select').disabled);
 const remove=(id)=>page.locator(`[data-delete-exchange="${id}"]`);
 assert.equal(await page.locator('.delete-exchange').count(),2);
 page.once('dialog',dialog=>dialog.dismiss());
 await remove(first).click();
 assert.equal(await page.locator('.message').count(),4);
 page.on('dialog',dialog=>dialog.accept());
 await remove(first).click();
 await page.waitForFunction(()=>document.querySelectorAll('.message').length===2);
 assert.match(await page.locator('#undo-chat-delete').innerText(),/1/);
 await page.reload({waitUntil:'networkidle'});
 assert.equal(await page.locator('.message').count(),2);
 await page.locator('#undo-chat-delete').click();
 await page.waitForFunction(()=>document.querySelectorAll('.message').length===4);
 assert.match(await page.locator('.message.user').first().innerText(),/保留和恢复/);
 await remove(failed).click();
 await page.waitForFunction(()=>document.querySelectorAll('.message').length===2);
 await page.locator('#undo-chat-delete').click();
 await page.waitForFunction(()=>document.querySelectorAll('.message').length===4);
 assert.match(await page.locator('.message.assistant.error').innerText(),/模型暂不可用/);
 // The just-sent question must be deletable immediately, without a page refresh.
 const fresh=randomUUID();
 await page.route('**/api/ask',async route=>{
  const payload=route.request().postDataJSON();
  const result={chat_id:fresh,answer:'新的回答',citations:[],mode:'extractive'};
  seed(fresh,payload.question,result);
  await route.fulfill({json:result});
 });
 await page.locator('#question-input').fill('刚发送的问题');
 await page.locator('#question-input').press('Enter');
 await remove(fresh).waitFor();
 await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);
 await remove(fresh).click();
 await page.waitForFunction(()=>document.querySelectorAll('.message').length===4);
 await page.locator('#undo-chat-delete').click();
 await page.waitForFunction(()=>document.querySelectorAll('.message').length===6);
 // A newly failed response carries its persisted ID in an HTTP header too.
 const freshFailure=randomUUID();
 await page.unroute('**/api/ask');
 await page.route('**/api/ask',async route=>{
  const payload=route.request().postDataJSON();
  seed(freshFailure,payload.question,{detail:'新的失败回答'},true);
  await route.fulfill({status:502,headers:{'X-Chat-Id':freshFailure},json:{detail:'新的失败回答'}});
 });
 await page.locator('#question-input').fill('刚失败的问题');
 await page.locator('#question-input').press('Enter');
 await remove(freshFailure).waitFor();
 await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);
 await remove(freshFailure).click();
 await page.waitForFunction(()=>document.querySelectorAll('.message').length===6);
 await fs.mkdir('artifacts/chat-delete',{recursive:true});
 await page.screenshot({path:'artifacts/chat-delete/desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1));
 await page.screenshot({path:'artifacts/chat-delete/mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,cancel:true,specificDelete:true,undoAfterRefresh:true,failedQuestion:true,freshQuestion:true,mobile:true,errors}));
} finally {
 if(notebook){await request(`/api/notebooks/${notebook.id}/chat`,'DELETE');await request(`/api/notebooks/${notebook.id}`,'DELETE');}
 await browser.close();
}
