// All file/model APIs are mocked. No personal data is read or changed.
import assert from 'node:assert/strict';
import os from 'node:os';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const modules=path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium}=await import(pathToFileURL(path.join(modules,'playwright/index.mjs')).href);
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[]; page.on('pageerror',e=>errors.push(e.message));
const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1sAAAAASUVORK5CYII=','base64');
const used={id:'linked',question:'帮我解释这道 TCP 题',status:'complete',deleted_at:null};
let attachments=[
  {id:'unused',name:'本周作业.pdf',kind:'document',ocr_text:'第 1 页：完整题干与条件。',exchanges:[]},
  {id:'used',name:'TCP习题.txt',kind:'document',ocr_text:'TCP 原始题目',exchanges:[used,{...used,id:'undo',deleted_at:'2026-10-18'}]},
  {id:'image',name:'很长的题目截图文件名称'.repeat(6)+'.png',kind:'image',ocr_text:'识别题干',exchanges:[]}
].map(item=>({...item,url:`/api/question-images/${item.id}/file`,created_at:'2026-10-19T00:00:00Z',available:true,size_bytes:12345}));
let deletes=0,details=0,sourceWrites=0;
await page.route('**/api/**',async route=>{
 const request=route.request(),p=new URL(request.url()).pathname,method=request.method();
 const fulfill=json=>route.fulfill({json});
 if(p==='/api/settings') return fulfill({model_configured:true,model:'mock'});
 if(p==='/api/sync') return fulfill({enabled:false,available:false,conflicts:[]});
 if(p==='/api/notebooks') return fulfill({notebooks:[{id:'default',name:'附件管理验收',document_count:1}]});
 if(p==='/api/documents') {if(method!=='GET')sourceWrites++;return fulfill({documents:[{id:'course',name:'课程.md',kind:'md',size_bytes:100,page_count:1,chunk_count:1}]});}
 if(p==='/api/notes') return fulfill({notes:[]});
 if(p==='/api/notebooks/default/chat') return fulfill({exchanges:[{...used,created_at:'2026-10-19',images:[attachments[1]],reply_to:{},result:{answer:'对话里的原有解答。',citations:[],mode:'model'}}],deleted_count:1});
 if(p==='/api/notebooks/default/attachments') return fulfill({attachments:attachments.map(({ocr_text,...item})=>item)});
 if(p.startsWith('/api/notebooks/default/attachments/')) {
  const id=p.split('/').at(-1),item=attachments.find(item=>item.id===id);
  if(!item)return route.fulfill({status:404,json:{detail:'附件不存在'}});
  if(method==='GET') {details++;return fulfill({attachment:item});}
  if(method==='PATCH'){item.name=request.postDataJSON().name;return fulfill({attachment:item});}
  if(method==='DELETE'){assert.equal(item.exchanges.length,0);deletes++;attachments=attachments.filter(other=>other.id!==id);return fulfill({deleted:true});}
 }
 if(p.endsWith('/file')) return route.fulfill({contentType:'image/png',body:pixel});
 errors.push(`${method} ${p}`);return route.fulfill({status:404,json:{detail:'Unexpected mock API'}});
});
const open=async()=>{await page.locator('#manage-question-attachments').click();await page.locator('.managed-attachment').first().waitFor();};
const card=id=>page.locator(`[data-attachment-id="${id}"]`);
try {
 await page.goto(process.env.APP_URL||'http://127.0.0.1:8765',{waitUntil:'networkidle'});
 await fs.mkdir('artifacts/attachment-manager',{recursive:true});
 await page.screenshot({path:'artifacts/attachment-manager/composer-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'artifacts/attachment-manager/composer-mobile.png'});
 await page.setViewportSize({width:1440,height:1000});
 await open();assert.equal(await page.locator('.managed-attachment').count(),3);
 assert.equal(details,0,'list must not fetch every OCR body');
 await page.locator('#managed-attachment-search').fill('TCP');
 assert.equal(await page.locator('.managed-attachment').count(),1);
 assert(await card('used').getByRole('button',{name:'删除文件',exact:true}).isDisabled());
 await card('used').locator('.attachment-exchanges summary').click();
 await card('used').getByRole('button',{name:used.question,exact:true}).click();
 assert.equal(await page.locator('#attachments-dialog').evaluate(el=>el.open),false);
 assert.equal(await page.locator('.attachment-jump').count(),1);
 await open();
 await card('unused').locator('.attachment-extracted summary').click();
 await page.waitForFunction(()=>document.querySelector('[data-attachment-id="unused"] pre').textContent.includes('完整题干'));
 await card('unused').getByRole('button',{name:'附到本次问题',exact:true}).click();
 await page.locator('#question-image-draft').waitFor({state:'visible'});
 assert.equal(await page.locator('#question-attachment-count').textContent(),'1 个附件');
 assert.equal(await page.locator('#attachments-dialog').evaluate(el=>el.open),false);
 await page.locator('#question-image-draft summary').click();
 await page.locator('#question-image-text').fill('用户校对后的题干');
 await open();page.once('dialog',dialog=>dialog.accept('新作业.pdf'));
 await card('unused').getByRole('button',{name:'重命名',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[data-attachment-id="unused"] h3').textContent==='新作业.pdf');
 await page.locator('#close-attachments').click();
 assert.equal(await page.locator('#question-image-text').inputValue(),'用户校对后的题干');
 assert.match(await page.locator('#question-image-list').textContent(),/新作业/);
 page.once('dialog',dialog=>dialog.accept());
 await page.getByRole('button',{name:'移除附件 新作业.pdf',exact:true}).click();
 assert.equal(deletes,0,'detaching must preserve manager files');
 await open();page.once('dialog',dialog=>dialog.accept());
 await card('unused').getByRole('button',{name:'删除文件',exact:true}).click();
 await card('unused').waitFor({state:'detached'});
 assert.equal(deletes,1);assert.equal(await page.locator('.managed-attachment').count(),2);
 await fs.mkdir('artifacts/attachment-manager',{recursive:true});
 await page.screenshot({path:'artifacts/attachment-manager/desktop.png'});
 await page.locator('#close-attachments').click();
 await page.reload({waitUntil:'networkidle'});await open();
 assert.equal(await page.locator('.managed-attachment').count(),2);
 for(const width of [390,320]) {
  await page.setViewportSize({width,height:844});
  const box=await page.locator('#attachments-dialog').boundingBox();assert(box.x>=0&&box.x+box.width<=width+1);
  assert(await page.locator('#attachments-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:`artifacts/attachment-manager/mobile-${width}.png`});
 }
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('#attachments-dialog').evaluate(el=>el.open),false);
 assert.equal(sourceWrites,0);assert.deepEqual(errors,[]);
 console.log('PASS: notebook attachment management, lazy OCR, reuse, rename preserving edits, safe delete, history links, detached-file retention, reload and mobile.');
} finally {await browser.close();}
