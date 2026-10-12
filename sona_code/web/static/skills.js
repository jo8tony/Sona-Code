"use strict";
/* Local skill management and shared accessible selection/import dialogs. */

function skillError(error) { return error?.detail || error?.data?.detail || error?.message || String(error); }

function openSpecError(error) {
  const detail = skillError(error);
  return typeof detail === "object" ? [detail.message || "OpenSpec 准备失败", ...(detail.conflicts || [])].join("\n") : String(detail);
}

async function openProjectOpenSpec(project, onChanged, autoEnable = false) {
  const dialog = createSkillDialog(`OpenSpec · ${project.name}`);
  const content = el("div", { class: "wsp-openspec-content" });
  const errorLine = el("div", { class: "banner banner-err wsp-openspec-error", role: "alert", hidden: true });
  dialog.body.append(el("p", { text: "为当前项目启用规范驱动工作流。从需求提案到实现、验证和归档，直接在对话中完成。" }), content, errorLine);
  dialog.body.insertBefore(el("p", { class: "dim", text: "离线内置：无需下载，不联网检查或升级。同步模板仅使用当前客户端携带的版本。" }), content);
  let busy = false, status;
  const labels = { not_enabled: "尚未启用", detected: "已有 OpenSpec", prepared: "正在检查命令", ready: "可用", disabled: "已停用",
    upgrade_available: "内置模板待同步", conflict: "文件冲突", error: "需要检查" };
  function draw() {
    if (!dialog.alive()) return;
    content.replaceChildren();
    if (!status) { content.append(el("p", { text: "正在检查项目…" })); return; }
    content.append(el("p", { class: "wsp-openspec-status", text: busy ? "正在准备，请稍候…" : labels[status.state] || status.state }));
    if (status.state === "ready") content.append(el("p", { text: "在对话中输入 /opsx-propose 开始规划，或输入 /opsx 查看全部工作流。" }));
    if (status.state === "detected") content.append(el("p", { text: "接入时会检查已有文件，并补齐 OpenCode 技能和命令。已有规范和项目配置会保留。" }));
    if (status.error) content.append(el("p", { class: "dim", text: status.error }));
    if (status.terminal_restart_required) content.append(el("p", { text: "请先关闭该项目的 OpenCode 终端，准备完成后重新打开。" }));
    const details = el("details", { class: "wsp-openspec-details" }, el("summary", { text: "版本与工作流详情" }),
      el("p", { text: `内置版本：${status.bundled_version || "不可用"} · 项目版本：${status.version || "尚未接入"}` }),
      el("div", { class: "wsp-openspec-commands" }, ...Object.keys(status.workflows || {}).map(name => el("code", { text: `/${name}` }))));
    for (const path of status.conflicts || []) details.append(el("p", { class: "mono", text: path }));
    content.append(details);
    const actions = el("div", { class: "wsp-modal-actions" });
    actions.append(el("button", { class: "wsp-mini", type: "button", text: "刷新", disabled: busy, onclick: load }));
    if (status.enabled) actions.append(el("button", { class: "wsp-mini", type: "button", text: "停用", disabled: busy, onclick: () => change("disable") }));
    const action = status.enabled ? "update" : "enable";
    actions.append(el("button", { class: "wsp-mini primary", type: "button", disabled: busy || !status.available,
      text: status.enabled ? "同步内置模板" : status.state === "detected" ? "接入到 Sona" : "启用 OpenSpec", onclick: () => change(action) }));
    content.append(actions);
  }
  async function load() {
    try {
      const data = await api(`workspace/projects/${encodeURIComponent(project.id)}/openspec`, { silent: true });
      if (!dialog.alive()) return;
      status = data; errorLine.hidden = true; draw();
    } catch (error) {
      if (dialog.alive()) { errorLine.textContent = openSpecError(error); errorLine.hidden = false; }
    }
  }
  async function change(action) {
    if (busy) return;
    busy = true; errorLine.hidden = true; draw();
    try {
      const data = await api(`workspace/projects/${encodeURIComponent(project.id)}/openspec/${action}`, { method: "POST", body: {}, silent: true });
      if (!dialog.alive()) return;
      status = data;
      await onChanged?.();
      if (dialog.alive()) toast(status.state === "ready" ? "OpenSpec 已就绪" : status.state === "disabled" ? "OpenSpec 已停用" : "项目已准备，请检查加载状态", status.state === "error" ? "error" : "ok");
    } catch (error) {
      if (dialog.alive()) { errorLine.textContent = openSpecError(error); errorLine.hidden = false; }
    } finally { busy = false; draw(); }
  }
  draw();
  await load();
  if (autoEnable && dialog.alive() && status?.available && !status.enabled) await change("enable");
}

async function openOpenSpecProjects() {
  const dialog = createSkillDialog("为项目启用 OpenSpec");
  const content = el("div", null, el("p", { text: "正在读取项目…" }));
  dialog.body.append(content);
  try {
    const data = await api("workspace/projects", { silent: true });
    if (!dialog.alive()) return;
    content.replaceChildren();
    if (!data.items?.length) { content.append(el("p", { text: "请先在工作区添加项目，再通过项目菜单启用 OpenSpec。" })); return; }
    for (const project of data.items) content.append(el("button", { class: "wsp-openspec-project wsp-mini", type: "button",
      text: project.name, title: project.path, onclick: () => { dialog.close(); openProjectOpenSpec(project); } }));
  } catch (error) { if (dialog.alive()) content.replaceChildren(el("p", { text: openSpecError(error) })); }
}

function createSkillDialog(title) {
  const previous = document.activeElement;
  const body = el("div", { class: "wsp-modal skill-dialog", role: "dialog", "aria-modal": "true", "aria-label": title });
  const mask = el("div", { class: "wsp-modal-mask" }, body);
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    mask.remove();
    document.removeEventListener("keydown", onKey);
    if (previous?.isConnected) previous.focus();
  };
  const onKey = (event) => {
    if (event.key === "Escape") { event.preventDefault(); close(); }
    if (event.key !== "Tab") return;
    const controls = Array.from(body.querySelectorAll("button:not(:disabled), input:not(:disabled), a[href]"))
      .filter((node) => !node.hidden && node.getClientRects().length);
    const first = controls[0], last = controls[controls.length - 1];
    if (!first) { event.preventDefault(); return; }
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  body.append(el("div", { class: "skill-dialog-head" }, el("h2", { text: title }),
    el("button", { class: "wsp-mini", type: "button", text: "×", "aria-label": "关闭", onclick: close })));
  mask.addEventListener("click", (event) => { if (event.target === mask) close(); });
  document.addEventListener("keydown", onKey);
  document.body.append(mask);
  addCleanup(close);
  return { body, close, alive: () => !closed && mask.isConnected };
}

function openSkillImport(onImported) {
  const dialog = createSkillDialog("新增技能");
  const pathInput = el("input", { id: "skill-source-path", type: "text", placeholder: "包含 SKILL.md 的技能目录绝对路径",
    autocomplete: "off", spellcheck: "false", "aria-label": "技能目录" });
  const errorLine = el("div", { class: "wsp-question-error", role: "alert" });
  const invoke = window.__TAURI__?.core?.invoke;
  const browseButton = el("button", { class: "wsp-mini", type: "button", text: "选择目录", onclick: async () => {
    browseButton.disabled = true;
    errorLine.textContent = "";
    try {
      const path = await invoke("plugin:dialog|open", { options: { directory: true, multiple: false, title: "选择包含 SKILL.md 的技能目录" } });
      if (dialog.alive() && typeof path === "string") pathInput.value = path;
    } catch (error) {
      if (dialog.alive()) errorLine.textContent = "打开系统目录选择失败：" + skillError(error);
    } finally { browseButton.disabled = false; }
  } });
  const importButton = el("button", { class: "wsp-mini primary", type: "button", text: "导入并启用", onclick: async () => {
    const path = pathInput.value.trim();
    if (!path) { errorLine.textContent = "请指定技能目录"; pathInput.focus(); return; }
    importButton.disabled = true;
    importButton.textContent = "正在复制…";
    errorLine.textContent = "";
    try {
      const skill = await api("skills", { method: "POST", body: { path }, silent: true });
      if (!dialog.alive()) return;
      dialog.close();
      onImported(skill);
    } catch (error) {
      if (dialog.alive()) errorLine.textContent = skillError(error);
    } finally {
      importButton.disabled = false;
      importButton.textContent = "导入并启用";
    }
  } });
  dialog.body.append(el("p", { text: "选择单个技能目录，应用会复制 SKILL.md 及脚本、参考资料等资源。导入后即可在各项目中使用。" }),
    el("div", { class: "wsp-modal-row" }, pathInput,
      invoke ? browseButton : null),
    errorLine, el("div", { class: "wsp-modal-actions" },
      el("button", { class: "wsp-mini", type: "button", text: "取消", onclick: dialog.close }), importButton));
  pathInput.addEventListener("keydown", (event) => { if (event.key === "Enter" && !importButton.disabled) importButton.click(); });
  pathInput.focus();
}

function renderSkills(view) {
  let disposed = false, items = [], loading = true, busy = false, busyId = "", requestedEnabled = false;
  let filter = "all", failedId = "", failure = "", loadVersion = 0;
  const cards = new Map();
  addCleanup(() => { disposed = true; });
  const search = el("input", { type: "search", placeholder: "搜索技能名称或描述", "aria-label": "搜索技能" });
  const count = el("span", { class: "skill-count", role: "status" });
  const errorLine = el("div", { class: "banner banner-err", role: "alert", hidden: true });
  const list = el("div", { class: "skill-list", "aria-label": "技能列表" });
  const refresh = el("button", { class: "skill-refresh", type: "button", title: "刷新技能", "aria-label": "刷新技能",
    onclick: () => load() }, workspaceIcon("refresh"));
  const add = el("button", { class: "btn btn-primary skill-add", type: "button", onclick: () => openSkillImport((skill) => {
    toast(`技能 ${skill.name} 已导入并启用`, "ok"); load();
  }) }, workspaceIcon("plus"), "新增技能");
  const filters = [["all", "全部"], ["enabled", "已启用"], ["disabled", "已停用"]].map(([value, text]) =>
    el("button", { class: "skill-filter", type: "button", text, "aria-pressed": String(value === filter),
      onclick: () => { filter = value; draw(); } }));
  view.replaceChildren(el("section", { class: "skills-page" },
    el("div", { class: "skill-page-head" },
      el("div", null, el("h1", { text: "技能" }), el("p", { text: "把常用指令和工作流程，变成可复用的技能。" })), add),
    el("article", { class: "skill-feature" },
      el("span", { class: "skill-feature-icon", "aria-hidden": "true" }, workspaceIcon("skill")),
      el("div", { class: "skill-card-copy" }, el("div", { class: "skill-card-title" }, el("h2", { text: "OpenSpec" }), el("span", { class: "skill-state", text: "内置" })),
        el("p", { text: "12 个规范驱动工作流，覆盖提案、实现、验证与归档。" })),
      el("button", { class: "skill-feature-action", type: "button", onclick: openOpenSpecProjects }, "选择项目", workspaceIcon("chevron"))),
    errorLine, el("div", { class: "skill-toolbar" },
      el("div", { class: "skill-search" }, workspaceIcon("search"), search),
      el("div", { class: "skill-filters", "aria-label": "筛选技能" }, filters), refresh),
    el("div", { class: "skill-list-head" }, el("h2", { text: "我的技能" }), count), list));

  function statusLabel(item) {
    return item.error ? "格式错误" : item.conflict ? "同名冲突" : !item.enabled ? "已停用" : item.permission === "ask" ? "需确认" : "已启用";
  }
  function details(item) {
    const dialog = createSkillDialog(item.name);
    dialog.body.append(el("p", { class: "skill-detail-description", text: item.description || "该技能暂未提供描述。" }),
      el("dl", { class: "skill-detail-meta" },
        el("dt", { text: "来源" }), el("dd", { text: item.source === "app" ? "本应用 · 导入副本" : `本机 ${item.origin || "OpenCode"} · 原目录读取` }),
        el("dt", { text: "状态" }), el("dd", { text: statusLabel(item) }),
        el("dt", { text: "目录" }), el("dd", {}, el("code", { class: "skill-storage-path", text: item.path }))));
    if (item.error || item.conflict) dialog.body.append(el("p", { class: "banner banner-err", text: item.error || item.conflict }));
    dialog.body.append(el("div", { class: "wsp-modal-actions" }, el("button", { class: "wsp-mini", type: "button", text: "关闭", onclick: dialog.close })));
    dialog.body.querySelector("button").focus();
  }
  function remove(item) {
    const dialog = createSkillDialog("删除技能");
    dialog.body.append(el("p", { text: `删除应用中的「${item.name}」副本？源目录不会被删除。` }),
      el("div", { class: "wsp-modal-actions" }, el("button", { class: "wsp-mini", type: "button", text: "取消", onclick: dialog.close }),
        el("button", { class: "wsp-mini", type: "button", text: "删除副本", onclick: () => { dialog.close(); change(item, "DELETE"); } })));
    dialog.body.querySelector("button").focus();
  }
  function card(item) {
    let row = cards.get(item.id);
    if (!row) {
      const name = el("h2", { class: "skill-card-name" });
      const source = el("span", { class: "skill-source" });
      const description = el("p", { class: "skill-card-description" });
      const status = el("span", { class: "skill-card-status" });
      const issue = el("p", { class: "skill-card-error", role: "alert", hidden: true });
      const toggle = el("button", { class: "skill-switch", type: "button", role: "switch",
        onclick: () => change(row.item, "PATCH", { enabled: !row.item.enabled }) },
        el("span", { class: "skill-switch-track", "aria-hidden": "true" }));
      const removeButton = el("button", { class: "skill-remove", type: "button", onclick: () => remove(row.item) }, workspaceIcon("trash"));
      const detailsButton = el("button", { class: "skill-details", type: "button", onclick: () => details(row.item) }, "查看详情", workspaceIcon("chevron"));
      const node = el("article", { class: "skill-card" },
        el("div", { class: "skill-card-head" }, el("span", { class: "skill-card-icon", "aria-hidden": "true" }, workspaceIcon("skill")),
          el("div", { class: "skill-card-copy" }, name, source), removeButton), description, issue,
        el("div", { class: "skill-card-foot" }, detailsButton, el("div", { class: "skill-card-actions" }, status, toggle)));
      row = { node, name, source, description, status, issue, toggle, removeButton, detailsButton };
      cards.set(item.id, row);
    }
    row.item = item;
    const pending = busy && busyId === item.id;
    const invalid = !!item.error || !!item.conflict;
    row.node.className = "skill-card" + (!item.enabled ? " skill-disabled" : "") + (invalid ? " skill-invalid" : "");
    row.node.setAttribute("aria-label", item.name);
    row.name.textContent = item.name;
    row.source.textContent = item.source === "app" ? "本应用" : item.origin || "本机技能";
    row.source.title = item.source === "app" ? "应用导入的技能副本" : "从本机原目录读取";
    row.description.textContent = item.error || item.conflict || item.description || "该技能暂未提供描述。";
    row.status.textContent = pending ? (requestedEnabled ? "正在启用…" : "正在停用…") : statusLabel(item);
    if (pending && requestedEnabled === null) row.status.textContent = "正在删除…";
    row.status.className = "skill-card-status" + (invalid ? " warning" : item.enabled ? " enabled" : "");
    row.toggle.setAttribute("aria-checked", String(!!item.enabled && !invalid));
    row.toggle.setAttribute("aria-label", `启用 ${item.name}`);
    row.toggle.setAttribute("aria-busy", String(pending));
    row.toggle.title = item.error || item.conflict || (item.enabled ? "停用此技能" : "启用此技能");
    row.toggle.disabled = busy || loading || invalid;
    row.removeButton.hidden = !item.deletable;
    row.removeButton.disabled = busy || loading;
    row.removeButton.title = `删除 ${item.name} 的应用副本`;
    row.removeButton.setAttribute("aria-label", `删除 ${item.name}`);
    row.detailsButton.setAttribute("aria-label", `查看 ${item.name} 详情`);
    row.issue.hidden = failedId !== item.id || !failure;
    row.issue.textContent = failedId === item.id ? failure : "";
    return row.node;
  }
  function draw() {
    const query = search.value.trim().toLocaleLowerCase();
    const enabled = item => item.enabled && !item.error && !item.conflict;
    const matches = items.filter(item => `${item.name} ${item.description}`.toLocaleLowerCase().includes(query) &&
      (filter === "all" || (filter === "enabled" ? enabled(item) : !item.enabled)));
    count.textContent = `${items.length} 个技能 · ${items.filter(enabled).length} 个已启用`;
    add.disabled = busy || loading;
    refresh.disabled = busy || loading;
    refresh.classList.toggle("loading", loading);
    refresh.setAttribute("aria-busy", String(loading));
    list.setAttribute("aria-busy", String(loading));
    filters.forEach((button, index) => button.setAttribute("aria-pressed", String(["all", "enabled", "disabled"][index] === filter)));
    let nodes;
    if (loading && !items.length) nodes = [el("div", { class: "skill-empty skill-loading", role: "status", text: "正在读取技能…" })];
    else if (!matches.length) {
      const narrowed = !!query || filter !== "all";
      nodes = [el("div", { class: "skill-empty" },
        el("span", { class: "skill-empty-icon", "aria-hidden": "true" }, workspaceIcon(narrowed ? "search" : "skill")),
        el("h2", { text: narrowed ? "没有匹配的技能" : "添加你的第一个技能" }),
        el("p", { text: narrowed ? "试试其他关键词，或切换到全部技能。" : "导入一个包含 SKILL.md 的目录，让常用工作流程随时可用。" }),
        el("button", { class: "skill-empty-action", type: "button", disabled: busy || loading,
          text: narrowed ? "清除筛选" : "新增技能", onclick: () => {
            if (narrowed) { search.value = ""; filter = "all"; draw(); search.focus(); }
            else add.click();
          } }))];
    } else nodes = matches.map(card);
    // Keep card controls mounted across API state changes so focus is retained.
    const keep = new Set(nodes);
    for (const child of Array.from(list.children)) if (!keep.has(child)) child.remove();
    nodes.forEach((node, index) => { if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null); });
    for (const id of cards.keys()) if (!items.some(item => item.id === id)) cards.delete(id);
  }
  async function load() {
    const version = ++loadVersion;
    loading = true; draw();
    try {
      const data = await api("skills", { silent: true });
      if (disposed || version !== loadVersion) return;
      items = data.items || [];
      errorLine.textContent = (data.warnings || []).join("；");
      errorLine.hidden = !errorLine.textContent;
    } catch (error) {
      if (disposed || version !== loadVersion) return;
      errorLine.textContent = "读取技能失败：" + skillError(error); errorLine.hidden = false;
    } finally { if (!disposed && version === loadVersion) { loading = false; draw(); } }
  }
  async function change(item, method, body) {
    if (busy || loading || disposed) return;
    const focused = document.activeElement;
    busy = true; busyId = item.id; requestedEnabled = body?.enabled ?? null;
    failedId = ""; failure = ""; draw();
    try {
      await api(`skills/${encodeURIComponent(item.id)}`, { method, body, silent: true });
      if (disposed) return;
      if (method === "DELETE") items = items.filter(entry => entry.id !== item.id);
      else items = items.map(entry => entry.id === item.id ? { ...entry, enabled: body.enabled } : entry);
      toast(method === "DELETE" ? "技能副本已删除" : body.enabled ? "技能已启用" : "技能已停用", "ok");
      await load();
    } catch (error) {
      if (!disposed) { failedId = item.id; failure = String(skillError(error)); }
    } finally {
      busy = false; busyId = "";
      if (!disposed) {
        draw();
        if (focused?.classList.contains("skill-switch") && document.activeElement === document.body) {
          if (focused.isConnected && !focused.disabled) focused.focus();
          else filters.find(button => button.getAttribute("aria-pressed") === "true")?.focus();
        }
      }
    }
  }
  search.addEventListener("input", draw);
  draw(); load();
}
