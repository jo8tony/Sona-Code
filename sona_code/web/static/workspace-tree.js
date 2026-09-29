"use strict";

function workspaceIcon(name) {
  const paths = {
    pencil: ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4L16.5 3.5Z"],
    folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"],
    file: ["M6 3h8l4 4v14H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z", "M14 3v5h5", "M8 13h8M8 17h6"],
    tree: ["M5 4v16", "M5 8h4M5 16h4", "M10 5h10v6H10z", "M10 13h10v6H10z"],
    trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 14h10l1-14", "M10 11v6M14 11v6"],
    fork: ["M7 3v7a5 5 0 0 0 5 5h3", "M17 3v7a5 5 0 0 1-5 5", "M12 15v6", "M5 3h4M15 3h4M10 21h4"],
    open: ["M14 4h6v6", "M20 4l-9 9", "M19 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6"],
    reveal: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z", "M8 13h8M8 16h5"],
    globe: ["M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Z", "M2 12h20", "M12 2c2.5 2.7 4 6.2 4 10s-1.5 7.3-4 10", "M12 2c-2.5 2.7-4 6.2-4 10s1.5 7.3 4 10"],
    reference: ["M16 8v7a3 3 0 0 0 6 0v-3a10 10 0 1 0-3.5 7.6", "M16 12a4 4 0 1 0-8 0 4 4 0 0 0 8 0Z"],
    locate: ["M12 2v3M12 19v3M2 12h3M19 12h3", "M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z", "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"],
  };
  return el("svg", { class: "wsp-icon", viewBox: "0 0 24 24", "aria-hidden": "true" },
    ...(paths[name] || paths.file).map((d) => el("path", { d })));
}

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
    const icons = { "打开": "open", "在文件管理器中显示": "reveal",
      "在浏览器中打开": "globe", "添加到对话引用": "reference" };
    for (const [label, action] of actions) menu.append(el("button", {
      type: "button", role: "menuitem",
      onclick: () => { closeMenu(); action(); },
    }, el("span", { class: "wsp-action-icon" }, workspaceIcon(icons[label])),
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
      el("span", { class: "wsp-tree-kind" }, workspaceIcon(item.directory ? "folder" : "file")),
      el("span", { class: "wsp-tree-name", text: item.name }),
      !item.directory && browserFile(item.path) ? el("button", {
        class: "wsp-tree-browser", type: "button",
        title: "在浏览器中打开", "aria-label": "在浏览器中打开 " + item.name,
        onclick: (event) => { event.stopPropagation(); void callbacks.open(project.id, item.path, "browser"); },
      }, workspaceIcon("globe")) : null);
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
