"""Website login handoff and scene catalog use existing XT APIs."""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from sona_code.admin.models import route_token
from sona_code.app import create_app
from sona_code.config import default_config
from sona_code.proxy.handler import _build_forward_headers
from sona_code.proxy.router import build_upstream_url, resolve_upstream
from sona_code.sona_site import SiteSession, SonaSiteManager


@pytest.mark.asyncio
async def test_catalog_uses_frontend_origin_and_scene_keys(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.url.host == "sona.example.test"
        assert request.headers["Authorization"] == "site-token"
        assert request.headers["X-B3-BusinessId"] == "LZ2103SONA"
        bodies = {
            "/api/v1/auth/current-user": {"id": 7, "userName": "用户", "token": "extra-secret"},
            "/api/v1/scenes": [{"id": 12, "name": "场景 A", "status": 1},
                                {"id": 13, "name": "停用场景", "status": 0}],
            "/api/v1/scenes/12/integration-info": {
                "sceneName": "场景 A", "apiKey": "scene-secret",
                "availableModels": [{"modelName": "model-a", "modalities": ["image"]}],
            },
        }
        return httpx.Response(200, json={"returnCode": "SUC0000", "body": bodies[request.url.path]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("sona_code.sona_site.httpx.AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    session = await SonaSiteManager().load_catalog("https://sona.example.test", "site-token", "uat")
    assert [request.url.path for request in calls] == [
        "/api/v1/auth/current-user", "/api/v1/scenes", "/api/v1/scenes/12/integration-info"]
    assert session.providers[0].base_url == "https://sona.example.test/api/v1"
    assert session.providers[0].api_key == "scene-secret"
    assert session.providers[0].models[0].id == "model-a"
    assert session.user == {"id": 7, "userName": "用户"}
    cfg = default_config().model_copy(update={"upstreams": session.providers})
    path = f"/managed/{route_token(session.providers[0].name)}/{route_token('model-a')}/chat/completions"
    upstream, tail = resolve_upstream(path, cfg)
    assert build_upstream_url(upstream.base_url, tail, "") == "https://sona.example.test/api/v1/chat/completions"
    headers = dict(_build_forward_headers(Request({"type": "http", "path": path,
        "headers": [(b"authorization", b"Bearer wrong")]}), upstream))
    assert headers["authorization"] == "Bearer scene-secret"
    assert headers["X-B3-BusinessId"] == "LZ2103SONA"


def test_login_callback_is_one_time_and_source_status_hides_secrets(tmp_path):
    cfg = default_config()
    app = create_app(cfg, str(tmp_path / "config.json"))

    async def catalog(origin, token, environment):
        assert origin == cfg.sona_site.prod_url
        assert environment == "prod"
        assert token == "site-secret"
        return SiteSession(token, {"id": 7, "userName": "用户"}, [], 0)

    app.state.runtime.sona_site.load_catalog = catalog
    with TestClient(app) as client:
        prefix = "/__recorder/api/models"
        source = client.get(prefix + "/source").json()
        assert source["source"] == "sona"
        assert source["prod_url"] == "https://sona.passoa.cmbchina.cn"
        start = client.post(prefix + "/sona/login/start").json()["url"]
        params = parse_qs(urlsplit(start).fragment.partition("?")[2])
        state = params["state"][0]
        assert params["callback"] == ["http://127.0.0.1:8117/__recorder/api/models/sona/callback"]
        callback = client.post(prefix + "/sona/callback", data={"state": state, "token": "site-secret"})
        assert callback.status_code == 200
        assert client.get(prefix + "/source").json()["connected"] is True
        assert "site-secret" not in client.get(prefix + "/source").text
        assert client.post(prefix + "/sona/callback", data={"state": state, "token": "site-secret"}).status_code == 400
        config_path = tmp_path / "config.json"
        assert not config_path.exists() or "site-secret" not in config_path.read_text()


def test_site_environment_switch_keeps_separate_login(tmp_path):
    app = create_app(default_config(), str(tmp_path / "config.json"))
    with TestClient(app) as client:
        prefix = "/__recorder/api/models/source"
        source = client.get(prefix).json()
        app.state.runtime.sona_site.set_session("prod", source["prod_url"],
                                                SiteSession("secret", {"id": 1}, [], 0))
        assert client.get(prefix).json()["connected"]
        source["environment"] = "uat"
        assert client.put(prefix, json=source).status_code == 200
        assert client.get(prefix).json()["connected"] is False
        source["environment"] = "prod"
        assert client.put(prefix, json=source).status_code == 200
        assert client.get(prefix).json()["connected"] is True


def test_refresh_fetches_new_subscriptions_and_updates_native_config(tmp_path, monkeypatch):
    cfg = default_config()
    cfg.recording.dir = str(tmp_path / "records")
    app = create_app(cfg, str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    platform_calls = []
    native_calls = []
    generation = 0

    def respond(request):
        platform_calls.append(request)
        assert request.headers["Cache-Control"] == "no-cache, no-store"
        assert request.url.params["_sona_refresh"]
        bodies = {
            "/api/v1/auth/current-user": {"id": 7},
            "/api/v1/scenes": [{"id": 12, "status": 1}],
            "/api/v1/scenes/12/integration-info": {
                "apiKey": "test-scene-key", "sceneName": "Test scene",
                "availableModels": [{"modelName": "first"}, *(
                    [{"modelName": "new-subscription"}] if generation else [])],
            },
        }
        return httpx.Response(200, json={"returnCode": "SUC0000", "body": bodies[request.url.path]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("sona_code.sona_site.httpx.AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))

    async def native(*args, **kwargs):
        native_calls.append(args)
        raise AssertionError("Website model picker must not wait for OpenCode startup")

    app.state.runtime.workspace.request = native
    with TestClient(app) as client:
        prefix = "/__recorder/api/models"
        start = client.post(prefix + "/sona/login/start").json()["url"]
        state = parse_qs(urlsplit(start).fragment.partition("?")[2])["state"][0]
        assert client.post(prefix + "/sona/callback", data={"state": state, "token": "test-token"}).status_code == 200
        before = client.get(prefix + "/source").json()
        assert [m["id"] for m in before["providers"][0]["models"]] == ["first"]
        generation = 1
        result = client.post(prefix + "/sona/refresh")
        assert result.status_code == 200
        assert [m["id"] for m in result.json()["providers"][0]["models"]] == ["first", "new-subscription"]
        assert result.json()["updated_at"] >= before["updated_at"]
        assert len(platform_calls) == 6
        assert len({request.url.params["_sona_refresh"] for request in platform_calls}) == 6
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        picker = client.get(f"/__recorder/api/workspace/projects/{project_id}/models")
        assert picker.status_code == 200
        assert "new-subscription" in picker.json()["providers"][0]["models"]
        assert not native_calls
        from sona_code.admin.models import compile_providers
        providers = compile_providers(app.state.runtime.provider_config())
        assert any("new-subscription" in provider["models"] for provider in providers.values())
        assert "test-token" not in picker.text
        assert "test-scene-key" not in result.text
