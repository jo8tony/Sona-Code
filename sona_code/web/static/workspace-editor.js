"use strict";

function createWorkspaceEditor(root, callbacks) {
  const files = new Map();
  const errorText = error => typeof error.detail === "string" ? error.detail : error.message;
  let current = null, monaco = null, editor = null, opener = null, disposed = false, reopen = false;
  let preferences = {defaultEditing: false, autoSave: false};
  try {
    const saved = JSON.parse(localStorage.getItem("sona-code:editor-preferences") || "null");
    if (saved) preferences = {defaultEditing: saved.defaultEditing === true, autoSave: saved.autoSave === true};
  } catch (_) {}
  const panel = el("section", { class: "wsp-editor", hidden: true, "aria-label": "项目代码编辑器" });
  const tabs = el("div", { class: "wsp-editor-tabs", role: "tablist", "aria-label": "已打开文件" });
  const title = el("strong", { class: "wsp-editor-path" });
  const status = el("span", { role: "status", "aria-live": "polite" });
  const message = el("p", { class: "wsp-editor-message", role: "status" });
  const canvas = el("div", { class: "wsp-editor-canvas" });
  const language = el("select", { "aria-label": "代码语言" });
  const button = (text, action, attrs = {}) => el("button", { type: "button", text, onclick: action, ...attrs });
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
  panel.append(el("header", { class: "wsp-editor-toolbar" }, title, language, mode, save, format, reload, systemOpen, close),
    options, tabs, message, canvas, el("footer", { class: "wsp-editor-status" }, status,
      el("span", { text: "Ctrl/Cmd+I 预览/编辑 · Ctrl/Cmd+S 保存 · Ctrl/Cmd+F 查找" })));
  root.append(panel);

  function endpoint(file, resource = "file") {
    return "workspace/projects/" + encodeURIComponent(file.projectId) + "/" + resource + "?path=" + encodeURIComponent(file.path);
  }
  function dirty(file) { return !!file.model && file.model.getValue() !== file.saved; }
  function show() {
    if (!disposed) { panel.hidden = false; callbacks.visibility?.(true); }
    requestAnimationFrame(() => editor?.layout());
  }
  function hide() { panel.hidden = true; callbacks.visibility?.(false); }
  function render() {
    if (disposed) return;
    document.documentElement.dataset.sonaUnsavedCode = String(Array.from(files.values()).some(file => dirty(file) || file.saving));
    tabs.replaceChildren(...Array.from(files.values(), file => {
      const select = button((dirty(file) ? "● " : "") + file.path.split("/").at(-1), () => activate(file), {
        role: "tab", "aria-selected": String(current === file), title: file.projectName + "/" + file.path,
      });
      return el("div", { class: "wsp-editor-tab" + (current === file ? " active" : "") }, select,
        button("×", () => closeFile(file), { "aria-label": "关闭文件 " + file.path, title: "关闭此文件" }));
    }));
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
      opener = monaco.editor.registerEditorOpener({openCodeEditor(source, resource, selection) {
        if (source !== editor || disposed) return false;
        const target = Array.from(files.values()).find(file => file.model?.uri.toString() === resource.toString());
        if (!target) return false;
        activate(target); show();
        if (selection) {
          if ("startLineNumber" in selection) { editor.setSelection(selection); editor.revealRangeInCenter(selection); }
          else { editor.setPosition(selection); editor.revealPositionInCenter(selection); }
        }
        editor.focus(); return true;
      }});
      const languages = monaco.languages.getLanguages().sort((a, b) => a.id.localeCompare(b.id));
      language.replaceChildren(...languages.map(item => el("option", { value: item.id, text: item.aliases?.[0] || item.id })));
      editor.onDidChangeCursorPosition(event => {
        if (!current?.model) return;
        status.textContent = status.textContent.split(" · 行 ")[0] + ` · 行 ${event.position.lineNumber}, 列 ${event.position.column}`;
      });
    }
    return true;
  }
  async function load(file) {
    const generation = file.generation = (file.generation || 0) + 1;
    const valid = () => !disposed && files.get(file.key) === file && file.generation === generation;
    file.loading = true; file.notice = ""; render();
    try {
      const [data, ready] = await Promise.all([callbacks.api(endpoint(file), { silent: true }), ensureEditor()]);
      if (!ready || !valid()) return;
      file.model?.dispose(); file.change?.dispose();
      file.data = data; file.saved = data.content;
      const uri = monaco.Uri.from({ scheme: "inmemory", authority: file.projectId, path: "/" + file.path });
      file.model = monaco.editor.createModel(data.content, workspaceCodeLanguage(monaco, file.path), uri);
      file.change = file.model.onDidChangeContent(() => { render(); scheduleAutoSave(file); });
      file.autoPaused = false;
      file.editing = preferences.defaultEditing && !data.read_only;
      if (current === file) activate(file);
    } catch (error) {
      if (valid()) file.notice = "打开失败：" + errorText(error);
    } finally { if (valid()) { file.loading = false; render(); } }
  }
  async function open(project, path) {
    if (disposed || !project) return;
    const key = JSON.stringify([project.id, path]);
    let file = files.get(key);
    if (!file) {
      if (files.size >= 12) { callbacks.error("最多打开 12 个文件，请先关闭部分标签"); return; }
      file = { key, projectId: project.id, projectName: project.name, path, editing: false,
        model: null, data: null, saved: "", loading: true, saving: false, notice: "" };
      files.set(key, file);
      activate(file); show(); await load(file);
    } else { activate(file); show(); }
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
    clearTimeout(file.autoTimer); files.delete(file.key); file.change?.dispose();
    if (current === file) {
      const next = Array.from(files.values()).at(-1);
      if (next) activate(next);
      else { current = null; editor?.setModel(null); hide(); }
    }
    file.model?.dispose(); render();
  }
  function closePanel() {
    if (Array.from(files.values()).some(file => file.saving)) { callbacks.error("请等待保存完成后关闭"); return; }
    const changed = Array.from(files.values()).filter(dirty);
    if (changed.length && !window.confirm(`关闭将丢弃 ${changed.length} 个文件的未保存修改，是否继续？`)) return;
    for (const file of files.values()) { clearTimeout(file.autoTimer); file.change?.dispose(); file.model?.dispose(); }
    files.clear(); current = null; editor?.setModel(null); hide(); render();
  }
  const beforeUnload = event => {
    if (Array.from(files.values()).some(file => dirty(file) || file.saving)) { event.preventDefault(); event.returnValue = ""; }
  };
  window.addEventListener("beforeunload", beforeUnload);
  const shortcut = event => {
    if (disposed || panel.hidden || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.isComposing) return;
    const key = event.key.toLowerCase();
    if (key !== "s" && key !== "i") return;
    event.preventDefault(); event.stopPropagation();
    if (key === "i") toggleEditing(); else void saveFile(current);
  };
  document.addEventListener("keydown", shortcut, true);
  language.addEventListener("change", () => {
    if (current?.model) monaco.editor.setModelLanguage(current.model, language.value);
  });
  return { open,
    suspend() { reopen = !panel.hidden; hide(); },
    resume() { if (reopen && files.size) show(); reopen = false; },
    dispose() {
      disposed = true; window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("keydown", shortcut, true);
      opener?.dispose();
      editor?.dispose();
      for (const file of files.values()) { clearTimeout(file.autoTimer); file.change?.dispose(); file.model?.dispose(); }
      files.clear(); hide(); panel.remove();
      document.documentElement.dataset.sonaUnsavedCode = "false";
    },
  };
}
