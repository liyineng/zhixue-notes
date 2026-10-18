// Local layout preferences only; no notebook data or model settings are changed.
export function initializePanelResize(onResize = () => {}) {
  const shell = document.querySelector('.app-shell');
  const workspace = document.getElementById('workspace');
  const sidebar = document.getElementById('sidebar-resizer');
  const split = document.getElementById('workspace-resizer');
  const studySplit = document.getElementById('study-resizer');
  const viewer = document.querySelector('.viewer-pane');
  if (!shell || !workspace || !sidebar || !split) return;
  const key = 'zhixue-panel-layout:v1';
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(key)) || {}; } catch { /* Use defaults. */ }
  const defaults = { sidebar: 276, ratio: .5, studyRatio: .34, documentRatio: .48, documentVerticalRatio: .42 };
  const layout = Object.fromEntries(Object.entries(defaults).map(([name, value]) => [name, Number.isFinite(saved[name]) ? saved[name] : value]));
  const studying = () => viewer.classList.contains('study-open');
  const horizontal = () => innerWidth >= 1280;
  const field = kind => kind === 'study' ? (horizontal() ? 'documentRatio' : 'documentVerticalRatio') : kind === 'ratio' && studying() ? 'studyRatio' : kind;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  let timer;
  function apply() {
    const width = shell.getBoundingClientRect().width;
    const minimumWorkspace = studying() && horizontal() ? 1000 : width > 1080 ? 720 : 420;
    const maximum = Math.max(220, Math.min(480, width - minimumWorkspace));
    const sidebarWidth = clamp(layout.sidebar, 220, maximum);
    shell.style.setProperty('--sidebar-width', `${sidebarWidth}px`);
    sidebar.setAttribute('aria-valuemin', '220'); sidebar.setAttribute('aria-valuemax', String(Math.round(maximum)));
    sidebar.setAttribute('aria-valuenow', String(Math.round(sidebarWidth)));
    sidebar.setAttribute('aria-valuetext', `${Math.round(sidebarWidth)} 像素`);
    const available = Math.max(1, workspace.getBoundingClientRect().width - 8);
    const minimumRight = studying() && horizontal() ? 620 : 280;
    const lower = Math.min(.5, 320 / available), upper = Math.max(lower, 1 - minimumRight / available);
    const ratio = clamp(layout[field('ratio')], lower, upper);
    workspace.style.setProperty('--chat-share', `${ratio}fr`);
    workspace.style.setProperty('--viewer-share', `${1 - ratio}fr`);
    split.setAttribute('aria-valuemin', String(Math.ceil(lower * 100)));
    split.setAttribute('aria-valuemax', String(Math.floor(upper * 100)));
    split.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
    split.setAttribute('aria-valuetext', `问答栏 ${Math.round(ratio * 100)}%`);
    if (studySplit && studying()) {
      const bar = document.getElementById('right-pane-tabs').getBoundingClientRect().height;
      const size = Math.max(1, (horizontal() ? viewer.clientWidth : viewer.clientHeight - bar) - 8);
      const min = Math.min(.5, (horizontal() ? 280 : 100) / size);
      const max = Math.max(min, 1 - (horizontal() ? 300 : 150) / size);
      const proportion = clamp(layout[field('study')], min, max);
      viewer.style.setProperty('--document-share', `${proportion}fr`);
      viewer.style.setProperty('--quiz-share', `${1 - proportion}fr`);
      studySplit.setAttribute('aria-orientation', horizontal() ? 'vertical' : 'horizontal');
      studySplit.setAttribute('aria-valuemin', String(Math.ceil(min * 100)));
      studySplit.setAttribute('aria-valuemax', String(Math.floor(max * 100)));
      studySplit.setAttribute('aria-valuenow', String(Math.round(proportion * 100)));
      studySplit.setAttribute('aria-valuetext', `原文占 ${Math.round(proportion * 100)}%`);
    }
    clearTimeout(timer); timer = setTimeout(onResize, 120);
  }
  const save = () => localStorage.setItem(key, JSON.stringify(layout));
  for (const [handle, kind] of [[sidebar, 'sidebar'], [split, 'ratio'], ...(studySplit ? [[studySplit, 'study']] : [])]) {
    let dragging = false;
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      dragging = true; handle.setPointerCapture(event.pointerId); handle.focus();
      document.body.classList.add('resizing-panels'); event.preventDefault();
    });
    handle.addEventListener('pointermove', event => {
      if (!dragging) return;
      if (kind === 'sidebar') layout.sidebar = clamp(event.clientX - shell.getBoundingClientRect().left, 220, Number(handle.getAttribute('aria-valuemax')));
      else if (kind === 'study') {
        const rect = viewer.getBoundingClientRect(), bar = document.getElementById('right-pane-tabs').getBoundingClientRect().height;
        const fraction = horizontal() ? (event.clientX - rect.left) / (rect.width - 8) : (event.clientY - rect.top - bar) / (rect.height - bar - 8);
        layout[field(kind)] = clamp(fraction, Number(handle.getAttribute('aria-valuemin')) / 100, Number(handle.getAttribute('aria-valuemax')) / 100);
      } else layout[field(kind)] = clamp((event.clientX - workspace.getBoundingClientRect().left) / (workspace.clientWidth - 8),
        Number(handle.getAttribute('aria-valuemin')) / 100, Number(handle.getAttribute('aria-valuemax')) / 100);
      apply();
    });
    const finish = () => {
      if (!dragging) return;
      dragging = false; document.body.classList.remove('resizing-panels'); save();
    };
    handle.addEventListener('pointerup', finish); handle.addEventListener('pointercancel', finish);
    handle.addEventListener('lostpointercapture', finish);
    handle.addEventListener('dblclick', () => { layout[field(kind)] = defaults[field(kind)]; apply(); save(); });
    handle.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const min = Number(handle.getAttribute('aria-valuemin')), max = Number(handle.getAttribute('aria-valuemax'));
      const current = Number(handle.getAttribute('aria-valuenow'));
      const next = event.key === 'Home' ? min : event.key === 'End' ? max : current + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) * (kind === 'sidebar' ? 20 : 3);
      layout[field(kind)] = clamp(next, min, max) / (kind === 'sidebar' ? 1 : 100); apply(); save();
    });
  }
  window.addEventListener('resize', apply);
  workspace.addEventListener('panelschange', apply);
  apply();
}
