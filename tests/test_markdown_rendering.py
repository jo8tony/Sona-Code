"""Safe table rendering shared by conversation and recording views."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_markdown_tables_alignment_inline_content_streaming_and_code_fences():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for Markdown rendering coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
class Element {
  constructor(tag, props = {}) {this.tag = tag; Object.assign(this, props); this.children = [];}
  append(...items) {for (const item of items) {
    if (item == null || item === false) continue;
    if (item.tag === "fragment") this.children.push(...item.children);
    else this.children.push(item);
  }}
}
global.document = {createDocumentFragment: () => new Element("fragment")};
global.el = (tag, props, ...children) => {const node = new Element(tag, props); node.append(...children); return node;};
vm.runInThisContext(fs.readFileSync("sona_code/web/static/trajectory.js", "utf8"));
const find = (node, tag) => typeof node === "object" ? [ ...(node.tag === tag ? [node] : []),
  ...node.children.flatMap(child => find(child, tag)) ] : [];
const text = node => typeof node === "string" ? node : node.children.map(text).join("");
const table = "| 代码 | 状态 | 枚举常量 |\r\n| :--- | :---: | ---: |\r\n| -5 | **校验中** | `DATA_PREPARING` |\r\n" +
  "| -4 | [链接](https://example.test) | a\\|b |\r\n| -3 | <script>alert(1)</script> |\r\n";
const root = trjMarkdown("说明\n" + table + "\n后续说明");
assert.equal(find(root, "table").length, 1); assert.equal(find(root, "tr").length, 4);
assert.deepEqual(find(root, "th").map(cell => cell.style), ["text-align: left", "text-align: center", "text-align: right"]);
assert(find(root, "th").every(cell => cell.scope === "col"));
assert.equal(find(root, "td").length, 9, "Missing cells should be padded");
assert.equal(text(find(root, "strong")[0]), "校验中");
assert.equal(text(find(root, "code")[0]), "DATA_PREPARING");
assert.equal(find(root, "a")[0].href, "https://example.test");
assert.equal(text(find(root, "td")[5]), "a|b");
assert.equal(find(root, "script").length, 0);
assert(text(root).includes("<script>alert(1)</script>"));
assert.equal(find(trjMarkdown("```md\n" + table + "```"), "table").length, 0);
assert.equal(find(trjMarkdown("a | b\n-- | invalid\n1 | 2"), "table").length, 0);
assert.equal(find(trjMarkdown("a | b\n--- | ---\n1 | par"), "table").length, 1, "Incomplete streamed rows should render");
assert.deepEqual(trjTableCells("| `a|b` | a\\|b |"), ["`a|b`", "a|b"]);
assert.equal(text(trjMarkdown("hello\nworld")), "hello world");
assert.equal(find(trjMarkdown("hello  \nworld"), "br").length, 1);
assert.equal(find(trjMarkdown("[bad](javascript:alert)"), "a")[0].href, "#");
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)
