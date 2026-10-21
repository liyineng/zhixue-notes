import { renderQuiz } from '/static/quizzes.js';
const notebook = new URLSearchParams(location.search).get('notebook_id') || 'default';
const identity = decodeURIComponent(location.pathname.split('/').at(-1));
const endpoint = `/api/notebooks/${encodeURIComponent(notebook)}/quiz/${encodeURIComponent(identity)}`;
document.getElementById('quiz-back').href = `/?notebook_id=${encodeURIComponent(notebook)}&library=applications`;
const colors = ['#2563eb', '#047857', '#b45309', '#7c3aed', '#be185d', '#0e7490'];
async function api(url, body) {
  const response = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败：HTTP ${response.status}`);
  return data;
}
let result;
function showSource(index) {
  const source = result.citations.find(c => c.index === index);
  if (!source) return;
  const pane = document.querySelector('.quiz-page-source');
  pane.style.setProperty('--citation-color', colors[(index - 1) % colors.length]);
  document.getElementById('quiz-source-title').textContent = `[${index}] ${source.document_name}${source.page ? ` · 第 ${source.page} 页` : ''}`;
  document.getElementById('quiz-source-text').textContent = source.text;
  const file = document.getElementById('quiz-source-file');
  file.href = `/api/documents/${encodeURIComponent(source.document_id)}/${source.document_kind === 'pptx' ? 'preview' : 'file'}${source.page ? `#page=${Number(source.page)}` : ''}`;
  file.textContent = ['pptx', 'pdf'].includes(source.document_kind) ? '打开原文对应页 ↗' : '打开原文件 ↗';
  file.hidden = false;
  if (innerWidth <= 760) pane.scrollIntoView({ block: 'start', behavior: 'smooth' });
}
async function update(suffix, body) {
  result = await api(endpoint + suffix, body);
  if (suffix === '/reset') {
    document.getElementById('quiz-source-title').textContent = '查看依据';
    document.getElementById('quiz-source-text').textContent = '提交答案后，点击解析下方的来源可在这里查看引用原文。';
    document.getElementById('quiz-source-file').hidden = true;
  }
  return result;
}
try {
  result = await api(endpoint);
  document.title = `${result.quiz.topic || '交互式测验'} · 智学笔记`;
  document.getElementById('quiz-page-title').textContent = result.quiz.topic || result.title || '课程测验';
  renderQuiz(document.getElementById('quiz-page-body'), result, {
    notebookId: notebook,
    onAnswer: (question_id, option) => update('/answer', { question_id, option }),
    onReset: () => update('/reset', {}), onCitation: showSource,
    colorCitation: (element, index) => { element.style.setProperty('--citation-color', colors[(index - 1) % colors.length]); element.style.setProperty('--citation-bg', '#f0f5ff'); },
  });
} catch (error) {
  document.getElementById('quiz-page-title').textContent = '暂时无法打开测验';
  const message = document.getElementById('quiz-page-error'); message.textContent = error.message; message.hidden = false;
}
