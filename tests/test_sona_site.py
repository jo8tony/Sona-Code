"""Website login handoff and scene catalog use existing XT APIs."""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from llm_api_proxy_recorder.admin.models import route_token
from llm_api_proxy_recorder.app import create_app
from llm_api_proxy_recorder.config import default_config
from llm_api_proxy_recorder.proxy.handler import _build_forward_headers
from llm_api_proxy_recorder.proxy.router import build_upstream_url, resolve_upstream
from llm_api_proxy_recorder.sona_site import SiteSession, SonaSiteManager


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
    monkeypatch.setattr("llm_api_proxy_recorder.sona_site.httpx.AsyncClient",
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
