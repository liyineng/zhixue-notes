import {renderAnswerHtml} from '/static/answer-format.js';
import renderMathInElement from '/static/vendor/katex/contrib/auto-render.mjs';

export function initializeDeletedChatPanel({api, notebookId, isBusy, onRestored, onPurged, toast}) {
  const dialog = document.getElementById('deleted-chat-dialog');
  const list = document.getElementById('deleted-chat-list');
  const status = document.getElementById('deleted-chat-status');
  const clear = document.getElementById('clear-deleted-chat');
  let records = [], scope = null, revision = 0, working = false;
  const valid = () => dialog.open && scope === notebookId();
  const base = () => `/api/notebooks/${encodeURIComponent(scope)}/chat`;
  const date = value => { const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? '时间未知' : parsed.toLocaleString(); };
  function preview(record) {
    const body = document.createElement('div'); body.className = 'deleted-chat-preview message-body';
    const question = document.createElement('div'); question.className = 'deleted-chat-question'; question.textContent = record.question;
    body.append(question);
    if (record.images?.length) {
      const files = document.createElement('div'); files.className = 'deleted-chat-files';
      for (const image of record.images) {
        const link = document.createElement('a'); link.textContent = image.name || '问题附件';
        link.href = `/api/question-images/${encodeURIComponent(image.id)}/file`; link.target = '_blank'; link.rel = 'noopener'; files.append(link);
      }
      body.append(files);
    }
    const result = record.result || {}, answer = document.createElement('div');
    if (result.quiz) {
      const title = document.createElement('p'); title.textContent = `测验 · ${result.quiz.questions?.length || 0} 道题，恢复后可在“应用”中继续作答。`; answer.append(title);
      for (const [index, item] of (result.quiz.questions || []).entries()) {
        const text = document.createElement('div'); text.innerHTML = renderAnswerHtml(`${index + 1}. ${item.prompt}`, result.citations || []); answer.append(text);
      }
    } else {
      answer.innerHTML = renderAnswerHtml(result.answer || result.detail || (result.cancelled ? '这条回答已取消。' : '这条记录没有回答内容。'), result.citations || []);
    }
    // Read-only preview: display saved numbering without dead citation buttons.
    for (const button of answer.querySelectorAll('.citation-ref')) {
      const text = document.createElement('span'); text.className = 'deleted-citation-number'; text.textContent = button.textContent; button.replaceWith(text);
    }
    body.append(answer);
    if (result.citations?.length) {
      const sources = document.createElement('details'), summary = document.createElement('summary'); summary.textContent = `已保存的引用（${result.citations.length}）`; sources.append(summary);
      for (const citation of result.citations) {
        const text = document.createElement('p'); text.textContent = `[${citation.index}] ${citation.document_name}${citation.page ? ` · 第 ${citation.page} 页` : ''}\n${citation.text || ''}`; sources.append(text);
      }
      body.append(sources);
    }
    return body;
  }
  async function changeRecord(record, permanent = false) {
    if (!valid() || working) return;
    if (isBusy()) return toast('请等待当前操作完成。');
    if (permanent && !window.confirm(`永久删除“${(record.question || '附件提问').slice(0, 100)}”？这条对话将无法恢复，仅被这条记录使用的附件也会清理。`)) return;
    const operationScope = scope, operationRevision = revision;
    working = true; render();
    try {
      const data = await api(`${base()}/${encodeURIComponent(record.id)}/${permanent ? 'permanent' : 'restore'}`, {method:permanent ? 'DELETE' : 'POST'});
      if (permanent) await onPurged(data, record, operationScope);
      else await onRestored(data, record, operationScope);
      if (!valid() || scope !== operationScope) return;
      records = records.filter(item => item.id !== record.id);
      toast(permanent ? '这条对话已永久删除。' : record.result?.quiz ? '测验已恢复到“应用”。' : '对话及附件已恢复，原有内容和顺序保留。');
    } catch (error) { toast(error.message, true); }
    finally { working = false; if (valid()) { if (operationRevision !== revision) load(); else render(); } }
  }
  async function clearRecords() {
    if (!valid() || working || clear.disabled || !records.length) return;
    if (isBusy()) return toast('请等待当前操作完成。');
    if (!window.confirm(`永久清空当前笔记本的全部 ${records.length} 条删除记录？清空后无法恢复，仅被这些记录使用的附件也会清理。正常对话不受影响。`)) return;
    const operationScope = scope, operationRevision = revision;
    working = true; render();
    try {
      const data = await api(`${base()}/deleted/all`, {method:'DELETE'});
      await onPurged(data, null, operationScope);
      if (!valid() || scope !== operationScope) return;
      records = [];
      toast(`已永久清空 ${data.purged_count} 条删除记录。`);
    } catch (error) { toast(error.message, true); }
    finally { working = false; if (valid()) { if (operationRevision !== revision) load(); else render(); } }
  }
  function render() {
    clear.disabled = working || isBusy() || !records.length;
    const opened = new Set([...list.querySelectorAll('article')].filter(card => card.querySelector('details')?.open).map(card => card.dataset.deletedId));
    list.replaceChildren(); status.textContent = `可恢复 ${records.length} / 5 条 · 当前笔记本`;
    if (!records.length) {
      const empty = document.createElement('p'); empty.className = 'deleted-chat-empty'; empty.textContent = '暂无可恢复的删除记录。'; list.append(empty); return;
    }
    for (const record of records) {
      const card = document.createElement('article'); card.className = 'deleted-chat-card'; card.dataset.deletedId = record.id;
      const header = document.createElement('div'); header.className = 'deleted-chat-card-heading';
      const heading = document.createElement('h3'); heading.textContent = record.question || '附件提问'; heading.title = record.question;
      const restore = document.createElement('button'); restore.className = 'button ghost'; restore.type = 'button'; restore.textContent = '恢复这条'; restore.disabled = working || isBusy();
      restore.addEventListener('click', () => changeRecord(record));
      const remove = document.createElement('button'); remove.className = 'button ghost deleted-chat-permanent'; remove.type = 'button'; remove.textContent = '永久删除'; remove.disabled = working || isBusy();
      remove.addEventListener('click', () => changeRecord(record, true));
      const actions = document.createElement('div'); actions.className = 'deleted-chat-card-actions'; actions.append(restore, remove);
      header.append(heading, actions);
      const meta = document.createElement('p'); meta.className = 'deleted-chat-meta';
      const type = record.result?.quiz ? '测验' : record.result?.cancelled ? '已取消的问答' : record.status === 'error' ? '失败的问答' : '问答';
      meta.textContent = `${type} · 删除于 ${date(record.deleted_at)}${record.images?.length ? ` · ${record.images.length} 个附件` : ''}`;
      const details = document.createElement('details'), summary = document.createElement('summary'); summary.textContent = '预览内容';
      const body = preview(record); details.append(summary, body); details.open = opened.has(record.id);
      details.addEventListener('toggle', () => {
        if (!details.open || details.dataset.mathRendered) return;
        renderMathInElement(body, {delimiters:[{left:'$$',right:'$$',display:true},{left:'\\[',right:'\\]',display:true},{left:'\\(',right:'\\)',display:false},{left:'$',right:'$',display:false}],throwOnError:false,trust:false,maxExpand:1000,strict:'ignore'});
        details.dataset.mathRendered = 'true';
      });
      card.append(header, meta, details); list.append(card);
    }
  }
  async function load() {
    if (working) return;
    const version = ++revision; scope = notebookId(); clear.disabled = true; status.textContent = '正在读取删除记录…'; list.replaceChildren();
    try {
      const data = await api(`${base()}/deleted`);
      if (version !== revision || !valid()) return;
      records = data.exchanges || []; render();
    } catch (error) { if (version === revision && valid()) status.textContent = error.message; }
  }
  document.getElementById('manage-deleted-chat').addEventListener('click', () => {
    dialog.showModal();
    if (working) {
      scope = notebookId(); records = []; clear.disabled = true; list.replaceChildren();
      status.textContent = '正在完成操作，请稍候…';
    } else load();
  });
  clear.addEventListener('click', clearRecords);
  document.getElementById('refresh-deleted-chat').addEventListener('click', load);
  document.getElementById('close-deleted-chat').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { revision++; });
}
