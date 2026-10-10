"""Workspace navigation retention, request priority and independent server startup."""

import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from sona_code.config import default_config
from sona_code.workspace import manager as workspace_manager


def run_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace loading coverage")
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True, timeout=10)


def test_router_retains_workspace_nodes_and_owns_settings_cleanup():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/app.js", "utf8");
let builds = 0, suspends = 0, resumes = 0, cleanups = 0;
const nodes = [];
const view = {hidden: false, replaceChildren(...children) {this.children = children;}, before(node) {nodes.push(node);}};
const context = vm.createContext({location: {hash: "#/workspace"},
  document: {body: {classList: {toggle() {}}}, querySelectorAll: () => []},
  $: () => view, el: (tag, attrs) => ({...attrs}), updateAdminMenus() {},
  renderWorkspace(node) {
    builds++; node.editor = {text: "草稿"}; node.scrollTop = 123;
    return {suspend() {suspends++;}, resume() {resumes++;}};
  },
  renderSettings() {context.addCleanup(() => cleanups++);},
});
vm.runInContext(source.slice(source.indexOf("let cleanups ="), source.indexOf("/* ============================================================ 仪表盘")), context);
context.route();
const workspace = nodes[0], editor = workspace.editor;
assert.equal(view.hidden, true);
context.location.hash = "#/settings"; context.route();
assert.equal(workspace.hidden, true); assert.equal(view.hidden, false);
assert.equal(cleanups, 0); assert.equal(suspends, 1);
context.location.hash = "#/workspace"; context.route();
assert.equal(cleanups, 1); assert.equal(resumes, 1); assert.equal(builds, 1);
assert.equal(workspace.hidden, false); assert.equal(workspace.editor, editor);
assert.equal(workspace.editor.text, "草稿"); assert.equal(workspace.scrollTop, 123);
context.route(); assert.equal(builds, 1);
''')


def test_projects_do_not_wait_for_detection_or_start_unopened_projects():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8").replace(/\r\n/g, "\n");
const requests = [], selected = [], sessionLoads = [];
const state = {projects: [], collapsedProjects: new Set(), projectId: "p", check: null};
const context = vm.createContext({state, projectsLoading: null, checkLoading: null, draftContextReady: false,
  api(path) {return new Promise(resolve => requests.push({path, resolve}));},
  alive: () => true, workspaceOrderItems: items => items,
  refreshWorkspaceStatuses() {}, renderSidebar() {}, renderHeader() {}, renderMain() {},
  selectProject(id) {selected.push(id);}, loadSessions(project) {sessionLoads.push(project.id);},
});
vm.runInContext(source.slice(source.indexOf("  async function loadCheck("), source.indexOf("  loadProjects();\n  loadCheck();")), context);
(async () => {
  const deadline = setTimeout(() => process.exit(1), 3000);
  const projects = context.loadProjects(), check = context.loadCheck();
  context.loadProjects(); context.loadCheck();
  assert.equal(requests.length, 2, "Duplicate loads must share their request");
  requests.find(r => r.path === "workspace/projects").resolve({items: [{id: "p"}, {id: "q"}, {id: "r"}]});
  await projects;
  assert.deepEqual(selected, ["p"]); assert.equal(state.check, null);
  assert.deepEqual(sessionLoads, [], "Collapsed projects must not start native servers");
  state.collapsedProjects.delete("q"); context.draftContextReady = true;
  const refresh = context.loadProjects();
  requests.at(-1).resolve({items: [{id: "p"}, {id: "q"}, {id: "r"}, {id: "new"}]});
  await refresh;
  assert.deepEqual(selected, ["p"], "Background refresh must preserve conversation selection");
  assert.equal(state.collapsedProjects.has("q"), false);
  assert.equal(state.collapsedProjects.has("new"), true);
  requests.find(r => r.path === "workspace/check").resolve({found: true}); await check;
  assert.equal(state.check.found, true); clearTimeout(deadline);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_session_list_requests_are_shared_per_project():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const requests = [];
const context = vm.createContext({sessionLoads: new Map(),
  fetchSessions(project) {return new Promise(resolve => requests.push({project, resolve}));},
});
vm.runInContext(source.slice(source.indexOf("  async function loadSessions("), source.indexOf("  async function fetchSessions(")), context);
(async () => {
  const a = context.loadSessions({id: "p"}), duplicate = context.loadSessions({id: "p"});
  const b = context.loadSessions({id: "q"});
  assert.equal(requests.length, 2);
  for (const request of requests) request.resolve(); await Promise.all([a, duplicate, b]);
  assert.equal(context.sessionLoads.size, 0);
  const next = context.loadSessions({id: "p"}); assert.equal(requests.length, 3);
  requests.at(-1).resolve(); await next;
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_project_catalog_hydrates_collapsed_counts_before_selecting_conversation():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8").replace(/\r\n/g, "\n");
const loaded = [], state = {projects: [], sessions: new Map(), sessionDetails: new Map(), errors: new Map(),
  collapsedProjects: new Set(), projectId: "empty"};
const projects = [
  {id: "empty", session_count: 0, sessions: []},
  {id: "one", session_count: 1, sessions: [{id: "ses_one", time: {updated: 1}}]},
];
const context = vm.createContext({state, projectsLoading: null, checkLoading: null, draftContextReady: false,
  api: async () => ({items: projects}), alive: () => true, workspaceOrderItems: items => items,
  workspaceConversationKey: (p,s) => p + ":" + s,
  refreshWorkspaceStatuses() {}, renderSidebar() {}, renderHeader() {}, renderMain() {},
  selectProject(id) {assert.equal(state.sessions.get("empty").length, 0);
    assert.equal(state.sessions.get("one")[0].id, "ses_one"); assert.equal(id, "empty");},
  loadSessions(project) {loaded.push(project.id);},
});
vm.runInContext(source.slice(source.indexOf("  function storeSessions("), source.indexOf("  function modelDisplayName(")), context);
vm.runInContext(source.slice(source.indexOf("  async function loadCheck("), source.indexOf("  loadProjects();\n  loadCheck();")), context);
(async () => {
  await context.loadProjects();
  assert.equal(state.collapsedProjects.has("one"), true);
  assert.equal(state.projects.find(p => p.id === "one").session_count, 1);
  assert.equal(state.sessionDetails.get("one:ses_one").id, "ses_one");
  assert.deepEqual(loaded, [], "Counts must not need a native startup for each collapsed project");
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_workspace_suspends_requests_and_resumes_without_resetting_view():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8").replace(/\r\n/g, "\n");
const calls = [], state = {projectId: "p", sessionId: "a", search: "query", messages: [{saved: true}]};
const methods = ["saveDraft", "cancelSelectedRefresh", "hideAutocomplete", "closeRowMenus", "closeChangePopover",
  "closeModelPicker", "closeAgentPicker", "closeVariantPicker", "updateTrajectoryHeight", "loadProjects",
  "loadCheck", "refreshSelected", "connectEvents", "loadModels", "loadAgents", "refreshSidebarAccount", "markVisibleSessionRead"];
const context = vm.createContext({state, disposed: false, suspended: false,
    navigation: {close() {}},
  events: {close() {calls.push("closeEvents");}}, eventProjectId: "p", queueDialog: null, deleteDialog: null,
  workspaceModelCache: new Map([["p", {data: {old: true}}]]), cleanups: [() => calls.push("dispose")],
  ...Object.fromEntries(methods.map(name => [name, () => calls.push(name)])),
});
const controller = vm.runInContext("(function() {" + source.slice(source.lastIndexOf("  return {\n    suspend()"), source.lastIndexOf("}")) + "})()", context);
controller.suspend(); controller.suspend();
assert.equal(context.suspended, true); assert.equal(context.events, null);
assert.equal(calls.filter(call => call === "closeEvents").length, 1);
assert.equal(calls.filter(call => call === "saveDraft").length, 1);
calls.length = 0; controller.resume(); controller.resume();
assert.equal(context.suspended, false);
assert.deepEqual(calls, ["markVisibleSessionRead", "updateTrajectoryHeight", "refreshSidebarAccount", "loadProjects", "loadCheck", "refreshSelected", "connectEvents", "loadModels", "loadAgents"]);
assert.equal(context.workspaceModelCache.has("p"), false, "Settings changes must invalidate cached models");
assert.equal(state.search, "query"); assert.equal(state.sessionId, "a"); assert.equal(state.messages[0].saved, true);
controller.dispose(); controller.dispose(); assert.equal(calls.filter(call => call === "dispose").length, 1);
''')


def test_idle_polling_uses_events_with_fallback_and_pauses_in_settings():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
let poll, clockTick, active = true, waiting = false, reads = 0, clockUpdates = 0;
const cleanups = [], cleared = [];
const context = vm.createContext({alive: () => active,
  setInterval(fn, delay) {if (delay === 2500) poll = fn; else clockTick = fn; return delay;},
  clearInterval: id => cleared.push(id), addCleanup: fn => cleanups.push(fn),
  renderRoundClock() {clockUpdates++;},
  refreshWorkspaceStatuses() {}, refreshSelected() {reads++;}, waitingForReply: () => waiting,
  state: {messagesLoaded: true}, events: {readyState: 1}, lastSelectedRefresh: Date.now(),
  lastSessionListRefresh: Date.now(), activeProject: () => null,
});
vm.runInContext(source.slice(source.indexOf("  const poll = setInterval("), source.indexOf('  window.addEventListener("pagehide"')), context);
poll(); assert.equal(reads, 0);
waiting = true; poll(); assert.equal(reads, 1);
waiting = false; context.events.readyState = 0; poll(); assert.equal(reads, 2);
context.events.readyState = 1; context.lastSelectedRefresh = Date.now() - 16000;
poll(); assert.equal(reads, 3);
clockTick(); assert.equal(clockUpdates, 1); assert.equal(reads, 3);
active = false; poll(); assert.equal(reads, 3);
clockTick(); assert.equal(clockUpdates, 1);
cleanups.forEach(fn => fn()); assert.deepEqual(cleared, [1000]);
''')


@pytest.fixture
def slow_startup(tmp_path, monkeypatch):
    manager = workspace_manager.WorkspaceManager()
    release = asyncio.Event()
    entered = asyncio.Queue()
    clients = []
    processes = []
    native_client = httpx.AsyncClient

    def process(*args, **kwargs):
        instance = SimpleNamespace(poll=lambda: None, stdout=None)
        processes.append(instance)
        return instance

    async def respond(request):
        if request.url.path == "/global/health":
            await entered.put(request.url.port)
            await release.wait()
        return httpx.Response(200, json={})

    def client(**kwargs):
        instance = native_client(**kwargs, transport=httpx.MockTransport(respond))
        clients.append(instance)
        return instance

    async def stop(process):
        process.poll = lambda: 0

    monkeypatch.setattr(workspace_manager, "resolve_opencode", lambda cfg: SimpleNamespace(path="opencode", source="path"))
    monkeypatch.setattr(workspace_manager, "resolve_executable", lambda path: path)
    monkeypatch.setattr(workspace_manager, "_build_env", lambda *args: {})
    monkeypatch.setattr(workspace_manager.subprocess, "Popen", process)
    monkeypatch.setattr(workspace_manager.httpx, "AsyncClient", client)
    monkeypatch.setattr(manager, "_stop_process", stop)
    paths = []
    for name in ("one", "two", "warm"):
        path = tmp_path / name
        path.mkdir()
        paths.append(str(path.resolve()))
    return SimpleNamespace(manager=manager, release=release, entered=entered, clients=clients,
                           processes=processes, paths=paths)


async def test_project_starts_are_parallel_deduplicated_and_survive_request_cancellation(slow_startup):
    fixture = slow_startup
    manager = fixture.manager
    config = default_config()
    one, two, warm = fixture.paths
    warm_server = SimpleNamespace(process=SimpleNamespace(poll=lambda: None))
    manager._servers[warm] = warm_server
    first = asyncio.create_task(manager.ensure(one, config))
    duplicate = asyncio.create_task(manager.ensure(one, config))
    second = asyncio.create_task(manager.ensure(two, config))
    try:
        await asyncio.wait_for(fixture.entered.get(), 2)
        await asyncio.wait_for(fixture.entered.get(), 2)
        assert len(fixture.processes) == 2
        assert await asyncio.wait_for(manager.ensure(warm, config), .5) is warm_server
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not duplicate.done()
        fixture.release.set()
        results = await asyncio.wait_for(asyncio.gather(duplicate, second), 2)
        assert await manager.ensure(one, config) is results[0]
        assert not manager._starting
    finally:
        fixture.release.set()
        manager._servers.pop(warm)
        await asyncio.gather(first, duplicate, second, return_exceptions=True)
        await manager.shutdown()


async def test_failed_start_is_removed_and_can_be_retried(slow_startup, monkeypatch):
    fixture = slow_startup
    monkeypatch.setattr(workspace_manager, "resolve_opencode", lambda cfg: SimpleNamespace(path=None))
    with pytest.raises(workspace_manager.WorkspaceError, match="未找到 OpenCode"):
        await fixture.manager.ensure(fixture.paths[0], default_config())
    assert not fixture.manager._starting
    monkeypatch.setattr(workspace_manager, "resolve_opencode", lambda cfg: SimpleNamespace(path="opencode"))
    fixture.release.set()
    try:
        server = await asyncio.wait_for(fixture.manager.ensure(fixture.paths[0], default_config()), 2)
        assert fixture.manager._servers[fixture.paths[0]] is server
    finally:
        await fixture.manager.shutdown()


@pytest.mark.parametrize("operation", ["stop", "shutdown", "configure"])
async def test_lifecycle_operations_wait_for_pending_starts(slow_startup, operation):
    fixture = slow_startup
    manager = fixture.manager
    startup = asyncio.create_task(manager.ensure(fixture.paths[0], default_config()))
    await asyncio.wait_for(fixture.entered.get(), 2)
    saved = []

    async def apply():
        saved.append(True)
        return {"ok": True}

    action = asyncio.create_task(manager.stop(fixture.paths[0]) if operation == "stop" else
                                 manager.shutdown() if operation == "shutdown" else
                                 manager.update_configuration(apply))
    try:
        await asyncio.sleep(.02)
        assert not action.done()
        assert not saved
        fixture.release.set()
        await asyncio.wait_for(asyncio.gather(startup, action), 2)
        assert not manager._starting and not manager._servers
        assert all(client.is_closed for client in fixture.clients)
        assert saved == ([True] if operation == "configure" else [])
    finally:
        fixture.release.set()
        await asyncio.gather(startup, action, return_exceptions=True)
        await manager.shutdown()
