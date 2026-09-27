"""Regression coverage for conversation DOM stability during refreshes."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_refresh_retains_unchanged_messages_and_updates_changed_rows():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace rendering coverage")
    script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync("llm_api_proxy_recorder/web/static/workspace.js", "utf8");
vm.runInThisContext(source);
vm.runInThisContext(source.slice(source.indexOf("  function renderMessages("), source.indexOf("  function panelIntro(")));
class Element {
  constructor(tag, props = {}) { this.tag = tag; Object.assign(this, props); this.childNodes = []; this.moves = 0; }
  get children() { return this.childNodes; }
  get lastChild() { return this.childNodes.at(-1); }
  append(...nodes) { for (const node of nodes) this.insertBefore(typeof node === "string" ? new Element("text", {text: node}) : node, null); }
  insertBefore(node, before) {
    if (node.parent) { node.moves++; node.remove(); }
    const index = before ? this.childNodes.indexOf(before) : this.childNodes.length;
    this.childNodes.splice(index, 0, node); node.parent = this;
  }
  remove() {
    if (this.parent) this.parent.childNodes.splice(this.parent.childNodes.indexOf(this), 1);
    this.parent = null;
  }
  querySelector() { return null; }
  addEventListener() {}
}
global.el = (tag, props, ...children) => { const element = new Element(tag, props); element.append(...children); return element; };
global.content = new Element("main");
global.state = {projectId: "p", sessionId: "s", messages: [], permissions: [], questions: [], statuses: {}, sending: false,
  expandedTools: new Map()};
global.changeTriggers = new Map();
global.messageDay = () => "今天";
global.activeProject = () => ({path: "/project"});
global.modelDisplayName = () => "Model";
global.lastUserMessage = () => state.messages.findLast(message => message.info.role === "user");
global.messageError = () => null;
global.textPart = part => new Element("text", {text: part.text});
global.skillForTool = () => null;
global.compactionRunning = () => false;
global.statusIcon = () => new Element("svg", {class: "wsp-status-icon"});
global.toolGroup = () => new Element("tools");
let actionsBuilt = 0;
global.messageActions = message => { actionsBuilt++; return new Element("actions", {text: message.parts[0]?.text}); };
const reply = (id, text) => ({info: {id, role: "assistant", parentID: "u-" + id}, parts: [{type: "text", text}]});
const render = () => {
  const children = [];
  renderMessages({append: (...nodes) => children.push(...nodes)});
  workspaceSyncChildren(content, children);
  return content.children.filter(row => row.tag === "article");
};
state.messages = [reply("a", "first"), reply("b", "stream")];
const initial = render();
const copy = initial[0].children.at(-1).children.at(-1);
// A fresh API response with identical data must preserve the actual row and buttons.
state.messages = JSON.parse(JSON.stringify(state.messages));
assert.deepEqual(render(), initial);
assert.equal(actionsBuilt, 2);
assert.equal(initial[0].children.at(-1).children.at(-1), copy);
assert.equal(initial[0].moves, 0);
assert.equal(initial[1].moves, 0);
// Streaming only replaces the changed response; earlier messages remain attached.
state.messages[1].parts[0].text = "stream complete";
const streamed = render();
assert.equal(streamed[0], initial[0]);
assert.notEqual(streamed[1], initial[1]);
assert.equal(actionsBuilt, 3);
assert.equal(streamed[0].moves, 0);
assert.equal(streamed[1].children.at(-1).children.at(-1).text, "stream complete");
// Context changes must invalidate reuse even when native message IDs match.
state.sessionId = "other";
const other = render();
assert.notEqual(other[0], streamed[0]);
assert.notEqual(other[1], streamed[1]);
// Withdraw/delete removes obsolete rows without moving the retained message.
state.messages = [state.messages[0]];
const retained = render();
assert.equal(retained[0], other[0]);
assert.equal(retained[0].moves, 0);
assert.equal(other[1].parent, null);
state.messages = [{info: {id: "u", role: "user"}, parts: [{type: "text", text: "question"}]}];
const user = render()[0];
state.sending = true;
assert.notEqual(render()[0], user);
// Native step markers and empty content parts must not hide the waiting state.
state.sending = false;
const prompt = {info: {id: "u", role: "user"}, parts: [{type: "text", text: "question"}]};
const assistant = {info: {id: "a", role: "assistant", parentID: "u"}, parts: []};
const descendants = node => [node, ...node.children.flatMap(descendants)];
const progress = row => descendants(row).filter(node => node.class === "wsp-thinking");
const label = row => progress(row)[0]?.children.at(-1)?.text;
state.statuses = {other: {type: "busy"}};
for (const parts of [[], [{type: "step-start"}], [{type: "text", text: ""}],
                     [{type: "reasoning", text: ""}, {type: "step-start"}],
                     [{type: "reasoning", text: "Considering the layout", time: {start: 1}}]]) {
  state.messages = [prompt, {...assistant, parts}];
  const row = render().at(-1);
  assert.equal(label(row), "正在思考…");
  assert.equal(progress(row)[0].role, "status");
  assert.equal(progress(row)[0].children[0].tag, "svg");
}
// Only the current turn shows progress; completed history must stay unchanged.
state.messages = [{...assistant, info: {...assistant.info, id: "old", parentID: "old-u"}}, prompt, assistant];
assert.equal(progress(render()[0]).length, 0);
state.statuses.other = {type: "retry", attempt: 1};
assert.equal(label(render().at(-1)), "正在重试…");
state.statuses.other = {type: "idle"};
assert.equal(progress(render().at(-1)).length, 0);
assert(descendants(render().at(-1)).some(node => node.text === "本轮未收到回复"));
// Before the first assistant message arrives, show a temporary reply row.
state.messages = [prompt];
state.statuses.other = {type: "busy"};
assert.equal(label(render().at(-1)), "正在思考…");
assert.equal(state.messages.length, 1);
assert(!descendants(render().at(-1)).some(node => node.tag === "actions"));
state.statuses = {};
assert.equal(render().length, 1);
// Output and tool execution have distinct progress; idle removes the spinner.
state.statuses.other = {type: "busy"};
state.messages = [prompt, {...assistant, parts: [{type: "text", text: "Streaming", time: {start: 1}}]}];
assert.equal(label(render().at(-1)), "正在回复…");
state.messages[1].parts[0].time.end = 2;
assert.equal(label(render().at(-1)), "正在思考…");
state.messages[1].parts = [{type: "tool", state: {status: "running"}}];
assert.equal(progress(render().at(-1)).length, 0);
state.messages[1].parts[0].state.status = "completed";
assert.equal(label(render().at(-1)), "正在思考…");
state.statuses = {};
assert.equal(progress(render().at(-1)).length, 0);
// Permission/question pauses and compaction must not be labelled thinking.
state.messages = [prompt, assistant];
state.statuses.other = {type: "busy"};
state.permissions = [{sessionID: "different", id: "p"}];
assert.equal(label(render().at(-1)), "正在思考…");
state.permissions = [{sessionID: "other", id: "p"}];
assert.equal(progress(render().at(-1)).length, 0);
state.permissions = [];
global.questionCard = () => new Element("question");
state.questions = [{sessionID: "other", id: "q"}];
assert.equal(progress(render().at(-1)).length, 0);
state.questions = [];
global.messageError = info => info?.error ? new Element("error") : null;
state.messages[1] = {...assistant, info: {...assistant.info, error: {name: "APIError"}}};
assert.equal(progress(render().at(-1)).length, 0);
state.messages[1] = assistant;
global.compactionRunning = () => true;
assert.equal(progress(render().at(-1)).length, 0);
// A non-Git native server may return an empty diff even after successful writes.
vm.runInThisContext(source.slice(source.indexOf("  async function openMessageChange("),
  source.indexOf("  function focusSelectedChange(")));
global.closeChangePopover = () => {};
global.renderHeader = global.renderMain = global.focusSelectedChange = () => {};
global.sessionPath = () => "session";
global.alive = () => true;
global.detail = error => error.message;
const toolDiff = {file: "game.html", before: "", after: "new", additions: 1, deletions: 0};
global.api = async () => [];
(async () => {
  await openMessageChange("u", [toolDiff], "game.html");
  assert.deepEqual(state.selectedChange.diffs, [toolDiff]);
  assert.equal(state.selectedChange.loading, false);
  const nativeDiff = {...toolDiff, additions: 2};
  global.api = async () => [nativeDiff];
  await openMessageChange("u", [toolDiff], "game.html");
  assert.deepEqual(state.selectedChange.diffs, [nativeDiff]);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True
    )
