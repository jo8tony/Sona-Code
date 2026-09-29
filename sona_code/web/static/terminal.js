"use strict";
/* 终端页：浏览器中管理多个 opencode / shell 会话（xterm.js + WebSocket）。
 * 路由离开时保留终端实例，避免重放 ANSI 输出破坏交互式程序的画面。 */

let TERM = null;
let terminalLoadId = 0;

function opencodeStatusText(check) {
  if (!check.pty_available) return "当前系统缺少终端依赖";
  if (!check.opencode_found) return "未检测到 OpenCode，可先使用 shell 会话";
  const source = { bundled: "随包", custom: "自定义", path: "PATH" }[check.opencode_source] || "已检测";
  return `OpenCode ${check.opencode_version || ""} 已就绪（${source}）`.replace("  ", " ");
}

/* ============================================================ 页面渲染 */
function renderTerminal(view) {
  if (TERM) {
    TERM.page.classList.remove("hidden");
    view.replaceChildren(TERM.page);
    document.body.classList.add("term-route");
    addCleanup(parkTerminal);
    if (TERM.activeId) activate(TERM.activeId);
    refreshTerminalInfo();
    return;
  }
  view.replaceChildren(el("div", { class: "loading", text: "加载中…" }));
  const loadId = ++terminalLoadId;
  (async () => {
    let meta, check, sessions, projects;
    try {
      [meta, check, sessions, projects] = await Promise.all([
        api("meta"),
        api("terminal/check", { silent: true }),
        api("terminal/sessions"),
        api("terminal/projects"),
      ]);
    } catch (e) {
      if (loadId !== terminalLoadId || (location.hash || "#/terminal") !== "#/terminal") return;
      view.replaceChildren(errorCard("加载终端失败：" + (e.detail ? format422(e.detail) : e.message), () => route()));
      return;
    }
    if (loadId !== terminalLoadId || (location.hash || "#/terminal") !== "#/terminal" || !view.isConnected) return;

    TERM = {
      adminPrefix: meta.admin_prefix || "/__recorder",
      check,
      newBtn: null,
      hint: null,
      byId: new Map(),
      projects: projects.items || [],
      projectsRequestId: 0,
      activeId: null,
      sideList: null,
      projectList: null,
      tabs: null,
      tabList: null,
      fullscreenBtn: null,
      fullscreen: false,
      main: null,
      page: null,
      area: null,
      empty: null,
      ro: null,
    };

    const newBtn = el("button", { class: "btn btn-primary", type: "button", text: "+ 新建会话", onclick: () => openCreateModal() });
    newBtn.disabled = !check.pty_available || !check.enabled;
    const hint = el("div", {
      class: "term-hint " + (check.opencode_found ? "ok" : "warn"),
      text: opencodeStatusText(check),
      title: check.compatibility_warning || "",
    });
    TERM.newBtn = newBtn;
    TERM.hint = hint;

    TERM.sideList = el("div", { class: "term-list" });
    TERM.projectList = el("div", { class: "term-project-list" });
    TERM.tabs = el("div", { class: "term-tabs" });
    TERM.tabList = el("div", { class: "term-tab-list" });
    TERM.fullscreenBtn = el("button", {
      class: "term-fullscreen", type: "button", title: "应用内全屏显示终端",
      "aria-label": "应用内全屏显示终端", text: "⛶", onclick: toggleTerminalFullscreen,
    });
    TERM.tabs.append(TERM.tabList, TERM.fullscreenBtn);
    TERM.area = el("div", { class: "term-area" });
    TERM.main = el("div", { class: "term-main" }, TERM.tabs, TERM.area);
    TERM.empty = el("div", { class: "term-empty-wrap" },
      emptyBox("暂无终端会话", "点击「新建会话」选择项目目录，在浏览器中启动 opencode"));

    TERM.page = el("section", { class: "term-page" },
      el("aside", { class: "term-side" },
        el("div", { class: "term-side-head" }, newBtn, hint),
        el("div", { class: "term-side-section", text: "运行中的会话" }),
        TERM.sideList,
        el("div", { class: "term-side-section", text: "已保存的项目" }),
        TERM.projectList),
      TERM.main);
    view.replaceChildren(TERM.page);
    document.body.classList.add("term-route");

    // 已有会话逐个挂接（服务重启前残留的会话仍在运行）
    for (const info of sessions.items || []) addSession(info, { focus: false });
    refreshAll();
    refreshProjectList();
    if (TERM.byId.size) activate(TERM.byId.keys().next().value);

    // 尺寸自适应：容器变化 + 窗口变化
    TERM.ro = new ResizeObserver(() => fitActive());
    TERM.ro.observe(TERM.area);
    const onWinResize = () => fitActive();
    window.addEventListener("resize", onWinResize);
    document.addEventListener("click", (ev) => {
      if (!ev.target.closest(".term-project-more, .term-project-menu")) closeProjectMenus();
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key !== "Escape") return;
      closeProjectMenus();
      if (TERM && TERM.fullscreen && !document.querySelector(".modal-mask")) setTerminalFullscreen(false);
    });
    addCleanup(parkTerminal);
  })();
}

function parkTerminal() {
  if (!TERM || !TERM.page) return;
  setTerminalFullscreen(false);
  TERM.page.classList.add("hidden");
  document.body.append(TERM.page);
  document.body.classList.remove("term-route");
}

async function refreshTerminalInfo() {
  const projectsRequestId = ++TERM.projectsRequestId;
  try {
    const [check, projects] = await Promise.all([
      api("terminal/check", { silent: true }),
      api("terminal/projects", { silent: true }),
    ]);
    if (!TERM) return;
    TERM.check = check;
    TERM.newBtn.disabled = !check.pty_available || !check.enabled;
    TERM.hint.className = "term-hint " + (check.opencode_found ? "ok" : "warn");
    TERM.hint.textContent = opencodeStatusText(check);
    TERM.hint.title = check.compatibility_warning || "";
    if (TERM.projectsRequestId === projectsRequestId) {
      TERM.projects = projects.items || [];
      refreshProjectList();
    }
  } catch (_) { /* 保留当前信息，下一次进入页面再试 */ }
}

/* 应用内全屏：终端铺满整个应用窗口，不触发显示器（操作系统）全屏 */
function setTerminalFullscreen(on) {
  if (!TERM || !TERM.page) return;
  TERM.fullscreen = on;
  TERM.page.classList.toggle("term-fs", on);
  if (TERM.fullscreenBtn) {
    TERM.fullscreenBtn.textContent = on ? "↙" : "⛶";
    TERM.fullscreenBtn.title = on ? "退出应用内全屏" : "应用内全屏显示终端";
    TERM.fullscreenBtn.setAttribute("aria-label", TERM.fullscreenBtn.title);
  }
  requestAnimationFrame(fitActive);
}

function toggleTerminalFullscreen() {
  if (!TERM) return;
  setTerminalFullscreen(!TERM.fullscreen);
}

/* ============================================================ 会话管理 */
function addSession(info, { focus }) {
  const s = Object.assign({}, info, {
    ws: null,
    wsState: "closed",
    wsAttached: false,
    pendingInput: "",
    inputErrorShown: false,
    reconnectTimer: null,
    attempts: 0,
    hasConnected: false,
  });

  s.overlayEl = el("div", { class: "term-overlay hidden" });
  s.boxEl = el("div", { class: "term-box" }, s.overlayEl);

  s.term = new Terminal({
    scrollback: 5000,
    fontSize: 13,
    fontFamily: '"SF Mono", ui-monospace, Menlo, Consolas, "Liberation Mono", monospace',
    minimumContrastRatio: 4.5,
    cursorBlink: true,
    theme: {
      background: "#181d27",
      foreground: "#e6e9ef",
      cursor: "#3b82f6",
      selectionBackground: "rgba(59, 130, 246, .35)",
      black: "#242b38", red: "#ff6b75", green: "#75dca4", yellow: "#f6cc7a",
      blue: "#86b7ff", magenta: "#d8a0fa", cyan: "#79d9e8", white: "#e6e9ef",
      brightBlack: "#8893a4", brightRed: "#ff8e95", brightGreen: "#a3efc0",
      brightYellow: "#ffe0a3", brightBlue: "#acd0ff", brightMagenta: "#e8c5ff",
      brightCyan: "#aaedf5", brightWhite: "#ffffff",
    },
  });
  s.fit = new FitAddon.FitAddon();
  s.term.loadAddon(s.fit);
  TERM.byId.set(s.id, s);
  TERM.area.append(s.boxEl);
  s.term.open(s.boxEl);
  s.term.onData((d) => sendInput(s, d));
  s.boxEl.addEventListener("pointerdown", () => {
    if (s.alive && TERM && TERM.activeId === s.id) requestAnimationFrame(() => s.term.focus());
  });
  if (focus) activate(s.id);
}

function closeSession(id, { silent } = {}) {
  const s = TERM.byId.get(id);
  if (!s) return;
  if (s.reconnectTimer) clearTimeout(s.reconnectTimer);
  closeWs(s);
  try { s.term.dispose(); } catch (_) { /* 已销毁 */ }
  s.boxEl.remove();
  TERM.byId.delete(id);
  api("terminal/sessions/" + encodeURIComponent(id), { method: "DELETE", silent: true }).catch(() => {});
  if (TERM.activeId === id) {
    TERM.activeId = null;
    if (TERM.byId.size) activate(TERM.byId.keys().next().value);
  }
  refreshAll();
  if (!silent) toast("会话已关闭", "");
}

async function restartSession(s) {
  const { cwd, kind } = s;
  try {
    const info = await api("terminal/sessions", { method: "POST", body: { cwd, kind }, silent: true });
    closeSession(s.id, { silent: true });
    addSession(info, { focus: true });
    refreshAll();
    toast("会话已重新启动", "");
  } catch (e) {
    toast("重启失败：" + (e.detail ? format422(e.detail) : e.message), "error");
  }
}

/* ============================================================ WebSocket */
function wsUrl(id) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return proto + "//" + location.host + TERM.adminPrefix + "/api/terminal/ws/" + encodeURIComponent(id);
}

function connect(s) {
  closeWs(s);
  s.hasConnected = true;
  const ws = new WebSocket(wsUrl(s.id));
  ws.binaryType = "arraybuffer";
  s.ws = ws;
  s.wsState = "connecting";
  ws.onopen = () => { s.attempts = 0; s.wsState = "open"; refreshAll(); };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      let msg = null;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      if (msg.type === "attached") {
        // 每次连接都从服务器回放缓冲重新建立画面；清掉旧画面，避免重连重复叠字。
        s.alive = msg.alive !== false;
        s.wsAttached = s.alive;
        s.inputErrorShown = false;
        s.term.reset();
        if (s.alive) flushPendingInput(s);
        if (s.alive && TERM && TERM.activeId === s.id) {
          sendResize(s);
          requestAnimationFrame(() => s.term.focus());
        }
      }
      if (msg.type === "input_error" && !s.inputErrorShown) {
        s.inputErrorShown = true;
        toast(msg.detail || "终端输入写入失败，请重新启动会话", "error");
      }
      if (msg.type === "exit") markExited(s, msg.code);
    } else {
      s.term.write(new Uint8Array(ev.data));
    }
  };
  ws.onclose = () => {
    s.ws = null;
    s.wsState = "closed";
    s.wsAttached = false;
    if (s.alive) scheduleReconnect(s);
    refreshAll();
  };
  ws.onerror = () => { try { ws.close(); } catch (_) { /* 关闭中 */ } };
}

function scheduleReconnect(s) {
  if (s.reconnectTimer || !s.alive) return;
  s.attempts++;
  const delay = Math.min(15000, 500 * Math.pow(2, Math.min(s.attempts, 5)));
  s.reconnectTimer = setTimeout(() => {
    s.reconnectTimer = null;
    if (s.alive && TERM && TERM.byId.has(s.id)) connect(s);
  }, delay);
}

function closeWs(s) {
  s.wsAttached = false;
  if (s.ws) {
    s.ws.onclose = null;
    s.ws.onerror = null;
    s.ws.onmessage = null;
    try { s.ws.close(); } catch (_) { /* 关闭中 */ }
    s.ws = null;
  }
}

function sendInput(s, data) {
  if (!data || !s.alive) return;
  if (s.ws && s.ws.readyState === 1 && s.wsAttached) {
    s.ws.send(JSON.stringify({ type: "input", data }));
    return;
  }
  // xterm 会在 WebSocket 完成 attached 握手前获得焦点；缓存这段时间的按键，避免静默丢失。
  s.pendingInput = (s.pendingInput + data).slice(-65536);
}

function flushPendingInput(s) {
  if (!s.pendingInput || !s.ws || s.ws.readyState !== 1 || !s.wsAttached) return;
  const data = s.pendingInput;
  s.pendingInput = "";
  s.ws.send(JSON.stringify({ type: "input", data }));
}

function sendResize(s) {
  if (s.ws && s.ws.readyState === 1) s.ws.send(JSON.stringify({ type: "resize", cols: s.term.cols, rows: s.term.rows }));
}

function markExited(s, code) {
  s.alive = false;
  if (s.reconnectTimer) { clearTimeout(s.reconnectTimer); s.reconnectTimer = null; }
  closeWs(s);
  s.overlayEl.classList.remove("hidden");
  s.overlayEl.replaceChildren(
    el("div", { class: "term-overlay-card" },
      el("div", { class: "term-overlay-title", text: "进程已退出" + (code != null ? "（code " + formatExitCode(code) + "）" : "") }),
      el("div", { class: "term-overlay-actions" },
        el("button", { class: "btn btn-xs", type: "button", text: "重新启动", onclick: () => restartSession(s) }),
        el("button", { class: "btn btn-xs btn-danger", type: "button", text: "关闭", onclick: () => closeSession(s.id) }))));
  refreshAll();
}

function formatExitCode(code) {
  const numeric = Number(code);
  if (!Number.isInteger(numeric) || numeric < 0x80000000) return String(code);
  return `${numeric} / 0x${(numeric >>> 0).toString(16).toUpperCase().padStart(8, "0")}`;
}

/* ============================================================ tab / 侧栏 */
function activate(id) {
  const s = TERM.byId.get(id);
  if (!s) return;
  TERM.activeId = id;
  for (const [sid, x] of TERM.byId) x.boxEl.classList.toggle("active", sid === id);
  refreshAll();
  requestAnimationFrame(() => {
    if (!TERM || TERM.activeId !== s.id || !TERM.byId.has(s.id) || TERM.page.classList.contains("hidden")) return;
    fitActive();
    if (!s.ws && !s.reconnectTimer && (!s.hasConnected || s.alive)) connect(s);
    try { s.term.focus(); } catch (_) { /* 已销毁 */ }
  });
}

function fitActive() {
  if (!TERM || !TERM.page || TERM.page.classList.contains("hidden")) return;
  const s = TERM.byId.get(TERM.activeId);
  if (!s) return;
  try {
    s.fit.fit();
    sendResize(s);
  } catch (_) { /* 尺寸不可用时忽略 */ }
}

function statusDot(s) {
  const cls = s.alive ? (!s.hasConnected ? "conn" : s.wsState === "open" ? "run" : s.wsState === "connecting" ? "conn" : "lost") : "dead";
  const title = !s.hasConnected && s.alive ? "切换到此会话时连接" : { run: "运行中", conn: "连接中", lost: "连接断开，自动重连中", dead: "已退出" }[cls];
  return el("span", { class: "term-dot " + cls, title });
}

function refreshAll() {
  if (!TERM) return;
  refreshTabs();
  refreshSideList();
  refreshEmpty();
}

function refreshTabs() {
  TERM.tabList.replaceChildren();
  for (const s of TERM.byId.values()) {
    TERM.tabList.append(el("button", {
      class: "term-tab" + (s.id === TERM.activeId ? " active" : ""),
      type: "button",
      title: s.cwd + "（" + (s.kind === "shell" ? "shell" : "opencode") + "）",
      onclick: () => activate(s.id),
    },
      statusDot(s),
      el("span", { class: "term-tab-name", text: s.title || s.id }),
      el("span", {
        class: "term-tab-close", title: "关闭会话", text: "×",
        onclick: (ev) => { ev.stopPropagation(); closeSession(s.id); },
      })));
  }
}

function refreshSideList() {
  TERM.sideList.replaceChildren();
  for (const s of TERM.byId.values()) {
    TERM.sideList.append(el("div", {
      class: "term-side-item" + (s.id === TERM.activeId ? " active" : ""),
      onclick: () => activate(s.id),
    },
      el("div", { class: "term-side-row" }, statusDot(s),
        el("span", { class: "term-side-title", text: s.title || s.id }),
        el("span", { class: "badge " + (s.kind === "shell" ? "b-dim" : "b-ok"), text: s.kind === "shell" ? "shell" : "opencode" })),
      el("div", { class: "term-side-path mono", title: s.cwd, text: s.cwd })));
  }
}

function refreshProjectList() {
  TERM.projectList.replaceChildren();
  if (!TERM.projects.length) {
    TERM.projectList.append(el("div", { class: "term-project-empty", text: "启动项目后会自动保存到这里" }));
    return;
  }
  for (const project of TERM.projects) {
    const menu = el("div", { class: "term-project-menu hidden" },
      el("button", { class: "term-project-remove", type: "button", text: "从列表移除", onclick: () => openRemoveProjectDialog(project) }));
    const more = el("button", {
      class: "term-project-more", type: "button", title: "更多操作", "aria-label": `更多操作：${project.name}`,
      "aria-expanded": "false", text: "⋯",
      onclick: (ev) => {
        ev.stopPropagation();
        const opening = menu.classList.contains("hidden");
        closeProjectMenus();
        menu.classList.toggle("hidden", !opening);
        more.setAttribute("aria-expanded", String(opening));
      },
    });
    TERM.projectList.append(el("div", { class: "term-project-item", title: project.path },
      el("div", { class: "term-project-row" },
        el("button", { class: "term-project-open", type: "button", onclick: () => { closeProjectMenus(); openCreateModal(project); } },
          el("span", { class: "term-side-title", text: project.name }),
          el("span", { class: "term-side-path mono", text: project.path })),
        more),
      menu));
  }
}

function closeProjectMenus() {
  if (!TERM) return;
  TERM.projectList.querySelectorAll(".term-project-menu").forEach((menu) => menu.classList.add("hidden"));
  TERM.projectList.querySelectorAll(".term-project-more").forEach((button) => button.setAttribute("aria-expanded", "false"));
}

async function reloadProjects() {
  const projectsRequestId = ++TERM.projectsRequestId;
  try {
    const result = await api("terminal/projects", { silent: true });
    if (TERM && TERM.projectsRequestId === projectsRequestId) {
      TERM.projects = result.items || [];
      refreshProjectList();
    }
  } catch (e) {
    toast("读取项目列表失败：" + (e.detail || e.message), "error");
  }
}

async function removeProject(project) {
  await api("terminal/projects", { method: "DELETE", body: { path: project.path }, silent: true });
  TERM.projectsRequestId++;
  TERM.projects = TERM.projects.filter((item) => item.path !== project.path);
  refreshProjectList();
}

function openRemoveProjectDialog(project) {
  closeProjectMenus();
  const errorLine = el("div", { class: "term-modal-err" });
  const cancel = () => mask.remove();
  const removeBtn = el("button", { class: "btn btn-danger", type: "button", text: "确认移除", onclick: async () => {
    removeBtn.disabled = true;
    errorLine.textContent = "";
    try {
      await removeProject(project);
      mask.remove();
      toast("项目已从列表移除", "");
    } catch (e) {
      errorLine.textContent = "移除失败：" + (e.detail || e.message);
      removeBtn.disabled = false;
    }
  } });
  const mask = el("div", { class: "modal-mask", onclick: (ev) => { if (ev.target === mask) cancel(); } },
    el("div", { class: "modal term-confirm-modal", role: "dialog", "aria-modal": "true", "aria-label": "确认移除项目" },
      el("div", { class: "modal-title" }, el("b", { text: "移除已保存的项目？" })),
      el("p", { text: `确认从列表移除「${project.name}」？项目文件和运行中的会话不会被删除。` }),
      el("div", { class: "term-confirm-path mono", text: project.path }),
      errorLine,
      el("div", { class: "modal-actions" },
        el("button", { class: "btn", type: "button", text: "取消", onclick: cancel }),
        removeBtn)));
  document.body.append(mask);
  removeBtn.focus();
}

function refreshEmpty() {
  const has = TERM.byId.size > 0;
  TERM.area.classList.toggle("has-sessions", has);
  if (has) {
    TERM.empty.remove();
  } else {
    TERM.area.append(TERM.empty);
  }
}

/* ============================================================ 新建会话弹窗 */
function openCreateModal(project = null) {
  const check = TERM.check;

  const cwdInput = el("input", {
    type: "text", class: "mono", placeholder: check.platform === "darwin" ? "/Users/你的用户名/Projects/my-app" : "项目目录绝对路径，如 D:\\project\\my-app",
    autocomplete: "off", spellcheck: "false",
  });
  if (project) cwdInput.value = project.path;

  // 桌面版使用系统对话框；普通浏览器保留页内目录浏览器。
  const browser = el("div", { class: "term-browser hidden" });
  let browserLoaded = false;
  const browseInPage = async () => {
    if (browser.classList.contains("hidden")) {
      browser.classList.remove("hidden");
      toggleBtn.textContent = "收起浏览";
      if (!browserLoaded) { browserLoaded = true; await loadFs(null); }
    } else {
      browser.classList.add("hidden");
      toggleBtn.textContent = "浏览目录…";
    }
  };
  const nativeDialog = window.__TAURI__?.dialog;
  const toggleBtn = el("button", { class: "btn btn-xs", type: "button",
    text: nativeDialog?.open ? "选择目录…" : "浏览目录…", onclick: async () => {
      if (!nativeDialog?.open) { await browseInPage(); return; }
      toggleBtn.disabled = true;
      try {
        const options = { directory: true, multiple: false, title: "选择项目目录" };
        if (cwdInput.value.trim()) options.defaultPath = cwdInput.value.trim();
        const selected = await nativeDialog.open(options);
        if (selected && mask.isConnected) {
          cwdInput.value = selected;
          errLine.textContent = "";
        }
      } catch (e) {
        if (mask.isConnected) errLine.textContent = "选择目录失败：" + (e.message || String(e));
      } finally {
        toggleBtn.disabled = false;
      }
    } });

  async function loadFs(path) {
    let r;
    try {
      r = await api("terminal/fs" + (path ? "?path=" + encodeURIComponent(path) : ""), { silent: true });
    } catch (e) {
      toast("读取目录失败：" + (e.detail ? format422(e.detail) : e.message), "error");
      return;
    }
    renderFs(r);
  }

  function renderFs(r) {
    browser.replaceChildren();
    // 面包屑：根 → 逐级
    const crumb = el("div", { class: "term-crumb" });
    if (!r.path) {
      crumb.append(el("span", { class: "term-crumb-item", text: check.platform === "darwin" ? "根目录" : "此电脑" }));
    } else if (String(r.path).startsWith("/")) {
      crumb.append(el("span", { class: "term-crumb-item clickable", text: "/", onclick: () => loadFs("/") }));
      const parts = String(r.path).split("/").filter(Boolean);
      let target = "";
      parts.forEach((part) => {
        target += "/" + part;
        const current = target;
        crumb.append(el("span", { class: "term-crumb-sep", text: "/" }),
          el("span", { class: "term-crumb-item clickable", text: part, onclick: () => loadFs(current) }));
      });
    } else {
      // parts[0] 形如 “D:”（盘符本身已带冒号），逐级重建各级目标路径
      const parts = String(r.path).split(/[\\/]+/).filter(Boolean);
      const driveLabel = /^[a-zA-Z]:?$/.test(parts[0] || "") ? parts[0].replace(/:$/, "") + ":" : null;
      parts.forEach((p, i) => {
        const label = i === 0 && driveLabel ? driveLabel : p;
        const target = parts.slice(0, i + 1).map((x, j) => (j === 0 ? driveLabel || x : x)).join("\\") + (driveLabel && i === 0 ? "\\" : "");
        crumb.append(
          el("span", { class: "term-crumb-item clickable", text: label, onclick: () => loadFs(target) }));
        if (i < parts.length - 1) crumb.append(el("span", { class: "term-crumb-sep", text: "\\" }));
      });
    }
    browser.append(crumb);

    const list = el("div", { class: "term-browser-list" });
    if (r.parent && r.parent !== r.path) {
      list.append(el("div", { class: "term-browser-item up", text: "← 上级目录", onclick: () => loadFs(r.parent) }));
    }
    if (!(r.entries || []).length) {
      list.append(el("div", { class: "term-browser-empty", text: "（无子目录）" }));
    }
    for (const e of r.entries || []) {
      list.append(el("div", {
        class: "term-browser-item", title: e.path,
        onclick: () => { cwdInput.value = e.path; loadFs(e.path); },
      }, el("span", { class: "term-dir-icon", text: "▸" }), el("span", { text: e.name })));
    }
    browser.append(list);
  }

  // 会话类型
  const opencodeOpt = el("label", { class: "term-kind" },
    el("input", { type: "radio", name: "term-kind", value: "opencode", checked: true, disabled: !check.opencode_found }),
    el("span", null, " opencode", check.opencode_found
      ? el("span", { class: "dim", text: `（AI 编码代理 · ${check.opencode_version || check.opencode_source || "ready"}）` })
      : el("span", { class: "term-warn-text", text: "（未检测到）" })));
  const shellOpt = el("label", { class: "term-kind" },
    el("input", { type: "radio", name: "term-kind", value: "shell" }),
    el("span", null, " " + (check.shell_command || "shell"), el("span", { class: "dim", text: "（通用终端）" })));
  if (!check.opencode_found) {
    const shellRadio = shellOpt.querySelector("input");
    if (shellRadio) shellRadio.checked = true;
  }
  if (project && project.kind === "shell") {
    opencodeOpt.querySelector("input").checked = false;
    shellOpt.querySelector("input").checked = true;
  }

  const errLine = el("div", { class: "term-modal-err" });
  const startBtn = el("button", { class: "btn btn-primary", type: "button", text: "启动会话", onclick: async () => {
    const cwd = cwdInput.value.trim();
    if (!cwd) { errLine.textContent = "请填写或选择项目目录"; return; }
    const kind = (browser.closest(".modal") || document).querySelector('input[name="term-kind"]:checked');
    startBtn.disabled = true;
    startBtn.textContent = "启动中…";
    try {
      const info = await api("terminal/sessions", { method: "POST", body: { cwd, kind: kind ? kind.value : "opencode" }, silent: true });
      mask.remove();
      addSession(info, { focus: true });
      refreshAll();
      if (info.project_saved) reloadProjects();
      else toast("会话已启动，但项目目录未能保存", "error");
      toast("会话已启动：" + (info.title || cwd), "");
    } catch (e) {
      errLine.textContent = "启动失败：" + (e.detail ? format422(e.detail) : e.message);
      startBtn.disabled = false;
      startBtn.textContent = "启动会话";
    }
  } });

  const mask = el("div", { class: "modal-mask", onclick: (ev) => { if (ev.target === mask) mask.remove(); } },
    el("div", { class: "modal term-modal" },
      el("div", { class: "modal-title" }, el("b", { text: "新建终端会话" }),
        el("span", { class: "modal-close", text: "×", onclick: () => mask.remove() })),
      el("div", { class: "field" },
        el("label", { class: "f-label", text: "会话类型" }),
        el("div", { class: "term-kinds" }, opencodeOpt, shellOpt)),
      el("div", { class: "field" },
        el("label", { class: "f-label", text: "项目目录" }),
        el("div", { class: "term-cwd-row" }, cwdInput, toggleBtn),
        el("div", { class: "f-hint", text: "opencode 将在该目录下启动；也可直接粘贴路径" })),
      el("div", { class: "term-warn-text", text: "OpenCode 可在所选项目内执行命令并读写文件；其权限确认不是安全沙箱，请仅用于可信任的项目。" }),
      check.compatibility_warning ? el("div", { class: "term-warn-text", text: check.compatibility_warning }) : null,
      browser,
      errLine,
      el("div", { class: "modal-actions" },
        el("button", { class: "btn", type: "button", text: "取消", onclick: () => mask.remove() }),
        startBtn)));

  document.body.append(mask);
  cwdInput.focus();
}
