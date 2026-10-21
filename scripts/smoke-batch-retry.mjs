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
let records=[old,failed], submitted;
await page.route('**/api/**',async route=>{
 const req=route.request(),p=new URL(req.url()).pathname;
 if(p==='/api/health')return route.fulfill({json:{ok:true,service:'zhixue-notes'}});
 if(p==='/api/notebooks')return route.fulfill({json:{notebooks:[{id:'default',name:'恢复验收',document_count:1}]}});
 if(p==='/api/documents')return route.fulfill({json:{documents:[doc]}});
 if(p==='/api/settings')return route.fulfill({json:{model:'mock',model_configured:true}});
 if(p==='/api/sync')return route.fulfill({json:{enabled:false,available:false,conflicts:[]}});
 if(p==='/api/notes')return route.fulfill({json:{notes:[]}});
 if(p==='/api/notebooks/default/chat')return route.fulfill({json:{exchanges:records,deleted_count:0}});
 if(p==='/api/notebooks/default/chat/failed/retry'){
  submitted=req.postDataJSON();const result={chat_id:submitted.request_id,answer:'恢复完成 [1]',mode:'model',citations:old.result.citations};
  records.push({id:submitted.request_id,question:failed.question,status:'complete',images:[],reply_to:{},result});
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
 assert.deepEqual(errors,[]);
 console.log('PASS: failed generation retries saved source configuration, displays retained batch count, preserves completed DOM/disclosure and composer drafts.');
}finally{await browser.close();}
