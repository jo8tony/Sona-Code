"""Conversation selection, list refresh races and safe catalog replacement."""

from types import SimpleNamespace

import httpx
import pytest

from sona_code.workspace.manager import WorkspaceManager
from tests.test_workspace_interactions import run_node


def test_session_models_are_independent_and_new_sessions_inherit_last_manual_choice():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const stored = new Map();
const state = {projectId: "p", sessionId: "a", providers: [], chosenModels: new Map(), chosenVariants: new Map()};
const context = vm.createContext({state, localStorage: {
  getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value)},
  updateModelButton() {}, renderStatsLine() {}, closeModelPicker() {}, input: {focus() {}}});
vm.runInContext(source, context);
vm.runInContext(source.slice(source.indexOf("  function restoreModelSelection("), source.indexOf("  function updateModelButton(")), context);
vm.runInContext(source.slice(source.indexOf("  function chooseModel("), source.indexOf("  function renderModelPicker(")), context);
vm.runInContext(source.slice(source.indexOf("  function applyLatestModel("), source.indexOf("  function migrateCreationDraft(")), context);
const key = (p,s) => JSON.stringify([p,s]);
const select = (p,s) => {state.projectId = p; state.sessionId = s; context.restoreModelSelection();};
select("p", "a"); context.chooseModel("provider\u0000first");
state.chosenVariants.set(key("p","a"), "high");
select("p", "b"); context.chooseModel("provider\u0000second");
select("p", "a"); assert.equal(context.selectedModel().model_id, "first");
assert.equal(state.chosenVariants.get(key("p","a")), "high");
assert.equal(stored.get("sona-code:last-model"), "provider\u0000second", "Navigating must not change the last manual choice");
context.applyLatestModel("p", "creating:new"); select("p", "creating:new");
assert.equal(context.selectedModel().model_id, "second");
context.chooseModel("provider\u0000third");
context.migrateModelSelection(key("p","creating:new"), key("p","native-new"));
select("p", "native-new"); assert.equal(context.selectedModel().model_id, "third");
select("p", "b"); assert.equal(context.selectedModel().model_id, "second");
context.applyLatestModel("other", "new"); select("other", "new");
assert.equal(context.selectedModel().model_id, "third");
state.chosenModels.clear();
select("p", "a"); assert.equal(context.selectedModel().model_id, "first", "Reload restores each session's persisted choice");
select("p", "b"); assert.equal(context.selectedModel().model_id, "second");
context.chooseModel(""); context.applyLatestModel("chat", null); select("chat", null);
assert.equal(Object.keys(context.selectedModel()).length, 0, "Choosing default also updates the new-session default");
''')


def test_chat_list_snapshots_cannot_erase_newer_sessions_and_missing_catalog_recovers():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const requests = [], loads = [];
const chat = {id: "chat", name: "Chats", sessions: []};
const state = {projectId: "p", sessionId: "s", projects: [{id: "p"}, chat], sessions: new Map(),
  errors: new Map(), collapsedProjects: new Set()};
const context = vm.createContext({state, draftContextReady: true, WORKSPACE_CHAT_ID: "chat",
  api: path => new Promise(resolve => requests.push({path, resolve})), alive: () => true,
  workspaceOrderItems: items => items, renderSidebar() {}, renderHeader() {}, renderMain() {},
  refreshWorkspaceStatuses() {}, detail: error => error.message,
  loadSessions: project => loads.push(project.id),
  storeSessions(project, items) {state.sessions.set(project.id, items);},
});
vm.runInContext(source.slice(source.indexOf("  async function fetchProjects("), source.indexOf("\n  loadProjects();", source.indexOf("  async function fetchProjects("))), context);
(async () => {
  const startup = context.loadChatScope();
  const live = [{id: "old"}, {id: "new"}]; state.sessions.set("chat", live);
  requests[0].resolve(chat); await startup;
  assert.equal(state.sessions.get("chat"), live, "Delayed startup catalog cannot erase native or locally created sessions");
  const refresh = context.fetchProjects();
  requests[1].resolve({items: [{id: "p", sessions: []}]}); await refresh;
  assert.equal(state.sessions.get("chat"), live, "Refreshing projects must not reapply cached chat.sessions");
  const empty = context.loadChatScope(); requests[2].resolve(chat); await empty;
  assert.equal(state.sessions.get("chat"), live, "An empty cold snapshot must not hide known conversations");
  state.projectId = "chat";
  const missing = context.loadChatScope(); requests[3].resolve({...chat, sessions: null}); await missing;
  assert(loads.includes("chat"), "Missing/empty snapshots recover from the session API without creating a new conversation");
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_inflight_session_list_cannot_remove_a_session_created_after_request():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const state = {sessions: new Map([["chat", [{id: "old"}]]])};
let resolve;
const context = vm.createContext({state, api: () => new Promise(done => {resolve = done;}), alive: () => true,
  storeSessions() {throw Error("Stale list must not replace the new conversation");}});
vm.runInContext(source.slice(source.indexOf("  async function fetchSessions("), source.indexOf("  function storeSessions(")), context);
(async () => {
  const pending = context.fetchSessions({id: "chat"});
  const live = [{id: "new"}, {id: "old"}]; state.sessions.set("chat", live);
  resolve({items: []}); await pending;
  assert.equal(state.sessions.get("chat"), live);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_fork_requires_idle_session_and_final_reply_but_allows_other_completed_sessions():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const key = (p,s) => JSON.stringify([p,s]);
const user = {info: {role: "user"}}, reply = {info: {role: "assistant", time: {completed: 1}, finish: "stop"}};
const state = {projectId: "p", sessionId: "current", projectStatuses: new Map([["p", {current: {type: "busy"}}]]),
  messagesLoaded: true, messages: [user], queue: {items: []}};
const sendingConversations = new Set();
const context = vm.createContext({state, workspaceConversationKey: key, sendingConversations,
  conversationViews: new Map([[key("p","old"), {messages: [user, reply]}]]), waitingForReply: () => false});
vm.runInContext(source.slice(source.indexOf("  function sessionCanFork("), source.indexOf("  function messageActions(")), context);
assert.equal(context.sessionCanFork("p", "current"), false);
assert.equal(context.sessionCanFork("p", "old"), true, "Another running session does not prevent forking a completed one");
state.projectStatuses.clear(); assert.equal(context.sessionCanFork("p", "current"), false);
state.messages.push({...reply, info: {...reply.info, finish: "tool-calls"}});
assert.equal(context.sessionCanFork("p", "current"), false, "Intermediate completed tool steps do not complete the turn");
state.messages.push(reply); assert.equal(context.sessionCanFork("p", "current"), true);
sendingConversations.add(key("p","current")); assert.equal(context.sessionCanFork("p", "current"), false);
sendingConversations.clear(); state.queue.items.push({status: "pending"});
assert.equal(context.sessionCanFork("p", "current"), false);
''')


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["busy", "retry", "unavailable"])
async def test_catalog_refresh_keeps_busy_servers_and_recycles_after_idle(tmp_path, status):
    manager = WorkspaceManager()
    path = str(tmp_path.resolve())
    closed = []
    current = status

    def respond(request):
        if current == "unavailable":
            return httpx.Response(503)
        return httpx.Response(200, json={} if current == "idle" else {"s": {"type": current}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="http://native.test")
    server = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), client=client)
    manager._servers[path] = server

    async def close(instance):
        closed.append(instance)

    manager._close_server = close
    updates = []

    async def apply():
        updates.append("new catalog")
        return {"ok": True}

    try:
        async with manager.task_dispatch(path):
            assert await manager.update_catalog(apply) == {"ok": True}
            assert updates == ["new catalog"]
            assert not closed
        async with manager.task_dispatch(path):
            assert not closed and manager._servers[path] is server
        current = "idle"
        async with manager.task_dispatch(path):
            assert closed == [server]
            assert path not in manager._servers
            assert path not in manager._stale_catalog_servers
        async with manager.task_dispatch(path):
            assert closed == [server], "Recycle only once, before the next task"
    finally:
        await client.aclose()
