"use strict";

// Lucide SVG subset, ISC license. Source: lucide-icons/lucide@5a92b9ba262de5bf10e864219883267672c05db8.
// Keep the original geometry; size and stroke are controlled by workspace.css.
const WORKSPACE_ICONS = {
  undo: [["path", {"d": "M9 14 4 9l5-5"}], ["path", {"d": "M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"}]],
  busy: [["path", {"d": "M21 12a9 9 0 1 1-6.219-8.56"}]],
  fork: [["path", {"d": "M15 6a9 9 0 0 0-9 9V3"}], ["circle", {"cx": "18", "cy": "6", "r": "3"}], ["circle", {"cx": "6", "cy": "18", "r": "3"}]],
  pencil: [["path", {"d": "M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"}], ["path", {"d": "m15 5 4 4"}]],
  folder: [["path", {"d": "M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"}]],
  folderOpen: [["path", {"d": "m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"}]],
  file: [["path", {"d": "M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"}], ["path", {"d": "M14 2v5a1 1 0 0 0 1 1h5"}]],
  fileCode: [["path", {"d": "M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"}], ["path", {"d": "M14 2v5a1 1 0 0 0 1 1h5"}], ["path", {"d": "M10 12.5 8 15l2 2.5"}], ["path", {"d": "m14 12.5 2 2.5-2 2.5"}]],
  tree: [["path", {"d": "M20 10a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1h-2.5a1 1 0 0 1-.8-.4l-.9-1.2A1 1 0 0 0 15 3h-2a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1Z"}], ["path", {"d": "M20 21a1 1 0 0 0 1-1v-3a1 1 0 0 0-1-1h-2.9a1 1 0 0 1-.88-.55l-.42-.85a1 1 0 0 0-.92-.6H13a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1Z"}], ["path", {"d": "M3 5a2 2 0 0 0 2 2h3"}], ["path", {"d": "M3 3v13a2 2 0 0 0 2 2h3"}]],
  trash: [["path", {"d": "M10 11v6"}], ["path", {"d": "M14 11v6"}], ["path", {"d": "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"}], ["path", {"d": "M3 6h18"}], ["path", {"d": "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"}]],
  open: [["path", {"d": "M15 3h6v6"}], ["path", {"d": "M10 14 21 3"}], ["path", {"d": "M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"}]],
  reveal: [["path", {"d": "M10.7 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2v4.1"}], ["path", {"d": "m21 21-1.9-1.9"}], ["circle", {"cx": "17", "cy": "17", "r": "3"}]],
  globe: [["circle", {"cx": "12", "cy": "12", "r": "10"}], ["path", {"d": "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"}], ["path", {"d": "M2 12h20"}]],
  reference: [["circle", {"cx": "12", "cy": "12", "r": "4"}], ["path", {"d": "M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"}]],
  locate: [["path", {"d": "M3 7V5a2 2 0 0 1 2-2h2"}], ["path", {"d": "M17 3h2a2 2 0 0 1 2 2v2"}], ["path", {"d": "M21 17v2a2 2 0 0 1-2 2h-2"}], ["path", {"d": "M7 21H5a2 2 0 0 1-2-2v-2"}], ["circle", {"cx": "12", "cy": "12", "r": "1"}], ["path", {"d": "M18.944 12.33a1 1 0 0 0 0-.66 7.5 7.5 0 0 0-13.888 0 1 1 0 0 0 0 .66 7.5 7.5 0 0 0 13.888 0"}]],
  copy: [["rect", {"width": "14", "height": "14", "x": "8", "y": "8", "rx": "2", "ry": "2"}], ["path", {"d": "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"}]],
  more: [["circle", {"cx": "12", "cy": "12", "r": "1"}], ["circle", {"cx": "19", "cy": "12", "r": "1"}], ["circle", {"cx": "5", "cy": "12", "r": "1"}]],
  chevron: [["path", {"d": "m9 18 6-6-6-6"}]],
  search: [["path", {"d": "m21 21-4.34-4.34"}], ["circle", {"cx": "11", "cy": "11", "r": "8"}]],
  settings: [["path", {"d": "M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"}], ["circle", {"cx": "12", "cy": "12", "r": "3"}]],
  plus: [["path", {"d": "M5 12h14"}], ["path", {"d": "M12 5v14"}]],
  send: [["path", {"d": "m5 12 7-7 7 7"}], ["path", {"d": "M12 19V5"}]],
  stop: [["rect", {"width": "18", "height": "18", "x": "3", "y": "3", "rx": "2"}]],
  terminal: [["path", {"d": "M12 19h8"}], ["path", {"d": "m4 17 6-6-6-6"}]],
  chevronDown: [["path", {"d": "m6 9 6 6 6-6"}]],
  refresh: [["path", {"d": "M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"}], ["path", {"d": "M21 3v5h-5"}]],
  close: [["path", {"d": "M18 6 6 18"}], ["path", {"d": "m6 6 12 12"}]],
  panelLeft: [["rect", {"width": "18", "height": "18", "x": "3", "y": "3", "rx": "2"}], ["path", {"d": "M9 3v18"}]],
  panelRight: [["rect", {"width": "18", "height": "18", "x": "3", "y": "3", "rx": "2"}], ["path", {"d": "M15 3v18"}]],
  todo: [["path", {"d": "M13 5h8"}], ["path", {"d": "M13 12h8"}], ["path", {"d": "M13 19h8"}], ["path", {"d": "m3 17 2 2 4-4"}], ["rect", {"x": "3", "y": "4", "width": "6", "height": "6", "rx": "1"}]],
  queue: [["path", {"d": "M16 5H3"}], ["path", {"d": "M11 12H3"}], ["path", {"d": "M16 19H3"}], ["path", {"d": "M18 9v6"}], ["path", {"d": "M21 12h-6"}]],
  skill: [["path", {"d": "M10 22V7a1 1 0 0 0-1-1H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5a1 1 0 0 0-1-1H2"}], ["rect", {"x": "14", "y": "2", "width": "8", "height": "8", "rx": "1"}]],
  check: [["path", {"d": "M20 6 9 17l-5-5"}]],
  attention: [["circle", {"cx": "12", "cy": "12", "r": "10"}], ["line", {"x1": "12", "x2": "12", "y1": "8", "y2": "12"}], ["line", {"x1": "12", "x2": "12.01", "y1": "16", "y2": "16"}]],
  error: [["circle", {"cx": "12", "cy": "12", "r": "10"}], ["path", {"d": "m15 9-6 6"}], ["path", {"d": "m9 9 6 6"}]],
  info: [["circle", {"cx": "12", "cy": "12", "r": "10"}], ["path", {"d": "M12 16v-4"}], ["path", {"d": "M12 8h.01"}]],
};

function workspaceIcon(name, className = "wsp-icon") {
  return el("svg", { class: className + " wsp-lucide", viewBox: "0 0 24 24",
    fill: "none", stroke: "currentColor", "stroke-width": 1.7,
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" },
    ...(WORKSPACE_ICONS[name] || WORKSPACE_ICONS.file).map(([tag, attrs]) => el(tag, attrs)));
}

function createWorkspaceTree(pane, callbacks) {
  const body = pane.querySelector("#wsp-tree-body");
  const title = pane.querySelector("#wsp-tree-title");
  const search = pane.querySelector("#wsp-tree-search");
  const sideButtons = pane.querySelectorAll("[data-tree-side]");
  const resizer = pane.querySelector(".wsp-tree-resizer");
  const root = pane.closest(".wsp");
  const items = new Map();
  const expanded = new Set();
  let project = null;
  let selected = "";
  let menu = null;
  let searchRequest = 0;
  let locateRequest = 0;
  let results = null;
  let searchError = "";
  let truncated = false;
  let preferredWidth = null;
  let drag = null;
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

  function itemRow(item, depth = 0) {
    const kind = item.directory ? "folder" : /\.(?:[cm]?[jt]sx?|py|rs|go|java|sh|ps1|html?|css|scss|vue|svelte)$/i.test(item.path) ? "code" :
      /\.(?:png|jpe?g|gif|webp|svg|ico)$/i.test(item.path) ? "image" :
      /\.(?:json|ya?ml|toml|ini|xml)$/i.test(item.path) ? "config" :
      /\.(?:md|txt|pdf|docx?)$/i.test(item.path) ? "document" : "file";
    const row = el("div", {
      class: "wsp-tree-row" + (selected === item.path ? " selected" : ""),
      role: "treeitem", "aria-level": String(depth + 1),
      "aria-expanded": item.directory ? String(expanded.has(item.path)) : null,
      title: item.path, draggable: true,
      onclick: () => {
        if (results) { void locate(project, item.path); return; }
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
    el("span", { class: "wsp-tree-toggle" }, item.directory ? workspaceIcon(expanded.has(item.path) ? "chevronDown" : "chevron") : null),
    el("span", { class: "wsp-tree-kind wsp-tree-kind-" + kind }, workspaceIcon(item.directory ? expanded.has(item.path) ? "folderOpen" : "folder" : kind === "code" ? "fileCode" : "file")),
    el("span", { class: "wsp-tree-name", text: item.name }));
    row.style.paddingLeft = 8 + depth * 15 + "px";
    return row;
  }

  function branch(path, depth) {
    return (items.get(path) || []).map((item) => {
      return el("div", null, itemRow(item, depth),
        item.directory && expanded.has(item.path) ? el("div", { role: "group" }, ...branch(item.path, depth + 1)) : null);
    });
  }

  function render() {
    pane.hidden = !project;
    if (!project) return;
    title.textContent = project.name;
    callbacks.visibility?.(true);
    body.replaceChildren(...(results ? results.map(path => itemRow({name: path.split("/").at(-1), path, directory: false})) : branch("", 0)));
    if (results?.length) for (const row of body.children) {
      const path = row.title;
      row.classList.add("wsp-tree-result");
      row.querySelector(".wsp-tree-name").append(el("small", {text: path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "项目根目录"}));
    }
    if (search.value.trim() && !results) body.replaceChildren(el("p", {class: "wsp-tree-empty", text: searchError || "正在搜索…"}));
    else if (results && !results.length) body.append(el("p", {class: "wsp-tree-empty", text: "没有找到匹配文件"}));
    if (results && truncated) body.append(el("p", {class: "wsp-tree-empty", text: "项目较大，搜索基于已索引的文件；可展开目录查找其余文件。"}));
    if (!body.childNodes.length) body.append(el("p", { class: "wsp-tree-empty", text: "目录为空或正在加载…" }));
  }

  function open(projectInfo) {
    if (project?.id !== projectInfo.id) {
      items.clear(); expanded.clear(); selected = ""; results = null; search.value = ""; searchRequest++; locateRequest++;
    }
    project = projectInfo;
    render();
    if (!items.has("")) return load();
    return Promise.resolve();
  }

  async function locate(projectInfo, path) {
    if (!projectInfo) return;
    const opening = open(projectInfo);
    const request = ++locateRequest;
    const projectId = projectInfo.id;
    await opening;
    if (project?.id !== projectId || request !== locateRequest) return;
    search.value = ""; results = null; searchRequest++;
    const normalized = path.replaceAll("\\", "/");
    let parent = "";
    for (const part of normalized.split("/").slice(0, -1)) {
      parent = parent ? parent + "/" + part : part;
      expanded.add(parent);
      if (!items.has(parent)) await load(parent);
      if (project?.id !== projectId || request !== locateRequest) return;
    }
    selected = normalized;
    render();
    body.querySelector(".wsp-tree-row.selected .wsp-tree-name")?.scrollIntoView({ block: "center", inline: "nearest" });
  }

  function close() { finishResize(); project = null; searchRequest++; locateRequest++; closeMenu(); render(); callbacks.visibility?.(false); }
  pane.querySelector("#wsp-tree-close").addEventListener("click", close);
  pane.querySelector("#wsp-tree-refresh").addEventListener("click", () => {
    items.clear(); callbacks.refresh?.(project?.id);
    if (search.value.trim()) void runSearch();
    else { for (const path of ["", ...expanded]) void load(path); }
  });
  async function runSearch() {
    const request = ++searchRequest;
    const current = project?.id;
    const query = search.value.trim();
    results = null; searchError = "";
    render();
    if (!query || !current) return;
    try {
      const data = await callbacks.search(current, query);
      if (request !== searchRequest || current !== project?.id) return;
      results = data.items; truncated = !!data.truncated; render();
    } catch (error) {
      if (request !== searchRequest || current !== project?.id) return;
      searchError = "搜索失败：" + error.message; render();
    }
  }
  search.addEventListener("input", () => { void runSearch(); });
  search.addEventListener("keydown", event => {
    if (event.key === "Escape") { search.value = ""; void runSearch(); }
    if (event.key === "Enter" && results?.[0]) { event.preventDefault(); void locate(project, results[0]); }
  });
  function setSide(value) {
    finishResize();
    const position = value === "right" ? "right" : "left";
    for (const button of sideButtons) button.setAttribute("aria-pressed", String(button.dataset.treeSide === position));
    root.classList.toggle("tree-right", position === "right");
    if (project) body.querySelector(".wsp-tree-row.selected .wsp-tree-name")?.scrollIntoView({block: "nearest", inline: "nearest"});
    try { localStorage.setItem("sona-code:tree-side", position); } catch (_) {}
  }
  try { setSide(localStorage.getItem("sona-code:tree-side")); } catch (_) { setSide("left"); }
  for (const button of sideButtons) button.addEventListener("click", () => setSide(button.dataset.treeSide));

  function widthBounds() {
    const minimum = parseFloat(getComputedStyle(root).getPropertyValue("--wsp-tree-min-width")) || 280;
    const sidebarWidth = root.querySelector(".wsp-side").getBoundingClientRect().width;
    return { minimum, maximum: Math.max(minimum, root.clientWidth - sidebarWidth - 320) };
  }
  function updateWidth() {
    const { minimum, maximum } = widthBounds();
    root.style.setProperty("--wsp-tree-max-width", maximum + "px");
    if (preferredWidth !== null) root.style.setProperty("--wsp-tree-width", preferredWidth + "px");
    const width = Math.max(minimum, Math.min(preferredWidth ?? minimum, maximum));
    resizer.setAttribute("aria-valuemin", String(minimum));
    resizer.setAttribute("aria-valuemax", String(maximum));
    resizer.setAttribute("aria-valuenow", String(Math.round(width)));
  }
  function resizeTo(width) {
    const { minimum, maximum } = widthBounds();
    preferredWidth = Math.max(minimum, Math.min(width, maximum));
    updateWidth();
  }
  function saveWidth() {
    if (preferredWidth !== null) try { localStorage.setItem("sona-code:tree-width", String(preferredWidth)); } catch (_) {}
  }
  function finishResize(event) {
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    const pointerId = drag.pointerId;
    drag = null;
    root.classList.remove("tree-resizing");
    if (resizer.hasPointerCapture(pointerId)) resizer.releasePointerCapture(pointerId);
    saveWidth();
  }
  try {
    const saved = Number(localStorage.getItem("sona-code:tree-width"));
    if (Number.isFinite(saved) && saved > 0) preferredWidth = saved;
  } catch (_) {}
  updateWidth();
  resizer.addEventListener("pointerdown", event => {
    if (event.button !== 0 || window.innerWidth <= 700) return;
    event.preventDefault();
    drag = {pointerId: event.pointerId, x: event.clientX, width: pane.getBoundingClientRect().width,
      direction: root.classList.contains("tree-right") ? -1 : 1};
    resizer.setPointerCapture(event.pointerId);
    root.classList.add("tree-resizing");
  });
  resizer.addEventListener("pointermove", event => {
    if (drag && event.pointerId === drag.pointerId) resizeTo(drag.width + (event.clientX - drag.x) * drag.direction);
  });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) resizer.addEventListener(event, finishResize);
  resizer.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const { minimum, maximum } = widthBounds();
    const direction = root.classList.contains("tree-right") ? -1 : 1;
    resizeTo(event.key === "Home" ? minimum : event.key === "End" ? maximum :
      pane.getBoundingClientRect().width + (event.key === "ArrowRight" ? 20 : -20) * direction);
    saveWidth();
  });
  const resizeObserver = new ResizeObserver(updateWidth);
  resizeObserver.observe(root);
  resizeObserver.observe(root.querySelector(".wsp-side"));
  const outsideMenu = (event) => { if (menu && !menu.contains(event.target)) closeMenu(); };
  document.addEventListener("pointerdown", outsideMenu);
  return { open, close, locate,
    dispose() { finishResize(); resizeObserver.disconnect(); closeMenu(); document.removeEventListener("pointerdown", outsideMenu); },
    get projectId() { return project?.id; } };
}
