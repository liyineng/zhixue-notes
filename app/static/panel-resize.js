// Local layout preferences only; no notebook data or model settings are changed.
export function initializePanelResize(onResize = () => {}) {
  const shell = document.querySelector('.app-shell');
  const workspace = document.getElementById('workspace');
  const sidebar = document.getElementById('sidebar-resizer');
  const split = document.getElementById('workspace-resizer');
  if (!shell || !workspace || !sidebar || !split) return;
  const key = 'zhixue-panel-layout:v1';
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(key)) || {}; } catch { /* Use defaults. */ }
  const layout = { sidebar: Number.isFinite(saved.sidebar) ? saved.sidebar : 276,
    ratio: Number.isFinite(saved.ratio) ? saved.ratio : .5 };
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  let timer;
  function apply() {
    const width = shell.getBoundingClientRect().width;
    const maximum = Math.max(220, Math.min(480, width - (width > 1080 ? 720 : 420)));
    const sidebarWidth = clamp(layout.sidebar, 220, maximum);
    shell.style.setProperty('--sidebar-width', `${sidebarWidth}px`);
    sidebar.setAttribute('aria-valuemin', '220'); sidebar.setAttribute('aria-valuemax', String(Math.round(maximum)));
    sidebar.setAttribute('aria-valuenow', String(Math.round(sidebarWidth)));
    sidebar.setAttribute('aria-valuetext', `${Math.round(sidebarWidth)} 像素`);
    const available = Math.max(1, workspace.getBoundingClientRect().width - 8);
    const ratio = clamp(layout.ratio, Math.min(.5, 320 / available), Math.max(.5, 1 - 280 / available));
    workspace.style.setProperty('--chat-share', `${ratio}fr`);
    workspace.style.setProperty('--viewer-share', `${1 - ratio}fr`);
    split.setAttribute('aria-valuemin', String(Math.round(Math.min(.5, 320 / available) * 100)));
    split.setAttribute('aria-valuemax', String(Math.round(Math.max(.5, 1 - 280 / available) * 100)));
    split.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    split.setAttribute('aria-valuetext', `问答栏 ${Math.round(ratio * 100)}%`);
    clearTimeout(timer); timer = setTimeout(onResize, 120);
  }
  const save = () => localStorage.setItem(key, JSON.stringify(layout));
  for (const [handle, kind] of [[sidebar, 'sidebar'], [split, 'ratio']]) {
    let dragging = false;
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      dragging = true; handle.setPointerCapture(event.pointerId); handle.focus();
      document.body.classList.add('resizing-panels'); event.preventDefault();
    });
    handle.addEventListener('pointermove', event => {
      if (!dragging) return;
      if (kind === 'sidebar') layout.sidebar = clamp(event.clientX - shell.getBoundingClientRect().left, 220, Number(handle.getAttribute('aria-valuemax')));
      else layout.ratio = clamp((event.clientX - workspace.getBoundingClientRect().left) / (workspace.clientWidth - 8),
        Number(handle.getAttribute('aria-valuemin')) / 100, Number(handle.getAttribute('aria-valuemax')) / 100);
      apply();
    });
    const finish = () => {
      if (!dragging) return;
      dragging = false; document.body.classList.remove('resizing-panels'); save();
    };
    handle.addEventListener('pointerup', finish); handle.addEventListener('pointercancel', finish);
    handle.addEventListener('lostpointercapture', finish);
    handle.addEventListener('dblclick', () => { layout[kind] = kind === 'sidebar' ? 276 : .5; apply(); save(); });
    handle.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const min = Number(handle.getAttribute('aria-valuemin')), max = Number(handle.getAttribute('aria-valuemax'));
      const current = Number(handle.getAttribute('aria-valuenow'));
      const next = event.key === 'Home' ? min : event.key === 'End' ? max : current + (event.key === 'ArrowLeft' ? -1 : 1) * (kind === 'sidebar' ? 20 : 3);
      layout[kind] = clamp(next, min, max) / (kind === 'sidebar' ? 1 : 100); apply(); save();
    });
  }
  window.addEventListener('resize', apply);
  apply();
}
