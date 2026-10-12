"use strict";

// Preview resources are bundled locally. No project content runs as HTML or script.
let workspacePdfLibrary = null;

function workspacePreviewSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function workspaceFileMatches(text, query, limit = 1000) {
  if (!query) return { positions: [], limited: false };
  const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "giu");
  const positions = [];
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (positions.length === limit) return { positions, limited: true };
    positions.push({ start: match.index, end: match.index + match[0].length });
  }
  return { positions, limited: false };
}

function workspacePreviewContentUrl(projectId, path, version) {
  return "api/workspace/projects/" + encodeURIComponent(projectId) + "/entries/content?path=" +
    encodeURIComponent(path) + "&version=" + encodeURIComponent(version);
}

async function workspacePreviewContent(projectId, path, version, signal) {
  const response = await fetch(workspacePreviewContentUrl(projectId, path, version), { signal, cache: "no-store" });
  if (!response.ok) {
    let message = "读取文件失败（HTTP " + response.status + "）";
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.blob();
}

function workspaceLoadPdf() {
  if (!workspacePdfLibrary) {
    workspacePdfLibrary = import("./vendor/pdfjs/pdf.mjs").then(library => {
      library.GlobalWorkerOptions.workerSrc = new URL("vendor/pdfjs/pdf.worker.mjs", document.baseURI).href;
      return library;
    }).catch(error => { workspacePdfLibrary = null; throw error; });
  }
  return workspacePdfLibrary;
}

function createWorkspaceFilePreview(pane, callbacks) {
  pane.innerHTML = `
    <header class="wsp-preview-head">
      <div class="wsp-preview-heading"><span class="wsp-preview-kind">${workspaceIcon("fileCode").outerHTML}</span><strong class="wsp-preview-name">文件预览</strong><span class="wsp-preview-readonly">只读</span></div>
      <div class="wsp-preview-actions">
        <button type="button" data-preview-action="reload" aria-label="刷新文件预览" title="刷新文件预览">${workspaceIcon("refresh").outerHTML}</button>
        <button type="button" data-preview-action="system" aria-label="用系统应用打开" title="用系统应用打开">${workspaceIcon("open").outerHTML}</button>
        <button type="button" data-preview-action="maximize" aria-label="放大文件预览" title="放大文件预览">${workspaceIcon("maximize").outerHTML}</button>
        <button type="button" data-preview-action="close" aria-label="关闭文件预览" title="关闭文件预览">${workspaceIcon("close").outerHTML}</button>
      </div>
    </header>
    <div class="wsp-preview-path"></div>
    <div class="wsp-preview-toolbar"><div class="wsp-preview-controls"></div><button class="wsp-preview-reference" type="button" data-preview-action="reference">${workspaceIcon("reference").outerHTML}<span>引用到对话</span></button></div>
    <div class="wsp-preview-find" hidden>
      <label>${workspaceIcon("search").outerHTML}<input type="search" aria-label="查找文件内容" placeholder="查找文件内容" maxlength="500"></label>
      <span class="wsp-preview-find-count" role="status" aria-live="polite"></span>
      <button type="button" data-preview-action="find-prev" aria-label="上一个匹配" title="上一个匹配">${workspaceIcon("chevronUp").outerHTML}</button>
      <button type="button" data-preview-action="find-next" aria-label="下一个匹配" title="下一个匹配">${workspaceIcon("chevronDown").outerHTML}</button>
      <button type="button" data-preview-action="find-close" aria-label="关闭文件查找" title="关闭文件查找">${workspaceIcon("close").outerHTML}</button>
    </div>
    <div class="wsp-preview-notice" role="status" hidden></div>
    <div class="wsp-preview-body"></div>
    <footer class="wsp-preview-status"><span></span><span></span></footer>`;
  const root = pane.closest(".wsp");
  const heading = pane.querySelector(".wsp-preview-name");
  const pathLabel = pane.querySelector(".wsp-preview-path");
  const controls = pane.querySelector(".wsp-preview-controls");
  const body = pane.querySelector(".wsp-preview-body");
  const status = pane.querySelector(".wsp-preview-status");
  const notice = pane.querySelector(".wsp-preview-notice");
  const find = pane.querySelector(".wsp-preview-find");
  const findInput = find.querySelector("input");
  const findCount = find.querySelector(".wsp-preview-find-count");
  let selected = null;
  let generation = 0;
  let abort = null;
  let objectUrl = null;
  let image = null;
  let sourceMode = false;
  let wrap = false;
  let zoom = 1;
  let fit = true;
  let matches = { positions: [], limited: false };
  let matchIndex = -1;
  let pdf = null;
  let pdfTask = null;
  let pdfRender = null;
  let pdfTextLayer = null;
  let pdfPage = 1;
  let pageRequest = 0;
  let resizeFrame = null;
  let disposed = false;

  const button = (action, label, icon, pressed) => el("button", {
    type: "button", "data-preview-action": action,
    "aria-pressed": pressed === undefined ? null : String(pressed),
  }, icon ? workspaceIcon(icon) : null, el("span", {text: label}));
  const current = request => !disposed && request === generation && pane.isConnected;
  const editable = target => !!target.closest?.("input, textarea, [contenteditable='true']");

  function setMessage(title, message = "", loading = false) {
    body.replaceChildren(el("div", {class: "wsp-preview-empty", role: "status"},
      workspaceIcon(loading ? "busy" : "file", "wsp-preview-empty-icon" + (loading ? " wsp-status-spinning" : "")),
      el("strong", {text: title}), message ? el("p", {text: message}) : null));
    pane.setAttribute("aria-busy", String(loading));
  }

  function releaseMedia() {
    pageRequest++;
    pdfRender?.cancel(); pdfRender = null;
    pdfTextLayer?.cancel(); pdfTextLayer = null;
    if (pdfTask) void pdfTask.destroy().catch(() => {});
    pdfTask = null; pdf = null; image = null;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  }

  function maximize(value) {
    root.classList.toggle("preview-maximized", value);
    const action = pane.querySelector('[data-preview-action="maximize"]');
    const label = value ? "恢复文件预览大小" : "放大文件预览";
    action.setAttribute("aria-label", label); action.title = label;
    action.replaceChildren(workspaceIcon(value ? "restore" : "maximize"));
  }

  function reset() {
    generation++; abort?.abort(); abort = null;
    releaseMedia(); maximize(false);
    selected = null; sourceMode = false; fit = true; zoom = 1; pdfPage = 1;
    heading.textContent = "文件预览"; pathLabel.textContent = ""; controls.replaceChildren();
    status.firstElementChild.textContent = status.lastElementChild.textContent = "";
    notice.hidden = find.hidden = true; findInput.value = "";
    updateActions(); setMessage("选择一个文件", "点击目录树中的文件，在这里查看内容。");
  }

  function updateActions() {
    for (const action of ["reload", "system", "reference"]) {
      pane.querySelector(`[data-preview-action="${action}"]`).disabled = !selected;
    }
  }

  function renderControls() {
    controls.replaceChildren();
    const info = selected?.info;
    if (!info) return;
    if (info.kind === "markdown") controls.append(
      button("read", "阅读", null, !sourceMode), button("source", "源码", null, sourceMode));
    if (info.kind === "text" || info.kind === "markdown" && sourceMode) controls.append(
      button("find", "查找", "search"), button("copy", "复制", "copy"),
      button("wrap", "自动换行", null, wrap));
    if (info.kind === "image" || info.kind === "pdf") {
      if (info.kind === "pdf") {
        const previous = button("page-prev", "上一页", "chevronLeft");
        previous.disabled = !pdf || pdfPage <= 1;
        const next = button("page-next", "下一页", "chevron");
        next.disabled = !pdf || pdfPage >= pdf.numPages;
        controls.append(previous, el("label", {class: "wsp-preview-page"},
          el("input", {type: "number", min: "1", max: String(pdf?.numPages || 1), value: pdfPage,
            disabled: !pdf, "aria-label": "PDF 页码"}),
          el("span", {text: "/ " + (pdf?.numPages || "…")})), next);
      }
      controls.append(button("fit", info.kind === "image" ? "适应窗口" : "适应宽度", null, fit),
        button("zoom-out", "−"), button("zoom-reset", fit ? "100%" : Math.round(zoom * 100) + "%"), button("zoom-in", "+"));
      controls.querySelector('[data-preview-action="zoom-out"]').setAttribute("aria-label", "缩小");
      controls.querySelector('[data-preview-action="zoom-in"]').setAttribute("aria-label", "放大");
      controls.querySelector('[data-preview-action="zoom-reset"]').title = "原始大小（100%）";
    }
  }

  function renderText() {
    const info = selected.info;
    body.className = "wsp-preview-body";
    body.replaceChildren(); find.hidden = true;
    notice.hidden = !info.truncated;
    if (info.truncated) notice.textContent = "文件较长，当前显示前 20,000 行；完整内容可使用系统应用打开。";
    if (!info.content) {
      setMessage("空文件", "此文件尚未包含内容。"); renderControls(); return;
    }
    if (info.kind === "markdown" && !sourceMode) {
      const article = el("article", {class: "wsp-preview-markdown"}, trjMarkdown(info.content));
      body.append(article);
      if (window.Prism) for (const block of article.querySelectorAll(".trj-md-codeblock")) {
        const code = block.querySelector("code"), label = block.querySelector(".trj-md-codebar span").textContent;
        if (Prism.languages[label] && code.textContent.length < 200_000) {
          code.className = "language-" + label; Prism.highlightElement(code);
        }
      }
    } else {
      const language = info.content.length < 200_000 ? info.language : "plain";
      const code = el("code", {class: "language-" + language, text: info.content});
      const pre = el("pre", {class: "wsp-preview-code line-numbers" + (wrap ? " wrap" : ""),
        "aria-label": "文件源码"}, code);
      body.append(pre);
      if (window.Prism) Prism.highlightElement(code);
    }
    pane.setAttribute("aria-busy", "false");
    renderControls();
  }

  function updateImage() {
    if (!image) return;
    const stage = image.parentNode;
    stage.classList.toggle("fit", fit);
    image.style.width = fit ? "" : Math.round(image.naturalWidth * zoom) + "px";
    image.style.height = fit ? "" : Math.round(image.naturalHeight * zoom) + "px";
    renderControls();
  }

  async function renderPdf(resetScroll = true) {
    if (!pdf || !selected) return;
    const request = ++pageRequest, opened = generation, documentPdf = pdf;
    pdfRender?.cancel(); pdfTextLayer?.cancel();
    try {
      const page = await documentPdf.getPage(pdfPage);
      if (request !== pageRequest || !current(opened)) return;
      const natural = page.getViewport({scale: 1});
      const scale = fit ? Math.max(0.1, Math.min(2.5, (body.clientWidth - 48) / natural.width)) : zoom;
      const viewport = page.getViewport({scale});
      const pixels = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(16_000_000 / (viewport.width * viewport.height)));
      const canvas = el("canvas", {"aria-label": `PDF 第 ${pdfPage} 页`});
      canvas.width = Math.max(1, Math.floor(viewport.width * pixels));
      canvas.height = Math.max(1, Math.floor(viewport.height * pixels));
      canvas.style.width = viewport.width + "px"; canvas.style.height = viewport.height + "px";
      const text = el("div", {class: "wsp-pdf-text-layer"});
      const sheet = el("div", {class: "wsp-preview-pdf-sheet"}, canvas, text);
      sheet.style.setProperty("--total-scale-factor", scale);
      sheet.style.width = viewport.width + "px";
      sheet.style.height = viewport.height + "px";
      body.className = "wsp-preview-body wsp-preview-pdf";
      body.replaceChildren(sheet);
      notice.hidden = true;
      if (resetScroll) { body.scrollTop = 0; body.scrollLeft = 0; }
      pane.setAttribute("aria-busy", "true");
      renderControls();
      const task = page.render({canvasContext: canvas.getContext("2d", {alpha: false}), viewport,
        transform: pixels !== 1 ? [pixels, 0, 0, pixels, 0, 0] : null});
      pdfRender = task;
      await task.promise;
      if (request !== pageRequest || !current(opened)) return;
      const library = await workspaceLoadPdf();
      if (request !== pageRequest || !current(opened)) return;
      pdfTextLayer = new library.TextLayer({textContentSource: page.streamTextContent(), container: text, viewport});
      await pdfTextLayer.render();
      if (request !== pageRequest || !current(opened)) return;
      pane.setAttribute("aria-busy", "false");
      status.lastElementChild.textContent = `第 ${pdfPage} / ${documentPdf.numPages} 页`;
    } catch (error) {
      if (request !== pageRequest || !current(opened) || error.name === "RenderingCancelledException") return;
      pane.setAttribute("aria-busy", "false");
      notice.hidden = false; notice.textContent = "PDF 页面显示失败：" + error.message + "，可刷新或使用系统应用打开。";
    }
  }

  async function open(projectId, path) {
    generation++; abort?.abort(); releaseMedia();
    const request = generation;
    abort = new AbortController();
    const signal = abort.signal;
    selected = {projectId, path, info: null};
    sourceMode = false; fit = true; zoom = 1; pdfPage = 1;
    find.hidden = notice.hidden = true; findInput.value = "";
    heading.textContent = path.split("/").at(-1); pathLabel.textContent = path; pathLabel.title = path;
    status.firstElementChild.textContent = status.lastElementChild.textContent = "";
    controls.replaceChildren(); updateActions();
    body.className = "wsp-preview-body";
    setMessage("正在读取文件…", "", true);
    try {
      const info = await callbacks.load(projectId, path, signal);
      if (!current(request)) return;
      selected.info = info;
      pane.querySelector(".wsp-preview-kind").replaceChildren(workspaceIcon(info.kind === "text" ? "fileCode" : info.kind === "image" ? "image" : "file"));
      status.firstElementChild.textContent = [info.kind === "text" ? info.language : info.kind === "markdown" ? "Markdown" : info.kind === "pdf" ? "PDF" : "",
        info.encoding, workspacePreviewSize(info.size)].filter(Boolean).join(" · ");
      status.lastElementChild.textContent = info.line_count ? info.line_count.toLocaleString() + " 行" : "";
      renderControls();
      if (info.kind === "unsupported") { setMessage("暂不支持预览", info.message); return; }
      if (info.kind === "text" || info.kind === "markdown") { renderText(); return; }
      const blob = await callbacks.content(projectId, path, info.version, signal);
      if (!current(request)) return;
      if (info.kind === "image") {
        objectUrl = URL.createObjectURL(blob);
        const node = el("img", {alt: info.name, draggable: "false"});
        await new Promise((resolve, reject) => {
          node.onload = resolve; node.onerror = () => reject(new Error("图片无法解码，请使用系统应用打开"));
          node.src = objectUrl;
        });
        if (!current(request)) return;
        image = node;
        body.replaceChildren(el("div", {class: "wsp-preview-image fit"}, image));
        body.className = "wsp-preview-body wsp-preview-image-body";
        status.lastElementChild.textContent = `${image.naturalWidth} × ${image.naturalHeight}`;
        updateImage(); pane.setAttribute("aria-busy", "false");
      } else if (info.kind === "pdf") {
        const library = await workspaceLoadPdf();
        if (!current(request)) return;
        const data = await blob.arrayBuffer();
        if (!current(request)) return;
        const assetBase = new URL("vendor/pdfjs/", document.baseURI).href;
        pdfTask = library.getDocument({data: new Uint8Array(data), cMapUrl: assetBase + "cmaps/", cMapPacked: true,
          standardFontDataUrl: assetBase + "standard_fonts/", wasmUrl: assetBase + "wasm/", isEvalSupported: false});
        const loaded = await pdfTask.promise;
        if (!current(request)) return;
        pdf = loaded;
        await renderPdf();
      }
    } catch (error) {
      if (!current(request) || error.name === "AbortError") return;
      const message = error.name === "PasswordException" ? "此 PDF 需要密码，请使用系统应用打开。" :
        (error.detail || error.message || "文件读取失败");
      setMessage("无法预览此文件", String(message));
    }
  }

  function showMatch(index) {
    const code = body.querySelector(".wsp-preview-code > code");
    if (!code || !matches.positions.length) return;
    matchIndex = (index + matches.positions.length) % matches.positions.length;
    const match = matches.positions[matchIndex];
    const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.parentElement.closest(".line-numbers-rows, .line-numbers-sizer") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const range = document.createRange();
    let offset = 0, started = false, node;
    while ((node = walker.nextNode())) {
      const end = offset + node.data.length;
      if (!started && match.start >= offset && match.start < end) {
        range.setStart(node, match.start - offset); started = true;
      }
      if (started && match.end <= end) { range.setEnd(node, match.end - offset); break; }
      offset = end;
    }
    if (!started) return;
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    const rect = range.getBoundingClientRect(), frame = body.getBoundingClientRect();
    body.scrollTop += rect.top - frame.top - body.clientHeight / 2;
    if (rect.left < frame.left || rect.right > frame.right) body.scrollLeft += rect.left - frame.left - 50;
    findCount.textContent = `${matchIndex + 1} / ${matches.positions.length}${matches.limited ? "+" : ""}`;
  }

  function search() {
    matches = workspaceFileMatches(selected?.info?.content || "", findInput.value);
    matchIndex = -1;
    findCount.textContent = findInput.value ? (matches.positions.length ? "" : "没有匹配") : "";
    if (matches.positions.length) showMatch(0);
  }

  async function copy() {
    const text = selected?.info?.content || "";
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const previous = document.activeElement;
        const field = el("textarea", {class: "wsp-clipboard-field", value: text}); document.body.append(field);
        try { field.select(); if (!document.execCommand("copy")) throw new Error("浏览器未允许复制"); }
        finally { field.remove(); previous?.focus(); }
      }
      callbacks.notify("已复制文件内容");
    } catch (error) { callbacks.notify("复制失败：" + error.message, "error"); }
  }

  function zoomTo(value) {
    if (fit && image) zoom = image.getBoundingClientRect().width / image.naturalWidth;
    if (fit && pdf) zoom = Number(body.querySelector(".wsp-preview-pdf-sheet")?.style.getPropertyValue("--total-scale-factor")) || 1;
    fit = false;
    zoom = value === null ? 1 : Math.max(0.1, Math.min(3, zoom + value));
    if (image) updateImage();
    else if (pdf) void renderPdf(false);
  }

  function click(event) {
    const action = event.target.closest("[data-preview-action]")?.dataset.previewAction;
    if (!action) return;
    if (action === "close") { callbacks.close(); return; }
    if (action === "maximize") { maximize(!root.classList.contains("preview-maximized")); return; }
    if (!selected) return;
    if (action === "reload") void open(selected.projectId, selected.path);
    if (action === "system") void callbacks.system(selected.projectId, selected.path);
    if (action === "reference") callbacks.reference(selected.path);
    if (action === "copy") void copy();
    if (action === "find") { find.hidden = false; findInput.focus(); findInput.select(); }
    if (action === "find-close") { find.hidden = true; pane.focus({preventScroll: true}); }
    if (action === "find-prev") showMatch(matchIndex - 1);
    if (action === "find-next") showMatch(matchIndex + 1);
    if (action === "read" || action === "source") { sourceMode = action === "source"; notice.hidden = true; renderText(); }
    if (action === "wrap") {
      wrap = !wrap; const pre = body.querySelector(".wsp-preview-code"); pre?.classList.toggle("wrap", wrap);
      if (pre && window.Prism?.plugins?.lineNumbers) Prism.plugins.lineNumbers.resize(pre);
      renderControls();
    }
    if (action === "fit") { fit = true; if (image) updateImage(); else if (pdf) void renderPdf(false); }
    if (action === "zoom-out") zoomTo(-0.25);
    if (action === "zoom-in") zoomTo(0.25);
    if (action === "zoom-reset") zoomTo(null);
    if (pdf && (action === "page-prev" || action === "page-next")) {
      pdfPage = Math.max(1, Math.min(pdf.numPages, pdfPage + (action === "page-prev" ? -1 : 1)));
      void renderPdf();
    }
  }

  function keydown(event) {
    if (pane.hidden || !pane.getClientRects().length || disposed || event.isComposing) return;
    if (event.key === "Escape") {
      if (!find.hidden) { event.preventDefault(); find.hidden = true; pane.focus({preventScroll: true}); }
      else if (!editable(event.target)) {
        event.preventDefault();
        if (root.classList.contains("preview-maximized")) maximize(false);
        else callbacks.close();
      }
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f" && body.querySelector(".wsp-preview-code")) {
      event.preventDefault(); find.hidden = false; findInput.focus(); findInput.select();
    }
    if (event.target === findInput && event.key === "Enter") { event.preventDefault(); showMatch(matchIndex + (event.shiftKey ? -1 : 1)); }
    if (pdf && !editable(event.target) && !event.ctrlKey && !event.metaKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault(); pdfPage = Math.max(1, Math.min(pdf.numPages, pdfPage + (event.key === "ArrowLeft" ? -1 : 1))); void renderPdf();
    }
  }

  const resizeObserver = new ResizeObserver(() => {
    if (pane.hidden || disposed) return;
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = null;
      if (fit && pdf) void renderPdf(false);
      const pre = body.querySelector(".wsp-preview-code.wrap");
      if (pre && window.Prism?.plugins?.lineNumbers) Prism.plugins.lineNumbers.resize(pre);
    });
  });
  resizeObserver.observe(body);
  pane.addEventListener("click", click);
  // The tree may retain focus while a file is loading.
  document.addEventListener("keydown", keydown);
  findInput.addEventListener("input", search);
  controls.addEventListener("change", event => {
    if (!pdf || !event.target.matches(".wsp-preview-page input")) return;
    const value = Number(event.target.value);
    if (!Number.isInteger(value) || value < 1 || value > pdf.numPages) { event.target.value = pdfPage; return; }
    pdfPage = value; void renderPdf();
  });
  reset();
  return {
    open, reset, maximize,
    get projectId() { return selected?.projectId || null; },
    get path() { return selected?.path || null; },
    get visible() { return !pane.hidden; },
    focus() { pane.focus({preventScroll: true}); },
    dispose() {
      disposed = true; reset(); resizeObserver.disconnect();
      if (resizeFrame) cancelAnimationFrame(resizeFrame);
      pane.removeEventListener("click", click);
      document.removeEventListener("keydown", keydown);
    },
  };
}
