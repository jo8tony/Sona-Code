"""Reasoning choices must reach requests sent by the custom workspace picker."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_workspace_submit_preserves_reasoning_variants_for_prompts_and_commands():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace variant coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const state = {projectId: "project", sessionId: "session", attachments: [], commands: [{name: "review"}],
  check: {found: true}, chosenModels: new Map(), chosenVariants: new Map(),
  providers: [{id: "provider", models: {
    reasoning: {name: "Reasoning", variants: {high: {}, none: {}, max: {}}},
    plain: {name: "Plain", variants: {}}
  }}]};
const variantSelect = {hidden: true, value: "", replaceChildren() {}};
const variantTrigger = {dataset: {}};
const composer = {value: "", fileReferences: [], snapshot: () => ({text: composer.value})};
let submit, captured;
const noop = () => {};
const context = vm.createContext({state, variantSelect, variantTrigger, composer,
  variantLabel: {}, variantPicker: {hidden: true}, agentSelect: {value: "build"},
  modelButton: {replaceChildren: noop}, el: (tag, props) => props,
  closeVariantPicker: noop, renderVariantPicker: noop,
  view: {querySelector: () => ({addEventListener: (event, handler) => {submit = handler;}})},
  consumeMenuCommand: () => false, builtInCommands: [],
  executeBuiltIn: async () => false,
  workspacePrepareNotifications: () => {},
  workspaceConversationKey: (project, session) => `${project}:${session}`,
  sendingConversations: new Set(), pendingActions: new Map(),
  saveDraft: noop, renderHeader: noop, hideAutocomplete: noop, applyQueue: noop,
  ensureSessionForSend: async () => ({projectId: state.projectId, sessionId: state.sessionId}),
  sessionPath: (project, session) => `workspace/projects/${project}/sessions/${session}`,
  api: async (path, options) => {captured = {path, ...options}; return {};},
  alive: () => false, detail: error => error.message,
  toast: message => {throw new Error(message);}
});
const icons = fs.readFileSync("sona_code/web/static/workspace-tree.js", "utf8");
vm.runInContext(icons.slice(0, icons.indexOf("function createWorkspaceTree(")), context);
vm.runInContext(source.slice(source.indexOf("  function selectedModel()"),
  source.indexOf("  function closeModelPicker()")), context);
const start = source.indexOf('  view.querySelector("#wsp-form").addEventListener("submit"');
assert(start >= 0);
const end = source.indexOf("\n  function consumeMenuCommand()", start);
assert(end > start);
vm.runInContext(source.slice(start, end), context);
(async () => {
  for (const text of ["hello", "/review HEAD"]) {
    for (const [modelID, chosen, expected] of [
      ["reasoning", "high", "high"], ["reasoning", "none", "none"],
      ["reasoning", "max", "max"], ["reasoning", "", undefined],
      ["plain", "max", undefined]
    ]) {
      state.chosenModels.set(state.projectId, `provider\u0000${modelID}`);
      state.chosenVariants.set(state.projectId, chosen);
      context.updateModelButton();
      assert.equal(variantSelect.hidden, true);
      assert.equal(variantTrigger.hidden, modelID === "plain");
      if (modelID === "plain") assert.equal(state.chosenVariants.get(state.projectId), "");
      composer.value = text;
      captured = undefined;
      let prevented = false;
      await submit({preventDefault: () => {prevented = true;}});
      assert(prevented);
      assert(captured);
      assert.equal(captured.path, "workspace/projects/project/sessions/session/queue");
      assert.equal(captured.method, "POST");
      const {kind, payload} = captured.body;
      assert.equal(payload.provider_id, "provider");
      assert.equal(payload.model_id, modelID);
      assert.equal(payload.agent, "build");
      assert.equal(payload.variant, expected, `${text}: ${modelID} / ${chosen}`);
      assert.equal(Object.hasOwn(payload, "variant"), expected !== undefined);
      if (text.startsWith("/")) {
        assert.equal(kind, "command");
        assert.equal(payload.command, "review");
        assert.equal(payload.arguments, "HEAD");
      } else {
        assert.equal(kind, "prompt");
        assert.equal(payload.text, "hello");
      }
    }
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True
    )
