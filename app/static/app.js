import * as pdfjsLib from "/static/vendor/pdfjs/pdf.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.mjs";

const state = {
  notebooks: [],
  notebookId: localStorage.getItem("zhixue-notebook") || "default",
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
  pdfPageCount: 0,
  pdfPage: 1,
  highlightLines: [],
  renderGeneration: 0,
  renderTask: null,
  viewerGeneration: 0,
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
  "source-search", "search-results", "select-all-sources", "note-count", "note-list",
  "new-note", "export-notes", "note-dialog", "note-form", "note-title", "note-content",
  "note-sources", "close-note", "delete-note", "export-note", "save-note", "clear-chat",
].map((id) => [id, document.getElementById(id)]));
const emptyChatTemplate = dom.emptyChat.outerHTML;
let currentNote = null;
let noteSaving = false;
let chatPollTimer = null;

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

function selectionStatus() {
  ui["selection-status"].textContent = `已选择 ${selectedIds().length} / ${state.documents.length} 个来源`;
  ui["select-all-sources"].checked = !!state.documents.length && selectedIds().length === state.documents.length;
  ui["select-all-sources"].indeterminate = selectedIds().length > 0 && selectedIds().length < state.documents.length;
  const heading = document.querySelector("#empty-chat h1");
  if (heading) heading.textContent = state.documents.length ? "从这个笔记本开始提问" : "上传资料后开始提问";
}

function setBusy(busy) {
  state.busy = busy;
  [dom.sendButton, dom.summaryButton, dom.uploadButton, ui["notebook-select"],
    ui["new-notebook"], ui["rename-notebook"], ui["delete-notebook"], ui["clear-chat"]].forEach((el) => el.disabled = busy);
}

async function loadNotebooks() {
  const { notebooks } = await api("/api/notebooks");
  state.notebooks = notebooks;
  if (!notebooks.some((n) => n.id === state.notebookId)) state.notebookId = notebooks[0]?.id || "default";
  localStorage.setItem("zhixue-notebook", state.notebookId);
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
  window.clearTimeout(chatPollTimer);
  state.chatPending = false;
  state.notebookId = id;
  localStorage.setItem("zhixue-notebook", id);
  state.selectedSources.clear();
  state.documents = [];
  state.notes = [];
  ui["note-list"].replaceChildren();
  ui["note-count"].textContent = "0";
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
    setLibraryTab("sources");
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
    throw new Error(detail);
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
    content.addEventListener("click", () => openDocument(doc));

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
  const previous = new Set(state.documents.map((doc) => doc.id));
  const data = await api(`/api/documents?notebook_id=${encodeURIComponent(state.notebookId)}`);
  state.documents = data.documents || [];
  state.selectedSources = new Set(state.documents.filter((doc) => selectAll || state.selectedSources.has(doc.id) || !previous.has(doc.id)).map((doc) => doc.id));
  renderDocuments();
  selectionStatus();
  ui["search-results"].hidden = true;
  dom.documentList.hidden = false;
}

async function uploadFiles(files) {
  const accepted = [...files];
  if (!accepted.length) return;
  if (state.busy) return showToast("请等待当前操作完成。");
  setBusy(true);
  try {
    for (const file of accepted) {
      showToast(`正在解析：${file.name}`);
      const body = new FormData();
      body.append("file", file);
      const data = await api(`/api/documents?notebook_id=${encodeURIComponent(state.notebookId)}`, { method: "POST", body });
      showToast(`已导入：${data.document.name}`);
    }
    await loadDocuments();
    await loadNotebooks();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setBusy(false);
    dom.fileInput.value = "";
  }
}

function resetViewer() {
  state.viewerGeneration++;
  state.renderGeneration++;
  state.renderTask?.cancel();
  state.renderTask = null;
  state.pdfLoadingTask?.destroy().catch(() => {});
  state.pdfLoadingTask = null;
  state.pdfDocument = null;
  state.pdfDocumentId = null;
  state.pdfPageCount = 0;
  state.pdfPage = 1;
  state.highlightLines = [];
  dom.highlightLayer.replaceChildren();
  dom.canvas.width = 0;
  dom.canvas.height = 0;
  dom.viewerEmpty.hidden = false;
  dom.viewerScroll.hidden = true;
  dom.viewerName.textContent = "尚未打开资料";
  dom.pageTotal.textContent = "/ 0";
  dom.pageInput.value = "1";
  dom.sourceText.textContent = "尚未选择引用。";
}

async function openDocument(doc) {
  if (doc.kind === "pdf") {
    setMobilePane("viewer");
    await openPdf(doc.id, 1, []);
    return;
  }
  resetViewer();
  setMobilePane("viewer");
  dom.viewerName.textContent = doc.name;
  dom.viewerEmpty.hidden = false;
  dom.viewerScroll.hidden = true;
  const data = await api(`/api/documents/${doc.id}/chunks`);
  dom.sourceText.replaceChildren();
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

async function openPdf(documentId, pageNumber, lines = []) {
  const generation = ++state.viewerGeneration;
  const doc = state.documents.find((item) => item.id === documentId);
  if (!doc) {
    showToast("找不到对应资料。", true);
    return;
  }
  state.highlightLines = lines || [];
  dom.viewerName.textContent = doc.name;
  dom.viewerEmpty.hidden = true;
  dom.viewerScroll.hidden = false;

  try {
    if (state.pdfDocumentId !== documentId || !state.pdfDocument) {
      state.renderGeneration++;
      state.renderTask?.cancel();
      state.renderTask = null;
      dom.highlightLayer.replaceChildren();
      dom.canvas.width = 0;
      dom.canvas.height = 0;
      state.pdfLoadingTask?.destroy().catch(() => {});
      state.pdfDocument = null;
      state.pdfDocumentId = documentId;
      const loadingTask = pdfjsLib.getDocument({
        url: `/api/documents/${documentId}/file`,
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
    await renderPdfPage(pageNumber);
  } catch (error) {
    if (generation !== state.viewerGeneration) return;
    console.error("PDF preview failed", error);
    resetViewer();
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
  drawHighlights(viewport);
}

function drawHighlights(viewport) {
  const lines = state.highlightLines.filter(
    (line) => Number(line.page) === Number(state.pdfPage) && Array.isArray(line.bbox)
  );
  for (const line of lines) {
    // PyMuPDF boxes start at the crop area's top left; PDF.js accepts PDF bottom-left coordinates.
    const [left, , , top] = viewport.viewBox;
    const [x1, y1] = viewport.convertToViewportPoint(left + line.bbox[0], top - line.bbox[1]);
    const [x2, y2] = viewport.convertToViewportPoint(left + line.bbox[2], top - line.bbox[3]);
    const box = document.createElement("div");
    box.className = "highlight-box";
    box.style.left = `${Math.min(x1, x2)}px`;
    box.style.top = `${Math.min(y1, y2)}px`;
    box.style.width = `${Math.abs(x2 - x1)}px`;
    box.style.height = `${Math.abs(y2 - y1)}px`;
    dom.highlightLayer.append(box);
  }
}

function renderInline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(
      /\[(\d+)\]/g,
      '<button class="citation-ref" type="button" data-citation="$1">[$1]</button>'
    );
}

function renderAnswerHtml(text) {
  const lines = String(text || "").split("\n");
  const blocks = [];
  let listOpen = false;
  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      if (!listOpen) {
        blocks.push("<ul>");
        listOpen = true;
      }
      blocks.push(`<li>${renderInline(bullet[1])}</li>`);
      continue;
    }
    if (listOpen) {
      blocks.push("</ul>");
      listOpen = false;
    }
    if (!line.trim()) {
      blocks.push('<div class="message-gap"></div>');
      continue;
    }
    blocks.push(`<p>${renderInline(line)}</p>`);
  }
  if (listOpen) blocks.push("</ul>");
  return blocks.join("");
}

function scrollMessages() {
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
      body.innerHTML = '<span class="loading-dots">正在检索和生成</span>';
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
  body.innerHTML = renderAnswerHtml(answer);
  state.answers.set(messageId, { answer, citations: citations || [], notebook_id: state.notebookId,
    title: article.previousElementSibling?.querySelector(".message-body")?.textContent?.slice(0, 200) || "学习笔记",
    queryTerms: options.question ? extractSearchTerms(options.question) : window.__lastQuestionTerms || [] });
  body.querySelectorAll(".citation-ref").forEach((button) => {
    button.dataset.messageId = messageId;
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
  if (citations?.length) {
    const list = document.createElement("div");
    list.className = "citation-list";
    citations.forEach((citation, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "citation-card";
      button.dataset.messageId = messageId;
      button.dataset.citation = String(index + 1);
      button.innerHTML = `
        <span class="citation-index">${index + 1}</span>
        <span>
          <span class="citation-title">${escapeHtml(citation.document_name)}${citation.page ? ` · 第 ${citation.page} 页` : ""}</span>
          <span class="citation-preview">${escapeHtml(citation.text)}</span>
        </span>
      `;
      list.append(button);
    });
    body.append(list);
  }
  const save = document.createElement("button");
  save.type = "button";
  save.className = "button save-answer";
  save.dataset.saveAnswer = messageId;
  save.textContent = "保存为笔记";
  article.append(save);
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

async function askQuestion(question) {
  const text = question.trim();
  if (!text) return;
  if (state.busy) return showToast("请等待当前回答完成。");
  if (state.documents.length && !selectedIds().length) return showToast("请至少勾选一个来源。", true);
  window.__lastQuestionTerms = extractSearchTerms(text);
  addMessage("user", text);
  const assistantId = addMessage("assistant", "", { loading: true });
  setBusy(true);
  try {
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: text, notebook_id: state.notebookId, document_ids: selectedIds() }),
    });
    updateAssistantMessage(
      assistantId,
      data.answer,
      data.citations,
      data.mode,
      data.source_mode
    );
  } catch (error) {
    setAssistantError(assistantId, error);
    showToast(error.message, true);
  } finally {
    setBusy(false);
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
  dom.sourceText.innerHTML = `
    <strong>${escapeHtml(citation.document_name)}${citation.page ? ` · 第 ${citation.page} 页` : ""}</strong><br />
    ${plainSourceText(citation)}
  `;
  if (citation.document_kind === "pdf") {
    setMobilePane("viewer");
    const terms = (citation.queryTerms || []).filter((term) => term.length >= 2);
    const matchingLines = (citation.lines || []).filter((line) => {
      const lineText = String(line.text || "").toLowerCase();
      return terms.some((term) => lineText.includes(String(term).toLowerCase()));
    });
    const targetPage = matchingLines[0]?.page || citation.page || 1;
    openPdf(
      citation.document_id,
      targetPage,
      matchingLines.length ? matchingLines : citation.lines || []
    );
  } else {
    setMobilePane("viewer");
    dom.viewerEmpty.hidden = false;
    dom.viewerScroll.hidden = true;
    dom.viewerName.textContent = citation.document_name;
  }
  const article = dom.messages.querySelector(`[data-message-id="${messageId}"]`);
  article?.querySelectorAll(".citation-card").forEach((item) => {
    item.style.borderColor =
      item.dataset.citation === String(citationIndex) ? "#2563eb" : "";
  });
}

function setMobilePane(pane) {
  document.querySelectorAll(".mobile-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.pane === pane);
  });
  dom.workspace.classList.toggle("show-viewer", pane === "viewer");
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

async function generateSummary() {
  if (state.busy) return;
  if (!selectedIds().length) {
    showToast("请先上传至少一份资料。", true);
    return;
  }
  setBusy(true);
  addMessage("user", "根据当前选中的来源生成一份复习提纲。");
  const assistantId = addMessage("assistant", "", { loading: true });
  try {
    const data = await api("/api/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notebook_id: state.notebookId, document_ids: selectedIds() }),
    });
    updateAssistantMessage(assistantId, data.answer, data.citations, data.mode);
  } catch (error) {
    setAssistantError(assistantId, error);
    showToast(error.message, true);
  } finally {
    setBusy(false);
  }
}

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
  if (state.documents.length && !selectedIds().length) return showToast("请至少勾选一个来源。", true);
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
  const save = event.target.closest("[data-save-answer]");
  if (save) {
    saveAnswer(save).catch((error) => showToast(error.message, true));
    return;
  }
  const button = event.target.closest("[data-citation]");
  if (!button) return;
  const messageId = button.dataset.messageId;
  const citations = state.messageCitations.get(messageId) || [];
  const citation = citations[Number(button.dataset.citation) - 1];
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
dom.summaryButton.addEventListener("click", generateSummary);

dom.prevPage.addEventListener("click", () => renderPdfPage(state.pdfPage - 1));
dom.nextPage.addEventListener("click", () => renderPdfPage(state.pdfPage + 1));
dom.pageInput.addEventListener("change", () => renderPdfPage(dom.pageInput.value));
dom.openFile.addEventListener("click", () => {
  if (!state.pdfDocumentId) return;
  window.open(
    `/api/documents/${state.pdfDocumentId}/file#page=${state.pdfPage}`,
    "_blank",
    "noopener"
  );
});

document.querySelectorAll(".mobile-tab").forEach((tab) => {
  tab.addEventListener("click", () => setMobilePane(tab.dataset.pane));
});

window.addEventListener("resize", () => {
  if (state.pdfDocument) renderPdfPage(state.pdfPage);
});

function setLibraryTab(tab) {
  const notes = tab === "notes";
  ui["sources-content"].hidden = notes;
  ui["notes-content"].hidden = !notes;
  for (const id of ["sources-tab", "notes-tab"]) {
    const active = id === `${tab}-tab`;
    ui[id].classList.toggle("active", active);
    ui[id].setAttribute("aria-selected", String(active));
  }
}

async function loadChat(polling = false) {
  window.clearTimeout(chatPollTimer);
  const notebookId = state.notebookId;
  const { exchanges } = await api(`/api/notebooks/${encodeURIComponent(notebookId)}/chat`);
  if (notebookId !== state.notebookId) return;
  dom.messages.innerHTML = emptyChatTemplate;
  state.messageCitations.clear();
  state.answers.clear();
  for (const exchange of exchanges) {
    addMessage("user", exchange.question, { id: `user-${exchange.id}` });
    const id = addMessage("assistant", "", { id: exchange.id, loading: true });
    if (exchange.status === "complete") {
      const result = exchange.result;
      updateAssistantMessage(id, result.answer, result.citations, result.mode, result.source_mode, { question: exchange.question });
    } else if (exchange.status === "error") {
      setAssistantError(id, new Error(exchange.result.detail || "上次请求未完成，请重新提问。"));
    }
  }
  selectionStatus();
  state.chatPending = exchanges.some((exchange) => exchange.status === "pending");
  if (state.chatPending) {
    setBusy(true);
    chatPollTimer = window.setTimeout(() => {
      loadChat(true).catch((error) => { state.chatPending = false; setBusy(false); showToast(`恢复对话失败：${error.message}`, true); });
    }, 1500);
  } else if (polling) setBusy(false);
}

ui["clear-chat"].addEventListener("click", async () => {
  if (state.busy || !window.confirm("清空当前笔记本的对话？已保存的笔记和资料不会删除。")) return;
  setBusy(true);
  try {
    await api(`/api/notebooks/${encodeURIComponent(state.notebookId)}/chat`, { method: "DELETE" });
    await loadChat();
    showToast("当前笔记本的对话已清空。");
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
          showCitation({ ...citation, lines: chunk?.lines || [] }, "", citation.index);
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
    const data = await api(`/api/sources/search?notebook_id=${encodeURIComponent(state.notebookId)}&q=${encodeURIComponent(query)}`);
    if (generation !== state.searchGeneration) return;
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
ui["select-all-sources"].addEventListener("change", (event) => {
  state.selectedSources = new Set(event.target.checked ? state.documents.map((doc) => doc.id) : []);
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
  } catch (error) {
    showToast(`初始化失败：${error.message}`, true);
  } finally { setBusy(state.chatPending); }
}

initialize();
