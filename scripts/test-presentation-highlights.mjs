import assert from 'node:assert/strict';
import { presentationHighlights } from '../app/static/presentation-highlights.js';
const lines = [
 { page: 2, text: '谐振回路具有选频作用', bbox: [10, 20, 200, 40] },
 { page: 2, text: '带宽取决于品质因数', bbox: [10, 45, 200, 65] },
 { page: 2, text: '其他内容不应被标注', bbox: [10, 70, 200, 90] },
];
const refs = [
 { index: 1, page: 2, text: '谐振回路具有 选频作用。', lines: [{ page: 2, text: '谐振回路具有选频作用' }] },
 { index: 2, page: 2, text: '带宽取决于品质因数', lines: [] },
 { index: 3, page: 3, text: '其他内容不应被标注', lines: [] },
];
assert.deepEqual(presentationHighlights(refs, lines, 2).map(line => line.citationIndex), [1, 2]);
const wrapped = [{ page: 1, text: 'A complete quoted', bbox: [0, 0, 90, 10] }, { page: 1, text: 'sentence spans lines.', bbox: [0, 12, 90, 22] }];
assert.equal(presentationHighlights([{ index: 9, page: 1, text: 'A complete quoted sentence spans lines.' }], wrapped, 1).length, 2);
assert.equal(presentationHighlights([{ index: 5, page: 2, text: '不存在的引用', lines: [{ page: 2, text: '其他内容不应被标注' }] }], lines, 2).length, 0);
assert.equal(presentationHighlights([{ index: 5, page: 2, text: 'LC' }], [{ page: 2, text: 'LC', bbox: [1, 2, 3, 4] }], 2).length, 0);
const overlap = presentationHighlights([{ index: 1, page: 2, text: lines[0].text }, { index: 2, page: 2, text: lines[0].text }], lines, 2);
assert.deepEqual(overlap.map(line => line.citationIndex), [1, 2]);
console.log('Presentation citation matching passed: page scope, wrapping, punctuation, unrelated context, short labels and overlap.');
