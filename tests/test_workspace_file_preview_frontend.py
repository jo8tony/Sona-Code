"""Preview search, conversation preservation, and offline assets."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for file preview frontend coverage")
    subprocess.run([node, "-e", script], cwd=ROOT, check=True)


def test_file_search_is_literal_case_insensitive_and_bounded():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace-files.js", "utf8"));
assert.deepEqual(workspaceFileMatches("A.[x] a.[x]", "a.[x]").positions,
  [{start:0,end:5},{start:6,end:11}]);
assert.deepEqual(workspaceFileMatches("😀你好 😀你好", "😀你好").positions,
  [{start:0,end:4},{start:5,end:9}]);
assert.deepEqual(workspaceFileMatches("\\ .*+?^${}()|[]", "\\").positions, [{start:0,end:1}]);
assert.deepEqual(workspaceFileMatches("abc", "").positions, []);
assert.equal(workspaceFileMatches("a".repeat(1001), "a").positions.length, 1000);
assert.equal(workspaceFileMatches("a".repeat(1001), "a").limited, true);
assert.equal(workspaceFileMatches("a".repeat(1000), "a").limited, false);
assert.equal(workspacePreviewContentUrl("p/id", "src/中文 #?.svg", "v"),
  "api/workspace/projects/p%2Fid/entries/content?path=src%2F%E4%B8%AD%E6%96%87%20%23%3F.svg&version=v");
''')


def test_return_from_preview_restores_history_without_erasing_new_messages():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const scroll = {scrollTop:420};
const state = {tab:"chat",projectId:"p",sessionId:"s",filePreview:{focus(){},maximize(){}}};
const context = vm.createContext({state,scroll,browsingHistory:true,followLatest:false,messageVersion:3,
  root:{classList:{remove(){}}}, closeChangePopover(){},hideAutocomplete(){},renderHeader(){},
  renderMain(){scroll.scrollTop=0;},workspaceSelection:{tab:"chat"},navigation:{render(){}},
  view:{querySelector:()=>({hidden:true,focus(){}})},refreshSelected(){},});
vm.runInContext(source.slice(source.indexOf("  function enterFilePreview("),
  source.indexOf("  async function openFilePreview(")),context);
context.enterFilePreview();
assert.equal(state.tab,"preview"); assert.equal(state.previewReturn.top,420);
// Opening another file retains the original return position.
context.enterFilePreview(); assert.equal(state.previewReturn.top,420);
context.leaveFilePreview();
assert.equal(state.tab,"chat"); assert.equal(scroll.scrollTop,420);
assert.equal(context.browsingHistory,true); assert.equal(context.followLatest,false);
// A response arriving during preview must not force a jump to the latest message.
context.browsingHistory=false; context.followLatest=true; scroll.scrollTop=800;
context.enterFilePreview(); context.messageVersion++;
context.leaveFilePreview();
assert.equal(scroll.scrollTop,800); assert.equal(context.browsingHistory,true);
assert.equal(context.followLatest,false);
// Closing a preview opened from another view returns there.
state.tab="changes"; context.enterFilePreview(); context.leaveFilePreview();
assert.equal(state.tab,"changes");
// A stale return context cannot be restored into a different conversation.
context.enterFilePreview(); state.sessionId="other"; context.leaveFilePreview();
assert.equal(state.tab,"chat");
''')


def test_background_chat_render_does_not_replace_file_preview_or_chat_nodes():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const preview = {hidden:true}, chat = {hidden:false}, composer = {hidden:false};
const context = vm.createContext({state:{tab:"preview"},composerDock:composer,
  view:{querySelector:selector=>selector==="#wsp-file-preview"?preview:chat},renderTodoPanel(){},
  content:{replaceChildren(){throw new Error("Chat DOM was replaced during preview");}},
  scroll:{get scrollTop(){throw new Error("Hidden chat was scrolled during preview");}},});
vm.runInContext(source.slice(source.indexOf("  function renderMain("),
  source.indexOf("  async function loadSessions(")),context);
context.renderMain(true);
assert.equal(preview.hidden,false); assert.equal(chat.hidden,true); assert.equal(composer.hidden,true);
''')


def test_offline_preview_assets_match_pinned_manifest():
    vendor = ROOT / "sona_code/web/static/vendor"
    manifest = json.loads((vendor / "file-preview.json").read_text())
    assert manifest["packages"]["prismjs"]["version"] == "1.30.0"
    assert manifest["packages"]["pdfjs-dist"]["version"] == "5.4.624"
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256((vendor / relative).read_bytes()).hexdigest() == expected, relative
    assert {"prism.js", "pdfjs/pdf.mjs", "pdfjs/pdf.worker.mjs", "pdfjs/LICENSE"} <= manifest["files"].keys()
    assert any(name.startswith("pdfjs/cmaps/") for name in manifest["files"])
    assert any(name.startswith("pdfjs/standard_fonts/") for name in manifest["files"])
    assert any(name.startswith("pdfjs/wasm/") for name in manifest["files"])
