"""Completed background conversations retain unread markers until viewed."""

import shutil
import subprocess
from pathlib import Path

import pytest


def run_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace completion coverage")
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_background_completion_read_markers_and_confirmation_races():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const stored = new Map(), notices = [];
let focused = true, visible = true, queue = [], failed = false, completed = Date.now() + 1000, hold = null;
const key = (p, s) => JSON.stringify([p, s]);
const context = vm.createContext({Date, setTimeout: () => 1, clearTimeout() {},
  localStorage: {getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value)},
  document: {hasFocus: () => focused, visibilityState: "visible"}, alive: background => background || visible,
  state: {projectId: "p", sessionId: "shown", tab: "chat", projects: [{id: "p", name: "Project"}, {id: "other", name: "Other"}]},
  completionWatches: new Map(), renderSidebar() {}, sessionPath: (p,s) => `${p}/${s}`, timestamp: value => value,
  api(path) {
    if (path.endsWith("/status")) return Promise.resolve({});
    if (path.endsWith("/queue")) return Promise.resolve({items: queue});
    const result = [{info: {role: "assistant", time: {completed}, error: failed ? {} : null}}];
    if (hold) return new Promise(resolve => {hold.resolve = () => resolve(result);});
    return Promise.resolve(result);
  }, workspaceNotifyAnswerComplete: body => {if (!focused) notices.push(body);},
});
vm.runInContext(source.slice(0, source.indexOf("function workspaceRoundDuration(")), context);
vm.runInContext(source.slice(source.indexOf("  function unreadIndicator("), source.indexOf("  function connectEvents(")), context);
const unread = () => JSON.parse(stored.get("sona-code:unread-completions") || "[]");
function start(p, s) {context.observeSessionStatus(p, s, undefined, "busy");}
function finish(p, s) {context.observeSessionStatus(p, s, "busy", "idle"); return context.completionWatches.get(key(p,s));}
async function complete(p, s) {start(p,s); const watch = finish(p,s); if (watch) await context.confirmAnswerComplete(p,s,watch);}
(async () => {
  await complete("p", "background"); assert.deepEqual(unread(), [key("p", "background")]);
  assert.equal(notices.length, 0, "Focused app still marks other sessions unread without requesting OS attention");
  await complete("other", "background"); assert.equal(unread().length, 2, "Equal session IDs in projects are independent");
  context.markSessionRead("p", "background"); assert.deepEqual(unread(), [key("other", "background")]);
  await complete("p", "shown"); assert.equal(unread().length, 1, "Visible selected reply is read");
  focused = false; await complete("p", "shown"); assert.equal(unread().length, 2); assert.equal(notices.length, 1);
  focused = true; context.markVisibleSessionRead(); assert.deepEqual(unread(), [key("other", "background")]);
  visible = false; await complete("p", "shown"); assert.equal(unread().length, 2, "Settings page does not read a reply");
  visible = true; context.markVisibleSessionRead();
  queue = [{}]; await complete("p", "queued"); assert(!unread().includes(key("p", "queued")));
  queue = []; failed = true; await complete("p", "failed"); assert(!unread().includes(key("p", "failed")));
  failed = false; completed = 1; await complete("p", "old"); assert(!unread().includes(key("p", "old")));
  completed = Date.now() + 1000;
  // Opening a session while its completion details load must keep it read.
  hold = {}; start("p", "clicked"); const clicked = finish("p", "clicked");
  const pending = context.confirmAnswerComplete("p", "clicked", clicked);
  context.markSessionRead("p", "clicked"); hold.resolve(); hold = null; await pending;
  assert(!unread().includes(key("p", "clicked")));
  // A new run invalidates an in-flight completion from the previous run.
  hold = {}; start("p", "racing"); const old = finish("p", "racing");
  const racing = context.confirmAnswerComplete("p", "racing", old);
  start("p", "racing"); const next = context.completionWatches.get(key("p", "racing"));
  hold.resolve(); hold = null; await racing;
  assert(!unread().includes(key("p", "racing"))); assert.equal(context.completionWatches.get(key("p", "racing")), next);
  const reloaded = vm.createContext({localStorage: context.localStorage});
  vm.runInContext(source.slice(0, source.indexOf("function workspaceRoundDuration(")), reloaded);
  assert(vm.runInContext('workspaceUnreadCompletions.has(JSON.stringify(["other", "background"]))', reloaded));
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_taskbar_attention_is_independent_of_system_notification_permission():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8"), calls = [];
let focused = false;
const context = vm.createContext({document: {hasFocus: () => focused}, window: {__TAURI__: {
  core: {invoke: async command => calls.push(command)},
  notification: {isPermissionGranted: async () => false, sendNotification: () => {throw Error("Permission denied");}},
}}});
vm.runInContext(source, context);
(async () => {
  await context.workspaceNotifyAnswerComplete("Done"); assert.deepEqual(calls, ["request_task_attention"]);
  focused = true; await context.workspaceNotifyAnswerComplete("Done"); assert.equal(calls.length, 1);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_sidebar_unread_moves_between_collapsed_project_and_session():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
class Node {
  constructor(tag, attrs, children) {this.tag = tag; Object.assign(this, attrs); this.children = []; this.append(...children);}
  append(...nodes) {for (const node of nodes.filter(Boolean)) {if (node instanceof Node) node.parent = this; this.children.push(node);}}
  remove() {this.parent.children = this.parent.children.filter(node => node !== this);}
  setAttribute(name, value) {this[name] = value;}
}
const el = (tag, attrs, ...children) => new Node(tag, attrs, children);
const project = {id: "p", name: "Project", path: "path"}, sessions = Array.from({length: 9}, (_, i) => ({id: `s${i}`, title: `Session ${i}`}));
const state = {projects: [project], sessions: new Map([["p", sessions]]), collapsedProjects: new Set(["p"]),
  projectStatuses: new Map(), sessionLimits: new Map(), errors: new Map(), search: "", projectId: "p", sessionId: "s0"};
const sidebarSections = new Map(), sidebarRows = new Map(), sideList = el("div");
const context = vm.createContext({state, sidebarSections, sidebarRows, sideList, el, sidebarDrag: null,
  view: {querySelector: () => ({})}, closeRowMenus() {}, bindSidebarDrag() {}, rowMenu: () => el("div"),
  sessionTitle: s => s.title, stamp: () => "today", workspaceIcon: () => el("svg"), statusIcon: () => el("svg"),
  workspaceSyncChildren(parent, children) {parent.children = []; parent.append(...children);},
});
vm.runInContext(source.slice(0, source.indexOf("function workspaceRoundDuration(")), context);
vm.runInContext(source.slice(source.indexOf("  function unreadIndicator("), source.indexOf("  function sessionIsVisible(")), context);
vm.runInContext(source.slice(source.indexOf("  function renderSidebar("), source.indexOf("  function renderHeader(")), context);
vm.runInContext('workspaceUnreadCompletions.add(workspaceConversationKey("p", "s8"))', context);
const dots = node => (node?.class === "wsp-unread-dot" ? 1 : 0) + (node?.children || []).reduce((n, child) => n + dots(child), 0);
const heading = () => sidebarSections.get("p").section.children[0].children[0];
context.renderSidebar(); assert.equal(dots(heading()), 1);
state.collapsedProjects.clear(); context.renderSidebar(); assert.equal(dots(heading()), 0);
assert.equal(dots(sidebarSections.get("p").threads.children.at(-1)), 1, "Truncated sessions must keep a visible indicator");
state.sessionLimits.set("p", 12); context.renderSidebar();
assert.equal(dots(sidebarRows.get(JSON.stringify(["p", "s8"])).button), 1);
vm.runInContext('workspaceMarkSessionRead("p", "s8")', context); context.renderSidebar();
assert.equal(dots(sideList), 0);
''')
