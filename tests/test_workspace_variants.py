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
  messages: [{info: {id: "previous-user", role: "user"}}],
  check: {found: true}, chosenModels: new Map(), chosenVariants: new Map(),
  providers: [{id: "provider", models: {
    reasoning: {name: "Reasoning", variants: {high: {}, none: {}, max: {}}},
    plain: {name: "Plain", variants: {}}
  }}]};
const variantSelect = {hidden: true, value: "", replaceChildren() {}};
const variantTrigger = {dataset: {}};
const composer = {value: "", fileReferences: [], snapshot: () => ({text: composer.value, references: composer.fileReferences}),
  restore(draft) {this.value = draft.text; this.fileReferences = draft.references || [];}};
let submit, captured, mounted = false;
const noop = () => {};
const context = vm.createContext({state, variantSelect, variantTrigger, composer, creatingSessions: new Set(),
  roundClocks: new Map(), renderRoundClock: noop,
  variantLabel: {}, variantPicker: {hidden: true}, agentSelect: {value: "build"},
  modelButton: {replaceChildren: noop}, el: (tag, props) => props,
  closeVariantPicker: noop, renderVariantPicker: noop,
  view: {querySelector: () => ({addEventListener: (event, handler) => {submit = handler;}})},
  consumeMenuCommand: () => false, builtInCommands: [], availableBuiltInCommands: () => [],
  WORKSPACE_CHAT_ID: "chat", WORKSPACE_CHAT_AGENT: "sonacode-chat",
  executeBuiltIn: async () => false,
  workspacePrepareNotifications: () => {},
  workspaceConversationKey: (project, session) => `${project}:${session}`,
  sendingConversations: new Set(), pendingActions: new Map(), outgoingMessages: new Map(),
  updateSkillInput: noop, renderAttachments: noop, renderMain: noop,
  saveDraft: noop, renderHeader: noop, hideAutocomplete: noop, closeSendMenu: noop, applyQueue: noop,
  ensureSessionForSend: async () => ({projectId: state.projectId, sessionId: state.sessionId}),
  sessionPath: (project, session) => `workspace/projects/${project}/sessions/${session}`,
  api: async (path, options) => {captured = {path, ...options}; return {items: [{id: "admission"}]};},
  alive: () => mounted, detail: error => error.message,
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
      state.chosenModels.set("project:session", `provider\u0000${modelID}`);
      state.chosenVariants.set("project:session", chosen);
      context.updateModelButton();
      assert.equal(variantSelect.hidden, true);
      assert.equal(variantTrigger.hidden, modelID === "plain");
      if (modelID === "plain") assert.equal(state.chosenVariants.get("project:session"), "");
      composer.value = text;
      captured = undefined;
      const previousClock = context.roundClocks.get("project:session");
      const before = Date.now();
      let prevented = false;
      await submit({preventDefault: () => {prevented = true;}});
      assert(prevented);
      assert(captured);
      const clock = context.roundClocks.get("project:session");
      assert.notEqual(clock, previousClock);
      assert(clock.startedAt >= before && clock.startedAt <= Date.now());
      assert.equal(clock.baselineId, "previous-user");
      assert.equal(clock.queueId, "admission");
      assert.equal(captured.path, "workspace/projects/project/sessions/session/send");
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
  composer.value = "queued explicitly";
  await submit({preventDefault: noop, submitter: {dataset: {delivery: "queue"}}});
  assert.equal(captured.path, "workspace/projects/project/sessions/session/queue");

  // A slow native/history response must not lock input or erase a new draft.
  mounted = true;
  let resolveAdmission, refreshes = 0, focused = 0;
  const blockedHistory = new Promise(() => {});
  const input = {focus: () => {focused++;}};
  Object.assign(context, {
    input, document: {activeElement: input}, sendActions: {contains: () => false},
    dismissedTodoPanels: new Set(), renderTodoPanel: noop,
    workspaceReadDraft: () => null, workspaceWriteDraft: noop,
    updateSkillInput: noop, renderAttachments: noop, renderMain: noop,
    refreshWorkspaceStatuses: noop,
    loadSessions: () => {refreshes++; return blockedHistory;},
    refreshSelected: () => {refreshes++; return blockedHistory;},
    api: () => new Promise(resolve => {resolveAdmission = resolve;})
  });
  state.projects = [{id: "project"}];
  composer.value = "first submission";
  const pending = submit({preventDefault: noop});
  while (!resolveAdmission) await Promise.resolve();
  assert(context.sendingConversations.has("project:session"));
  composer.value = "next draft";
  const attachment = {filename: "new.png", mime: "image/png", url: "data:image/png;base64,AA=="};
  state.attachments = [attachment];
  resolveAdmission({submitted_id: "exact", message_id: "native", items: [{id: "later-item"}]});
  await pending;
  assert.equal(composer.value, "next draft");
  assert.equal(state.attachments[0], attachment);
  assert.equal(context.sendingConversations.size, 0);
  assert.equal(context.roundClocks.get("project:session").queueId, "exact");
  assert.equal(context.roundClocks.get("project:session").userId, "native");
  assert.equal(refreshes, 2);
  assert.equal(focused, 1);

  resolveAdmission = undefined;
  const next = submit({preventDefault: noop});
  while (!resolveAdmission) await Promise.resolve();
  resolveAdmission({submitted_id: "next", message_id: "native-next", items: []});
  await next;
  assert.equal(composer.value, "");
  assert.equal(state.attachments.length, 0);
  assert.equal(context.sendingConversations.size, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True
    )
