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
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
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
// Metadata-only and damaged history rows must explain missing content.
for (const parts of [[], [{type: "text", text: ""}], [{type: "text", text: "  \n"}]]) {
  state.messages = [{...prompt, parts}];
  const row = render()[0];
  assert(descendants(row).some(node => node.text === "消息内容暂不可用"));
}
state.messages = [prompt];
assert(descendants(render()[0]).some(node => node.text === "question"), "Late content must replace the missing-content hint");
const synthetic = {type: "text", synthetic: true, text: "Continue with the task"};
state.messages = [{...prompt, parts: [synthetic]}];
assert.equal(render().length, 0, "Native internal messages must not create empty user bubbles");
assert.equal(content.children.length, 0, "A hidden internal message must not leave an orphan date divider");
state.messages = [{...prompt, parts: [synthetic, ...prompt.parts]}];
assert(descendants(render()[0]).some(node => node.text === "question"));
state.messages = [{...prompt, parts: [synthetic, {type: "file", filename: "image.png", mime: "image/png"}]}];
assert(descendants(render()[0]).some(node => node.class === "wsp-message-file"), "Attachment-only prompts must stay visible");
global.skillMention = skill => new Element("skill", {text: skill.name});
state.messages = [{...prompt, parts: [synthetic], skillUse: {name: "review", arguments: "检查代码"}}];
assert(descendants(render()[0]).some(node => node.text === "review"), "Manual skill history must stay visible");
// Internal user rows still own their assistant replies and waiting state.
state.messages = [{...prompt, parts: [synthetic]}, {...assistant, parts: [{type: "text", text: "continued"}]}];
assert.equal(render().length, 1);
assert(descendants(render()[0]).some(node => node.text === "continued"));
assert.equal(state.messages.length, 2);
state.messages = [{...prompt, parts: [synthetic]}];
state.statuses = {other: {type: "busy"}};
assert.equal(label(render()[0]), "正在思考…");
state.statuses = {other: {type: "busy"}};
for (const parts of [[], [{type: "step-start"}], [{type: "text", text: ""}],
                     [{type: "reasoning", text: ""}, {type: "step-start"}],
                     [{type: "reasoning", text: "Considering the layout", time: {start: 1}}]]) {
  state.messages = [prompt, {...assistant, parts}];
  const row = render().at(-1);
  assert.equal(label(row), "正在思考…");
  assert.equal(progress(row)[0].role, "status");
  assert.equal(progress(row)[0].children[0].tag, "svg");
  assert(descendants(row).some(node => node.tag === "details" &&
    node.class?.split(" ").includes("wsp-live-reasoning")), "Thinking must show an expandable panel before reasoning-start");
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
assert(descendants(render().at(-1)).some(node => node.tag === "details"), "The first native assistant event must not gate the panel");
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


def test_completed_turn_folds_process_and_retains_final_reply():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace rendering coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
vm.runInThisContext(source);
vm.runInThisContext(source.slice(source.indexOf("  function renderMessages("), source.indexOf("  function panelIntro(")));
class Element {
  constructor(tag, props = {}) { this.tag = tag; Object.assign(this, props); this.childNodes = []; this.listeners = {}; }
  get children() { return this.childNodes; }
  get isConnected() { return this === content || !!this.parent?.isConnected; }
  append(...nodes) { for (const node of nodes) {
    const child = typeof node === "string" ? new Element("text", {text: node}) : node;
    child.remove(); this.childNodes.push(child); child.parent = this;
  } }
  remove() { if (this.parent) this.parent.childNodes.splice(this.parent.childNodes.indexOf(this), 1); this.parent = null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  emit(type) { this.listeners[type]?.forEach(fn => fn()); }
  querySelector(selector) { return descendants(this).find(node => selector.startsWith(".") ?
    node.class?.split(" ").includes(selector.slice(1)) : node.tag === selector) || null; }
}
const descendants = node => [node, ...node.children.flatMap(descendants)];
const find = (node, name) => descendants(node).find(child => child.class === name);
global.el = (tag, props, ...children) => { const node = new Element(tag, props); node.append(...children); return node; };
global.content = new Element("main");
global.state = {projectId: "p", sessionId: "s", messages: [], permissions: [], questions: [], statuses: {}, sending: false,
  expandedTools: new Map()};
global.changeTriggers = new Map();
global.messageDay = () => "今天";
global.activeProject = () => ({path: "/project"});
global.modelDisplayName = () => "Model";
global.lastUserMessage = () => state.messages.findLast(message => message.info.role === "user");
global.messageError = info => info?.error ? new Element("error", {text: "failed"}) : null;
global.textPart = part => new Element("text", {text: part.text});
global.skillForTool = () => null;
global.compactionRunning = () => false;
global.workspaceIcon = () => new Element("svg");
global.statusIcon = () => new Element("svg");
global.toolGroup = parts => el("tools", {}, ...parts.map(part => el("tool", {text: part.id})));
global.toolPart = part => el("skill", {text: part.id});
global.workspaceReasoningNode = (cached, key, text) => el("reasoning", {text});
global.messageActions = message => new Element("actions", {
  text: message.parts.filter(part => part.type === "text").map(part => part.text).join("\n"),
});
global.questionCard = () => el("question");
const render = () => {
  const rows = []; renderMessages({append: (...nodes) => rows.push(...nodes)});
  content.childNodes.forEach(node => { node.parent = null; }); content.childNodes = [];
  content.append(...rows); return rows.filter(row => row.tag === "article").at(-1);
};
const user = {info: {id: "u", role: "user", time: {created: 1000}}, parts: [{type: "text", text: "question"}]};
const step = {info: {id: "a1", role: "assistant", parentID: "u", finish: "tool-calls", time: {created: 1100, completed: 2000}},
  parts: [{id: "r", type: "reasoning", text: "thinking"}, {id: "plan", type: "text", text: "progress"},
    {id: "command", type: "tool", tool: "bash", state: {status: "completed"}}]};
const final = {info: {id: "a2", role: "assistant", parentID: "u", finish: "stop", time: {created: 2100, completed: 139000}},
  parts: [{id: "fr", type: "reasoning", text: "final thinking"}, {id: "f1", type: "text", text: "final answer"},
    {id: "f2", type: "text", text: "validation"}, {id: "attachment", type: "file", filename: "result.png"}]};
state.messages = [user, step, final]; state.statuses.s = {type: "busy"};
let row = render();
assert(!find(row, "wsp-turn-process"), "Completion metadata alone must not fold a busy turn");
assert(descendants(row).some(node => node.text === "progress"));
state.statuses.s = {type: "idle"}; row = render();
let process = find(row, "wsp-turn-process"), body = find(row, "wsp-message-inner");
assert(process && !process.open, "Completed turn defaults to collapsed");
assert(find(process, "wsp-turn-duration").text === "用时 2 分 18 秒", "Duration includes the whole turn");
assert(find(process, "wsp-turn-count").text === "1 项工具操作");
assert(descendants(process).some(node => node.text === "progress"));
assert(descendants(process).some(node => node.text === "command"));
assert(descendants(process).some(node => node.text === "final thinking"));
assert(!descendants(process).some(node => node.text === "final answer"));
assert(body.children.some(node => node.text === "final answer"));
assert(body.children.some(node => node.text === "validation"));
assert(body.children.some(node => node.class === "wsp-message-file"));
assert(descendants(row).find(node => node.tag === "actions").text === "final answer\nvalidation",
  "Copy action uses only the visible final reply");
assert.equal(state.messages[1].parts.length, 3, "Folding must not modify native history");
// Native details toggles survive row rebuilds and conversation switches.
process.open = true; process.emit("toggle"); state.fileIndexVersion = 1;
row = render(); process = find(row, "wsp-turn-process"); assert(process.open);
process.open = false; process.emit("toggle"); state.fileIndexVersion++;
assert(!find(render(), "wsp-turn-process").open);
state.sessionId = "different"; assert(!find(render(), "wsp-turn-process").open);
state.sessionId = "s"; assert(!find(render(), "wsp-turn-process").open);
// Waiting for user input is not completion, even with stale terminal metadata.
state.permissions = [{id: "permission", sessionID: "s"}];
assert(!find(render(), "wsp-turn-process")); state.permissions = [];
state.questions = [{id: "question", sessionID: "s"}];
assert(!find(render(), "wsp-turn-process")); state.questions = [];
state.sending = true; assert(find(render(), "wsp-turn-process"), "Submitting a new prompt must not reopen history"); state.sending = false;
state.statuses.s = {type: "retry"}; assert(!find(render(), "wsp-turn-process"));
// A new busy turn must not expand completed history.
state.messages.push({...user, info: {...user.info, id: "u2"}}); state.statuses.s = {type: "busy"};
render(); assert(content.querySelector(".wsp-turn-process"));
state.messages = [user, step]; state.statuses.s = {type: "idle"};
assert(!find(render(), "wsp-turn-process"), "A finished tool step is not a final reply");
state.messages = [user, step, {...final, info: {...final.info, error: {name: "MessageAbortedError"}}}];
row = render(); assert(!find(row, "wsp-turn-process")); assert(descendants(row).some(node => node.text === "failed"));
// A single reply with reasoning folds; a simple text reply needs no extra bar.
state.messages = [user, final]; row = render(); assert(find(row, "wsp-turn-process"));
state.messages = [user, {...final, parts: final.parts.slice(1)}];
assert(!find(render(), "wsp-turn-process"));
// Preserve all trailing final blocks when a provider combines tools/text in one message.
const combined = {...final, parts: [...step.parts, ...final.parts]};
const presentation = workspaceTurnPresentation(combined, {user}, false);
assert.deepEqual(presentation.finalIndexes, [4, 5, 6]);
for (const finish of ["tool-calls", "unknown", undefined]) {
  assert.equal(workspaceTurnPresentation({...combined, info: {...final.info, finish}}, {user}), null);
}
assert.equal(workspaceTurnPresentation({...combined, info: {...final.info, summary: true}}, {user}), null);
assert.equal(workspaceTurnPresentation({...combined, info: {...final.info, time: {created: 2100}}}, {user}), null);
assert.equal(workspaceTurnPresentation({...combined, parts: step.parts}, {user}), null);
assert.equal(workspaceTurnDuration(3661000), "1 小时 1 分 1 秒");
assert.equal(workspaceTurnDuration(0), "0 秒");
'''
    subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True
    )
