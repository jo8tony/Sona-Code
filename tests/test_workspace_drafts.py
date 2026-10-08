"""Browser draft partitioning and stale refresh isolation without DOM emulation."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_drafts_restore_per_project_session_and_ignore_stale_refresh():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for workspace draft coverage')
    script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('sona_code/web/static/workspace.js', 'utf8');
const storage = new Map();
global.sessionStorage = {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)};
vm.runInThisContext(source.slice(0, source.indexOf('function renderWorkspace(')));
let draftTimer = null, draftContextReady = false;
let editor = {text: '', references: []};
const composer = {snapshot: () => structuredClone(editor), restore: draft => {editor = structuredClone(draft || {text: '', references: []});}, get fileReferences() { return (editor.references || []).map(r => ({path: r.path})); }};
const state = {projectId: 'p1', sessionId: 'same', attachments: []};
const updateSkillInput = () => {}, renderAttachments = () => {};
vm.runInThisContext(source.slice(source.indexOf('  function saveDraft()'), source.indexOf('  const attachmentList =')));
const firstKey = workspaceConversationKey('p1', 'same');
workspaceWriteDraft(firstKey, {text:'saved before page reload', references:[], attachments:[]});
saveDraft(); // Initial empty DOM must not erase the stored selection's draft.
assert.equal(workspaceReadDraft(firstKey).text, 'saved before page reload');
restoreDraft();
assert.equal(editor.text, 'saved before page reload');
editor = {text:'多行草稿\n@src/file.js 与 @src/file.js', references:[{path:'src/file.js', start:5},{path:'src/file.js',start:22}]};
state.attachments = [{id:'image', filename:'demo.png', mime:'image/png', url:'data:image/png;base64,AAAA'}];
saveDraft();
const expected = structuredClone(editor);
state.projectId = 'p2'; restoreDraft();
assert.equal(editor.text, ''); assert.deepEqual(state.attachments, []);
editor.text = 'other project, same native session ID'; saveDraft();
state.projectId = 'p1'; state.sessionId = 'second'; restoreDraft();
assert.equal(editor.text, '');
editor.text = 'second conversation'; saveDraft();
state.sessionId = 'same'; restoreDraft();
assert.equal(editor.text, expected.text); assert.deepEqual(editor.references, expected.references);
assert.equal(state.attachments[0].id, 'image');
workspaceDrafts.clear(); restoreDraft(); // Simulate reading persisted storage on refresh.
assert.equal(editor.text, expected.text);
sessionStorage.setItem = () => {throw new Error('quota');};
editor.text = 'large attachment still stays in memory'; saveDraft();
state.sessionId = 'second'; restoreDraft();
state.sessionId = 'same'; restoreDraft();
assert.equal(editor.text, 'large attachment still stays in memory');
workspaceWriteDraft(firstKey, null);
assert.equal(workspaceReadDraft(firstKey), undefined);
assert.equal(workspaceReadDraft(workspaceConversationKey('p2','same')).text, 'other project, same native session ID');
// A history request started for A must never update the current B conversation.
(async () => {
  let selectedRefresh = null;
  let lastSelectedRefresh = 0;
  let resolveMessages;
  const pending = new Promise(resolve => {resolveMessages = resolve;});
  const api = path => path.endsWith('/messages') ? pending : Promise.resolve({});
  const alive = () => true, sessionPath = (p,s) => `${p}/${s}`;
  const queueUpdateVersion = 0, statusVersions = new Map(), messageVersion = 0;
  const creatingSessions = new Set(), messagePartVersions = new Map();
  const scheduleRefresh = () => {}, renderSidebar = () => {}, renderHeader = () => {};
  let rendered = 0; const renderMain = () => {rendered++;};
  const scheduleSelectedRender = renderMain;
  const content = {replaceChildren: () => {throw new Error('wrong conversation');}};
  state.tab='chat'; state.messages=[];
  const refreshSource = source.slice(source.indexOf('  async function refreshSelected()'), source.indexOf('  function selectProject('));
  const refreshSelected = eval('(() => {' + refreshSource + '; return refreshSelected;})()');
  const refresh = refreshSelected();
  state.projectId='p2'; state.sessionId='second'; state.messages=[{own:'B'}];
  resolveMessages([{own:'A'}]); await refresh;
  assert.deepEqual(state.messages,[{own:'B'}]); assert.equal(rendered,0);
})().catch(error => {console.error(error); process.exitCode=1;});
'''
    subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1], check=True)
