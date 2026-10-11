"use strict";

function createWorkspaceEditor(root, callbacks) {
  const files = new Map();
  const tabNodes = new Map();
  const documentVersions = new Map(), documentEpochs = new Map(), analysisStates = new Map();
  const errorText = error => typeof error.detail === "string" ? error.detail : error.message;
  let current = null, monaco = null, editor = null, opener = null, disposed = false, reopen = false;
  let navigationGeneration = 0;
  let navigation = null;
  let preferences = {defaultEditing: false, autoSave: false};
  try {
    const saved = JSON.parse(localStorage.getItem("sona-code:editor-preferences") || "null");
    if (saved) preferences = {defaultEditing: saved.defaultEditing === true, autoSave: saved.autoSave === true};
  } catch (_) {}
  const panel = el("section", { class: "wsp-editor", hidden: true, "aria-label": "项目代码编辑器" });
  const tabs = el("div", { class: "wsp-editor-tabs", role: "tablist", "aria-label": "已打开文件" });
  const title = el("strong", { class: "wsp-editor-path" });
  const status = el("span", { role: "status", "aria-live": "polite" });
  const analysisStatus = el("span", {class: "wsp-analysis-status", role: "status", "aria-live": "polite"});
  const message = el("p", { class: "wsp-editor-message", role: "status" });
  const canvas = el("div", { class: "wsp-editor-canvas" });
  const language = el("select", { "aria-label": "代码语言" });
  const button = (text, action, attrs = {}) => el("button", { type: "button", text, onclick: action, ...attrs });
  const back = button("后退", () => { void history.move(-1); }, {title: "Ctrl+← 回到上一位置", disabled: true});
  const forward = button("前进", () => { void history.move(1); }, {title: "Ctrl+→ 回到下一位置", disabled: true});
  const history = createCodeHistory({capture: capturePosition,
    openTarget: place => openTarget(place.projectId, place.path, place.selection,
      {recordHistory: false, viewState: place.viewState, historyMove: true}),
    onChange: () => { back.disabled = !history.canBack; forward.disabled = !history.canForward; },
    onError: callbacks.error});
  const client = createLanguageClient({urlFor: callbacks.languageUrl, onError: callbacks.error,
    onStatus: value => {analysisStates.set(JSON.stringify([value.projectId, value.family]), value); render();}});
  const analysisSettings = createAnalysisSettings({api: callbacks.api, projectId: () => current?.projectId,
    onSaved: projectId => { for (const family of ["java", "python", "typescript"]) client.reanalyze(projectId, family); },
    onError: callbacks.error});
  const analysis = button("项目分析", () => { void analysisSettings.open(); });
  const reanalyze = button("重新分析", () => {
    const source = documentSnapshot(); if (source) client.reanalyze(source.projectId, workspaceLanguageFamily(source.language));
  });
  function toggleEditing() {
    if (!current?.model || current.loading || current.data.read_only) return;
    current.editing = !current.editing;
    render(); editor.focus();
  }
  const mode = button("编辑", toggleEditing, {title: "Ctrl/Cmd+I 切换预览和编辑"});
  const save = button("保存", () => { void saveFile(current); }, { class: "wsp-editor-save", title: "Ctrl/Cmd+S 保存" });
  const reload = button("重新加载", () => { void reloadFile(current); });
  const format = button("格式化", async () => {
    const action = editor?.getAction("editor.action.formatDocument");
    if (action?.isSupported()) await action.run();
    else { current.notice = "此语言没有内置格式化器"; render(); }
  });
  const systemOpen = button("系统打开", () => {
    if (current) void callbacks.api(endpoint(current, "entries/open") + "&mode=default", {
      method: "POST", body: {}, silent: true,
    }).catch(error => callbacks.error(errorText(error)));
  });
  const close = button("关闭", closePanel, { "aria-label": "关闭代码编辑器" });
  const defaultEditing = el("input", {type: "checkbox", checked: preferences.defaultEditing});
  const autoSave = el("input", {type: "checkbox", checked: preferences.autoSave});
  const options = el("div", {class: "wsp-editor-options"},
    el("label", {title: "打开文件时直接进入编辑模式"}, defaultEditing, "默认编辑"),
    el("label", {title: "停止输入 1 秒后自动保存；冲突或失败时暂停"}, autoSave, "自动保存"));
  defaultEditing.addEventListener("change", () => {
    preferences.defaultEditing = defaultEditing.checked;
    if (current?.model && !current.loading && !current.data.read_only) current.editing = preferences.defaultEditing;
    persistPreferences(); render();
  });
  autoSave.addEventListener("change", () => {
    preferences.autoSave = autoSave.checked; persistPreferences();
    for (const file of files.values()) { clearTimeout(file.autoTimer); scheduleAutoSave(file); }
    render();
  });
  function persistPreferences() {
    try { localStorage.setItem("sona-code:editor-preferences", JSON.stringify(preferences)); }
    catch (_) { callbacks.error("无法记住编辑器设置；当前窗口仍可使用"); }
  }
  function scheduleAutoSave(file) {
    clearTimeout(file.autoTimer);
    if (disposed || files.get(file.key) !== file || !preferences.autoSave || file.autoPaused || file.saving || file.loading || !dirty(file) || file.data.read_only) return;
    file.autoTimer = setTimeout(() => { void saveFile(file); }, 1000);
  }
  panel.append(el("header", { class: "wsp-editor-toolbar" }, back, forward, title, language, mode, save, format, reload, analysis, reanalyze, systemOpen, close),
    options, tabs, message, canvas, el("footer", { class: "wsp-editor-status" }, status, analysisStatus,
      el("span", { text: "Ctrl+←/→ 后退/前进 · Ctrl/Cmd+I 预览/编辑 · Ctrl/Cmd+S 保存" })));
  root.append(panel);
  function layoutPanel() {
    const nav = root.querySelector?.(".wsp-tabs");
    if (nav) panel.style.top = Math.max(0, nav.getBoundingClientRect().bottom - root.getBoundingClientRect().top) + "px";
    editor?.layout();
  }
  const layoutObserver = typeof ResizeObserver === "function" ? new ResizeObserver(layoutPanel) : null;
  layoutObserver?.observe(root);

  function endpoint(file, resource = "file") {
    return "workspace/projects/" + encodeURIComponent(file.projectId) + "/" + resource + "?path=" + encodeURIComponent(file.path);
  }
  function dirty(file) { return !!file.model && file.model.getValue() !== file.saved; }
  function show() {
    if (!disposed) { panel.hidden = false; callbacks.visibility?.(true); }
    const source = documentSnapshot(); client.setActive(source?.projectId, source && workspaceLanguageFamily(source.language));
    requestAnimationFrame(layoutPanel);
  }
  function hide() {
    panel.hidden = true;
    if (!disposed) { client.setActive(null, null); navigation?.invalidate(); }
    callbacks.visibility?.(false);
  }
  function render() {
    if (disposed) return;
    document.documentElement.dataset.sonaUnsavedCode = String(Array.from(files.values()).some(file => dirty(file) || file.saving));
    // Keep buttons mounted: progress or autosave updates can arrive between
    // pointerdown and click, and replacing the pressed node loses that click.
    for (const [key, tab] of tabNodes) {
      if (!files.has(key)) { tab.node.remove(); tabNodes.delete(key); }
    }
    for (const file of files.values()) {
      let tab = tabNodes.get(file.key);
      if (!tab) {
        const select = button("", () => { void openTarget(file.projectId, file.path); }, {
          role: "tab", title: file.projectName + "/" + file.path,
        });
        const node = el("div", {class: "wsp-editor-tab"}, select,
          button("×", () => closeFile(file), {"aria-label": "关闭文件 " + file.path, title: "关闭此文件"}));
        tab = {node, select}; tabNodes.set(file.key, tab); tabs.append(node);
      }
      tab.node.setAttribute("class", "wsp-editor-tab" + (current === file ? " active" : ""));
      tab.select.textContent = (dirty(file) ? "● " : "") + file.path.split("/").at(-1);
      tab.select.setAttribute("aria-selected", String(current === file));
    }
    const file = current;
    title.textContent = file ? file.projectName + " / " + file.path : "代码编辑器";
    title.title = title.textContent;
    message.textContent = file?.notice || file?.data?.reason || (file?.loading ? "正在加载本地编辑器…" : "");
    message.hidden = !message.textContent;
    canvas.hidden = !file?.model;
    mode.disabled = !file?.model || file.loading || file.data.read_only;
    mode.textContent = file?.editing ? "预览" : "编辑";
    mode.setAttribute("aria-pressed", String(!!file?.editing));
    save.disabled = !file?.model || file.loading || file.data.read_only || !dirty(file) || file.saving;
    save.textContent = file?.saving ? "保存中…" : "保存";
    reload.disabled = !file || file.loading || file.saving;
    format.disabled = !file?.editing || file.loading || file?.data?.read_only;
    language.disabled = !file?.model || file.loading;
    analysis.disabled = !file;
    const source = documentSnapshot(), family = source && workspaceLanguageFamily(source.language);
    reanalyze.disabled = !family;
    analysisStatus.textContent = family ? analysisStates.get(JSON.stringify([file.projectId, family]))?.message || "正在连接本地语言服务" : "此语言支持语法预览";
    if (file?.model) {
      language.value = file.model.getLanguageId();
      editor.updateOptions({ readOnly: file.loading || !file.editing || file.data.read_only });
      status.textContent = [file.editing ? "编辑" : "预览", file.data.encoding.toUpperCase(), file.data.eol,
        dirty(file) ? "未保存" : "已与磁盘同步", preferences.autoSave ? file.autoPaused ? "自动保存已暂停" : "自动保存开启" : "手动保存"].join(" · ");
    } else status.textContent = "仅支持 2 MiB 以内的 UTF-8 文本";
  }
  function activate(file) {
    if (current?.model && editor) current.viewState = editor.saveViewState();
    current = file;
    if (editor) {
      editor.setModel(file.model || null);
      if (file.viewState) editor.restoreViewState(file.viewState);
      editor.layout();
    }
    render();
    tabNodes.get(file.key)?.node.scrollIntoView?.({block: "nearest", inline: "nearest"});
  }
  function capturePosition() {
    if (!current?.model || !editor) return null;
    const selection = editor.getSelection();
    return JSON.parse(JSON.stringify({projectId: current.projectId, path: current.path,
      selection: selection && {startLineNumber: selection.startLineNumber, startColumn: selection.startColumn,
        endLineNumber: selection.endLineNumber, endColumn: selection.endColumn},
      viewState: editor.saveViewState()}));
  }
  function nextDocumentVersion(file) {
    file.version = (documentVersions.get(file.key) || 0) + 1; documentVersions.set(file.key, file.version);
  }
  function newDocumentEpoch(file) {
    file.epoch = (documentEpochs.get(file.key) || 0) + 1; documentEpochs.set(file.key, file.epoch); nextDocumentVersion(file);
  }
  function documentSnapshot(file = current) {
    if (!file?.model || file.loading || !file.data?.encoding?.startsWith("utf-8")) return null;
    let language = file.model.getLanguageId();
    if (language === "javascript" && /\.jsx$/i.test(file.path)) language = "javascriptreact";
    if (language === "typescript" && /\.tsx$/i.test(file.path)) language = "typescriptreact";
    return {projectId: file.projectId, path: file.path, language, version: file.version, epoch: file.epoch, text: file.model.getValue()};
  }
  function applySelection(selection) {
    if (!selection || !current?.model) return;
    const range = "startLineNumber" in selection ? selection : {
      startLineNumber: selection.lineNumber, startColumn: selection.column,
      endLineNumber: selection.lineNumber, endColumn: selection.column};
    const bounded = current.model.validateRange(range);
    editor.setSelection(bounded);
    return bounded;
  }
  async function ensureEditor() {
    const loaded = await loadWorkspaceMonaco();
    if (disposed) return false;
    if (!editor) {
      monaco = loaded;
      editor = monaco.editor.create(canvas, { model: null, automaticLayout: true, theme: "vs",
        fontFamily: "Consolas, Menlo, monospace", fontSize: 13, minimap: { enabled: false },
        scrollBeyondLastLine: false, readOnly: true, fixedOverflowWidgets: true,
        unicodeHighlight: { ambiguousCharacters: false }, padding: { top: 12, bottom: 12 } });
      opener = monaco.editor.registerEditorOpener({async openCodeEditor(source, resource, selection) {
        if (source !== editor || disposed) return false;
        const target = Array.from(files.values()).find(file => file.model?.uri.toString() === resource.toString());
        if (!target) return false;
        return openTarget(target.projectId, target.path, selection);
      }});
      const languages = monaco.languages.getLanguages().sort((a, b) => a.id.localeCompare(b.id));
      language.replaceChildren(...languages.map(item => el("option", { value: item.id, text: item.aliases?.[0] || item.id })));
      editor.onDidChangeCursorPosition(event => {
        if (!current?.model) return;
        status.textContent = status.textContent.split(" · 行 ")[0] + ` · 行 ${event.position.lineNumber}, 列 ${event.position.column}`;
      });
      navigation = createCodeNavigation({monaco, editor, canvas, client, snapshot: documentSnapshot, openTarget,
        history, onError: callbacks.error});
    }
    return true;
  }
  async function load(file, {deferAnalysis = false} = {}) {
    const generation = file.generation = (file.generation || 0) + 1;
    const valid = () => !disposed && files.get(file.key) === file && file.generation === generation;
    file.loading = true; file.notice = ""; render();
    file.loadError = null;
    try {
      const [data, ready] = await Promise.all([callbacks.api(endpoint(file), { silent: true }), ensureEditor()]);
      if (!ready || !valid()) return false;
      client.close(file.projectId, file.path);
      file.model?.dispose(); file.change?.dispose();
      file.data = data; file.saved = data.content;
      const uri = monaco.Uri.from({ scheme: "inmemory", authority: file.projectId, path: "/" + file.path });
      file.model = monaco.editor.createModel(data.content, workspaceCodeLanguage(monaco, file.path), uri);
      newDocumentEpoch(file);
      file.change = file.model.onDidChangeContent(() => {
        nextDocumentVersion(file); const source = documentSnapshot(file); if (source) client.change(source);
        render(); scheduleAutoSave(file);
      });
      file.autoPaused = false;
      file.editing = preferences.defaultEditing && !data.read_only;
      file.loading = false;
      file.analysisPending = deferAnalysis;
      const source = documentSnapshot(file); if (source && !deferAnalysis) client.open(source);
      if (current === file) activate(file);
      return true;
    } catch (error) {
      if (valid()) { file.loadError = error; file.notice = "打开失败：" + errorText(error); }
      return false;
    } finally { if (valid()) { file.loading = false; render(); } }
  }
  async function open(project, path) {
    if (!project) return false;
    return openTarget(project.id, path);
  }
  async function openTarget(projectId, path, selection, {recordHistory = true, viewState = null, historyMove = false, navigationGuard = null} = {}) {
    if (disposed || navigationGuard && !navigationGuard()) return false;
    const project = callbacks.projects().find(item => item.id === projectId);
    if (!project) {
      if (historyMove) throw {status: 404, detail: "项目已移除"};
      callbacks.error("项目已移除"); return false;
    }
    const key = JSON.stringify([projectId, path]);
    let file = files.get(key);
    if (!file && files.size >= 12) { callbacks.error("最多打开 12 个文件，请先关闭部分标签"); return false; }
    const stamp = ++navigationGeneration;
    if (!file) {
      file = { key, projectId, projectName: project.name, path, editing: false,
        model: null, data: null, saved: "", loading: true, saving: false, notice: "" };
      files.set(key, file);
      render(); show(); file.pending = load(file, {deferAnalysis: true});
    }
    if (file.pending) await file.pending;
    if (disposed || stamp !== navigationGeneration || files.get(key) !== file || navigationGuard && !navigationGuard()) return false;
    if (!file.model) {
      files.delete(key); render();
      if (!files.size) hide();
      if (historyMove && file.loadError?.status === 404) throw file.loadError;
      callbacks.error(file.notice || "无法打开文件"); return false;
    }
    const from = capturePosition();
    // Existing models keep their drafts; failed or superseded loads never change the active model.
    const keepSelection = current === file && !selection ? from?.selection : null;
    activate(file); show();
    if (file.analysisPending) {
      file.analysisPending = false;
      const source = documentSnapshot(file); if (source) client.open(source);
    }
    if (viewState) editor.restoreViewState(viewState);
    const bounded = applySelection(selection || keepSelection);
    if (bounded && !viewState) editor.revealRangeInCenter(bounded);
    editor.focus();
    if (recordHistory) history.record(from, capturePosition());
    try { await callbacks.locate?.(projectId, path); }
    catch (error) { if (!disposed) callbacks.error("文件树定位失败：" + errorText(error)); }
    return !disposed && stamp === navigationGeneration;
  }
  async function saveFile(file) {
    if (!file?.model || file.saving || file.loading || file.data.read_only || !dirty(file)) return false;
    const content = file.model.getValue();
    const revision = file.data.revision;
    clearTimeout(file.autoTimer);
    file.saving = true; file.notice = ""; render();
    try {
      const data = await callbacks.api(endpoint(file), { method: "PUT", body: { content, revision }, silent: true });
      if (disposed || files.get(file.key) !== file) return true;
      // Editing may continue while a save is in flight. Only the submitted text is the new baseline.
      file.saved = data.content; file.data = data; file.notice = dirty(file) ? "已保存，仍有新的未保存修改" : "已保存";
      client.saved(file.projectId, file.path, data.revision);
      file.autoPaused = false;
      return true;
    } catch (error) {
      file.notice = (error.status === 409 ? "保存冲突：" : "保存失败：") + errorText(error);
      file.autoPaused = true;
      return false;
    } finally { file.saving = false; render(); scheduleAutoSave(file); }
  }
  async function reloadFile(file) {
    if (!file || file.loading || file.saving) return;
    if (dirty(file) && !window.confirm("重新加载会丢弃此文件的未保存修改，是否继续？")) return;
    await load(file);
  }
  function closeFile(file) {
    if (file.saving) { file.notice = "请等待保存完成后关闭"; render(); return; }
    if (dirty(file) && !window.confirm("丢弃 “" + file.path + "” 的未保存修改？")) return;
    ++navigationGeneration;
    const leaving = current === file ? capturePosition() : null;
    client.close(file.projectId, file.path);
    clearTimeout(file.autoTimer); files.delete(file.key); file.change?.dispose();
    if (current === file) {
      const next = Array.from(files.values()).at(-1);
      if (next) {
        activate(next); history.record(leaving, capturePosition());
        const source = documentSnapshot(); client.setActive(source?.projectId, source && workspaceLanguageFamily(source.language));
        Promise.resolve(callbacks.locate?.(next.projectId, next.path)).catch(error => callbacks.error(errorText(error)));
      }
      else { current = null; editor?.setModel(null); hide(); history.clear(); }
    }
    if (!files.size) { hide(); history.clear(); }
    file.model?.dispose(); render();
  }
  function closePanel() {
    if (Array.from(files.values()).some(file => file.saving)) { callbacks.error("请等待保存完成后关闭"); return; }
    const changed = Array.from(files.values()).filter(dirty);
    if (changed.length && !window.confirm(`关闭将丢弃 ${changed.length} 个文件的未保存修改，是否继续？`)) return;
    ++navigationGeneration; history.clear();
    for (const file of files.values()) { client.close(file.projectId, file.path); clearTimeout(file.autoTimer); file.change?.dispose(); file.model?.dispose(); }
    files.clear(); current = null; editor?.setModel(null); hide(); render();
  }
  const beforeUnload = event => {
    if (Array.from(files.values()).some(file => dirty(file) || file.saving)) { event.preventDefault(); event.returnValue = ""; }
  };
  window.addEventListener("beforeunload", beforeUnload);
  const shortcut = event => {
    if (disposed || panel.hidden || analysisSettings.isOpen || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.isComposing) return;
    const key = event.key.toLowerCase();
    if (editor?.hasTextFocus() && event.ctrlKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      void history.move(event.key === "ArrowLeft" ? -1 : 1); return;
    }
    if (key !== "s" && key !== "i") return;
    event.preventDefault(); event.stopPropagation();
    if (key === "i") toggleEditing(); else void saveFile(current);
  };
  document.addEventListener("keydown", shortcut, true);
  language.addEventListener("change", () => {
    if (current?.model) {
      client.close(current.projectId, current.path); monaco.editor.setModelLanguage(current.model, language.value);
      newDocumentEpoch(current); const source = documentSnapshot(); if (source) client.open(source);
      client.setActive(current.projectId, source && workspaceLanguageFamily(source.language)); render();
    }
  });
  return { open, openTarget,
    hide() { ++navigationGeneration; reopen = false; hide(); },
    suspend() { reopen = !panel.hidden; hide(); },
    resume() { if (reopen && files.size) show(); reopen = false; },
    dispose() {
      disposed = true; ++navigationGeneration; history.clear(); window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("keydown", shortcut, true);
      opener?.dispose();
      layoutObserver?.disconnect();
      navigation?.dispose(); analysisSettings.dispose(); client.dispose();
      editor?.dispose();
      for (const file of files.values()) { clearTimeout(file.autoTimer); file.change?.dispose(); file.model?.dispose(); }
      files.clear(); hide(); panel.remove();
      document.documentElement.dataset.sonaUnsavedCode = "false";
    },
  };
}
