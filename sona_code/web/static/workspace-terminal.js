"use strict";
/* Project-owned shells keep their xterm DOM and process across workspace redraws. */
function createWorkspaceTerminal(options) {
  const groups = new Map();
  let current = null;
  let disposed = false;
  const tabs = el("div", {class: "wsp-terminal-tabs", role: "tablist", "aria-label": "项目终端会话"});
  const newButton = el("button", {class: "wsp-mini", type: "button", text: "+ 新建终端", onclick: () => { if (current) void create(current.project.id); }});
  const status = el("div", {class: "wsp-terminal-status", role: "status", "aria-live": "polite"});
  const area = el("div", {class: "wsp-terminal-area"});
  const page = el("section", {class: "wsp-terminal", hidden: true},
    el("div", {class: "wsp-terminal-toolbar"}, tabs, newButton), status, area);
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => fitCurrent());
  observer?.observe(area);

  function report(group, message) {
    group.error = message;
    if (disposed) options.error?.(message);
    else if (current === group) render();
  }
  function selected(group) { return group?.sessions.get(group.activeId); }
  function render() {
    if (!current || disposed) return;
    const group = current;
    newButton.disabled = !!group.pending || group.removing;
    if (group.tabs.parentNode !== tabs) tabs.replaceChildren(group.tabs);
    for (const session of group.sessions.values()) {
      session.select.setAttribute("aria-selected", String(session.id === group.activeId));
      session.tab.classList.toggle("active", session.id === group.activeId);
      session.close.disabled = !!session.closing;
      session.box.hidden = session.id !== group.activeId;
    }
    const session = selected(group);
    status.textContent = group.error || (group.pending ? "正在启动项目 Shell…" : session ?
      !session.alive ? "进程已退出" + (session.exitCode != null ? `（退出码 ${session.exitCode}）` : "") :
        session.attached ? "运行中 · " + group.project.path : session.connection : "暂无终端，点击「新建终端」启动 Shell");
    status.classList.toggle("error", !!group.error);
  }
  function fitCurrent() {
    const session = selected(current);
    if (disposed || page.hidden || !page.isConnected || !session || !area.clientWidth || !area.clientHeight) return;
    try { session.fit.fit(); sendResize(session); } catch (_) { /* A hidden parent may have no dimensions yet. */ }
  }
  function scheduleFit(focus = false) {
    const group = current;
    requestAnimationFrame(() => {
      if (disposed || group !== current || page.hidden) return;
      fitCurrent();
      if (focus) selected(group)?.term.focus();
    });
  }
  function sendResize(session) {
    if (session.ws?.readyState === 1 && session.attached) session.ws.send(JSON.stringify({type: "resize", cols: session.term.cols, rows: session.term.rows}));
  }
  function sendInput(session, data) {
    if (!data || !session.alive || session.closing || disposed) return;
    if (session.ws?.readyState === 1 && session.attached) {
      session.ws.send(JSON.stringify({type: "input", data}));
    } else if (session.pendingInput.length + data.length <= 65536) session.pendingInput += data;
    else report(session.group, "终端连接未恢复，输入缓存已满，请连接恢复后重试");
  }
  function closeSocket(session) {
    session.attached = false;
    if (session.timer) { clearTimeout(session.timer); session.timer = null; }
    if (session.ws) {
      session.ws.onopen = session.ws.onmessage = session.ws.onclose = session.ws.onerror = null;
      session.ws.close(); session.ws = null;
    }
  }
  function disposeSession(session) {
    closeSocket(session);
    if (!session.disposed) { session.disposed = true; session.term.dispose(); }
  }
  function exited(session, code) {
    session.alive = false; session.exitCode = code; session.pendingInput = "";
    closeSocket(session); render();
  }
  function connect(session) {
    if (disposed || !session.alive || !session.group.sessions.has(session.id)) return;
    closeSocket(session);
    session.connection = "连接中…";
    const ws = new WebSocket(options.wsUrl(session.id));
    session.ws = ws; ws.binaryType = "arraybuffer";
    ws.onopen = () => { session.connection = "正在接入终端…"; render(); };
    ws.onmessage = event => {
      if (disposed || session.ws !== ws) return;
      if (typeof event.data !== "string") { session.term.write(new Uint8Array(event.data)); return; }
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type === "attached") {
        session.term.reset(); session.attempts = 0;
        session.alive = message.alive !== false; session.attached = session.alive;
        if (session.alive) {
          sendResize(session);
          if (session.pendingInput) { const data = session.pendingInput; session.pendingInput = ""; sendInput(session, data); }
        } else { session.pendingInput = ""; session.exitCode = message.code; }
        render();
      } else if (message.type === "exit") exited(session, message.code);
      else if (message.type === "input_error") report(session.group, message.detail || "终端输入失败，请新建终端重试");
    };
    ws.onclose = event => {
      if (session.ws !== ws || disposed) return;
      session.ws = null; session.attached = false;
      if (event.code === 4404) { exited(session, null); report(session.group, "终端会话已不存在，请新建终端"); return; }
      if (session.alive) {
        session.connection = "连接断开，正在自动重连…";
        const delay = Math.min(15000, 500 * Math.pow(2, Math.min(session.attempts++, 5)));
        session.timer = setTimeout(() => { session.timer = null; connect(session); }, delay);
      }
      render();
    };
    ws.onerror = () => ws.close();
    render();
  }
  function addSession(group, info) {
    const session = {...info, group, alive: info.alive !== false, attached: false, pendingInput: "", attempts: 0, timer: null, ws: null};
    const name = session.title || "Shell";
    session.select = el("button", {class: "wsp-terminal-select", type: "button", role: "tab", text: name,
      onclick: () => { group.activeId = session.id; group.error = ""; render(); scheduleFit(true); }});
    session.close = el("button", {class: "wsp-terminal-close", type: "button", text: "×",
      "aria-label": `关闭终端：${name}`, onclick: () => void closeSession(group.project.id, session.id)});
    session.tab = el("div", {class: "wsp-terminal-tab"}, session.select, session.close);
    group.tabs.append(session.tab);
    session.box = el("div", {class: "wsp-terminal-box", hidden: true});
    session.term = new Terminal({scrollback: 5000, fontSize: 14, cursorBlink: true,
      fontFamily: 'ui-monospace, Menlo, Consolas, monospace', minimumContrastRatio: 4.5,
      theme: {background: "#181d27", foreground: "#e6e9ef", cursor: "#3b82f6"}});
    session.fit = new FitAddon.FitAddon(); session.term.loadAddon(session.fit);
    group.sessions.set(info.id, session); group.activeId = info.id;
    group.area.append(session.box); session.term.open(session.box);
    session.term.onData(data => sendInput(session, data));
    connect(session);
    if (current === group) { render(); scheduleFit(true); }
  }
  async function create(projectId) {
    const group = groups.get(projectId);
    if (!group || group.pending || group.removing || disposed) return;
    group.attempted = true; group.error = "";
    group.pending = (async () => {
      try {
        // Omitting a command delegates Shell selection to the configured backend.
        const info = await options.api("terminal/sessions", {method: "POST", body: {cwd: group.project.path, kind: "shell", cols: 80, rows: 24}, silent: true});
        if (disposed || !groups.has(projectId)) {
          try { await options.api("terminal/sessions/" + encodeURIComponent(info.id), {method: "DELETE", silent: true}); }
          catch (error) { report(group, "清理迟到的终端失败：" + (error.message || error.detail)); }
          return;
        }
        addSession(group, info);
      } catch (error) { report(group, "启动终端失败：" + (error.message || error.detail)); }
      finally { group.pending = null; if (current === group) render(); }
    })();
    render();
    await group.pending;
  }
  async function closeSession(projectId, id) {
    const group = groups.get(projectId), session = group?.sessions.get(id);
    if (!session || session.closing) return false;
    session.closing = true; group.error = ""; render();
    try {
      await options.api("terminal/sessions/" + encodeURIComponent(id), {method: "DELETE", silent: true});
      disposeSession(session); session.box.remove(); session.tab.remove(); group.sessions.delete(id);
      if (group.activeId === id) group.activeId = group.sessions.keys().next().value || null;
      render(); scheduleFit(); return true;
    } catch (error) { report(group, "关闭终端失败，进程仍保留：" + (error.message || error.detail)); return false; }
    finally { session.closing = false; render(); }
  }
  return {
    create, close: closeSession,
    mount(host, project) {
      if (disposed) return;
      if (page.parentNode !== host) host.append(page);
      if (!project) { current = null; page.hidden = false; tabs.replaceChildren(); newButton.disabled = true; status.textContent = "请先选择工作区项目"; for (const group of groups.values()) group.area.hidden = true; return; }
      let group = groups.get(project.id);
      if (!group) {
        group = {project, sessions: new Map(), activeId: null, attempted: false, pending: null, error: "", removing: false,
          area: el("div", {class: "wsp-terminal-project"}), tabs: el("div", {class: "wsp-terminal-group-tabs"})};
        groups.set(project.id, group); area.append(group.area);
      }
      const focus = page.hidden || current !== group;
      current = group; page.hidden = false;
      for (const item of groups.values()) item.area.hidden = item !== group;
      render(); scheduleFit(focus);
      if (!group.attempted) void create(project.id);
    },
    hide() { page.hidden = true; },
    async removeProject(projectId) {
      const group = groups.get(projectId);
      if (!group) return;
      group.removing = true;
      try {
        await group.pending;
        for (const id of [...group.sessions.keys()]) if (!await closeSession(projectId, id)) throw new Error(group.error);
        group.area.remove(); groups.delete(projectId); if (current === group) { current = null; page.hidden = true; }
      } finally { group.removing = false; render(); }
    },
    dispose() {
      disposed = true; observer?.disconnect(); page.remove();
      for (const group of groups.values()) for (const session of group.sessions.values()) { disposeSession(session); }
      // Successfully created shells keep running; only explicit close kills them.
    },
  };
}
