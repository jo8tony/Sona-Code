"use strict";

function createWorkspaceTree(pane, callbacks) {
  const body = pane.querySelector("#wsp-tree-body");
  const title = pane.querySelector("#wsp-tree-title");
  const items = new Map();
  const expanded = new Set();
  let project = null;
  let selected = "";
  let menu = null;
  const browserFile = (path) => /\.(html?|svg|pdf|txt|xml|css|m?js|json|md|png|jpe?g|gif|webp)$/i.test(path);

  function closeMenu() { menu?.remove(); menu = null; }

  async function load(path = "") {
    if (!project) return;
    const current = project.id;
    try {
      const data = await callbacks.list(current, path);
      if (project?.id !== current) return;
      items.set(path, data.items || []);
      render();
    } catch (error) { callbacks.error("读取目录树失败：" + error.message); }
  }

  function showMenu(event, item) {
    event.preventDefault();
    closeMenu();
    menu = el("div", { class: "wsp-tree-menu", role: "menu" });
    const actions = [
      ["打开", () => callbacks.open(project.id, item.path, "default")],
      ["在文件管理器中显示", () => callbacks.open(project.id, item.path, "reveal")],
      ...(item.directory || !browserFile(item.path) ? [] :
        [["在浏览器中打开", () => callbacks.open(project.id, item.path, "browser")]]),
      ["添加到对话引用", () => callbacks.reference(item.path)],
    ];
    const icons = { "打开": "↗", "在文件管理器中显示": "▣",
      "在浏览器中打开": "🌐", "添加到对话引用": "@" };
    for (const [label, action] of actions) menu.append(el("button", {
      type: "button", role: "menuitem",
      onclick: () => { closeMenu(); action(); },
    }, el("span", { class: "wsp-action-icon", text: icons[label], "aria-hidden": "true" }),
    el("span", { text: label })));
    document.body.append(menu);
    menu.style.left = Math.max(8, Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 8)) + "px";
    menu.style.top = Math.max(8, Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 8)) + "px";
  }

  function branch(path, depth) {
    return (items.get(path) || []).map((item) => {
      const row = el("div", {
        class: "wsp-tree-row" + (selected === item.path ? " selected" : ""),
        role: "treeitem", "aria-level": String(depth + 1),
        "aria-expanded": item.directory ? String(expanded.has(item.path)) : null,
        title: item.path, draggable: true,
        onclick: () => {
          selected = item.path;
          if (item.directory) {
            if (expanded.has(item.path)) expanded.delete(item.path);
            else { expanded.add(item.path); void load(item.path); }
          }
          render();
        },
        ondblclick: () => { if (!item.directory) void callbacks.open(project.id, item.path, "default"); },
        oncontextmenu: (event) => showMenu(event, item),
        ondragstart: (event) => {
          event.dataTransfer.setData("application/x-sona-project-reference",
            JSON.stringify({ projectId: project.id, path: item.path }));
          event.dataTransfer.setData("text/plain", "@" + item.path);
          event.dataTransfer.effectAllowed = "copy";
        },
      },
      el("span", { class: "wsp-tree-toggle", text: item.directory ? expanded.has(item.path) ? "⌄" : "›" : "" }),
      el("span", { class: "wsp-tree-kind", text: item.directory ? "▣" : "▤" }),
      el("span", { class: "wsp-tree-name", text: item.name }),
      !item.directory && browserFile(item.path) ? el("button", {
        class: "wsp-tree-browser", type: "button", text: "🌐",
        title: "在浏览器中打开", "aria-label": "在浏览器中打开 " + item.name,
        onclick: (event) => { event.stopPropagation(); void callbacks.open(project.id, item.path, "browser"); },
      }) : null);
      row.style.paddingLeft = 8 + depth * 15 + "px";
      return el("div", null, row,
        item.directory && expanded.has(item.path) ? el("div", { role: "group" }, ...branch(item.path, depth + 1)) : null);
    });
  }

  function render() {
    pane.hidden = !project;
    if (!project) return;
    title.textContent = project.name;
    body.replaceChildren(...branch("", 0));
    if (!body.childNodes.length) body.append(el("p", { class: "wsp-tree-empty", text: "目录为空或正在加载…" }));
  }

  function open(projectInfo) {
    if (project?.id !== projectInfo.id) { items.clear(); expanded.clear(); selected = ""; }
    project = projectInfo;
    render();
    return load();
  }

  async function locate(projectInfo, path) {
    await open(projectInfo);
    const normalized = path.replaceAll("\\", "/");
    let parent = "";
    for (const part of normalized.split("/").slice(0, -1)) {
      parent = parent ? parent + "/" + part : part;
      expanded.add(parent);
      if (!items.has(parent)) await load(parent);
    }
    selected = normalized;
    render();
    body.querySelector(".wsp-tree-row.selected")?.scrollIntoView({ block: "center" });
  }

  function close() { project = null; closeMenu(); render(); }
  pane.querySelector("#wsp-tree-close").addEventListener("click", close);
  pane.querySelector("#wsp-tree-refresh").addEventListener("click", () => { items.clear(); void load(); });
  const outsideMenu = (event) => { if (menu && !menu.contains(event.target)) closeMenu(); };
  document.addEventListener("pointerdown", outsideMenu);
  return { open, close, locate,
    dispose() { closeMenu(); document.removeEventListener("pointerdown", outsideMenu); },
    get projectId() { return project?.id; } };
}
