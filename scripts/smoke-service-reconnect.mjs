// Full app initialization recovery, mocked APIs only and no model calls.
import assert from 'node:assert/strict';
import os from 'node:os';import path from 'node:path';import {pathToFileURL} from 'node:url';
const {chromium}=await import(pathToFileURL(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs')));
const browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:390,height:844}});
let online=false;const mutations=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/**',async route=>{
 const r=route.request(),p=new URL(r.url()).pathname;
 if(r.method()!=='GET')mutations.push(p);
 if(!online)return route.abort('connectionrefused');
 if(p==='/api/health')return route.fulfill({json:{ok:true,service:'zhixue-notes'}});
 if(p==='/api/notebooks')return route.fulfill({json:{notebooks:[{id:'default',name:'连接恢复验收',document_count:0}]}});
 if(p==='/api/documents')return route.fulfill({json:{documents:[]}});
 if(p==='/api/notes')return route.fulfill({json:{notes:[]}});
 if(p==='/api/settings')return route.fulfill({json:{model:'mock',model_configured:true}});
 if(p==='/api/sync')return route.fulfill({json:{enabled:false,available:false,conflicts:[]}});
 if(p==='/api/notebooks/default/chat')return route.fulfill({json:{exchanges:[{id:'saved',status:'complete',question:'之前的问题',images:[],reply_to:{},result:{answer:'已保存的回答',citations:[],mode:'model'}}],deleted_count:0}});
 return route.fulfill({json:{}});
});
try{
 await page.goto(process.env.APP_URL||'http://127.0.0.1:8765',{waitUntil:'networkidle'});
 await page.waitForFunction(()=>document.getElementById('service-status').dataset.state==='disconnected');
 await page.locator('#question-input').fill('断线期间保留的草稿');
 online=true;await page.evaluate(()=>window.dispatchEvent(new Event('online')));
 await page.waitForFunction(()=>document.getElementById('notebook-title').textContent==='连接恢复验收' && document.querySelector('article[data-message-id="saved"] .message-body')?.textContent.includes('已保存的回答'));
 assert.equal(await page.locator('#question-input').inputValue(),'断线期间保留的草稿');
 assert.equal(await page.locator('#service-status').isVisible(),false);
 assert.deepEqual(mutations,[]);assert.deepEqual(errors,[]);
 console.log('PASS: startup disconnect recovers notebook/history after verified service reconnect, preserves draft and never resubmits model requests.');
}finally{await browser.close();}
