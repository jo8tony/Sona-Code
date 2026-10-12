"use strict";

function workspaceLanguageFamily(language) {
  return language === "java" || language === "python" ? language :
    ["javascript", "javascriptreact", "typescript", "typescriptreact"].includes(language) ? "typescript" : null;
}

function createLanguageClient({urlFor, onStatus, onError}) {
  const groups = new Map(), listeners = new Set(), editorId = crypto.randomUUID();
  const failure = (code, message) => Object.assign(new Error(message), {code});
  const aborted = () => Object.assign(new Error("导航查询已取消"), {name: "AbortError", code: "cancelled"});
  const isNavigation = kind => ["definition", "implementation", "references"].includes(kind);
  function waitForSync(promise, signal) {
    return new Promise((resolve, reject) => {
      const finish = (fn, value) => {clearTimeout(timer); signal?.removeEventListener("abort", cancel); fn(value);};
      const cancel = () => finish(reject, aborted());
      const timer = setTimeout(() => finish(reject, failure("timeout", "草稿同步超时，请重新分析")), 15000);
      signal?.addEventListener("abort", cancel, {once: true});
      promise.then(value => finish(resolve, value), error => finish(reject, error));
      if (signal?.aborted) cancel();
    });
  }
  let disposed = false, active = null;
  const keyFor = (projectId, family) => JSON.stringify([projectId, family]);
  function report(g, state, message) { if (!disposed) onStatus?.({projectId: g.projectId, family: g.family, state, message}); }
  function changed(g) {
    ++g.revision;
    for (const [id, p] of g.pending) if (isNavigation(p.kind)) {
      fire(g, {type: "cancel", request_id: id}); p.reject(failure("stale", "草稿已变化，请重新查询"));
    }
    for (const listener of listeners) listener(g.projectId, g.family);
  }
  function group(projectId, family) {
    const key = keyFor(projectId, family);
    let g = groups.get(key);
    if (!g) {
      g = {key, projectId, family, documents: new Map(), epochs: new Map(), pending: new Map(),
        revision: 0, connection: 0, generation: null, id: 0, socket: null, ready: false,
        active: active === key, retryDelay: 1000, state: "initializing", implementation: false, references: false};
      groups.set(key, g); if (!g.active) scheduleIdle(g);
    }
    return g;
  }
  function fire(g, message) {
    if (!disposed && g.socket?.readyState === WebSocket.OPEN) {
      g.socket.send(JSON.stringify({...message, id: ++g.id})); return g.id;
    }
    return null;
  }
  function request(g, message, timeout, signal) {
    if (signal?.aborted) return Promise.reject(aborted());
    if (g.socket?.readyState !== WebSocket.OPEN) return Promise.reject(failure("disconnected", "语言服务连接已断开"));
    const id = g.id + 1;
    return new Promise((resolve, reject) => {
      const finish = (fn, value) => {
        clearTimeout(p.timer); signal?.removeEventListener("abort", cancel); g.pending.delete(id); fn(value);
      };
      const cancel = () => { fire(g, {type: "cancel", request_id: id}); finish(reject, aborted()); };
      const p = {kind: message.type, message, resolve: value => finish(resolve, value), reject: error => finish(reject, error)};
      p.timer = setTimeout(() => {
        if (isNavigation(message.type)) fire(g, {type: "cancel", request_id: id});
        p.reject(failure("timeout", "语言服务请求超时，请重新分析"));
      }, timeout);
      g.pending.set(id, p); signal?.addEventListener("abort", cancel, {once: true});
      try { fire(g, message); } catch (error) { p.reject(error); }
    });
  }
  function rejectPending(g, error) { for (const p of Array.from(g.pending.values())) p.reject(error); }
  function scheduleIdle(g) {
    clearTimeout(g.idleTimer);
    g.idleTimer = setTimeout(() => { if (!g.active) stop(g, "paused"); }, 180000);
  }
  function heartbeat(g, socket) {
    g.heartbeat = setTimeout(() => {
      if (disposed || g.socket !== socket) return;
      fire(g, {type: "ping"}); heartbeat(g, socket);
    }, 20000);
  }
  function stop(g, state) {
    clearTimeout(g.heartbeat); clearTimeout(g.retry); clearTimeout(g.idleTimer);
    for (const entry of g.documents.values()) clearTimeout(entry.timer);
    const socket = g.socket; g.socket = null; g.ready = false; g.state = state;
    rejectPending(g, failure("disconnected", "语言服务连接已关闭")); socket?.close();
    report(g, state, state === "paused" ? "不活动语言组已暂停" : "语言服务连接已关闭");
  }
  function connect(g) {
    if (disposed || g.socket) return;
    clearTimeout(g.retry); ++g.connection; g.generation = null; g.id = 0; g.ready = false; g.implementation = g.references = false;
    g.state = "initializing"; g.epochs.clear();
    for (const entry of g.documents.values()) { entry.sent = null; entry.ack = null; entry.error = null; }
    let socket;
    try { socket = new WebSocket(urlFor(g.projectId, g.family, editorId)); }
    catch (error) { report(g, "unavailable", error.message); return; }
    g.socket = socket; report(g, "initializing", "正在连接本地语言服务");
    socket.addEventListener("open", () => {
      if (disposed || g.socket !== socket) return;
      heartbeat(g, socket);
      for (const entry of g.documents.values()) void flush(g, entry);
    });
    socket.addEventListener("message", event => {
      if (disposed || g.socket !== socket || typeof event.data !== "string") return;
      let message; try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type === "status") {
        if (!Number.isSafeInteger(message.generation)) return;
        if (g.generation !== null && message.generation < g.generation) return;
        if (g.generation !== null && message.generation !== g.generation) {
          rejectPending(g, failure("stale", "语言服务已重新启动")); changed(g);
        }
        g.generation = message.generation; g.state = message.state;
        g.implementation = message.capabilities?.implementation === true;
        g.references = message.capabilities?.references === true;
        if (message.state === "initializing" || message.state === "unavailable" || message.state === "closed") g.ready = false;
        if (message.state === "ready") { g.ready = true; g.retryDelay = 1000; }
        if (message.state === "unavailable") rejectPending(g, failure("unavailable", message.message || "语言服务不可用"));
        report(g, message.state, message.message); return;
      }
      // Route validation can fail before a service exists. The current socket
      // and monotonic request id still identify these generation-free errors.
      if (message.type === "error" && (message.generation === undefined || message.generation === g.generation)) {
        const error = failure(message.code, message.message), pending = g.pending.get(message.id);
        if (pending) pending.reject(error);
        else if (message.id === null) {
          g.ready = false; g.state = message.code; rejectPending(g, error); report(g, message.code, message.message);
        }
        return;
      }
      if (g.generation === null || message.generation !== g.generation) return;
      const p = g.pending.get(message.id); if (!p) return;
      if (isNavigation(p.kind) && message.type === p.kind + "-result") p.resolve(message);
      else if (!isNavigation(p.kind) && message.type === "ack" &&
          (p.message.version === undefined || message.path === p.message.path && message.version === p.message.version)) p.resolve(message);
    });
    socket.addEventListener("close", () => {
      if (disposed || g.socket !== socket) return;
      g.socket = null; g.ready = false; ++g.connection; clearTimeout(g.heartbeat);
      rejectPending(g, failure("disconnected", "语言服务连接中断")); changed(g);
      report(g, "disconnected", "语言服务连接中断，正在重连");
      if (g.documents.size && (g.active || g.state !== "paused")) {
        g.retry = setTimeout(() => connect(g), g.retryDelay); g.retryDelay = Math.min(g.retryDelay * 2, 10000);
      }
    });
    socket.addEventListener("error", () => report(g, "disconnected", "无法连接本地语言服务"));
  }
  function flush(g, entry) {
    clearTimeout(entry.timer);
    if (disposed || g.documents.get(entry.snapshot.path) !== entry || entry.tooLarge || g.socket?.readyState !== WebSocket.OPEN) return Promise.resolve();
    const d = entry.snapshot;
    if (entry.sent?.version === d.version && entry.sent.epoch === d.epoch) return entry.ack || Promise.resolve();
    const opening = !entry.sent;
    if (opening) { entry.wireEpoch = (g.epochs.get(d.path) || 0) + 1; g.epochs.set(d.path, entry.wireEpoch); }
    entry.sent = {...d}; entry.error = null;
    entry.ack = request(g, {type: opening ? "open" : "change", path: d.path, version: d.version, text: d.text,
      ...(opening ? {language: d.language} : {})}, g.family === "java" ? 125000 : 50000).catch(error => {
      if (!["superseded", "cancelled", "stale", "disconnected"].includes(error.code)) {
        entry.error = error; report(g, error.code || "error", error.message);
      }
    });
    return entry.ack;
  }
  function update(snapshot, opening) {
    if (disposed) return;
    const family = workspaceLanguageFamily(snapshot.language); if (!family) return;
    const g = group(snapshot.projectId, family);
    let entry = g.documents.get(snapshot.path);
    if (entry && (entry.snapshot.epoch !== snapshot.epoch || entry.snapshot.language !== snapshot.language)) {
      close(snapshot.projectId, snapshot.path); entry = null;
    }
    if (entry && entry.snapshot.version >= snapshot.version) return;
    if (!entry) { entry = {sent: null, ack: null}; g.documents.set(snapshot.path, entry); }
    clearTimeout(entry.timer); entry.snapshot = {...snapshot};
    const encoder = new TextEncoder();
    entry.tooLarge = encoder.encode(snapshot.text).byteLength > 2 * 1024 * 1024 ||
      encoder.encode(JSON.stringify({type: "open", id: Number.MAX_SAFE_INTEGER, path: snapshot.path,
        language: snapshot.language, version: snapshot.version, text: snapshot.text})).byteLength > 4 * 1024 * 1024;
    changed(g);
    if (entry.tooLarge) {
      if (entry.sent) fire(g, {type: "close", path: snapshot.path});
      entry.sent = null; report(g, "document_limit", "草稿超过语义分析容量，缩小后可恢复分析"); return;
    }
    if (g.state !== "paused" || g.active) connect(g);
    if (opening) void flush(g, entry);
    else entry.timer = setTimeout(() => { void flush(g, entry); }, 150);
  }
  function close(projectId, path) {
    for (const g of groups.values()) {
      if (g.projectId !== projectId) continue;
      const entry = g.documents.get(path); if (!entry) continue;
      clearTimeout(entry.timer); if (entry.sent) fire(g, {type: "close", path});
      g.documents.delete(path); changed(g);
      if (!g.documents.size) stop(g, "paused");
    }
  }
  function stamp(snapshot) {
    const g = groups.get(keyFor(snapshot.projectId, workspaceLanguageFamily(snapshot.language)));
    return g ? `${g.revision}:${g.connection}:${g.generation}` : "";
  }
  async function navigation(kind, snapshot, position, signal) {
    const family = workspaceLanguageFamily(snapshot.language);
    const g = groups.get(keyFor(snapshot.projectId, family));
    if (disposed || !g) throw failure("unsupported", "此语言暂不支持项目跳转");
    if (g.documents.get(snapshot.path)?.tooLarge) throw failure("document_limit", "当前草稿超过语义分析容量，缩小后可恢复分析");
    if (!g.ready) throw failure(g.state === "unavailable" ? "unavailable" : "indexing", "语言服务尚未就绪，请稍后重试或重新分析");
    if (kind === "implementation" && !g.implementation) throw failure("unsupported_implementation", "此语言服务暂不支持转到实现");
    if (kind === "references" && !g.references) throw failure("unsupported_references", "此语言服务暂不支持查找引用");
    const entry = g.documents.get(snapshot.path), revision = stamp(snapshot);
    const valid = () => !disposed && revision === stamp(snapshot) && entry === g.documents.get(snapshot.path) &&
      entry?.snapshot.version === snapshot.version && entry.snapshot.epoch === snapshot.epoch;
    if (!valid()) throw failure("stale", "草稿版本已变化");
    if (Array.from(g.pending.values()).filter(p => isNavigation(p.kind)).length >= 8) throw failure("request_limit", "待处理导航查询已达上限");
    const deadline = Date.now() + 15000;
    await waitForSync(Promise.all(Array.from(g.documents.values(), d => flush(g, d))), signal);
    if (signal?.aborted) throw aborted();
    if (!valid()) throw failure("stale", "草稿已变化，请重新查询");
    const syncError = Array.from(g.documents.values()).find(d => !d.tooLarge && d.error)?.error; if (syncError) throw syncError;
    if (Array.from(g.pending.values()).filter(p => isNavigation(p.kind)).length >= 8) throw failure("request_limit", "待处理导航查询已达上限");
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw failure("timeout", "导航查询超时，请重新分析");
    const result = await request(g, {type: kind, path: snapshot.path, version: snapshot.version, position}, remaining, signal);
    if (!valid() || result.version !== snapshot.version || result.path !== snapshot.path || result.epoch !== entry.wireEpoch)
      throw failure("stale", "导航结果已过期");
    const targets = (result.targets || []).filter(target => !g.documents.get(target.path)?.tooLarge);
    if (!targets.length && result.targets?.length) throw failure("document_limit", "目标草稿超过语义分析容量，缩小后可恢复分析");
    if (!targets.length && result.rejected?.length) throw failure("unsupported_target", "目标位于项目外、依赖源码或不可打开的文件");
    return targets;
  }
  return {open: snapshot => update(snapshot, true), change: snapshot => update(snapshot, false), close,
    definition: (snapshot, position, signal) => navigation("definition", snapshot, position, signal),
    implementation: (snapshot, position, signal) => navigation("implementation", snapshot, position, signal), stamp,
    references: (snapshot, position, signal) => navigation("references", snapshot, position, signal),
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    saved(projectId, path, revision) {
      for (const g of groups.values()) if (g.projectId === projectId && g.documents.has(path)) {
        void flush(g, g.documents.get(path)).then(() => {
          if (g.documents.has(path)) fire(g, {type: "saved", path, revision});
        });
      }
    },
    setActive(projectId, family) {
      if (disposed) return;
      active = family ? keyFor(projectId, family) : null;
      for (const g of groups.values()) {
        const enabled = g.key === active;
        if (enabled) { g.active = true; clearTimeout(g.idleTimer); if (g.documents.size) connect(g); }
        else if (g.active) { g.active = false; scheduleIdle(g); }
      }
    },
    reanalyze(projectId, family) {
      const g = groups.get(keyFor(projectId, family)); if (!g?.documents.size || disposed) return;
      if (!g.socket) connect(g);
      else { g.ready = false; changed(g); fire(g, {type: "reanalyze"}); }
    },
    dispose() {
      disposed = true; for (const g of groups.values()) stop(g, "closed"); groups.clear(); listeners.clear();
    }};
}
