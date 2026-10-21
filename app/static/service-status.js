/** Connection health is separate from generation progress. Never retries a model request. */
export function initializeServiceStatus({
  isGenerating = () => false,
  onReconnect = () => {},
  intervalMs = 12000,
  timeoutMs = 5000,
  slowGenerationMs = 30000,
  fetchImpl = (...args) => fetch(...args),
  container = document.body,
} = {}) {
  const banner = document.createElement('aside');
  banner.id = 'service-status';
  banner.className = 'service-status';
  banner.setAttribute('role', 'status');
  banner.setAttribute('aria-live', 'polite');
  banner.hidden = true;
  const copy = document.createElement('span');
  copy.className = 'service-status-copy';
  const message = document.createElement('strong');
  const help = document.createElement('span');
  copy.append(message, help);
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'button ghost';
  retry.textContent = '检查连接';
  banner.append(copy, retry);
  container.append(banner);

  let status = 'checking';
  let stopped = false;
  let inFlight = null;
  let controller = null;
  let generationStarted = null;
  let hasDisconnected = false;
  let verified = false;
  const remote = !['localhost', '127.0.0.1', '[::1]', '::1'].includes(location.hostname);
  function reserveSpace() {
    if (container === document.body && document.querySelector('.app-shell')) {
      document.documentElement.style.setProperty('--service-notice-height', `${banner.hidden ? 0 : Math.ceil(banner.getBoundingClientRect().height)}px`);
    }
  }
  const sizeObserver = new ResizeObserver(reserveSpace);
  sizeObserver.observe(banner);

  function render() {
    const generating = Boolean(isGenerating());
    if (generating && generationStarted === null) generationStarted = Date.now();
    if (!generating) generationStarted = null;
    const slow = generating && Date.now() - generationStarted >= slowGenerationMs;
    banner.dataset.state = status;
    retry.disabled = status === 'reconnecting';
    retry.textContent = status === 'reconnecting' ? '正在检查…' : '检查连接';
    if (status === 'disconnected' || status === 'reconnecting') {
      banner.hidden = false;
      message.textContent = status === 'reconnecting' ? '正在重新连接本地服务' : '本地服务连接已断开';
      help.textContent = remote
        ? '请在运行智学笔记的电脑上重新打开启动入口，再检查连接。输入内容会保留。'
        : '请重新打开 start.command（Mac）或 start.bat（Windows），再检查连接。输入内容会保留。';
      retry.hidden = false;
    } else if (status === 'connected' && slow) {
      banner.hidden = false;
      message.textContent = '本地服务连接正常';
      help.textContent = '当前任务仍在处理，可查看对话中的生成状态或取消任务。';
      retry.hidden = true;
    } else {
      banner.hidden = true;
    }
    reserveSpace();
  }

  async function check() {
    if (stopped) return false;
    if (inFlight) return inFlight;
    const reconnecting = status === 'disconnected';
    if (reconnecting) {
      status = 'reconnecting';
      render();
    }
    controller = new AbortController();
    const requestController = controller;
    const timer = setTimeout(() => requestController.abort(), timeoutMs);
    inFlight = (async () => {
      try {
        const response = await fetchImpl('/api/health', {
          cache: 'no-store', credentials: 'same-origin', signal: requestController.signal,
        });
        if (!response.ok) throw new Error('Health check failed');
        const payload = await response.json();
        if (payload?.ok !== true || (payload.service && payload.service !== 'zhixue-notes')) {
          throw new Error('Unexpected service');
        }
        if (stopped) return false;
        const recovered = hasDisconnected;
        verified = true;
        hasDisconnected = false;
        status = 'connected';
        render();
        // Callback may recover polling but must not recreate messages or resubmit questions.
        if (recovered) Promise.resolve().then(() => onReconnect()).catch(() => {});
        return true;
      } catch {
        if (!stopped) {
          hasDisconnected = true;
          status = 'disconnected';
          render();
        }
        return false;
      } finally {
        clearTimeout(timer);
        if (controller === requestController) controller = null;
      }
    })();
    const result = await inFlight;
    inFlight = null;
    return result;
  }

  function wake() { if (!document.hidden) void check(); }
  function onOffline() { void check(); }
  retry.addEventListener('click', () => { void check(); });
  window.addEventListener('online', wake);
  window.addEventListener('offline', onOffline);
  document.addEventListener('visibilitychange', wake);
  const interval = setInterval(() => { render(); if (!document.hidden) void check(); }, intervalMs);
  // The first ping verifies service identity; no data reload or draft mutation.
  void check();
  return {
    check,
    getStatus: () => ({ state: status, verified }),
    stop() {
      stopped = true;
      clearInterval(interval);
      controller?.abort();
      sizeObserver.disconnect();
      document.documentElement.style.removeProperty('--service-notice-height');
      window.removeEventListener('online', wake);
      window.removeEventListener('offline', onOffline);
      document.removeEventListener('visibilitychange', wake);
      banner.remove();
    },
  };
}
