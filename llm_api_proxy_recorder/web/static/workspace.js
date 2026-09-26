"use strict";
/* Headless OpenCode workspace. The existing proxy record pages remain separate. */

let workspaceSelection = { projectId: null, sessionId: null, tab: "chat" };

// Link only calls inside a single native assistant lifetime. Ambiguous calls
// remain independent activity rows rather than pointing at the wrong message.
function workspaceCallOwners(assistants, turns) {
  const owners = new Map();
  for (const turn of turns) {
    const time = new Date(turn.started_at).getTime();
    const candidates = assistants.filter((message, index) => {
      const start = message.info?.time?.created;
      const end = message.info?.time?.completed || assistants[index + 1]?.info?.time?.created || Infinity;
      return start != null && time >= start && time < end;
    });
    if (candidates.length === 1) owners.set(turn.call_id, candidates[0].info.id);
  }
  return owners;
}

function renderWorkspace(view) {
  let disposed = false;
  let events = null;
  let eventProjectId = null;
  let refreshTimer = null;
  let refreshing = false;
  let refreshRequested = false;
  let composingInput = false;
  let autocompleteKind = null;
  let fileSearchTimer = null;
  let fileSearchRequest = 0;
  let commandLoadRequest = 0;
  let fileMatches = [];
  let fileMentionRange = null;
  let lastSessionListRefresh = 0;
  let activeRowMenu = null;
  let followLatest = true;
  let scrollToLatestOnLoad = true;
  let trajectoryView = null;
  let trajectorySignature = "";
  const state = {
    projects: [], sessions: new Map(), sessionDetails: new Map(), errors: new Map(),
    projectId: workspaceSelection.projectId, sessionId: workspaceSelection.sessionId,
    messages: [], permissions: [], questions: [], questionDrafts: new Map(), questionPages: new Map(),
    questionErrors: new Map(), diffs: [], todos: [], children: [], statuses: {},
    recordingData: null, recordingError: "", check: null, tab: workspaceSelection.tab || "chat", search: "", sending: false, chosenModels: new Map(), defaultModel: null,
    chosenAgents: new Map(), chosenVariants: new Map(), providers: [], connectedProviders: new Set(), agents: [], commands: [], skills: [], modelLoadError: "",
    collapsedProjects: new Set(), expandedTools: new Map(), pendingAction: "", actionError: "", compactingSessionId: null,
    attachments: [], fileReferences: [], pendingImageCount: 0, pendingImageBytes: 0, commandSelectedIndex: 0,
  };

  view.innerHTML = `
    <section class="wsp" id="wsp">
      <aside class="wsp-side" aria-label="项目与对话">
        <div class="wsp-brand"><img class="wsp-brand-mark" src="sona-code-icon.png" alt="" width="34" height="34"><span class="wsp-brand-copy"><strong>Sona Code</strong><small>桌面工作区</small></span><button class="wsp-side-close" id="wsp-side-close" type="button" aria-label="关闭项目栏">×</button></div>
        <div class="wsp-side-top">
          <div class="wsp-side-actions"><button class="wsp-new" id="wsp-new" type="button">＋ 新建项目</button></div>
          <label class="wsp-search-wrap"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/></svg><input class="wsp-search" id="wsp-search" type="search" placeholder="搜索项目和对话" aria-label="搜索项目和对话"><kbd>⌘K</kbd></label>
        </div>
        <div class="wsp-side-list"><div class="wsp-side-label"><span>项目与对话</span><span class="wsp-side-label-actions"><span id="wsp-project-count"></span></span></div><div id="wsp-projects"></div></div>
        <div class="wsp-side-bottom" id="wsp-connection">正在检查 OpenCode…</div>
      </aside>
      <div class="wsp-side-scrim" id="wsp-side-scrim"></div>
      <div class="wsp-main">
        <nav class="wsp-global-nav" aria-label="主导航"><a class="active" href="#/workspace">工作区</a><a href="#/models">模型</a><a href="#/skills">技能</a><a href="#/terminal">OpenCode 终端</a><a href="#/trajectory">轨迹</a><a href="#/calls">调用列表</a><a href="#/dashboard">仪表盘</a><a href="#/settings">设置</a><span class="wsp-nav-spacer"></span><span class="wsp-nav-note">代理观测与开发对话</span></nav>
        <header class="wsp-head"><button class="wsp-menu" id="wsp-menu" type="button" aria-label="打开项目栏"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button><div class="wsp-head-text"><div class="wsp-breadcrumb" id="wsp-breadcrumb">工作区</div><div class="wsp-title" id="wsp-title">选择项目</div></div><button class="wsp-abort" id="wsp-abort" type="button" title="停止任务" aria-label="停止任务" hidden><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="5" y="5" width="10" height="10" rx="2" fill="currentColor"/></svg></button><span class="wsp-status" id="wsp-status" role="status" aria-label="准备中" title="准备中"></span></header>
        <nav class="wsp-tabs" aria-label="对话视图"><button class="wsp-tab active" type="button" data-wsp-tab="chat">对话</button><button class="wsp-tab" type="button" data-wsp-tab="changes">文件改动<span class="wsp-tab-count" id="wsp-change-count" aria-label="修改文件数量">0</span></button><button class="wsp-tab" type="button" data-wsp-tab="trajectory">轨迹</button><button class="wsp-tab" type="button" data-wsp-tab="activity">活动</button><button class="wsp-tab" type="button" data-wsp-tab="tasks">任务</button></nav>
        <div class="wsp-scroll" id="wsp-scroll"><div class="wsp-content" id="wsp-content"></div></div>
        <div class="wsp-composer-dock"><form class="wsp-composer" id="wsp-form"><div class="wsp-command-menu" id="wsp-command-menu" role="listbox" aria-label="命令与项目文件" hidden></div><div class="wsp-model-picker" id="wsp-model-picker" role="dialog" aria-label="选择模型" hidden><div class="wsp-picker-head"><strong>选择模型</strong><button type="button" id="wsp-model-close" aria-label="关闭模型选择">×</button></div><input id="wsp-model-search" type="search" placeholder="搜索 Provider 或模型" aria-label="搜索 Provider 或模型"><div class="wsp-model-list" id="wsp-model-list"></div></div><div class="wsp-attachment-list" id="wsp-attachment-list" aria-label="待发送附件" hidden></div><div class="wsp-input" id="wsp-input" contenteditable="true" role="textbox" aria-multiline="true" data-placeholder="向 Sona Code 描述你的需求…" aria-label="输入消息" aria-describedby="wsp-skill-error"></div><div id="wsp-skill-error" class="wsp-skill-error" role="status" aria-live="polite" hidden></div><div class="wsp-composer-bottom"><button class="wsp-attach" id="wsp-attach" type="button" title="选择 OpenCode 命令，也可输入 /" aria-label="选择 OpenCode 命令" aria-haspopup="listbox" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></button><select class="wsp-agent" id="wsp-agent" aria-label="选择 Agent" hidden><option value="build">Build · 执行</option></select><button class="wsp-agent-trigger" id="wsp-agent-trigger" type="button" aria-haspopup="menu" aria-expanded="false"><span id="wsp-agent-label">Build · 执行</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button><div class="wsp-agent-picker" id="wsp-agent-picker" role="menu" aria-label="选择 Agent" hidden></div><span class="wsp-composer-hint">Enter 发送 · Shift+Enter 换行</span><span class="wsp-composer-spacer"></span><button class="wsp-model-trigger" id="wsp-model-trigger" type="button" aria-haspopup="dialog" aria-expanded="false">自动</button><select class="wsp-variant" id="wsp-variant" aria-label="选择模型强度" title="模型推理强度" hidden></select><button class="wsp-send" id="wsp-send" type="submit" title="发送消息" aria-label="发送消息"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5m-7 7 7-7 7 7"/></svg></button></div></form><div class="wsp-stats" id="wsp-stats" aria-live="polite"></div></div>
      </div>
    </section>`;

  const root = view.querySelector("#wsp");
  try { if (localStorage.getItem("sona-code:sidebar-collapsed") === "1") root.classList.add("side-collapsed"); }
  catch (_) { /* Storage may be unavailable. */ }
  const sideList = view.querySelector("#wsp-projects");
  const content = view.querySelector("#wsp-content");
  const scroll = view.querySelector("#wsp-scroll");
  const input = view.querySelector("#wsp-input");
  const composer = createWorkspaceComposer(input, skillMention, fileMention);
  const attachmentList = view.querySelector("#wsp-attachment-list");
  const statsLine = view.querySelector("#wsp-stats");
  const modelButton = view.querySelector("#wsp-model-trigger");
  const modelPicker = view.querySelector("#wsp-model-picker");
  const modelSearch = view.querySelector("#wsp-model-search");
  const modelList = view.querySelector("#wsp-model-list");
  const agentSelect = view.querySelector("#wsp-agent");
  const agentTrigger = view.querySelector("#wsp-agent-trigger");
  const agentPicker = view.querySelector("#wsp-agent-picker");
  const agentLabel = view.querySelector("#wsp-agent-label");
  const variantSelect = view.querySelector("#wsp-variant");
  const variantTrigger = el("button", {
    class: "wsp-variant-trigger", id: "wsp-variant-trigger", type: "button",
    "aria-haspopup": "menu", "aria-expanded": "false", hidden: true,
  }, el("span", { id: "wsp-variant-label", text: "默认" }));
  const variantLabel = variantTrigger.querySelector("#wsp-variant-label");
  const variantPicker = el("div", {
    class: "wsp-agent-picker wsp-variant-picker", id: "wsp-variant-picker",
    role: "menu", "aria-label": "选择模型强度", hidden: true,
  });
  variantSelect.before(variantTrigger, variantPicker);
  const commandMenu = view.querySelector("#wsp-command-menu");
  const sessionPath = (projectId, sessionId) =>
    `workspace/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}`;
  const alive = () => !disposed && view.isConnected && (!location.hash || location.hash === "#/workspace");

  addCleanup(() => {
    disposed = true;
    if (events) events.close();
    if (refreshTimer) clearTimeout(refreshTimer);
    if (fileSearchTimer) clearTimeout(fileSearchTimer);
    clearInterval(poll);
  });

  scroll.addEventListener("scroll", () => {
    followLatest = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop <= 80;
  }, { passive: true });
  if (typeof ResizeObserver !== "undefined") {
    const resizeObserver = new ResizeObserver(() => {
      if (alive() && state.tab === "chat" && followLatest) scroll.scrollTop = scroll.scrollHeight;
    });
    resizeObserver.observe(content);
    resizeObserver.observe(scroll);
    addCleanup(() => resizeObserver.disconnect());
  }

  function activeProject() { return state.projects.find((item) => item.id === state.projectId); }
  function activeSession() {
    return state.sessionDetails.get(state.sessionId) ||
      (state.sessions.get(state.projectId) || []).find((item) => item.id === state.sessionId);
  }
  function sessionTitle(session) {
    const title = session?.title || "";
    return !title || /^New session - \d{4}-\d\d-\d\dT/.test(title) ? "新对话" : title;
  }
  function stamp(session) {
    const value = session?.time?.updated || session?.time?.created;
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const today = new Date();
    const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
    const day = sameDay(date, today) ? "今天" : sameDay(date, yesterday) ? "昨天" : date.toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
    return `更新于${day} ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
  }
  function shortStamp(session) {
    const value = session?.time?.updated || session?.time?.created;
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  function compactionRunning() {
    if (!state.sessionId) return false;
    if (state.compactingSessionId === state.sessionId) return true;
    const lastUser = [...state.messages].reverse().find((message) => message.info?.role === "user");
    if (!lastUser?.parts?.some((part) => part.type === "compaction")) return false;
    const finished = state.messages.some((message) => message.info?.role === "assistant" &&
      message.info.parentID === lastUser.info.id && (message.info.finish || message.info.error));
    return !finished && state.statuses?.[state.sessionId]?.type === "busy";
  }

  function statusIcon(kind) {
    const icon = el("svg", { class: "wsp-status-icon", viewBox: "0 0 20 20", "aria-hidden": "true" });
    if (kind === "busy") {
      icon.append(el("g", { class: "wsp-status-spinner" },
        el("circle", { cx: "10", cy: "10", r: "7.25", "stroke-dasharray": "12 34" })));
    } else {
      const path = kind === "ready" ? "m6.2 10.2 2.5 2.5 5.2-5.4" :
        kind === "attention" ? "M10 6.2v4.1m0 3.5h.01" :
        kind === "error" ? "m7 7 6 6m0-6-6 6" : "M10 7v3.3m0 3h.01";
      icon.append(el("path", { class: "wsp-status-mark", d: path }));
    }
    return icon;
  }
  async function copyMessageText(text) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const previousFocus = document.activeElement;
        const field = el("textarea", { class: "wsp-clipboard-field", "aria-label": "复制消息", text });
        document.body.append(field);
        try {
          field.select();
          if (!document.execCommand("copy")) throw new Error("浏览器未允许复制");
        } finally { field.remove(); previousFocus?.focus(); }
      }
      toast("已复制消息");
    } catch (error) { toast("复制失败：" + detail(error), "error"); }
  }

  function lastUserMessage() {
    return [...state.messages].reverse().find((message) => message.info?.role === "user" &&
      !message.parts?.some((part) => part.type === "compaction"));
  }

  function recallUserMessage(message) {
    if (!message) return;
    const text = message.skillUse ? `/${message.skillUse.name} ${message.skillUse.arguments || ""}` :
      (message.parts || []).filter((part) => part.type === "text" && !part.synthetic)
        .map((part) => isInitCommandPrompt(part.text) ? "/init" : part.text || "").join("\n");
    composer.value = text;
    const references = (message.parts || []).filter((part) => part.type === "file" &&
      part.url?.startsWith("file:") && part.filename).map((part) => part.filename);
    const pattern = fileReferencePattern(references);
    if (pattern) {
      const matches = [...text.matchAll(pattern)].reverse();
      for (const match of matches) {
        const start = match.index + match[1].length;
        composer.insertFileReference(match[2], start, start + match[2].length + 1, false);
      }
    }
    state.attachments = (message.parts || []).filter((part) => part.type === "file" &&
      part.mime?.startsWith("image/") && part.url && !part.url.startsWith("file:"))
      .map((part) => ({ id: part.id, filename: part.filename || "图片", mime: part.mime, url: part.url }));
    renderAttachments();
    updateSkillInput();
    hideAutocomplete();
    input.focus();
    composer.setSelectionRange(composer.value.length);
  }

  function messageActions(message) {
    const projectId = state.projectId;
    const sessionId = state.sessionId;
    const info = message.modelInfo || message.info || {};
    const actions = el("div", { class: "wsp-message-actions" });
    const text = (message.parts || []).filter((part) => part.type === "text" && !part.synthetic)
      .map((part) => part.text || "").join("\n\n");
    function actionButton(label, path, handler) {
      return el("button", { class: "wsp-message-action", type: "button", title: label, "aria-label": label, onclick: handler },
        el("svg", { viewBox: "0 0 24 24", width: 18, height: 18, fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, el("path", { d: path })));
    }
    const copy = actionButton("复制消息", "M8 8h11v13H8z M16 8V3H3v13h5", () => copyMessageText(text));
    copy.disabled = !text;
    actions.append(copy);
    if (info.role === "assistant") {
      const fork = actionButton("在新对话中分支", "M6 7v10m0-5h8a4 4 0 0 0 4-4V7 M8 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0 M8 19a2 2 0 1 1-4 0 2 2 0 0 1 4 0 M20 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0", async () => {
        if (fork.disabled) return;
        fork.disabled = true;
        try {
          const session = await api(`${sessionPath(projectId, sessionId)}/fork`, {
            method: "POST", body: { message_id: info.id }, silent: true,
          });
          if (!alive()) return;
          if (!session?.id) throw new Error("未返回分支会话 ID");
          const items = state.sessions.get(projectId) || [];
          state.sessions.set(projectId, [session, ...items.filter((item) => item.id !== session.id)]);
          selectSession(projectId, session.id);
          input.focus();
          toast("已创建会话分支");
        } catch (error) { toast("创建分支失败：" + detail(error), "error"); }
        finally { fork.disabled = !info.id; }
      });
      fork.disabled = !info.id;
      actions.append(fork);
    }
    if (info.role === "user" && info.id === lastUserMessage()?.info.id) {
      const withdraw = actionButton("撤回最后一轮", "M9 10 4 5l5-4 M4 5h10a6 6 0 0 1 0 12h-3", async () => {
        if (withdraw.disabled || state.sending) return;
        if (!confirm("撤回最后一轮用户消息及模型回复，并将提问回填到输入框？项目文件修改会保留，当前输入框内容会被替换。")) return;
        withdraw.disabled = true;
        state.sending = true;
        renderHeader();
        try {
          await api(`${sessionPath(projectId, sessionId)}/withdraw`, {
            method: "POST", body: { message_id: info.id }, silent: true,
          });
          if (!alive() || state.projectId !== projectId || state.sessionId !== sessionId) return;
          const index = state.messages.findIndex((item) => item.info?.id === info.id);
          if (index >= 0) state.messages = state.messages.slice(0, index);
          state.actionError = "";
          recallUserMessage(message);
          renderMain();
          await refreshSelected();
        } catch (error) { toast("撤回失败：" + detail(error), "error"); }
        finally { state.sending = false; if (alive()) { renderHeader(); renderMain(); } }
      });
      withdraw.disabled = state.sending || state.statuses?.[sessionId]?.type === "busy" ||
        state.statuses?.[sessionId]?.type === "retry";
      actions.append(withdraw);
    }
    const created = message.info?.time?.created;
    const date = created != null ? new Date(created) : null;
    if (date && Number.isFinite(date.getTime())) {
      const label = date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
      const metadata = el("span", { class: "wsp-message-time", text: label, title: label });
      const completed = info.time?.completed;
      const duration = completed != null ? (new Date(completed) - date) / 1000 : null;
      if (info.role === "assistant" && Number.isFinite(duration) && duration >= 0) metadata.append(` · 用时 ${duration.toFixed(1)} 秒`);
      actions.append(metadata);
    }
    return actions;
  }

  function messageDay(message) {
    const date = new Date(message?.info?.time?.created || message?.info?.time?.updated || Date.now());
    if (Number.isNaN(date.getTime())) return "今天";
    const today = new Date();
    const sameDay = date.toDateString() === today.toDateString();
    return sameDay ? "今天" : date.toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
  }
  function eventTime(value) {
    const date = new Date(value || Date.now());
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  }
  function toolDuration(part) {
    const time = part.state?.time || {};
    if (!time.start || !time.end) return "";
    const seconds = (new Date(time.end) - new Date(time.start)) / 1000;
    return Number.isFinite(seconds) && seconds >= 0 ? `${seconds.toFixed(1)} 秒` : "";
  }
  function numeric(value) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? number : 0;
  }
  function timestamp(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    const parsed = Date.parse(value || "");
    return Number.isFinite(parsed) ? parsed : null;
  }
  function formatTokens(value) {
    if (value < 1000) return String(Math.round(value));
    const scaled = value < 1_000_000 ? value / 1000 : value / 1_000_000;
    const suffix = value < 1_000_000 ? "K" : "M";
    return `${scaled >= 100 ? Math.round(scaled) : Math.round(scaled * 10) / 10}${suffix}`;
  }
  function formatDuration(milliseconds) {
    const seconds = milliseconds / 1000;
    if (seconds < 60) return `${Math.round(seconds * 10) / 10} 秒`;
    const whole = Math.round(seconds);
    return `${Math.floor(whole / 60)} 分 ${whole % 60} 秒`;
  }
  function renderStatsLine() {
    const assistantMessages = state.messages.filter((message) => message.info?.role === "assistant");
    const turns = new Set();
    let inputTokens = 0;
    let outputTokens = 0;
    let llmMilliseconds = 0;
    let toolMilliseconds = 0;
    const busy = state.statuses?.[state.sessionId]?.type === "busy";
    let contextSample = null;
    let latestContextTime = -Infinity;
    for (const [messageIndex, message] of assistantMessages.entries()) {
      const info = message.info || {};
      turns.add(info.parentID || info.id || `assistant-${turns.size}`);
      const usage = info.tokens || {};
      const cache = usage.cache || {};
      const promptTokens = numeric(usage.input) + numeric(cache.read) + numeric(cache.write);
      inputTokens += promptTokens;
      outputTokens += numeric(usage.output);
      if (usage.input != null || usage.output != null || cache.read != null || cache.write != null) {
        const sampleTime = timestamp(info.time?.created) ?? messageIndex;
        if (sampleTime >= latestContextTime) {
          contextSample = { info, usage, promptTokens };
          latestContextTime = sampleTime;
        }
      }
      const started = timestamp(info.time?.created);
      const completed = timestamp(info.time?.completed);
      if (started !== null && (completed !== null || busy)) {
        llmMilliseconds += Math.max(0, (completed ?? Date.now()) - started);
      }
      for (const part of message.parts || []) {
        if (part.type !== "tool") continue;
        const toolStart = timestamp(part.state?.time?.start);
        const toolEnd = timestamp(part.state?.time?.end);
        if (toolStart !== null && toolEnd !== null) toolMilliseconds += Math.max(0, toolEnd - toolStart);
      }
    }
    const groups = [];
    let contextGroup = null;
    if (contextSample) {
      const { info, usage, promptTokens } = contextSample;
      const choice = selectedModel();
      const model = state.providers.find((provider) => provider.id === (info.providerID || choice.provider_id))
        ?.models?.[info.modelID || choice.model_id];
      const contextWindow = numeric(model?.limit?.context);
      const used = promptTokens + numeric(usage.output) + numeric(usage.reasoning);
      if (contextWindow > 0) {
        const percent = Math.min(100, Math.round(used / contextWindow * 100));
        contextGroup = el("span", { class: "wsp-stat-context", title: "按最近一次模型请求的 Token 用量和模型上下文上限估算",
          text: `上下文 ${formatTokens(used)} / ${formatTokens(contextWindow)} · ${percent}%` });
      }
    }
    if (turns.size) groups.push(el("span", { text: `${turns.size} 轮` }));
    if (inputTokens || outputTokens) groups.push(el("span", { text: `输入 ${formatTokens(inputTokens)} · 输出 ${formatTokens(outputTokens)}` }));
    if (llmMilliseconds > 0) groups.push(el("span", { text: `模型 ${formatDuration(llmMilliseconds)}` }));
    if (outputTokens > 0 && llmMilliseconds > 0) {
      const speed = outputTokens / (llmMilliseconds / 1000);
      const speedText = speed >= 10 ? String(Math.round(speed)) : String(Math.round(speed * 10) / 10);
      groups.push(el("span", { title: "输出 Token 数除以模型请求总用时，包含首 Token 等待", text: `速度 ${speedText} tok/s` }));
    }
    if (toolMilliseconds > 0) groups.push(el("span", { text: `工具 ${formatDuration(toolMilliseconds)}` }));
    const left = el("div", { class: "wsp-stats-left" });
    left.replaceChildren(...groups.flatMap((group, index) => index ? [document.createTextNode(" · "), group] : [group]));
    statsLine.replaceChildren(left, ...(contextGroup ? [contextGroup] : []));
    statsLine.hidden = groups.length === 0 && !contextGroup;
    statsLine.title = [...groups, ...(contextGroup ? [contextGroup] : [])].map((group) => group.textContent).join(" · ");
  }

  function renderAttachments() {
    attachmentList.replaceChildren();
    for (const attachment of state.attachments) {
      const preview = el("div", { class: "wsp-attachment" },
        el("img", { src: attachment.url, alt: attachment.filename, title: attachment.filename }),
        el("span", { text: attachment.filename }),
        el("button", { type: "button", title: `移除 ${attachment.filename}`, "aria-label": `移除 ${attachment.filename}`,
          text: "×", onclick: () => {
            state.attachments = state.attachments.filter((item) => item.id !== attachment.id);
            renderAttachments();
          } }));
      attachmentList.append(preview);
    }
    attachmentList.hidden = state.attachments.length === 0;
  }

  function readImage(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error("读取图片失败"));
      reader.onerror = () => reject(reader.error || new Error("读取图片失败"));
      reader.readAsDataURL(file);
    });
  }

  async function addImageFiles(files) {
    if (!state.projectId) { toast("请先选择项目，再添加图片", "error"); return; }
    if (state.sending) { toast("消息发送中，稍后再添加图片", "error"); return; }
    const projectId = state.projectId;
    const sessionId = state.sessionId;
    const acceptedTypes = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
    for (const file of files) {
      if (!file) continue;
      if (!acceptedTypes.has(file.type)) { toast("只支持 PNG、JPEG、GIF 或 WebP 图片", "error"); continue; }
      if (file.size > 8 * 1024 * 1024) { toast(`${file.name || "图片"} 超过 8 MB`, "error"); continue; }
      const totalBytes = state.attachments.reduce((sum, item) => sum + item.size, 0) + state.pendingImageBytes;
      if (state.attachments.length + state.fileReferences.length + state.pendingImageCount >= 8) { toast("一条消息最多添加 8 个附件或文件引用", "error"); break; }
      if (totalBytes + file.size > 20 * 1024 * 1024) { toast("图片总大小不能超过 20 MB", "error"); continue; }
      state.pendingImageCount += 1;
      state.pendingImageBytes += file.size;
      renderHeader();
      try {
        const url = await readImage(file);
        if (!alive() || projectId !== state.projectId || sessionId !== state.sessionId) return;
        state.attachments.push({ id: `${Date.now()}-${Math.random()}`, filename: (file.name || `粘贴图片.${file.type.split("/")[1] || "png"}`).replace(/[\\/]/g, "_"),
          mime: file.type, url, size: file.size });
        renderAttachments();
      } catch (error) { toast(detail(error), "error"); }
      finally {
        state.pendingImageCount = Math.max(0, state.pendingImageCount - 1);
        state.pendingImageBytes = Math.max(0, state.pendingImageBytes - file.size);
        if (alive()) renderHeader();
      }
    }
  }
  function detail(error) { return error?.detail ? format422(error.detail) : error?.message || String(error); }

  function updateSidebarButton() {
    const mobile = window.matchMedia("(max-width: 700px)").matches;
    const expanded = mobile ? root.classList.contains("show-side") : !root.classList.contains("side-collapsed");
    const button = view.querySelector("#wsp-menu");
    button.setAttribute("aria-label", expanded ? "收起项目栏" : "展开项目栏");
    button.setAttribute("aria-expanded", String(expanded));
  }

  function closeRowMenus() {
    if (activeRowMenu) {
      const { menu, trigger, wrapper } = activeRowMenu;
      menu.hidden = true;
      menu.classList.remove("wsp-action-menu-floating");
      menu.style.removeProperty("left");
      menu.style.removeProperty("top");
      wrapper.append(menu);
      trigger.setAttribute("aria-expanded", "false");
      activeRowMenu = null;
    }
    sideList.querySelectorAll(".wsp-action-menu").forEach((menu) => { menu.hidden = true; });
    sideList.querySelectorAll('[aria-haspopup="menu"]').forEach((button) => button.setAttribute("aria-expanded", "false"));
  }

  function rowMenu(actions) {
    const menu = el("div", { class: "wsp-action-menu", role: "menu", hidden: true });
    for (const [label, action] of actions) {
      menu.append(el("button", { type: "button", role: "menuitem", class: action === "delete" ? "danger" : "",
        text: label, onclick: () => { closeRowMenus(); action(); } }));
    }
    const trigger = el("button", { class: "wsp-row-action", type: "button",
      "aria-label": "更多操作", "aria-haspopup": "menu", "aria-expanded": "false",
      onclick: () => {
        const open = menu.hidden;
        closeRowMenus();
        if (open) {
          // Render outside the scrolling sidebar so ancestors cannot clip it.
          document.body.append(menu);
          menu.classList.add("wsp-action-menu-floating");
          menu.hidden = false;
          activeRowMenu = { menu, trigger, wrapper };
          const anchor = trigger.getBoundingClientRect();
          const bounds = menu.getBoundingClientRect();
          const margin = 8;
          const below = window.innerHeight - anchor.bottom - margin - 5;
          const above = anchor.top - margin - 5;
          const top = below >= bounds.height || below >= above
            ? anchor.bottom + 5 : anchor.top - bounds.height - 5;
          menu.style.left = `${Math.max(margin, Math.min(anchor.right - bounds.width, window.innerWidth - bounds.width - margin))}px`;
          menu.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - bounds.height - margin))}px`;
          trigger.setAttribute("aria-expanded", "true");
        }
      } }, el("svg", { viewBox: "0 0 20 20", "aria-hidden": "true" },
        el("circle", { cx: "5", cy: "10", r: "1.5" }),
        el("circle", { cx: "10", cy: "10", r: "1.5" }),
        el("circle", { cx: "15", cy: "10", r: "1.5" })));
    const wrapper = el("span", { class: "wsp-row-menu-wrap" }, trigger, menu);
    return wrapper;
  }

  function openRenameDialog(projectId, session) {
    const titleInput = el("input", { type: "text", value: sessionTitle(session), maxlength: "200" });
    const errorLine = el("p", { class: "wsp-question-error" });
    const mask = el("div", { class: "wsp-modal-mask" },
      el("div", { class: "wsp-modal", role: "dialog", "aria-modal": "true", "aria-label": "重命名对话" },
        el("h2", { text: "重命名对话" }), titleInput, errorLine,
        el("div", { class: "wsp-modal-actions" },
          el("button", { class: "wsp-mini", type: "button", text: "取消", onclick: () => mask.remove() }),
          el("button", { class: "wsp-mini primary", type: "button", text: "保存", onclick: async () => {
            const title = titleInput.value.trim();
            if (!title) { errorLine.textContent = "请输入对话名称"; return; }
            try {
              const updated = await api(`${sessionPath(projectId, session.id)}`, {
                method: "PATCH", body: { title }, silent: true,
              });
              state.sessionDetails.set(session.id, updated);
              mask.remove();
              await loadSessions(state.projects.find((item) => item.id === projectId));
              if (state.projectId === projectId && state.sessionId === session.id) renderHeader();
            } catch (error) { errorLine.textContent = detail(error); }
          } }))));
    mask.addEventListener("click", (event) => { if (event.target === mask) mask.remove(); });
    document.body.append(mask);
    titleInput.focus(); titleInput.select();
  }

  let deleteDialog = null;
  addCleanup(() => deleteDialog?.close());

  function confirmDeletion(title, message) {
    if (deleteDialog) return Promise.resolve(false);
    return new Promise((resolve) => {
      const cancel = el("button", { class: "wsp-mini", type: "button", text: "取消", onclick: () => dialog.close() });
      const dialog = el("dialog", { class: "wsp-modal wsp-delete-dialog", "aria-label": title, "aria-describedby": "wsp-delete-description" },
        el("h2", { text: title }),
        el("p", { id: "wsp-delete-description", text: message }),
        el("div", { class: "wsp-modal-actions" }, cancel,
          el("button", { class: "wsp-mini danger", type: "button", text: "确认删除", onclick: () => dialog.close("confirmed") })));
      dialog.addEventListener("click", (event) => {
        const bounds = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right ||
            event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
      });
      dialog.addEventListener("close", () => {
        deleteDialog = null;
        dialog.remove();
        resolve(dialog.returnValue === "confirmed" && alive());
      }, { once: true });
      deleteDialog = dialog;
      document.body.append(dialog);
      dialog.showModal();
      cancel.focus();
    });
  }

  async function performSessionAction(projectId, session, action) {
    if (!session) return;
    if (action === "summarize" && state.compactingSessionId === session.id) return;
    const base = sessionPath(projectId, session.id);
    if (action === "rename") { openRenameDialog(projectId, session); return; }
    if (action === "delete" && !await confirmDeletion("删除对话", `确定删除对话“${sessionTitle(session)}”及其全部消息？此操作无法撤销。`)) return;
    try {
      if (action === "fork") {
        const fork = await api(`${base}/fork`, { method: "POST", body: {}, silent: true });
        await loadSessions(state.projects.find((item) => item.id === projectId));
        selectSession(projectId, fork.id);
        return;
      }
      if (action === "delete") {
        await api(base, { method: "DELETE", silent: true });
        state.sessionDetails.delete(session.id);
        if (state.projectId === projectId && state.sessionId === session.id) {
          state.sessionId = null;
          workspaceSelection.sessionId = null;
          state.messages = [];
          renderHeader(); renderMain();
        }
        await loadSessions(state.projects.find((item) => item.id === projectId));
        return;
      }
      if (action === "summarize") {
        const chosen = selectedModel();
        const last = [...state.messages].reverse().find((message) => message.info?.role === "assistant")?.info;
        const provider_id = chosen.provider_id || last?.providerID;
        const model_id = chosen.model_id || last?.modelID;
        if (!provider_id || !model_id) { toast("请先选择模型", "error"); openModelPicker(); return; }
        state.compactingSessionId = session.id;
        renderHeader(); renderMain();
        const result = await api(`${base}/summarize`, { method: "POST", body: { provider_id, model_id }, silent: true });
        if (result === false) throw new Error("OpenCode 未能压缩上下文");
      }
      await refreshSelected();
      renderHeader();
    } catch (error) { toast(`对话操作失败：${detail(error)}`, "error"); }
    finally {
      if (action === "summarize" && state.compactingSessionId === session.id) {
        state.compactingSessionId = null;
        renderHeader(); renderMain();
      }
    }
  }

  function renderSidebar() {
    closeRowMenus();
    sideList.replaceChildren();
    view.querySelector("#wsp-project-count").textContent = `${state.projects.length} 个项目`;
    const query = state.search.trim().toLocaleLowerCase();
    let visible = 0;
    for (const project of state.projects) {
      const all = state.sessions.get(project.id);
      const projectMatches = project.name.toLocaleLowerCase().includes(query);
      const sessions = (all || []).filter((session) => !query || projectMatches || sessionTitle(session).toLocaleLowerCase().includes(query));
      if (query && !projectMatches && !sessions.length) continue;
      visible++;
      const collapsed = state.collapsedProjects.has(project.id) && !query;
      const section = el("section", { class: "wsp-project" + (collapsed ? " collapsed" : "") });
      const heading = el("button", { class: "wsp-project-head", type: "button", title: project.path,
        "aria-expanded": String(!collapsed), onclick: () => {
          if (collapsed) {
            state.collapsedProjects.delete(project.id);
            if (!all) loadSessions(project);
          } else state.collapsedProjects.add(project.id);
          renderSidebar();
        } },
        el("span", { class: "wsp-project-mark", text: (project.name || "P").slice(0, 2).toUpperCase() }),
        el("span", { class: "wsp-project-name", text: project.name }),
        el("span", { class: "wsp-project-count", text: all ? String(all.length) : "…" }));
      const projectRow = el("div", { class: "wsp-project-row" }, heading,
        rowMenu([["重命名", () => openRenameProject(project)], ["打开目录", () => openProjectDirectory(project)],
          ["删除工作区", () => removeProject(project)]]),
        el("button", { class: "wsp-row-action wsp-row-plus", type: "button", text: "+",
          title: `在 ${project.name} 中新建对话`, "aria-label": `在 ${project.name} 中新建对话`,
          onclick: () => { state.collapsedProjects.delete(project.id); renderSidebar(); createSession(project.id); } }));
      section.append(projectRow);
      const threads = el("div", { class: "wsp-threads" });
      if (state.errors.has(project.id)) {
        threads.append(el("div", { class: "wsp-error", text: state.errors.get(project.id) }));
      } else if (!all) {
        threads.append(el("div", { class: "wsp-thread-time", text: "点击项目读取对话" }));
      } else if (all && !sessions.length) {
        threads.append(el("div", { class: "wsp-thread-time", text: query ? "无匹配对话" : "暂无对话" }));
      }
      for (const session of sessions) {
        const threadButton = el("button", {
          class: "wsp-thread" + (session.parentID ? " child" : "") +
            (project.id === state.projectId && session.id === state.sessionId ? " active" : ""),
          type: "button", title: sessionTitle(session),
          onclick: () => selectSession(project.id, session.id),
        }, el("span", { class: "wsp-thread-line" },
          el("span", { class: "wsp-thread-title", text: `${session.parentID ? "↳ " : ""}${sessionTitle(session)}` }),
          el("span", { class: "wsp-thread-time", title: "对话最后更新时间", text: shortStamp(session) })),
        el("span", { class: "wsp-thread-preview", text: stamp(session) }));
        threads.append(el("div", { class: "wsp-thread-row" }, threadButton,
          rowMenu([["重命名", () => performSessionAction(project.id, session, "rename")],
            ["分叉会话", () => performSessionAction(project.id, session, "fork")],
            ["删除", () => performSessionAction(project.id, session, "delete")]])));
      }
      section.append(threads);
      sideList.append(section);
    }
    if (query && !visible) sideList.append(el("p", { class: "wsp-empty-search", text: "没有找到匹配的项目或对话。" }));
    else if (!state.projects.length) sideList.append(el("div", { class: "wsp-empty", style: "min-height:200px" },
      el("p", { text: "还没有项目。点击上方按钮选择已有目录。" })));
  }

  function renderHeader() {
    const project = activeProject();
    const session = activeSession();
    view.querySelector("#wsp-breadcrumb").textContent = project ? `${project.name} / 对话记录` : "工作区";
    view.querySelector("#wsp-title").textContent = session ? sessionTitle(session) : (project ? "新建或选择对话" : "选择项目");
    const status = state.statuses?.[state.sessionId];
    const pending = state.permissions.some((item) => item.sessionID === state.sessionId) ||
      state.questions.some((item) => item.sessionID === state.sessionId);
    const label = view.querySelector("#wsp-status");
    const last = state.messages.at(-1);
    const failed = last?.info?.role === "assistant" && last.info.error;
    const compacting = compactionRunning();
    const busy = compacting || state.sending || status?.type === "busy";
    const kind = failed ? "error" : busy ? "busy" : pending ? "attention" : state.check?.found ? "ready" : "missing";
    const description = failed ? "请求失败" : compacting ? "正在压缩上下文" : busy ? "正在处理" :
      pending ? "等待确认" : state.check?.found ? "已就绪" : "未检测到 OpenCode";
    label.className = `wsp-status ${kind}`;
    label.setAttribute("aria-label", description);
    label.title = description;
    if (label.dataset.kind !== kind) {
      label.replaceChildren(statusIcon(kind));
      label.dataset.kind = kind;
    }
    view.querySelector("#wsp-abort").hidden = !session || !busy;
    view.querySelectorAll(".wsp-tab").forEach((button) => button.classList.toggle("active", button.dataset.wspTab === state.tab));
    view.querySelector("#wsp-send").disabled = state.sending || !project || !state.check?.found;
    view.querySelector("#wsp-attach").disabled = state.sending || state.pendingImageCount > 0 || !project;
    modelButton.disabled = !project;
  }

  function empty(title, description, action) {
    const box = el("div", { class: "wsp-empty" }, el("div", { class: "wsp-empty-icon", text: "✦" }),
      el("h2", { text: title }), el("p", { text: description }));
    if (action) box.append(el("button", { class: "wsp-mini primary", style: "margin-top:14px", type: "button", text: action[0], onclick: action[1] }));
    return box;
  }

  function textPart(part, role, references = []) {
    const node = el("div", { class: "wsp-part wsp-text" });
    if (role === "user") appendFileMentions(node, isInitCommandPrompt(part.text) ? "/init" : part.text || "", references);
    else node.append(trjMarkdown(part.text || ""));
    return node;
  }

  function isInitCommandPrompt(text) {
    return typeof text === "string" &&
      text.startsWith("Create or update `AGENTS.md` for this repository.") &&
      text.includes("The goal is a compact instruction file that helps future OpenCode sessions");
  }
  function messageError(info) {
    const error = info?.error;
    if (!error) return null;
    const data = error.data || {};
    const raw = data.responseBody || data.message || error.message || error.name || "OpenCode 请求失败";
    return el("div", { class: "wsp-message-error" },
      el("p", { text: typeof raw === "string" ? raw : JSON.stringify(raw, null, 2) }));
  }

  function skillIcon() {
    return el("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", class: "wsp-skill-icon" },
      el("path", { d: "m12 2 8 5v10l-8 5-8-5V7l8-5Zm0 10 8-5M12 12 4 7m8 5v10M4 12l8 5 8-5" }));
  }

  function skillMention(skill) {
    return el("span", { class: "wsp-skill-mention", title: skill.description || skill.path }, skillIcon(),
      el("span", { text: skill.name }));
  }

  function fileMention(path) {
    return el("span", { class: "wsp-file-mention", title: path },
      el("svg", { viewBox: "0 0 20 20", "aria-hidden": "true", class: "wsp-file-icon" },
        el("path", { d: "M4 2.5h8l4 4v11H4z M12 2.5v4h4 M7 10h6 M7 13h6" })),
      el("span", { text: path.split("/").at(-1) }));
  }

  function fileReferencePattern(paths) {
    const names = [...new Set(paths)].sort((a, b) => b.length - a.length)
      .map(path => path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return names.length ? new RegExp(`(^|[\\s(\\[{"'])@(${names.join("|")})`, "g") : null;
  }

  function appendFileMentions(node, text, paths) {
    const pattern = fileReferencePattern(paths);
    let cursor = 0;
    if (pattern) for (const match of text.matchAll(pattern)) {
      const start = match.index + match[1].length;
      node.append(document.createTextNode(text.slice(cursor, start)), fileMention(match[2]));
      cursor = start + match[2].length + 1;
    }
    node.append(document.createTextNode(text.slice(cursor)));
  }

  function updateSkillInput() {
    state.fileReferences = composer.fileReferences;
    input.dataset.empty = String(!composingInput && !composer.value);
    if (composingInput) return;
    const errorLine = view.querySelector("#wsp-skill-error");
    const draft = composer.value.trimStart();
    const match = draft.match(/^\/([A-Za-z0-9_-]+)(?:\s|$)/);
    const name = match?.[1] || (draft.startsWith("/") ? draft.slice(1).split(/\s/)[0] : "");
    const skill = state.skills.find(item => item.name === name);
    const known = builtInCommands.some(item => item.name === name) || state.commands.some(item => item.name === name);
    const invalid = !!name && !known && !skill;
    errorLine.hidden = !invalid;
    errorLine.textContent = invalid ? `未识别命令 /${name}，请通过 /skills 重新选择` : "";
    input.setAttribute("aria-invalid", String(invalid));
    composer.highlightSkill(skill);
  }

  function skillForTool(part) {
    if (part.tool === "skill") return { name: part.state?.input?.name || part.state?.metadata?.name || "技能", kind: "自动选择技能" };
    if (part.tool !== "read") return null;
    const path = String(part.state?.input?.filePath || part.state?.input?.path || "").replaceAll("\\", "/");
    const loaded = state.messages.flatMap(message => (message.parts || [])
      .filter(tool => tool.type === "tool" && tool.tool === "skill" && tool.state?.metadata?.dir)
      .map(tool => ({ name: tool.state.metadata.name || tool.state.input?.name, path: tool.state.metadata.dir })));
    const installed = [...state.skills, ...state.messages.filter(m => m.skillUse).map(m => m.skillUse), ...loaded];
    const skill = installed.find(item => path.startsWith(item.path.replaceAll("\\", "/") + "/"));
    if (skill) return { name: skill.name, kind: path.endsWith("/SKILL.md") ? "读取技能" : "读取技能资源" };
    if (path.endsWith("/SKILL.md")) return { name: path.split("/").at(-2), kind: "读取技能" };
    return null;
  }

  function toolPart(part) {
    const stateInfo = part.state || {};
    const toolName = String(part.tool || "工具");
    const toolLabels = { read: "读取文件", write: "写入文件", edit: "编辑文件", bash: "运行命令", glob: "查找文件", grep: "搜索内容", list: "列出目录", task: "执行任务" };
    const status = stateInfo.status || "running";
    const statusLabels = { completed: "已完成", running: "运行中", pending: "等待中", error: "失败" };
    const key = part.id || part.callID || `${toolName}:${JSON.stringify(stateInfo.input || {})}`;
    const card = el("details", { class: `wsp-tool ${status}` });
    if (state.expandedTools.get(key) ?? (status === "error" || status === "running")) card.open = true;
    card.addEventListener("toggle", () => state.expandedTools.set(key, card.open));
    const input = stateInfo.input || {};
    const skill = skillForTool(part);
    if (skill) card.classList.add("wsp-skill-use");
    const subject = skill?.name || input.filePath || input.path || input.command || input.pattern || input.description || "";
    card.append(el("summary", { class: "wsp-tool-head" },
      skill ? skillIcon() : el("span", { class: "wsp-tool-symbol", text: { read: "↳", write: "+", edit: "±", bash: ">", grep: "⌕", glob: "⌕" }[toolName] || "·" }),
      el("span", { class: "wsp-tool-title", text: skill?.kind || toolLabels[toolName] || toolName }),
      el("span", { class: "wsp-tool-subject", title: String(subject), text: String(subject) }),
      el("span", { class: "wsp-tool-status", text: skill && status === "completed" ? (toolName === "skill" ? "已加载" : "已读取") : statusLabels[status] || status }),
      el("svg", { class: "wsp-tool-chevron", viewBox: "0 0 24 24", "aria-hidden": "true" }, el("path", { d: "m6 9 6 6 6-6" }))));
    const rawOutput = stateInfo.output || stateInfo.error || "";
    const output = typeof rawOutput === "string" ? rawOutput : JSON.stringify(rawOutput, null, 2);
    const inputText = stateInfo.input ? JSON.stringify(stateInfo.input, null, 2) : "";
    const body = el("div", { class: "wsp-tool-body" });
    if (inputText) body.append(el("div", { class: "wsp-tool-label", text: "输入" }), el("pre", { text: inputText.slice(0, 4000) }));
    body.append(el("div", { class: "wsp-tool-label", text: stateInfo.error ? "错误" : "结果" }),
      el("pre", { text: (output || "等待结果…").slice(0, 12000) }));
    card.append(body);
    return card;
  }

  function toolGroup(parts) {
    const finished = parts.every((part) => part.state?.status === "completed");
    const group = el("div", { class: "wsp-tool-group" },
      el("div", { class: "wsp-tool-group-head" },
        el("span", { class: "wsp-tool-symbol", text: "›_" }),
        el("strong", { text: finished ? "已完成的步骤" : "工具操作" }),
        el("span", { class: "wsp-tool-group-count", text: `${parts.length} 项活动` })));
    for (const part of parts) {
      const status = part.state?.status || "running";
      const labels = { read: "读取文件", write: "写入文件", edit: "编辑文件", bash: "运行命令", glob: "查找文件", grep: "搜索内容", list: "列出目录", task: "执行任务" };
      const name = labels[part.tool] || part.tool || "工具操作";
      const subject = part.state?.input?.filePath || part.state?.input?.path || part.state?.input?.command || part.state?.input?.description || "";
      const row = el("details", { class: `wsp-tool-step ${status}` },
        el("summary", {},
          el("span", { class: "wsp-step-check", text: status === "completed" ? "✓" : status === "error" ? "!" : "◉" }),
          el("span", { class: "wsp-step-label", text: subject ? `${name} · ${subject}` : name }),
          el("span", { class: "wsp-step-duration", text: toolDuration(part) || (status === "running" ? "运行中" : status === "error" ? "失败" : "") })));
      const key = `step:${part.id || part.callID || `${name}:${subject}`}`;
      if (state.expandedTools.get(key)) row.open = true;
      row.addEventListener("toggle", () => state.expandedTools.set(key, row.open));
      const inputText = part.state?.input ? JSON.stringify(part.state.input, null, 2) : "";
      const rawOutput = part.state?.output || part.state?.error || "";
      const output = typeof rawOutput === "string" ? rawOutput : JSON.stringify(rawOutput, null, 2);
      row.append(el("div", { class: "wsp-tool-step-detail" },
        ...(inputText ? [el("strong", { text: "输入" }), el("pre", { text: inputText.slice(0, 4000) })] : []),
        el("strong", { text: part.state?.error ? "错误" : "结果" }),
        el("pre", { text: (output || "等待结果…").slice(0, 12000) })));
      group.append(row);
    }
    return group;
  }

  function questionCard(request) {
    const drafts = state.questionDrafts.get(request.id) || [];
    state.questionDrafts.set(request.id, drafts);
    const questions = request.questions || [];
    const page = Math.min(state.questionPages.get(request.id) || 0, Math.max(questions.length - 1, 0));
    state.questionPages.set(request.id, page);
    const question = questions[page];
    const draft = drafts[page] || { selected: [], custom: "" };
    drafts[page] = draft;
    const card = el("div", { class: "wsp-question" },
      el("div", { class: "wsp-question-head" },
        el("strong", { text: "OpenCode 需要你的回答" }),
        el("span", { class: "wsp-question-count", text: `${page + 1} / ${questions.length}` })));
    if (!question) return card;
    const group = el("fieldset", { class: "wsp-question-group" },
      el("legend", { text: question.question || question.header || `问题 ${page + 1}` }));
    if (question.multiple) group.append(el("p", { text: "可多选" }));
    for (const option of question.options || []) {
      const inputOption = el("input", {
        type: question.multiple ? "checkbox" : "radio", name: `wsp-question-${request.id}-${page}`,
        value: option.label, checked: draft.selected.includes(option.label),
        onchange: (event) => {
          state.questionErrors.delete(request.id);
          errorLine.textContent = "";
          if (question.multiple) {
            draft.selected = event.target.checked
              ? [...draft.selected, option.label]
              : draft.selected.filter((label) => label !== option.label);
          } else draft.selected = [option.label];
        },
      });
      group.append(el("label", { class: "wsp-question-option" }, inputOption,
        el("span", {}, el("strong", { text: option.label }),
          option.description ? el("small", { text: option.description }) : null)));
    }
    if (question.custom !== false) group.append(el("input", {
      class: "wsp-question-custom", type: "text", placeholder: "或输入自己的回答",
      "data-request-id": request.id, "data-question-index": page,
      value: draft.custom, oninput: (event) => {
        draft.custom = event.target.value;
        state.questionErrors.delete(request.id);
        errorLine.textContent = "";
      },
    }));
    card.append(group);
    const errorLine = el("p", { class: "wsp-question-error", text: state.questionErrors.get(request.id) || "" });
    const actions = el("div", { class: "wsp-question-actions" });
    const navigation = el("div", { class: "wsp-question-navigation" });
    navigation.append(el("button", {
      class: "wsp-mini", type: "button", text: "← 上一题", disabled: page === 0,
      onclick: () => { state.questionPages.set(request.id, page - 1); renderMain(); },
    }));
    if (page < questions.length - 1) {
      navigation.append(el("button", {
        class: "wsp-mini primary", type: "button", text: "下一题 →",
        onclick: () => { state.questionPages.set(request.id, page + 1); renderMain(); },
      }));
    } else {
      navigation.append(el("button", { class: "wsp-mini primary", type: "button", text: "提交回答", onclick: async () => {
        const answers = questions.map((question, index) => {
          const answerDraft = drafts[index] || { selected: [], custom: "" };
          const values = question.multiple ? answerDraft.selected.slice() : answerDraft.selected.slice(0, 1);
          if (answerDraft?.custom.trim()) {
            if (!question.multiple) return [answerDraft.custom.trim()];
            values.push(answerDraft.custom.trim());
          }
          return values;
        });
        const missing = answers.findIndex((answer) => !answer.length);
        if (missing >= 0) {
          state.questionPages.set(request.id, missing);
          state.questionErrors.set(request.id, "请先回答每个问题");
          renderMain();
          return;
        }
        try {
          await api(`workspace/projects/${encodeURIComponent(state.projectId)}/questions/${encodeURIComponent(request.id)}/reply`, {
            method: "POST", body: { answers }, silent: true,
          });
          state.questionDrafts.delete(request.id);
          state.questionPages.delete(request.id);
          state.questionErrors.delete(request.id);
          await refreshSelected();
        } catch (error) { errorLine.textContent = `提交失败：${detail(error)}`; }
      } }));
    }
    actions.append(navigation, el("button", { class: "wsp-mini", type: "button", text: "跳过", onclick: async () => {
      try {
        await api(`workspace/projects/${encodeURIComponent(state.projectId)}/questions/${encodeURIComponent(request.id)}/reject`, {
          method: "POST", body: {}, silent: true,
        });
        state.questionDrafts.delete(request.id);
        state.questionPages.delete(request.id);
        state.questionErrors.delete(request.id);
        await refreshSelected();
      } catch (error) { errorLine.textContent = `操作失败：${detail(error)}`; }
    } }));
    card.append(errorLine, actions);
    return card;
  }

  function compactionCard(kind) {
    const label = kind === "busy" ? "正在压缩上下文…" : kind === "ready" ? "上下文已压缩" : "上下文压缩未完成";
    return el("div", { class: `wsp-compaction ${kind}`, role: "status" },
      statusIcon(kind), el("span", { text: label }));
  }

  function renderMessages() {
    if (!state.sessionId) {
      content.append(empty("开始一段新对话", "选择项目后新建对话，OpenCode 会在该项目目录中工作。",
        activeProject() ? ["新建对话", () => createSession()] : ["新建项目", openAddProject]));
      return;
    }
    if (!state.messages.length && !state.permissions.some((item) => item.sessionID === state.sessionId) &&
      !state.questions.some((item) => item.sessionID === state.sessionId)) {
      content.append(empty("输入你的开发需求", "发送第一条消息后，这里会实时显示回复和工具操作。"));
      if (state.compactingSessionId === state.sessionId) content.append(compactionCard("busy"));
      if (state.pendingAction) content.append(el("div", { class: "wsp-action-progress", text: `正在执行 ${state.pendingAction}…` }));
      if (state.actionError) content.append(el("div", { class: "wsp-error", text: state.actionError }));
      return;
    }
    const displayMessages = [];
    for (const message of state.messages) {
      const previous = displayMessages.at(-1);
      if (message.info?.role === "assistant" && previous?.info?.role === "assistant" &&
          message.info.parentID && previous.info.parentID === message.info.parentID) {
        previous.parts.push(...(message.parts || []));
        if (message.info.error) previous.errorInfo = message.info;
        previous.modelInfo = message.info;
      } else displayMessages.push({ info: message.info, parts: [...(message.parts || [])],
        errorInfo: message.info?.error ? message.info : null, modelInfo: message.info, skillUse: message.skillUse });
    }
    let previousDay = "";
    for (const message of displayMessages) {
      const day = messageDay(message);
      if (day !== previousDay) {
        content.append(el("div", { class: "wsp-date-divider", text: day }));
        previousDay = day;
      }
      if (message.info?.role === "user" && message.parts.some((part) => part.type === "compaction")) {
        const replies = state.messages.filter((item) => item.info?.role === "assistant" &&
          item.info.parentID === message.info.id);
        const complete = replies.some((item) => item.info.summary && item.info.finish && !item.info.error);
        const failed = replies.some((item) => item.info.error);
        content.append(compactionCard(complete ? "ready" : failed ? "error" :
          compactionRunning() ? "busy" : "error"));
        continue;
      }
      const role = message?.info?.role || "assistant";
      const row = el("article", { class: "wsp-message " + (role === "user" ? "user" : "assistant") });
      if (role !== "user") row.append(el("img", { class: "wsp-avatar", src: "sona-code-icon.png", alt: "", width: 30, height: 30 }));
      const body = el("div", { class: "wsp-message-inner" });
      const modelInfo = message.modelInfo || message.info;
      if (role !== "user") body.append(el("div", { class: "wsp-message-meta" },
        el("strong", { text: "Sona" }),
        `OpenCode${modelInfo?.providerID && modelInfo?.modelID ? ` · ${modelInfo.providerID} / ${modelInfo.modelID}` : ""}`));
      const parts = message.parts || [];
      const references = role === "user" ? parts.filter(part => part.type === "file" &&
        part.url?.startsWith("file:") && part.filename).map(part => part.filename) : [];
      if (message.skillUse) {
        body.append(el("div", { class: "wsp-text wsp-skill-message" }, skillMention(message.skillUse),
          message.skillUse.arguments ? ` ${message.skillUse.arguments}` : ""));
      }
      const error = role !== "user" ? messageError(message.errorInfo) : null;
      if (error) body.append(error);
      else if (role !== "user" && !parts.length) {
        const busy = state.statuses?.[state.sessionId]?.type === "busy";
        body.append(el("div", { class: busy ? "wsp-thinking" : "wsp-no-response", text: busy ? "正在思考…" : "本轮未收到回复" }));
      }
      let pendingTools = [];
      const flushTools = () => {
        if (pendingTools.length) body.append(toolGroup(pendingTools));
        pendingTools = [];
      };
      for (const [partIndex, part] of parts.entries()) {
        if (part.type === "tool") {
          if (skillForTool(part)) { flushTools(); body.append(toolPart(part)); }
          else pendingTools.push(part);
          continue;
        }
        if (!['text', 'reasoning', 'file'].includes(part.type)) continue;
        flushTools();
        if (part.type === "text" && !part.synthetic) {
          if (message.skillUse) body.append(el("details", { class: "wsp-reasoning" }, el("summary", { text: "查看已加载技能内容" }), el("pre", { text: part.text })));
          else body.append(textPart(part, role, references));
        }
        else if (part.type === "reasoning" && part.text) {
          const reasoning = el("details", { class: "wsp-reasoning" }, el("summary", { text: "思考过程" }), el("div", { text: part.text }));
          const key = `reasoning:${part.id || `${message.info?.id}:${partIndex}`}`;
          if (state.expandedTools.get(key)) reasoning.open = true;
          reasoning.addEventListener("toggle", () => state.expandedTools.set(key, reasoning.open));
          body.append(reasoning);
        } else if (part.type === "file") {
          const filename = part.filename || "附件";
          if (references.includes(filename)) {
            const pattern = fileReferencePattern([filename]);
            if (!parts.some(item => item.type === "text" && !item.synthetic && pattern.test(item.text || ""))) {
              body.append(el("div", { class: "wsp-text" }, fileMention(filename)));
            }
            continue;
          }
          const file = el("div", { class: "wsp-message-file" },
            el("span", { text: `📎 ${filename}` }));
          if (/^data:image\/(?:png|jpeg|gif|webp);base64,/.test(part.url || "")) {
            file.append(el("img", { src: part.url, alt: filename, loading: "lazy" }));
          }
          body.append(file);
        }
      }
      flushTools();
      const column = el("div", { class: "wsp-message-column" }, body, messageActions(message));
      row.append(column);
      content.append(row);
    }
    if (state.compactingSessionId === state.sessionId && !state.messages.some((message) =>
      message.info?.role === "user" && message.parts?.some((part) => part.type === "compaction"))) {
      content.append(compactionCard("busy"));
    }
    for (const permission of state.permissions.filter((item) => item.sessionID === state.sessionId)) {
      const card = el("div", { class: "wsp-permission" },
        el("strong", { text: `需要确认：${permission.permission || permission.action || "工具操作"}` }),
        el("p", { text: (permission.patterns || permission.resources || []).join("、") || "OpenCode 请求继续执行此操作。" }));
      const actions = el("div", { class: "wsp-permission-actions" });
      for (const [label, reply, cls] of [["允许一次", "once", "wsp-mini primary"], ["始终允许", "always", "wsp-mini"], ["拒绝", "reject", "wsp-mini"]]) {
        actions.append(el("button", { class: cls, type: "button", text: label, onclick: () => replyPermission(permission.id, reply) }));
      }
      card.append(actions);
      content.append(card);
    }
    for (const question of state.questions.filter((item) => item.sessionID === state.sessionId)) {
      content.append(questionCard(question));
    }
    if (state.pendingAction) content.append(el("div", { class: "wsp-action-progress", text: `正在执行 ${state.pendingAction}…` }));
    if (state.actionError) content.append(el("div", { class: "wsp-error", text: state.actionError }));
  }

  function panelIntro(title, description) {
    return el("div", { class: "wsp-panel-intro" }, el("div", {},
      el("h2", { text: title }), el("p", { text: description })));
  }

  function diffRows(diff) {
    if (diff.derived) {
      const input = diff.input || {};
      if (typeof input.oldString === "string" && typeof input.newString === "string") {
        return [...input.oldString.split("\n").map((text, index) => ({ type: "removed", number: index + 1, text: `-${text}` })),
          ...input.newString.split("\n").map((text, index) => ({ type: "added", number: index + 1, text: `+${text}` }))].slice(0, 120);
      }
      const preview = typeof input.content === "string" ? input.content : "";
      if (!preview) return [];
      return preview.split("\n").slice(0, 120).map((text, index) => ({ type: "context", number: index + 1, text: ` ${text}` }));
    }
    const patch = String(diff.patch || "");
    if (patch) {
      let oldLine = 1;
      let newLine = 1;
      return patch.split("\n").flatMap((line) => {
        const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)/);
        if (hunk) { oldLine = Number(hunk[1]); newLine = Number(hunk[2]); return [{ type: "hunk", number: "", text: line }]; }
        if (line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("\\ No newline")) return [];
        if (line.startsWith("+")) return [{ type: "added", number: newLine++, text: line }];
        if (line.startsWith("-")) return [{ type: "removed", number: oldLine++, text: line }];
        return [{ type: "context", number: newLine++, text: line }].map((row) => { oldLine++; return row; });
      }).slice(0, 240);
    }
    if (typeof diff.before !== "string" || typeof diff.after !== "string") return [];
    const before = diff.before.split("\n");
    const after = diff.after.split("\n");
    let prefix = 0;
    while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix++;
    let suffix = 0;
    while (suffix < before.length - prefix && suffix < after.length - prefix &&
      before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix++;
    const rows = [];
    const start = Math.max(0, prefix - 3);
    if (start) rows.push({ type: "hunk", number: "", text: `… ${start} 行未显示` });
    for (let i = start; i < prefix; i++) rows.push({ type: "context", number: i + 1, text: ` ${before[i]}` });
    for (let i = prefix; i < before.length - suffix && rows.length < 235; i++) rows.push({ type: "removed", number: i + 1, text: `-${before[i]}` });
    for (let i = prefix; i < after.length - suffix && rows.length < 235; i++) rows.push({ type: "added", number: i + 1, text: `+${after[i]}` });
    for (let i = after.length - suffix; i < Math.min(after.length, after.length - suffix + 3) && rows.length < 240; i++)
      rows.push({ type: "context", number: i + 1, text: ` ${after[i]}` });
    if (after.length - suffix + 3 < after.length) rows.push({ type: "hunk", number: "", text: "… 其余未改动行已省略" });
    return rows;
  }

  function diffLineCounts(diff) {
    const additions = Number(diff.additions);
    const deletions = Number(diff.deletions);
    if (diff.additions != null && diff.deletions != null && Number.isFinite(additions) && Number.isFinite(deletions))
      return { additions, deletions };
    const input = diff.derived ? diff.input || {} : diff;
    if (typeof input.oldString === "string" && typeof input.newString === "string") {
      const count = (value) => value ? value.split("\n").length : 0;
      return { additions: count(input.newString), deletions: count(input.oldString) };
    }
    if (typeof diff.patch === "string" && diff.patch) {
      const lines = diff.patch.split("\n");
      return { additions: lines.filter((line) => line.startsWith("+") && !line.startsWith("+++ ")).length,
        deletions: lines.filter((line) => line.startsWith("-") && !line.startsWith("--- ")).length };
    }
    return null;
  }

  function changedFiles() {
    const writtenFiles = new Map();
    if (state.sessionId && !state.diffs.length) {
      for (const message of state.messages) for (const part of message.parts || []) {
        if (part.type !== "tool" || !["write", "edit", "apply_patch", "multiedit"].includes(part.tool)) continue;
        if (part.state?.status !== "completed") continue;
        const path = part.state?.input?.filePath || part.state?.input?.path;
        if (path) writtenFiles.set(path, { file: path, derived: true, input: part.state.input });
      }
    }
    const diffs = state.diffs.length ? state.diffs : [...writtenFiles.values()];
    return [...new Map(diffs.map((diff, index) => [diff.file || diff.path || index, diff])).values()];
  }

  function renderChanges() {
    content.append(panelIntro("文件改动", "在同一处查看当前对话涉及的文件与代码差异。"));
    const diffs = changedFiles();
    if (!state.sessionId || !diffs.length) {
      content.append(empty("暂无文件改动", "OpenCode 修改文件后，这里会显示改动摘要。"));
      return;
    }
    const counts = diffs.map(diffLineCounts);
    const knownCounts = counts.filter(Boolean);
    const additions = knownCounts.reduce((sum, count) => sum + count.additions, 0);
    const deletions = knownCounts.reduce((sum, count) => sum + count.deletions, 0);
    const countLabel = (value, sign) => !knownCounts.length ? "—" :
      `${knownCounts.length < diffs.length ? "≥" : ""}${sign}${value}`;
    const overview = el("div", { class: "wsp-diff-overview" });
    for (const [value, label, className] of [[diffs.length, "修改文件", ""], [countLabel(additions, "+"), "新增行", "add"], [countLabel(deletions, "−"), "删除行", "remove"]]) {
      overview.append(el("div", { class: `wsp-diff-metric ${className}` },
        el("strong", { text: String(value) }), el("span", { text: label })));
    }
    content.append(overview);
    for (const [index, diff] of diffs.entries()) {
      const lineCounts = counts[index];
      const title = diff.file || diff.path || "文件";
      const card = el("section", { class: "wsp-diff-file" },
        el("div", { class: "wsp-diff-head" },
          el("span", { class: "wsp-file-badge", text: diff.derived ? "已写入" : "修改" }),
          el("span", { class: "wsp-diff-path", title, text: title }),
          el("span", { class: "wsp-diff-stats", text: lineCounts ? `+${lineCounts.additions}  −${lineCounts.deletions}` : "" })));
      const rows = diffRows(diff);
      if (rows.length) {
        if (diff.derived) card.append(el("p", { class: "wsp-diff-preview-label", text: "写入内容预览 · OpenCode 未返回完整逐行差异" }));
        const body = el("div", { class: "wsp-diff-body" });
        for (const row of rows) body.append(el("div", { class: `wsp-diff-line ${row.type}` },
          el("span", { class: "wsp-line-number", text: String(row.number) }), el("span", { text: row.text })));
        card.append(body);
      } else card.append(el("p", { class: "wsp-diff-unavailable", text: diff.derived
        ? "OpenCode 记录了文件写入，但没有返回可显示的逐行差异。" : "此文件没有可显示的逐行差异。" }));
      content.append(card);
    }
  }

  function renderSessionTrajectory() {
    content.classList.add("wsp-content-trajectory");
    if (!state.sessionId) {
      content.append(empty("请选择对话", "选择对话后查看其代理调用轨迹。"));
      return;
    }
    if (state.recordingError) {
      content.append(errorCard("加载轨迹失败：" + state.recordingError, refreshSelected));
      return;
    }
    if (!state.recordingData) {
      content.append(empty("正在加载轨迹", "读取当前对话的代理调用记录。"));
      return;
    }
    if (!state.recordingData.turns?.length) {
      content.append(empty("暂无对话轨迹", "经过代理录制的模型请求完成后会显示在这里；直连请求没有代理记录。"));
      return;
    }
    const signature = JSON.stringify(state.recordingData);
    if (!trajectoryView || trajectorySignature !== signature) {
      trajectoryView = el("div", { class: "wsp-trajectory" });
      trajectorySignature = signature;
      renderTrajectorySession(trajectoryView, state.recordingData.session_key, {
        data: state.recordingData, embedded: true, retry: refreshSelected,
      });
    }
    if (trajectoryView.parentNode !== content) content.append(trajectoryView);
  }

  function renderActivity() {
    content.append(panelIntro("对话活动", "这里汇总当前对话的模型请求和工具步骤；点击已录制的模型请求查看调用详情。"));
    const timeline = el("div", { class: "wsp-trace-list" });
    let count = 0;
    const addEvent = (time, title, description, duration, color, callId) => {
      timeline.append(el(callId ? "a" : "div", {
        class: "wsp-trace-row" + (callId ? " wsp-trace-link" : ""),
        ...(callId ? { href: "#/calls/" + encodeURIComponent(callId), title: "查看模型请求详情" } : {}),
      },
        el("span", { class: "wsp-trace-time", text: eventTime(time) }),
        el("span", { class: `wsp-trace-dot ${color}` }),
        el("span", { class: "wsp-trace-content" }, el("strong", { text: title }), el("span", { text: description })),
        el("span", { class: "wsp-trace-duration", text: duration })));
      count++;
    };
    const turns = state.recordingData?.turns || [];
    const linkedCalls = new Set();
    const assistants = state.messages.filter((message) => message.info?.role === "assistant");
    const callOwners = workspaceCallOwners(assistants, turns);
    const addCall = (turn) => {
      linkedCalls.add(turn.call_id);
      addEvent(turn.started_at, "模型请求", turn.model || "OpenCode 回复",
        turn.duration_ms != null ? `${(turn.duration_ms / 1000).toFixed(1)} 秒` : "",
        turn.status === "error" || turn.status_code >= 400 ? "red" : "", turn.call_id);
    };
    for (const message of assistants) {
      const info = message.info;
      const start = info.time?.created;
      const end = info.time?.completed;
      const seconds = start && end ? (new Date(end) - new Date(start)) / 1000 : NaN;
      const calls = turns.filter((turn) => callOwners.get(turn.call_id) === info.id);
      if (calls.length) calls.forEach(addCall);
      else addEvent(start, "模型请求", [info.providerID, info.modelID].filter(Boolean).join(" / ") || "OpenCode 回复",
        Number.isFinite(seconds) && seconds >= 0 ? `${seconds.toFixed(1)} 秒 · 暂无调用记录` : "暂无调用记录", info.error ? "red" : "");
      for (const part of message.parts || []) {
        if (part.type !== "tool") continue;
        const detail = part.state?.input || {};
        const description = detail.filePath || detail.path || detail.command || detail.pattern || detail.description || "工具操作";
        const title = { read: "读取文件", write: "文件改动", edit: "文件改动", bash: "运行命令", grep: "搜索内容", glob: "查找文件", task: "执行任务" }[part.tool] || part.tool || "工具操作";
        const status = part.state?.status;
        addEvent(part.state?.time?.start || start, title, String(description), toolDuration(part) || (status === "running" ? "运行中" : ""),
          status === "error" ? "red" : status === "completed" ? "green" : "amber");
      }
    }
    for (const turn of turns) if (!linkedCalls.has(turn.call_id)) addCall(turn);
    if (state.recordingError) content.append(el("p", { class: "wsp-error", text: "调用记录加载失败：" + state.recordingError }));
    for (const request of [...state.permissions, ...state.questions].filter((item) => item.sessionID === state.sessionId))
      addEvent(request.time?.created, "等待确认", request.permission || "需要你的回答", "待确认", "amber");
    if (count) content.append(timeline);
    else content.append(empty("暂无对话活动", "OpenCode 回复或调用工具后，这里会按顺序展示。"));
  }

  function renderTasks() {
    content.append(el("h2", { class: "wsp-section-title", text: "任务与子对话" }),
      el("p", { class: "wsp-section-note", text: "OpenCode 在本轮对话中维护的待办和委派任务。" }));
    if (!state.todos.length && !state.children.length) {
      content.append(empty("暂无任务", "OpenCode 制定计划或启动子任务后会显示在这里。"));
      return;
    }
    for (const todo of state.todos) {
      const status = todo.status || "pending";
      content.append(el("div", { class: `wsp-todo ${status}` },
        el("span", { class: "wsp-todo-mark", text: status === "completed" ? "✓" : status === "in_progress" ? "◉" : "○" }),
        el("span", { text: todo.content || todo.title || "任务" }),
        el("small", { text: status === "completed" ? "已完成" : status === "in_progress" ? "进行中" : "待处理" })));
    }
    if (state.children.length) content.append(el("h3", { class: "wsp-section-title", text: "子对话" }));
    for (const child of state.children) {
      content.append(el("button", { class: "wsp-child", type: "button", onclick: () => selectSession(state.projectId, child.id) },
        el("strong", { text: sessionTitle(child) }), el("small", { text: stamp(child) })));
    }
  }

  function renderMain(forceBottom = false) {
    const previousTop = scroll.scrollTop;
    const previousMaximum = Math.max(0, scroll.scrollHeight - scroll.clientHeight);
    const nearBottom = previousMaximum - previousTop <= 80;
    const stickToBottom = state.tab === "chat" && (forceBottom || nearBottom);
    const active = document.activeElement;
    const editingQuestion = active?.classList?.contains("wsp-question-custom")
      ? { id: active.dataset.requestId, index: active.dataset.questionIndex,
          start: active.selectionStart, end: active.selectionEnd } : null;
    const keepTrajectory = state.tab === "trajectory" && !state.recordingError &&
      trajectoryView?.parentNode === content && trajectorySignature === JSON.stringify(state.recordingData);
    if (!keepTrajectory) content.replaceChildren();
    content.classList.remove("wsp-content-trajectory");
    const changeCount = view.querySelector("#wsp-change-count");
    changeCount.textContent = String(state.sessionId ? changedFiles().length : 0);
    changeCount.title = `修改了 ${changeCount.textContent} 个文件`;
    if (state.tab === "trajectory") renderSessionTrajectory();
    if (state.tab === "chat") renderMessages();
    if (state.tab === "changes") renderChanges();
    if (state.tab === "activity") renderActivity();
    if (state.tab === "tasks") renderTasks();
    renderStatsLine();
    followLatest = stickToBottom;
    scroll.scrollTop = stickToBottom ? scroll.scrollHeight : previousTop;
    if (stickToBottom) {
      const sessionId = state.sessionId;
      requestAnimationFrame(() => {
        if (alive() && state.sessionId === sessionId && state.tab === "chat" && followLatest) {
          scroll.scrollTop = scroll.scrollHeight;
        }
      });
    }
    if (editingQuestion) {
      const restored = Array.from(content.querySelectorAll(".wsp-question-custom")).find((field) =>
        field.dataset.requestId === editingQuestion.id && field.dataset.questionIndex === editingQuestion.index);
      if (restored) {
        restored.focus({ preventScroll: true });
        restored.setSelectionRange(editingQuestion.start, editingQuestion.end);
      }
    }
  }

  async function loadSessions(project) {
    try {
      const data = await api(`workspace/projects/${encodeURIComponent(project.id)}/sessions`, { silent: true });
      if (!alive()) return;
      const items = (data.items || []).filter((item) => !item.time?.archived);
      items.sort((a, b) => (b.time?.updated || 0) - (a.time?.updated || 0));
      state.sessions.set(project.id, items);
      if (state.projectId === project.id) lastSessionListRefresh = Date.now();
      state.errors.delete(project.id);
      if (state.projectId === project.id && !items.some((item) => item.id === state.sessionId) &&
          !state.sessionDetails.has(state.sessionId)) {
        state.sessionId = items[0]?.id || null;
        scrollToLatestOnLoad = true;
        workspaceSelection.sessionId = state.sessionId;
        refreshSelected();
      }
      renderSidebar();
      renderHeader();
    } catch (error) {
      if (!alive()) return;
      state.errors.set(project.id, detail(error));
      renderSidebar();
      if (state.projectId === project.id) {
        content.replaceChildren(el("div", { class: "wsp-error", text: detail(error) }));
      }
    }
  }

  function selectedModel() {
    const [providerID, modelID] = (state.chosenModels.get(state.projectId) || "").split("\u0000");
    return providerID && modelID ? { provider_id: providerID, model_id: modelID }
      : state.defaultModel ? { provider_id: state.defaultModel.providerID, model_id: state.defaultModel.modelID } : {};
  }

  function updateModelButton(clearInvalidVariant = true) {
    const choice = selectedModel();
    const provider = state.providers.find((item) => item.id === choice.provider_id);
    const model = provider?.models?.[choice.model_id];
    modelButton.replaceChildren(
      el("span", { class: "wsp-model-label", text: model ? model.name || choice.model_id : "选择模型" }),
      el("svg", { class: "wsp-model-chevron", viewBox: "0 0 24 24", "aria-hidden": "true" },
        el("path", { d: "m6 9 6 6 6-6" })));
    modelButton.title = model?.source === "application" && !model.route_through_proxy
      ? "本次调用不会进入本地调用记录与代理轨迹；工作区对话和工具活动仍然可见。" : model ? `${provider.id} / ${choice.model_id}` : "请添加或选择模型";
    const variants = Object.keys(model?.variants || {});
    variantSelect.replaceChildren(el("option", { value: "", text: "默认" }),
      ...variants.map((variant) => el("option", { value: variant, text: variant })));
    variantSelect.hidden = true;
    const chosen = state.chosenVariants.get(state.projectId) || "";
    variantSelect.value = variants.includes(chosen) ? chosen : "";
    if (clearInvalidVariant && chosen && !variants.includes(chosen)) state.chosenVariants.set(state.projectId, "");
    variantTrigger.hidden = !variants.length;
    variantLabel.textContent = variantSelect.value || "默认";
    variantTrigger.dataset.default = String(!variantSelect.value);
    variantTrigger.title = variantSelect.value ? "模型推理强度：" + variantSelect.value : "模型推理强度：默认";
    if (!variants.length) closeVariantPicker();
    else if (!variantPicker.hidden) renderVariantPicker();
  }

  function closeModelPicker() {
    modelPicker.hidden = true;
    modelButton.setAttribute("aria-expanded", "false");
  }

  function positionPicker(picker, anchor, align = "left") {
    const margin = 12;
    const gap = 7;
    const anchorRect = anchor.getBoundingClientRect();
    const pickerRect = picker.getBoundingClientRect();
    const left = align === "right" ? anchorRect.right - pickerRect.width : anchorRect.left;
    picker.style.left = `${Math.max(margin, Math.min(left, window.innerWidth - pickerRect.width - margin))}px`;
    picker.style.top = `${Math.max(margin, anchorRect.top - pickerRect.height - gap)}px`;
  }

  function closeAgentPicker() {
    agentPicker.hidden = true;
    agentTrigger.setAttribute("aria-expanded", "false");
  }

  function closeVariantPicker() {
    variantPicker.hidden = true;
    variantTrigger.setAttribute("aria-expanded", "false");
  }

  function renderVariantPicker() {
    variantPicker.replaceChildren(...Array.from(variantSelect.options, (option) =>
      el("button", { type: "button", role: "menuitemradio", class: "wsp-agent-option" +
        (option.value === variantSelect.value ? " selected" : ""),
      "aria-checked": option.value === variantSelect.value ? "true" : "false",
      text: option.value || "默认", onclick: () => {
        variantSelect.value = option.value;
        variantSelect.dispatchEvent(new Event("change", { bubbles: true }));
        closeVariantPicker();
        variantTrigger.focus();
      } })));
    if (!variantPicker.hidden) positionPicker(variantPicker, variantTrigger);
  }

  function openVariantPicker() {
    closeModelPicker();
    closeAgentPicker();
    hideAutocomplete();
    variantPicker.hidden = false;
    variantTrigger.setAttribute("aria-expanded", "true");
    renderVariantPicker();
  }

  function renderAgentPicker() {
    agentLabel.textContent = agentSelect.selectedOptions[0]?.textContent || "Build · 执行";
    agentPicker.replaceChildren(...Array.from(agentSelect.options, (option) =>
      el("button", { type: "button", role: "menuitemradio", class: "wsp-agent-option" +
        (option.value === agentSelect.value ? " selected" : ""),
      "aria-checked": option.value === agentSelect.value ? "true" : "false",
      text: option.textContent, onclick: () => {
        agentSelect.value = option.value;
        agentSelect.dispatchEvent(new Event("change", { bubbles: true }));
        closeAgentPicker();
        agentTrigger.focus();
      } })));
    if (!agentPicker.hidden) positionPicker(agentPicker, agentTrigger);
  }

  function openAgentPicker() {
    closeModelPicker();
    closeVariantPicker();
    hideAutocomplete();
    agentPicker.hidden = false;
    agentTrigger.setAttribute("aria-expanded", "true");
    renderAgentPicker();
  }

  function openProviderKeyDialog(provider) {
    const keyInput = el("input", { type: "password", autocomplete: "new-password", placeholder: "粘贴 Provider API Key" });
    const message = el("p", { class: "wsp-question-error" });
    const mask = el("div", { class: "wsp-modal-mask" },
      el("div", { class: "wsp-modal", role: "dialog", "aria-modal": "true", "aria-label": `设置 ${provider.name || provider.id} API Key` },
        el("h2", { text: `设置 ${provider.name || provider.id} API Key` }),
        el("p", { text: state.connectedProviders.has(provider.id)
          ? "OpenCode 已保存此 Provider 的凭据。新 Key 保存后会替换旧凭据；认证是否有效，以实际请求结果为准。"
          : "保存到 OpenCode 的本地凭据存储。该 Provider 后续请求会使用这个 Key。" }),
        keyInput, message,
        el("div", { class: "wsp-modal-actions" },
          el("button", { class: "wsp-mini", type: "button", text: "取消", onclick: () => mask.remove() }),
          el("button", { class: "wsp-mini primary", type: "button", text: "保存到 OpenCode", onclick: async (event) => {
            const button = event.currentTarget;
            const key = keyInput.value.trim();
            if (!key) { message.textContent = "请先输入 API Key"; keyInput.focus(); return; }
            button.disabled = true;
            message.textContent = "正在保存到本机 OpenCode 凭据存储…";
            try {
              await api(`workspace/projects/${encodeURIComponent(state.projectId)}/providers/${encodeURIComponent(provider.id)}/api-key`, {
                method: "POST", body: { key }, silent: true,
              });
              keyInput.value = "";
              state.connectedProviders.add(provider.id);
              mask.remove();
              renderModelPicker();
              toast(`${provider.name || provider.id} 凭据已保存到 OpenCode`, "ok");
            } catch (error) {
              message.textContent = `保存失败：${detail(error)}`;
              button.disabled = false;
            }
          } }))));
    mask.addEventListener("click", (event) => { if (event.target === mask) mask.remove(); });
    document.body.append(mask);
    keyInput.focus();
  }

  function chooseModel(value) {
    if (state.projectId) {
      state.chosenModels.set(state.projectId, value);
      state.chosenVariants.set(state.projectId, "");
      try { localStorage.setItem(`sona-code:model:${state.projectId}`, value); } catch (_) { /* Storage may be unavailable. */ }
    }
    updateModelButton();
    renderStatsLine();
    closeModelPicker();
    input.focus();
  }

  function renderModelPicker() {
    modelList.replaceChildren();
    if (state.modelLoadError) {
      modelList.append(el("p", { class: "wsp-picker-empty", text: `模型加载失败：${state.modelLoadError}` }));
      return;
    }
    const query = modelSearch.value.trim().toLocaleLowerCase();
    const selected = state.chosenModels.get(state.projectId) || "";
    if (!query) modelList.append(el("button", { type: "button", class: "wsp-model-option" + (!selected ? " selected" : ""),
      onclick: () => chooseModel("") },
      el("span", { text: "应用默认模型" }), el("small", { text: "使用模型页面配置的默认模型" })));
    let count = 0;
    for (const provider of state.providers) {
      const items = Object.entries(provider.models || {}).filter(([id, model]) =>
        `${provider.id} ${provider.name || ""} ${id} ${model.name || ""}`.toLocaleLowerCase().includes(query));
      if (!items.length) continue;
      const group = el("section", { class: "wsp-model-group" },
        el("div", { class: "wsp-model-group-title" },
          el("strong", { text: provider.name || provider.id }),
          el("span", { text: `${items.length} 个模型 · ${provider.source === "application" ? (provider.route_through_proxy ? "应用配置 · 代理记录" : "应用配置 · 直连") : "OpenCode 原生配置"}` })),
        provider.source === "application"
          ? el("a", { class: "wsp-provider-key", href: "#/models", text: "管理模型" })
          : el("button", { type: "button", class: "wsp-provider-key", text: "设置 API Key", onclick: () => openProviderKeyDialog(provider) }));
      for (const [id, model] of items) {
        const value = `${provider.id}\u0000${id}`;
        group.append(el("button", { type: "button", class: "wsp-model-option" + (value === selected ? " selected" : ""),
          title: `${provider.id} / ${id}`, onclick: () => chooseModel(value) },
          el("span", { text: model.name || id }),
          el("small", { text: [id, model.capabilities?.input?.image ? "图片" : "", model.capabilities?.reasoning ? "思考" : "",
            model.source === "application" ? (model.route_through_proxy ? "代理记录" : "直连 · 不记录代理轨迹") : "原生路由",
            model.capabilities?.toolcall === false ? "不支持工具调用" : ""].filter(Boolean).join(" · ") })));
        count++;
      }
      modelList.append(group);
    }
    if (!count && query) modelList.append(el("p", { class: "wsp-picker-empty", text: "没有匹配的模型" }));
    else if (!state.providers.length) modelList.append(el("p", { class: "wsp-picker-empty", text: "暂无可用模型，请前往模型页面添加。" }));
    if (!modelPicker.hidden) positionPicker(modelPicker, modelButton, "right");
  }

  function openModelPicker() {
    if (!state.projectId) return;
    loadModels(state.projectId);
    closeAgentPicker();
    closeVariantPicker();
    hideAutocomplete();
    modelPicker.hidden = false;
    modelButton.setAttribute("aria-expanded", "true");
    modelSearch.value = "";
    renderModelPicker();
    positionPicker(modelPicker, modelButton, "right");
    modelSearch.focus();
  }

  async function loadModels(projectId) {
    state.providers = [];
    state.defaultModel = null;
    state.modelLoadError = "";
    updateModelButton(false);
    try {
      const data = await api(`workspace/projects/${encodeURIComponent(projectId)}/models`, { silent: true });
      if (!alive() || state.projectId !== projectId) return;
      state.providers = Array.isArray(data.providers) ? data.providers.slice().sort((a, b) =>
        (a.id === "opencode" ? -1 : b.id === "opencode" ? 1 : (a.name || a.id).localeCompare(b.name || b.id))) : [];
      state.defaultModel = data.default_model || null;
      state.connectedProviders = new Set(Array.isArray(data.connected) ? data.connected : []);
      const value = state.chosenModels.get(projectId) || "";
      if (value) {
        const [providerID, modelID] = value.split("\u0000");
        if (!state.providers.some((provider) => provider.id === providerID && provider.models?.[modelID])) {
          state.chosenModels.set(projectId, "");
          state.chosenVariants.set(projectId, "");
          try { localStorage.removeItem(`sona-code:model:${projectId}`); } catch (_) {}
          toast("原模型已不可用，请重新选择模型", "error");
        }
      }
      updateModelButton();
      renderStatsLine();
      if (!modelPicker.hidden) renderModelPicker();
    } catch (error) {
      if (alive() && state.projectId === projectId) {
        state.modelLoadError = detail(error);
        if (!modelPicker.hidden) renderModelPicker();
      }
    }
  }

  async function loadAgents(projectId) {
    try {
      const data = await api(`workspace/projects/${encodeURIComponent(projectId)}/agents`, { silent: true });
      if (!alive() || state.projectId !== projectId) return;
      state.agents = Array.isArray(data) ? data.filter((item) => item.mode === "primary" && !item.hidden) : [];
      agentSelect.replaceChildren(...state.agents.map((item) =>
        el("option", { value: item.name, text: item.name === "build" ? "Build · 执行" : item.name === "plan" ? "Plan · 规划" : item.name })));
      if (!state.agents.length) agentSelect.append(el("option", { value: "build", text: "Build" }));
      agentSelect.value = state.chosenAgents.get(projectId) || "build";
      if (!agentSelect.value) agentSelect.selectedIndex = 0;
      renderAgentPicker();
      loadCommands(projectId);
    } catch (_) { /* The default Build agent remains usable. */ }
  }

  async function loadCommands(projectId) {
    const request = ++commandLoadRequest;
    try {
      const [data, skills] = await Promise.all([
        api(`workspace/projects/${encodeURIComponent(projectId)}/commands`, { silent: true }),
        api(`workspace/projects/${encodeURIComponent(projectId)}/skills?agent=${encodeURIComponent(agentSelect.value || "build")}`, { silent: true }),
      ]);
      if (alive() && state.projectId === projectId && request === commandLoadRequest) {
        state.skills = skills.items || [];
        state.commands = (Array.isArray(data) ? data : []).filter(command => command.source !== "skill" || state.skills.some(skill => skill.name === command.name));
        updateSkillInput();
        if (!commandMenu.hidden && autocompleteKind === "commands") renderCommandMenu();
      }
    } catch (_) {
      if (alive() && state.projectId === projectId && request === commandLoadRequest) {
        state.commands = []; state.skills = []; updateSkillInput();
      }
    }
  }

  const builtInCommands = [
    { name: "help", description: "查看可用命令" },
    { name: "new", description: "新建对话" },
    { name: "compact", description: "压缩当前对话上下文" },
    { name: "summarize", description: "压缩当前对话上下文（/compact 别名）" },
    { name: "models", description: "选择 Provider 和模型" },
    { name: "agents", description: "选择 Agent" },
    { name: "skills", description: "选择已启用的技能" },
    { name: "stop", description: "停止当前任务" },
    { name: "settings", description: "打开设置" },
  ];
  let commandPaletteOpen = false;

  function hideAutocomplete() {
    commandMenu.hidden = true;
    commandPaletteOpen = false;
    view.querySelector("#wsp-attach").setAttribute("aria-expanded", "false");
    autocompleteKind = null;
    fileMentionRange = null;
    fileSearchRequest++;
    if (fileSearchTimer) clearTimeout(fileSearchTimer);
  }

  function currentFileMention() {
    const cursor = composer.selectionStart ?? composer.value.length;
    if (composer.isFileReferenceAt(cursor)) return null;
    const before = composer.value.slice(0, cursor);
    const match = /(^|[\s(\[{"'])@([^\s@]*)$/u.exec(before);
    if (!match) return null;
    return { query: match[2], start: cursor - match[2].length - 1, end: cursor };
  }

  function drawFileMenu(message) {
    commandMenu.replaceChildren(el("div", { class: "wsp-command-heading", text: "项目文件 · ↑ ↓ 选择 · Enter 引用" }));
    if (message) {
      commandMenu.append(el("div", { class: "wsp-file-state", text: message }));
      commandMenu.hidden = false;
      return;
    }
    for (const [index, path] of fileMatches.entries()) {
      commandMenu.append(el("button", { type: "button", role: "option", "aria-selected": String(index === state.commandSelectedIndex),
        class: `wsp-command-option wsp-file-option${index === state.commandSelectedIndex ? " selected" : ""}`,
        onclick: () => selectFileReference(path),
        onmouseenter: () => { state.commandSelectedIndex = index; updateCommandSelection(); } },
      el("strong", { text: path }), el("span", { text: "文件" })));
    }
    if (!fileMatches.length) commandMenu.append(el("div", { class: "wsp-file-state", text: "没有找到匹配的项目文件" }));
    commandMenu.hidden = false;
  }

  function renderFileMenu(mention) {
    autocompleteKind = "files";
    fileMentionRange = mention;
    state.commandSelectedIndex = 0;
    fileMatches = [];
    drawFileMenu("正在搜索项目文件…");
    if (fileSearchTimer) clearTimeout(fileSearchTimer);
    const requestID = ++fileSearchRequest;
    const projectId = state.projectId;
    if (!projectId) { drawFileMenu("请先选择项目"); return; }
    if (!mention.query) { drawFileMenu("继续输入文件名以搜索项目文件"); return; }
    fileSearchTimer = setTimeout(async () => {
      try {
        const data = await api(`workspace/projects/${encodeURIComponent(projectId)}/files?query=${encodeURIComponent(mention.query)}`, { silent: true });
        const current = currentFileMention();
        if (!alive() || state.projectId !== projectId || autocompleteKind !== "files" ||
            requestID !== fileSearchRequest || current?.start !== mention.start || current?.query !== mention.query) return;
        fileMatches = Array.isArray(data.items) ? data.items.filter((item) => typeof item === "string") : [];
        state.commandSelectedIndex = 0;
        drawFileMenu();
      } catch (error) {
        if (requestID !== fileSearchRequest || autocompleteKind !== "files") return;
        drawFileMenu(`文件搜索失败：${detail(error)}`);
      }
    }, 120);
  }

  function selectFileReference(path) {
    state.fileReferences = composer.fileReferences;
    if (state.attachments.length + state.fileReferences.length >= 8 &&
        !state.fileReferences.some((item) => item.path === path)) {
      toast("一条消息最多添加 8 个附件或文件引用", "error");
      hideAutocomplete();
      return;
    }
    const mention = fileMentionRange || currentFileMention();
    if (!mention) return;
    composer.insertFileReference(path, mention.start, mention.end);
    updateSkillInput();
    hideAutocomplete();
    input.focus();
  }

  function renderCommandMenu() {
    const mention = currentFileMention();
    if (!commandPaletteOpen && mention && state.projectId) {
      renderFileMenu(mention);
      return;
    }
    const draft = composer.value.trimStart();
    if (!commandPaletteOpen && (!draft.startsWith("/") || draft.includes("\n") || draft.includes(" "))) {
      hideAutocomplete();
      return;
    }
    autocompleteKind = "commands";
    fileMentionRange = null;
    const query = commandPaletteOpen ? "" : draft.slice(1).toLocaleLowerCase();
    commandMenu.replaceChildren(el("div", { class: "wsp-command-heading", text: "命令 · ↑ ↓ 选择 · Enter 选中" }));
    const commands = [...builtInCommands, ...state.commands.filter((item) =>
      !builtInCommands.some((builtIn) => builtIn.name === item.name))];
    const matches = commands.filter((item) => item.name.toLocaleLowerCase().includes(query)).slice(0, 12);
    state.commandSelectedIndex = Math.max(0, Math.min(state.commandSelectedIndex, matches.length - 1));
    for (const [index, command] of matches.entries()) {
      commandMenu.append(el("button", { type: "button", role: "option", "aria-selected": String(index === state.commandSelectedIndex),
        class: `wsp-command-option${index === state.commandSelectedIndex ? " selected" : ""}`, onclick: () => selectCommand(command),
        onmouseenter: () => { state.commandSelectedIndex = index; updateCommandSelection(); } },
      el("strong", { text: `/${command.name}` }), el("span", { text: command.description || "OpenCode 命令" })));
    }
    commandMenu.hidden = commandMenu.children.length === 1;
    view.querySelector("#wsp-attach").setAttribute("aria-expanded", String(!commandMenu.hidden && commandPaletteOpen));
  }

  function updateCommandSelection() {
    const options = commandMenu.querySelectorAll(".wsp-command-option");
    options.forEach((option, index) => {
      const selected = index === state.commandSelectedIndex;
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-selected", String(selected));
      if (selected) option.scrollIntoView({ block: "nearest" });
    });
  }

  function selectCommand(command) {
    if (command.name === "skills") { hideAutocomplete(); void openSkillPicker(); return; }
    composer.value = `/${command.name} `;
    updateSkillInput();
    composer.setSelectionRange(composer.value.length, composer.value.length);
    hideAutocomplete();
    input.focus();
  }

  async function openSkillPicker() {
    if (!state.projectId) { toast("请先选择项目", "error"); return; }
    hideAutocomplete();
    closeModelPicker(); closeAgentPicker(); closeVariantPicker();
    const projectId = state.projectId;
    const dialog = createSkillDialog("选择技能");
    const search = el("input", { type: "search", placeholder: "搜索已启用的技能", "aria-label": "搜索已启用的技能" });
    const list = el("div", { class: "skill-picker-list" }, el("p", { text: "正在读取技能…" }));
    dialog.body.append(el("p", { text: "选择技能后补充任务并发送。也可以直接描述任务，让 AI 自动选择。" }), search, list,
      el("div", { class: "wsp-modal-actions" }, el("a", { class: "wsp-mini", href: "#/skills", text: "管理技能" })));
    search.focus();
    try {
      const data = await api(`workspace/projects/${encodeURIComponent(projectId)}/skills?agent=${encodeURIComponent(agentSelect.value || "build")}`, { silent: true });
      if (!alive() || !dialog.alive() || state.projectId !== projectId) return;
      function drawSkills() {
        const query = search.value.trim().toLocaleLowerCase();
        const matches = (data.items || []).filter((item) => `${item.name} ${item.description}`.toLocaleLowerCase().includes(query));
        list.replaceChildren();
        for (const skill of matches) list.append(el("button", { class: "skill-picker-option", type: "button", onclick: () => {
          const draft = composer.value.trim();
          const argumentsText = draft.startsWith("/skills") ? draft.replace(/^\/skills(?:\s+|$)/, "") : draft.startsWith("/") ? "" : draft;
          composer.value = `/${skill.name} ${argumentsText}`;
          state.skills = data.items || [];
          updateSkillInput();
          if (!state.commands.some((item) => item.name === skill.name)) state.commands.push({ name: skill.name, description: skill.description, source: "skill" });
          dialog.close(); input.focus(); composer.setSelectionRange(composer.value.length, composer.value.length);
        } }, el("strong", { text: skill.name }), el("span", { text: skill.description })));
        if (!matches.length) list.append(el("p", { text: query ? "没有匹配的技能" : "暂无可用技能，请在技能菜单中导入并启用。" }));
        if (data.unavailable?.length) list.append(el("p", { class: "wsp-question-error", text: `以下技能未被 OpenCode 加载或存在同名命令：${data.unavailable.join("、")}` }));
      }
      search.addEventListener("input", drawSkills);
      search.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") { event.preventDefault(); list.querySelector("button")?.focus(); }
        if (event.key === "Enter") { event.preventDefault(); list.querySelector("button")?.click(); }
      });
      drawSkills();
    } catch (error) {
      if (dialog.alive()) list.replaceChildren(el("p", { class: "wsp-question-error", role: "alert", text: "读取技能失败：" + detail(error) }));
    }
  }

  async function executeBuiltIn(name) {
    if (name === "help") { composer.value = "/"; renderCommandMenu(); return true; }
    if (name === "new") { composer.value = ""; await createSession(); return true; }
    if (name === "compact" || name === "summarize") {
      if (!state.sessionId) { toast("请先选择对话", "error"); return true; }
      composer.value = "";
      await performSessionAction(state.projectId, activeSession(), "summarize");
      return true;
    }
    if (name === "skills") { await openSkillPicker(); return true; }
    if (name === "models") { composer.value = ""; openModelPicker(); return true; }
    if (name === "agents") { composer.value = ""; agentSelect.focus(); return true; }
    if (name === "settings") { location.hash = "#/settings"; return true; }
    if (name === "stop") {
      if (state.sessionId) await api(`${sessionPath(state.projectId, state.sessionId)}/abort`, { method: "POST", body: {}, silent: true });
      composer.value = "";
      await refreshSelected();
      return true;
    }
    return false;
  }

  function connectEvents(projectId) {
    if (eventProjectId === projectId) return;
    if (events) events.close();
    events = null;
    eventProjectId = projectId;
    if (!projectId || typeof EventSource === "undefined") return;
    events = new EventSource(`api/workspace/projects/${encodeURIComponent(projectId)}/events`);
    events.onmessage = (event) => {
      scheduleRefresh();
      try {
        const update = JSON.parse(event.data);
        if (update.type === "session.updated" && Date.now() - lastSessionListRefresh > 700) {
          const project = activeProject();
          if (project) loadSessions(project);
        }
      } catch (_) { /* A malformed event should not interrupt updates. */ }
    };
    events.onopen = () => { if (alive() && state.projectId === projectId) loadModels(projectId); };
    events.onerror = () => { /* EventSource reconnects; polling also remains active. */ };
  }

  function scheduleRefresh() {
    if (refreshTimer) return;
    refreshTimer = setTimeout(() => { refreshTimer = null; refreshSelected(); }, 120);
  }

  async function refreshSelected() {
    if (!alive() || !state.projectId || !state.sessionId) return;
    if (refreshing) { refreshRequested = true; return; }
    refreshing = true;
    const projectId = state.projectId;
    const sessionId = state.sessionId;
    const base = sessionPath(projectId, sessionId);
    try {
      const [messages, statuses, permissions, questions, diffs, todos, children, session, recording] = await Promise.allSettled([
        api(`${base}/messages`, { silent: true }),
        api(`workspace/projects/${encodeURIComponent(projectId)}/status`, { silent: true }),
        api(`workspace/projects/${encodeURIComponent(projectId)}/permissions`, { silent: true }),
        api(`workspace/projects/${encodeURIComponent(projectId)}/questions`, { silent: true }),
        api(`${base}/diff`, { silent: true }),
        state.tab === "tasks" ? api(`${base}/todo`, { silent: true }) : Promise.resolve(state.todos),
        state.tab === "tasks" ? api(`${base}/children`, { silent: true }) : Promise.resolve(state.children),
        api(base, { silent: true }),
        ["trajectory", "activity"].includes(state.tab) ? api(`${base}/trajectory`, { silent: true }) : Promise.resolve(state.recordingData),
      ]);
      if (!alive() || state.projectId !== projectId || state.sessionId !== sessionId) return;
      if (recording.status === "fulfilled") { state.recordingData = recording.value; state.recordingError = ""; }
      else state.recordingError = detail(recording.reason);
      if (messages.status === "fulfilled") state.messages = Array.isArray(messages.value) ? messages.value : [];
      if (statuses.status === "fulfilled") state.statuses = statuses.value || {};
      if (permissions.status === "fulfilled") state.permissions = Array.isArray(permissions.value) ? permissions.value : [];
      if (questions.status === "fulfilled") state.questions = Array.isArray(questions.value) ? questions.value : [];
      if (diffs.status === "fulfilled") state.diffs = Array.isArray(diffs.value) ? diffs.value : [];
      if (todos.status === "fulfilled") state.todos = Array.isArray(todos.value) ? todos.value : [];
      if (children.status === "fulfilled") {
        state.children = Array.isArray(children.value) ? children.value : [];
        for (const child of state.children) state.sessionDetails.set(child.id, child);
      }
      if (session.status === "fulfilled" && session.value?.id) state.sessionDetails.set(session.value.id, session.value);
      if (messages.status === "rejected") content.replaceChildren(el("div", { class: "wsp-error", text: detail(messages.reason) }));
      else {
        renderMain(scrollToLatestOnLoad);
        scrollToLatestOnLoad = false;
      }
      renderHeader();
    } finally {
      refreshing = false;
      if (refreshRequested) { refreshRequested = false; scheduleRefresh(); }
    }
  }

  function selectProject(projectId) {
    hideAutocomplete();
    scrollToLatestOnLoad = true;
    state.projectId = projectId;
    state.skills = []; state.commands = []; updateSkillInput();
    composer.clearFileReferences();
    state.attachments = [];
    state.fileReferences = [];
    renderAttachments();
    if (!state.chosenModels.has(projectId)) {
      try { state.chosenModels.set(projectId, localStorage.getItem(`sona-code:model:${projectId}`) || ""); }
      catch (_) { state.chosenModels.set(projectId, ""); }
    }
    const remembered = workspaceSelection.projectId === projectId ? workspaceSelection.sessionId : null;
    state.sessionId = remembered || (state.sessions.get(projectId) || [])[0]?.id || null;
    state.messages = []; state.permissions = []; state.questions = []; state.questionDrafts.clear(); state.diffs = []; state.todos = []; state.children = [];
    state.recordingData = null; state.recordingError = ""; trajectoryView = null; trajectorySignature = "";
    state.tab = remembered ? workspaceSelection.tab || "chat" : "chat";
    workspaceSelection = { projectId, sessionId: state.sessionId, tab: state.tab };
    root.classList.remove("show-side");
    updateSidebarButton();
    renderSidebar(); renderHeader(); renderMain();
    connectEvents(projectId);
    loadModels(projectId);
    loadAgents(projectId);
    loadCommands(projectId);
    if (!state.sessions.has(projectId)) {
      const project = activeProject();
      if (project) loadSessions(project);
    } else refreshSelected();
  }

  function selectSession(projectId, sessionId) {
    hideAutocomplete();
    scrollToLatestOnLoad = true;
    state.projectId = projectId;
    state.skills = []; state.commands = []; updateSkillInput();
    composer.clearFileReferences();
    state.attachments = [];
    state.fileReferences = [];
    renderAttachments();
    if (!state.chosenModels.has(projectId)) {
      try { state.chosenModels.set(projectId, localStorage.getItem(`sona-code:model:${projectId}`) || ""); }
      catch (_) { state.chosenModels.set(projectId, ""); }
    }
    state.sessionId = sessionId;
    state.messages = []; state.permissions = []; state.questions = []; state.questionDrafts.clear(); state.diffs = []; state.todos = []; state.children = [];
    state.recordingData = null; state.recordingError = ""; trajectoryView = null; trajectorySignature = "";
    state.tab = "chat";
    workspaceSelection = { projectId, sessionId, tab: state.tab };
    root.classList.remove("show-side");
    updateSidebarButton();
    renderSidebar(); renderHeader(); renderMain();
    connectEvents(projectId);
    loadModels(projectId);
    loadAgents(projectId);
    loadCommands(projectId);
    refreshSelected();
  }

  async function createSession(projectId = state.projectId || state.projects[0]?.id) {
    if (!projectId) { openAddProject(); return; }
    try {
      const session = await api(`workspace/projects/${encodeURIComponent(projectId)}/sessions`, {
        method: "POST", body: {}, silent: true,
      });
      if (!alive()) return;
      const items = state.sessions.get(projectId) || [];
      state.sessions.set(projectId, [session, ...items.filter((item) => item.id !== session.id)]);
      selectSession(projectId, session.id);
      input.focus();
    } catch (error) { toast("新建对话失败：" + detail(error), "error"); }
  }

  async function ensureSessionForSend() {
    if (state.sessionId) return;
    const session = await api(`workspace/projects/${encodeURIComponent(state.projectId)}/sessions`, {
      method: "POST", body: {}, silent: true,
    });
    const items = state.sessions.get(state.projectId) || [];
    state.sessions.set(state.projectId, [session, ...items.filter((item) => item.id !== session.id)]);
    state.sessionId = session.id;
    workspaceSelection = { projectId: state.projectId, sessionId: session.id };
    connectEvents(state.projectId);
    renderSidebar(); renderHeader();
  }

  async function replyPermission(permissionId, reply) {
    try {
      await api(`workspace/projects/${encodeURIComponent(state.projectId)}/permissions/${encodeURIComponent(permissionId)}/reply`, {
        method: "POST", body: { reply }, silent: true,
      });
      await refreshSelected();
    } catch (error) { toast("权限处理失败：" + detail(error), "error"); }
  }

  function openAddProject() {
    const pathInput = el("input", { type: "text", placeholder: "项目目录的绝对路径", autocomplete: "off", spellcheck: "false" });
    const errorLine = el("div", { class: "wsp-error hidden" });
    const choose = el("button", { class: "wsp-mini", type: "button", text: "选择目录…", onclick: async () => {
      const dialog = window.__TAURI__?.dialog;
      if (!dialog?.open) { pathInput.focus(); return; }
      try {
        const selected = await dialog.open({ directory: true, multiple: false, title: "选择项目目录" });
        if (selected) pathInput.value = selected;
      } catch (error) { errorLine.textContent = detail(error); errorLine.classList.remove("hidden"); }
    } });
    if (!window.__TAURI__?.dialog?.open) choose.hidden = true;
    const mask = el("div", { class: "wsp-modal-mask" },
      el("div", { class: "wsp-modal", role: "dialog", "aria-modal": "true", "aria-label": "新建项目" }, el("h2", { text: "新建项目" }),
        el("p", { text: "输入现有目录的绝对路径，或选择一个目录作为 OpenCode 工作区。" }),
        el("div", { class: "wsp-modal-row" }, pathInput, choose), errorLine,
        el("div", { class: "wsp-modal-actions" },
          el("button", { class: "wsp-mini", type: "button", text: "取消", onclick: () => mask.remove() }),
          el("button", { class: "wsp-mini primary", type: "button", text: "创建项目", onclick: async () => {
            const path = pathInput.value.trim();
            if (!path) { errorLine.textContent = "请输入项目目录"; errorLine.classList.remove("hidden"); return; }
            try {
              const project = await api("workspace/projects", { method: "POST", body: { path }, silent: true });
              if (!alive()) return;
              mask.remove();
              state.projects = [project, ...state.projects.filter((item) => item.id !== project.id)];
              state.collapsedProjects.delete(project.id);
              selectProject(project.id);
              loadSessions(project);
            } catch (error) { errorLine.textContent = detail(error); errorLine.classList.remove("hidden"); }
          } }))));
    mask.addEventListener("click", (event) => { if (event.target === mask) mask.remove(); });
    document.body.append(mask);
    pathInput.focus();
  }

  function openRenameProject(project) {
    const nameInput = el("input", { type: "text", value: project.name, maxlength: "100" });
    const errorLine = el("p", { class: "wsp-question-error" });
    const mask = el("div", { class: "wsp-modal-mask" },
      el("div", { class: "wsp-modal", role: "dialog", "aria-modal": "true", "aria-label": "重命名工作区" },
        el("h2", { text: "重命名工作区" }),
        el("p", { text: "仅修改侧边栏中的显示名称，目录路径保持不变。" }), nameInput, errorLine,
        el("div", { class: "wsp-modal-actions" },
          el("button", { class: "wsp-mini", type: "button", text: "取消", onclick: () => mask.remove() }),
          el("button", { class: "wsp-mini primary", type: "button", text: "保存", onclick: async () => {
            const name = nameInput.value.trim();
            if (!name) { errorLine.textContent = "请输入工作区名称"; return; }
            try {
              const updated = await api(`workspace/projects/${encodeURIComponent(project.id)}`, {
                method: "PATCH", body: { name }, silent: true,
              });
              state.projects = state.projects.map((item) => item.id === project.id ? updated : item);
              mask.remove(); renderSidebar(); renderHeader();
            } catch (error) { errorLine.textContent = detail(error); }
          } }))));
    mask.addEventListener("click", (event) => { if (event.target === mask) mask.remove(); });
    document.body.append(mask);
    nameInput.focus(); nameInput.select();
  }

  async function openProjectDirectory(project) {
    try {
      await api(`workspace/projects/${encodeURIComponent(project.id)}/open`, { method: "POST", body: {}, silent: true });
    } catch (error) { toast("打开目录失败：" + detail(error), "error"); }
  }

  async function removeProject(project) {
    if (!await confirmDeletion("删除工作区项目", `确定从工作区列表删除项目“${project.name}”？项目目录和 OpenCode 对话仍保留在磁盘上。`)) return;
    try {
      await api(`workspace/projects/${encodeURIComponent(project.id)}`, { method: "DELETE", silent: true });
      state.projects = state.projects.filter((item) => item.id !== project.id);
      state.sessions.delete(project.id);
      state.errors.delete(project.id);
      if (state.projectId === project.id) {
        state.projectId = null; state.sessionId = null; state.messages = [];
        workspaceSelection = { projectId: null, sessionId: null };
        connectEvents(null);
        if (state.projects.length) selectProject(state.projects[0].id);
        else { renderSidebar(); renderHeader(); renderMain(); }
      } else renderSidebar();
    } catch (error) { toast("删除工作区失败：" + detail(error), "error"); }
  }

  view.querySelector("#wsp-new").addEventListener("click", openAddProject);
  const outsideActions = (event) => {
    if (!event.target.closest(".wsp-row-menu-wrap, .wsp-action-menu")) closeRowMenus();
  };
  const rowMenuKeydown = (event) => {
    if (event.key === "Escape" && activeRowMenu) {
      const trigger = activeRowMenu.trigger;
      closeRowMenus();
      trigger.focus({ preventScroll: true });
    }
  };
  sideList.addEventListener("scroll", closeRowMenus, { passive: true });
  window.addEventListener("resize", closeRowMenus);
  document.addEventListener("keydown", rowMenuKeydown);
  addCleanup(() => {
    closeRowMenus();
    window.removeEventListener("resize", closeRowMenus);
    document.removeEventListener("keydown", rowMenuKeydown);
  });
  document.addEventListener("pointerdown", outsideActions);
  addCleanup(() => document.removeEventListener("pointerdown", outsideActions));
  view.querySelector("#wsp-abort").addEventListener("click", async () => {
    if (!state.projectId || !state.sessionId) return;
    try {
      await api(`${sessionPath(state.projectId, state.sessionId)}/abort`, { method: "POST", body: {}, silent: true });
      await refreshSelected();
    } catch (error) { toast("停止任务失败：" + detail(error), "error"); }
  });
  view.querySelector("#wsp-menu").addEventListener("click", () => {
    if (window.matchMedia("(max-width: 700px)").matches) root.classList.toggle("show-side");
    else {
      root.classList.toggle("side-collapsed");
      try { localStorage.setItem("sona-code:sidebar-collapsed", root.classList.contains("side-collapsed") ? "1" : "0"); }
      catch (_) { /* Storage may be unavailable. */ }
    }
    updateSidebarButton();
  });
  for (const id of ["#wsp-side-close", "#wsp-side-scrim"]) {
    view.querySelector(id).addEventListener("click", () => { root.classList.remove("show-side"); updateSidebarButton(); });
  }
  view.querySelector("#wsp-search").addEventListener("input", (event) => { state.search = event.target.value; renderSidebar(); });
  const searchShortcut = (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && location.hash === "#/workspace") {
      event.preventDefault();
      view.querySelector("#wsp-search").focus();
    }
  };
  document.addEventListener("keydown", searchShortcut);
  addCleanup(() => document.removeEventListener("keydown", searchShortcut));
  window.addEventListener("resize", updateSidebarButton);
  addCleanup(() => window.removeEventListener("resize", updateSidebarButton));
  modelButton.addEventListener("click", () => modelPicker.hidden ? openModelPicker() : closeModelPicker());
  agentTrigger.addEventListener("click", () => agentPicker.hidden ? openAgentPicker() : closeAgentPicker());
  variantTrigger.addEventListener("click", () => variantPicker.hidden ? openVariantPicker() : closeVariantPicker());
  agentTrigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && agentPicker.hidden) { event.preventDefault(); openAgentPicker(); agentPicker.querySelector("button")?.focus(); }
    else if (event.key === "Escape") closeAgentPicker();
  });
  agentPicker.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeAgentPicker(); agentTrigger.focus(); }
  });
  variantTrigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && variantPicker.hidden) {
      event.preventDefault();
      openVariantPicker();
      variantPicker.querySelector("button")?.focus();
    } else if (event.key === "Escape") closeVariantPicker();
  });
  variantPicker.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeVariantPicker(); variantTrigger.focus(); }
  });
  view.querySelector("#wsp-model-close").addEventListener("click", closeModelPicker);
  modelSearch.addEventListener("input", renderModelPicker);
  modelSearch.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeModelPicker(); modelButton.focus(); }
  });
  const outsidePicker = (event) => {
    if (!modelPicker.hidden && !modelPicker.contains(event.target) && event.target !== modelButton) closeModelPicker();
    if (!agentPicker.hidden && !agentPicker.contains(event.target) && event.target !== agentTrigger) closeAgentPicker();
    if (!variantPicker.hidden && !variantPicker.contains(event.target) && event.target !== variantTrigger) closeVariantPicker();
  };
  document.addEventListener("pointerdown", outsidePicker);
  addCleanup(() => document.removeEventListener("pointerdown", outsidePicker));
  agentSelect.addEventListener("change", () => {
    if (state.projectId) state.chosenAgents.set(state.projectId, agentSelect.value);
    renderAgentPicker();
    if (state.projectId) loadCommands(state.projectId);
  });
  const repositionPickers = () => {
    if (!modelPicker.hidden) positionPicker(modelPicker, modelButton, "right");
    if (!agentPicker.hidden) positionPicker(agentPicker, agentTrigger);
    if (!variantPicker.hidden) positionPicker(variantPicker, variantTrigger);
  };
  window.addEventListener("resize", repositionPickers);
  addCleanup(() => window.removeEventListener("resize", repositionPickers));
  variantSelect.addEventListener("change", () => {
    if (state.projectId) state.chosenVariants.set(state.projectId, variantSelect.value);
    variantLabel.textContent = variantSelect.value || "默认";
    variantTrigger.dataset.default = String(!variantSelect.value);
    variantTrigger.title = variantSelect.value ? "模型推理强度：" + variantSelect.value : "模型推理强度：默认";
    renderVariantPicker();
  });
  view.querySelectorAll(".wsp-tab").forEach((button) => button.addEventListener("click", () => {
    state.tab = button.dataset.wspTab;
    workspaceSelection.tab = state.tab;
    scroll.scrollTop = 0;
    renderHeader(); renderMain(state.tab === "chat");
    if (state.tab !== "chat") refreshSelected();
  }));
  view.querySelector("#wsp-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    state.fileReferences = composer.fileReferences;
    const text = composer.value.trim();
    if ((!text && !state.attachments.length && !state.fileReferences.length) || state.sending) return;
    if (state.pendingImageCount) { toast("图片正在读取，请稍后发送", "error"); return; }
    if (!state.projectId) { openAddProject(); return; }
    if (!state.check?.found) { toast("未找到 OpenCode，请在设置中配置程序路径", "error"); return; }
    const slash = text.match(/^\/([A-Za-z0-9_-]+)(?:\s+([\s\S]*))?$/);
    const shell = text.startsWith("!") ? text.slice(1).trim() : "";
    if ((state.attachments.length || state.fileReferences.length) && (text.startsWith("/") || text.startsWith("!"))) {
      toast("附件和文件引用请与普通消息一起发送", "error"); return;
    }
    if (text.startsWith("/") && !slash) { toast("命令格式应为 /命令 参数", "error"); return; }
    if (text.startsWith("!") && !shell) { toast("请输入要运行的命令", "error"); return; }
    if (slash && !builtInCommands.some((item) => item.name === slash[1]) && !state.commands.some((item) => item.name === slash[1])) {
      toast(`未知命令：/${slash[1]}`, "error"); return;
    }
    state.sending = true; renderHeader();
    hideAutocomplete();
    state.actionError = "";
    try {
      if (slash && await executeBuiltIn(slash[1])) return;
      await ensureSessionForSend();
      const model = selectedModel();
      const variant = variantSelect.hidden || !variantSelect.value ? {} : { variant: variantSelect.value };
      const agent = agentSelect.value || "build";
      if (slash || shell) {
        state.pendingAction = slash ? `/${slash[1]}` : `!${shell}`;
        state.actionError = "";
        renderMain();
      }
      if (slash) {
        await api(`${sessionPath(state.projectId, state.sessionId)}/command`, {
          method: "POST", body: { command: slash[1], arguments: slash[2] || "", agent, ...model, ...variant }, silent: true,
        });
      } else if (shell) {
        await api(`${sessionPath(state.projectId, state.sessionId)}/shell`, {
          method: "POST", body: { command: shell, agent, ...model }, silent: true,
        });
      } else {
        await api(`${sessionPath(state.projectId, state.sessionId)}/prompt`, {
          method: "POST", body: { text, files: state.attachments.map(({ filename, mime, url }) => ({ filename, mime, url })),
            references: state.fileReferences.map(({ path }) => ({ path })), agent, ...model, ...variant }, silent: true,
        });
      }
      if (!alive()) return;
      composer.value = "";
      updateSkillInput();
      state.attachments = [];
      state.fileReferences = [];
      renderAttachments();
      await loadSessions(activeProject());
      await refreshSelected();
    } catch (error) {
      state.actionError = `操作失败：${detail(error)}`;
      toast(state.actionError, "error");
    } finally {
      state.pendingAction = "";
      state.sending = false;
      if (alive()) { updateSkillInput(); renderHeader(); renderMain(); }
    }
  });
  input.addEventListener("input", () => { updateSkillInput(); commandPaletteOpen = false; state.commandSelectedIndex = 0; renderCommandMenu(); });
  input.addEventListener("click", () => { commandPaletteOpen = false; renderCommandMenu(); });
  input.addEventListener("keyup", (event) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) renderCommandMenu();
  });
  view.querySelector("#wsp-attach").addEventListener("click", () => {
    if (commandPaletteOpen) { hideAutocomplete(); return; }
    commandPaletteOpen = true;
    state.commandSelectedIndex = 0;
    renderCommandMenu();
    input.focus();
  });
  input.addEventListener("paste", (event) => {
    const files = Array.from(event.clipboardData?.items || [])
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile()).filter(Boolean);
    const text = event.clipboardData?.getData("text/plain");
    event.preventDefault();
    if (text) document.execCommand("insertText", false, text);
    if (files.length) void addImageFiles(files);
  });
  input.addEventListener("compositionstart", () => { composingInput = true; updateSkillInput(); });
  input.addEventListener("compositionend", () => { composingInput = false; updateSkillInput(); renderCommandMenu(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { hideAutocomplete(); return; }
    const composing = composingInput || event.isComposing || event.keyCode === 229;
    if (!composing && event.key === "Backspace" && !event.metaKey && !event.ctrlKey && !event.altKey &&
        composer.deleteMentionBackward()) {
      event.preventDefault();
      updateSkillInput();
      hideAutocomplete();
      return;
    }
    if (!composing && event.key === "ArrowUp" && !event.shiftKey && !event.metaKey && !event.ctrlKey &&
        !event.altKey && commandMenu.hidden && !composer.value && !state.attachments.length &&
        !state.pendingImageCount && !state.sending) {
      const message = lastUserMessage();
      if (message) { event.preventDefault(); recallUserMessage(message); }
      return;
    }
    if (!composing && !commandMenu.hidden && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      const count = commandMenu.querySelectorAll(".wsp-command-option").length;
      if (count) {
        event.preventDefault();
        state.commandSelectedIndex = (state.commandSelectedIndex + (event.key === "ArrowDown" ? 1 : count - 1)) % count;
        updateCommandSelection();
      }
      return;
    }
    if (!composing && event.key === "Enter" && !event.shiftKey && !commandMenu.hidden) {
      event.preventDefault();
      const option = commandMenu.querySelectorAll(".wsp-command-option")[state.commandSelectedIndex];
      if (option) {
        if (autocompleteKind === "files") {
          selectFileReference(fileMatches[state.commandSelectedIndex]);
        } else {
          selectCommand({ name: option.querySelector("strong")?.textContent?.slice(1) || "" });
        }
      }
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !composing) {
      event.preventDefault();
      view.querySelector("#wsp-form").requestSubmit();
    }
  });

  const poll = setInterval(() => {
    if (!alive() || !state.sessionId) return;
    refreshSelected();
    if (Date.now() - lastSessionListRefresh > 15000) {
      const project = activeProject();
      if (project) loadSessions(project);
    }
  }, 2500);
  updateModelButton();
  updateSidebarButton();
  (async () => {
    try {
      const [projects, check] = await Promise.all([
        api("workspace/projects", { silent: true }), api("workspace/check", { silent: true }),
      ]);
      if (!alive()) return;
      state.projects = projects.items || [];
      state.collapsedProjects = new Set(state.projects.map((project) => project.id));
      state.check = check;
      view.querySelector("#wsp-connection").replaceChildren(
        el("strong", { text: check.found ? "OpenCode 可用" : "未找到 OpenCode" }),
        document.createTextNode(check.found ? ` · ${check.source}` : " · 请在设置中配置程序路径"));
      const selected = state.projects.find((item) => item.id === state.projectId) || state.projects[0];
      renderSidebar();
      if (selected) selectProject(selected.id);
      else { renderHeader(); renderMain(); }
      state.projects.filter((project) => project.id !== selected?.id).slice(0, 3).forEach(loadSessions);
    } catch (error) {
      if (alive()) content.replaceChildren(el("div", { class: "wsp-error", text: "加载工作区失败：" + detail(error) }));
    }
  })();
}
