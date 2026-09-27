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
}
global.el = (tag, props, ...children) => { const element = new Element(tag, props); element.append(...children); return element; };
global.content = new Element("main");
global.state = {projectId: "p", sessionId: "s", messages: [], permissions: [], questions: [], statuses: {}, sending: false};
global.changeTriggers = new Map();
global.messageDay = () => "今天";
global.activeProject = () => ({path: "/project"});
global.modelDisplayName = () => "Model";
global.lastUserMessage = () => state.messages.findLast(message => message.info.role === "user");
global.messageError = () => null;
global.textPart = part => new Element("text", {text: part.text});
global.skillForTool = () => null;
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
'''
    subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True
    )
