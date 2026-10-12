"use strict";

function createAnalysisSettings({api, projectId, onSaved, onError}) {
  const dialog = el("dialog", {class: "wsp-analysis-settings", "aria-label": "项目分析设置"});
  const mode = el("select", {}, ...[["source", "源码模式"], ["maven", "Maven 离线导入"], ["gradle", "Gradle 离线导入"]]
    .map(([value, text]) => el("option", {value, text})));
  const sourceRoots = el("textarea", {rows: "3", placeholder: "src/main/java"});
  const libraries = el("textarea", {rows: "3", placeholder: "lib/example.jar"});
  const jdk = el("input", {type: "text", placeholder: "可选，项目使用的本地 JDK 目录"});
  const gradle = el("input", {type: "text", placeholder: "Gradle 离线导入时必填"});
  const interpreter = el("input", {type: "text", placeholder: "可选，Python 或虚拟环境解释器的完整路径"});
  const hint = el("p", {class: "wsp-analysis-hint", text: "源码模式直接分析本地源码与 JAR。构建导入只使用本地依赖；Gradle 模式会评估项目构建脚本。"});
  const field = (label, input) => el("label", {}, el("span", {text: label}), input);
  const save = el("button", {type: "button", text: "保存设置", onclick: () => { void submit(); }});
  const cancel = el("button", {type: "button", text: "取消", onclick: () => dialog.close()});
  const form = el("form", {}, el("h3", {text: "项目分析"}), field("Java 分析模式", mode), hint,
    field("源码根目录（项目内相对路径，每行一个）", sourceRoots), field("依赖 JAR（项目内相对路径，每行一个）", libraries),
    field("项目 JDK", jdk), field("本地 Gradle 目录", gradle), field("Python 解释器", interpreter),
    el("div", {class: "wsp-analysis-actions"}, cancel, save));
  dialog.append(form); document.body.append(dialog);
  let generation = 0, currentProject = null, disposed = false, saving = false;
  form.addEventListener("submit", event => {event.preventDefault(); void submit();});
  dialog.addEventListener("close", () => { ++generation; });
  const lines = input => input.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const optional = input => input.value.trim() || null;
  async function submit() {
    if (disposed || saving || !currentProject) return;
    const stamp = generation, id = currentProject;
    saving = true; save.disabled = true;
    try {
      await api("workspace/projects/" + encodeURIComponent(id) + "/analysis-settings", {method: "PUT", silent: true, body: {
        java: {mode: mode.value, source_roots: lines(sourceRoots), libraries: lines(libraries),
          project_jdk: optional(jdk), gradle_home: optional(gradle)}, python: {interpreter: optional(interpreter)}}});
      if (disposed || stamp !== generation) return;
      onSaved(id); dialog.close();
    } catch (error) { if (!disposed && stamp === generation) onError(typeof error.detail === "string" ? error.detail : error.message || "保存分析设置失败"); }
    finally { saving = false; save.disabled = false; }
  }
  return {async open() {
    const id = projectId(); if (disposed || !id || saving) return;
    const stamp = ++generation;
    try {
      const settings = await api("workspace/projects/" + encodeURIComponent(id) + "/analysis-settings", {silent: true});
      if (disposed || stamp !== generation) return;
      currentProject = id; mode.value = settings.java.mode;
      sourceRoots.value = settings.java.source_roots.join("\n"); libraries.value = settings.java.libraries.join("\n");
      jdk.value = settings.java.project_jdk || ""; gradle.value = settings.java.gradle_home || "";
      interpreter.value = settings.python.interpreter || "";
      if (!dialog.open) dialog.showModal();
    } catch (error) { if (!disposed && stamp === generation) onError(error.message || "读取分析设置失败"); }
  }, get isOpen() {return dialog.open;}, dispose() {disposed = true; ++generation; dialog.close(); dialog.remove();}};
}
