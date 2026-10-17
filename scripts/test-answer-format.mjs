import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
// Import as a data module so this runs without changing the project's module type.
const source = await fs.readFile(new URL('../app/static/answer-format.js', import.meta.url), 'utf8');
const { renderAnswerHtml } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const html = renderAnswerHtml('## 最终结果\n\n| 主机 | B1 | B2 |\n|---|:---:|---:|\n| **H1** | 1 [2] | 1 |\n\n1. 学习源地址\n2. 查目的地址\n\n`[2]` [99]\n\n<script>alert(1)</script>\n\n```html\n<img onerror="alert(1)">\n```', [{index: 2}]);
assert.match(html, /<table><thead>/);
assert.match(html, /<h3>最终结果<\/h3>/);
assert.match(html, /<ol start="1"><li>学习源地址<\/li><li>查目的地址<\/li><\/ol>/);
assert.equal((html.match(/class="citation-ref"/g) || []).length, 1);
assert.match(html, /data-citation="2"/);
assert.match(html, /<code>\[2\]<\/code> \[99\]/);
assert(!html.includes('<script>'));
assert(!html.includes('<img'));
assert.match(html, /&lt;script&gt;/);
const pipes = renderAnswerHtml('| A | B |\n|---|---|\n| a\\|b | `x|y` |');
assert.match(pipes, /<td[^>]*>a\|b<\/td>/);
assert.match(pipes, /<code>x\|y<\/code>/);
console.log(JSON.stringify({ passed: true, tables: true, orderedLists: true, headings: true, escapedHtml: true, validCitationsOnly: true }));
