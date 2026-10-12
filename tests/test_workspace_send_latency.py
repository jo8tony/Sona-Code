"""Send feedback must not wait for session creation, admission or history."""

from tests.test_workspace_interactions import run_node


def test_send_previews_reconcile_and_recover_without_waiting_for_network():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8").replace(/\r\n/g, "\n");
const noop = () => {};
function harness(sessionId = "s") {
  let submit, resolveAdmission, rejectAdmission;
  const drafts = new Map(), renders = [], notices = [];
  const outgoingMessages = new Map(), sendingConversations = new Set();
  const input = {focus: noop};
  const state = {projectId: "p", sessionId, messages: [], attachments: [], fileReferences: [],
    projects: [{id: "p"}], check: {found: true}, pendingImageCount: 0, commands: [],
    get sending() {return sendingConversations.has(`${this.projectId}:${this.sessionId}`);}};
  const composer = {text: "", references: [],
    get value() {return this.text;}, set value(text) {this.text = text; this.references = [];},
    get fileReferences() {return this.references;},
    snapshot() {return {text: this.text, references: this.references.map(ref => ({...ref}))};},
    restore(draft) {this.text = draft.text; this.references = draft.references.map(ref => ({...ref}));}};
  const context = vm.createContext({state, composer, input, outgoingMessages, sendingConversations,
    creatingSessions: new Set(), pendingActions: new Map(), roundClocks: new Map(),
    variantSelect: {value: "high"}, agentSelect: {value: "build"},
    WORKSPACE_CHAT_ID: "chat", WORKSPACE_CHAT_AGENT: "sonacode-chat",
    selectedModel: () => ({provider_id: "demo", model_id: "model"}),
    workspaceConversationKey: (p, s) => `${p}:${s}`,
    workspaceReadDraft: key => drafts.get(key),
    workspaceWriteDraft: (key, value) => value ? drafts.set(key, value) : drafts.delete(key),
    saveDraft() {drafts.set(`${state.projectId}:${state.sessionId}`,
      {...composer.snapshot(), attachments: state.attachments.map(item => ({...item}))});},
    sessionPath: (p, s) => `${p}/${s}`,
    ensureSessionForSend: async () => ({projectId: state.projectId, sessionId: state.sessionId}),
    view: {querySelector: () => ({addEventListener: (_, handler) => {submit = handler;}})},
    api: (path, options) => new Promise((resolve, reject) => {
      context.request = {path, ...options}; resolveAdmission = resolve; rejectAdmission = reject;
    }),
    renderHeader: noop, renderRoundClock: noop, renderAttachments: noop, renderTodoPanel: noop,
    renderMain() {renders.push(context.conversationMessages());},
    applyQueue: noop, refreshWorkspaceStatuses: noop, loadSessions: noop, refreshSelected: noop,
    updateSkillInput: noop, hideAutocomplete: noop, closeSendMenu: noop,
    workspacePrepareNotifications: noop, consumeMenuCommand: () => false,
    alive: () => true, detail: error => error.message, toast: message => notices.push(message),
    dismissedTodoPanels: new Set(), document: {activeElement: input}, sendActions: {contains: () => false},
  });
  vm.runInContext(source.slice(source.indexOf("function workspaceOutgoingProjection("),
    source.indexOf("// Group all descendants")), context);
  vm.runInContext(source.slice(source.indexOf("function workspaceDirectoryReference("),
    source.indexOf("function workspacePreserveUserText(")), context);
  vm.runInContext(source.slice(source.indexOf("  function conversationMessages("),
    source.indexOf("  async function queueAction(")), context);
  vm.runInContext(source.slice(source.indexOf('  view.querySelector("#wsp-form").addEventListener("submit"'),
    source.indexOf("  function consumeMenuCommand()")), context);
  return {state, composer, context, drafts, outgoingMessages, renders, notices,
    send: queued => submit({preventDefault: noop, submitter: {dataset: {delivery: queued ? "queue" : ""}}}),
    resolve: receipt => resolveAdmission(receipt), reject: () => rejectAdmission(new Error("offline"))};
}
const tick = async () => {for (let i = 0; i < 5; i++) await Promise.resolve();};
const textPart = text => ({type: "text", text});
const native = (id, parts) => ({info: {id, role: "user"}, parts});
const image = {id: "image", filename: "a.png", mime: "image/png", url: "data:image/png;base64,AA=="};
const receipt = id => ({message_id: id, submitted_id: id, items: []});
(async () => {
  const h = harness();
  h.state.messages = [native("old", [textPart("same prompt")])];
  h.composer.value = "same prompt"; h.composer.references = [{path: "src/a.py", start: 0}];
  h.state.attachments = [image];
  const pending = h.send();
  // Feedback is synchronous, even before the first network request starts.
  assert.equal(h.composer.value, ""); assert.equal(h.state.attachments.length, 0);
  assert.equal(h.composer.fileReferences.length, 0);
  assert.equal(h.renders[0].length, 2); assert(h.renders[0][1].outgoing);
  assert.equal(h.state.messages.length, 1, "Preview must not contaminate native history");
  await tick();
  assert.equal(h.context.request.body.payload.variant, "high");
  assert.equal(h.context.request.body.payload.references[0].path, "src/a.py");
  // Empty/metadata-only history and unrelated messages cannot erase the preview.
  h.state.messages = [native("old", [textPart("same prompt")]), native("other", [textPart("unrelated")])];
  assert.equal(h.context.conversationMessages().length, 3);
  const parts = [textPart("same prompt"), {...image, type: "file"},
    {type: "file", filename: "src/a.py", mime: "text/plain", url: "file:///project/src/a.py"}];
  h.state.messages.push(native("actual", parts)); // SSE beats admission response.
  assert.equal(h.context.conversationMessages().length, 3, "No duplicate bubble before receipt");
  assert.equal(h.outgoingMessages.size, 1, "Only admission can confirm its exact ID");
  h.composer.value = "same prompt"; // Retyping the same message is still a new draft.
  h.resolve(receipt("actual")); await pending;
  assert.equal(h.composer.value, "same prompt");
  assert.equal(h.context.conversationMessages().length, 3);
  assert.equal(h.outgoingMessages.size, 0);

  const slow = harness(); slow.composer.value = "with attachment"; slow.state.attachments = [image];
  const admission = slow.send(); await tick(); slow.resolve(receipt("exact")); await admission;
  assert.equal(slow.context.conversationMessages()[0].outgoing.nativeId, "exact");
  slow.state.messages = [native("exact", [])];
  assert.equal(slow.context.conversationMessages()[0].parts.length, 2);
  slow.state.messages[0].parts = [textPart("with attachment")];
  assert.equal(slow.context.conversationMessages()[0].parts.length, 2, "Keep images during partial SSE");
  slow.state.messages[0].parts.push({...image, type: "file"});
  assert.equal(slow.context.conversationMessages()[0].outgoing, undefined);
  assert.equal(slow.outgoingMessages.size, 0);
  slow.state.messages = []; assert.equal(slow.context.conversationMessages().length, 0,
    "Authoritative removal must not resurrect a confirmed preview");

  const failed = harness(); failed.composer.value = "@src/a.py original";
  failed.composer.references = [{path: "src/a.py", start: 0}]; failed.state.attachments = [image];
  const failure = failed.send(); await tick(); failed.reject(); await failure;
  assert.equal(failed.composer.value, "@src/a.py original");
  assert.equal(failed.composer.fileReferences[0].path, "src/a.py");
  assert.equal(failed.state.attachments[0].url, image.url);
  assert.equal(failed.outgoingMessages.size, 0); assert.equal(failed.state.sending, false);

  const newer = harness(); newer.composer.value = "recover me";
  const oldSend = newer.send(); await tick(); newer.composer.value = "new draft";
  newer.reject(); await oldSend;
  assert.equal(newer.composer.value, "new draft");
  const backup = [...newer.outgoingMessages.values()][0];
  assert.equal(newer.context.conversationMessages()[0].outgoing.error, "offline");
  assert.equal(newer.context.restoreOutgoingDraft(backup), false);
  newer.composer.value = ""; assert.equal(newer.context.restoreOutgoingDraft(backup), true);
  assert.equal(newer.composer.value, "recover me");

  const creation = harness(null); creation.composer.value = "first message";
  let releaseCreation;
  creation.context.ensureSessionForSend = () => new Promise(resolve => {releaseCreation = () => {
    creation.state.sessionId = "new"; resolve({projectId: "p", sessionId: "new"});
  };});
  const creating = creation.send();
  assert.equal(creation.renders[0][0].parts[0].text, "first message");
  assert.equal(creation.composer.value, ""); assert.equal(creation.context.request, undefined);
  releaseCreation(); await tick();
  assert.equal(creation.context.conversationMessages().length, 1);
  creation.state.projectId = "other"; creation.state.sessionId = "different";
  creation.composer.value = "other draft";
  assert.equal(creation.context.conversationMessages().length, 0, "Preview stays in its conversation");
  creation.reject(); await creating;
  assert.equal(creation.composer.value, "other draft");
  assert.equal(creation.drafts.get("p:new").text, "first message");
  assert.equal(creation.outgoingMessages.size, 0);

  const queued = harness(); queued.composer.value = "later";
  const enqueue = queued.send(true); await tick();
  assert.equal(queued.outgoingMessages.size, 0, "Queued drafts must not pretend to be in native history");
  assert.equal(queued.context.request.path, "p/s/queue");
  queued.resolve({items: []}); await enqueue;
})().catch(error => {console.error(error); process.exitCode = 1;});
''')
