"""Catalog persistence, credentials, native config and managed routing regressions."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.admin.models import compile_providers, native_provider_id, route_token
from sona_code.config import AppConfig, UpstreamConfig, UpstreamModelConfig, ModelSettings, ModelChoice
from sona_code.proxy.router import resolve_upstream
from sona_code.proxy.handler import _build_forward_headers
from sona_code.workspace.manager import WorkspaceError, WorkspaceManager


@pytest.fixture
def cfg(tmp_path):
    return AppConfig(upstreams=[UpstreamConfig(name="company", base_url="http://example.com/v1", api_key="provider-secret",
        models=[UpstreamModelConfig(id="model/one", api_key="model-secret", context_length=64000, output_length=8000),
                UpstreamModelConfig(id="two", context_length=32000, output_length=4000)])], default_upstream="company",
        recording={"dir": str(tmp_path / "records")}, model_settings={"source": "custom"})


def test_catalog_keys_revision_and_partial_settings(cfg, tmp_path):
    path = tmp_path / "config.json"
    app = create_app(cfg, str(path))
    with TestClient(app) as client:
        url = "/__recorder/api/models/config"
        original = client.get(url).json()
        assert original["providers"][0]["api_key"] == "provider-secret"
        assert original["providers"][0]["models"][0]["api_key"] == "model-secret"
        assert original["providers"][0]["models"][1]["api_key"] == ""
        settings = client.get("/__recorder/api/settings").text
        assert "provider-secret" not in settings and "model-secret" not in settings
        assert original["providers"][0]["models"][0]["key_source"] == "model"
        original["providers"][0]["display_name"] = "公司"
        original["default_model"] = {"provider": "company", "model": "two"}
        response = client.put(url, json=original)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["revision"] != original["revision"]
        assert saved["providers"][0]["api_key"] == "provider-secret"
        assert saved["providers"][0]["models"][0]["api_key"] == "model-secret"
        assert app.state.runtime.config.upstreams[0].models[0].api_key == "model-secret"
        assert client.put(url, json=original).status_code == 409
        assert client.put("/__recorder/api/settings", json={"outbound": {"proxy_url": ""}}).status_code == 200
        assert app.state.runtime.config.upstreams[0].display_name == "公司"
        saved["providers"][0]["models"][0]["api_key"] = ""
        cleared = client.put(url, json=saved)
        assert cleared.status_code == 200
        assert cleared.json()["providers"][0]["models"][0]["api_key"] == ""
        assert cleared.json()["providers"][0]["models"][0]["key_source"] == "provider"
        cleared = cleared.json()
        cleared["providers"][0]["api_key"] = ""
        result = client.put(url, json=cleared).json()
        assert result["providers"][0]["api_key"] == ""
        assert result["providers"][0]["models"][0]["key_source"] == "none"
        assert path.exists()


def test_validation_no_secret_leak_and_empty_catalog(cfg, tmp_path):
    app = create_app(cfg, str(tmp_path / "cfg.json"))
    with TestClient(app) as client:
        url = "/__recorder/api/models/config"
        data = client.get(url).json()
        data["providers"][0]["api_key"] = {"secret": "must-not-leak"}
        response = client.put(url, json=data)
        assert response.status_code == 422 and "must-not-leak" not in response.text
        data = client.get(url).json()
        data["providers"][0]["models"].append({"id": "new", "api_key": "secret-with-invalid-model"})
        response = client.put(url, json=data)
        assert response.status_code == 422 and "secret-with-invalid-model" not in response.text
        for field, value in [("name", []), ("models", None), ("models", [{"id": []}])]:
            invalid = client.get(url).json()
            invalid["providers"][0][field] = value
            assert client.put(url, json=invalid).status_code == 422
        data.update(providers=[], default_upstream="", default_model=None)
        data["revision"] = client.get(url).json()["revision"]
        assert client.put(url, json=data).status_code == 200
        assert client.post("/v1/chat/completions", json={}).status_code == 503


def test_compiler_defaults_variants_and_direct_keys(cfg):
    p = cfg.upstreams[0]
    p.models[0].reasoning = True
    p.models[0].reasoning_efforts = ["low", "high"]
    p.models[0].default_effort = "high"
    p.models[0].api_type = "responses"
    compiled = compile_providers(cfg)[native_provider_id("company")]
    assert compiled["options"]["apiKey"] == ""
    model = compiled["models"]["model/one"]
    assert "model-secret" not in json.dumps(compiled)
    assert model["provider"]["npm"] == "@ai-sdk/openai"
    assert model["provider"]["api"].endswith(route_token("model/one"))
    assert model["variants"]["medium"] == {"disabled": True}
    assert model["options"] == {"reasoningEffort": "high"}
    p.route_through_proxy = False
    direct = compile_providers(cfg)[native_provider_id("company")]["models"]
    assert direct["model/one"]["headers"]["Authorization"] == "Bearer model-secret"
    assert direct["two"]["headers"]["Authorization"] == "Bearer provider-secret"
    assert "reasoningEffort" not in direct["two"]["options"]
    p.api_key = ""
    assert compile_providers(cfg)[native_provider_id("company")]["models"]["two"]["headers"]["Authorization"] == ""


def test_managed_routes_do_not_fallback_or_parse_body(cfg):
    from starlette.requests import Request
    for model, key in [("model/one", "model-secret"), ("two", "provider-secret")]:
        path = f"/managed/{route_token('company')}/{route_token(model)}/chat/completions"
        upstream, tail = resolve_upstream(path, cfg)
        assert tail == "/chat/completions"
        request = Request({"type": "http", "path": path, "headers": [(b'authorization', b'Bearer wrong-key')]})
        assert dict(_build_forward_headers(request, upstream))["authorization"] == "Bearer " + key
    cfg.upstreams[0].api_key = ""
    path = f"/managed/{route_token('company')}/{route_token('two')}/responses"
    upstream, _ = resolve_upstream(path, cfg)
    headers = dict(_build_forward_headers(Request({"type": "http", "path": path, "headers": []}), upstream))
    assert set(headers) == {"Sona-Code-Client"}
    with pytest.raises(HTTPException) as error:
        resolve_upstream("/managed/missing/model/chat/completions", cfg)
    assert error.value.status_code == 404


def test_workspace_catalog_defaults_filter_and_sanitize(cfg, tmp_path):
    app = create_app(cfg, str(tmp_path / "cfg.json"))
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    calls = []
    async def fake(project, cfg, method, endpoint, **kwargs):
        calls.append((endpoint, kwargs.get("body")))
        if endpoint == "/config/providers":
            return {"providers": [{"id": "native", "options": {"apiKey": "native-secret"}, "models": {
                "one": {"name": "原生", "headers": {"Authorization": "native-secret"},
                        "variants": {"high": {"apiKey": "native-secret"}}}}}]}
        if endpoint == "/provider":
            return {"connected": ["native"]}
        return {"ok": True}
    app.state.runtime.workspace.request = fake
    with TestClient(app) as client:
        pid = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        base = f"/__recorder/api/workspace/projects/{pid}"
        models = client.get(base + "/models").json()
        assert models["providers"][0]["id"] == native_provider_id("company")
        assert calls == [("/config/providers", None)]
        response = client.post(base + "/sessions/session-one/prompt", json={"text": "hello"})
        assert response.status_code == 200
        assert calls[-1][1]["model"] == models["default_model"]
        assert client.post(base + "/sessions/session-one/prompt", json={"text": "hello", "variant": "high"}).status_code == 400
        cfg.model_settings.show_native_models = True
        response = client.get(base + "/models")
        assert "native-secret" not in response.text
        assert len(response.json()["providers"]) == 1


@pytest.mark.asyncio
async def test_running_status_and_dispatch_block_changes():
    manager = WorkspaceManager()
    changed = []
    async def apply():
        changed.append(True)
        return {}
    async with manager.task_dispatch():
        with pytest.raises(WorkspaceError) as error:
            await manager.update_configuration(apply)
        assert error.value.status == 409
    for status in ("busy", "retry"):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"s": {"type": status}})), base_url="http://localhost")
        manager._servers["project"] = SimpleNamespace(process=SimpleNamespace(poll=lambda: None), client=client)
        with pytest.raises(WorkspaceError):
            await manager.update_configuration(apply)
        await client.aclose()
    assert not changed


def test_legacy_migration_preserves_routes_and_modalities():
    cfg = AppConfig.model_validate({"upstreams": [{"name": "old", "base_url": "http://localhost/v1",
        "models": [{"id": "old-model", "input_modalities": ["image", "pdf"]}]}], "default_upstream": "old",
        "terminal": {"route_through_proxy": False}})
    assert not cfg.upstreams[0].route_through_proxy
    assert cfg.upstreams[0].models[0].context_length is None
    assert cfg.upstreams[0].models[0].input_modalities == ["image", "pdf"]


def test_pending_listener_and_environment_secrets_survive_model_save(cfg, tmp_path):
    cfg.terminal.inject_env = {"OPENAI_API_KEY": "environment-secret", "TERM": "xterm"}
    path = tmp_path / "cfg.json"
    app = create_app(cfg, str(path))
    with TestClient(app) as client:
        settings = client.get("/__recorder/api/settings").json()["config"]
        assert "environment-secret" not in json.dumps(settings)
        settings["server"]["port"] = 8999
        result = client.put("/__recorder/api/settings", json=settings)
        assert result.status_code == 200 and result.json()["restart_required"]
        catalog = client.get("/__recorder/api/models/config").json()
        catalog["providers"][0]["display_name"] = "changed"
        assert client.put("/__recorder/api/models/config", json=catalog).status_code == 200
        assert json.loads(path.read_text())["server"]["port"] == 8999
        assert app.state.runtime.config.server.port == 8117
        assert app.state.runtime.config.terminal.inject_env["OPENAI_API_KEY"] == "environment-secret"
        assert client.get("/__recorder/api/settings").json()["config"]["server"]["port"] == 8999


@pytest.mark.asyncio
async def test_idle_configuration_change_recycles_servers_without_losing_history(monkeypatch):
    manager = WorkspaceManager()
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"s": {"type": "idle"}})), base_url="http://localhost")
    process = SimpleNamespace(poll=lambda: None)
    from sona_code.workspace.manager import OpenCodeServer
    manager._servers["project"] = OpenCodeServer(process, client, 1234)
    stopped = []
    async def stop(process):
        stopped.append(process)
    monkeypatch.setattr(manager, "_stop_process", stop)
    async def apply():
        return {"saved": True}
    assert await manager.update_configuration(apply) == {"saved": True}
    assert stopped == [process] and not manager._servers and client.is_closed


def test_catalog_busy_error_preserves_disk_and_revision(cfg, tmp_path):
    path = tmp_path / "cfg.json"
    app = create_app(cfg, str(path))
    with TestClient(app) as client:
        original = client.get("/__recorder/api/models/config").json()
        app.state.runtime.workspace._task_requests = 1
        original["providers"][0]["api_key"] = "replacement-secret"
        response = client.put("/__recorder/api/models/config", json=original)
        assert response.status_code == 409 and "replacement-secret" not in response.text
        assert not path.exists()
        assert client.get("/__recorder/api/models/config").json()["revision"] == original["revision"]
        app.state.runtime.workspace._task_requests = 0


def test_unchanged_legacy_model_can_save_but_edit_requires_limits(tmp_path):
    cfg = AppConfig(upstreams=[UpstreamConfig(name="old", base_url="http://localhost", models=[UpstreamModelConfig(id="old")])], default_upstream="old")
    app = create_app(cfg, str(tmp_path / "cfg.json"))
    with TestClient(app) as client:
        data = client.get("/__recorder/api/models/config").json()
        saved = client.put("/__recorder/api/models/config", json=data)
        assert saved.status_code == 200
        data = saved.json()
        data["providers"][0]["models"][0]["display_name"] = "edited"
        assert client.put("/__recorder/api/models/config", json=data).status_code == 422


def test_request_schema_validation_never_echoes_credentials(cfg, tmp_path):
    app = create_app(cfg, str(tmp_path / "cfg.json"))
    with TestClient(app) as client:
        response = client.put("/__recorder/api/models/config", json={"providers": "secret-in-invalid-body"})
        assert response.status_code == 422 and "secret-in-invalid-body" not in response.text


def test_provider_key_replacement_persists_and_inherits_after_reload(cfg, tmp_path):
    from sona_code.config import load_config
    path = tmp_path / "saved.json"
    app = create_app(cfg, str(path))
    with TestClient(app) as client:
        url = "/__recorder/api/models/config"
        draft = client.get(url).json()
        draft["providers"][0]["api_key"] = "replacement-provider-key"
        saved = client.put(url, json=draft)
        assert saved.status_code == 200
        assert saved.json()["providers"][0]["has_api_key"]
        assert saved.json()["providers"][0]["api_key"] == "replacement-provider-key"
        assert client.get(url).json()["providers"][0]["api_key"] == "replacement-provider-key"
        assert client.put(url, json=saved.json()).status_code == 200
    restored = load_config(path)
    assert restored.upstreams[0].api_key == "replacement-provider-key"
    route = f"/managed/{route_token('company')}/{route_token('two')}/chat/completions"
    provider, _ = resolve_upstream(route, restored)
    assert provider.api_key == "replacement-provider-key"
