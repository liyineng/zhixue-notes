// Fixtures only: no personal chat changes and no paid model requests.
import assert from 'node:assert/strict';
import os from 'node:os';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const modules=path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=await import(pathToFileURL(path.join(modules,'playwright/index.mjs')).href);
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[],restores=[],purges=[];page.on('pageerror',e=>errors.push(e.message));
const citation={index:1,document_id:'course',document_name:'课程.md',document_kind:'md',page:4,text:'原始依据',lines:[]};
const active={id:'active',question:'当前保留的问题',images:[],reply_to:{},status:'complete',created_at:'2026-10-18T00:00:00Z',result:{answer:'原有回答 [1]',citations:[citation],mode:'model'}};
const file={id:'file',name:'习题.pdf',kind:'document',url:'/api/question-images/file/file'};
let clearCalls=0;
let history=[active],deleted=[
 {id:'recent',question:'最近删除的失败问答',images:[],reply_to:{},status:'error',deleted_at:'2026-10-19T02:00:00Z',created_at:'2026-10-19T00:00:00Z',result:{detail:'示例请求失败'}},
 {id:'older',question:'较早删除：TCP公式怎么理解？',images:[file],reply_to:{answer:'原引用快照'},status:'complete',deleted_at:'2026-10-19T01:00:00Z',created_at:'2026-10-18T01:00:00Z',result:{answer:String.raw`**结论：\(T=3RTT+2MSL\)** [1]`,citations:[citation],mode:'model'}}
];
let release;const gate=new Promise(resolve=>release=resolve);
await page.route('**/api/**',async route=>{
 const req=route.request(),p=new URL(req.url()).pathname;
 const fulfill=json=>route.fulfill({json});
 if(p==='/api/settings')return fulfill({model:'mock',model_configured:true});
 if(p==='/api/sync')return fulfill({enabled:false,available:false,conflicts:[]});
 if(p==='/api/notebooks')return fulfill({notebooks:[{id:'default',name:'删除记录验收',document_count:1}]});
 if(p==='/api/documents')return fulfill({documents:[{id:'course',name:'课程.md',kind:'md',size_bytes:100,page_count:1,chunk_count:1}]});
 if(p==='/api/notes')return fulfill({notes:[]});
 if(p==='/api/notebooks/default/chat')return fulfill({exchanges:history,deleted_count:deleted.length});
 if(p==='/api/notebooks/default/chat/deleted')return fulfill({exchanges:deleted,retention_limit:5});
 if(p==='/api/notebooks/default/chat/deleted/all'){
  assert.equal(req.method(),'DELETE');clearCalls++;
  if(clearCalls===1)return route.fulfill({status:503,json:{detail:'模拟连接中断，请重试'}});
  const count=deleted.length;deleted=[];
  return fulfill({deleted:true,permanent:true,purged_count:count,deleted_count:0,deleted_image_ids:[]});
 }
 if(p.endsWith('/permanent')){
  assert.equal(req.method(),'DELETE');
  const id=p.split('/').at(-2);purges.push(id);
  assert(deleted.some(item=>item.id===id));deleted=deleted.filter(item=>item.id!==id);
  return fulfill({deleted:true,permanent:true,chat_id:id,deleted_count:deleted.length,deleted_image_ids:[]});
 }
 if(p.endsWith('/restore')){
  const id=p.split('/').at(-2);restores.push(id);
  if(id==='older')await gate;
  const record=deleted.find(item=>item.id===id);assert(record);
  history.push({...record,deleted_at:null});deleted=deleted.filter(item=>item.id!==id);
  return fulfill({restored:true,chat_id:id,deleted_count:deleted.length});
 }
 errors.push(`${req.method()} ${p}`);return route.fulfill({status:404,json:{detail:'Unexpected API'}});
});
try{
 await page.goto(process.env.APP_URL||'http://127.0.0.1:8765',{waitUntil:'networkidle'});
 await page.locator('#question-input').fill('保留我未发送的草稿');
 await page.locator('.citation-disclosure').evaluate(el=>el.open=true);
 await page.evaluate(()=>window.__activeAnswer=document.querySelector('[data-message-id="active"]'));
 await page.getByRole('button',{name:'管理已删除对话',exact:true}).click();
 await page.locator('[data-deleted-id="older"]').waitFor();
 assert.equal(await page.locator('.deleted-chat-card').count(),2);
 await page.locator('[data-deleted-id="older"] > details > summary').click();
 await page.locator('#deleted-chat-dialog .katex').waitFor();
 assert.equal(await page.locator('#deleted-chat-dialog .katex').count(),1);
 assert.equal(await page.locator('#deleted-chat-dialog .katex-error').count(),0);
 assert.equal(await page.locator('#deleted-chat-dialog .deleted-chat-files a').getAttribute('href'),file.url);
 assert.equal(await page.locator('#deleted-chat-dialog .citation-ref').count(),0,'preview has no dead citation buttons');
 await fs.mkdir('artifacts/deleted-chat-panel',{recursive:true});
 await page.screenshot({path:'artifacts/deleted-chat-panel/desktop.png'});
 await page.locator('[data-deleted-id="older"]').getByRole('button',{name:'恢复这条',exact:true}).click();
 await page.waitForFunction(()=>[...document.querySelectorAll('#deleted-chat-list button')].every(button=>button.disabled));
 await page.locator('#close-deleted-chat').click();
 await page.getByRole('button',{name:'管理已删除对话',exact:true}).click();
 assert.equal(await page.locator('.deleted-chat-card').count(),0,'reopening during restore must not expose stale records');
 release();await page.locator('[data-deleted-id="recent"]').waitFor();
 await page.locator('[data-deleted-id="older"]').waitFor({state:'detached'});
 assert.deepEqual(restores,['older']);
 assert.equal(await page.locator('#question-input').inputValue(),'保留我未发送的草稿');
 assert.equal(await page.evaluate(()=>window.__activeAnswer===document.querySelector('[data-message-id="active"]')),true);
 assert(await page.locator('.citation-disclosure').first().evaluate(el=>el.open));
 assert.match(await page.locator('#undo-chat-delete').textContent(),/1/);
 await page.locator('#close-deleted-chat').click();
 await page.reload({waitUntil:'networkidle'});
 await page.getByRole('button',{name:'管理已删除对话',exact:true}).click();
 await page.locator('[data-deleted-id="recent"]').waitFor();
 assert.equal(await page.locator('.deleted-chat-card').count(),1);
 await page.locator('[data-deleted-id="recent"] > details > summary').click();
 assert.match(await page.locator('#deleted-chat-list').textContent(),/示例请求失败/);
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  const box=await page.locator('#deleted-chat-dialog').boundingBox();assert(box.x>=0&&box.x+box.width<=width+1);
  assert(await page.locator('#deleted-chat-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:`artifacts/deleted-chat-panel/mobile-${width}.png`});
 }
 page.once('dialog',dialog=>dialog.dismiss());
 await page.locator('[data-deleted-id="recent"]').getByRole('button',{name:'永久删除',exact:true}).click();
 assert.deepEqual(purges,[],'cancelled confirmation must send no delete');
 assert.equal(await page.locator('.deleted-chat-card').count(),1);
 page.once('dialog',dialog=>{assert.match(dialog.message(),/无法恢复/);dialog.accept();});
 await page.locator('[data-deleted-id="recent"]').getByRole('button',{name:'永久删除',exact:true}).click();
 await page.locator('.deleted-chat-empty').waitFor();
 assert.deepEqual(purges,['recent']);
 assert.deepEqual(restores,['older']);
 assert.equal(history.length,2,'permanent deletion must not restore the record');
 assert.match(await page.locator('#deleted-chat-status').textContent(),/0 \/ 5/);
 await page.keyboard.press('Escape');
 assert(await page.locator('#undo-chat-delete').isDisabled());
 assert(await page.locator('#manage-deleted-chat').isEnabled());
 deleted=[{...active,id:'bulk-a',question:'待清空记录 A',deleted_at:'2026-10-19T03:00:00Z'},
          {...active,id:'bulk-b',question:'待清空记录 B',deleted_at:'2026-10-19T04:00:00Z'}];
 await page.locator('#question-input').fill('清空删除记录时保留此草稿');
 await page.evaluate(()=>window.__beforeClear=document.querySelector('[data-message-id="active"]'));
 await page.getByRole('button',{name:'管理已删除对话',exact:true}).click();
 await page.locator('[data-deleted-id="bulk-a"]').waitFor();
 await page.screenshot({path:'artifacts/deleted-chat-panel/clear-mobile.png'});
 page.once('dialog',dialog=>dialog.dismiss());
 await page.locator('#clear-deleted-chat').click();
 assert.equal(clearCalls,0);
 assert.equal(await page.locator('.deleted-chat-card').count(),2);
 page.once('dialog',dialog=>{assert.match(dialog.message(),/全部 2 条删除记录/);dialog.accept();});
 await page.locator('#clear-deleted-chat').click();
 await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('模拟连接中断'));
 assert.equal(await page.locator('.deleted-chat-card').count(),2,'failed request must preserve retryable records');
 assert(await page.locator('#clear-deleted-chat').isEnabled());
 page.once('dialog',dialog=>dialog.accept());
 await page.locator('#clear-deleted-chat').click();
 await page.locator('.deleted-chat-empty').waitFor();
 assert.equal(clearCalls,2);assert.equal(deleted.length,0);assert.equal(history.length,2);
 assert(await page.locator('#clear-deleted-chat').isDisabled());
 assert(await page.locator('#undo-chat-delete').isDisabled());
 assert.equal(await page.evaluate(()=>window.__beforeClear===document.querySelector('[data-message-id="active"]')),true);
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('#question-input').inputValue(),'清空删除记录时保留此草稿');
 await page.reload({waitUntil:'networkidle'});
 await page.getByRole('button',{name:'管理已删除对话',exact:true}).click();
 await page.locator('.deleted-chat-empty').waitFor();
 assert(await page.locator('#clear-deleted-chat').isDisabled());
 assert.deepEqual(errors,[]);
 console.log('PASS: deleted-record preview, source/attachment snapshots, selective restore, duplicate guard, preserved drafts/DOM, permanent deletion/confirmation, clear-all/cancel/retry, reload, empty state and mobile.');
}finally{await browser.close();}
