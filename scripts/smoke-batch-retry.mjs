// Mocked APIs only: checkpoints and retry UI never call a model or mutate user data.
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const {chromium} = await import(pathToFileURL(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs')));
const browser = await chromium.launch({headless:true});
const page = await browser.newPage({viewport:{width:1440,height:960}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const doc={id:'course',name:'选中课件.md',kind:'md',page_count:2,chunk_count:2,size_bytes:20};
const old={id:'old',question:'先前的问题',status:'complete',images:[],reply_to:{},result:{answer:'先前的回答 [1]',mode:'model',citations:[{index:1,document_id:'course',document_name:doc.name,page:1,text:'保存的原文'}]}};
const failed={id:'failed',question:'生成复习提纲',status:'error',images:[],reply_to:{},result:{detail:'第二批生成超时',generation_job:{kind:'summary',payload:{document_ids:['course']},batches:{first:'已完成'},total:2}}};
const emptyFailed={id:'empty-failed',question:'解答原附件中的全部题目',status:'error',images:[],reply_to:{},result:{detail:'后续习题解答超时',generation_job:{kind:'ask',payload:{document_ids:[],image_ids:['attachment']},batches:{first:'已完成'},total:7}}};
const evidenceFailed={id:'evidence-failed',question:'解答原附件中的全部题目',status:'error',images:[],reply_to:{},result:{detail:'核对资料时连接中断',generation_job:{kind:'ask',payload:{document_ids:['course'],image_ids:['attachment']},batches:{},total:0,phases:{evidence:{batches:{first:[],second:[]},total:45}}}}};
let records=[old,failed], submitted, emptyRetried=false, evidenceRetried=false;
await page.route('**/api/**',async route=>{
 const req=route.request(),p=new URL(req.url()).pathname;
 if(p==='/api/health')return route.fulfill({json:{ok:true,service:'zhixue-notes'}});
 if(p==='/api/notebooks')return route.fulfill({json:{notebooks:[{id:'default',name:'恢复验收',document_count:1}]}});
 if(p==='/api/documents')return route.fulfill({json:{documents:[doc]}});
 if(p==='/api/settings')return route.fulfill({json:{model:'mock',model_configured:true}});
 if(p==='/api/sync')return route.fulfill({json:{enabled:false,available:false,conflicts:[]}});
 if(p==='/api/notes')return route.fulfill({json:{notes:[]}});
 if(p==='/api/notebooks/default/chat')return route.fulfill({json:{exchanges:records,deleted_count:0}});
 if(['/api/notebooks/default/chat/failed/retry','/api/notebooks/default/chat/empty-failed/retry','/api/notebooks/default/chat/evidence-failed/retry'].includes(p)){
  const target=p.includes('/empty-failed/')?emptyFailed:p.includes('/evidence-failed/')?evidenceFailed:failed;
  if(target===emptyFailed)emptyRetried=true;
  if(target===evidenceFailed)evidenceRetried=true;
  submitted=req.postDataJSON();const result={chat_id:submitted.request_id,answer:target===emptyFailed?'恢复完成':'恢复完成 [1]',mode:'model',citations:target===emptyFailed?[]:old.result.citations};
  records.push({id:submitted.request_id,question:target.question,status:'complete',images:[],reply_to:{},result});
  return route.fulfill({json:result});
 }
 return route.fulfill({json:{}});
});
try{
 await page.goto(process.env.APP_URL||'http://127.0.0.1:8765',{waitUntil:'networkidle'});
 const previous=page.locator('article[data-message-id="old"]');await previous.locator('details').click();
 const previousNode=await previous.elementHandle();
 await page.locator('#question-input').fill('当前未发送的草稿');
 assert.match(await page.locator('.retry-generation').innerText(),/已保留 1\/2 批/);
 page.once('dialog',dialog=>dialog.accept());await page.locator('.retry-generation').click();
 await page.waitForFunction(()=>[...document.querySelectorAll('.message-body')].some(el=>el.textContent.includes('恢复完成')));
 assert(submitted.request_id);
 assert.equal(await previous.evaluate((node,old)=>node===old,previousNode),true);
 assert.equal(await previous.locator('details').evaluate(el=>el.open),true);
 assert.equal(await page.locator('#question-input').inputValue(),'当前未发送的草稿');
 assert.equal(await page.locator(`article[data-message-id="${submitted.request_id}"]`).count(),1);
 records=[old,emptyFailed];
 await page.reload({waitUntil:'networkidle'});
 page.once('dialog',async dialog=>{assert.match(dialog.message(),/原附件和空来源选择/);await dialog.accept();});
 await page.locator('.retry-generation').click();
 await page.waitForFunction(()=>[...document.querySelectorAll('.message-body')].some(el=>el.textContent.includes('恢复完成')));
 assert.equal(emptyRetried,true,'An attachment answer with an explicit empty source selection can resume');
 records=[old,evidenceFailed];
 await page.reload({waitUntil:'networkidle'});
 assert.match(await page.locator('.retry-generation').innerText(),/已核对 2\/45 批资料/);
 page.once('dialog',dialog=>dialog.accept());await page.locator('.retry-generation').click();
 await page.waitForFunction(()=>[...document.querySelectorAll('.message-body')].some(el=>el.textContent.includes('恢复完成')));
 assert.equal(evidenceRetried,true,'Evidence-only checkpoints must expose the same original-scope retry action');
 assert.deepEqual(errors,[]);
 console.log('PASS: failed generation retries saved source configuration (including attachment-only empty selections), displays retained batch count, preserves completed DOM/disclosure and composer drafts.');
}finally{await browser.close();}
