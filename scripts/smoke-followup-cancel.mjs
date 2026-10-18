import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
const modules=process.env.PLAYWRIGHT_MODULES || path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=await import(pathToFileURL(path.join(modules,'playwright/index.mjs')).href);
const python=process.env.PYTHON || path.resolve('.venv/bin/python');
const base=process.env.APP_URL || 'http://127.0.0.1:8765';
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1440,height:960}});
const page=await context.newPage();
let notebook;
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const api=async(url,method='GET',data)=>{
 const r=await context.request.fetch(base+url,{method,data});
 assert(r.ok(),`${method} ${url}: ${r.status()} ${await r.text()}`);return r.json();
};
function seed(id,question,result,images=[],replyTo={}){
 execFileSync(python,['-c',`
import json,sys
from app import db
d=json.load(sys.stdin)
db.start_exchange(d['id'],d['notebook'],d['question'],d['images'],d['reply'])
if d['result'] is not None: db.finish_exchange(d['id'],d['result'])
`],{input:JSON.stringify({id,notebook:notebook.id,question,result,images,reply:replyTo})});
}
let releaseDelayed;
try{
 notebook=(await api('/api/notebooks','POST',{name:'引用追问与取消验收'})).notebook;
 const parent=randomUUID();
 const parentAnswer='协议规定通信双方的数据格式和交换规则。';
 seed(parent,'网络协议是什么？',{answer:parentAnswer,citations:[],mode:'model'});
 await page.goto(base,{waitUntil:'networkidle'});
 await page.locator('#notebook-select').selectOption(notebook.id);
 await page.waitForFunction(()=>!document.querySelector('#notebook-select').disabled);
 const original=page.locator(`article[data-message-id="${parent}"]`);
 await page.locator('#question-input').fill('请详细说明，并举例');
 await original.locator('.quote-answer').click();
 assert.equal(await page.locator('#question-input').inputValue(),'请详细说明，并举例');
 assert.match(await page.locator('#reply-preview').innerText(),/数据格式/);
 await page.locator('#remove-reply').click();
 assert.equal(await page.locator('#reply-draft').isVisible(),false);
 await original.locator('.quote-answer').click();
 let followup;
 await page.route('**/api/ask',async route=>{
  const payload=route.request().postDataJSON();followup=payload.request_id;
  assert.equal(payload.reply_to_id,parent);
  const reply={id:parent,question:'网络协议是什么？',answer:parentAnswer};
  const result={chat_id:followup,answer:'例如 HTTP 约定请求和响应格式。',citations:[],mode:'model'};
  seed(followup,payload.question,result,[],reply);
  await route.fulfill({json:result});
 });
 await page.locator('#question-input').press('Enter');
 await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);
 assert.equal(await page.locator('#reply-draft').isVisible(),false);
 assert.equal(await page.locator('.quoted-answer').count(),1);
 await page.reload({waitUntil:'networkidle'});
 const restoredQuote=page.locator(`article[data-message-id="user-${followup}"] .quoted-answer`);
 await restoredQuote.waitFor();
 await restoredQuote.locator('summary').click();
 assert.match(await restoredQuote.innerText(),/数据格式/);
 // Hold a mocked model response after persisting its pending record. Cancel uses the real backend.
 await page.unroute('**/api/ask');
 const png=execFileSync(python,['-c',`import sys,io;from PIL import Image;b=io.BytesIO();Image.new('RGB',(100,70),'white').save(b,format='PNG');sys.stdout.buffer.write(b.getvalue())`]);
 const imageResponse=page.waitForResponse(r=>r.url().includes('/api/question-images?') && r.request().method()==='POST');
 await page.locator('#question-image-input').setInputFiles({name:'题目.png',mimeType:'image/png',buffer:png});
 const image=(await(await imageResponse).json()).image;
 await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);
 await page.locator(`article[data-message-id="${parent}"] .quote-answer`).click();
 const waiting=new Promise(resolve=>{releaseDelayed=resolve;});
 let pendingId;
 await page.route('**/api/ask',async route=>{
  const payload=route.request().postDataJSON();pendingId=payload.request_id;
  assert.equal(payload.reply_to_id,parent);
  assert.deepEqual(payload.image_ids,[image.id]);
  seed(pendingId,payload.question,null,[image],{id:parent,question:'网络协议是什么？',answer:parentAnswer});
  await waiting;
  try{await route.abort();}catch{}
 });
 await page.locator('#question-input').fill('结合这张图继续解释');
 await page.locator('#question-input').press('Enter');
 await page.locator('#cancel-generation').waitFor();
 await page.waitForFunction(()=>document.querySelector('#cancel-generation').hidden===false);
 // Wait for the fixture to reach the backend before cancelling.
 for(let i=0;!pendingId && i<40;i++)await new Promise(r=>setTimeout(r,25));
 assert(pendingId);
 await page.locator('#cancel-generation').click();
 await page.waitForFunction(()=>document.querySelector('#cancel-generation').hidden && !document.querySelector('#send-button').disabled);
 releaseDelayed();
 assert.equal(await page.locator('#question-input').inputValue(),'结合这张图继续解释');
 assert.equal(await page.locator('.draft-image').count(),1);
 assert.equal(await page.locator('#reply-draft').isVisible(),true);
 const records=(await api(`/api/notebooks/${notebook.id}/chat`)).exchanges;
 const cancelled=records.find(e=>e.id===pendingId);
 assert.equal(cancelled.status,'error');assert.equal(cancelled.result.cancelled,true);
 assert.equal(cancelled.images[0].id,image.id);
 await page.reload({waitUntil:'networkidle'});
 await page.locator(`article[data-message-id="${pendingId}"].cancelled`).waitFor();
 assert.equal(await page.locator('#cancel-generation').isVisible(),false);
 const restoredPending=randomUUID();
 seed(restoredPending,'刷新后的待取消问题',null,[image],{id:parent,question:'网络协议是什么？',answer:parentAnswer});
 await page.reload({waitUntil:'networkidle'});
 await page.locator('#cancel-generation').waitFor();
 await page.locator('#cancel-generation').click();
 await page.waitForFunction(()=>document.querySelector('#cancel-generation').hidden && !document.querySelector('#send-button').disabled);
 assert.equal(await page.locator('#question-input').inputValue(),'刷新后的待取消问题');
 assert.equal(await page.locator('.draft-image').count(),1);
 assert.equal(await page.locator('#reply-draft').isVisible(),true);
 await fs.mkdir('artifacts/followup-cancel',{recursive:true});
 await page.locator(`article[data-message-id="${parent}"] .quote-answer`).click();
 await page.screenshot({path:'artifacts/followup-cancel/desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.screenshot({path:'artifacts/followup-cancel/mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,quotedFollowup:true,quoteRestored:true,cancelBackendState:true,draftImageAndTextRetained:true,cancelRestored:true,cancelAfterRefresh:true,mobile:true,errors}));
}finally{
 releaseDelayed?.();
 if(notebook){await api(`/api/notebooks/${notebook.id}/chat`,'DELETE');await api(`/api/notebooks/${notebook.id}`,'DELETE');}
 await browser.close();
}
