"""Native token events stay current without crossing project/session boundaries."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sona_code.admin.models import native_provider_id
from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig, UpstreamModelConfig


def test_context_limit_uses_native_model_without_exposing_private_metadata(tmp_path):
    config = AppConfig(upstreams=[UpstreamConfig(
        name="demo", base_url="http://127.0.0.1:1",
        models=[UpstreamModelConfig(id="model", context_length=32000)],
    )], default_upstream="demo", model_settings={"source": "custom"})
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    provider_id = native_provider_id("demo")
    unavailable = False

    async def request(project, cfg, method, endpoint, **kwargs):
        assert endpoint == "/config/providers"
        if unavailable:
            raise HTTPException(502, "unavailable")
        return {"providers": [{"id": provider_id, "options": {"apiKey": "private-native-key"},
                "models": {"model": {"limit": {"context": 64000}, "headers": {"secret": "private-header"}}}},
                {"id": "external", "models": {"hidden": {"limit": {"context": 99999}}}}]}

    app.state.runtime.workspace.request = request
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        url = f"/__recorder/api/workspace/projects/{project_id}/models"
        response = client.get(url)
        assert response.status_code == 200
        providers = response.json()["providers"]
        assert len(providers) == 1  # Hidden native models remain hidden.
        assert providers[0]["models"]["model"]["limit"]["context"] == 64000
        assert "private-native-key" not in response.text
        assert "private-header" not in response.text
        unavailable = True
        assert client.get(url).json()["providers"][0]["models"]["model"]["limit"]["context"] == 32000


def test_native_usage_events_preserve_last_valid_sample_and_reject_stale_history():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace usage coverage")
    script = r'''
const fs = require('node:fs'), assert = require('node:assert/strict');
const source = fs.readFileSync('sona_code/web/static/workspace.js','utf8');
function el(tag, props={}, ...children) {
  return {tag,...props,children, get textContent() {return this.text || this.children.map(x => x.textContent).join('');},
    replaceChildren(...items) {this.children=items;}};
}
const document = {createTextNode: text => ({textContent:text})};
const statsLine = el('div');
const selectedModel = () => ({provider_id:'p',model_id:'m'});
const state = {projectId:'project',sessionId:'session',messages:[],statuses:{},providers:[{id:'p',models:{m:{limit:{context:10000}}}}]};
let messageVersion=0;
const messageInfoVersions=new Map();
const messagePartVersions=new Map(), creatingSessions=new Set();
const roundClocks=new Map();
eval(source.slice(source.indexOf('function workspaceConversationKey('), source.indexOf('function workspaceReadDraft(')));
eval(source.slice(source.indexOf('  function numeric('), source.indexOf('  function renderAttachments(')));
eval(source.slice(source.indexOf('  function applyMessageEvent('), source.indexOf('  function connectEvents(')));
const info = {id:'first',sessionID:'session',role:'assistant',providerID:'p',modelID:'m',time:{created:1},
  tokens:{input:1000,output:100,reasoning:50,cache:{read:200,write:50}}};
const event = info => ({type:'message.updated',properties:{info}});
applyMessageEvent('project',event(info));
renderStatsLine();
assert.match(statsLine.textContent,/上下文 1.4K \/ 10K · 14%/);
assert.match(statsLine.textContent,/本轮耗时 0:0:0/);
// OpenCode initializes the next assistant's tokens to zero while streaming.
const next = {...info,id:'next',time:{created:2},tokens:{input:0,output:0,reasoning:0,cache:{read:0,write:0}}};
applyMessageEvent('project',event(next));
renderStatsLine();
assert.match(statsLine.textContent,/上下文 1.4K/);
const version = messageVersion;
applyMessageEvent('other-project',event({...next,tokens:{input:9999}}));
applyMessageEvent('project',event({...next,sessionID:'other-session',tokens:{input:9999}}));
assert.equal(messageVersion,version); assert.match(statsLine.textContent,/14%/);
applyMessageEvent('project',event({...next,tokens:{input:2000,output:200,reasoning:100,cache:{read:500,write:200}}}));
renderStatsLine();
assert.match(statsLine.textContent,/上下文 3K \/ 10K · 30%/);
// Sona's default 256 * 1024 window must read as 256K, with the same base for usage.
state.providers[0].models.m = {source:'sona',limit:{context:262144}};
applyMessageEvent('project',event({...next,tokens:{input:65536,output:0,cache:{read:0,write:0}}}));
renderStatsLine();
assert.match(statsLine.textContent,/上下文 64K \/ 256K · 25%/);
state.providers[0].models.m = {limit:{context:10000}};
// Slow history fetched before the event must not roll back the fresh native usage.
(async () => {
  let resolveMessages;
  const pending = new Promise(resolve => {resolveMessages=resolve;});
  const api = path => path.endsWith('/messages') ? pending : Promise.resolve({});
  const alive=()=>true, sessionPath=(p,s)=>`${p}/${s}`, queueUpdateVersion=0;
  const statusVersions=new Map(), workspaceConversationKey=(p,s)=>JSON.stringify([p,s]);
  let selectedRefresh=null;
  const scheduleRefresh=()=>{}, renderSidebar=()=>{}, renderHeader=()=>{}, renderMain=()=>renderStatsLine();
  const scheduleSelectedRender=renderMain;
  const detail=()=>'', content={replaceChildren(){}};
  state.tab='chat'; state.sessionDetails=new Map(); state.projectStatuses=new Map();
  const refreshSelected=eval('(() => {' + source.slice(source.indexOf('  async function refreshSelected('),source.indexOf('  function selectProject(')) + ';return refreshSelected;})()');
  const refresh=refreshSelected();
  applyMessageEvent('project',event({...next,tokens:{input:4000,output:100,cache:{read:0,write:0}}}));
  resolveMessages([{info}]); await refresh;
  assert.match(statsLine.textContent,/上下文 4.1K \/ 10K · 41%/);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_round_clock_counts_wall_time_resets_on_admission_and_freezes_on_completion():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace round-clock coverage")
    script = r'''
const fs = require('node:fs'), assert = require('node:assert/strict');
const source = fs.readFileSync('sona_code/web/static/workspace.js', 'utf8');
eval(source.slice(source.indexOf('function workspaceConversationKey('), source.indexOf('function workspaceReadDraft(')));
assert.equal(workspaceRoundDuration(0), '0:0:0');
assert.equal(workspaceRoundDuration(3935999), '1:5:35');
assert.equal(workspaceRoundDuration(86400000), '24:0:0');
assert.equal(workspaceRoundDuration(-1000), '0:0:0');
const user = (id, created) => ({info: {id, role: 'user', time: {created}}, parts: []});
const reply = (id, parentID, created, completed) => ({info: {id, parentID, role: 'assistant',
  time: {created, ...(completed == null ? {} : {completed})}}, parts: []});
const old = [user('u1', 1000), reply('a1', 'u1', 2000, 9000)];
assert.equal(workspaceRoundElapsed([], {type: 'idle'}, null, null, 50000), 0);
assert.equal(workspaceRoundElapsed(old, {type: 'idle'}, null, null, 50000), 8000);
// Reset immediately, even though the prior assistant still remains in history.
const clock = {startedAt: 10000, baselineId: 'u1'};
assert.equal(workspaceRoundElapsed(old, {type: 'idle'}, null, clock, 10000), 0);
assert.equal(workspaceRoundElapsed(old, {type: 'idle'}, null, clock, 12000), 2000);
assert.equal(clock.endedAt, undefined);
// Native timestamps start after admission; waiting and tool continuations count too.
const messages = [...old, user('u2', 11000), reply('a2', 'u2', 11500, 13000),
  reply('a3', 'u2', 14000)];
assert.equal(workspaceRoundElapsed(messages, {type: 'busy'}, null, clock, 16000), 6000);
assert.equal(clock.userId, 'u2');
assert.equal(workspaceRoundElapsed(messages, {type: 'retry'}, null, clock, 17000), 7000);
messages.at(-1).info.time.completed = 20000;
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, null, clock, 22000), 10000);
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, null, clock, 40000), 10000);
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, null, null, 40000), 9000);
// A queued new turn keeps counting when the preceding running turn ends.
const waiting = {startedAt: 18000, baselineId: 'u2', queueId: 'q3'};
const queue = {items: [{id: 'q3', status: 'pending'}]};
assert.equal(workspaceRoundElapsed(messages, {type: 'busy'}, queue, waiting, 18000), 0);
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, queue, waiting, 22000), 4000);
assert.equal(waiting.endedAt, undefined);
queue.items[0] = {id: 'q3', status: 'sending', message_id: 'u3'};
messages.push(user('u3', 23000), reply('a4', 'u3', 24000, 29000));
assert.equal(workspaceRoundElapsed(messages, {type: 'busy'}, queue, waiting, 25000), 7000);
assert.equal(waiting.userId, 'u3');
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, {items: []}, waiting, 30000), 11000);
// Per-conversation clocks do not reset each other when switching projects/sessions.
const clocks = new Map([[workspaceConversationKey('p1', 's'), clock],
  [workspaceConversationKey('p2', 's'), waiting]]);
assert.equal(workspaceRoundElapsed(messages.slice(0, 5), {type: 'idle'}, null,
  clocks.get(workspaceConversationKey('p1', 's')), 60000), 10000);
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, null,
  clocks.get(workspaceConversationKey('p2', 's')), 60000), 11000);
// Stop/error may end before a completed assistant message arrives.
const stopped = {startedAt: 30000, baselineId: 'u3'};
messages.push(user('u4', 31000), reply('a5', 'u4', 31500));
assert.equal(workspaceRoundElapsed(messages, {type: 'busy'}, null, stopped, 33000), 3000);
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, null, stopped, 35000), 5000);
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, null, stopped, 50000), 5000);
// A later turn sent from another client supersedes a finished local clock.
messages.at(-1).info.time.completed = 36000;
assert.equal(workspaceRoundElapsed(messages, {type: 'idle'}, null, waiting, 50000), 5000);
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)
