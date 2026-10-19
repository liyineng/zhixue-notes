// Notebook-scoped question attachments, separate from the source library.
export function initializeAttachmentManager({ api, notebookId, isBusy, onUse, onRename, onDelete, onJump, deleteNote, toast }) {
  const dialog = document.getElementById('attachments-dialog');
  const list = document.getElementById('managed-attachment-list');
  const search = document.getElementById('managed-attachment-search');
  const status = document.getElementById('managed-attachment-status');
  let attachments = [], generation = 0, scope = null, working = false;
  const endpoint = id => `/api/notebooks/${encodeURIComponent(scope)}/attachments${id ? '/' + encodeURIComponent(id) : ''}`;
  const button = (label, action) => {
    const element = document.createElement('button');
    element.type = 'button'; element.className = 'button ghost'; element.textContent = label;
    element.addEventListener('click', () => Promise.resolve().then(action).catch(error => toast(error.message, true)));
    return element;
  };
  const valid = () => dialog.open && scope === notebookId();
  async function change(action) {
    if (!valid() || working) return;
    if (isBusy()) return toast('请等待当前操作完成。');
    working = true; render();
    try { await action(); }
    finally { working = false; if (valid()) render(); }
  }
  function render() {
    list.replaceChildren();
    const query = search.value.trim().toLocaleLowerCase();
    const visible = attachments.filter(item => item.name.toLocaleLowerCase().includes(query));
    status.textContent = query ? `找到 ${visible.length} / ${attachments.length} 个附件` : `${attachments.length} 个附件 · 当前笔记本`;
    if (!visible.length) {
      const empty = document.createElement('p'); empty.className = 'attachment-empty';
      empty.textContent = query ? '没有匹配的附件。' : '还没有问题附件。从输入框“上传资料”添加后，会保存在这里。';
      list.append(empty); return;
    }
    for (const item of visible) {
      const card = document.createElement('article'); card.className = 'managed-attachment'; card.dataset.attachmentId = item.id;
      const name = document.createElement('h3'); name.textContent = item.name; name.title = item.name;
      const meta = document.createElement('p'); meta.className = 'attachment-meta';
      const size = item.size_bytes < 1024 ? `${item.size_bytes} B` : item.size_bytes < 1048576 ? `${Math.ceil(item.size_bytes / 1024)} KB` : `${(item.size_bytes / 1048576).toFixed(1)} MB`;
      const uses = item.exchanges || [], active = uses.filter(exchange => !exchange.deleted_at);
      const date = new Date(item.created_at);
      meta.textContent = `${item.kind === 'image' ? '图片' : item.name.split('.').pop().toUpperCase()} · ${size} · ${Number.isNaN(date.valueOf()) ? '' : date.toLocaleDateString()} · ` +
        (!item.available ? '原文件缺失' : uses.length ? `${active.length} 条对话使用${uses.length > active.length ? `，${uses.length - active.length} 条可撤销删除记录` : ''}` : '尚未用于对话');
      const actions = document.createElement('div'); actions.className = 'attachment-card-actions';
      const open = document.createElement('a'); open.className = 'button ghost'; open.textContent = '打开原文件';
      open.href = item.url; open.target = '_blank'; open.rel = 'noopener';
      if (item.available) actions.append(open);
      const use = button('附到本次问题', () => change(async () => {
        const data = await api(endpoint(item.id));
        if (!valid()) return;
        if (await onUse(data.attachment)) { dialog.close(); toast('已添加到输入框上方，发送问题时使用。'); }
      }));
      use.disabled = working || !item.available || isBusy();
      const rename = button('重命名', () => change(async () => {
        const value = window.prompt('新的附件名称（请保留扩展名）', item.name);
        if (value === null || value.trim() === item.name) return;
        const data = await api(endpoint(item.id), { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name:value.trim()}) });
        if (!valid()) return;
        item.name = data.attachment.name; onRename(data.attachment); toast('附件已重命名，历史对话保留发送时的名称。');
      }));
      rename.disabled = working || isBusy();
      const remove = button('删除文件', () => change(async () => {
        if (!window.confirm(`永久删除“${item.name}”？文件不会再出现在附件管理中。${deleteNote(item.id)}`)) return;
        await api(endpoint(item.id), {method:'DELETE'});
        if (!valid()) return;
        attachments = attachments.filter(other => other.id !== item.id); onDelete(item.id); toast('附件已删除。');
      }));
      remove.disabled = working || isBusy() || uses.length > 0;
      if (uses.length) remove.title = '对话和可撤销的删除记录仍在使用此附件，暂不能永久删除。';
      actions.append(use, rename, remove); card.append(name, meta, actions);
      if (uses.length) {
        const details = document.createElement('details'); details.className = 'attachment-exchanges';
        const summary = document.createElement('summary'); summary.textContent = `关联对话（${uses.length}）`; details.append(summary);
        for (const exchange of uses) {
          if (exchange.deleted_at) {
            const text = document.createElement('p'); text.textContent = `已删除，可撤销：${exchange.question}`; details.append(text);
          } else {
            const jump = button(exchange.question || '查看对话', () => { dialog.close(); onJump(exchange.id); });
            details.append(jump);
          }
        }
        card.append(details);
      }
      const extracted = document.createElement('details'); extracted.className = 'attachment-extracted';
      const heading = document.createElement('summary'); heading.textContent = '查看提取文字';
      const text = document.createElement('pre'); text.textContent = '展开后读取'; extracted.append(heading, text);
      let loaded = false;
      extracted.addEventListener('toggle', async () => {
        if (!extracted.open || loaded) return;
        loaded = true; text.textContent = '正在读取…';
        try { const data = await api(endpoint(item.id)); text.textContent = data.attachment.ocr_text || '暂未提取到文字。'; }
        catch (error) { loaded = false; text.textContent = error.message; }
      });
      card.append(extracted); list.append(card);
    }
  }
  document.getElementById('manage-question-attachments').addEventListener('click', async () => {
    const version = ++generation; scope = notebookId(); attachments = []; search.value = '';
    dialog.showModal(); list.replaceChildren(); status.textContent = '正在加载附件…';
    try {
      const data = await api(endpoint());
      if (version !== generation || !valid()) return;
      attachments = data.attachments || []; render();
    } catch (error) { if (version === generation && valid()) status.textContent = error.message; }
  });
  document.getElementById('close-attachments').addEventListener('click', () => dialog.close());
  search.addEventListener('input', render);
  dialog.addEventListener('close', () => { generation++; });
}
