"""Native token events stay current without crossing project/session boundaries."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from llm_api_proxy_recorder.admin.models import native_provider_id
from llm_api_proxy_recorder.app import create_app
from llm_api_proxy_recorder.config import AppConfig, UpstreamConfig, UpstreamModelConfig


def test_context_limit_uses_native_model_without_exposing_private_metadata(tmp_path):
    config = AppConfig(upstreams=[UpstreamConfig(
        name="demo", base_url="http://127.0.0.1:1",
        models=[UpstreamModelConfig(id="model", context_length=32000)],
    )], default_upstream="demo")
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
const source = fs.readFileSync('llm_api_proxy_recorder/web/static/workspace.js','utf8');
function el(tag, props={}, ...children) {
  return {tag,...props,children, get textContent() {return this.text || this.children.map(x => x.textContent).join('');},
    replaceChildren(...items) {this.children=items;}};
}
const document = {createTextNode: text => ({textContent:text})};
const statsLine = el('div');
const selectedModel = () => ({provider_id:'p',model_id:'m'});
const state = {projectId:'project',sessionId:'session',messages:[],statuses:{},providers:[{id:'p',models:{m:{limit:{context:10000}}}}]};
let messageVersion=0;
eval(source.slice(source.indexOf('  function numeric('), source.indexOf('  function renderAttachments(')));
eval(source.slice(source.indexOf('  function applyMessageEvent('), source.indexOf('  function connectEvents(')));
const info = {id:'first',sessionID:'session',role:'assistant',providerID:'p',modelID:'m',time:{created:1},
  tokens:{input:1000,output:100,reasoning:50,cache:{read:200,write:50}}};
const event = info => ({type:'message.updated',properties:{info}});
applyMessageEvent('project',event(info));
assert.match(statsLine.textContent,/上下文 1.4K \/ 10K · 14%/);
// OpenCode initializes the next assistant's tokens to zero while streaming.
const next = {...info,id:'next',time:{created:2},tokens:{input:0,output:0,reasoning:0,cache:{read:0,write:0}}};
applyMessageEvent('project',event(next));
assert.match(statsLine.textContent,/上下文 1.4K/);
const version = messageVersion;
applyMessageEvent('other-project',event({...next,tokens:{input:9999}}));
applyMessageEvent('project',event({...next,sessionID:'other-session',tokens:{input:9999}}));
assert.equal(messageVersion,version); assert.match(statsLine.textContent,/14%/);
applyMessageEvent('project',event({...next,tokens:{input:2000,output:200,reasoning:100,cache:{read:500,write:200}}}));
assert.match(statsLine.textContent,/上下文 3K \/ 10K · 30%/);
// Slow history fetched before the event must not roll back the fresh native usage.
(async () => {
  let resolveMessages;
  const pending = new Promise(resolve => {resolveMessages=resolve;});
  const api = path => path.endsWith('/messages') ? pending : Promise.resolve({});
  const alive=()=>true, sessionPath=(p,s)=>`${p}/${s}`, queueUpdateVersion=0;
  const statusVersions=new Map(), workspaceConversationKey=(p,s)=>JSON.stringify([p,s]);
  let refreshing=false, refreshRequested=false, scrollToLatestOnLoad=false;
  const scheduleRefresh=()=>{}, renderSidebar=()=>{}, renderHeader=()=>{}, renderMain=()=>renderStatsLine();
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
