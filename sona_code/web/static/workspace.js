"use strict";
/* Headless OpenCode workspace. The existing proxy record pages remain separate. */

let workspaceSelection = { projectId: null, sessionId: null, tab: "chat" };
const workspaceDrafts = new Map();
const workspaceModelCache = new Map();
const WORKSPACE_MODEL_TTL = 5 * 60 * 1000;

function workspaceOrderItems(items, key) {
  let order = [];
  try { order = JSON.parse(localStorage.getItem(key) || "[]"); } catch (_) {}
  if (!Array.isArray(order)) order = [];
  const positions = new Map(order.map((id, index) => [id, index]));
  return items.slice().sort((a, b) => (positions.get(a.id) ?? order.length) - (positions.get(b.id) ?? order.length));
}

function workspaceMoveItem(items, sourceId, targetId, after) {
  const source = items.find(item => item.id === sourceId);
  if (!source || sourceId === targetId || !items.some(item => item.id === targetId)) return items;
  const ordered = items.filter(item => item.id !== sourceId);
  ordered.splice(ordered.findIndex(item => item.id === targetId) + (after ? 1 : 0), 0, source);
  return ordered;
}
function workspaceConversationKey(projectId, sessionId) {
  return JSON.stringify([projectId, sessionId || null]);
}
function workspaceReadDraft(key) {
  if (!workspaceDrafts.has(key)) {
    try {
      const draft = JSON.parse(sessionStorage.getItem(`sona-code:draft:${key}`) || "null");
      if (typeof draft?.text === "string") workspaceDrafts.set(key, draft);
    } catch (_) { /* The in-memory draft remains available without browser storage. */ }
  }
  return workspaceDrafts.get(key);
}
function workspaceWriteDraft(key, draft) {
  const present = draft && (draft.text || draft.attachments?.length);
  if (present) workspaceDrafts.set(key, draft);
  else workspaceDrafts.delete(key);
  try {
    if (present) sessionStorage.setItem(`sona-code:draft:${key}`, JSON.stringify(draft));
    else sessionStorage.removeItem(`sona-code:draft:${key}`);
  } catch (_) { /* Large image drafts still survive conversation switches in memory. */ }
}

let workspaceNotificationPermissionRequested = false;
async function workspacePrepareNotifications() {
  const notification = window.__TAURI__?.notification;
  if (!notification || workspaceNotificationPermissionRequested) return;
  workspaceNotificationPermissionRequested = true;
  try {
    if (!(await notification.isPermissionGranted())) await notification.requestPermission();
  } catch (_) { /* Notifications are optional when the OS denies permission. */ }
}

async function workspaceNotifyAnswerComplete(body) {
  const notification = window.__TAURI__?.notification;
  if (!notification || document.hasFocus()) return;
  try {
    if (!(await notification.isPermissionGranted())) return;
    if (!document.hasFocus()) await notification.sendNotification({ title: "Sona Code", body });
  } catch (_) { /* Notification failures must not interrupt the workspace. */ }
}
try {
  const saved = JSON.parse(localStorage.getItem("sona-code:workspace-selection") || "null");
  if (saved && typeof saved.projectId === "string") {
    workspaceSelection = {
      projectId: saved.projectId,
      sessionId: typeof saved.sessionId === "string" ? saved.sessionId : null,
      tab: "chat",
    };
  }
} catch (_) { /* Storage may be unavailable or contain invalid data. */ }

function persistWorkspaceSelection() {
  try {
    localStorage.setItem("sona-code:workspace-selection", JSON.stringify({
      projectId: workspaceSelection.projectId, sessionId: workspaceSelection.sessionId,
    }));
  } catch (_) { /* Keep selection usable when storage is unavailable. */ }
}

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

function workspaceRelativeFile(path, projectPath = "") {
  const file = String(path || "").replace(/\\/g, "/");
  const base = String(projectPath || "").replace(/\\/g, "/").replace(/\/$/, "");
  return base && file.startsWith(`${base}/`) ? file.slice(base.length + 1) : file;
}

function workspaceDirectoryReference(part) {
  if (part?.type !== "text") return "";
  return part.text?.match(/^\n引用项目目录 @(.+)。请按需查看此目录下的文件。$/)?.[1] || "";
}

function workspaceMessageTurns(messages) {
  const turns = new Map();
  for (const message of messages) {
    const id = message.info?.role === "user" ? message.info.id : message.info?.parentID;
    if (!id) continue;
    if (!turns.has(id)) turns.set(id, { user: null, replies: [] });
    const turn = turns.get(id);
    if (message.info.role === "user") turn.user = message;
    else if (message.info.role === "assistant") turn.replies.push(message);
  }
  return turns;
}

// Native user summaries describe one turn, including all of its assistant steps.
// Never substitute the cumulative session diff for a missing turn summary.
function workspaceTurnDiffs(messages, message, projectPath = "", turn = null) {
  const userId = message.info?.parentID;
  if (!userId) return [];
  const user = turn ? turn.user : messages.find(item => item.info?.role === "user" && item.info.id === userId);
  const native = user?.info?.summary?.diffs;
  const files = new Map();
  const add = (diff) => {
    const file = workspaceRelativeFile(diff.file || diff.path, projectPath);
    if (file) files.set(file, { ...diff, file });
  };
  if (Array.isArray(native) && native.length) {
    native.forEach(add);
    return [...files.values()];
  }
  const replies = turn ? turn.replies : messages.filter(item => item.info?.role === "assistant" && item.info.parentID === userId);
  for (const reply of replies) {
    for (const part of reply.parts || []) {
      if (part.type !== "tool" || part.state?.status !== "completed" ||
          !["write", "edit", "apply_patch", "multiedit"].includes(part.tool)) continue;
      const { input = {}, metadata = {} } = part.state;
      const changes = Array.isArray(metadata.files) ? metadata.files.map(file => ({
        ...file, file: file.relativePath || file.filePath, patch: file.diff,
      })) : metadata.filediff ? [metadata.filediff] : [{
        file: input.filePath || input.path || metadata.filepath, patch: metadata.diff, derived: true, input,
        countsPartial: input.replaceAll === true,
        ...(part.tool === "write" && typeof input.content === "string" ? {
          writtenLines: input.content ? input.content.replace(/\n$/, "").split("\n").length : 0,
        } : {}),
        ...(part.tool === "write" && metadata.exists === false ? {
          before: "", after: input.content || "", derived: false,
          additions: input.content ? input.content.replace(/\n$/, "").split("\n").length : 0, deletions: 0,
        } : {}),
      }];
      for (const diff of changes) {
        const file = workspaceRelativeFile(diff.file || diff.path, projectPath);
        const previous = files.get(file);
        if (!previous) { add(diff); continue; }
        // Preserve all successful edits. These totals describe editing activity,
        // while native summaries above remain authoritative for the final diff.
        add(workspaceCombineFileEdits(previous, diff, file));
      }
    }
  }
  return [...files.values()];
}

function workspaceCombineFileEdits(previous, diff, file) {
  const edits = [...(previous.toolEdits || [previous]), ...(diff.toolEdits || [diff])];
  const counts = edits.map(workspaceDiffLineCounts).filter(Boolean);
  const patch = edits.map(edit => edit.patch || "").filter(Boolean).join("\n");
  return { ...diff, file, toolEdits: edits, cumulative: true, patch,
    derived: !patch, input: diff.input || previous.input, countsUnknown: !counts.length,
    countsPartial: counts.length < edits.length || edits.some(edit => edit.countsPartial),
    writtenLines: edits.some(edit => Number.isFinite(edit.writtenLines))
      ? edits.reduce((sum, edit) => sum + (edit.writtenLines || 0), 0) : undefined,
    additions: counts.reduce((sum, count) => sum + count.additions, 0),
    deletions: counts.reduce((sum, count) => sum + count.deletions, 0) };
}

function workspaceDiffLineCounts(diff) {
  if (diff.countsUnknown) return null;
  const additions = Number(diff.additions);
  const deletions = Number(diff.deletions);
  if (diff.additions != null && diff.deletions != null && Number.isFinite(additions) && Number.isFinite(deletions))
    return { additions, deletions };
  if (typeof diff.patch === "string" && diff.patch) {
    const lines = diff.patch.split("\n");
    return { additions: lines.filter(line => line.startsWith("+") && !line.startsWith("+++ ")).length,
      deletions: lines.filter(line => line.startsWith("-") && !line.startsWith("--- ")).length };
  }
  const input = diff.input || diff;
  const count = value => value ? value.replace(/\n$/, "").split("\n").length : 0;
  if (typeof input.oldString === "string" && typeof input.newString === "string")
    return { additions: count(input.newString), deletions: count(input.oldString) };
  if (typeof diff.before === "string" && typeof diff.after === "string") {
    // Without a native patch, only an entirely new/deleted file has exact totals.
    if (!diff.before) return { additions: count(diff.after), deletions: 0 };
    if (!diff.after) return { additions: 0, deletions: count(diff.before) };
  }
  return null;
}

// Keep unchanged nodes attached so polling preserves animations, focus and selection.
function workspaceSyncChildren(container, children) {
  const retained = new Set(children);
  for (const child of Array.from(container.childNodes)) {
    if (!retained.has(child)) child.remove();
  }
  children.forEach((child, index) => {
    const current = container.childNodes[index];
    if (current !== child) container.insertBefore(child, current || null);
  });
  while (container.childNodes.length > children.length) container.lastChild.remove();
}

function renderWorkspace(view) {
  // The router keeps this page mounted across settings visits.
  const cleanups = [];
  const addCleanup = fn => cleanups.push(fn);
  let disposed = false;
  let suspended = false;
  let projectsLoading = null;
  let checkLoading = null;
  let lastSelectedRefresh = 0;
  const sessionLoads = new Map();
  let events = null;
  let eventProjectId = null;
  let refreshTimer = null;
  let selectedRefresh = null;
  let renderTimer = null;
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
  let changePopover = null;
  let draftTimer = null;
  let draftContextReady = false;
  let statusRefreshing = false;
  let sidebarDrag = null;
  let changesSignature = "";
  let changesView = null;
  const sendingConversations = new Set();
  const pendingActions = new Map();
  const pendingImages = new Map();
  const statusVersions = new Map();
  const completionWatches = new Map();
  addCleanup(() => {
    for (const watch of completionWatches.values()) if (watch.timer) clearTimeout(watch.timer);
    completionWatches.clear();
  });
  let messageVersion = 0;
  const messageInfoVersions = new Map();
  const conversationViews = new Map();
  const dismissedTodoPanels = new Set();
  const todoPanelTurns = new Map();
  let renderedTodoSignature = "";
  const sidebarSections = new Map();
  const sidebarRows = new Map();
  const changeTriggers = new Map();
  const state = {
    projects: [], sessions: new Map(), sessionDetails: new Map(), errors: new Map(), projectStatuses: new Map(),
    projectId: workspaceSelection.projectId, sessionId: workspaceSelection.sessionId,
    messages: [], messagesLoaded: false, messageLoadError: "", permissions: [], questions: [], questionDrafts: new Map(), questionPages: new Map(),
    questionErrors: new Map(), diffs: [], selectedChange: null, todos: [], todoVersion: 0, children: [], statuses: {},
    recordingData: null, recordingError: "", check: null, tab: workspaceSelection.tab || "chat", search: "", chosenModels: new Map(), defaultModel: null,
    get sending() { return sendingConversations.has(workspaceConversationKey(this.projectId, this.sessionId)); },
    get pendingAction() { return pendingActions.get(workspaceConversationKey(this.projectId, this.sessionId)) || ""; },
    queue: { items: [], paused: false, error: "" }, queueLoaded: false,
    chosenAgents: new Map(), chosenVariants: new Map(), providers: [], connectedProviders: new Set(), agents: [], commands: [], skills: [], modelLoadError: "", modelSource: "sona", sonaEnvironment: "prod", sonaConnected: null,
    collapsedProjects: new Set(), sessionLimits: new Map(), expandedTools: new Map(), actionError: "", compactingSessionId: null,
    attachments: [], fileReferences: [], commandSelectedIndex: 0,
    get pendingImageCount() { return pendingImages.get(workspaceConversationKey(this.projectId, this.sessionId))?.count || 0; },
    get pendingImageBytes() { return pendingImages.get(workspaceConversationKey(this.projectId, this.sessionId))?.bytes || 0; },
  };

  view.innerHTML = `
    <section class="wsp" id="wsp">
      <aside class="wsp-side" aria-label="项目与对话">
        <div class="wsp-brand"><img class="wsp-brand-mark" src="sona-code-icon.png" alt="" width="34" height="34"><span class="wsp-brand-copy"><strong>Sona Code</strong><small>桌面工作区</small></span><button class="wsp-side-close" id="wsp-side-close" type="button" aria-label="关闭项目栏">${workspaceIcon("close").outerHTML}</button></div>
        <div class="wsp-side-top">
          <div class="wsp-side-actions"><button class="wsp-new" id="wsp-new" type="button">${workspaceIcon("plus").outerHTML}<span>新建项目</span></button></div>
          <label class="wsp-search-wrap">${workspaceIcon("search").outerHTML}<input class="wsp-search" id="wsp-search" type="search" placeholder="搜索项目和对话" aria-label="搜索项目和对话"><kbd>⌘K</kbd></label>
        </div>
        <div class="wsp-side-list"><div class="wsp-side-label"><span>项目与对话</span><span class="wsp-side-label-actions"><span id="wsp-project-count"></span></span></div><div id="wsp-projects"></div></div>
        <div class="wsp-side-bottom"><a class="wsp-settings" href="#/preferences" title="打开设置">${workspaceIcon("settings").outerHTML}<span>设置</span></a></div>
      </aside>
      <aside class="wsp-tree" id="wsp-tree" aria-label="项目目录树" hidden><div class="wsp-tree-head"><strong id="wsp-tree-title">目录树</strong><button id="wsp-tree-refresh" type="button" title="刷新目录树" aria-label="刷新目录树">${workspaceIcon("refresh").outerHTML}</button><button id="wsp-tree-close" type="button" title="关闭目录树" aria-label="关闭目录树">${workspaceIcon("close").outerHTML}</button></div><div class="wsp-tree-body" id="wsp-tree-body" role="tree"></div></aside>
      <div class="wsp-side-scrim" id="wsp-side-scrim"></div>
      <div class="wsp-main">
        <button class="wsp-todo-trigger" id="wsp-todo-trigger" type="button" aria-label="打开任务进度" aria-controls="wsp-todo-panel" aria-expanded="false" title="打开任务进度" hidden>${workspaceIcon("todo").outerHTML}<span>任务进度</span><small id="wsp-todo-trigger-count"></small></button>
        <header class="wsp-head"><button class="wsp-menu" id="wsp-menu" type="button" aria-label="打开项目栏">${workspaceIcon("panelLeft").outerHTML}</button><div class="wsp-head-text"><div class="wsp-breadcrumb" id="wsp-breadcrumb">工作区</div><div class="wsp-title" id="wsp-title">选择项目</div></div><button class="wsp-abort" id="wsp-abort" type="button" title="停止任务" aria-label="停止任务" hidden>${workspaceIcon("stop").outerHTML}</button><span class="wsp-status" id="wsp-status" role="status" aria-label="准备中" title="准备中"></span></header>
        <nav class="wsp-tabs" aria-label="对话视图"><button class="wsp-tab active" type="button" data-wsp-tab="chat">对话</button><button class="wsp-tab" type="button" data-wsp-tab="changes">文件改动<span class="wsp-tab-count" id="wsp-change-count" aria-label="修改文件数量">0</span></button><button class="wsp-tab" type="button" data-wsp-tab="trajectory">轨迹</button><button class="wsp-tab" type="button" data-wsp-tab="activity">活动</button><button class="wsp-tab" type="button" data-wsp-tab="tasks">任务</button></nav>
        <aside class="wsp-todo-panel" id="wsp-todo-panel" aria-label="当前对话任务进度" hidden><div class="wsp-todo-panel-head"><span class="wsp-todo-panel-icon" aria-hidden="true">${workspaceIcon("todo").outerHTML}</span><div><strong>任务进度</strong><small>当前对话 · OpenCode</small></div><button class="wsp-todo-panel-close" id="wsp-todo-panel-close" type="button" aria-label="关闭任务进度" title="关闭任务进度">${workspaceIcon("close").outerHTML}</button></div><div class="wsp-todo-panel-summary"><span id="wsp-todo-summary"></span><strong id="wsp-todo-progress"></strong></div><div class="wsp-todo-progress-track"><span id="wsp-todo-progress-fill"></span></div><ol class="wsp-todo-panel-list" id="wsp-todo-panel-list"></ol><button class="wsp-todo-panel-link" id="wsp-todo-panel-link" type="button">查看任务页 ${workspaceIcon("open").outerHTML}</button></aside>
        <div class="wsp-scroll" id="wsp-scroll"><div class="wsp-content" id="wsp-content"></div></div>
        <div class="wsp-composer-dock"><form class="wsp-composer" id="wsp-form"><div class="wsp-command-menu" id="wsp-command-menu" role="listbox" aria-label="命令与项目文件" hidden></div><div class="wsp-model-picker" id="wsp-model-picker" role="dialog" aria-label="选择模型" hidden><div class="wsp-picker-head"><strong>选择模型</strong><button type="button" id="wsp-model-refresh" title="刷新 Sona 订阅模型" aria-label="刷新 Sona 订阅模型" hidden>${workspaceIcon("refresh").outerHTML}</button><button type="button" id="wsp-model-close" aria-label="关闭模型选择">${workspaceIcon("close").outerHTML}</button></div><input id="wsp-model-search" type="search" placeholder="搜索 Provider 或模型" aria-label="搜索 Provider 或模型"><div class="wsp-model-list" id="wsp-model-list"></div></div><div class="wsp-attachment-list" id="wsp-attachment-list" aria-label="待发送附件" hidden></div><div class="wsp-input" id="wsp-input" contenteditable="true" role="textbox" aria-multiline="true" data-placeholder="向 Sona Code 描述你的需求…" aria-label="输入消息" aria-describedby="wsp-skill-error"></div><div id="wsp-skill-error" class="wsp-skill-error" role="status" aria-live="polite" hidden></div><div class="wsp-composer-bottom"><button class="wsp-attach" id="wsp-attach" type="button" title="选择 Sona Code 命令，也可输入 /" aria-label="选择 Sona Code 命令" aria-haspopup="listbox" aria-expanded="false">${workspaceIcon("plus").outerHTML}</button><select class="wsp-agent" id="wsp-agent" aria-label="选择 Agent" hidden><option value="build">Build · 执行</option></select><button class="wsp-agent-trigger" id="wsp-agent-trigger" type="button" aria-haspopup="menu" aria-expanded="false"><span id="wsp-agent-label">Build · 执行</span>${workspaceIcon("chevronDown").outerHTML}</button><div class="wsp-agent-picker" id="wsp-agent-picker" role="menu" aria-label="选择 Agent" hidden></div><span class="wsp-composer-hint">Enter 发送 · Shift+Enter 换行</span><span class="wsp-composer-spacer"></span><button class="wsp-model-trigger" id="wsp-model-trigger" type="button" aria-haspopup="dialog" aria-expanded="false">自动</button><select class="wsp-variant" id="wsp-variant" aria-label="选择模型强度" title="模型推理强度" hidden></select><button class="wsp-send" id="wsp-send" type="submit" title="发送消息" aria-label="发送消息">${workspaceIcon("send").outerHTML}</button></div></form><div class="wsp-stats" id="wsp-stats" aria-live="polite"></div></div>
      </div>
    </section>`;

  const root = view.querySelector("#wsp");
  try { if (localStorage.getItem("sona-code:sidebar-collapsed") === "1") root.classList.add("side-collapsed"); }
  catch (_) { /* Storage may be unavailable. */ }
  const sideList = view.querySelector("#wsp-projects");
  const content = view.querySelector("#wsp-content");
  const scroll = view.querySelector("#wsp-scroll");
  const todoTrigger = view.querySelector("#wsp-todo-trigger");
  const todoPanel = view.querySelector("#wsp-todo-panel");
  const todoList = view.querySelector("#wsp-todo-panel-list");
  view.querySelector(".wsp-head-text").after(todoTrigger);
  updateAdminMenus();
  const input = view.querySelector("#wsp-input");
  const composerDock = view.querySelector(".wsp-composer-dock");
  composerDock.hidden = state.tab !== "chat";
  const composer = createWorkspaceComposer(input, skillMention, fileMention);
  const browserPreviewFile = (path) => /\.(html?|svg|pdf|txt|xml|css|m?js|json|md|png|jpe?g|gif|webp)$/i.test(path);
  async function openTreePath(path, mode) {
    if (!state.projectId) return;
    try {
      await api("workspace/projects/" + encodeURIComponent(state.projectId) + "/entries/open?path=" +
        encodeURIComponent(path) + "&mode=" + mode, { method: "POST", body: {}, silent: true });
    } catch (error) { toast("打开路径失败：" + detail(error), "error"); }
  }
  const tree = createWorkspaceTree(view.querySelector("#wsp-tree"), {
    list: (projectId, path) => api("workspace/projects/" + encodeURIComponent(projectId) +
      "/tree?path=" + encodeURIComponent(path), { silent: true }),
    open: async (projectId, path, mode) => {
      try {
        await api("workspace/projects/" + encodeURIComponent(projectId) + "/entries/open?path=" +
          encodeURIComponent(path) + "&mode=" + mode, { method: "POST", body: {}, silent: true });
      } catch (error) { toast("打开路径失败：" + detail(error), "error"); }
    },
    reference: (path) => addTreeReference(path),
    error: (message) => toast(message, "error"),
  });
  addCleanup(() => tree.dispose());
  function openProjectTree(project) {
    if (state.projectId !== project.id) selectProject(project.id);
    tree.open(project);
  }
  function addTreeReference(path) {
    const refs = composer.fileReferences;
    if (state.attachments.length + refs.length >= 8 && !refs.some(item => item.path === path)) {
      toast("一条消息最多添加 8 个附件或文件引用", "error"); return;
    }
    let end = composer.value.length;
    if (end && !/\s$/.test(composer.value)) {
      input.focus();
      composer.setSelectionRange(end);
      document.execCommand("insertText", false, " ");
      end++;
    }
    composer.insertFileReference(path, end, end);
    updateSkillInput(); scheduleDraftSave(); input.focus();
  }
  const treeDropForm = view.querySelector("#wsp-form");
  treeDropForm.addEventListener("dragover", (event) => {
    if (event.dataTransfer.types.includes("application/x-sona-project-reference")) {
      event.preventDefault(); event.dataTransfer.dropEffect = "copy";
    }
  });
  treeDropForm.addEventListener("drop", (event) => {
    const raw = event.dataTransfer.getData("application/x-sona-project-reference");
    if (!raw) return;
    event.preventDefault();
    try {
      const item = JSON.parse(raw);
      if (item.projectId !== state.projectId) { toast("只能引用当前项目的路径", "error"); return; }
      addTreeReference(item.path);
    } catch (_) { /* Ignore invalid drag data. */ }
  });
  function saveDraft() {
    if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; }
    if (!state.projectId || !draftContextReady) return;
    workspaceWriteDraft(workspaceConversationKey(state.projectId, state.sessionId), {
      ...composer.snapshot(), attachments: state.attachments.map(item => ({ ...item })),
    });
  }
  function scheduleDraftSave() {
    if (draftTimer) clearTimeout(draftTimer);
    draftTimer = setTimeout(saveDraft, 180);
  }
  function restoreDraft() {
    draftContextReady = true;
    const draft = workspaceReadDraft(workspaceConversationKey(state.projectId, state.sessionId));
    composer.restore(draft);
    state.attachments = (draft?.attachments || []).map(item => ({ ...item }));
    state.fileReferences = composer.fileReferences;
    state.actionError = "";
    updateSkillInput(); renderAttachments();
  }
  const attachmentList = view.querySelector("#wsp-attachment-list");
  const statsLine = view.querySelector("#wsp-stats");
  const modelButton = view.querySelector("#wsp-model-trigger");
  const modelPicker = view.querySelector("#wsp-model-picker");
  const modelSearch = view.querySelector("#wsp-model-search");
  const modelList = view.querySelector("#wsp-model-list");
  let modelLoadVersion = 0;
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
  const alive = () => !disposed && !suspended && view.isConnected && (!location.hash || location.hash === "#/workspace");
  const queuePanel = el("section", { class: "wsp-queue", "aria-label": "待发送消息", hidden: true });
  view.querySelector("#wsp-form").before(queuePanel);
  let queueSignature = "";
  let queueUpdateVersion = 0;
  let queueDialog = null;
  addCleanup(() => queueDialog?.close());

  function waitingForReply() {
    const status = state.statuses?.[state.sessionId]?.type;
    return compactionRunning() || status === "busy" || status === "retry" ||
      state.queue.items.some((item) => item.status === "sending");
  }

  function applyQueue(queue, projectId, sessionId) {
    if (!alive() || state.projectId !== projectId || state.sessionId !== sessionId) return;
    state.queue = queue;
    queueUpdateVersion++;
    state.queueLoaded = true;
    renderHeader();
  }

  async function queueAction(suffix, method, body) {
    const projectId = state.projectId, sessionId = state.sessionId;
    const queue = await api(`${sessionPath(projectId, sessionId)}/queue${suffix}`, { method, body, silent: true });
    applyQueue(queue, projectId, sessionId);
  }

  function renderQueue() {
    const items = state.queue.items.filter((item) => item.status === "pending" || state.queue.paused);
    const pendingCount = items.filter((item) => item.status === "pending").length;
    const visible = !!items.length || !!state.queue.error;
    queuePanel.hidden = !visible;
    const signature = JSON.stringify([state.projectId, state.sessionId, state.queue, waitingForReply()]);
    if (signature === queueSignature) return;
    queueSignature = signature;
    queuePanel.replaceChildren();
    if (!visible) return;
    const heading = el("div", { class: "wsp-queue-heading" },
      el("span", { class: "wsp-queue-title", role: "status", text: `待发送 · ${pendingCount}` }),
      el("span", { class: "wsp-queue-note", text: state.queue.paused ? "已暂停" : waitingForReply() ? "本轮结束后依次发送" : "即将发送" }),
      el("button", { type: "button", class: "wsp-queue-toggle", text: state.queue.paused ? "继续发送" : "暂停",
        onclick: async () => {
          try { await queueAction("", "PATCH", { paused: !state.queue.paused }); }
          catch (error) { toast("操作失败：" + detail(error), "error"); }
        } }));
    queuePanel.append(heading);
    if (state.queue.error) queuePanel.append(el("p", { class: "wsp-queue-error", role: "status", text: state.queue.error }));
    const list = el("ol", { class: "wsp-queue-list" });
    for (const [index, item] of items.entries()) {
      const payload = item.payload;
      const text = item.kind === "command" ? `/${payload.command} ${payload.arguments || ""}` : payload.text || "";
      const files = [...(payload.references || []).map((ref) => ref.path), ...(payload.files || []).map((file) => file.filename)];
      const description = text || (files.length ? files.join("、") : "附件消息");
      const sent = item.status === "sending";
      const metadata = [sent ? "已提交 · 继续后核对状态" : "", payload.agent, payload.model_id,
        files.length ? `${files.length} 个文件` : ""].filter(Boolean).join(" · ");
      list.append(el("li", { class: "wsp-queue-row" },
        el("span", { class: "wsp-queue-number", text: String(index + 1) }),
        el("div", { class: "wsp-queue-message" }, el("span", { class: "wsp-queue-preview", text: description, title: description }),
          el("small", { text: metadata })),
        !sent && el("button", { type: "button", class: "wsp-queue-action", text: "编辑", "aria-label": `编辑第 ${index + 1} 条待发送消息`, onclick: () => editQueuedMessage(item) }),
        el("button", { type: "button", class: "wsp-queue-action wsp-queue-remove", title: sent ? "移出队列，不撤回已发送消息" : "移除", "aria-label": `移除第 ${index + 1} 条待发送消息`,
          onclick: async () => {
            try { await queueAction(`/${encodeURIComponent(item.id)}`, "DELETE"); }
            catch (error) { toast("移除失败：" + detail(error), "error"); }
          } }, workspaceIcon("close"))));
    }
    queuePanel.append(list);
  }

  function editQueuedMessage(item) {
    if (queueDialog) return;
    const projectId = state.projectId, sessionId = state.sessionId;
    const command = item.kind === "command";
    const field = el("textarea", { class: "wsp-queue-editor", "aria-label": command ? "技能任务" : "消息内容",
      value: command ? item.payload.arguments || "" : item.payload.text || "", rows: "5" });
    const errorLine = el("p", { class: "wsp-queue-error", role: "alert" });
    const save = el("button", { type: "button", class: "wsp-mini primary", text: "保存", onclick: async () => {
      save.disabled = true;
      try {
        const payload = { ...item.payload, [command ? "arguments" : "text"]: field.value };
        const queue = await api(`${sessionPath(projectId, sessionId)}/queue/${encodeURIComponent(item.id)}`, {
          method: "PATCH", body: { kind: item.kind, payload }, silent: true,
        });
        applyQueue(queue, projectId, sessionId);
        dialog.close();
      } catch (error) { errorLine.textContent = detail(error); }
      finally { save.disabled = false; }
    } });
    const dialog = el("dialog", { class: "wsp-modal wsp-queue-dialog", "aria-label": "编辑待发送消息" },
      el("h2", { text: "编辑待发送消息" }),
      el("p", { text: command ? `/${item.payload.command} · 修改任务内容，保留队列位置` : "保存后保留队列位置和已选文件" }),
      field, errorLine, el("div", { class: "wsp-modal-actions" },
        el("button", { type: "button", class: "wsp-mini", text: "取消", onclick: () => dialog.close() }), save));
    dialog.addEventListener("close", () => { queueDialog = null; dialog.remove(); });
    queueDialog = dialog;
    document.body.append(dialog);
    dialog.showModal(); field.focus();
  }

  addCleanup(() => {
    saveDraft();
    disposed = true;
    if (events) events.close();
    cancelSelectedRefresh();
    conversationViews.clear();
    if (fileSearchTimer) clearTimeout(fileSearchTimer);
    clearInterval(poll);
  });

  scroll.addEventListener("scroll", () => {
    followLatest = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop <= 80;
  }, { passive: true });
  const updateTrajectoryHeight = () => scroll.style.setProperty("--wsp-scroll-height", `${scroll.clientHeight}px`);
  updateTrajectoryHeight();
  if (typeof ResizeObserver !== "undefined") {
    const resizeObserver = new ResizeObserver(() => {
      updateTrajectoryHeight();
      if (alive() && state.tab === "chat" && followLatest) scroll.scrollTop = scroll.scrollHeight;
    });
    resizeObserver.observe(content);
    resizeObserver.observe(scroll);
    addCleanup(() => resizeObserver.disconnect());
  }

  function activeProject() { return state.projects.find((item) => item.id === state.projectId); }
  function activeSession() {
    return state.sessionDetails.get(workspaceConversationKey(state.projectId, state.sessionId)) ||
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
    const name = kind === "ready" ? "check" : ["busy", "attention", "error"].includes(kind) ? kind : "info";
    const icon = workspaceIcon(name, "wsp-status-icon" + (kind === "busy" ? " wsp-status-spinning" : ""));
    if (kind === "busy") icon.setAttribute("style", `animation-delay: -${(Date.now() % 1100) / 1000}s`);
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
      (message.parts || []).filter((part) => part.type === "text" && !part.synthetic && !workspaceDirectoryReference(part))
        .map((part) => isInitCommandPrompt(part.text) ? "/init" : part.text || "").join("\n");
    composer.value = text;
    const references = (message.parts || []).flatMap((part) => workspaceDirectoryReference(part) ?
      [workspaceDirectoryReference(part)] : part.type === "file" && part.url?.startsWith("file:") && part.filename ?
        [part.filename] : []);
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
    const text = (message.parts || []).filter((part) => part.type === "text" && !part.synthetic && !workspaceDirectoryReference(part))
      .map((part) => part.text || "").join("\n\n");
    function actionButton(label, iconName, handler) {
      return el("button", { class: "wsp-message-action", type: "button", title: label, "aria-label": label, onclick: handler },
        workspaceIcon(iconName));
    }
    const copy = actionButton("复制消息", "copy", () => copyMessageText(text));
    copy.disabled = !text;
    actions.append(copy);
    if (info.role === "assistant") {
      const fork = actionButton("在新对话中分支", "fork", async () => {
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
      const withdraw = actionButton("撤回最后一轮", "undo", async () => {
        if (withdraw.disabled || state.sending) return;
        if (!confirm("撤回最后一轮用户消息及模型回复，并将提问回填到输入框？项目文件修改会保留，当前输入框内容会被替换。")) return;
        withdraw.disabled = true;
        const operationKey = workspaceConversationKey(projectId, sessionId);
        sendingConversations.add(operationKey);
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
        finally { sendingConversations.delete(operationKey); if (alive()) { renderHeader(); renderMain(); } }
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
  function formatTokens(value, base = 1000) {
    if (value < base) return String(Math.round(value));
    const scaled = value < base * base ? value / base : value / (base * base);
    const suffix = value < base * base ? "K" : "M";
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
      if (promptTokens + numeric(usage.output) + numeric(usage.reasoning) > 0) {
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
        const percent = Math.round(used / contextWindow * 100);
        const base = model.source === "sona" ? 1024 : 1000;
        contextGroup = el("span", { class: "wsp-stat-context", title: `OpenCode 最近一次有效模型用量（含输出、推理和缓存 Token）：${used} / ${contextWindow} tokens；模型调用结束时更新`,
          text: `上下文 ${formatTokens(used, base)} / ${formatTokens(contextWindow, base)} · ${percent}%` });
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
          onclick: () => {
            state.attachments = state.attachments.filter((item) => item.id !== attachment.id);
            renderAttachments(); saveDraft();
          } }, workspaceIcon("close")));
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
    const key = workspaceConversationKey(projectId, sessionId);
    const pending = pendingImages.get(key) || { count: 0, bytes: 0 };
    pendingImages.set(key, pending);
    const acceptedTypes = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);
    for (const file of files) {
      if (!file) continue;
      if (!acceptedTypes.has(file.type)) { toast("只支持 PNG、JPEG、GIF 或 WebP 图片", "error"); continue; }
      if (file.size > 8 * 1024 * 1024) { toast(`${file.name || "图片"} 超过 8 MB`, "error"); continue; }
      const totalBytes = state.attachments.reduce((sum, item) => sum + item.size, 0) + state.pendingImageBytes;
      if (state.attachments.length + state.fileReferences.length + state.pendingImageCount >= 8) { toast("一条消息最多添加 8 个附件或文件引用", "error"); break; }
      if (totalBytes + file.size > 20 * 1024 * 1024) { toast("图片总大小不能超过 20 MB", "error"); continue; }
      pending.count += 1;
      pending.bytes += file.size;
      renderHeader();
      try {
        const url = await readImage(file);
        if (!alive()) return;
        const attachment = { id: `${Date.now()}-${Math.random()}`, filename: (file.name || `粘贴图片.${file.type.split("/")[1] || "png"}`).replace(/[\\/]/g, "_"),
          mime: file.type, url, size: file.size };
        if (projectId !== state.projectId || sessionId !== state.sessionId) {
          const draft = workspaceReadDraft(key) || { text: "", references: [], attachments: [] };
          workspaceWriteDraft(key, { ...draft, attachments: [...(draft.attachments || []), attachment] });
          return;
        }
        state.attachments.push(attachment);
        renderAttachments(); saveDraft();
      } catch (error) { toast(detail(error), "error"); }
      finally {
        pending.count = Math.max(0, pending.count - 1);
        pending.bytes = Math.max(0, pending.bytes - file.size);
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
    const icons = { "重命名": "pencil", "打开目录": "folder", "打开目录树": "tree",
      "删除工作区": "trash", "分叉会话": "fork", "删除": "trash" };
    for (const [label, action] of actions) {
      menu.append(el("button", { type: "button", role: "menuitem",
        class: label.startsWith("删除") ? "danger" : "",
        onclick: () => { closeRowMenus(); action(); } },
        el("span", { class: "wsp-action-icon" }, workspaceIcon(icons[label])),
        el("span", { text: label })));
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
      } }, workspaceIcon("more"));
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
              state.sessionDetails.set(workspaceConversationKey(projectId, session.id), updated);
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
        workspaceWriteDraft(workspaceConversationKey(projectId, session.id), null);
        conversationViews.delete(workspaceConversationKey(projectId, session.id));
        state.sessionDetails.delete(workspaceConversationKey(projectId, session.id));
        if (state.projectId === projectId && state.sessionId === session.id) {
          cancelSelectedRefresh();
          state.sessionId = null;
          restoreDraft();
          state.queue = { items: [], paused: false, error: "" }; state.queueLoaded = false;
          workspaceSelection.sessionId = null;
          persistWorkspaceSelection();
          state.messages = [];
          state.selectedChange = null;
          closeChangePopover();
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
        if (result === false) throw new Error("Sona Code 未能压缩上下文");
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

  function bindSidebarDrag(node, kind, projectId, id) {
    node.draggable = true;
    node.addEventListener("dragstart", event => {
      if (event.target.closest(".wsp-row-menu")) { event.preventDefault(); return; }
      sidebarDrag = { kind, projectId, id };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", id);
    });
    const accepts = () => sidebarDrag?.kind === kind && sidebarDrag.id !== id &&
      (kind === "project" || sidebarDrag.projectId === projectId);
    node.addEventListener("dragenter", event => {
      if (!accepts()) return;
      event.preventDefault(); event.stopPropagation();
      event.dataTransfer.dropEffect = "move";
    });
    node.addEventListener("dragover", event => {
      if (!accepts()) return;
      event.preventDefault(); event.stopPropagation();
      event.dataTransfer.dropEffect = "move";
      node.classList.toggle("wsp-drop-after", event.clientY > node.getBoundingClientRect().top + node.getBoundingClientRect().height / 2);
      node.classList.add("wsp-drop-target");
    });
    const clear = () => node.classList.remove("wsp-drop-target", "wsp-drop-after");
    node.addEventListener("dragleave", clear);
    node.addEventListener("dragend", () => {
      sidebarDrag = null;
      sideList.querySelectorAll(".wsp-drop-target").forEach(item => item.classList.remove("wsp-drop-target", "wsp-drop-after"));
    });
    node.addEventListener("drop", event => {
      if (!accepts()) return;
      event.preventDefault(); event.stopPropagation();
      const after = event.clientY > node.getBoundingClientRect().top + node.getBoundingClientRect().height / 2;
      const items = kind === "project" ? state.projects : state.sessions.get(projectId) || [];
      const ordered = workspaceMoveItem(items, sidebarDrag.id, id, after);
      if (kind === "project") state.projects = ordered;
      else state.sessions.set(projectId, ordered);
      try { localStorage.setItem(kind === "project" ? "sona-code:project-order" : `sona-code:session-order:${projectId}`, JSON.stringify(ordered.map(item => item.id))); } catch (_) {}
      sidebarDrag = null; clear(); renderSidebar();
    });
  }

  function renderSidebar() {
    if (sidebarDrag) return;
    closeRowMenus();
    const sections = [];
    const sectionKeys = new Set();
    const rowKeys = new Set();
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
      sectionKeys.add(project.id);
      let cached = sidebarSections.get(project.id);
      if (!cached) {
        cached = {section: el("section"), threads: el("div", {class: "wsp-threads"}), indicator: null};
        sidebarSections.set(project.id, cached);
      }
      const {section, threads} = cached;
      section.className = "wsp-project" + (collapsed ? " collapsed" : "");
      const heading = el("button", { class: "wsp-project-head", type: "button", title: project.path,
        "aria-expanded": String(!collapsed), onclick: () => {
          state.sessionLimits.delete(project.id);
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
          ["打开目录树", () => openProjectTree(project)],
          ["删除工作区", () => removeProject(project)]]),
        el("button", { class: "wsp-row-action wsp-row-plus", type: "button",
          title: `在 ${project.name} 中新建对话`, "aria-label": `在 ${project.name} 中新建对话`,
          onclick: () => { state.collapsedProjects.delete(project.id); renderSidebar(); createSession(project.id); } }, workspaceIcon("plus")));
      bindSidebarDrag(projectRow, "project", project.id, project.id);
      const projectRunning = collapsed && Object.values(state.projectStatuses.get(project.id) || {}).some(status => ["busy", "retry"].includes(status?.type));
      if (projectRunning && !cached.indicator) cached.indicator = el("span", {
        class: "wsp-thread-running", role: "img", "aria-label": "项目内有任务正在运行", title: "项目内有任务正在运行",
      }, statusIcon("busy"));
      // Keep the project spinner attached across status polls.
      if (cached.indicator) cached.indicator.hidden = !projectRunning;
      const threadNodes = [];
      if (state.errors.has(project.id)) {
        threadNodes.push(el("div", { class: "wsp-error", text: state.errors.get(project.id) }));
      } else if (!all) {
        threadNodes.push(el("div", { class: "wsp-thread-time", text: "点击项目读取对话" }));
      } else if (all && !sessions.length) {
        threadNodes.push(el("div", { class: "wsp-thread-time", text: query ? "无匹配对话" : "暂无对话" }));
      }
      const limit = state.sessionLimits.get(project.id) || 6;
      for (const session of sessions.slice(0, limit)) {
        const status = state.projectStatuses.get(project.id)?.[session.id];
        const running = ["busy", "retry"].includes(status?.type);
        const key = workspaceConversationKey(project.id, session.id);
        rowKeys.add(key);
        let row = sidebarRows.get(key);
        if (!row) {
          const title = el("span", {class: "wsp-thread-title"});
          const preview = el("span", {class: "wsp-thread-preview"});
          const text = el("span", {class: "wsp-thread-text"},
            el("span", {class: "wsp-thread-line"}, title), preview);
          const button = el("button", {type: "button", onclick: () => selectSession(project.id, session.id)}, text);
          row = {node: el("div", {class: "wsp-thread-row"}, button), button, title, preview, indicator: null};
          bindSidebarDrag(row.button, "session", project.id, session.id);
          sidebarRows.set(key, row);
        }
        row.button.className = "wsp-thread" + (session.parentID ? " child" : "") +
          (project.id === state.projectId && session.id === state.sessionId ? " active" : "");
        row.button.title = sessionTitle(session);
        row.title.textContent = `${session.parentID ? "↳ " : ""}${sessionTitle(session)}`;
        row.preview.textContent = stamp(session);
        if (running && !row.indicator) {
          row.indicator = el("span", {class: "wsp-thread-running", role: "img"}, statusIcon("busy"));
          row.button.append(row.indicator);
        } else if (!running && row.indicator) {
          row.indicator.remove(); row.indicator = null;
        }
        if (row.indicator) {
          const label = status.type === "retry" ? "正在重试" : "正在运行";
          row.indicator.title = label; row.indicator.setAttribute("aria-label", label);
        }
        // Menus capture current session metadata; the button and spinner stay attached.
        const menu = rowMenu([["重命名", () => performSessionAction(project.id, session, "rename")],
          ["分叉会话", () => performSessionAction(project.id, session, "fork")],
          ["删除", () => performSessionAction(project.id, session, "delete")]]);
        workspaceSyncChildren(row.node, [row.button, menu]);
        threadNodes.push(row.node);
      }
      if (sessions.length > limit) {
        threadNodes.push(el("button", {
          class: "wsp-show-more", type: "button", text: "展示更多",
          onclick: () => {
            state.sessionLimits.set(project.id, limit + 6);
            renderSidebar();
          },
        }));
      }
      workspaceSyncChildren(threads, threadNodes);
      workspaceSyncChildren(section, [projectRow, ...(cached.indicator ? [cached.indicator] : []), threads]);
      sections.push(section);
    }
    if (query && !visible) sections.push(el("p", { class: "wsp-empty-search", text: "没有找到匹配的项目或对话。" }));
    else if (!state.projects.length) sections.push(el("div", { class: "wsp-empty", style: "min-height:200px" },
      el("p", { text: "还没有项目。点击上方按钮选择已有目录。" })));
    workspaceSyncChildren(sideList, sections);
    for (const key of sidebarSections.keys()) if (!sectionKeys.has(key)) sidebarSections.delete(key);
    for (const key of sidebarRows.keys()) if (!rowKeys.has(key)) sidebarRows.delete(key);
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
    const busy = state.sending || waitingForReply();
    const kind = failed ? "error" : busy ? "busy" : pending ? "attention" : state.check?.found ? "ready" : "missing";
    const description = failed ? "请求失败" : compacting ? "正在压缩上下文" : busy ? "正在处理" :
      pending ? "等待确认" : state.check?.found ? "已就绪" : "未检测到 Sona Code";
    label.className = `wsp-status ${kind}`;
    label.setAttribute("aria-label", description);
    label.title = description;
    if (label.dataset.kind !== kind) {
      label.replaceChildren(statusIcon(kind));
      label.dataset.kind = kind;
    }
    view.querySelector("#wsp-abort").hidden = !session || !busy;
    view.querySelectorAll(".wsp-tab").forEach((button) => button.classList.toggle("active", button.dataset.wspTab === state.tab));
    const send = view.querySelector("#wsp-send");
    send.disabled = state.sending || !project || !state.check?.found || (!!state.sessionId && !state.queueLoaded);
    const queued = waitingForReply() || state.queue.items.length > 0 || state.queue.paused;
    const sendLabel = queued ? "加入队列" : "发送消息";
    send.title = sendLabel;
    send.setAttribute("aria-label", sendLabel);
    send.classList.toggle("queuing", queued);
    if (send.dataset.mode !== String(queued)) {
      send.replaceChildren(workspaceIcon(queued ? "queue" : "send"));
      send.dataset.mode = String(queued);
    }
    view.querySelector(".wsp-composer-hint").textContent = `Enter ${queued ? "加入队列" : "发送"} · Shift+Enter 换行`;
    input.dataset.placeholder = state.queue.paused ? "队列已暂停，继续输入可加入队列…" :
      queued ? "继续输入，本轮结束后发送…" : "向 Sona Code 描述你的需求…";
    renderQueue();
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
    const raw = data.responseBody || data.message || error.message || error.name || "Sona Code 请求失败";
    return el("div", { class: "wsp-message-error" },
      el("p", { text: typeof raw === "string" ? raw : JSON.stringify(raw, null, 2) }));
  }

  function skillIcon() {
    return workspaceIcon("skill", "wsp-skill-icon");
  }

  function skillMention(skill) {
    return el("span", { class: "wsp-skill-mention", title: skill.description || skill.path }, skillIcon(),
      el("span", { text: skill.name }));
  }

  function fileMention(path) {
    return el("span", { class: "wsp-file-mention", title: path },
      workspaceIcon("file", "wsp-file-icon"),
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
    card.style.setProperty("--wsp-progress-delay", `-${(Date.now() % 1600) / 1000}s`);
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
      workspaceIcon("chevronDown", "wsp-tool-chevron")));
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
        el("span", { class: "wsp-tool-symbol" }, workspaceIcon("terminal")),
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
      row.style.setProperty("--wsp-progress-delay", `-${(Date.now() % 1600) / 1000}s`);
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
    const key = JSON.stringify([state.projectId, state.sessionId, request.id]);
    const signature = JSON.stringify([request, page, state.questionErrors.get(request.id) || ""]);
    const existing = Array.from(content.children).find(node => node.workspaceQuestionKey === key);
    if (existing?.workspaceQuestionSignature === signature && existing.workspaceQuestionDrafts === drafts) return existing;
    const question = questions[page];
    const draft = drafts[page] || { selected: [], custom: "" };
    drafts[page] = draft;
    const card = el("div", { class: "wsp-question" },
      el("div", { class: "wsp-question-head" },
        el("strong", { text: "Sona Code 需要你的回答" }),
        el("span", { class: "wsp-question-count", text: `${page + 1} / ${questions.length}` })));
    card.workspaceQuestionKey = key;
    card.workspaceQuestionSignature = signature;
    card.workspaceQuestionDrafts = drafts;
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
          card.workspaceQuestionSignature = JSON.stringify([request, page, ""]);
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
        card.workspaceQuestionSignature = JSON.stringify([request, page, ""]);
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

  function renderMessages(target) {
    const turns = workspaceMessageTurns(state.messages);
    const existing = new Map(Array.from(content.children)
      .filter(row => row.workspaceMessageKey)
      .map(row => [row.workspaceMessageKey, row]));
    if (!state.sessionId) {
      target.append(empty("开始一段新对话", "选择项目后新建对话，Sona Code 会在该项目目录中工作。",
        activeProject() ? ["新建对话", () => createSession()] : ["新建项目", openAddProject]));
      return;
    }
    if (!state.messages.length && !state.permissions.some((item) => item.sessionID === state.sessionId) &&
      !state.questions.some((item) => item.sessionID === state.sessionId)) {
      if (state.messageLoadError) {
        target.append(el("div", { class: "wsp-error", text: state.messageLoadError }));
        return;
      }
      if (!state.messagesLoaded) {
        target.append(el("div", { class: "wsp-action-progress", role: "status", text: "正在加载对话…" }));
        return;
      }
      target.append(empty("输入你的开发需求", "发送第一条消息后，这里会实时显示回复和工具操作。"));
      if (state.compactingSessionId === state.sessionId) target.append(compactionCard("busy"));
      if (state.pendingAction) target.append(el("div", { class: "wsp-action-progress", text: `正在执行 ${state.pendingAction}…` }));
      if (state.actionError) target.append(el("div", { class: "wsp-error", text: state.actionError }));
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
    const sessionStatus = state.statuses?.[state.sessionId];
    const running = ["busy", "retry"].includes(sessionStatus?.type) && !compactionRunning() &&
      !state.permissions.some(item => item.sessionID === state.sessionId) &&
      !state.questions.some(item => item.sessionID === state.sessionId);
    if (running && displayMessages.at(-1)?.info?.role === "user") {
      const user = displayMessages.at(-1);
      displayMessages.push({ info: { id: `waiting:${user.info.id}`, role: "assistant", parentID: user.info.id,
        time: user.info.time }, parts: [], awaitingReply: true });
    }
    let previousDay = "";
    const lastReplies = new Map();
    displayMessages.forEach((message, index) => {
      if (message.info?.role === "assistant") lastReplies.set(message.info.parentID, index);
    });
    const lastUserId = lastUserMessage()?.info.id;
    for (const [messageIndex, message] of displayMessages.entries()) {
      const day = messageDay(message);
      if (day !== previousDay) {
        target.append(el("div", { class: "wsp-date-divider", text: day }));
        previousDay = day;
      }
      if (message.info?.role === "user" && message.parts.some((part) => part.type === "compaction")) {
        const replies = turns.get(message.info.id)?.replies || [];
        const complete = replies.some((item) => item.info.summary && item.info.finish && !item.info.error);
        const failed = replies.some((item) => item.info.error);
        target.append(compactionCard(complete ? "ready" : failed ? "error" :
          compactionRunning() ? "busy" : "error"));
        continue;
      }
      const role = message?.info?.role || "assistant";
      const modelInfo = message.modelInfo || message.info;
      const modelLabel = modelInfo?.modelID ? modelDisplayName(modelInfo.providerID, modelInfo.modelID) : "";
      const laterReply = lastReplies.get(message.info?.parentID) > messageIndex;
      const diffs = role === "assistant" && !laterReply
        ? workspaceTurnDiffs(state.messages, message, activeProject()?.path, turns.get(message.info?.parentID)) : [];
      let progressLabel = "";
      if (running && role === "assistant" && messageIndex === displayMessages.length - 1 && !message.errorInfo) {
        const activeTool = message.parts.some(part => part.type === "tool" &&
          ["pending", "running"].includes(part.state?.status));
        const streamingText = message.parts.some(part => part.type === "text" && !part.synthetic &&
          part.text && part.time?.end == null && modelInfo?.time?.completed == null);
        progressLabel = sessionStatus.type === "retry" ? "正在重试…" : activeTool ? "" :
          streamingText ? "正在回复…" : "正在思考…";
      }
      const key = JSON.stringify([state.projectId, state.sessionId, message.info?.id || messageIndex]);
      const signature = JSON.stringify([message, modelLabel, diffs, progressLabel,
        role === "user" ? [message.info?.id === lastUserId, state.sending,
          state.statuses?.[state.sessionId]?.type] : null,
        !message.parts.length ? state.statuses?.[state.sessionId]?.type : null,
        message.parts.filter(part => part.type === "tool").map(skillForTool)]);
      const cached = existing.get(key);
      if (cached?.workspaceMessageSignature === signature) {
        const trigger = cached.querySelector(".wsp-change-summary");
        if (trigger) changeTriggers.set(message.info.parentID, { trigger, diffs });
        target.append(cached);
        continue;
      }
      const row = el("article", { class: "wsp-message " + (role === "user" ? "user" : "assistant") });
      if (role !== "user") row.append(el("img", { class: "wsp-avatar", src: "sona-code-icon.png", alt: "", width: 30, height: 30 }));
      const body = el("div", { class: "wsp-message-inner" });
      row.workspaceMessageKey = key;
      row.workspaceMessageSignature = signature;
      if (role !== "user") body.append(el("div", { class: "wsp-message-meta" },
        el("strong", { text: "Sona" }),
        modelLabel));
      const parts = message.parts || [];
      const references = role === "user" ? parts.flatMap(part => workspaceDirectoryReference(part) ?
        [workspaceDirectoryReference(part)] : part.type === "file" &&
          part.url?.startsWith("file:") && part.filename ? [part.filename] : []) : [];
      if (message.skillUse) {
        body.append(el("div", { class: "wsp-text wsp-skill-message" }, skillMention(message.skillUse),
          message.skillUse.arguments ? ` ${message.skillUse.arguments}` : ""));
      }
      const error = role !== "user" ? messageError(message.errorInfo) : null;
      if (error) body.append(error);
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
        if (part.type === "text" && !part.synthetic && !workspaceDirectoryReference(part) && (role === "user" || part.text)) {
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
      if (progressLabel) body.append(el("div", { class: "wsp-thinking", role: "status", "aria-live": "polite" },
        statusIcon("busy"), el("span", { text: progressLabel })));
      else if (role !== "user" && !error && body.children.length === 1) {
        body.append(el("div", { class: "wsp-no-response", text: "本轮未收到回复" }));
      }
      if (diffs.length) body.append(changeSummary(message.info.parentID, diffs));
      const column = el("div", { class: "wsp-message-column" }, body);
      if (!message.awaitingReply) column.append(messageActions(message));
      row.append(column);
      target.append(row);
    }
    if (state.compactingSessionId === state.sessionId && !state.messages.some((message) =>
      message.info?.role === "user" && message.parts?.some((part) => part.type === "compaction"))) {
      target.append(compactionCard("busy"));
    }
    for (const permission of state.permissions.filter((item) => item.sessionID === state.sessionId)) {
      const card = el("div", { class: "wsp-permission" },
        el("strong", { text: `需要确认：${permission.permission || permission.action || "工具操作"}` }),
        el("p", { text: (permission.patterns || permission.resources || []).join("、") || "Sona Code 请求继续执行此操作。" }));
      const actions = el("div", { class: "wsp-permission-actions" });
      for (const [label, reply, cls] of [["允许一次", "once", "wsp-mini primary"], ["始终允许", "always", "wsp-mini"], ["拒绝", "reject", "wsp-mini"]]) {
        actions.append(el("button", { class: cls, type: "button", text: label, onclick: () => replyPermission(permission.id, reply) }));
      }
      card.append(actions);
      target.append(card);
    }
    for (const question of state.questions.filter((item) => item.sessionID === state.sessionId)) {
      target.append(questionCard(question));
    }
    if (state.pendingAction) target.append(el("div", { class: "wsp-action-progress", text: `正在执行 ${state.pendingAction}…` }));
    if (state.actionError) target.append(el("div", { class: "wsp-error", text: state.actionError }));
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
    const before = diff.before ? diff.before.split("\n") : [];
    const after = diff.after ? diff.after.split("\n") : [];
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
    return workspaceDiffLineCounts(diff);
  }

  function changeTotals(diffs) {
    const counts = diffs.map(diffLineCounts).filter(Boolean);
    const prefix = counts.length < diffs.length || diffs.some(diff => diff.countsPartial) ? "≥" : "";
    return { additions: counts.length ? `${prefix}+${counts.reduce((sum, item) => sum + item.additions, 0)}` : "—",
      deletions: counts.length ? `${prefix}−${counts.reduce((sum, item) => sum + item.deletions, 0)}` : "—" };
  }

  function changeStats(diffs) {
    const totals = changeTotals(diffs);
    const unknownWrites = diffs.filter(diff => !diffLineCounts(diff) && Number.isFinite(diff.writtenLines));
    if (unknownWrites.length && totals.additions === "—") {
      return el("span", { class: "wsp-change-stats" },
        el("span", { class: "add", text: `写入 ${unknownWrites.reduce((sum, diff) => sum + diff.writtenLines, 0)} 行`,
          title: "成功写入的内容行数，可能包含对同一文件的多次覆盖" }),
        el("small", { class: "wsp-change-count-kind", text: "增删待确认", title: "OpenCode 未返回原文件内容或增删行数" }));
    }
    return el("span", { class: "wsp-change-stats" },
      el("span", { class: "add", text: totals.additions, title: "新增行" }),
      el("span", { class: "remove", text: totals.deletions, title: "删除行" }),
      diffs.some(diff => diff.cumulative) ? el("small", { class: "wsp-change-count-kind", text: "累计编辑",
        title: "成功编辑操作的增删行数累计，可能包含对同一行的多次修改" }) : null);
  }

  function changeFileIcon() {
    return workspaceIcon("file");
  }

  function closeChangePopover(restoreFocus = false) {
    if (!changePopover) return;
    const { trigger, panel } = changePopover;
    trigger.setAttribute("aria-expanded", "false");
    panel.remove();
    changePopover = null;
    if (restoreFocus && trigger.isConnected) trigger.focus({ preventScroll: true });
  }

  function positionChangePopover() {
    if (!changePopover) return;
    const { trigger, panel } = changePopover;
    const anchor = trigger.getBoundingClientRect();
    const area = scroll.getBoundingClientRect();
    if (anchor.bottom < area.top || anchor.top > area.bottom) { closeChangePopover(); return; }
    const below = window.innerHeight - anchor.bottom - 20;
    const above = anchor.top - 20;
    const downward = below >= Math.min(panel.scrollHeight, 300) || below >= above;
    panel.style.maxHeight = `${Math.min(360, Math.max(80, downward ? below : above))}px`;
    panel.style.left = `${Math.max(12, Math.min(anchor.left, window.innerWidth - panel.offsetWidth - 12))}px`;
    panel.style.top = `${downward ? anchor.bottom + 8 : Math.max(12, anchor.top - panel.offsetHeight - 8)}px`;
  }

  function populateChangePopover(diffs) {
    const { panel, messageId } = changePopover;
    const focusedFile = document.activeElement?.dataset.changeFile;
    panel.replaceChildren(el("div", { class: "wsp-change-popover-head" },
      el("strong", { text: "本轮改动" }), changeStats(diffs),
      el("button", { class: "wsp-change-close", type: "button", "aria-label": "关闭文件列表",
        onclick: () => closeChangePopover(true) }, workspaceIcon("close"))));
    const list = el("div", { class: "wsp-change-list" });
    for (const diff of diffs) {
      const path = diff.file || diff.path;
      const separator = path.lastIndexOf("/");
      list.append(el("div", { class: "wsp-change-file-row" },
        el("button", { class: "wsp-change-file", type: "button", "data-change-file": path,
          title: path, onclick: () => openMessageChange(messageId, diffs, path) },
          changeFileIcon(),
          el("span", { class: "wsp-change-file-copy" },
            el("strong", { text: path.slice(separator + 1) }),
            separator >= 0 ? el("small", { text: path.slice(0, separator) }) : null),
          changeStats([diff]), workspaceIcon("chevron", "wsp-change-arrow")),
        browserPreviewFile(path) ? el("button", { class: "wsp-change-locate", type: "button",
          title: "在浏览器中打开", "aria-label": "在浏览器中打开 " + path,
          onclick: () => openTreePath(path, "browser") }, workspaceIcon("globe")) : null,
        el("button", { class: "wsp-change-locate", type: "button", title: "在目录树中定位",
          "aria-label": "在目录树中定位 " + path,
          onclick: () => { closeChangePopover(); void tree.locate(activeProject(), path); } }, workspaceIcon("locate"))));
    }
    panel.append(list);
    if (focusedFile) Array.from(list.querySelectorAll("button")).find(button =>
      button.dataset.changeFile === focusedFile)?.focus({ preventScroll: true });
    changePopover.signature = JSON.stringify(diffs);
  }

  function openChangePopover(messageId, trigger, diffs) {
    closeChangePopover();
    const panel = el("div", { class: "wsp-change-popover", id: "wsp-change-popover",
      role: "dialog", "aria-label": "本轮修改的文件" });
    changePopover = { messageId, trigger, panel, signature: "" };
    root.append(panel);
    trigger.setAttribute("aria-expanded", "true");
    populateChangePopover(diffs);
    positionChangePopover();
    panel.addEventListener("keydown", event => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const buttons = Array.from(panel.querySelectorAll(".wsp-change-file"));
      const index = buttons.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 :
        (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next]?.focus({ preventScroll: true });
    });
  }

  function changeSummary(messageId, diffs) {
    const totals = changeTotals(diffs);
    const trigger = el("button", { class: "wsp-change-summary", type: "button", "data-change-message": messageId,
      "aria-haspopup": "dialog", "aria-expanded": "false", "aria-controls": "wsp-change-popover",
      "aria-label": `已修改 ${diffs.length} 个文件，新增 ${totals.additions} 行，删除 ${totals.deletions} 行，查看文件列表`,
      onclick: () => changePopover?.messageId === messageId ? closeChangePopover() : openChangePopover(messageId, trigger, diffs),
      onkeydown: event => {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          openChangePopover(messageId, trigger, diffs);
          changePopover.panel.querySelector(".wsp-change-file")?.focus({ preventScroll: true });
        }
      } },
      changeFileIcon(), el("span", { text: `已修改 ${diffs.length} 个文件` }),
      el("span", { class: "wsp-change-divider", "aria-hidden": "true" }), changeStats(diffs),
      workspaceIcon("chevronDown", "wsp-change-chevron"));
    changeTriggers.set(messageId, { trigger, diffs });
    return trigger;
  }

  async function openMessageChange(messageId, diffs, file) {
    closeChangePopover();
    const selection = { messageId, diffs, file, loading: true, error: "" };
    state.selectedChange = selection;
    state.tab = workspaceSelection.tab = "changes";
    renderHeader(); renderMain();
    focusSelectedChange();
    const projectId = state.projectId;
    const sessionId = state.sessionId;
    try {
      const result = await api(`${sessionPath(projectId, sessionId)}/diff?message_id=${encodeURIComponent(messageId)}`, { silent: true });
      if (!alive() || state.selectedChange !== selection || state.projectId !== projectId || state.sessionId !== sessionId) return;
      if (Array.isArray(result) && result.length) selection.diffs = result.map(diff => ({ ...diff,
        file: workspaceRelativeFile(diff.file || diff.path, activeProject()?.path) }));
    } catch (error) {
      if (state.selectedChange === selection) selection.error = detail(error);
    } finally {
      if (state.selectedChange === selection) selection.loading = false;
      if (alive() && state.selectedChange === selection && state.tab === "changes") {
        renderMain(); focusSelectedChange();
      }
    }
  }

  function focusSelectedChange() {
    const card = Array.from(content.querySelectorAll(".wsp-diff-file")).find(item =>
      item.dataset.changeFile === state.selectedChange?.file);
    if (!card) { scroll.scrollTop = 0; return; }
    scroll.scrollTop += card.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 16;
    card.focus({ preventScroll: true });
  }

  function changedFiles() {
    const writtenFiles = new Map();
    if (state.sessionId && !state.diffs.length) {
      for (const turn of workspaceMessageTurns(state.messages).values()) {
        const message = turn.replies[0];
        if (!message) continue;
        for (const diff of workspaceTurnDiffs(state.messages, message, activeProject()?.path, turn)) {
          const previous = writtenFiles.get(diff.file);
          writtenFiles.set(diff.file, previous ? workspaceCombineFileEdits(previous, diff, diff.file) : diff);
        }
      }
    }
    const diffs = state.diffs.length ? state.diffs : [...writtenFiles.values()];
    return [...new Map(diffs.map((diff, index) => [diff.file || diff.path || index, diff])).values()];
  }

  function renderChanges(target = content) {
    const selection = state.selectedChange;
    target.append(el("div", { class: "wsp-changes-heading" },
      el("h2", { text: "文件改动" }), selection ? el("span", { class: "wsp-change-scope", text: "本轮" }) : null,
      selection ? el("button", { class: "wsp-change-back", type: "button", text: "返回对话", onclick: () => {
        state.tab = workspaceSelection.tab = "chat";
        renderHeader(); renderMain();
        const trigger = changeTriggers.get(selection.messageId)?.trigger;
        if (trigger) {
          scroll.scrollTop += trigger.getBoundingClientRect().top - scroll.getBoundingClientRect().top - scroll.clientHeight / 2;
          trigger.focus({ preventScroll: true });
        }
      } }) : null));
    const diffs = selection ? selection.diffs : changedFiles();
    if (selection?.loading) target.append(el("div", { class: "wsp-change-loading", role: "status", text: "正在加载差异…" }));
    if (selection?.error) target.append(el("div", { class: "wsp-error", role: "status", text: `差异加载失败：${selection.error}` }));
    if (!state.sessionId || !diffs.length) {
      target.append(empty("暂无文件改动", "Sona Code 修改文件后，这里会显示改动摘要。"));
      return;
    }
    const counts = diffs.map(diffLineCounts);
    const knownCounts = counts.filter(Boolean);
    const additions = knownCounts.reduce((sum, count) => sum + count.additions, 0);
    const deletions = knownCounts.reduce((sum, count) => sum + count.deletions, 0);
    const countLabel = (value, sign) => !knownCounts.length ? "—" :
      `${knownCounts.length < diffs.length || diffs.some(diff => diff.countsPartial) ? "≥" : ""}${sign}${value}`;
    const overview = el("div", { class: "wsp-diff-overview" });
    for (const [value, label, className] of [[diffs.length, "修改文件", ""], [countLabel(additions, "+"), "新增行", "add"], [countLabel(deletions, "−"), "删除行", "remove"]]) {
      overview.append(el("div", { class: `wsp-diff-metric ${className}` },
        el("strong", { text: String(value) }), el("span", { text: label })));
    }
    target.append(overview);
    for (const diff of diffs) {
      const title = workspaceRelativeFile(diff.file || diff.path || "文件", activeProject()?.path);
      const status = diff.status || (diff.before === "" && diff.after ? "added" : diff.before && diff.after === "" ? "deleted" : "modified");
      const label = diff.derived ? "已写入" : status === "added" ? "新增" : status === "deleted" ? "删除" : "修改";
      const card = el("section", { class: `wsp-diff-file${selection?.file === title ? " selected" : ""}`,
        "data-change-file": title, tabindex: "-1", "aria-label": title },
        el("div", { class: "wsp-diff-head" },
          el("span", { class: "wsp-file-badge", text: label }),
          el("span", { class: "wsp-diff-path", title, text: title }),
          browserPreviewFile(title) ? el("button", { class: "wsp-change-locate", type: "button",
            title: "在浏览器中打开", "aria-label": "在浏览器中打开 " + title,
            onclick: () => openTreePath(title, "browser") }, workspaceIcon("globe")) : null,
          el("button", { class: "wsp-change-locate", type: "button", title: "在目录树中定位",
            "aria-label": "在目录树中定位 " + title,
            onclick: () => void tree.locate(activeProject(), title) }, workspaceIcon("locate")),
          changeStats([diff])));
      const rows = diffRows(diff);
      if (rows.length) {
        if (diff.derived) card.append(el("p", { class: "wsp-diff-preview-label", text: "写入内容预览 · Sona Code 未返回完整逐行差异" }));
        const body = el("div", { class: "wsp-diff-body" });
        for (const row of rows) body.append(el("div", { class: `wsp-diff-line ${row.type}` },
          el("span", { class: "wsp-line-number", text: String(row.number) }), el("span", { text: row.text })));
        card.append(body);
      } else card.append(el("p", { class: "wsp-diff-unavailable", text: diff.derived
        ? "Sona Code 记录了文件写入，但没有返回可显示的逐行差异。" : "此文件没有可显示的逐行差异。" }));
      target.append(card);
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
        ...(callId ? { href: "#/calls/" + encodeURIComponent(callId) + "?from=workspace", title: "查看模型请求详情" } : {}),
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
      addEvent(turn.started_at, "模型请求", turn.model || "Sona Code 回复",
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
      else addEvent(start, "模型请求", modelDisplayName(info.providerID, info.modelID) || "Sona Code 回复",
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
    else content.append(empty("暂无对话活动", "Sona Code 回复或调用工具后，这里会按顺序展示。"));
  }

  function renderTodoPanel() {
    const key = workspaceConversationKey(state.projectId, state.sessionId);
    const turn = (state.messages || []).filter(message => message.info?.role === "user").at(-1)?.info?.id || "";
    if (todoPanelTurns.has(key) && todoPanelTurns.get(key) !== turn && turn) dismissedTodoPanels.add(key);
    todoPanelTurns.set(key, turn);
    const available = !!state.projectId && !!state.sessionId && state.tab === "chat" && state.todos.length > 0;
    const open = available && !dismissedTodoPanels.has(key);
    todoTrigger.hidden = !available;
    todoTrigger.setAttribute("aria-expanded", String(open));
    todoTrigger.setAttribute("aria-label", open ? "关闭任务进度" : "打开任务进度");
    todoTrigger.title = open ? "关闭任务进度" : "打开任务进度";
    todoTrigger.classList.toggle("active", open);
    todoPanel.hidden = !open;
    root.classList.toggle("todo-panel-open", open);
    if (!available) { renderedTodoSignature = ""; return; }

    const completed = state.todos.filter((todo) => todo.status === "completed").length;
    view.querySelector("#wsp-todo-trigger-count").textContent = `${completed}/${state.todos.length}`;
    if (!open) { renderedTodoSignature = ""; return; }
    view.querySelector("#wsp-todo-summary").textContent = completed === state.todos.length ? "全部任务已完成" : "按计划逐步完成";
    view.querySelector("#wsp-todo-progress").textContent = `${completed} / ${state.todos.length}`;
    view.querySelector("#wsp-todo-progress-fill").style.width = `${completed / state.todos.length * 100}%`;
    const signature = key + JSON.stringify(state.todos);
    if (signature === renderedTodoSignature) return;
    renderedTodoSignature = signature;
    const listScrollTop = todoList.scrollTop;
    todoList.replaceChildren(...state.todos.map((todo) => {
      const status = ["completed", "in_progress", "cancelled"].includes(todo.status) ? todo.status : "pending";
      const label = { completed: "已完成", in_progress: "进行中", cancelled: "已取消", pending: "待处理" }[status];
      return el("li", { class: `wsp-todo-panel-item ${status}` },
        el("span", { class: "wsp-todo-panel-mark", text: status === "completed" ? "✓" : status === "in_progress" ? "●" : status === "cancelled" ? "−" : "" }),
        el("span", { class: "wsp-todo-panel-copy" },
          el("span", { class: "wsp-todo-panel-text", text: todo.content || todo.title || "任务" }),
          el("small", { text: label })));
    }));
    todoList.scrollTop = listScrollTop;
  }

  function renderTasks() {
    content.append(el("h2", { class: "wsp-section-title", text: "任务与子对话" }),
      el("p", { class: "wsp-section-note", text: "Sona Code 在本轮对话中维护的待办和委派任务。" }));
    if (!state.todos.length && !state.children.length) {
      content.append(empty("暂无任务", "Sona Code 制定计划或启动子任务后会显示在这里。"));
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
    composerDock.hidden = state.tab !== "chat";
    const previousTop = scroll.scrollTop;
    const previousMaximum = Math.max(0, scroll.scrollHeight - scroll.clientHeight);
    const nearBottom = previousMaximum - previousTop <= 80;
    renderTodoPanel();
    const stickToBottom = state.tab === "chat" && (forceBottom || nearBottom);
    const active = document.activeElement;
    const editingQuestion = active?.classList?.contains("wsp-question-custom")
      ? { id: active.dataset.requestId, index: active.dataset.questionIndex,
          start: active.selectionStart, end: active.selectionEnd } : null;
    const keepTrajectory = state.tab === "trajectory" && !state.recordingError &&
      trajectoryView?.parentNode === content && trajectorySignature === JSON.stringify(state.recordingData);
    const focusedChangeTrigger = changePopover?.trigger === document.activeElement;
    changeTriggers.clear();
    if (!keepTrajectory && !["chat", "changes"].includes(state.tab)) content.replaceChildren();
    content.classList.remove("wsp-content-trajectory");
    const changeCount = view.querySelector("#wsp-change-count");
    changeCount.textContent = String(state.sessionId ? changedFiles().length : 0);
    changeCount.title = `修改了 ${changeCount.textContent} 个文件`;
    if (state.tab === "trajectory") renderSessionTrajectory();
    if (state.tab === "chat") {
      const children = [];
      renderMessages({ append: (...nodes) => children.push(...nodes) });
      workspaceSyncChildren(content, children);
    }
    if (state.tab === "changes") {
      const signature = JSON.stringify([state.projectId, state.sessionId, state.selectedChange, changedFiles()]);
      if (changesView?.parentNode !== content || changesSignature !== signature) {
        const positions = new Map(Array.from(content.querySelectorAll(".wsp-diff-file")).map(card => {
          const body = card.querySelector(".wsp-diff-body");
          return [card.dataset.changeFile, { top: body?.scrollTop || 0, left: body?.scrollLeft || 0 }];
        }));
        const children = [];
        renderChanges({ append: (...nodes) => children.push(...nodes) });
        workspaceSyncChildren(content, children);
        for (const card of content.querySelectorAll(".wsp-diff-file")) {
          const position = positions.get(card.dataset.changeFile);
          const body = card.querySelector(".wsp-diff-body");
          if (position && body) { body.scrollTop = position.top; body.scrollLeft = position.left; }
        }
        changesView = content.firstChild;
        changesSignature = signature;
      }
    }
    if (state.tab === "activity") renderActivity();
    if (state.tab === "tasks") renderTasks();
    if (changePopover) {
      const updated = changeTriggers.get(changePopover.messageId);
      if (!updated) closeChangePopover();
      else {
        changePopover.trigger = updated.trigger;
        updated.trigger.setAttribute("aria-expanded", "true");
        if (changePopover.signature !== JSON.stringify(updated.diffs)) populateChangePopover(updated.diffs);
        if (focusedChangeTrigger) updated.trigger.focus({ preventScroll: true });
      }
    }
    renderStatsLine();
    followLatest = stickToBottom;
    scroll.scrollTop = stickToBottom ? scroll.scrollHeight : previousTop;
    positionChangePopover();
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
      if (restored && restored !== active) {
        restored.focus({ preventScroll: true });
        restored.setSelectionRange(editingQuestion.start, editingQuestion.end);
      }
    }
  }

  async function loadSessions(project) {
    if (!project) return;
    if (sessionLoads.has(project.id)) return sessionLoads.get(project.id);
    const pending = fetchSessions(project);
    sessionLoads.set(project.id, pending);
    try { await pending; }
    finally { if (sessionLoads.get(project.id) === pending) sessionLoads.delete(project.id); }
  }

  async function fetchSessions(project) {
    try {
      const data = await api(`workspace/projects/${encodeURIComponent(project.id)}/sessions`, { silent: true });
      if (!alive()) return;
      const items = (data.items || []).filter((item) => !item.time?.archived);
      items.sort((a, b) => (b.time?.updated || 0) - (a.time?.updated || 0));
      state.sessions.set(project.id, workspaceOrderItems(items, `sona-code:session-order:${project.id}`));
      for (const item of items) state.sessionDetails.set(workspaceConversationKey(project.id, item.id), item);
      if (state.projectId === project.id) lastSessionListRefresh = Date.now();
      state.errors.delete(project.id);
      if (state.projectId === project.id && !items.some((item) => item.id === state.sessionId) &&
          !state.sessionDetails.has(workspaceConversationKey(project.id, state.sessionId))) {
        saveDraft();
        saveConversationView();
        cancelSelectedRefresh();
        state.sessionId = items[0]?.id || null;
        restoreConversationView();
        restoreDraft();
        scrollToLatestOnLoad = true;
        workspaceSelection.sessionId = state.sessionId;
        persistWorkspaceSelection();
        refreshSelected();
      } else if (state.projectId === project.id && !state.messagesLoaded && !selectedRefresh) {
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

  function modelDisplayName(providerId, modelId) {
    const provider = state.providers.find((item) => item.id === providerId);
    let providerName = provider?.name || providerId || "";
    if (!provider?.name && providerId?.startsWith("sonacode-")) {
      try {
        const encoded = providerId.slice(6).replace(/-/g, "+").replace(/_/g, "/");
        const bytes = Uint8Array.from(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")), (char) => char.charCodeAt(0));
        providerName = new TextDecoder().decode(bytes);
      } catch (_) { providerName = ""; }
    }
    return [providerName, provider?.models?.[modelId]?.name || modelId].filter(Boolean).join(" / ");
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
      workspaceIcon("chevronDown", "wsp-model-chevron"));
    modelButton.title = model?.source === "custom" && !model.route_through_proxy
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
          ? "Sona Code 已保存此 Provider 的凭据。新 Key 保存后会替换旧凭据；认证是否有效，以实际请求结果为准。"
          : "保存到 Sona Code 的本地凭据存储。该 Provider 后续请求会使用这个 Key。" }),
        keyInput, message,
        el("div", { class: "wsp-modal-actions" },
          el("button", { class: "wsp-mini", type: "button", text: "取消", onclick: () => mask.remove() }),
          el("button", { class: "wsp-mini primary", type: "button", text: "保存到 Sona Code", onclick: async (event) => {
            const button = event.currentTarget;
            const key = keyInput.value.trim();
            if (!key) { message.textContent = "请先输入 API Key"; keyInput.focus(); return; }
            button.disabled = true;
            message.textContent = "正在保存到本机 Sona Code 凭据存储…";
            try {
              await api(`workspace/projects/${encodeURIComponent(state.projectId)}/providers/${encodeURIComponent(provider.id)}/api-key`, {
                method: "POST", body: { key }, silent: true,
              });
              keyInput.value = "";
              state.connectedProviders.add(provider.id);
              const cached = workspaceModelCache.get(state.projectId);
              if (cached?.data) cached.data.connected = [...state.connectedProviders];
              mask.remove();
              renderModelPicker();
              toast(`${provider.name || provider.id} 凭据已保存到 Sona Code`, "ok");
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
      try {
        localStorage.setItem(`sona-code:model:${state.projectId}`, value);
        localStorage.setItem("sona-code:last-model", value);
      } catch (_) { /* Storage may be unavailable. */ }
    }
    updateModelButton();
    renderStatsLine();
    closeModelPicker();
    input.focus();
  }

  function renderModelPicker() {
    const refresh = view.querySelector("#wsp-model-refresh");
    refresh.hidden = state.modelSource !== "sona" || state.sonaConnected === false;
    modelList.replaceChildren();
    if (state.modelSource === "sona" && state.sonaConnected === false) {
      modelList.append(el("p", { class: "wsp-picker-empty", text: state.modelLoadError || "请登录 Sona 网站以获取已订阅模型。" }),
        el("button", { type: "button", class: "wsp-model-option", text: "登录 Sona", onclick: async event => {
          const button = event.currentTarget;
          if (button.disabled) return;
          button.disabled = true; button.textContent = "等待浏览器登录…";
          try {
            await startSonaLogin(state.sonaEnvironment);
            if (alive() && state.projectId) await loadModels(state.projectId);
          } catch (error) {
            if (alive()) { state.modelLoadError = detail(error); renderModelPicker(); }
          } finally { button.disabled = false; button.textContent = "登录 Sona"; }
        } }));
      if (!modelPicker.hidden) positionPicker(modelPicker, modelButton, "right");
      return;
    }
    if (state.modelLoadError) {
      modelList.append(el("p", { class: "wsp-picker-empty", text: `模型加载失败：${state.modelLoadError}` }));
      return;
    }
    const query = modelSearch.value.trim().toLocaleLowerCase();
    const selected = state.chosenModels.get(state.projectId) || "";
    if (!query) modelList.append(el("button", { type: "button", class: "wsp-model-option" + (!selected ? " selected" : ""),
      onclick: () => chooseModel("") },
      el("span", { text: "当前来源默认模型" }), el("small", { text: "使用当前来源的默认模型" })));
    let count = 0;
    for (const provider of state.providers) {
      const items = Object.entries(provider.models || {}).filter(([id, model]) =>
        `${provider.id} ${provider.name || ""} ${id} ${model.name || ""}`.toLocaleLowerCase().includes(query));
      if (!items.length) continue;
      const group = el("section", { class: "wsp-model-group" },
        el("div", { class: "wsp-model-group-title" },
          el("strong", { text: provider.name || provider.id }),
          el("span", { text: `${items.length} 个模型 · ${provider.source === "custom" ? (provider.route_through_proxy ? "应用配置 · 代理记录" : "应用配置 · 直连") : provider.source === "sona" ? `Sona ${state.sonaEnvironment.toUpperCase()}` : "OpenCode 原生配置"}` })));
      if (provider.source === "native") {
        group.append(el("button", { type: "button", class: "wsp-provider-key", text: "设置 API Key", onclick: () => openProviderKeyDialog(provider) }));
      }
      for (const [id, model] of items) {
        const value = `${provider.id}\u0000${id}`;
        group.append(el("button", { type: "button", class: "wsp-model-option" + (value === selected ? " selected" : ""),
          title: `${provider.id} / ${id}`, onclick: () => chooseModel(value) },
          el("span", { text: model.name || id }),
          el("small", { text: [id, model.capabilities?.input?.image ? "图片" : "", model.capabilities?.reasoning ? "思考" : "",
            model.source === "custom" ? (model.route_through_proxy ? "代理记录" : "直连 · 不记录代理轨迹") : model.source === "sona" ? "Sona 网站" : "原生路由",
            model.capabilities?.toolcall === false ? "不支持工具调用" : ""].filter(Boolean).join(" · ") })));
        count++;
      }
      modelList.append(group);
    }
    if (!count && query) modelList.append(el("p", { class: "wsp-picker-empty", text: "没有匹配的模型" }));
    else if (!state.providers.length) {
      modelList.append(el("p", { class: "wsp-picker-empty", text: state.modelSource === "sona"
        ? (state.sonaConnected === null ? "正在读取模型…" : "当前环境没有可用订阅，请订阅后点击刷新。") : "暂无可用模型。" }));
      if (state.modelSource !== "sona") modelList.append(el("a", { href: "#/models", class: "wsp-model-option", text: "前往模型页面" }));
    }
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
    const version = ++modelLoadVersion;
    state.modelLoadError = "";
    try {
      let entry = workspaceModelCache.get(projectId);
      if (!entry || (!entry.pending && (!entry.data || (entry.data.source !== "sona" && Date.now() - entry.loadedAt >= WORKSPACE_MODEL_TTL)))) {
        const previous = entry?.data;
        entry = { data: previous, loadedAt: 0, pending: null };
        workspaceModelCache.set(projectId, entry);
        entry.pending = api(`workspace/projects/${encodeURIComponent(projectId)}/models`, { silent: true })
          .then(data => { entry.data = data; entry.loadedAt = Date.now(); return data; })
          .finally(() => { entry.pending = null; });
      }
      const apply = data => {
        if (!alive() || state.projectId !== projectId || version !== modelLoadVersion) return;
        state.providers = Array.isArray(data.providers) ? data.providers.slice().sort((a, b) =>
          (a.id === "opencode" ? -1 : b.id === "opencode" ? 1 : (a.name || a.id).localeCompare(b.name || b.id))) : [];
        state.modelSource = data.source || "sona";
        state.sonaEnvironment = data.sona_environment || "prod";
        state.sonaConnected = data.sona_connected === true;
        state.defaultModel = data.default_model || null;
        state.connectedProviders = new Set(Array.isArray(data.connected) ? data.connected : []);
      };
      if (entry.data) { apply(entry.data); updateModelButton(); }
      else { state.providers = []; state.defaultModel = null; updateModelButton(false); }
      const data = entry.pending ? await entry.pending : entry.data;
      if (!alive() || state.projectId !== projectId || version !== modelLoadVersion) return;
      apply(data);
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
      if (alive() && state.projectId === projectId && version === modelLoadVersion) {
        state.modelLoadError = detail(error);
        if (!modelPicker.hidden) renderModelPicker();
      }
    }
  }

  async function refreshSonaModels() {
    const button = view.querySelector("#wsp-model-refresh");
    if (button.disabled) return;
    const projectId = state.projectId;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      await api("models/sona/refresh", { method: "POST", silent: true });
      workspaceModelCache.clear();
      if (alive() && state.projectId === projectId) {
        await loadModels(projectId);
        if (!state.modelLoadError) toast("模型已刷新");
      }
    } catch (error) {
      if (error.status === 401) {
        workspaceModelCache.clear();
        if (alive() && state.projectId === projectId) {
          await loadModels(projectId);
          state.modelLoadError = "网站登录已失效，请重新登录。";
          if (!modelPicker.hidden) renderModelPicker();
        }
      }
      toast(`刷新模型失败：${detail(error)}`, "error");
    }
    finally { button.disabled = false; button.removeAttribute("aria-busy"); }
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
    } catch (_) { /* The default Build agent remains usable. */ }
    finally { if (alive() && state.projectId === projectId) loadCommands(projectId); }
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
      el("strong", { text: `/${command.name}` }), el("span", { text: command.description || "Sona Code 命令" })));
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
        if (data.unavailable?.length) list.append(el("p", { class: "wsp-question-error", text: `以下技能未被 Sona Code 加载或存在同名命令：${data.unavailable.join("、")}` }));
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
    if (name === "settings") { location.hash = "#/preferences"; return true; }
    if (name === "stop") {
      if (state.sessionId) await api(`${sessionPath(state.projectId, state.sessionId)}/abort`, { method: "POST", body: {}, silent: true });
      composer.value = "";
      await refreshSelected();
      return true;
    }
    return false;
  }

  function applyMessageEvent(projectId, update) {
    const info = update.type === "message.updated" ? update.properties?.info : null;
    if (!info?.id || !info.sessionID || projectId !== state.projectId || info.sessionID !== state.sessionId) return;
    const message = state.messages.find(item => item.info?.id === info.id);
    if (message) message.info = {...message.info, ...info};
    else state.messages.push({info, parts: []});
    messageVersion++;
    messageInfoVersions.set(info.id, messageVersion);
  }

  function observeSessionStatus(projectId, sessionId, previous, current) {
    const key = workspaceConversationKey(projectId, sessionId);
    const wasRunning = previous && previous !== "idle";
    const running = current && current !== "idle";
    if (running && !wasRunning) {
      const old = completionWatches.get(key);
      if (old?.timer) clearTimeout(old.timer);
      // Allow for a small delay between OpenCode's timestamp and SSE delivery.
      completionWatches.set(key, { startedAt: Date.now() - 2000, timer: null });
    } else if (wasRunning && !running) {
      const watch = completionWatches.get(key);
      if (!watch) return;
      if (document.hasFocus()) { completionWatches.delete(key); return; }
      watch.timer = setTimeout(() => { void confirmAnswerComplete(projectId, sessionId, watch); }, 1200);
    }
  }

  function observeProjectStatuses(projectId, previous, current) {
    for (const sessionId of new Set([...Object.keys(previous || {}), ...Object.keys(current || {})])) {
      observeSessionStatus(projectId, sessionId, previous?.[sessionId]?.type, current?.[sessionId]?.type);
    }
  }

  async function confirmAnswerComplete(projectId, sessionId, watch) {
    const key = workspaceConversationKey(projectId, sessionId);
    if (!alive() || completionWatches.get(key) !== watch) return;
    completionWatches.delete(key);
    try {
      const base = sessionPath(projectId, sessionId);
      const [statuses, queue, messages] = await Promise.all([
        api(`workspace/projects/${encodeURIComponent(projectId)}/status`, { silent: true }),
        api(`${base}/queue`, { silent: true }),
        api(`${base}/messages`, { silent: true }),
      ]);
      if (!alive() || completionWatches.has(key) || document.hasFocus() ||
          (statuses?.[sessionId]?.type && statuses[sessionId].type !== "idle") ||
          queue?.items?.length || !Array.isArray(messages)) return;
      const lastAssistant = messages.filter((message) => message.info?.role === "assistant").at(-1)?.info;
      if (!lastAssistant?.time?.completed || lastAssistant.error ||
          timestamp(lastAssistant.time.completed) < watch.startedAt) return;
      const project = state.projects.find((item) => item.id === projectId);
      await workspaceNotifyAnswerComplete(`${project?.name || "工作区"}的 AI 回复已完成`);
    } catch (_) { /* Missing completion details should not produce a notification. */ }
  }

  function connectEvents(projectId) {
    if (eventProjectId === projectId) return;
    if (events) events.close();
    events = null;
    eventProjectId = projectId;
    if (!projectId || typeof EventSource === "undefined") return;
    events = new EventSource(`api/workspace/projects/${encodeURIComponent(projectId)}/events`);
    events.onmessage = (event) => {
      if (!alive() || state.projectId !== projectId) return;
      try {
        const update = JSON.parse(event.data);
        const properties = update.properties || {};
        const sessionId = properties.sessionID || properties.info?.sessionID || properties.part?.sessionID ||
          (update.type === "session.updated" ? properties.info?.id : null);
        if ((!sessionId || sessionId === state.sessionId) && !["server.heartbeat", "server.connected"].includes(update.type)) scheduleRefresh();
        applyMessageEvent(projectId, update);
        if (update.type === "todo.updated" && sessionId === state.sessionId && Array.isArray(properties.todos)) {
          state.todoVersion = (state.todoVersion || 0) + 1;
          state.todos = properties.todos;
          scheduleSelectedRender();
        }
        if (update.type === "session.status" && update.properties?.sessionID && update.properties?.status) {
          const before = state.projectStatuses.get(projectId) || {};
          const previous = before[update.properties.sessionID]?.type;
          const statuses = { ...before,
            [update.properties.sessionID]: update.properties.status };
          observeSessionStatus(projectId, update.properties.sessionID, previous, update.properties.status.type);
          state.projectStatuses.set(projectId, statuses);
          statusVersions.set(projectId, (statusVersions.get(projectId) || 0) + 1);
          state.statuses = statuses;
          if (previous !== update.properties.status.type) {
            renderSidebar(); renderHeader();
            if (update.properties.sessionID === state.sessionId) renderMain();
          }
        }
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
    refreshTimer = setTimeout(() => { refreshTimer = null; refreshSelected(); }, 600);
  }

  function scheduleSelectedRender() {
    if (renderTimer) return;
    renderTimer = setTimeout(() => {
      renderTimer = null;
      if (!alive()) return;
      renderMain(scrollToLatestOnLoad);
      if (state.messagesLoaded) scrollToLatestOnLoad = false;
      renderHeader();
    }, 16);
  }

  function cancelSelectedRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    if (renderTimer) clearTimeout(renderTimer);
    refreshTimer = null;
    renderTimer = null;
    selectedRefresh?.controller.abort();
    selectedRefresh = null;
  }

  async function refreshWorkspaceStatuses() {
    if (!alive() || statusRefreshing) return;
    statusRefreshing = true;
    const versions = new Map(statusVersions);
    try {
      const result = await api("workspace/status", { silent: true });
      if (!alive()) return;
      let changed = false;
      let selectedChanged = false;
      for (const [projectId, entry] of Object.entries(result.projects || {})) {
        if (entry.error) continue;
        if (versions.get(projectId) !== statusVersions.get(projectId)) continue;
        const statuses = entry.statuses || {};
        observeProjectStatuses(projectId, state.projectStatuses.get(projectId), statuses);
        if (JSON.stringify(state.projectStatuses.get(projectId)) !== JSON.stringify(statuses)) {
          changed = true;
          if (projectId === state.projectId) selectedChanged =
            JSON.stringify(state.statuses?.[state.sessionId]) !== JSON.stringify(statuses[state.sessionId]);
        }
        state.projectStatuses.set(projectId, statuses);
        if (projectId === state.projectId) state.statuses = statuses;
      }
      if (changed) renderSidebar();
      renderHeader();
      if (selectedChanged) renderMain();
    } catch (_) { /* Keep the last known states until the next poll succeeds. */ }
    finally { statusRefreshing = false; }
  }

  async function refreshSelected() {
    if (!alive() || !state.projectId || !state.sessionId) return;
    if (selectedRefresh) { selectedRefresh.pending = true; return; }
    const refresh = { controller: new AbortController(), pending: false };
    selectedRefresh = refresh;
    lastSelectedRefresh = Date.now();
    const projectId = state.projectId;
    const sessionId = state.sessionId;
    const base = sessionPath(projectId, sessionId);
    const queueVersion = queueUpdateVersion;
    const statusVersion = statusVersions.get(projectId);
    const messagesVersion = messageVersion;
    const currentTodoVersion = state.todoVersion || 0;
    const current = () => alive() && selectedRefresh === refresh && state.projectId === projectId && state.sessionId === sessionId;
    // Apply each response independently so a slow diff/queue cannot delay messages.
    const read = async (path, apply, failed = null) => {
      try {
        const value = await api(path, { silent: true, signal: refresh.controller.signal });
        if (!current()) return;
        if (apply(value) !== false) scheduleSelectedRender();
      } catch (error) {
        if (current() && error.name !== "AbortError" && failed) {
          failed(error);
          scheduleSelectedRender();
        }
      }
    };
    try {
      const messagesRead = read(`${base}/messages`, value => {
        const messages = Array.isArray(value) ? value : [];
        if (messagesVersion !== messageVersion) {
          const live = new Map(state.messages.map(message => [message.info?.id, message]));
          const received = new Set(messages.map(message => message.info?.id));
          for (const message of messages) {
            if ((messageInfoVersions.get(message.info?.id) || 0) > messagesVersion && live.has(message.info?.id))
              message.info = { ...message.info, ...live.get(message.info.id).info };
          }
          for (const message of state.messages) {
            if (!received.has(message.info?.id) && (messageInfoVersions.get(message.info?.id) || 0) > messagesVersion) messages.push(message);
          }
        }
        state.messages = messages;
        state.messagesLoaded = true;
        state.messageLoadError = "";
      }, error => { state.messageLoadError = detail(error); state.messagesLoaded = true; });
      const queueRead = read(`${base}/queue`, value => {
        if (queueVersion === queueUpdateVersion) { state.queue = value; state.queueLoaded = true; }
        renderHeader();
        return false;
      });
      // Leave browser connections available for the sidebar and conversation.
      // A slow queue or diff must never delay displaying message history.
      await messagesRead;
      if (!current()) return;
      await Promise.allSettled([
        queueRead,
        read(`workspace/projects/${encodeURIComponent(projectId)}/status`, value => {
          if (statusVersion !== statusVersions.get(projectId)) return false;
          const selectedChanged = JSON.stringify(state.statuses?.[sessionId]) !== JSON.stringify(value?.[sessionId]);
          observeProjectStatuses(projectId, state.projectStatuses.get(projectId), value);
          state.statuses = value || {};
          const changed = JSON.stringify(state.projectStatuses.get(projectId)) !== JSON.stringify(state.statuses);
          state.projectStatuses.set(projectId, state.statuses);
          statusVersions.set(projectId, (statusVersions.get(projectId) || 0) + 1);
          if (changed) renderSidebar();
          return selectedChanged;
        }),
        read(`workspace/projects/${encodeURIComponent(projectId)}/permissions`, value => {
          const permissions = Array.isArray(value) ? value : [];
          const changed = JSON.stringify(state.permissions) !== JSON.stringify(permissions);
          state.permissions = permissions;
          return changed;
        }),
        read(`workspace/projects/${encodeURIComponent(projectId)}/questions`, value => {
          const questions = Array.isArray(value) ? value : [];
          const changed = JSON.stringify(state.questions) !== JSON.stringify(questions);
          state.questions = questions;
          return changed;
        }),
        read(`${base}/diff`, value => {
          const diffs = Array.isArray(value) ? value : [];
          const changed = JSON.stringify(state.diffs) !== JSON.stringify(diffs);
          state.diffs = diffs;
          return changed;
        }),
        read(`${base}/todo`, value => {
          if (currentTodoVersion !== (state.todoVersion || 0)) return false;
          const todos = Array.isArray(value) ? value : [];
          if (JSON.stringify(state.todos) === JSON.stringify(todos)) return false;
          state.todos = todos;
        }),
        state.tab === "tasks" ? read(`${base}/children`, value => {
          state.children = Array.isArray(value) ? value : [];
          for (const child of state.children) state.sessionDetails.set(workspaceConversationKey(projectId, child.id), child);
        }) : null,
        read(base, value => {
          if (value?.id) state.sessionDetails.set(workspaceConversationKey(projectId, value.id), value);
          renderHeader();
          return false;
        }),
        ["trajectory", "activity"].includes(state.tab) ? read(`${base}/trajectory`, value => {
          state.recordingData = value; state.recordingError = "";
        }, error => { state.recordingError = detail(error); }) : null,
      ]);
    } finally {
      if (selectedRefresh === refresh) {
        selectedRefresh = null;
        if (refresh.pending) scheduleRefresh();
      }
    }
  }

  function saveConversationView() {
    if (!state.projectId || !state.sessionId || !state.messagesLoaded) return;
    const key = workspaceConversationKey(state.projectId, state.sessionId);
    conversationViews.delete(key);
    conversationViews.set(key, {
      messages: state.messages, diffs: state.diffs, todos: state.todos, children: state.children,
      permissions: state.permissions, questions: state.questions, queue: state.queue, queueLoaded: state.queueLoaded,
      nodes: state.tab === "chat" ? Array.from(content.childNodes) : [],
      scrollTop: state.tab === "chat" ? scroll.scrollTop : null,
    });
    while (conversationViews.size > 6) conversationViews.delete(conversationViews.keys().next().value);
  }

  function restoreConversationView() {
    const cached = conversationViews.get(workspaceConversationKey(state.projectId, state.sessionId));
    state.messages = cached?.messages || [];
    state.messagesLoaded = !!cached;
    state.messageLoadError = "";
    state.permissions = cached?.permissions || []; state.questions = cached?.questions || [];
    state.diffs = cached?.diffs || []; state.todos = cached?.todos || []; state.children = cached?.children || [];
    state.queue = cached?.queue || { items: [], paused: false, error: "" }; state.queueLoaded = cached?.queueLoaded || false;
    state.questionDrafts.clear(); state.questionPages.clear(); state.questionErrors.clear();
    state.actionError = "";
    messageInfoVersions.clear();
    messageVersion++;
    workspaceSyncChildren(content, state.tab === "chat" ? cached?.nodes || [] : []);
    if (state.tab === "chat" && cached?.scrollTop != null) {
      scroll.scrollTop = cached.scrollTop;
      scrollToLatestOnLoad = false;
    }
  }

  function selectProject(projectId) {
    if (tree.projectId && tree.projectId !== projectId) tree.close();
    saveDraft();
    saveConversationView();
    cancelSelectedRefresh();
    hideAutocomplete();
    scrollToLatestOnLoad = true;
    state.projectId = projectId;
    state.queue = { items: [], paused: false, error: "" }; state.queueLoaded = false;
    state.skills = []; state.commands = []; updateSkillInput();
    if (!state.chosenModels.has(projectId)) {
      try { state.chosenModels.set(projectId, localStorage.getItem(`sona-code:model:${projectId}`) || ""); }
      catch (_) { state.chosenModels.set(projectId, ""); }
    }
    const remembered = workspaceSelection.projectId === projectId ? workspaceSelection.sessionId : null;
    state.sessionId = remembered || (state.sessions.get(projectId) || [])[0]?.id || null;
    state.statuses = state.projectStatuses.get(projectId) || {};
    restoreDraft();
    closeChangePopover();
    state.selectedChange = null;
    state.recordingData = null; state.recordingError = ""; trajectoryView = null; trajectorySignature = "";
    state.tab = remembered ? workspaceSelection.tab || "chat" : "chat";
    restoreConversationView();
    workspaceSelection = { projectId, sessionId: state.sessionId, tab: state.tab };
    persistWorkspaceSelection();
    state.collapsedProjects.delete(projectId);
    root.classList.remove("show-side");
    updateSidebarButton();
    renderSidebar(); renderHeader(); renderMain();
    refreshSelected();
    if (!state.sessions.has(projectId)) {
      const project = activeProject();
      if (project) loadSessions(project);
    }
    connectEvents(projectId);
    loadModels(projectId);
    loadAgents(projectId);
  }

  function selectSession(projectId, sessionId) {
    if (draftContextReady && state.projectId === projectId && state.sessionId === sessionId) return;
    saveDraft();
    saveConversationView();
    cancelSelectedRefresh();
    hideAutocomplete();
    scrollToLatestOnLoad = true;
    const projectChanged = state.projectId !== projectId;
    if (projectChanged && tree.projectId) tree.close();
    state.projectId = projectId;
    state.queue = { items: [], paused: false, error: "" }; state.queueLoaded = false;
    if (projectChanged) { state.skills = []; state.commands = []; updateSkillInput(); }
    if (!state.chosenModels.has(projectId)) {
      try { state.chosenModels.set(projectId, localStorage.getItem(`sona-code:model:${projectId}`) || ""); }
      catch (_) { state.chosenModels.set(projectId, ""); }
    }
    state.sessionId = sessionId;
    state.statuses = state.projectStatuses.get(projectId) || {};
    restoreDraft();
    closeChangePopover();
    state.selectedChange = null;
    state.recordingData = null; state.recordingError = ""; trajectoryView = null; trajectorySignature = "";
    state.tab = "chat";
    restoreConversationView();
    workspaceSelection = { projectId, sessionId, tab: state.tab };
    persistWorkspaceSelection();
    state.collapsedProjects.delete(projectId);
    root.classList.remove("show-side");
    updateSidebarButton();
    renderSidebar(); renderHeader(); renderMain();
    refreshSelected();
    connectEvents(projectId);
    if (projectChanged) {
      loadModels(projectId);
      loadAgents(projectId);
    }
  }

  function applyLatestModel(projectId) {
    try {
      const value = localStorage.getItem("sona-code:last-model");
      if (value === null) return;
      if (value && projectId === state.projectId && state.providers.length) {
        const [providerId, modelId] = value.split("\u0000");
        if (!state.providers.some(provider => provider.id === providerId && provider.models?.[modelId])) return;
      }
      state.chosenModels.set(projectId, value);
      state.chosenVariants.set(projectId, "");
      localStorage.setItem("sona-code:model:" + projectId, value);
      updateModelButton();
    } catch (_) { /* Storage may be unavailable. */ }
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
      applyLatestModel(projectId);
      selectSession(projectId, session.id);
      input.focus();
    } catch (error) { toast("新建对话失败：" + detail(error), "error"); }
  }

  async function ensureSessionForSend() {
    const projectId = state.projectId;
    if (state.sessionId) return { projectId, sessionId: state.sessionId };
    const session = await api(`workspace/projects/${encodeURIComponent(projectId)}/sessions`, {
      method: "POST", body: {}, silent: true,
    });
    const items = state.sessions.get(projectId) || [];
    state.sessions.set(projectId, [session, ...items.filter((item) => item.id !== session.id)]);
    applyLatestModel(projectId);
    if (!alive() || state.projectId !== projectId || state.sessionId) return { projectId, sessionId: session.id };
    state.sessionId = session.id;
    state.queue = { items: [], paused: false, error: "" }; state.queueLoaded = true;
    workspaceSelection = { projectId: state.projectId, sessionId: session.id };
    persistWorkspaceSelection();
    connectEvents(state.projectId);
    renderSidebar(); renderHeader();
    return { projectId, sessionId: session.id };
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
    const directoryBrowser = el("div", { class: "wsp-directory-browser hidden", "aria-live": "polite" });
    let browseVersion = 0;
    let browseTimer;
    function navigateDirectory(path) {
      pathInput.value = path;
      scheduleBrowse(0);
    }
    function scheduleBrowse(delay = 200) {
      clearTimeout(browseTimer);
      const version = ++browseVersion;
      const path = pathInput.value.trim();
      directoryBrowser.classList.toggle("hidden", !path);
      directoryBrowser.replaceChildren();
      if (!path) return;
      directoryBrowser.append(el("div", { class: "wsp-directory-status", text: "正在读取目录…" }));
      browseTimer = setTimeout(() => loadDirectories(path, version), delay);
    }
    async function loadDirectories(path, version) {
      const current = () => mask.isConnected && version === browseVersion;
      const read = (value) => api("terminal/fs?path=" + encodeURIComponent(value), { silent: true });
      try {
        let result;
        let prefix = "";
        try { result = await read(path); }
        catch (error) {
          if (!current()) return;
          // A partially typed basename can still browse its existing parent.
          const separator = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
          if (separator < 0 || separator === path.length - 1) throw error;
          const parent = path.slice(0, separator + 1);
          prefix = path.slice(separator + 1).toLocaleLowerCase();
          result = await read(parent);
        }
        if (!current()) return;
        const heading = el("div", { class: "wsp-directory-heading" },
          el("span", { text: result.path, title: result.path }));
        if (result.parent) heading.append(el("button", { class: "wsp-mini", type: "button", text: "↑ 上一级",
          onclick: () => navigateDirectory(result.parent) }));
        if (prefix) heading.append(el("button", { class: "wsp-mini", type: "button", text: "使用此目录",
          onclick: () => navigateDirectory(result.path) }));
        const list = el("div", { class: "wsp-directory-list" });
        const entries = result.entries.filter((entry) => entry.name.toLocaleLowerCase().startsWith(prefix));
        for (const entry of entries) list.append(el("button", { class: "wsp-directory-item", type: "button",
          title: entry.path, onclick: () => navigateDirectory(entry.path) },
          workspaceIcon("folder"), el("span", { text: entry.name })));
        if (!entries.length) list.append(el("div", { class: "wsp-directory-status", text: prefix ? "没有匹配的子目录" : "此目录下没有子目录" }));
        directoryBrowser.replaceChildren(heading, list);
      } catch (error) {
        if (current()) directoryBrowser.replaceChildren(el("div", { class: "wsp-directory-status", text: "无法浏览目录：" + detail(error) }));
      }
    }
    pathInput.addEventListener("input", () => scheduleBrowse());
    const choose = el("button", { class: "wsp-mini", type: "button", text: "选择目录…", onclick: async () => {
      const dialog = window.__TAURI__?.dialog;
      if (!dialog?.open) { pathInput.focus(); return; }
      try {
        const selected = await dialog.open({ directory: true, multiple: false, title: "选择项目目录" });
        if (selected && mask.isConnected) navigateDirectory(selected);
      } catch (error) { errorLine.textContent = detail(error); errorLine.classList.remove("hidden"); }
    } });
    if (!window.__TAURI__?.dialog?.open) choose.hidden = true;
    const mask = el("div", { class: "wsp-modal-mask" },
      el("div", { class: "wsp-modal", role: "dialog", "aria-modal": "true", "aria-label": "新建项目" }, el("h2", { text: "新建项目" }),
        el("p", { text: "输入现有目录的绝对路径，或选择一个目录作为 Sona Code 工作区。" }),
        el("div", { class: "wsp-modal-row" }, pathInput, choose), directoryBrowser, errorLine,
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
    if (!await confirmDeletion("删除工作区项目", `确定从工作区列表删除项目“${project.name}”？项目目录和 Sona Code 对话仍保留在磁盘上。`)) return;
    try {
      await api(`workspace/projects/${encodeURIComponent(project.id)}`, { method: "DELETE", silent: true });
      state.projects = state.projects.filter((item) => item.id !== project.id);
      state.sessions.delete(project.id);
      state.errors.delete(project.id);
      for (const key of conversationViews.keys()) {
        if (JSON.parse(key)[0] === project.id) conversationViews.delete(key);
      }
      if (state.projectId === project.id) {
        cancelSelectedRefresh();
        saveDraft();
        state.projectId = null; state.sessionId = null; state.messages = [];
        draftContextReady = false;
        state.selectedChange = null;
        closeChangePopover();
        workspaceSelection = { projectId: null, sessionId: null };
        persistWorkspaceSelection();
        connectEvents(null);
        if (state.projects.length) selectProject(state.projects[0].id);
        else { restoreDraft(); renderSidebar(); renderHeader(); renderMain(); }
      } else renderSidebar();
    } catch (error) { toast("删除工作区失败：" + detail(error), "error"); }
  }

  view.querySelector("#wsp-new").addEventListener("click", openAddProject);
  const outsideChanges = event => {
    if (!event.target.closest(".wsp-change-summary, .wsp-change-popover")) closeChangePopover();
  };
  const changeKeydown = event => {
    if (event.key === "Escape" && changePopover) { event.preventDefault(); closeChangePopover(true); }
  };
  document.addEventListener("pointerdown", outsideChanges);
  document.addEventListener("keydown", changeKeydown);
  window.addEventListener("resize", positionChangePopover);
  scroll.addEventListener("scroll", positionChangePopover, { passive: true });
  addCleanup(() => {
    closeChangePopover();
    document.removeEventListener("pointerdown", outsideChanges);
    document.removeEventListener("keydown", changeKeydown);
    window.removeEventListener("resize", positionChangePopover);
  });
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
  view.querySelector("#wsp-model-refresh").addEventListener("click", refreshSonaModels);
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
    closeChangePopover();
    if (button.dataset.wspTab === "changes") state.selectedChange = null;
    state.tab = button.dataset.wspTab;
    workspaceSelection.tab = state.tab;
    scroll.scrollTop = 0;
    renderHeader(); renderMain(state.tab === "chat");
    if (state.tab !== "chat") refreshSelected();
  }));
  todoTrigger.addEventListener("click", () => {
    const key = workspaceConversationKey(state.projectId, state.sessionId);
    if (dismissedTodoPanels.has(key)) dismissedTodoPanels.delete(key);
    else dismissedTodoPanels.add(key);
    renderTodoPanel();
  });
  view.querySelector("#wsp-todo-panel-close").addEventListener("click", () => {
    dismissedTodoPanels.add(workspaceConversationKey(state.projectId, state.sessionId));
    renderTodoPanel();
    todoTrigger.focus();
  });
  view.querySelector("#wsp-todo-panel-link").addEventListener("click", () => {
    view.querySelector('[data-wsp-tab="tasks"]').click();
  });
  view.querySelector("#wsp-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (consumeMenuCommand()) return;
    state.fileReferences = composer.fileReferences;
    const text = composer.value.trim();
    if ((!text && !state.attachments.length && !state.fileReferences.length) || state.sending) return;
    if (state.pendingImageCount) { toast("图片正在读取，请稍后发送", "error"); return; }
    if (!state.projectId) { openAddProject(); return; }
    if (!state.check?.found) { toast("未找到 Sona Code，请在设置中配置程序路径", "error"); return; }
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
    if (shell && (waitingForReply() || state.queue.items.length)) {
      toast("请等待当前任务和消息队列结束后运行终端命令", "error"); return;
    }
    let model = selectedModel();
    const variant = variantSelect.value ? { variant: variantSelect.value } : {};
    const agent = agentSelect.value || "build";
    const files = state.attachments.map(({ filename, mime, url }) => ({ filename, mime, url }));
    const references = state.fileReferences.map(({ path }) => ({ path }));
    const sendKey = workspaceConversationKey(state.projectId, state.sessionId);
    let operationKey = sendKey;
    const originalDraft = { ...composer.snapshot(), attachments: state.attachments.map(item => ({ ...item })) };
    saveDraft();
    sendingConversations.add(sendKey); renderHeader();
    hideAutocomplete();
    state.actionError = "";
    try {
      if (slash && await executeBuiltIn(slash[1])) return;
      if (!shell) void workspacePrepareNotifications();
      const { projectId, sessionId } = await ensureSessionForSend();
      model = selectedModel();
      operationKey = workspaceConversationKey(projectId, sessionId);
      sendingConversations.add(operationKey);
      const base = sessionPath(projectId, sessionId);
      if (shell) {
        pendingActions.set(workspaceConversationKey(projectId, sessionId), `!${shell}`);
        state.actionError = "";
        renderMain();
      }
      if (slash) {
        const queue = await api(`${base}/queue`, {
          method: "POST", body: { kind: "command", payload: { command: slash[1], arguments: slash[2] || "", agent, ...model, ...variant } }, silent: true,
        });
        applyQueue(queue, projectId, sessionId);
      } else if (shell) {
        await api(`${base}/shell`, {
          method: "POST", body: { command: shell, agent, ...model }, silent: true,
        });
      } else {
        const queue = await api(`${base}/queue`, {
          method: "POST", body: { kind: "prompt", payload: { text, files, references, agent, ...model, ...variant } }, silent: true,
        });
        applyQueue(queue, projectId, sessionId);
      }
      if (!alive()) return;
      const deliveredKey = workspaceConversationKey(projectId, sessionId);
      dismissedTodoPanels.add(deliveredKey);
      renderTodoPanel();
      const saved = workspaceReadDraft(sendKey);
      if (JSON.stringify(saved) === JSON.stringify(originalDraft)) workspaceWriteDraft(sendKey, null);
      if (sendKey !== deliveredKey && JSON.stringify(workspaceReadDraft(deliveredKey)) === JSON.stringify(originalDraft)) {
        workspaceWriteDraft(deliveredKey, null);
      }
      if (state.projectId === projectId && state.sessionId === sessionId) {
        if (composer.value.trim() === text) composer.value = "";
        updateSkillInput();
        state.attachments = [];
        state.fileReferences = composer.fileReferences;
        renderAttachments();
        saveDraft();
      }
      refreshWorkspaceStatuses();
      await loadSessions(state.projects.find((project) => project.id === projectId));
      await refreshSelected();
    } catch (error) {
      const failure = `操作失败：${detail(error)}`;
      if (workspaceConversationKey(state.projectId, state.sessionId) === sendKey) state.actionError = failure;
      toast(failure, "error");
    } finally {
      pendingActions.delete(operationKey);
      sendingConversations.delete(operationKey);
      sendingConversations.delete(sendKey);
      if (alive()) { updateSkillInput(); renderHeader(); renderMain(); }
    }
  });
  function consumeMenuCommand() {
    if (composingInput || !handleMenuCommand(composer.value)) return false;
    composer.value = "";
    updateSkillInput();
    hideAutocomplete();
    return true;
  }

  input.addEventListener("input", () => { consumeMenuCommand(); updateSkillInput(); commandPaletteOpen = false; state.commandSelectedIndex = 0; renderCommandMenu(); scheduleDraftSave(); });
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
  input.addEventListener("compositionend", () => { composingInput = false; consumeMenuCommand(); updateSkillInput(); renderCommandMenu(); scheduleDraftSave(); });
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
    if (!alive()) return;
    refreshWorkspaceStatuses();
    if (!state.messagesLoaded || waitingForReply() || events?.readyState !== 1 || Date.now() - lastSelectedRefresh >= 15000)
      refreshSelected();
    if (Date.now() - lastSessionListRefresh > 15000) {
      const project = activeProject();
      if (project) loadSessions(project);
    }
  }, 2500);
  window.addEventListener("pagehide", saveDraft);
  addCleanup(() => window.removeEventListener("pagehide", saveDraft));
  const reloadSonaModels = () => {
    workspaceModelCache.clear();
    if (alive() && state.projectId) loadModels(state.projectId);
  };
  window.addEventListener("sona-models-changed", reloadSonaModels);
  addCleanup(() => window.removeEventListener("sona-models-changed", reloadSonaModels));
  watchSonaStartupRefresh(reloadSonaModels, alive);
  updateModelButton();
  updateSidebarButton();

  async function loadCheck() {
    if (checkLoading) return checkLoading;
    checkLoading = (async () => {
      try {
        const check = await api("workspace/check", { silent: true });
        if (alive()) { state.check = check; renderHeader(); }
      } catch (_) { /* Project browsing remains available when detection fails. */ }
    })();
    try { await checkLoading; }
    finally { checkLoading = null; }
  }

  async function loadProjects() {
    if (projectsLoading) return projectsLoading;
    projectsLoading = fetchProjects();
    try { await projectsLoading; }
    finally { projectsLoading = null; }
  }

  async function fetchProjects() {
    try {
      const projects = await api("workspace/projects", { silent: true });
      if (!alive()) return;
      const previousIds = new Set(state.projects.map(project => project.id));
      state.projects = workspaceOrderItems(projects.items || [], "sona-code:project-order");
      for (const project of state.projects) if (!previousIds.has(project.id)) state.collapsedProjects.add(project.id);
      refreshWorkspaceStatuses();
      const selected = state.projects.find((item) => item.id === state.projectId) || state.projects[0];
      renderSidebar();
      if (selected && (!draftContextReady || selected.id !== state.projectId)) selectProject(selected.id);
      else if (selected) loadSessions(selected);
      else { renderHeader(); renderMain(); }
    } catch (error) {
      if (alive() && !state.projects.length) content.replaceChildren(el("div", { class: "wsp-error", text: "加载工作区失败：" + detail(error) }));
    }
  }

  loadProjects();
  loadCheck();
  return {
    suspend() {
      if (disposed || suspended) return;
      saveDraft();
      suspended = true;
      cancelSelectedRefresh();
      if (events) events.close();
      events = null; eventProjectId = null;
      hideAutocomplete(); closeRowMenus(); closeChangePopover();
      closeModelPicker(); closeAgentPicker(); closeVariantPicker();
      queueDialog?.close(); deleteDialog?.close();
    },
    resume() {
      if (disposed || !suspended) return;
      suspended = false;
      // Retain DOM, drafts, expanded projects and scroll positions; refresh in place.
      updateTrajectoryHeight();
      loadProjects(); loadCheck();
      if (state.projectId) {
        refreshSelected();
        connectEvents(state.projectId);
        workspaceModelCache.delete(state.projectId);
        loadModels(state.projectId); loadAgents(state.projectId);
      }
    },
    dispose() {
      for (const cleanup of cleanups.splice(0)) { try { cleanup(); } catch (_) {} }
    },
  };
}
