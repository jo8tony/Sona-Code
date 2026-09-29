"""Behavior coverage for the expandable call-detail JSON viewer."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_json_tree_controls_and_lazy_children():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for JSON viewer coverage")
    script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/app.js", "utf8");
vm.runInThisContext(source.slice(source.indexOf("function jsonViewer("),
  source.indexOf("/* ============================================================ 响应内容")));
class Element {
  constructor(tag, props = {}) {
    this.tagName = tag.toUpperCase(); Object.assign(this, props);
    this.children = []; this.events = {};
    this.classList = {add: value => { this.highlight = value; }, remove: () => { this.highlight = null; }};
  }
  append(...nodes) {
    for (const item of nodes.filter(Boolean)) {
      const node = typeof item === "string" ? new Element("text", {text: item}) : item;
      this.children.push(node); node.parentElement = this;
    }
  }
  addEventListener(event, handler) { this.events[event] = handler; }
  set open(value) { this._open = value; this.events?.toggle?.(); }
  get open() { return !!this._open; }
  get firstElementChild() { return this.children[0]; }
  scrollIntoView() { this.scrolled = true; }
}
global.el = (tag, props, ...children) => {
  const result = new Element(tag, props); result.append(...children); return result;
};
const all = node => [node, ...node.children.flatMap(all)];
const details = viewer => all(viewer).filter(node => node.tagName === "DETAILS");
const click = (viewer, label) => all(viewer).find(node => node.text === label).onclick();
const payload = {messages: [{role: "user", content: {text: "<script>hello</script>"}}],
  tools: {fn: {parameters: {type: "object"}}}, stream: true, max_tokens: 42, empty: [], none: null};
const original = JSON.stringify(payload);
const viewer = jsonViewer(payload);
assert.equal(details(viewer).length, 3); // Root and its two child collections only.
assert.deepEqual(details(viewer).map(node => node.open), [true, false, false]);
assert(!all(viewer).some(node => node.text === '"role"'));
click(viewer, "展开一级");
assert.equal(details(viewer).filter(node => node.open).length, 3);
assert.equal(details(viewer).length, 5);
click(viewer, "展开一级");
assert.equal(details(viewer).filter(node => node.open).length, 5);
click(viewer, "收起一级");
assert.equal(details(viewer).filter(node => node.open).length, 3);
click(viewer, "全部展开");
assert(details(viewer).every(node => node.open));
assert(all(viewer).some(node => node.class === "j-str" && node.text === '"<script>hello</script>"'));
assert(all(viewer).some(node => node.class === "j-index" && node.text === "0"));
assert(all(viewer).some(node => node.text === "[]"));
assert(all(viewer).some(node => node.class === "j-null" && node.text === "null"));
click(viewer, "全部收起");
assert(details(viewer).every(node => !node.open));
click(viewer, "展开一级");
assert.equal(details(viewer).filter(node => node.open).length, 1);
// Individual node toggles load only that branch and retain its subtree on reopen.
const branch = details(viewer)[1];
branch.open = true;
const count = details(viewer).length;
branch.open = false; branch.open = true;
assert.equal(details(viewer).length, count);
assert.equal(JSON.stringify(payload), original);
// Root arrays, empty collections and scalar JSON values remain readable.
for (const value of [[], {}, null, false, 0, "hello"]) {
  assert.equal(details(jsonViewer(value)).length, 0);
}
const array = jsonViewer([{nested: {value: 1}}]);
assert.deepEqual(details(array).map(node => node.open), [true, false]);
// Search unopened fields and values, navigate, and restore expansion on clear.
const searchViewer = jsonViewer({nested: {needle: "NEEDLE"}, other: "needle", flags: [false, null, 42]});
const input = all(searchViewer).find(node => node.class === "json-search-input");
const countLabel = all(searchViewer).find(node => node.class === "json-search-count");
input.value = "needle"; input.events.input();
assert.equal(countLabel.textContent, "1 / 2");
assert(details(searchViewer).every(node => node.open || !all(node).some(item => item.text === '"needle"')));
click(searchViewer, "下一个");
assert.equal(countLabel.textContent, "2 / 2");
click(searchViewer, "上一个");
assert.equal(countLabel.textContent, "1 / 2");
input.value = "missing"; input.events.input();
assert.equal(countLabel.textContent, "无匹配结果");
assert(all(searchViewer).find(node => node.text === "下一个").disabled);
input.value = "42"; input.events.input();
assert.equal(countLabel.textContent, "1 / 1");
input.value = ""; input.events.input();
assert.equal(countLabel.textContent, "");
assert.deepEqual(details(searchViewer).map(node => node.open), [true, false, false]);
assert(!all(searchViewer).some(node => node.highlight));
// Large request bodies retain tree controls and defer nested content.
const large = jsonViewer({messages: [{content: "x".repeat(1000001)}]});
assert.equal(details(large).length, 2);
assert(!all(large).some(node => node.class === "j-str"));
const parsed = bodyViewer(JSON.stringify(payload), true);
assert(all(parsed).some(node => node.class === "banner banner-warn"));
assert.equal(details(parsed).length, 3);
const invalid = bodyViewer('{"broken":', false);
assert(all(invalid).some(node => node.tagName === "PRE" && node.text === '{"broken":'));
'''
    subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True
    )
