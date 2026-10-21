// Source parsing report. Opening it never starts OCR or a model request.
export function initializeDocumentStatus({ api, notebookId, toast, onUpdated }) {
  const style = document.createElement('link'); style.rel = 'stylesheet'; style.href = '/static/document-status.css'; document.head.append(style);
  const dialog = document.createElement('dialog'); dialog.className = 'settings-dialog document-status-dialog';
  dialog.setAttribute('aria-labelledby', 'document-status-title');
  const header = document.createElement('header'); header.className = 'dialog-heading';
  const heading = document.createElement('h2'); heading.id = 'document-status-title'; heading.textContent = '资料解析情况';
  const closeButton = document.createElement('button'); closeButton.type = 'button'; closeButton.className = 'icon-button'; closeButton.textContent = '×'; closeButton.setAttribute('aria-label', '关闭资料解析情况');
  header.append(heading, closeButton);
  const status = document.createElement('p'); status.className = 'document-status-message'; status.setAttribute('role', 'status');
  const notice = document.createElement('p'); notice.className = 'document-status-notice';
  const actions = document.createElement('div'); actions.className = 'document-status-actions';
  const all = makeButton('全选待识别页'), none = makeButton('全不选'), reload = makeButton('刷新状态');
  const retry = makeButton('识别所选页', true); actions.append(all, none, reload, retry);
  const list = document.createElement('div'); list.className = 'document-status-pages';
  dialog.append(header, status, notice, actions, list); document.body.append(dialog);
  let scope = null, documentInfo = null, report = null, generation = 0, working = false;
  const selected = new Set();
  function makeButton(text, primary = false) { const button = document.createElement('button'); button.type = 'button'; button.className = `button ${primary ? 'primary' : 'ghost'}`; button.textContent = text; return button; }
  const valid = token => dialog.open && token === generation && scope === notebookId();
  const endpoint = () => `/api/documents/${encodeURIComponent(documentInfo.id)}/status`;
  function controls() {
    const candidates = (report?.pages || []).filter(page => page.retryable);
    all.disabled = working || !candidates.some(page => !selected.has(page.page));
    none.disabled = working || !selected.size; reload.disabled = working;
    retry.disabled = working || !selected.size;
    retry.textContent = working ? '正在识别…' : selected.size ? `识别所选 ${selected.size} 页` : '识别所选页';
  }
  function render() {
    list.replaceChildren(); controls();
    if (!report) return;
    notice.textContent = report.notice || '';
    if (report.error) { status.textContent = report.error; return; }
    const summary = report.summary || {};
    status.textContent = `共 ${report.page_count || 0} 页 · 文字已提取 ${(summary.indexed_text_pages || []).length} 页 · 图片已识别 ${(summary.recognized_image_pages || []).length} 页 · 公式已识别 ${(summary.recognized_formula_pages || []).length} 页 · 待核验 ${(summary.pending_pages || []).length} 页`;
    for (const page of report.pages || []) {
      const row = document.createElement('article'); row.className = 'document-status-page'; row.dataset.page = page.page;
      const label = document.createElement('label');
      const check = document.createElement('input'); check.type = 'checkbox'; check.value = page.page; check.checked = selected.has(page.page); check.disabled = working || !page.retryable; check.setAttribute('aria-label', `选择第 ${page.page} 页`);
      check.addEventListener('change', () => { check.checked ? selected.add(page.page) : selected.delete(page.page); controls(); });
      const text = document.createElement('span'); text.textContent = `第 ${page.page} 页`;
      const state = document.createElement('strong'); state.className = `document-page-state is-${page.status}`; state.textContent = page.label;
      label.append(check, text, state); row.append(label);
      const detail = document.createElement('p'); detail.textContent = page.detail || '';
      const badges = [page.indexed_text ? '文字索引' : '', page.image_candidate ? '图片候选' : '', page.formula_candidate ? '公式对象' : '', ...(page.recognition_methods || []).map(method => method === 'vision' ? '视觉识别' : method === 'ocr' ? '本地 OCR' : method)].filter(Boolean);
      if (badges.length) detail.textContent = `${badges.join(' · ')}。${detail.textContent}`;
      row.append(detail);
      if (['pdf', 'pptx'].includes(documentInfo.kind)) {
        const open = document.createElement('a'); open.className = 'document-status-original'; open.textContent = '查看原文 ↗';
        open.href = `/api/documents/${encodeURIComponent(documentInfo.id)}/${documentInfo.kind === 'pptx' ? 'preview' : 'file'}#page=${page.page}`; open.target = '_blank'; open.rel = 'noopener'; row.append(open);
      }
      list.append(row);
    }
    if (!report.pages?.length) { const empty = document.createElement('p'); empty.textContent = '暂无可检查的页面。'; list.append(empty); }
  }
  async function load() {
    const token = ++generation; report = null; selected.clear(); list.replaceChildren(); notice.textContent = ''; status.textContent = '正在检查原文件与缓存的解析结果…'; controls();
    try {
      const data = await api(`${endpoint()}?notebook_id=${encodeURIComponent(scope)}`);
      if (!valid(token)) return;
      report = data; render();
    } catch (error) { if (valid(token)) { status.textContent = error.message; controls(); } }
  }
  all.addEventListener('click', () => { for (const page of report?.pages || []) if (page.retryable) selected.add(page.page); render(); });
  none.addEventListener('click', () => { selected.clear(); render(); });
  reload.addEventListener('click', load);
  retry.addEventListener('click', async () => {
    if (working || !selected.size || scope !== notebookId()) return;
    const token = generation, pages = [...selected].sort((a, b) => a - b);
    working = true; render(); status.textContent = `正在识别所选页：${pages.join('、')}。如配置了视觉模型，本次识别会使用该模型。`;
    try {
      const data = await api(endpoint() + '/retry', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({notebook_id: scope, pages}) });
      if (!valid(token)) return;
      report = data; selected.clear(); working = false; render();
      const unread = data.retry?.unread_pages || [];
      status.textContent += unread.length ? `；第 ${unread.join('、')} 页仍未完成，可检查模型或 OCR 后重试。` : '；所选页识别已完成。';
      await onUpdated?.(documentInfo, data);
    } catch (error) { if (valid(token)) { working = false; render(); status.textContent = `识别未完成：${error.message}。所选页已保留，可以重试。`; } }
    finally { working = false; if (valid(token)) controls(); }
  });
  closeButton.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { generation++; });
  return {
    open(document) { documentInfo = document; scope = notebookId(); working = false; heading.textContent = `${document.name} · 解析情况`; if (!dialog.open) dialog.showModal(); load(); },
    close() { if (dialog.open) dialog.close(); },
  };
}
