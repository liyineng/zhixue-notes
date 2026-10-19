// Fixture-only notes UX checks; no personal data or model calls.
import assert from 'node:assert/strict';
import os from 'node:os';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const modules=path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=await import(pathToFileURL(path.join(modules,'playwright/index.mjs')).href);
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const citations=[{index:1,document_id:'course',document_name:'TCP.md',document_kind:'md',page:null,chunk_id:'chunk',text:'握手与挥手的原文',lines:[]},
 {index:2,document_id:'gone',document_name:'旧课件.pdf',document_kind:'pdf',page:7,chunk_id:'old',text:'已经移走的课件原文'}];
const answer=String.raw`## TCP 时间计算

**结论：\(T=3RTT+2MSL\)** [1]。

| 阶段 | 时间 |
|---|---|
| 连接建立 | \(RTT\) |

旧资料中的补充说明 [2]。

\`\`\`text
array[99]
\`\`\``.replaceAll('\\`','`');
let notes=[],posts=0,patches=0,manualPosts=0;
let release;const gate=new Promise(resolve=>release=resolve);
await page.route('**/api/**',async route=>{
 const req=route.request(),p=new URL(req.url()).pathname,method=req.method();const fulfill=json=>route.fulfill({json});
 if(p==='/api/settings')return fulfill({model:'mock',model_configured:true});
 if(p==='/api/sync')return fulfill({enabled:false,available:false,conflicts:[]});
 if(p==='/api/notebooks')return fulfill({notebooks:[{id:'default',name:'笔记体验验收',document_count:1,note_count:notes.length}]});
 if(p==='/api/documents')return fulfill({documents:[{id:'course',name:'TCP.md',kind:'md',size_bytes:100,page_count:1,chunk_count:1}]});
 if(p==='/api/documents/course/chunks')return fulfill({chunks:[{id:'chunk',document_id:'course',text:'握手与挥手的原文',page:null,lines:[]}]});
 if(p==='/api/notebooks/default/chat')return fulfill({exchanges:[{id:'original',question:'详细解释 TCP 时间',images:[],reply_to:{},status:'complete',created_at:'2026-10-19',result:{answer,citations,mode:'model'}}],deleted_count:0});
 if(p==='/api/notebooks/default/chat/original/note'){
  posts++;await gate;
  let note=notes.find(n=>n.id==='answer-original');const created=!note;
  if(!note){note={id:'answer-original',notebook_id:'default',source_exchange_id:'original',title:'TCP 时间计算',content:answer,citations,created_at:'2026-10-19T00:00:00Z',updated_at:'2026-10-19T00:00:00Z'};notes.push(note);}
  return fulfill({note,created});
 }
 if(p==='/api/notes'&&method==='GET')return fulfill({notes});
 if(p==='/api/notes'&&method==='POST'){
  manualPosts++;const data=req.postDataJSON(),note={...data,id:'manual',source_exchange_id:null,citations:[],created_at:'2026-10-19T00:00:00Z',updated_at:'2026-10-19T00:00:00Z'};notes.push(note);return fulfill({note});
 }
 if(p.startsWith('/api/notes/')&&method==='PATCH'){
  patches++;const note=notes.find(n=>n.id===p.split('/').at(-1));Object.assign(note,req.postDataJSON());return fulfill({note});
 }
 if(p.endsWith('/export'))return route.fulfill({contentType:'text/markdown',headers:{'Content-Disposition':'attachment; filename="note.md"'},body:notes[0].content+'\n\n参考来源\n握手与挥手的原文'});
 errors.push(`${method} ${p}`);return route.fulfill({status:404,json:{detail:'Unexpected mock API'}});
});
try{
 await page.goto(process.env.APP_URL||'http://127.0.0.1:8765',{waitUntil:'networkidle'});
 await page.locator('#question-input').fill('保留未发送草稿');
 await page.locator('.save-answer').click();
 await page.locator('.save-answer').evaluate(el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));
 release();await page.waitForFunction(()=>document.querySelector('.save-answer').textContent==='已保存到笔记');
 assert.equal(posts,1);assert.equal(notes.length,1);
 assert.equal(await page.locator('#notes-content').isVisible(),true);
 assert.equal(await page.locator('#note-dialog').evaluate(el=>el.open),false,'saving should not interrupt with an editor');
 assert.equal(await page.locator('#question-input').inputValue(),'保留未发送草稿');
 await page.locator('.note-item').first().click();
 assert(await page.locator('#note-reading').isVisible());assert(!await page.locator('#note-editor').isVisible());
 assert.equal(await page.locator('#note-reading-body table').count(),1);
 assert(await page.locator('#note-reading-body .katex').count()>0);
 assert.equal(await page.locator('#note-reading-body .katex-error').count(),0);
 assert.match(await page.locator('#note-reading-body pre code').textContent(),/array\[99\]/);
 await fs.mkdir('artifacts/answer-notes',{recursive:true});
 await page.screenshot({path:'artifacts/answer-notes/desktop.png'});
 await page.locator('#note-reading-body [data-citation="2"]').click();
 assert.match(await page.locator('#note-citation-preview').textContent(),/已经移走的课件原文/);
 await page.locator('#edit-note').click();await page.locator('#note-title').fill('我的 TCP 复习笔记');
 await page.locator('#note-content').fill(answer+'\n\n我自己的补充。');
 await page.locator('#save-note').click();await page.locator('#note-reading').waitFor({state:'visible'});
 assert.match(await page.locator('#note-reading-title').textContent(),/我的 TCP/);
 assert.equal(patches,1);
 await page.evaluate(()=>document.addEventListener('click',event=>{const link=event.target.closest('a[download]');if(link){event.preventDefault();window.__noteExportURL=link.getAttribute('href');}},true));
 await page.locator('#export-note').click();
 assert.equal(await page.evaluate(()=>window.__noteExportURL),'/api/notes/answer-original/export');
 assert.equal(patches,1,'read-only export must not rewrite note');
 await page.locator('#note-reading-body [data-citation="1"]').click();
 await page.waitForFunction(()=>!document.getElementById('note-dialog').open);
 await page.waitForFunction(()=>document.getElementById('source-text').textContent.includes('握手与挥手的原文'));
 await page.reload({waitUntil:'networkidle'});
 await page.locator('.save-answer.is-saved').waitFor();
 await page.locator('.save-answer').click();assert.equal(posts,1);assert.equal(notes.length,1);
 assert.match(await page.locator('#note-reading-body').textContent(),/我自己的补充/);
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  const box=await page.locator('#note-dialog').boundingBox();assert(box.x>=0&&box.x+box.width<=width+1);
  assert(await page.locator('#note-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:`artifacts/answer-notes/mobile-${width}.png`});
 }
 await page.locator('#edit-note').click();await page.locator('#note-content').fill('尚未保存的修改');
 page.once('dialog',dialog=>dialog.dismiss());await page.keyboard.press('Escape');
 assert(await page.locator('#note-dialog').evaluate(el=>el.open));assert.equal(await page.locator('#note-content').inputValue(),'尚未保存的修改');
 page.once('dialog',dialog=>dialog.accept());await page.locator('#close-note').click();
 await page.setViewportSize({width:1440,height:1000});
 await page.locator('#new-note').click();assert(await page.locator('#note-editor').isVisible());
 await page.locator('#note-title').fill('手写笔记');await page.locator('#note-content').fill('**手写重点**');
 await page.locator('#save-note').click();await page.locator('#note-reading').waitFor({state:'visible'});
 assert.equal(manualPosts,1);assert.equal(notes.length,2);assert.match(await page.locator('#note-reading-body strong').textContent(),/手写重点/);
 assert.deepEqual(errors,[]);
 console.log('PASS: one-click snapshot, duplicate guard, list focus, readable math/table/code, inline citations and archived quotes, explicit edit/export, refresh saved state, manual notes, drafts and mobile.');
}finally{await browser.close();}
