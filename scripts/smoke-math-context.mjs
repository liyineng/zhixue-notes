// Fixture-only rendering checks: no personal data or model calls.
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const modules = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const {chromium} = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
const formula = String.raw`\[
T_{\min}=1RTT_{\text{建连}}+2RTT_{\text{数据}}+1RTT_{\text{挥手到TIME-WAIT}}+2MSL
=4RTT+2MSL
\]`;
const code = String.raw`\[literal_{code}=1+2\]`;
const answer = `${formula}\n\n\`\`\`text\n${code}\n\`\`\`\n\n正常文字和引用 [1]。`;
const browser = await chromium.launch({headless:true});
try {
  for (const width of [1440,390]) {
    const page = await browser.newPage({viewport:{width,height:1000}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/**',r=>{
      const p=new URL(r.request().url()).pathname; let data={};
      if(p==='/api/notebooks') data={notebooks:[{id:'default',name:'公式验收',document_count:0}]};
      else if(p==='/api/documents') data={documents:[]};
      else if(p==='/api/notes') data={notes:[]};
      else if(p==='/api/settings') data={model_configured:true,model:'mock',context_window:0,model_context:{context_window:1048576,source:'provider_api'}};
      else if(p==='/api/model-context') data={context_window:128000,source:'provider_api'};
      else if(p==='/api/sync') data={enabled:false,available:false,conflicts:[]};
      else if(p==='/api/notebooks/default/chat') data={exchanges:[{id:'math-test',question:'公式',status:'complete',images:[],reply_to:{},result:{answer,citations:[{index:1,document_id:'source',document_name:'课程',page:1,text:'依据'}]}}],deleted_count:0};
      return r.fulfill({json:data});
    });
    await page.goto(process.env.APP_URL||'http://127.0.0.1:8765',{waitUntil:'networkidle'});
    assert.equal(await page.locator('.katex-error').count(),0);
    assert.equal(await page.locator('.answer-math .mtable').count(),1,'long equation should be aligned');
    assert.equal(await page.locator('pre code').textContent(),code);
    assert.equal(await page.locator('.citation-ref').count(),1);
    const bounds=await page.locator('.answer-math').evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth}));
    assert(bounds.scroll<=bounds.client+2,'reflowed formula should fit chat pane');
    assert.equal(await page.locator('.answer-math').getAttribute('tabindex'),'0');
    assert.match(await page.locator('#model-context-status').textContent(),/1,048,576/);
    // Settings controls can be exercised without opening or changing saved data.
    await page.locator('#detect-model-context').evaluate(el=>el.click());
    await page.waitForFunction(()=>document.getElementById('model-context-status').textContent.includes('128,000'));
    assert.deepEqual(errors,[]);
    await page.close();
  }
  console.log('PASS: long formulas fit desktop/mobile; literals/citations preserved; context metadata detection works.');
} finally {await browser.close();}
