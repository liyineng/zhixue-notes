import { renderAnswerHtml, matchingSourceLines } from "/static/answer-format.js";
import { initializeCloudSync } from '/static/cloud-sync.js';
import { presentationHighlights } from '/static/presentation-highlights.js';
import { initializePanelResize } from '/static/panel-resize.js';
import { renderQuiz } from '/static/quizzes.js';
import renderMathInElement from '/static/vendor/katex/contrib/auto-render.mjs';
import * as pdfjsLib from "/static/vendor/pdfjs/pdf.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.mjs";

const state = {
  notebooks: [],
  notebookId: new URLSearchParams(location.search).get('notebook_id') || localStorage.getItem("zhixue-notebook") || "default",
  applications: [],
  activeQuiz: null,
  quizGeneration: 0,
  selectedSources: new Set(),
  notes: [],
  answers: new Map(),
  busy: false,
  chatPending: false,
  searchGeneration: 0,
  documents: [],
  settings: null,
  messageCitations: new Map(),
  pdfDocument: null,
  pdfLoadingTask: null,
  pdfDocumentId: null,
  viewerDocumentId: null,
  previewCitations: [],
  previewPageLines: new Map(),
  pdfPageCount: 0,
  pdfPage: 1,
  highlightLines: [],
  renderGeneration: 0,
  renderTask: null,
  viewerGeneration: 0,
  draftImages: [],
  imageTextEdited: false,
  activeCitation: 1,
  highlightMessageId: "",
  deletedCount: 0,
  replyTo: null,
  activeGeneration: null,
  pendingExchangeId: null,
  pendingExchange: null,
  cancelling: false,
};

const dom = {
  workspace: document.querySelector("#workspace"),
  fileInput: document.querySelector("#file-input"),
  uploadButton: document.querySelector("#upload-button"),
  dropzone: document.querySelector("#dropzone"),
  documentList: document.querySelector("#document-list"),
  documentCount: document.querySelector("#document-count"),
  settingsButton: document.querySelector("#settings-button"),
  settingsDialog: document.querySelector("#settings-dialog"),
  settingsForm: document.querySelector("#settings-form"),
  closeSettings: document.querySelector("#close-settings"),
  cancelSettings: document.querySelector("#cancel-settings"),
  modelPill: document.querySelector("#model-pill"),
  messages: document.querySelector("#messages"),
  emptyChat: document.querySelector("#empty-chat"),
  askForm: document.querySelector("#ask-form"),
  questionInput: document.querySelector("#question-input"),
  sendButton: document.querySelector("#send-button"),
  summaryButton: document.querySelector("#summary-button"),
  viewerName: document.querySelector("#viewer-name"),
  viewerEmpty: document.querySelector("#viewer-empty"),
  viewerScroll: document.querySelector("#viewer-scroll"),
  pageWrap: document.querySelector("#page-wrap"),
  canvas: document.querySelector("#pdf-canvas"),
  highlightLayer: document.querySelector("#highlight-layer"),
  pageInput: document.querySelector("#page-input"),
  pageTotal: document.querySelector("#page-total"),
  prevPage: document.querySelector("#prev-page"),
  nextPage: document.querySelector("#next-page"),
  openFile: document.querySelector("#open-file"),
  sourceText: document.querySelector("#source-text"),
  toast: document.querySelector("#toast"),
};

const ui = Object.fromEntries([
  "notebook-select", "new-notebook", "rename-notebook", "delete-notebook", "notebook-title", "selection-status",
  "sources-tab", "notes-tab", "sources-content", "notes-content", "source-search-form",
  "applications-tab", "applications-content", "application-count", "application-list",
  "source-search", "search-results", "select-all-sources", "note-count", "note-list",
  "new-note", "export-notes", "note-dialog", "note-form", "note-title", "note-content",
  "note-sources", "close-note", "delete-note", "export-note", "save-note", "clear-chat", "undo-chat-delete",
  "question-image-input", "attach-question-image", "question-image-draft", "question-image-list", "question-image-text", "highlight-legend",
  "reply-draft", "reply-preview", "remove-reply", "cancel-generation",
].map((id) => [id, document.getElementById(id)]));
const emptyChatTemplate = dom.emptyChat.outerHTML;
let currentNote = null;
let noteSaving = false;
let chatPollTimer = null;
let chatPollFailures = 0;
let holdChatScroll = false;

const citationPalette = [
  ["#2563eb", "rgba(37,99,235,.19)"], ["#047857", "rgba(5,150,105,.20)"],
  ["#b45309", "rgba(245,158,11,.23)"], ["#7c3aed", "rgba(124,58,237,.19)"],
  ["#be185d", "rgba(219,39,119,.19)"], ["#0e7490", "rgba(6,182,212,.21)"],
  ["#b91c1c", "rgba(239,68,68,.18)"], ["#4d7c0f", "rgba(132,204,22,.22)"],
  ["#9a3412", "rgba(234,88,12,.19)"], ["#475569", "rgba(100,116,139,.21)"],
  ["#4338ca", "rgba(99,102,241,.19)"], ["#0f766e", "rgba(20,184,166,.20)"],
];
function citationTone(index) { return citationPalette[(Math.max(1, Number(index) || 1) - 1) % citationPalette.length]; }
function colorCitation(element, index) {
  const [color, background] = citationTone(index);
  element.style.setProperty("--citation-color", color);
  element.style.setProperty("--citation-bg", background);
}

function noteDirty() {
  return ui["note-title"].value !== (currentNote?.title || "") ||
    ui["note-content"].value !== (currentNote?.content || "");
}

function closeNote() {
  if (noteSaving) return false;
  if (noteDirty() && !window.confirm("笔记有未保存的修改，放弃修改并关闭？")) return false;
  ui["note-dialog"].close();
  return true;
}

function jsonOptions(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function selectedIds() {
  return state.documents.filter((doc) => state.selectedSources.has(doc.id)).map((doc) => doc.id);
}

function saveSourceSelection() {
  localStorage.setItem(`zhixue-sources:${state.notebookId}`, JSON.stringify(selectedIds()));
}

function selectionStatus() {
  ui["selection-status"].textContent = `已选择 ${selectedIds().length} / ${state.documents.length} 个来源`;
  ui["select-all-sources"].checked = !!state.documents.length && selectedIds().length === state.documents.length;
  ui["select-all-sources"].indeterminate = selectedIds().length > 0 && selectedIds().length < state.documents.length;
  dom.questionInput.placeholder = selectedIds().length
    ? '结合已选资料提问，Enter 发送，Shift+Enter 换行'
    : '输入问题，未勾选资料时按通用知识回答';
  const heading = document.querySelector("#empty-chat h1");
  if (heading) heading.textContent = state.documents.length ? "从这个笔记本开始提问" : "上传资料后开始提问";
}

function setBusy(busy) {
  state.busy = busy;
  [dom.sendButton, dom.summaryButton, dom.uploadButton, ui["notebook-select"],
    ui["new-notebook"], ui["rename-notebook"], ui["delete-notebook"], ui["clear-chat"]].forEach((el) => el.disabled = busy);
  ui["attach-question-image"].disabled = busy;
  document.querySelectorAll(".draft-remove").forEach((button) => button.disabled = busy);
  document.querySelectorAll(".delete-exchange").forEach((button) => button.disabled = busy || button.dataset.pending === "true");
  document.querySelectorAll('.delete-application').forEach(button => button.disabled = busy);
  ui["undo-chat-delete"].disabled = busy || !state.deletedCount;
  document.querySelectorAll('.quote-answer').forEach((button) => button.disabled = busy || !button.dataset.exchangeId);
  ui['remove-reply'].disabled = busy;
  updateCancelButton();
}

function rememberNotebook() {
  localStorage.setItem('zhixue-notebook', state.notebookId);
  const url = new URL(location.href);
  if (url.searchParams.has('notebook_id') && url.searchParams.get('notebook_id') !== state.notebookId) {
    url.searchParams.set('notebook_id', state.notebookId);
    history.replaceState(history.state, '', url);
  }
}

async function loadNotebooks() {
  const { notebooks } = await api("/api/notebooks");
  state.notebooks = notebooks;
  if (!notebooks.some((n) => n.id === state.notebookId)) state.notebookId = notebooks[0]?.id || "default";
  rememberNotebook();
  ui["notebook-select"].replaceChildren();
  for (const notebook of notebooks) {
    const option = new Option(`${notebook.name} (${notebook.document_count})`, notebook.id);
    ui["notebook-select"].append(option);
  }
  ui["notebook-select"].value = state.notebookId;
  ui["notebook-title"].textContent = notebooks.find((n) => n.id === state.notebookId)?.name || "笔记本";
}

async function switchNotebook(id) {
  if (state.busy) return;
  closeQuizPanel(false);
  window.clearTimeout(chatPollTimer);
  state.chatPending = false;
  state.searchGeneration++;
  state.notebookId = id;
  state.draftImages = [];
  renderDraftImages();
  state.replyTo = null;
  state.pendingExchangeId = null;
  state.pendingExchange = null;
  renderReplyDraft();
  rememberNotebook();
  state.selectedSources.clear();
  state.documents = [];
  state.notes = [];
  ui["note-list"].replaceChildren();
  ui["note-count"].textContent = "0";
  state.applications = [];
  renderApplications();
  renderDocuments();
  selectionStatus();
  state.messageCitations.clear();
  state.answers.clear();
  state.searchGeneration++;
  ui["source-search"].value = "";
  dom.messages.innerHTML = emptyChatTemplate;
  dom.questionInput.value = "";
  resetViewer();
  setBusy(true);
  try {
    await loadNotebooks();
    await Promise.all([loadDocuments(true), loadNotes(), loadChat()]);
    setLibraryTab(localStorage.getItem(`zhixue-library-tab:${state.notebookId}`) || 'sources');
  } finally { setBusy(state.chatPending); }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) {
    const detail = Array.isArray(data?.detail)
      ? data.detail.map((item) => item.msg).join("；")
      : data?.detail || `请求失败：HTTP ${response.status}`;
    const error = new Error(detail);
    error.status = response.status;
    error.chatId = response.headers.get("X-Chat-Id");
    error.cancelled = response.headers.get('X-Generation-Cancelled') === '1';
    throw error;
  }
  return data;
}

let toastTimer = null;
function showToast(message, error = false) {
  dom.toast.textContent = message;
  dom.toast.classList.toggle("error", error);
  dom.toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => dom.toast.classList.remove("show"), 3200);
}

function formatBytes(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function renderDocuments() {
  dom.documentCount.textContent = String(state.documents.length);
  dom.documentList.replaceChildren();
  if (!state.documents.length) {
    const empty = document.createElement("div");
    empty.className = "document-empty";
    empty.textContent = "还没有资料。上传 PDF、PPTX、DOCX、Markdown 或 TXT 文件后即可提问。";
    dom.documentList.append(empty);
    return;
  }

  state.documents.forEach((doc) => {
    const item = document.createElement("div");
    item.className = "document-item";
    item.dataset.documentId = doc.id;

    const icon = document.createElement("input");
    icon.type = "checkbox";
    icon.checked = state.selectedSources.has(doc.id);
    icon.setAttribute("aria-label", `选择 ${doc.name}`);
    icon.addEventListener("change", () => {
      if (icon.checked) state.selectedSources.add(doc.id); else state.selectedSources.delete(doc.id);
      saveSourceSelection();
      selectionStatus();
    });

    const content = document.createElement("button");
    content.type = "button";
    content.style.cssText = "border:0;background:transparent;padding:0;text-align:left;min-width:0;cursor:pointer";
    content.title = doc.name;

    const name = document.createElement("div");
    name.className = "document-name";
    name.textContent = doc.name;
    const meta = document.createElement("div");
    meta.className = "document-meta";
    meta.textContent = `${doc.kind.toUpperCase()} · ${formatBytes(doc.size_bytes)} · ${doc.chunk_count} 个片段`;
    content.append(name, meta);
    content.addEventListener("click", () => openDocument(doc).catch(error => showToast(error.message, true)));

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "document-delete";
    remove.title = "删除资料";
    remove.setAttribute("aria-label", `删除 ${doc.name}`);
    remove.innerHTML = '<svg class="icon"><use href="#i-trash"></use></svg>';
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (state.busy) return showToast("请等待当前操作完成。");
      if (!window.confirm(`删除“${doc.name}”？`)) return;
      try {
        await api(`/api/documents/${doc.id}`, { method: "DELETE" });
        if (state.pdfDocumentId === doc.id) resetViewer();
        await loadDocuments();
        await loadNotebooks();
        showToast("资料已删除。");
      } catch (error) {
        showToast(error.message, true);
      }
    });

    const move = document.createElement("button");
    move.type = "button";
    move.className = "document-move";
    move.textContent = "移动";
    move.setAttribute("aria-label", `移动 ${doc.name}`);
    move.addEventListener("click", () => moveDocument(doc).catch((error) => showToast(error.message, true)));
    const actions = document.createElement("div");
    actions.className = "document-actions";
    actions.append(move, remove);
    item.append(icon, content, actions);
    dom.documentList.append(item);
  });
  selectionStatus();
}

async function loadDocuments(selectAll = false) {
  const notebookId = state.notebookId;
  const previous = new Set(state.documents.map((doc) => doc.id));
  const data = await api(`/api/documents?notebook_id=${encodeURIComponent(notebookId)}`);
  if (notebookId !== state.notebookId) return;
  state.documents = data.documents || [];
  if (selectAll) {
    const saved = localStorage.getItem(`zhixue-sources:${notebookId}`);
    let ids;
    try { ids = saved === null ? state.documents.map(doc => doc.id) : JSON.parse(saved); }
    catch (_) { ids = []; }
    const selected = new Set(Array.isArray(ids) ? ids : []);
    state.selectedSources = new Set(state.documents.filter(doc => selected.has(doc.id)).map(doc => doc.id));
  } else {
    state.selectedSources = new Set(state.documents.filter(doc => state.selectedSources.has(doc.id) || !previous.has(doc.id)).map(doc => doc.id));
  }
  saveSourceSelection();
  renderDocuments();
  selectionStatus();
  ui["search-results"].hidden = true;
  dom.documentList.hidden = false;
}

async function uploadFiles(files) {
  const accepted = [...files];
  if (!accepted.length) return;
  if (state.busy) return showToast("请等待当前操作完成。");
  const imageSuffixes = new Set(['png', 'jpg', 'jpeg', 'webp']);
  const documentSuffixes = new Set(['pdf', 'pptx', 'docx', 'md', 'markdown', 'txt']);
  const isImage = file => imageSuffixes.has(file.name.split('.').pop().toLowerCase());
  const images = accepted.filter(isImage);
  const invalid = accepted.find(file => !isImage(file) && !documentSuffixes.has(file.name.split('.').pop().toLowerCase()));
  const clearInputs = () => { dom.fileInput.value = ''; ui['question-image-input'].value = ''; };
  let validationError;
  if (invalid) validationError = `不支持 ${invalid.name}，请选择 PDF、PPTX、DOCX、Markdown、TXT 或 PNG、JPG、WebP 图片。`;
  else if (state.draftImages.length + images.length > 3) validationError = '每次最多附带 3 张题目图片，文档数量不受此限制。';
  else if (images.some(file => file.size > 10 * 1024 * 1024)) validationError = '每张题目图片最多 10 MB。';
  if (validationError) { clearInputs(); return showToast(validationError, true); }
  if (images.length && state.imageTextEdited && !window.confirm('添加图片会重置校对文字，继续？')) { clearInputs(); return; }
  let imported = 0, attached = 0, failure = null;
  const unreadPages = [];
  setBusy(true);
  try {
    for (const file of accepted) {
      const body = new FormData();
      body.append("file", file);
      if (isImage(file)) {
        showToast(`正在识别题目：${file.name}`);
        const data = await api(`/api/question-images?notebook_id=${encodeURIComponent(state.notebookId)}`, { method: 'POST', body });
        state.draftImages.push(data.image);
        attached++;
      } else {
        showToast(`正在解析：${file.name}`);
        const data = await api(`/api/documents?notebook_id=${encodeURIComponent(state.notebookId)}`, { method: 'POST', body });
        imported++;
        const unread = data.unread_image_pages?.flatMap(item => item.pages) || [];
        if (unread.length) unreadPages.push(`${data.document.name}：第 ${unread.join('、')} 页图片未成功识别`);
      }
    }
  } catch (error) {
    failure = error.message;
  } finally {
    // Refresh successful imports even if a later file fails; keep image drafts.
    try {
      if (imported) { await loadDocuments(); await loadNotebooks(); }
    } catch (error) { failure ||= error.message; }
    if (attached) renderDraftImages();
    setBusy(false);
    clearInputs();
  }
  const completed = [imported && `已导入 ${imported} 份资料并选为来源`, attached && `已添加 ${attached} 张题目图片，可发送或校对文字`].filter(Boolean).join('；');
  showToast([completed, ...unreadPages, failure].filter(Boolean).join('；'), Boolean(failure || unreadPages.length));
}

function resetViewer() {
  document.getElementById('viewer-document').classList.remove('is-text');
  state.viewerGeneration++;
  state.renderGeneration++;
  state.renderTask?.cancel();
  state.renderTask = null;
  state.pdfLoadingTask?.destroy().catch(() => {});
  state.pdfLoadingTask = null;
  state.pdfDocument = null;
  state.pdfDocumentId = null;
  state.viewerDocumentId = null;
  state.previewCitations = [];
  state.previewPageLines.clear();
  state.pdfPageCount = 0;
  state.pdfPage = 1;
  state.highlightLines = [];
  ui["highlight-legend"].hidden = true;
  ui["highlight-legend"].replaceChildren();
  dom.highlightLayer.replaceChildren();
  dom.canvas.width = 0;
  dom.canvas.height = 0;
  dom.viewerEmpty.hidden = false;
  dom.viewerEmpty.querySelector('p').textContent = '点击回答中的引用编号，原文会在这里定位。';
  dom.viewerScroll.hidden = true;
  dom.viewerName.textContent = "尚未打开资料";
  dom.pageTotal.textContent = "/ 0";
  dom.pageInput.value = "1";
  dom.sourceText.textContent = "尚未选择引用。";
}

async function openDocument(doc) {
  closeStudyDrawers();
  updateStudyLayout();
  if (["pdf", "pptx"].includes(doc.kind)) {
    setMobilePane("viewer");
    await openPdf(doc.id, 1, []);
    return;
  }
  await openTextDocument(doc);
}

async function openTextDocument(doc, errorMessage = '') {
  resetViewer();
  document.getElementById('viewer-document').classList.add('is-text');
  state.viewerDocumentId = doc.id;
  const generation = state.viewerGeneration;
  setMobilePane("viewer");
  dom.viewerName.textContent = doc.name;
  dom.viewerEmpty.hidden = false;
  dom.viewerScroll.hidden = true;
  const data = await api(`/api/documents/${doc.id}/chunks`);
  if (generation !== state.viewerGeneration) return;
  dom.sourceText.replaceChildren();
  if (errorMessage) {
    const warning = document.createElement('p');
    warning.className = 'preview-warning';
    warning.textContent = errorMessage;
    dom.sourceText.append(warning);
    dom.viewerEmpty.querySelector('p').textContent = '原页预览暂不可用，下方可查看提取文字；右上角可下载原文件。';
  }
  for (const chunk of data.chunks) {
    const section = document.createElement("section");
    section.className = "source-chunk";
    const heading = document.createElement("strong");
    heading.textContent = chunk.page ? `第 ${chunk.page} 页 / 部分` : `片段 ${chunk.index}`;
    const paragraph = document.createElement("p");
    paragraph.textContent = chunk.text;
    section.append(heading, paragraph);
    dom.sourceText.append(section);
  }
}

async function openPdf(documentId, pageNumber, lines = [], references = []) {
  const generation = ++state.viewerGeneration;
  const doc = state.documents.find((item) => item.id === documentId);
  if (!doc) {
    showToast("找不到对应资料。", true);
    return;
  }
  state.highlightLines = lines || [];
  state.previewCitations = doc.kind === 'pptx' ? references : [];
  state.viewerDocumentId = documentId;
  dom.viewerName.textContent = doc.name;
  dom.viewerEmpty.hidden = doc.kind !== 'pptx';
  dom.viewerScroll.hidden = doc.kind === 'pptx';
  if (doc.kind === 'pptx') dom.viewerEmpty.querySelector('p').textContent = '正在生成课件原页预览，首次打开可能需要一点时间…';

  try {
    if (state.pdfDocumentId !== documentId || !state.pdfDocument) {
      state.renderGeneration++;
      state.renderTask?.cancel();
      state.renderTask = null;
      dom.highlightLayer.replaceChildren();
      ui["highlight-legend"].hidden = true;
      dom.canvas.width = 0;
      dom.canvas.height = 0;
      state.pdfLoadingTask?.destroy().catch(() => {});
      state.pdfDocument = null;
      state.previewPageLines.clear();
      state.pdfDocumentId = documentId;
      let input = { url: `/api/documents/${documentId}/file` };
      if (doc.kind === 'pptx') {
        const response = await fetch(`/api/documents/${documentId}/preview`);
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail.detail || '课件预览暂不可用。');
        }
        const data = new Uint8Array(await response.arrayBuffer());
        if (generation !== state.viewerGeneration) return;
        input = { data };
      }
      const loadingTask = pdfjsLib.getDocument({
        ...input,
        cMapUrl: "/static/vendor/pdfjs/cmaps/",
        cMapPacked: true,
        iccUrl: "/static/vendor/pdfjs/iccs/",
        standardFontDataUrl: "/static/vendor/pdfjs/standard_fonts/",
        wasmUrl: "/static/vendor/pdfjs/wasm/",
      });
      state.pdfLoadingTask = loadingTask;
      const pdf = await loadingTask.promise;
      if (generation !== state.viewerGeneration) return;
      state.pdfDocument = pdf;
      state.pdfPageCount = state.pdfDocument.numPages;
      dom.pageTotal.textContent = `/ ${state.pdfPageCount}`;
    }
    dom.viewerEmpty.hidden = true;
    dom.viewerScroll.hidden = false;
    await renderPdfPage(pageNumber);
  } catch (error) {
    if (generation !== state.viewerGeneration) return;
    if (doc.kind === 'pptx') {
      await openTextDocument(doc, error.message);
      return;
    }
    console.error("PDF preview failed", error);
    resetViewer();
    state.viewerDocumentId = documentId;
    dom.viewerName.textContent = doc.name;
    dom.sourceText.textContent = `PDF 预览加载失败：${error.message}`;
    showToast("PDF 预览加载失败，可使用右上角按钮在新窗口打开。", true);
  }
}

async function renderPdfPage(pageNumber) {
  if (!state.pdfDocument) return;
  const safePage = Math.max(1, Math.min(Number(pageNumber) || 1, state.pdfPageCount));
  state.pdfPage = safePage;
  dom.pageInput.value = String(safePage);

  if (state.renderTask) {
    state.renderTask.cancel();
    state.renderTask = null;
  }
  const generation = ++state.renderGeneration;
  const page = await state.pdfDocument.getPage(safePage);
  if (generation !== state.renderGeneration) return;

  const baseViewport = page.getViewport({ scale: 1 });
  const availableWidth = Math.max(320, dom.viewerScroll.clientWidth - 44);
  const scale = Math.max(0.55, Math.min(1.9, availableWidth / baseViewport.width));
  const viewport = page.getViewport({ scale });
  const outputScale = window.devicePixelRatio || 1;
  const context = dom.canvas.getContext("2d", { alpha: false });

  dom.canvas.width = Math.floor(viewport.width * outputScale);
  dom.canvas.height = Math.floor(viewport.height * outputScale);
  dom.canvas.style.width = `${Math.floor(viewport.width)}px`;
  dom.canvas.style.height = `${Math.floor(viewport.height)}px`;
  dom.pageWrap.style.width = `${Math.floor(viewport.width)}px`;
  dom.pageWrap.style.height = `${Math.floor(viewport.height)}px`;
  dom.highlightLayer.replaceChildren();

  const renderTask = page.render({
    canvasContext: context,
    viewport,
    transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
  });
  state.renderTask = renderTask;
  try {
    await renderTask.promise;
  } catch (error) {
    if (error?.name === "RenderingCancelledException") return;
    throw error;
  }
  if (generation !== state.renderGeneration) return;
  state.renderTask = null;
  if (state.previewCitations.length) {
    const documentId = state.pdfDocumentId;
    let geometry = state.previewPageLines.get(safePage);
    let locationError = false;
    if (!geometry) {
      try {
        const data = await api(`/api/documents/${documentId}/preview-lines?page=${safePage}`);
        if (generation !== state.renderGeneration || documentId !== state.pdfDocumentId) return;
        geometry = data;
        state.previewPageLines.set(safePage, geometry);
      } catch (_) {
        if (generation !== state.renderGeneration) return;
        geometry = { lines: [] };
        locationError = true;
      }
    }
    const references = state.previewCitations.filter(ref => Number(ref.page) === safePage);
    drawHighlights(viewport, presentationHighlights(references, geometry.lines, safePage, geometry.formula_regions), references, locationError);
  } else drawHighlights(viewport);
}

function drawHighlights(viewport, mappedLines = null, pageReferences = null, locationError = false) {
  const lines = (mappedLines || state.highlightLines).filter(
    (line) => Number(line.page) === Number(state.pdfPage) && Array.isArray(line.bbox)
  );
  const grouped = new Map();
  for (const line of lines) {
    const key = line.bbox.map((v) => v.toFixed(2)).join(",");
    if (!grouped.has(key)) grouped.set(key, { ...line, indices: new Set() });
    grouped.get(key).indices.add(line.citationIndex || state.activeCitation);
  }
  const labelled = new Set();
  for (const line of grouped.values()) {
    // PyMuPDF boxes start at the crop area's top left; PDF.js accepts PDF bottom-left coordinates.
    const [left, , , top] = viewport.viewBox;
    const [x1, y1] = viewport.convertToViewportPoint(left + line.bbox[0], top - line.bbox[1]);
    const [x2, y2] = viewport.convertToViewportPoint(left + line.bbox[2], top - line.bbox[3]);
    const box = document.createElement("div");
    box.className = "highlight-box";
    const indices = [...line.indices];
    const active = indices.includes(Number(state.activeCitation));
    const index = active ? state.activeCitation : indices[0];
    colorCitation(box, index);
    box.dataset.citations = indices.join(",");
    if (line.formula) box.dataset.formula = 'true';
    box.classList.toggle("active-highlight", active);
    if (indices.length > 1) box.style.background = `linear-gradient(90deg, ${indices.map((n) => citationTone(n)[1]).join(",")})`;
    box.style.left = `${Math.min(x1, x2)}px`;
    box.style.top = `${Math.min(y1, y2)}px`;
    box.style.width = `${Math.abs(x2 - x1)}px`;
    box.style.height = `${Math.abs(y2 - y1)}px`;
    for (const number of indices) {
      if (labelled.has(number)) continue;
      const badge = document.createElement("span");
      badge.className = "highlight-label";
      badge.textContent = `[${number}]`;
      colorCitation(badge, number);
      box.append(badge);
      labelled.add(number);
    }
    dom.highlightLayer.append(box);
  }
  ui["highlight-legend"].replaceChildren();
  const indices = pageReferences ? pageReferences.map(ref => Number(ref.index)) : lines.map(line => line.citationIndex || state.activeCitation);
  for (const index of [...new Set(indices)].sort((a, b) => a - b)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "citation-ref";
    button.textContent = `[${index}]`;
    button.setAttribute("aria-label", `查看引用 ${index}`);
    button.setAttribute("aria-pressed", String(Number(index) === Number(state.activeCitation)));
    colorCitation(button, index);
    button.addEventListener("click", () => {
      const citation = state.messageCitations.get(state.highlightMessageId)?.find((c) => Number(c.index) === Number(index)) || state.previewCitations.find(c => Number(c.index) === Number(index));
      if (citation) showCitation(citation, state.highlightMessageId, index);
    });
    ui["highlight-legend"].append(button);
  }
  const missing = pageReferences?.filter(ref => !lines.some(line => Number(line.citationIndex) === Number(ref.index))) || [];
  if (missing.length) {
    const note = document.createElement('span');
    note.className = 'preview-location-note';
    note.textContent = locationError ? '文字标注暂不可用，可再次点击引用重试。' : `${missing.map(ref => `[${ref.index}]`).join('、')} 已定位到此页，未匹配到可精确标注的文字。`;
    ui['highlight-legend'].append(note);
  }
  ui["highlight-legend"].hidden = !indices.length;
}

function scrollMessages() {
  if (holdChatScroll) return;
  dom.messages.scrollTop = dom.messages.scrollHeight;
}

function addMessage(role, text, options = {}) {
  document.querySelector("#empty-chat")?.remove();
  const messageId = options.id || crypto.randomUUID();
  const article = document.createElement("article");
  article.className = `message ${role}`;
  article.dataset.messageId = messageId;

  if (role === "user") {
    const body = document.createElement("div");
    body.className = "message-body";
    body.textContent = text;
    article.append(body);
    if (options.replyTo?.answer) {
      const quote = document.createElement('details');
      quote.className = 'quoted-answer';
      const heading = document.createElement('summary');
      heading.textContent = '引用的 AI 回答';
      const content = document.createElement('div');
      content.textContent = options.replyTo.answer;
      quote.append(heading, content);
      article.prepend(quote);
    }
    if (options.images?.length) {
      const images = document.createElement("div");
      images.className = "message-images";
      for (const item of options.images) {
        const link = document.createElement("a");
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener";
        const image = document.createElement("img");
        image.src = item.url;
        image.alt = item.name;
        link.append(image);
        images.append(link);
      }
      article.append(images);
    }
    if (options.imageText) addRecognizedText(article, options.imageText, options.imageMode);
  } else {
    const label = document.createElement("div");
    label.className = "message-label";
    label.innerHTML =
      role === "assistant"
        ? '<svg class="icon"><use href="#i-sparkles"></use></svg><span>智学笔记</span>'
        : "<span>错误</span>";
    const body = document.createElement("div");
    body.className = "message-body";
    if (options.loading) {
      body.innerHTML = '<div class="generation-progress" role="status" aria-live="polite"><div class="loading-dots">正在提交请求</div></div>';
    } else if (role === "assistant") {
      body.innerHTML = renderAnswerHtml(text);
    } else {
      body.textContent = text;
    }
    article.append(label, body);
  }
  dom.messages.append(article);
  scrollMessages();
  return messageId;
}

function updateAssistantMessage(messageId, answer, citations, mode, sourceMode, options = {}) {
  state.messageCitations.set(messageId, citations || []);
  const article = dom.messages.querySelector(`[data-message-id="${messageId}"]`);
  if (!article) return;
  const body = article.querySelector(".message-body");
  body.innerHTML = renderAnswerHtml(answer, citations || []);
  renderMathInElement(body, {
    delimiters: [{ left: '$$', right: '$$', display: true }, { left: '\\[', right: '\\]', display: true },
      { left: '\\(', right: '\\)', display: false }, { left: '$', right: '$', display: false }],
    throwOnError: false, trust: false, maxExpand: 1000, strict: 'ignore',
    ignoredClasses: ['citation-ref', 'unavailable-reference'],
  });
  state.answers.set(messageId, { answer, citations: citations || [], notebook_id: state.notebookId,
    title: article.previousElementSibling?.querySelector(".message-body")?.textContent?.slice(0, 200) || "学习笔记",
    queryTerms: options.question ? extractSearchTerms(options.question) : window.__lastQuestionTerms || [] });
  body.querySelectorAll(".citation-ref").forEach((button) => {
    button.dataset.messageId = messageId;
    colorCitation(button, button.dataset.citation);
  });
  if (mode === "extractive") {
    const note = document.createElement("div");
    note.className = "document-meta";
    note.textContent = "当前为检索原文模式。配置模型后会生成归纳式回答。";
    body.append(note);
  } else if (sourceMode === "general_knowledge" && !citations?.length) {
    const note = document.createElement("div");
    note.className = "document-meta";
    note.textContent = "本次未检索到资料依据，以下内容来自模型的通用知识。";
    body.append(note);
  }
  if (options.coverage) {
    const coverage = options.coverage;
    const note = document.createElement("div");
    note.className = "document-meta summary-coverage";
    const selected = coverage.selected_documents || coverage.documents;
    const citedDocuments = new Set((citations || []).map(citation => citation.document_id || citation.document_name));
    note.textContent = `读取范围：${coverage.read_document_ids?.length ?? coverage.documents.length}/${selected.length} 份资料，${coverage.pages ? `${coverage.pages} 个有文字的页面` : `${coverage.sources} 个片段`}；回答引用 ${citedDocuments.size} 份、${citations?.length || 0} 处。读取范围不代表知识点已全部覆盖。`;
    if (coverage.unread_image_pages?.length) {
      note.textContent += ` 图片未识别：${coverage.unread_image_pages.map((item) => `${item.document_name} 第 ${item.pages.join('、')} 页`).join('；')}。`;
    }
    body.append(note);
    const disclosure = document.createElement('details');
    disclosure.className = 'summary-source-coverage';
    const heading = document.createElement('summary');
    heading.textContent = '逐份来源核对';
    disclosure.append(heading);
    for (const saved of coverage.document_details || selected.map(name => ({ name }))) {
      const references = (citations || []).filter(citation => saved.id ? citation.document_id === saved.id : citation.document_name === saved.name);
      const pages = new Set(references.map(citation => citation.page).filter(page => page != null));
      const row = document.createElement('p');
      const read = saved.read_pages > 0 ? `读取 ${saved.read_pages} 页；` : saved.read_sources > 0 ? `读取 ${saved.read_sources} 个文字片段；` : saved.read_sources === 0 ? '无可用文字；' : '';
      row.textContent = `${saved.name}：${read}${references.length ? `引用 ${pages.size ? `${pages.size} 页、` : ''}${references.length} 处` : '本回答未引用该来源'}。`;
      disclosure.append(row);
    }
    body.append(disclosure);
  }
  if (citations?.length) {
    const referenced = new Set([...body.querySelectorAll('.citation-ref')].map((button) => Number(button.dataset.citation)));
    const sources = citations.filter((citation) => referenced.has(Number(citation.index)));
    const disclosure = document.createElement("details");
    disclosure.className = "citation-disclosure";
    const summary = document.createElement("summary");
    summary.textContent = `引用来源（${sources.length}）`;
    const list = document.createElement("div");
    list.className = "citation-list";
    sources.forEach((citation) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "citation-card";
      button.dataset.messageId = messageId;
      button.dataset.citation = String(citation.index);
      colorCitation(button, citation.index);
      button.innerHTML = `
        <span class="citation-index">${citation.index}</span>
        <span>
          <span class="citation-title">${escapeHtml(citation.document_name)}${citation.page ? ` · 第 ${citation.page} 页` : ""}${citation.image_recognition?.length ? " · 图片识别" : ""}</span>
          <span class="citation-preview">${escapeHtml(citation.text)}</span>
        </span>
      `;
      list.append(button);
    });
    disclosure.append(summary, list);
    if (sources.length) body.append(disclosure);
  }
  const save = document.createElement("button");
  save.type = "button";
  save.className = "button save-answer";
  save.dataset.saveAnswer = messageId;
  save.textContent = "保存为笔记";
  article.append(save);
  const quote = document.createElement('button');
  quote.type = 'button';
  quote.className = 'button ghost quote-answer';
  quote.dataset.quoteAnswer = messageId;
  quote.textContent = '引用追问';
  quote.disabled = state.busy || !article.dataset.exchangeId;
  quote.dataset.exchangeId = article.dataset.exchangeId || '';
  article.append(quote);
  scrollMessages();
}

function setAssistantError(messageId, error) {
  const article = dom.messages.querySelector(`[data-message-id="${messageId}"]`);
  if (!article) return;
  article.classList.add("error");
  const label = article.querySelector(".message-label span:last-child");
  if (label) label.textContent = "请求失败";
  article.querySelector(".message-body").textContent = error.message;
}

function setAssistantCancelled(messageId) {
  setAssistantError(messageId, new Error('已取消生成，可修改问题后重新发送。'));
  const article = dom.messages.querySelector(`[data-message-id="${messageId}"]`);
  if (!article) return;
  article.classList.remove('error');
  article.classList.add('cancelled');
  article.querySelector('.message-label span:last-child').textContent = '已取消';
}

function renderReplyDraft() {
  ui['reply-draft'].hidden = !state.replyTo;
  ui['reply-preview'].textContent = state.replyTo ? `${state.replyTo.question}\n${state.replyTo.answer.slice(0, 220)}` : '';
}

function updateCancelButton() {
  const active = Boolean(state.activeGeneration || state.pendingExchangeId);
  ui['cancel-generation'].hidden = !active;
  ui['cancel-generation'].disabled = state.cancelling;
  ui['cancel-generation'].textContent = state.cancelling ? '正在取消…' : '取消生成';
  dom.sendButton.hidden = active;
}

function renderGenerationProgress(messageId, progress, createdAt) {
  const body = dom.messages.querySelector(`[data-message-id="${messageId}"] .message-body`);
  if (!body?.querySelector('.generation-progress')) return;
  const panel = document.createElement('div');
  panel.className = 'generation-progress';
  panel.setAttribute('role', 'status');
  panel.setAttribute('aria-live', 'polite');
  const activity = progress?.activity;
  const stage = progress?.steps?.at(-1)?.stage;
  const parts = [progress?.detail || '请求已接收，等待开始处理。'];
  if (activity?.query) parts.push(`检索词：${activity.query}`);
  if (stage !== 'sources' && activity?.sources?.length) {
    const names = activity.sources.slice(0, 3).map(source => {
      const pages = source.pages?.length ? `第 ${source.pages.join('、')} 页${source.page_count > source.pages.length ? `等 ${source.page_count} 页` : ''}` : '';
      return `《${source.name}》${pages}`;
    });
    const sources = names.join('、') + (activity.source_count > 3 ? `等 ${activity.source_count} 份资料` : '');
    const action = { evidence: '正在核对', answer: '正在参考', retrieval: '正在检索' }[stage] || '正在读取';
    parts.unshift(`${action}${sources}`);
  }
  const elapsed = Math.max(0, Math.floor((Date.now() - Date.parse(createdAt)) / 1000));
  if (Number.isFinite(elapsed)) parts.push(`已等待 ${elapsed} 秒${elapsed >= 30 ? '，可继续等待或取消' : ''}`);
  const line = document.createElement('span');
  line.className = 'generation-detail';
  line.textContent = parts.join(' · ');
  panel.title = line.textContent;
  panel.append(line);
  body.replaceChildren(panel);
}

async function pollGenerationProgress(job) {
  try {
    const data = await api(`/api/notebooks/${encodeURIComponent(job.notebookId)}/chat/${encodeURIComponent(job.id)}/status`,
      { signal: AbortSignal.timeout(5000) });
    if (state.activeGeneration !== job || job.finished || job.cancelled) return;
    if (data.status === 'pending') renderGenerationProgress(job.messageId, data.progress, data.created_at);
  } catch (_) { /* Submission may not yet exist; transient polling failures do not fail generation. */ }
  if (state.activeGeneration === job && !job.finished && !job.cancelled) {
    job.progressTimer = window.setTimeout(() => pollGenerationProgress(job), 1000);
  }
}

function startGeneration(question, messageId) {
  if (innerWidth <= 760) setMobilePane('chat');
  const job = { id: crypto.randomUUID(), notebookId: state.notebookId, controller: new AbortController(), question, cancelled: false };
  job.messageId = messageId;
  state.activeGeneration = job;
  job.progressTimer = window.setTimeout(() => pollGenerationProgress(job), 300);
  updateCancelButton();
  return job;
}

function finishGeneration(job) {
  job.finished = true;
  window.clearTimeout(job.progressTimer);
  if (state.activeGeneration === job) state.activeGeneration = null;
  setBusy(state.chatPending);
}

ui['remove-reply'].addEventListener('click', () => { if (!state.busy) { state.replyTo = null; renderReplyDraft(); } });
ui['cancel-generation'].addEventListener('click', async () => {
  if (state.cancelling) return;
  const job = state.activeGeneration;
  const restored = state.pendingExchange;
  const id = job?.id || state.pendingExchangeId;
  if (!id) return;
  state.cancelling = true;
  updateCancelButton();
  try {
    const result = await api(`/api/notebooks/${encodeURIComponent(job?.notebookId || state.notebookId)}/chat/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
    if (result.cancelled) {
      if (job) { job.cancelled = true; job.controller.abort(); }
      else if (restored) {
        if (!dom.questionInput.value) dom.questionInput.value = restored.question;
        if (!state.draftImages.length && restored.images?.length) { state.draftImages = restored.images; renderDraftImages(); }
        if (!state.replyTo && restored.reply_to?.answer) { state.replyTo = restored.reply_to; renderReplyDraft(); }
      }
      await loadChat();
      showToast('已取消生成，问题和图片可继续修改后发送。');
    } else showToast('这条回答已经结束，无需取消。');
  } catch (error) { showToast(error.message, true); }
  finally { state.cancelling = false; setBusy(Boolean(state.activeGeneration) || state.chatPending); }
});

function bindExchange(assistantId, userId, exchangeId, pending = false) {
  if (!exchangeId) return;
  const assistant = dom.messages.querySelector(`[data-message-id="${assistantId}"]`);
  const user = dom.messages.querySelector(`[data-message-id="${userId}"]`);
  if (!assistant || !user) return;
  assistant.dataset.exchangeId = user.dataset.exchangeId = exchangeId;
  const quote = assistant.querySelector('.quote-answer');
  if (quote) { quote.dataset.exchangeId = exchangeId; quote.disabled = state.busy || pending; }
  assistant.querySelector(".delete-exchange")?.remove();
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button ghost delete-exchange";
  button.dataset.deleteExchange = exchangeId;
  button.dataset.pending = String(pending);
  button.disabled = state.busy || pending;
  button.textContent = "删除本条对话";
  button.setAttribute("aria-label", "删除这条问题及回答");
  assistant.append(button);
}

function updateUndoButton() {
  ui["undo-chat-delete"].textContent = state.deletedCount ? `撤销删除 (${state.deletedCount})` : "撤销删除";
  ui["undo-chat-delete"].disabled = state.busy || !state.deletedCount;
}

async function deleteExchange(button) {
  const application = state.applications.some(item => item.id === button.dataset.deleteExchange);
  if (state.busy || !window.confirm(application ? '删除这份测验及其答题进度？可在问答栏撤销最近 5 条删除。' : "删除这条问题及回答？可撤销最近 5 条删除；已保存的笔记和资料不受影响。")) return;
  const exchangeId = button.dataset.deleteExchange;
  setBusy(true);
  try {
    const result = await api(`/api/notebooks/${encodeURIComponent(state.notebookId)}/chat/${encodeURIComponent(exchangeId)}`, { method: "DELETE" });
    dom.messages.querySelectorAll(`[data-exchange-id="${exchangeId}"]`).forEach((article) => {
      const id = article.dataset.messageId;
      if (state.highlightMessageId === id) { resetViewer(); state.highlightMessageId = ""; }
      state.messageCitations.delete(id);
      state.answers.delete(id);
      article.remove();
    });
    if (!dom.messages.querySelector(".message")) { dom.messages.innerHTML = emptyChatTemplate; selectionStatus(); }
    state.deletedCount = result.deleted_count;
    state.applications = state.applications.filter(item => item.id !== exchangeId);
    if (state.activeQuiz?.id === exchangeId) closeQuizPanel();
    renderApplications();
    if (state.replyTo?.id === exchangeId) { state.replyTo = null; renderReplyDraft(); }
    updateUndoButton();
    const removed = new Set(result.deleted_image_ids || []);
    if (state.draftImages.some((image) => removed.has(image.id))) {
      state.draftImages = state.draftImages.filter((image) => !removed.has(image.id));
      renderDraftImages();
    }
    showToast("已删除，可点击“撤销删除”恢复。最多保留最近 5 条删除记录。");
  } catch (error) { showToast(error.message, true); }
  finally { setBusy(state.chatPending); }
}

function addRecognizedText(article, text, mode) {
  if (!article) return;
  const details = document.createElement("details");
  details.className = "image-recognition";
  const summary = document.createElement("summary");
  summary.textContent = `识别到的题目（${mode === "vision" ? "视觉模型" : mode === "manual" ? "已校对" : "本机 OCR"}）`;
  const body = document.createElement("div");
  body.textContent = text;
  details.append(summary, body);
  article.append(details);
}

function renderDraftImages() {
  ui["question-image-draft"].hidden = !state.draftImages.length;
  ui["question-image-list"].replaceChildren();
  state.imageTextEdited = false;
  ui["question-image-text"].value = state.draftImages.map((image) => image.ocr_text || "").join("\n\n").slice(0, 20000);
  for (const image of state.draftImages) {
    const item = document.createElement("div");
    item.className = "draft-image";
    const preview = document.createElement("img");
    preview.src = image.url;
    preview.alt = image.name;
    const name = document.createElement("span");
    name.textContent = image.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "draft-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `移除图片 ${image.name}`);
    remove.addEventListener("click", async () => {
      if (state.busy) return;
      if (state.imageTextEdited && !window.confirm("移除图片会重置校对文字，继续？")) return;
      try {
        await api(`/api/question-images/${image.id}`, { method: "DELETE" });
        state.draftImages = state.draftImages.filter((item) => item.id !== image.id);
        renderDraftImages();
      } catch (error) { showToast(error.message, true); }
    });
    item.append(preview, name, remove);
    ui["question-image-list"].append(item);
  }
}

async function attachQuestionImages(files) {
  return uploadFiles(files);
}

ui["attach-question-image"].addEventListener("click", () => ui["question-image-input"].click());
ui["question-image-input"].addEventListener("change", (event) => attachQuestionImages([...event.target.files]));
ui["question-image-text"].addEventListener("input", () => { state.imageTextEdited = true; });
dom.questionInput.addEventListener("paste", (event) => {
  const files = [...(event.clipboardData?.files || [])];
  if (files.length) { event.preventDefault(); uploadFiles(files); }
});
dom.askForm.addEventListener("dragover", (event) => event.preventDefault());
dom.askForm.addEventListener("drop", (event) => {
  event.preventDefault();
  const files = [...(event.dataTransfer?.files || [])];
  if (files.length) uploadFiles(files);
});

async function askQuestion(question) {
  const text = question.trim();
  const images = [...state.draftImages];
  if (!text && !images.length) return;
  if (state.busy) return showToast("请等待当前回答完成。");
  window.__lastQuestionTerms = extractSearchTerms(text);
  const replyTo = state.replyTo;
  const userId = addMessage("user", text || "请解答图片中的题目，并结合选中的课程资料。", { images, replyTo });
  const assistantId = addMessage("assistant", "", { loading: true });
  const job = startGeneration(text, assistantId);
  setBusy(true);
  try {
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: job.controller.signal,
      body: JSON.stringify({ question: text, notebook_id: state.notebookId, document_ids: selectedIds(),
        request_id: job.id, reply_to_id: replyTo?.id || null,
        image_ids: images.map((image) => image.id), image_text: state.imageTextEdited ? ui["question-image-text"].value : null }),
    });
    updateAssistantMessage(
      assistantId,
      data.answer,
      data.citations,
      data.mode,
      data.source_mode,
      { coverage: data.summary_coverage }
    );
    bindExchange(assistantId, userId, data.chat_id);
    if (data.image_question_text) addRecognizedText(dom.messages.querySelector(`[data-message-id="${userId}"]`), data.image_question_text, data.image_recognition_mode);
    state.draftImages = [];
    renderDraftImages();
    state.replyTo = null;
    renderReplyDraft();
  } catch (error) {
    if (job.cancelled || error.cancelled) setAssistantCancelled(assistantId);
    else { setAssistantError(assistantId, error); showToast(error.message, true); }
    bindExchange(assistantId, userId, error.chatId || (job.cancelled ? job.id : null));
    if (!dom.questionInput.value) dom.questionInput.value = text;
  } finally {
    finishGeneration(job);
    dom.questionInput.focus();
  }
}

function extractSearchTerms(text) {
  const terms = new Set();
  for (const match of text.matchAll(/[a-zA-Z0-9_]{2,}/g)) {
    terms.add(match[0].toLowerCase());
  }
  for (const match of text.matchAll(/[\u3400-\u9fff]{2,}/g)) {
    const run = match[0];
    if (run.length <= 4) terms.add(run);
    for (let index = 0; index < run.length - 1; index += 1) {
      terms.add(run.slice(index, index + 2));
    }
  }
  return [...terms];
}

function plainSourceText(citation) {
  const text = String(citation.text || "");
  const terms = citation.queryTerms || [];
  if (!terms.length) return escapeHtml(text);
  const pattern = terms
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  if (!pattern) return escapeHtml(text);
  return escapeHtml(text).replace(new RegExp(`(${pattern})`, "gi"), "<mark>$1</mark>");
}

function showCitation(citation, messageId, citationIndex) {
  closeStudyDrawers();
  updateStudyLayout();
  if (!["pdf", "pptx"].includes(citation.document_kind)) resetViewer();
  state.activeCitation = Number(citationIndex);
  state.highlightMessageId = messageId;
  colorCitation(dom.sourceText, citationIndex);
  dom.sourceText.innerHTML = `
    <strong class="source-citation-label">[${Number(citationIndex)}] ${escapeHtml(citation.document_name)}${citation.page ? ` · 第 ${citation.page} 页` : ""}</strong><br />
    ${plainSourceText(citation)}
  `;
  if (["pdf", "pptx"].includes(citation.document_kind)) {
    setMobilePane("viewer");
    const terms = (citation.queryTerms || []).filter((term) => term.length >= 2);
    const relevantLines = (ref) => {
      const matched = (ref.lines || []).filter((line) => {
      const lineText = String(line.text || "").toLowerCase();
      return terms.some((term) => lineText.includes(String(term).toLowerCase()));
      });
      return matched.length ? matched : ref.lines || [];
    };
    const matchingLines = relevantLines(citation);
    const targetPage = matchingLines[0]?.page || citation.page || 1;
    const references = state.messageCitations.get(messageId) || [citation];
    const highlightLines = references.filter((ref) => ref.document_id === citation.document_id)
      .flatMap((ref) => relevantLines(ref).map((line) => ({ ...line, citationIndex: ref.index })));
    openPdf(
      citation.document_id,
      targetPage,
      highlightLines,
      references.filter(ref => ref.document_id === citation.document_id)
    );
  } else {
    document.getElementById('viewer-document').classList.add('is-text');
    ui["highlight-legend"].hidden = true;
    setMobilePane("viewer");
    dom.viewerEmpty.hidden = false;
    dom.viewerScroll.hidden = true;
    dom.viewerName.textContent = citation.document_name;
    state.viewerDocumentId = citation.document_id;
  }
  const article = dom.messages.querySelector(`[data-message-id="${messageId}"]`);
  article?.querySelectorAll(".citation-card").forEach((item) => {
    item.setAttribute("aria-pressed", String(item.dataset.citation === String(citationIndex)));
  });
}

function setMobilePane(pane) {
  document.querySelectorAll(".mobile-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.pane === pane);
    tab.setAttribute('aria-pressed', String(tab.dataset.pane === pane));
  });
  dom.workspace.classList.toggle("show-viewer", pane === "viewer");
  document.querySelector('.app-shell').dataset.mobilePane = pane;
  dom.workspace.dispatchEvent(new Event('panelschange'));
}

async function loadSettings() {
  state.settings = await api("/api/settings");
  const configured = Boolean(state.settings.model_configured);
  dom.modelPill.textContent = configured
    ? `模型：${state.settings.model}`
    : "未配置模型，当前为检索原文模式";
  dom.modelPill.classList.toggle("configured", configured);
  document.querySelector("#base-url").value = state.settings.base_url || "";
  document.querySelector("#api-key").value = "";
  document.querySelector("#api-key").placeholder = state.settings.api_key_set
    ? "已保存，留空则保留"
    : "sk-...";
  document.querySelector("#model-name").value = state.settings.model || "";
  document.querySelector("#temperature").value = state.settings.temperature ?? 0.2;
  document.querySelector("#max-tokens").value = state.settings.max_tokens ?? 1400;
  document.querySelector("#top-k").value = state.settings.top_k ?? 6;
  document.querySelector("#vision-model").value = state.settings.vision_model || "";
  document.querySelector("#vision-base-url").value = state.settings.vision_base_url || "";
  document.querySelector("#vision-api-key").value = "";
  document.querySelector("#vision-api-key").placeholder = state.settings.vision_api_key_set ? "已保存，留空保留" : "独立接口使用对应密钥";
}

async function saveSettings(event) {
  event.preventDefault();
  const payload = {
    base_url: document.querySelector("#base-url").value.trim(),
    api_key: document.querySelector("#api-key").value.trim(),
    model: document.querySelector("#model-name").value.trim(),
    temperature: Number(document.querySelector("#temperature").value),
    max_tokens: Number(document.querySelector("#max-tokens").value),
    top_k: Number(document.querySelector("#top-k").value),
    vision_model: document.querySelector("#vision-model").value.trim(),
    vision_base_url: document.querySelector("#vision-base-url").value.trim(),
    vision_api_key: document.querySelector("#vision-api-key").value.trim(),
  };
  try {
    state.settings = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    dom.settingsDialog.close();
    await loadSettings();
    showToast("模型设置已保存。");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function generateSummary(configuration = {}) {
  if (state.busy) return;
  const sources = configuration.document_ids || selectedIds();
  if (!sources.length) {
    showToast("请先上传至少一份资料。", true);
    return;
  }
  setBusy(true);
  const userId = addMessage("user", "根据当前选中的来源生成一份复习提纲。");
  const assistantId = addMessage("assistant", "", { loading: true });
  const job = startGeneration('根据当前选中的来源生成一份复习提纲。', assistantId);
  updateCancelButton();
  try {
    const data = await api("/api/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: job.controller.signal,
      body: JSON.stringify({ notebook_id: state.notebookId, document_ids: sources, request_id: job.id, focus: configuration.topic || '' }),
    });
    updateAssistantMessage(assistantId, data.answer, data.citations, data.mode, data.source_mode, { coverage: data.summary_coverage });
    bindExchange(assistantId, userId, data.chat_id);
  } catch (error) {
    if (job.cancelled || error.cancelled) {
      setAssistantCancelled(assistantId);
      if (!dom.questionInput.value) dom.questionInput.value = job.question;
    } else { setAssistantError(assistantId, error); showToast(error.message, true); }
    bindExchange(assistantId, userId, error.chatId || (job.cancelled ? job.id : null));
  } finally {
    finishGeneration(job);
  }
}

function renderApplications() {
  ui['application-count'].textContent = String(state.applications.length);
  ui['application-list'].replaceChildren();
  if (!state.applications.length) {
    const empty = document.createElement('p'); empty.className = 'application-empty';
    empty.textContent = '还没有测验。点击下方“功能选择”生成一份，完成后会保存在这里。';
    ui['application-list'].append(empty);
  }
  for (const exchange of [...state.applications].reverse()) {
    const quiz = exchange.result.quiz;
    const card = document.createElement('article'); card.className = 'application-card';
    const link = document.createElement('a'); link.className = 'application-open';
    link.href = `/quiz/${encodeURIComponent(exchange.id)}?notebook_id=${encodeURIComponent(state.notebookId)}`;
    link.addEventListener('click', event => {
      if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); openQuizPanel(exchange);
    });
    const label = document.createElement('span'); label.className = 'application-kind'; label.textContent = '交互式测验';
    const title = document.createElement('strong'); title.textContent = quiz.topic || exchange.question || '课程测验';
    const count = quiz.questions.length, answered = Object.keys(quiz.answers).length;
    const score = quiz.questions.filter(q => quiz.answers[q.id] === q.answer).length;
    const status = document.createElement('span'); status.className = 'application-status';
    status.textContent = `${count} 题 · 已答 ${answered} 题${answered ? ` · 答对 ${score} 题` : ''}`;
    const action = document.createElement('span'); action.className = 'application-action';
    action.textContent = answered === count ? '回顾解析 →' : answered ? '继续作答 →' : '开始作答 →';
    link.append(label, title, status, action);
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'button ghost delete-application';
    remove.textContent = '删除'; remove.setAttribute('aria-label', `删除测验：${title.textContent}`);
    remove.dataset.deleteExchange = exchange.id; remove.disabled = state.busy;
    remove.addEventListener('click', () => deleteExchange(remove));
    card.append(link, remove); ui['application-list'].append(card);
  }
}

function updateStudyLayout() {
  const quiz = Boolean(state.activeQuiz);
  document.querySelector('.app-shell').classList.toggle('study-focus', quiz);
  document.getElementById('study-rail').hidden = !quiz;
  if (!quiz) closeStudyDrawers();
  document.querySelector('.viewer-pane').classList.toggle('study-open', quiz);
  document.getElementById('viewer-document').hidden = false;
  document.getElementById('quiz-workspace').hidden = !quiz;
  document.getElementById('study-resizer').hidden = !quiz;
  document.getElementById('right-pane-tabs').hidden = !state.activeQuiz;
  document.querySelector('.mobile-tab[data-pane="viewer"]').textContent = quiz ? '原文与测验' : '原文';
  const key = `zhixue-active-quiz:${state.notebookId}`;
  if (quiz) localStorage.setItem(key, state.activeQuiz.id);
  else localStorage.removeItem(key);
  dom.workspace.dispatchEvent(new Event('panelschange'));
}

function closeQuizPanel(forget = true) {
  if (!forget) {
    // Switching notebooks must not clear the previous notebook's saved panel.
    state.activeQuiz = null; state.quizGeneration++;
    document.getElementById('quiz-panel-body').replaceChildren();
    document.getElementById('right-pane-tabs').hidden = true;
    document.getElementById('quiz-workspace').hidden = true;
    document.getElementById('viewer-document').hidden = false;
    document.querySelector('.app-shell').classList.remove('study-focus');
    document.getElementById('study-rail').hidden = true;
    closeStudyDrawers();
    document.getElementById('study-resizer').hidden = true;
    document.querySelector('.viewer-pane').classList.remove('study-open');
    dom.workspace.dispatchEvent(new Event('panelschange'));
    document.querySelector('.mobile-tab[data-pane="viewer"]').textContent = '原文';
    return;
  }
  state.activeQuiz = null; state.quizGeneration++;
  document.getElementById('quiz-panel-body').replaceChildren();
  updateStudyLayout();
}

async function openQuizPanel(exchange, restore = false) {
  closeStudyDrawers();
  if (state.activeQuiz?.id === exchange.id && !restore) {
    updateStudyLayout(); setMobilePane('viewer'); return;
  }
  const notebook = state.notebookId, token = ++state.quizGeneration;
  state.activeQuiz = { id: exchange.id, notebook };
  const content = document.createElement('div'); content.className = 'embedded-quiz-content'; content.textContent = '正在读取测验…';
  document.getElementById('quiz-panel-body').replaceChildren(content);
  document.getElementById('quiz-workspace-title').textContent = exchange.result.quiz.topic || exchange.question || '课程测验';
  updateStudyLayout();
  setMobilePane('viewer');
  const endpoint = `/api/notebooks/${encodeURIComponent(notebook)}/quiz/${encodeURIComponent(exchange.id)}`;
  const isCurrent = () => token === state.quizGeneration && notebook === state.notebookId;
  try {
    let result = await api(endpoint);
    if (!isCurrent()) return;
    const citationKey = `quiz-${exchange.id}`;
    state.messageCitations.set(citationKey, result.citations);
    const update = async (suffix, body) => {
      const updated = await api(endpoint + suffix, jsonOptions('POST', body));
      result = updated;
      if (notebook === state.notebookId) {
        const saved = state.applications.find(item => item.id === exchange.id);
        if (saved) saved.result = updated;
        renderApplications();
      }
      return updated;
    };
    renderQuiz(content, result, { colorCitation,
      onAnswer: (question_id, option) => update('/answer', { question_id, option }),
      onReset: () => update('/reset', {}),
      onCitation: index => {
        if (!isCurrent()) return;
        const source = result.citations.find(c => c.index === index);
        if (!source) return;
        state.messageCitations.set(citationKey, result.citations);
        showCitation(source, citationKey, index);
      },
    });
    if (!state.viewerDocumentId && result.citations.length) showCitation(result.citations[0], citationKey, result.citations[0].index);
  } catch (error) {
    if (!isCurrent()) return;
    content.textContent = error.message; content.classList.add('feature-error');
    // Allow another click on the application to retry a temporary load failure.
    state.activeQuiz = null;
    localStorage.removeItem(`zhixue-active-quiz:${notebook}`);
  }
}
document.getElementById('close-quiz-panel').addEventListener('click', () => { closeQuizPanel(); if (innerWidth <= 1080) setMobilePane('chat'); });

function closeStudyDrawers() {
  const shell = document.querySelector('.app-shell');
  shell.classList.remove('show-study-library', 'show-study-chat');
  document.getElementById('study-drawer-backdrop').hidden = true;
  for (const id of ['study-show-library', 'study-show-chat', 'study-show-apps']) document.getElementById(id).setAttribute('aria-expanded', 'false');
}
function toggleStudyDrawer(kind, trigger) {
  const shell = document.querySelector('.app-shell');
  const open = !shell.classList.contains(`show-study-${kind}`);
  closeStudyDrawers();
  if (!open) return;
  shell.classList.add(`show-study-${kind}`);
  document.getElementById('study-drawer-backdrop').hidden = false;
  document.getElementById(trigger || (kind === 'chat' ? 'study-show-chat' : 'study-show-library')).setAttribute('aria-expanded', 'true');
}
document.getElementById('study-show-library').addEventListener('click', () => { setLibraryTab('sources'); toggleStudyDrawer('library'); });
document.getElementById('study-show-chat').addEventListener('click', () => toggleStudyDrawer('chat'));
document.getElementById('study-show-apps').addEventListener('click', () => { setLibraryTab('applications'); toggleStudyDrawer('library', 'study-show-apps'); });
for (const id of ['study-close-library', 'study-close-chat', 'study-drawer-backdrop']) document.getElementById(id).addEventListener('click', closeStudyDrawers);
document.getElementById('study-exit').addEventListener('click', () => { closeQuizPanel(); if (innerWidth <= 1080) setMobilePane('chat'); });
document.addEventListener('keydown', event => { if (event.key === 'Escape' && !document.querySelector('dialog[open]')) closeStudyDrawers(); });

async function generateQuiz(configuration) {
  if (state.busy) return;
  const question = `根据勾选来源生成 ${ {less:5, standard:10, more:15}[configuration.count] } 道交互式测验。${configuration.topic ? `主题：${configuration.topic}` : ''}`;
  const userId = addMessage('user', question);
  const assistantId = addMessage('assistant', '', { loading: true });
  const job = startGeneration(question, assistantId);
  setBusy(true);
  try {
    const result = await api('/api/quiz', { ...jsonOptions('POST', { ...configuration, notebook_id: state.notebookId, request_id: job.id }), signal: job.controller.signal });
    state.applications.push({ id: result.chat_id, question, result });
    renderApplications();
    dom.messages.querySelector(`[data-message-id="${assistantId}"]`)?.remove();
    dom.messages.querySelector(`[data-message-id="${userId}"]`)?.remove();
    if (!dom.messages.querySelector('.message')) { dom.messages.innerHTML = emptyChatTemplate; selectionStatus(); }
    setLibraryTab('applications');
    if (innerWidth <= 760) setMobilePane('sources');
    showToast('测验已保存到“应用”，点击即可进入独立做题页面。');
  } catch (error) {
    if (job.cancelled || error.cancelled) setAssistantCancelled(assistantId);
    else { setAssistantError(assistantId, error); showToast(error.message, true); }
    bindExchange(assistantId, userId, error.chatId || (job.cancelled ? job.id : null));
  } finally { finishGeneration(job); }
}

const featuresDialog = document.getElementById('features-dialog');
let featureKind = 'summary';
const featureTopic = document.getElementById('feature-topic');
function saveFeatureDraft() {
  localStorage.setItem(`zhixue-features:${state.notebookId}`, JSON.stringify({ kind: featureKind, topic: featureTopic.value,
    document_ids: [...document.querySelectorAll('#feature-source-list input:checked')].map(input => input.value),
    count: document.getElementById('quiz-count').value, difficulty: document.getElementById('quiz-difficulty').value,
    language: document.getElementById('quiz-language').value }));
}
document.getElementById('features-form').addEventListener('input', saveFeatureDraft);
document.getElementById('features-form').addEventListener('change', saveFeatureDraft);
function chooseFeature(kind) {
  featureKind = kind;
  document.querySelectorAll('[data-feature]').forEach(button => {
    button.classList.toggle('active', button.dataset.feature === kind);
    button.setAttribute('aria-pressed', String(button.dataset.feature === kind));
  });
  document.getElementById('quiz-settings').hidden = kind !== 'quiz';
  document.getElementById('feature-topic-label').textContent = kind === 'quiz' ? '主题应该是什么？（选填）' : '复习重点（选填）';
  featureTopic.maxLength = kind === 'quiz' ? 1000 : 300;
  featureTopic.placeholder = kind === 'quiz' ? '例如：第二章，并联谐振回路的公式及适用条件' : '例如：整理第二章的主要概念和公式';
  document.getElementById('feature-generate').textContent = kind === 'quiz' ? '生成测验' : '生成提纲';
}
document.querySelectorAll('[data-feature]').forEach(button => button.addEventListener('click', () => { chooseFeature(button.dataset.feature); saveFeatureDraft(); }));
document.getElementById('close-features').addEventListener('click', () => featuresDialog.close());
function openFeatures() {
  if (state.busy) return;
  let draft = {};
  try { draft = JSON.parse(localStorage.getItem(`zhixue-features:${state.notebookId}`)) || {}; } catch { /* Ignore invalid local drafts. */ }
  chooseFeature(draft.kind === 'quiz' ? 'quiz' : 'summary');
  featureTopic.value = typeof draft.topic === 'string' ? draft.topic.slice(0, featureTopic.maxLength) : '';
  for (const [id, value] of [['quiz-count', draft.count], ['quiz-difficulty', draft.difficulty], ['quiz-language', draft.language]]) {
    const select = document.getElementById(id);
    if ([...select.options].some(option => option.value === value)) select.value = value;
  }
  const list = document.getElementById('feature-source-list'); list.replaceChildren();
  for (const documentSource of state.documents) {
    const label = document.createElement('label'); label.className = 'feature-source-row';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = documentSource.id;
    checkbox.checked = Array.isArray(draft.document_ids) ? draft.document_ids.includes(documentSource.id) : state.selectedSources.has(documentSource.id);
    const name = document.createElement('span'); name.textContent = documentSource.name;
    label.append(checkbox, name); list.append(label);
  }
  const updateCount = () => {
    const count = list.querySelectorAll('input:checked').length;
    document.getElementById('feature-source-count').textContent = `来源 · 已选择 ${count} / ${state.documents.length} 个`;
  };
  list.onchange = updateCount; updateCount();
  if (!state.documents.length) list.textContent = '请先在左侧上传课程资料。';
  document.getElementById('feature-error').hidden = true;
  featuresDialog.showModal();
}
document.getElementById('features-form').addEventListener('submit', event => {
  event.preventDefault();
  const document_ids = [...document.querySelectorAll('#feature-source-list input:checked')].map(input => input.value);
  const error = document.getElementById('feature-error');
  if (!document_ids.length) { error.textContent = '请至少勾选一个来源。'; error.hidden = false; return; }
  if (featureKind === 'quiz' && !state.settings?.model_configured) { error.textContent = '请先在模型设置中配置模型。'; error.hidden = false; return; }
  if (featureTopic.value.length > featureTopic.maxLength) { error.textContent = `主题最多 ${featureTopic.maxLength} 字，请缩短后生成。`; error.hidden = false; return; }
  const configuration = { document_ids, topic: featureTopic.value.trim(), count: document.getElementById('quiz-count').value,
    difficulty: document.getElementById('quiz-difficulty').value, language: document.getElementById('quiz-language').value };
  saveFeatureDraft();
  featuresDialog.close();
  if (featureKind === 'quiz') generateQuiz(configuration);
  else generateSummary(configuration);
});

dom.uploadButton.addEventListener("click", () => dom.fileInput.click());
dom.dropzone.addEventListener("click", () => dom.fileInput.click());
dom.fileInput.addEventListener("change", (event) => uploadFiles(event.target.files));

for (const eventName of ["dragenter", "dragover"]) {
  dom.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dom.dropzone.classList.add("dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dom.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dom.dropzone.classList.remove("dragging");
  });
}
dom.dropzone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));

dom.askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (state.busy) return showToast("请等待当前回答完成。");
  const question = dom.questionInput.value;
  dom.questionInput.value = "";
  askQuestion(question);
});

let composingQuestion = false;
dom.questionInput.addEventListener("compositionstart", () => { composingQuestion = true; });
dom.questionInput.addEventListener("compositionend", () => { composingQuestion = false; });
dom.questionInput.addEventListener("keydown", (event) => {
  // Safari can report isComposing=false for the Enter that confirms an IME candidate.
  if (composingQuestion || event.isComposing || event.keyCode === 229) return;
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    if (!event.repeat) dom.askForm.requestSubmit();
  }
});

dom.messages.addEventListener("click", (event) => {
  const quote = event.target.closest('[data-quote-answer]');
  if (quote) {
    if (state.busy) return;
    const answer = state.answers.get(quote.dataset.quoteAnswer);
    if (!answer || !quote.dataset.exchangeId) return;
    state.replyTo = { id: quote.dataset.exchangeId, question: answer.title, answer: answer.answer };
    renderReplyDraft();
    dom.questionInput.focus();
    return;
  }
  const remove = event.target.closest("[data-delete-exchange]");
  if (remove) { deleteExchange(remove); return; }
  const save = event.target.closest("[data-save-answer]");
  if (save) {
    saveAnswer(save).catch((error) => showToast(error.message, true));
    return;
  }
  const button = event.target.closest("[data-citation]");
  if (!button) return;
  const messageId = button.dataset.messageId;
  const citations = state.messageCitations.get(messageId) || [];
  const citation = citations.find((item) => Number(item.index) === Number(button.dataset.citation));
  if (!citation) return;
  citation.queryTerms = state.answers.get(messageId)?.queryTerms || [];
  showCitation(citation, messageId, button.dataset.citation);
});

dom.messages.addEventListener("click", (event) => {
  const button = event.target.closest(".prompt-examples button");
  if (button) {
    dom.questionInput.value = button.textContent.trim();
    dom.questionInput.focus();
  }
});

dom.settingsButton.addEventListener("click", async () => {
  await loadSettings();
  dom.settingsDialog.showModal();
});
dom.closeSettings.addEventListener("click", () => dom.settingsDialog.close());
dom.cancelSettings.addEventListener("click", () => dom.settingsDialog.close());
dom.settingsForm.addEventListener("submit", saveSettings);
dom.summaryButton.addEventListener("click", openFeatures);

dom.prevPage.addEventListener("click", () => renderPdfPage(state.pdfPage - 1));
dom.nextPage.addEventListener("click", () => renderPdfPage(state.pdfPage + 1));
dom.pageInput.addEventListener("change", () => renderPdfPage(dom.pageInput.value));
dom.openFile.addEventListener("click", () => {
  if (!state.viewerDocumentId) return;
  window.open(
    `/api/documents/${state.viewerDocumentId}/file#page=${state.pdfPage}`,
    "_blank",
    "noopener"
  );
});

document.querySelectorAll(".mobile-tab").forEach((tab) => {
  tab.addEventListener("click", () => setMobilePane(tab.dataset.pane));
});

window.addEventListener("resize", () => {
  if (innerWidth > 760 && document.querySelector('.app-shell').dataset.mobilePane === 'sources') setMobilePane('chat');
  if (state.pdfDocument && document.getElementById('viewer-document').clientWidth > 0) renderPdfPage(state.pdfPage);
});

function setLibraryTab(tab) {
  if (!['sources', 'notes', 'applications'].includes(tab)) tab = 'sources';
  for (const name of ['sources', 'notes', 'applications']) ui[`${name}-content`].hidden = name !== tab;
  localStorage.setItem(`zhixue-library-tab:${state.notebookId}`, tab);
  for (const id of ["sources-tab", "notes-tab", "applications-tab"]) {
    const active = id === `${tab}-tab`;
    ui[id].classList.toggle("active", active);
    ui[id].setAttribute("aria-selected", String(active));
  }
}

function scheduleChatPoll(notebookId, delay = 1500) {
  window.clearTimeout(chatPollTimer);
  chatPollTimer = window.setTimeout(() => {
    if (notebookId !== state.notebookId || !state.chatPending) return;
    loadChat(true).catch((error) => {
      if (notebookId !== state.notebookId || !state.chatPending) return;
      if (error.status === 404) {
        state.chatPending = false;
        state.pendingExchangeId = null;
        state.pendingExchange = null;
        setBusy(false);
        showToast('当前笔记本已不存在，请刷新或切换笔记本。', true);
        return;
      }
      chatPollFailures++;
      if (chatPollFailures === 1) showToast('状态连接暂时中断，正在自动重试；当前任务仍可能在生成。', true);
      setBusy(true);
      scheduleChatPoll(notebookId, Math.min(10000, 1500 * 2 ** Math.min(chatPollFailures, 3)));
    });
  }, delay);
}

async function loadChat(polling = false) {
  window.clearTimeout(chatPollTimer);
  const notebookId = state.notebookId;
  const { exchanges: allExchanges, deleted_count } = await api(`/api/notebooks/${encodeURIComponent(notebookId)}/chat`);
  if (notebookId !== state.notebookId) return;
  state.applications = allExchanges.filter(exchange => exchange.status === 'complete' && exchange.result.quiz);
  renderApplications();
  const remembered = localStorage.getItem(`zhixue-active-quiz:${notebookId}`);
  if (state.activeQuiz && !state.applications.some(item => item.id === state.activeQuiz.id)) closeQuizPanel();
  if (!state.activeQuiz && remembered) {
    const saved = state.applications.find(item => item.id === remembered);
    if (saved) openQuizPanel(saved, true);
    else localStorage.removeItem(`zhixue-active-quiz:${notebookId}`);
  }
  const exchanges = allExchanges.filter(exchange => !(exchange.status === 'complete' && exchange.result.quiz));
  chatPollFailures = 0;
  state.deletedCount = deleted_count || 0;
  updateUndoButton();
  const scrollTop = dom.messages.scrollTop;
  const nearBottom = dom.messages.scrollHeight - scrollTop - dom.messages.clientHeight < 80;
  holdChatScroll = polling && !nearBottom;
  if (!polling) {
    dom.messages.innerHTML = emptyChatTemplate;
    state.messageCitations.clear();
    state.answers.clear();
  } else {
    const activeIds = new Set(exchanges.map(exchange => exchange.id));
    for (const article of dom.messages.querySelectorAll('article[data-exchange-id]')) {
      if (!activeIds.has(article.dataset.exchangeId)) {
        state.messageCitations.delete(article.dataset.messageId);
        state.answers.delete(article.dataset.messageId);
        article.remove();
      }
    }
  }
  for (const exchange of exchanges) {
    let article = dom.messages.querySelector(`[data-message-id="${exchange.id}"]`);
    const signature = JSON.stringify(exchange);
    if (article?.dataset.exchangeStatus === exchange.status && exchange.status !== 'pending' && article.dataset.exchangeSignature === signature) continue;
    if (!article) {
      addMessage("user", exchange.question, { id: `user-${exchange.id}`, images: exchange.images,
        imageText: exchange.result.image_question_text, imageMode: exchange.result.image_recognition_mode, replyTo: exchange.reply_to });
      addMessage("assistant", "", { id: exchange.id, loading: true });
      article = dom.messages.querySelector(`[data-message-id="${exchange.id}"]`);
    }
    const id = exchange.id;
    if (exchange.status === "complete") {
      const result = exchange.result;
      updateAssistantMessage(id, result.answer, result.citations, result.mode, result.source_mode, { question: exchange.question, coverage: result.summary_coverage });
      const user = dom.messages.querySelector(`[data-message-id="user-${exchange.id}"]`);
      if (result.image_question_text && !user.querySelector('.image-recognition')) addRecognizedText(user, result.image_question_text, result.image_recognition_mode);
    } else if (exchange.status === "error") {
      if (exchange.result.cancelled) setAssistantCancelled(id);
      else setAssistantError(id, new Error(exchange.result.detail || "上次请求未完成，请重新提问。"));
    } else {
      renderGenerationProgress(id, exchange.result.progress, exchange.created_at);
    }
    bindExchange(id, `user-${exchange.id}`, exchange.id, exchange.status === "pending");
    article.dataset.exchangeStatus = exchange.status;
    article.dataset.exchangeSignature = signature;
  }
  holdChatScroll = false;
  if (polling && !nearBottom) dom.messages.scrollTop = scrollTop;
  else if (polling) scrollMessages();
  selectionStatus();
  state.chatPending = exchanges.some((exchange) => exchange.status === "pending");
  state.pendingExchange = exchanges.findLast((exchange) => exchange.status === 'pending') || null;
  state.pendingExchangeId = state.pendingExchange?.id || null;
  updateCancelButton();
  if (state.chatPending) {
    setBusy(true);
    scheduleChatPoll(notebookId);
  } else if (polling) setBusy(false);
}

ui["clear-chat"].addEventListener("click", async () => {
  if (state.busy || !window.confirm("永久清空当前笔记本的对话及删除记录？应用中的测验、已保存笔记和资料会保留。此操作无法撤销。")) return;
  setBusy(true);
  try {
    const result = await api(`/api/notebooks/${encodeURIComponent(state.notebookId)}/chat`, { method: "DELETE" });
    state.replyTo = null;
    renderReplyDraft();
    const removed = new Set(result.deleted_image_ids || []);
    if (state.draftImages.some((image) => removed.has(image.id))) {
      state.draftImages = state.draftImages.filter((image) => !removed.has(image.id));
      renderDraftImages();
    }
    await loadChat();
    showToast("当前笔记本的对话已清空。");
  } catch (error) { showToast(error.message, true); }
  finally { setBusy(state.chatPending); }
});

ui["undo-chat-delete"].addEventListener("click", async () => {
  if (state.busy || !state.deletedCount) return;
  setBusy(true);
  try {
    const result = await api(`/api/notebooks/${encodeURIComponent(state.notebookId)}/chat/undo`, { method: "POST" });
    await loadChat();
    dom.messages.querySelector(`[data-message-id="${result.chat_id}"]`)?.scrollIntoView({ block: "nearest" });
    showToast("已恢复最近删除的内容。");
  } catch (error) { showToast(error.message, true); }
  finally { setBusy(state.chatPending); }
});

async function loadNotes() {
  const id = state.notebookId;
  const { notes } = await api(`/api/notes?notebook_id=${encodeURIComponent(id)}`);
  if (state.notebookId !== id) return;
  state.notes = notes;
  ui["note-count"].textContent = String(notes.length);
  ui["note-list"].replaceChildren();
  if (!notes.length) {
    const empty = document.createElement("p");
    empty.className = "document-empty";
    empty.textContent = "还没有笔记。回答下方点“保存为笔记”，或在这里新建。笔记会保留在这个笔记本中。";
    ui["note-list"].append(empty);
  }
  for (const note of notes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "note-item";
    const title = document.createElement("strong");
    title.textContent = note.title;
    const preview = document.createElement("span");
    preview.textContent = note.content.slice(0, 95);
    const date = document.createElement("small");
    date.textContent = `${new Date(note.updated_at).toLocaleString("zh-CN")} · ${note.citations.length} 个引用`;
    button.append(title, preview, date);
    button.addEventListener("click", () => openNote(note));
    ui["note-list"].append(button);
  }
}

async function saveAnswer(button) {
  const answer = state.answers.get(button.dataset.saveAnswer);
  if (!answer) return;
  button.disabled = true;
  try {
    await api("/api/notes", jsonOptions("POST", {
      notebook_id: answer.notebook_id, title: answer.title, content: answer.answer,
      citations: answer.citations,
    }));
    button.textContent = "已保存到笔记";
    await Promise.all([loadNotes(), loadNotebooks()]);
    showToast("回答与引用已保存到当前笔记本。");
  } catch (error) { button.disabled = false; throw error; }
}

function openNote(note = null) {
  currentNote = note;
  ui["note-title"].value = note?.title || "";
  ui["note-content"].value = note?.content || "";
  ui["delete-note"].hidden = !note;
  ui["note-sources"].replaceChildren();
  if (note?.citations?.length) {
    const heading = document.createElement("strong");
    heading.textContent = "保存时的参考来源";
    ui["note-sources"].append(heading);
    for (const citation of note.citations) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "note-source";
      button.textContent = `[${citation.index}] ${citation.document_name}${citation.page ? ` · 第 ${citation.page} 页` : ""}`;
      button.addEventListener("click", async () => {
        if (!state.documents.some((doc) => doc.id === citation.document_id)) {
          showToast("来源已移出当前笔记本或被删除，笔记中仍保留引用原文。", true);
          return;
        }
        try {
          const { chunks } = await api(`/api/documents/${citation.document_id}/chunks`);
          const chunk = chunks.find((item) => item.id === citation.chunk_id);
          if (!closeNote()) return;
          const pageLines = matchingSourceLines(citation, chunks);
          showCitation({ ...citation, lines: pageLines.length ? pageLines : chunk?.lines || [] }, "", citation.index);
        } catch (error) { showToast(error.message, true); }
      });
      ui["note-sources"].append(button);
    }
  }
  ui["note-dialog"].showModal();
}

async function persistNote() {
  if (noteSaving) return null;
  if (!ui["note-form"].reportValidity()) return null;
  const title = ui["note-title"].value.trim();
  const content = ui["note-content"].value.trim();
  if (!title || !content) throw new Error("标题和内容不能为空。");
  noteSaving = true;
  ui["save-note"].disabled = true;
  ui["export-note"].disabled = true;
  ui["close-note"].disabled = true;
  ui["delete-note"].disabled = true;
  ui["note-title"].disabled = true;
  ui["note-content"].disabled = true;
  try {
    const result = currentNote
      ? await api(`/api/notes/${currentNote.id}`, jsonOptions("PATCH", { title, content }))
      : await api("/api/notes", jsonOptions("POST", { title, content, notebook_id: state.notebookId }));
    currentNote = result.note;
    ui["note-title"].value = result.note.title;
    ui["note-content"].value = result.note.content;
    ui["delete-note"].hidden = false;
    await Promise.all([loadNotes(), loadNotebooks()]);
    return result.note;
  } finally {
    noteSaving = false;
    ui["save-note"].disabled = false;
    ui["export-note"].disabled = false;
    ui["close-note"].disabled = false;
    ui["delete-note"].disabled = false;
    ui["note-title"].disabled = false;
    ui["note-content"].disabled = false;
  }
}

function download(url) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

async function searchSources() {
  const notebookId = state.notebookId;
  const query = ui["source-search"].value.trim();
  const generation = ++state.searchGeneration;
  if (!query) {
    ui["search-results"].hidden = true;
    dom.documentList.hidden = false;
    return;
  }
  ui["search-results"].hidden = false;
  dom.documentList.hidden = true;
  ui["search-results"].textContent = "正在查找来源…";
  try {
    const data = await api(`/api/sources/search?notebook_id=${encodeURIComponent(notebookId)}&q=${encodeURIComponent(query)}`);
    if (generation !== state.searchGeneration || notebookId !== state.notebookId) return;
    ui["search-results"].replaceChildren();
    const heading = document.createElement("p");
    heading.className = "search-heading";
    heading.textContent = `${data.documents.length} 个文件名匹配，${data.results.length} 处相关原文`;
    ui["search-results"].append(heading);
    for (const doc of data.documents) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "source-result";
      button.textContent = `文件：${doc.name}`;
      button.addEventListener("click", () => openDocument(doc).catch((error) => showToast(error.message, true)));
      ui["search-results"].append(button);
    }
    for (const citation of data.results) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "source-result";
      const title = document.createElement("strong");
      title.textContent = citation.document_name + (citation.page ? ` · 第 ${citation.page} 页` : "");
      const preview = document.createElement("span");
      preview.textContent = citation.snippet || citation.text.slice(0, 190);
      button.append(title, preview);
      button.addEventListener("click", () => showCitation({ ...citation, queryTerms: extractSearchTerms(query) }, "", citation.index));
      ui["search-results"].append(button);
    }
    if (!data.documents.length && !data.results.length) heading.textContent = "当前笔记本没有匹配内容。可换用教材中的关键词。";
  } catch (error) {
    if (generation === state.searchGeneration) ui["search-results"].textContent = error.message;
  }
}

let movingDocument = null;
async function moveDocument(doc) {
  if (state.busy) return showToast("请等待当前操作完成。");
  const targets = state.notebooks.filter((notebook) => notebook.id !== state.notebookId);
  if (!targets.length) return showToast("先点击笔记本旁的 ＋ 新建另一个笔记本。");
  movingDocument = doc;
  document.getElementById("move-document-name").textContent = doc.name;
  const select = document.getElementById("move-target");
  select.replaceChildren(...targets.map((notebook) => new Option(notebook.name, notebook.id)));
  document.getElementById("move-dialog").showModal();
}

ui["notebook-select"].addEventListener("change", (event) => switchNotebook(event.target.value).catch((error) => showToast(error.message, true)));
ui["new-notebook"].addEventListener("click", async () => {
  const name = window.prompt("新建笔记本，例如：计算机网络、信号与系统");
  if (!name?.trim()) return;
  try {
    const data = await api("/api/notebooks", jsonOptions("POST", { name: name.trim() }));
    await switchNotebook(data.notebook.id);
  } catch (error) { showToast(error.message, true); }
});
ui["rename-notebook"].addEventListener("click", async () => {
  const notebook = state.notebooks.find((item) => item.id === state.notebookId);
  const name = window.prompt("笔记本名称", notebook?.name || "");
  if (!name?.trim()) return;
  try { await api(`/api/notebooks/${state.notebookId}`, jsonOptions("PATCH", { name: name.trim() })); await loadNotebooks(); }
  catch (error) { showToast(error.message, true); }
});
ui["delete-notebook"].addEventListener("click", async () => {
  const notebook = state.notebooks.find((item) => item.id === state.notebookId);
  if (!window.confirm(`删除空笔记本“${notebook?.name}”？有资料或笔记的笔记本不能直接删除。`)) return;
  try {
    await api(`/api/notebooks/${state.notebookId}`, { method: "DELETE" });
    await switchNotebook("default");
  } catch (error) { showToast(error.message, true); }
});
ui["sources-tab"].addEventListener("click", () => setLibraryTab("sources"));
ui["notes-tab"].addEventListener("click", () => setLibraryTab("notes"));
ui["applications-tab"].addEventListener("click", () => setLibraryTab("applications"));
ui["select-all-sources"].addEventListener("change", (event) => {
  state.selectedSources = new Set(event.target.checked ? state.documents.map((doc) => doc.id) : []);
  saveSourceSelection();
  renderDocuments();
});
ui["source-search-form"].addEventListener("submit", (event) => { event.preventDefault(); searchSources(); });
ui["source-search"].addEventListener("input", () => { if (!ui["source-search"].value.trim()) searchSources(); });
ui["new-note"].addEventListener("click", () => openNote());
ui["close-note"].addEventListener("click", closeNote);
ui["note-dialog"].addEventListener("cancel", (event) => { event.preventDefault(); closeNote(); });
window.addEventListener("beforeunload", (event) => {
  if (ui["note-dialog"].open && (noteSaving || noteDirty())) {
    event.preventDefault();
    event.returnValue = "";
  }
});
ui["note-form"].addEventListener("submit", async (event) => {
  event.preventDefault();
  try { if (await persistNote()) { ui["note-dialog"].close(); showToast("笔记已保存。"); } }
  catch (error) { showToast(error.message, true); }
});
ui["delete-note"].addEventListener("click", async () => {
  if (!currentNote || !window.confirm(`删除笔记“${currentNote.title}”？`)) return;
  try {
    await api(`/api/notes/${currentNote.id}`, { method: "DELETE" });
    ui["note-dialog"].close();
    await Promise.all([loadNotes(), loadNotebooks()]);
  } catch (error) { showToast(error.message, true); }
});
ui["export-note"].addEventListener("click", async () => {
  try { const note = await persistNote(); if (note) download(`/api/notes/${note.id}/export`); }
  catch (error) { showToast(error.message, true); }
});
ui["export-notes"].addEventListener("click", () => download(`/api/notebooks/${state.notebookId}/export`));
document.getElementById("close-move").addEventListener("click", () => document.getElementById("move-dialog").close());
document.getElementById("move-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!movingDocument || state.busy) return;
  try {
    await api(`/api/documents/${movingDocument.id}`, jsonOptions("PATCH", { notebook_id: document.getElementById("move-target").value }));
    if (state.pdfDocumentId === movingDocument.id) resetViewer();
    document.getElementById("move-dialog").close();
    await Promise.all([loadDocuments(), loadNotebooks()]);
    showToast("资料已移动，已有笔记中的引用原文仍会保留。");
  } catch (error) { showToast(error.message, true); }
});

async function initialize() {
  setBusy(true);
  try {
    await loadNotebooks();
    await Promise.all([loadDocuments(true), loadNotes(), loadSettings(), loadChat()]);
    setLibraryTab(localStorage.getItem(`zhixue-library-tab:${state.notebookId}`) || 'sources');
    if (new URLSearchParams(location.search).get('library') === 'applications') {
      setLibraryTab('applications');
      if (innerWidth <= 760) setMobilePane('sources');
    }
  } catch (error) {
    showToast(`初始化失败：${error.message}`, true);
  } finally { setBusy(state.chatPending); }
}

initializePanelResize(() => { if (state.pdfDocument && document.getElementById('viewer-document').clientWidth > 0) renderPdfPage(state.pdfPage); });
initialize();

initializeCloudSync({ api, jsonOptions, showToast, refresh: async () => {
  if (state.busy || dom.questionInput.value || state.draftImages.length || state.replyTo ||
      document.querySelector('dialog[open]:not(#sync-dialog)')) return false;
  const notebook = state.notebookId;
  setBusy(true);
  try {
    await loadNotebooks();
    if (notebook !== state.notebookId) resetViewer();
    await Promise.all([loadDocuments(true), loadNotes(), loadChat(true)]);
    return true;
  } finally { setBusy(state.chatPending); }
} });
