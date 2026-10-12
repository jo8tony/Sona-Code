"""Skill switches must reflect confirmed native state and survive failed updates."""

from tests.test_workspace_interactions import run_node


def test_skill_switch_pending_failure_confirmation_filtering_and_disposal():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
class Node {
  constructor(tag, attrs = {}, children = []) {
    this.tag = tag; this.attrs = {}; this.children = []; this.value = ""; this.listeners = {};
    this.className = ""; this.disabled = false; this.hidden = false;
    this.classList = {
      contains: name => this.className.split(" ").includes(name),
      toggle: (name, active) => {
        const classes = new Set(this.className.split(" ").filter(Boolean));
        if (active) classes.add(name); else classes.delete(name);
        this.className = [...classes].join(" ");
      }
    };
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "class") this.className = value;
      else if (key === "text") this.textContent = value;
      else if (key.startsWith("on")) this.listeners[key.slice(2)] = value;
      else if (["disabled", "hidden", "value"].includes(key)) this[key] = value;
      else this.setAttribute(key, value);
    }
    this.append(...children);
  }
  setAttribute(key, value) {this.attrs[key] = String(value);}
  getAttribute(key) {return this.attrs[key] ?? null;}
  append(...children) {
    for (const child of children.flat(Infinity)) {
      if (child instanceof Node) this.insertBefore(child, null);
    }
  }
  insertBefore(child, reference) {
    child.remove(); child.parentElement = this;
    const index = reference ? this.children.indexOf(reference) : this.children.length;
    this.children.splice(index, 0, child);
  }
  remove() {
    if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
    this.parentElement = null;
  }
  replaceChildren(...children) {for (const child of [...this.children]) child.remove(); this.append(...children);}
  get isConnected() {return this === document.body || !!this.parentElement?.isConnected;}
  addEventListener(type, fn) {this.listeners[type] = fn;}
  focus() {document.activeElement = this;}
  click() {if (!this.disabled) {this.focus(); return this.listeners.click?.();}}
}
global.document = {body: new Node("body")}; document.activeElement = document.body;
global.el = (tag, attrs, ...children) => new Node(tag, attrs, children);
global.workspaceIcon = () => new Node("svg");
let cleanup; global.addCleanup = fn => {cleanup = fn;}; global.toast = () => {};
let items = [
  {id: "review", name: "code-review", description: "Review changes", enabled: true, source: "app", deletable: true},
  {id: "writing", name: "writing-guide", description: "Write clear docs", enabled: false, source: "external"},
  {id: "invalid", name: "invalid", error: "Invalid frontmatter", enabled: true}
];
let pending, reads = 0;
global.api = async (path, options = {}) => {
  if (!options.method) {reads++; return {items: structuredClone(items)};}
  return new Promise((resolve, reject) => {pending = {path, options, resolve, reject};});
};
vm.runInThisContext(fs.readFileSync("sona_code/web/static/skills.js", "utf8"));
const view = new Node("main"); document.body.append(view);
const nodes = (root = view) => [root, ...root.children.flatMap(child => nodes(child))];
const find = predicate => nodes().find(predicate);
const toggle = name => find(node => node.attrs.role === "switch" && node.attrs["aria-label"] === `启用 ${name}`);
const filter = label => find(node => node.classList.contains("skill-filter") && node.textContent === label);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  renderSkills(view); await tick();
  const review = toggle("code-review"), writing = toggle("writing-guide");
  assert.equal(review.attrs["aria-checked"], "true");
  assert.equal(toggle("invalid").disabled, true);
  assert.equal(toggle("invalid").attrs["aria-checked"], "false");
  const failed = review.click();
  assert.equal(pending.path, "skills/review"); assert.equal(pending.options.body.enabled, false);
  assert.equal(review.attrs["aria-checked"], "true", "Pending requests must not imply persisted permission");
  assert.equal(review.attrs["aria-busy"], "true"); assert.equal(writing.disabled, true);
  document.activeElement = document.body; // Browsers blur a button when it becomes disabled.
  pending.reject({detail: "请先等待当前任务结束"}); await failed;
  assert.equal(toggle("code-review"), review, "Keep controls mounted across state changes");
  assert.equal(review.attrs["aria-checked"], "true"); assert.equal(review.disabled, false);
  assert.equal(document.activeElement, review, "Keyboard users must retain their place after a failed request");
  assert.equal(find(node => node.classList.contains("skill-card-error") && !node.hidden).textContent, "请先等待当前任务结束");
  const succeeded = review.click();
  items[0].enabled = false; document.activeElement = document.body;
  pending.resolve({ok: true}); await succeeded;
  assert.equal(review.attrs["aria-checked"], "false"); assert.equal(document.activeElement, review);
  assert.equal(find(node => node.classList.contains("skill-count")).textContent, "3 个技能 · 0 个已启用");
  filter("已停用").click(); assert.equal(review.isConnected, true); assert.equal(writing.isConnected, true);
  const enabled = review.click(); items[0].enabled = true; document.activeElement = document.body;
  pending.resolve({ok: true}); await enabled;
  assert.equal(review.isConnected, false, "A confirmed update must apply the selected status filter");
  assert.equal(document.activeElement, filter("已停用"));
  filter("全部").click();
  const search = find(node => node.attrs["aria-label"] === "搜索技能");
  search.value = "clear"; search.listeners.input();
  assert.equal(review.isConnected, false); assert.equal(writing.isConnected, true);
  const abandoned = writing.click(), readsBefore = reads;
  cleanup(); pending.resolve({ok: true}); await abandoned;
  assert.equal(reads, readsBefore, "Navigation must not reload an abandoned page after an update");
})().catch(error => {console.error(error); process.exitCode = 1;});
''')
