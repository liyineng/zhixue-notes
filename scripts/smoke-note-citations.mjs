// Notebook note/source comparison with real PDF rendering, fixture-only APIs.
import assert from 'node:assert/strict';
import os from 'node:os';
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const modules=path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=await import(pathToFileURL(path.join(modules,'playwright/index.mjs')).href);
const pdf=execFileSync(path.resolve('.venv/bin/python'),['-c',`import sys,pymupdf
with pymupdf.open() as pdf:
 for i in range(2):
  page=pdf.new_page(width=600,height=720)
  page.insert_text((40,80),'TCP reference passage on page '+str(i+1))
 sys.stdout.buffer.write(pdf.tobytes())`]);
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const line={page:2,text:'TCP reference passage on page 2',bbox:[40,68,260,84]};
const citations=[{index:1,document_id:'pdf',document_name:'TCP.pdf',document_kind:'pdf',chunk_id:'pdf-page2',page:2,text:line.text,lines:[line]},
 {index:2,document_id:'slides',document_name:'TCP.pptx',document_kind:'pptx',chunk_id:'slide-page2',page:2,text:line.text,lines:[line]}];
const note={id:'note',notebook_id:'default',title:'TCP 对照阅读',content:'## 连接建立\n\n公式和讲义见 [1]，课件原页见 [2]。\n\n'+Array.from({length:12},(_,i)=>`### 步骤 ${i+1}\n\n保留笔记中的阅读内容和段落。`).join('\n\n'),citations,created_at:'2026-10-19',updated_at:'2026-10-19'};
let release;const gate=new Promise(resolve=>release=resolve);let delayChunks=false;
await page.route('**/api/**',async route=>{
 const p=new URL(route.request().url()).pathname;const fulfill=json=>route.fulfill({json});
 if(p==='/api/settings')return fulfill({model:'mock',model_configured:true});
 if(p==='/api/sync')return fulfill({enabled:false,available:false,conflicts:[]});
 if(p==='/api/notebooks')return fulfill({notebooks:[{id:'default',name:'笔记原文对照验收',document_count:2}]});
 if(p==='/api/documents')return fulfill({documents:[{id:'pdf',name:'TCP.pdf',kind:'pdf',size_bytes:100,page_count:2,chunk_count:2},{id:'slides',name:'TCP.pptx',kind:'pptx',size_bytes:100,page_count:2,chunk_count:2}]});
 if(p==='/api/notebooks/default/chat')return fulfill({exchanges:[],deleted_count:0});
 if(p==='/api/notes')return fulfill({notes:[note]});
 if(p==='/api/documents/pdf/chunks'||p==='/api/documents/slides/chunks'){
  if(delayChunks)await gate;
  return fulfill({chunks:[{id:p.includes('/pdf/')?'pdf-page2':'slide-page2',page:2,text:line.text,lines:[line]}]});
 }
 if(p==='/api/documents/pdf/file'||p==='/api/documents/slides/preview')return route.fulfill({contentType:'application/pdf',body:pdf});
 if(p==='/api/documents/slides/preview-lines')return fulfill({lines:[line],formula_regions:[]});
 errors.push(p);return route.fulfill({status:404,json:{detail:'Unexpected mock API'}});
});
try{
 await page.goto(process.env.APP_URL||'http://127.0.0.1:8765',{waitUntil:'networkidle'});
 await page.locator('#notes-tab').click();await page.locator('.note-item').click();
 await page.evaluate(()=>{window.__noteBody=document.getElementById('note-reading-body');window.__viewerParent=document.getElementById('viewer-document').parentElement;});
 await page.locator('#note-reading-body [data-citation="1"]').click();
 await page.waitForFunction(()=>document.getElementById('page-input').value==='2'&&document.querySelector('#note-source-pane .highlight-box'));
 assert(await page.locator('#note-dialog').evaluate(el=>el.open));
 assert(await page.locator('#note-source-pane').isVisible());
 assert.equal(await page.evaluate(()=>window.__noteBody===document.getElementById('note-reading-body')),true);
 assert.equal(await page.locator('#pdf-canvas').evaluate(el=>el.width>0),true);
 let left=await page.locator('#note-form').boundingBox(),right=await page.locator('#note-source-pane').boundingBox();
 assert(left.x+left.width<=right.x+1,'desktop note and original should sit side by side');
 await fs.mkdir('artifacts/note-citations',{recursive:true});await page.screenshot({path:'artifacts/note-citations/desktop.png'});
 await page.locator('#note-form').evaluate(el=>el.scrollTop=180);
 const before=await page.locator('#note-form').evaluate(el=>el.scrollTop);
 await page.locator('.note-source').nth(1).evaluate(el=>el.click());
 await page.waitForFunction(()=>document.getElementById('viewer-name').textContent==='TCP.pptx'&&document.querySelector('#note-source-pane .highlight-box'));
 assert.equal(await page.locator('#note-form').evaluate(el=>el.scrollTop),before,'switching citations must preserve note reading position');
 await page.locator('#next-page').click();await page.waitForFunction(()=>document.getElementById('page-input').value==='2');
 await page.locator('#prev-page').click();await page.waitForFunction(()=>document.getElementById('page-input').value==='1');
 await page.locator('#close-note-source').click();
 assert(await page.locator('#note-dialog').evaluate(el=>el.open));
 assert.equal(await page.evaluate(()=>window.__viewerParent===document.getElementById('viewer-document').parentElement),true);
 await page.locator('#edit-note').click();await page.locator('#note-content').fill('尚未保存的笔记修改');
 await page.locator('.note-source').first().click();
 await page.waitForFunction(()=>document.querySelector('#note-source-pane .highlight-box'));
 assert.equal(await page.locator('#note-content').inputValue(),'尚未保存的笔记修改');
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  left=await page.locator('#note-form').boundingBox();right=await page.locator('#note-source-pane').boundingBox();
  assert(left.y+left.height<=right.y+1,'mobile note and original should stack');
  assert(await page.locator('#note-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:`artifacts/note-citations/mobile-${width}.png`});
 }
 page.once('dialog',dialog=>dialog.dismiss());await page.keyboard.press('Escape');
 assert(await page.locator('#note-dialog').evaluate(el=>el.open));assert(await page.locator('#note-source-pane').isVisible());
 page.once('dialog',dialog=>dialog.accept());await page.locator('#close-note').click();
 await page.waitForFunction(()=>document.getElementById('viewer-document').parentElement.classList.contains('viewer-pane'));
 assert.equal(await page.locator('#viewer-document').count(),1);
 assert.equal(await page.locator('#note-source-pane').isVisible(),false);
 // A delayed source lookup must not reopen or reparent a closed note.
 await page.setViewportSize({width:1440,height:1000});await page.locator('.note-item').click();delayChunks=true;
 await page.locator('#note-reading-body [data-citation="1"]').click();
 await page.locator('#close-note').click();release();
 await page.waitForTimeout(80);
 assert.equal(await page.locator('#note-dialog').evaluate(el=>el.open),false);
 assert.equal(await page.evaluate(()=>window.__viewerParent===document.getElementById('viewer-document').parentElement),true);
 assert.deepEqual(errors,[]);
 console.log('PASS: note stays open, PDF/PPT page and highlight controls, desktop comparison/mobile stack, reading position, unsaved edits, viewer restoration and late-response guard.');
}finally{await browser.close();}
