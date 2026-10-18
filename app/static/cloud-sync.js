export function initializeCloudSync({ api, jsonOptions, showToast, refresh }) {
  const button = document.querySelector('#sync-button');
  const dialog = document.querySelector('#sync-dialog');
  const enabled = document.querySelector('#sync-enabled');
  const status = document.querySelector('#sync-status');
  const now = document.querySelector('#sync-now');
  const conflicts = document.querySelector('#sync-conflicts');
  let revision = null;
  let working = false;
  let latest = null;

  function render(data) {
    latest = data;
    enabled.checked = data.enabled;
    enabled.disabled = working || (!data.available && !data.enabled);
    now.disabled = working || !data.enabled || data.running;
    button.textContent = `☁ iCloud 同步 · ${!data.enabled ? '未开启' : data.last_error ? '待重试' : data.conflicts.length ? `${data.conflicts.length} 处冲突` : data.running ? '合并中' : '已开启'}`;
    status.textContent = working ? '正在合并各设备的修改，请稍候…' : !data.available
      ? '未找到 iCloud Drive，请先在系统设置中开启“同步此 Mac”。'
      : data.last_error || (data.enabled ? data.last_sync
        ? `本机合并完成：${new Date(data.last_sync).toLocaleString()} · 已发现 ${data.devices} 台设备。云端上传进度请在 Finder 查看。`
        : '已开启，等待首次同步。' : '开启后，现有学习资料将写入你的 iCloud Drive。');
    document.querySelector('#sync-folder').textContent = data.folder;
    for (const control of conflicts.querySelectorAll('button, select')) control.disabled = working || !data.enabled || data.running;
    // Preserve conflict selections while status polling refreshes unchanged versions.
    const signature = JSON.stringify(data.conflicts);
    if (conflicts.dataset.signature === signature) return;
    conflicts.dataset.signature = signature;
    conflicts.replaceChildren();
    for (const conflict of data.conflicts) {
      const card = document.createElement('section');
      card.className = 'sync-conflict';
      const heading = document.createElement('h3');
      const names = { notebooks: '笔记本', documents: '资料', notes: '笔记', chat_exchanges: '问答', chunks: '资料识别内容', question_images: '题目图片' };
      heading.textContent = `${names[conflict.kind]}存在不同版本`;
      card.append(heading);
      const select = document.createElement('select');
      select.disabled = working || !data.enabled || data.running;
      select.setAttribute('aria-label', '选择保留的版本');
      for (const [index, choice] of conflict.choices.entries()) {
        select.append(new Option(`版本 ${index + 1}：${choice.title || choice.preview.slice(0, 50)}`, choice.version));
      }
      const preview = document.createElement('pre');
      preview.className = 'sync-preview';
      const update = () => { preview.textContent = conflict.choices.find(choice => choice.version === select.value)?.preview || ''; };
      select.addEventListener('change', update);
      update();
      const choose = document.createElement('button');
      choose.type = 'button';
      choose.disabled = working || !data.enabled || data.running;
      choose.className = 'button secondary';
      choose.textContent = '保留所选版本';
      choose.addEventListener('click', () => run('/api/sync/resolve', { key: conflict.key, version: select.value, keep_both: false }));
      card.append(select, preview, choose);
      if (conflict.can_keep_both) {
        const both = document.createElement('button');
        both.type = 'button';
        both.disabled = working || !data.enabled || data.running;
        both.className = 'button secondary';
        both.textContent = '全部保留为笔记';
        both.addEventListener('click', () => run('/api/sync/resolve', { key: conflict.key, version: select.value, keep_both: true }));
        card.append(both);
      }
      conflicts.append(card);
    }
  }

  async function receive(data) {
    render(data);
    if (revision !== null && data.revision && data.revision !== revision) {
      if (!await refresh()) {
        status.textContent += ' 有更新待显示，编辑或生成完成后自动更新。';
        return;
      }
    }
    revision = data.revision;
  }

  async function run(url, body) {
    if (working) return;
    working = true;
    render(latest);
    for (const control of conflicts.querySelectorAll('button, select')) control.disabled = true;
    try {
      const data = await api(url, jsonOptions('POST', body || {}));
      await receive(data);
      showToast(data.conflicts.length ? '合并完成，有冲突版本待处理。' : '本机合并完成，iCloud 将继续传输文件。');
    } catch (error) {
      showToast(error.message, true);
    } finally {
      working = false;
      conflicts.dataset.signature = '';
      await poll();
    }
  }

  async function poll() {
    if (working) return;
    try { await receive(await api('/api/sync')); }
    catch (error) { if (dialog.open) status.textContent = `无法读取同步状态：${error.message}`; }
  }

  button.addEventListener('click', async () => { dialog.showModal(); await poll(); });
  document.querySelector('#close-sync').addEventListener('click', () => dialog.close());
  now.addEventListener('click', () => run('/api/sync/run'));
  enabled.addEventListener('change', async () => {
    if (working) return;
    working = true;
    const value = enabled.checked;
    try {
      const data = await api('/api/sync', jsonOptions('PUT', { enabled: value }));
      render(data);
      working = false;
      if (value) await run('/api/sync/run');
    } catch (error) { showToast(error.message, true); }
    finally { working = false; await poll(); }
  });
  poll();
  window.setInterval(poll, 15000);
}
