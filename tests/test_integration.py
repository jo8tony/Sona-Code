"""集成测试：uvicorn 真实 TCP 起代理 + mock 上游（httpx ASGITransport 不支持真流式）。

共享 stack fixture（module 级）串行产生记录；管理端点用例依赖前面用例的数据。
"""

import asyncio
import base64
import json
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from sona_code.app import create_app
from sona_code.config import AppConfig, RecordingConfig, ServerConfig, UpstreamConfig
from sona_code.recording.sse import SSEParser
from sona_code.recording.store import CallStore

ADMIN = "/__recorder"
UPSTREAM_KEY = "sk-upstream-main-9x8y7z"

CONTENT_PIECES = ["你好", "，", "世界", "！@", "Done"]
FULL_CONTENT = "".join(CONTENT_PIECES)
STREAM_USAGE = {
    "prompt_tokens": 21, "completion_tokens": 5, "total_tokens": 26,
    "prompt_cache_hit_tokens": 3,
}
NONSTREAM_BODY = {
    "id": "chatcmpl-mock-001",
    "object": "chat.completion",
    "model": "mock-model",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "你好，世界！"},
         "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
}


def _sse(obj) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


# ------------------------------------------------------------ mock 上游路由
async def chat_completions(request: Request):
    try:
        payload = json.loads(await request.body())
    except Exception:
        payload = {}
    if payload.get("stream"):
        async def gen():
            yield _sse({"id": "cmpl-stream", "model": "mock-model",
                        "choices": [{"index": 0, "delta": {"role": "assistant"}}]})
            await asyncio.sleep(0.02)
            for piece in CONTENT_PIECES:
                yield _sse({"id": "cmpl-stream", "model": "mock-model",
                            "choices": [{"index": 0, "delta": {"content": piece}}]})
                await asyncio.sleep(0.02)
            yield _sse({"id": "cmpl-stream", "model": "mock-model",
                        "choices": [{"index": 0, "delta": {}}], "usage": STREAM_USAGE})
            await asyncio.sleep(0.02)
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")
    return JSONResponse(NONSTREAM_BODY)


async def err(request: Request):
    return JSONResponse(
        {"error": {"message": "Invalid API key", "type": "invalid_request_error"}},
        status_code=401,
    )


async def stream_broken(request: Request):
    """SSE 流发到一半异常终止（模拟上游断连/网络错误）。"""
    async def gen():
        yield _sse({"id": "cmpl-broken", "model": "mock-model",
                    "choices": [{"index": 0, "delta": {"role": "assistant"}}]})
        await asyncio.sleep(0.02)
        for piece in CONTENT_PIECES[:2]:  # 只发出部分内容
            yield _sse({"id": "cmpl-broken", "model": "mock-model",
                        "choices": [{"index": 0, "delta": {"content": piece}}]})
            await asyncio.sleep(0.02)
        raise RuntimeError("上游连接被重置")
    return StreamingResponse(gen(), media_type="text/event-stream")


async def echo(request: Request):
    body = await request.body()
    return JSONResponse({
        "server": request.app.state.server_name,
        "method": request.method,
        "path": request.url.path,
        "query": request.url.query,
        "body_b64": base64.b64encode(body).decode("ascii"),
        "authorization": request.headers.get("authorization"),
        "x_api_key": request.headers.get("x-api-key"),
        "sona_code_client": request.headers.get("sona-code-client"),
        "content_type": request.headers.get("content-type"),
    })


def make_mock_app(name: str) -> Starlette:
    app = Starlette(routes=[
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/err", err, methods=["GET", "POST"]),
        Route("/v1/echo", echo, methods=["GET", "POST", "PUT"]),
        Route("/v1/stream-broken", stream_broken, methods=["POST"]),
    ])
    app.state.server_name = name
    return app


# --------------------------------------------------------- uvicorn 线程封装
class ServerThread:
    def __init__(self, app, name: str):
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True, name=name)

    def start(self) -> int:
        self._thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if self._server.started:
                return self._server.servers[0].sockets[0].getsockname()[1]
            if not self._thread.is_alive():
                raise RuntimeError("uvicorn 线程异常退出")
            time.sleep(0.02)
        raise RuntimeError("uvicorn 启动超时")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _dead_port() -> int:
    """取一个已释放端口：连接必然被拒（用于上游不可达场景）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    records_dir = tmp_path_factory.mktemp("records")
    config_path = tmp_path_factory.mktemp("cfg") / "config.json"

    mock1 = ServerThread(make_mock_app("mock1"), "mock1")
    mock2 = ServerThread(make_mock_app("mock2"), "mock2")
    port1 = mock1.start()
    port2 = mock2.start()

    cfg = AppConfig(
        server=ServerConfig(),
        upstreams=[
            UpstreamConfig(name="main", base_url=f"http://127.0.0.1:{port1}",
                           api_key=UPSTREAM_KEY, key_strategy="replace"),
            UpstreamConfig(name="second", base_url=f"http://127.0.0.1:{port2}",
                           key_strategy="keep"),
            UpstreamConfig(name="passthru", base_url=f"http://127.0.0.1:{port2}"),  # 默认 keep
        ],
        default_upstream="main",
        recording=RecordingConfig(dir=str(records_dir)),
    )
    proxy = ServerThread(create_app(cfg, config_path=str(config_path)), "proxy")
    proxy_port = proxy.start()

    yield {
        "proxy": f"http://127.0.0.1:{proxy_port}",
        "mock1": f"http://127.0.0.1:{port1}",
        "mock2": f"http://127.0.0.1:{port2}",
        "records_dir": records_dir,
        "config_path": config_path,
    }

    proxy.stop()
    mock1.stop()
    mock2.stop()


# -------------------------------------------------------------------- helpers
def _index_rows(records_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted((records_dir / "index").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _load_rec(records_dir: Path, call_id: str) -> dict:
    rec = CallStore(records_dir).load_call(call_id)
    assert rec is not None, f"记录不存在: {call_id}"
    return rec


def _wait_for_records(records_dir: Path, min_count: int, timeout: float = 10.0) -> list[dict]:
    """轮询等待后台定稿落盘（解析在后台线程，索引行写入即定稿完成）。"""
    rows = _index_rows(records_dir)
    deadline = time.time() + timeout
    while len(rows) < min_count and time.time() < deadline:
        time.sleep(0.05)
        rows = _index_rows(records_dir)
    return rows


def _wait_for_new_record(
    records_dir: Path,
    previous_ids: set[str],
    *,
    path: str,
    timeout: float = 10.0,
) -> dict:
    """Wait for this request's record even when older background finalizers finish later."""
    deadline = time.time() + timeout
    while True:
        candidates = [
            row for row in _index_rows(records_dir)
            if row.get("id") not in previous_ids and row.get("path") == path
        ]
        if candidates:
            return candidates[-1]
        if time.time() >= deadline:
            raise AssertionError(f"等待新记录超时: path={path}")
        time.sleep(0.05)


def _latest_rec(records_dir: Path) -> dict:
    rows = _wait_for_records(records_dir, 1)
    assert rows, "索引为空"
    return _load_rec(records_dir, rows[-1]["id"])


def _partial_path(records_dir: Path, call_id: str) -> Path:
    date = f"{call_id[1:5]}-{call_id[5:7]}-{call_id[7:9]}"
    return records_dir / "calls" / date / f"{call_id}.partial.json"


def _chat_payload(**over) -> dict:
    payload = {"model": "mock-model", "messages": [{"role": "user", "content": "问题"}]}
    payload.update(over)
    return payload


# =============================================================== 1. 非流式
async def test_nonstream_byte_identical_and_record(stack):
    before = len(_index_rows(stack["records_dir"]))
    async with httpx.AsyncClient(timeout=30) as client:
        direct = await client.post(f"{stack['mock1']}/v1/chat/completions", json=_chat_payload())
        proxied = await client.post(f"{stack['proxy']}/v1/chat/completions", json=_chat_payload())

    assert proxied.status_code == 200
    assert proxied.content == direct.content  # 响应字节级一致

    rows = _wait_for_records(stack["records_dir"], before + 1)
    rec = _load_rec(stack["records_dir"], rows[-1]["id"])
    cid = rec["id"]
    assert rec["status"] == "ok"
    assert rec["model"] == "mock-model"
    assert rec["stream"] is False
    assert rec["upstream_name"] == "main"
    assert rec["response"]["status_code"] == 200
    assert rec["response"]["content"] == NONSTREAM_BODY
    assert rec["usage"]["prompt_tokens"] == 12
    assert rec["usage"]["completion_tokens"] == 7
    assert rec["usage"]["total_tokens"] == 19
    assert rec["usage"]["cached_tokens"] is None
    # 后台旁路解析结果（不参与转发路径）
    rp = rec["request"]["parsed"]
    assert rp["model"] == "mock-model"
    assert rp["stream"] is False
    assert rp["messages"] == [{"role": "user", "content": "问题"}]
    rsp = rec["response"]["parsed"]
    assert rsp["message"] == {"role": "assistant", "content": "你好，世界！"}
    assert rsp["finish_reason"] == "stop"
    assert rec["session_key"] and rec["session_key"].startswith("s")
    assert not _partial_path(stack["records_dir"], cid).exists()  # partial 已清理


# ================================================================= 2. 流式
async def test_stream_incremental_chunks_and_record(stack):
    before = len(_index_rows(stack["records_dir"]))
    stamps, chunks = [], []
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream(
            "POST", f"{stack['proxy']}/v1/chat/completions", json=_chat_payload(stream=True)
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            async for chunk in resp.aiter_bytes():
                stamps.append(time.perf_counter())
                chunks.append(chunk)

    raw = b"".join(chunks)
    assert raw.endswith(b"data: [DONE]\n\n")  # [DONE] 结尾
    assert len(stamps) >= 4  # 多块到达而非一次性
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert sum(1 for g in gaps if g >= 0.01) >= 3  # 至少 3 个可感知间隔（逐块流式）

    p = SSEParser()
    p.feed(raw)
    assert p.content_text == FULL_CONTENT  # 完整文本组装正确

    rows = _wait_for_records(stack["records_dir"], before + 1)
    rec = _load_rec(stack["records_dir"], rows[-1]["id"])
    assert rec["status"] == "ok"
    assert rec["stream"] is True
    assert rec["model"] == "mock-model"
    assert rec["response"]["chunk_count"] == 7  # role + 5 content + usage
    assert rec["response"]["ttft_ms"] > 0
    assert rec["response"]["first_byte_ms"] > 0
    assert rec["response"]["content"] == {"role": "assistant", "content": FULL_CONTENT}
    assert rec["usage"] == {"prompt_tokens": 21, "completion_tokens": 5,
                            "total_tokens": 26, "cached_tokens": 3, "reasoning_tokens": None}
    # 后台旁路解析：组装消息 / finish_reason / 解码指标
    assert rec["response"]["parsed"]["message"] == {"role": "assistant", "content": FULL_CONTENT}
    assert rec["response"]["parsed"]["parse_error"] is None
    assert rec["response"]["decode_ms"] > 0
    assert rec["response"]["decode_tokens_per_sec"] > 0
    assert 0 < rec["response"]["ttft_ms"] <= rec["duration_ms"]
    assert not _partial_path(stack["records_dir"], rec["id"]).exists()


# ============================================================== 3. 错误透传
async def test_error_passthrough_401(stack):
    before = len(_index_rows(stack["records_dir"]))
    async with httpx.AsyncClient(timeout=30) as client:
        direct = await client.post(f"{stack['mock1']}/v1/err")
        proxied = await client.post(f"{stack['proxy']}/v1/err")

    assert proxied.status_code == 401
    assert proxied.content == direct.content  # body 字节一致

    rows = _wait_for_records(stack["records_dir"], before + 1)
    rec = _load_rec(stack["records_dir"], rows[-1]["id"])
    assert rec["status"] == "error"
    assert rec["response"]["status_code"] == 401
    assert rec["response"]["content"] == json.loads(direct.content)


# ============================================================ 4. 上游不可达
async def test_upstream_unreachable_502(tmp_path_factory):
    records_dir = tmp_path_factory.mktemp("records_dead")
    cfg_path = tmp_path_factory.mktemp("cfg_dead") / "config.json"
    cfg = AppConfig(
        server=ServerConfig(),
        upstreams=[UpstreamConfig(name="dead", base_url=f"http://127.0.0.1:{_dead_port()}")],
        default_upstream="dead",
        recording=RecordingConfig(dir=str(records_dir)),
    )
    server = ServerThread(create_app(cfg, config_path=str(cfg_path)), "proxy-dead")
    port = server.start()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"http://127.0.0.1:{port}/v1/chat/completions", json=_chat_payload()
            )
        assert r.status_code == 502
        assert r.json()["error"]["type"] == "upstream_unreachable"

        rec = _latest_rec(records_dir)
        assert rec["status"] == "error"
        assert rec["error"]["type"] == "upstream_unreachable"
        assert rec["error"]["message"]
    finally:
        server.stop()


# ============================================= 5. /up/{name} 路由与 key 策略
async def test_up_second_routing_and_key_strategy(stack):
    before = len(_index_rows(stack["records_dir"]))
    async with httpx.AsyncClient(timeout=30) as client:
        # second 上游 + keep：保留客户端原值
        r = await client.post(
            f"{stack['proxy']}/up/second/v1/echo", json={"k": 1},
            headers={"Authorization": "Bearer sk-client-keep-42", "Sona-Code-Client": "spoofed"},
        )
        d = r.json()
        assert d["server"] == "mock2"
        assert d["path"] == "/v1/echo"  # /up/second 前缀已剥
        assert d["authorization"] == "Bearer sk-client-keep-42"
        UUID(d["sona_code_client"])
        assert d["sona_code_client"] != "spoofed"

        # 默认路由 main + replace：mock 收到配置 key
        r2 = await client.post(
            f"{stack['proxy']}/v1/echo", json={"k": 1},
            headers={"Authorization": "Bearer sk-client-orig-99"},
        )
        d2 = r2.json()
        assert d2["server"] == "mock1"
        assert d2["authorization"] == f"Bearer {UPSTREAM_KEY}"
        UUID(d2["sona_code_client"])
        assert d2["sona_code_client"] != d["sona_code_client"]

        # 客户端无凭据头：replace 注入配置 key
        r3 = await client.post(f"{stack['proxy']}/v1/echo", json={"k": 1})
        assert r3.json()["authorization"] == f"Bearer {UPSTREAM_KEY}"

        # 显式 /up/main 前缀同样路由到 main
        r4 = await client.post(f"{stack['proxy']}/up/main/v1/echo", json={})
        assert r4.json()["server"] == "mock1"

    # 记录的上游归属正确
    rows = _wait_for_records(stack["records_dir"], before + 4)
    rec = _load_rec(stack["records_dir"], rows[-1]["id"])
    assert rec["upstream_name"] == "main"
    assert rec["request"]["path"] == "/up/main/v1/echo"


# ================================= 5b. 默认 key_strategy=keep 完全透明
async def test_default_keep_strategy_transparent_credentials(stack):
    """未配置 key_strategy 的上游（默认 keep）：客户端凭据头原样透传。"""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{stack['proxy']}/up/passthru/v1/echo", json={"k": 1},
            headers={"Authorization": "Bearer sk-client-orig-777", "X-Api-Key": "xkey-orig"},
        )
    d = r.json()
    assert d["server"] == "mock2"
    assert d["authorization"] == "Bearer sk-client-orig-777"  # 未被改写/注入
    assert d["x_api_key"] == "xkey-orig"


# ============================================ 6. 请求体/query 字节级透传
async def test_request_body_and_query_passthrough(stack):
    body_bytes = json.dumps(
        {"msg": "中文✨特殊\"引号\"&<标签>\\符号", "arr": [1, 2, 3]}, ensure_ascii=False
    ).encode("utf-8")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{stack['proxy']}/v1/echo",
            content=body_bytes,
            headers={"content-type": "application/json"},
            params={"a": "1", "b": "中文"},
        )
    d = r.json()
    assert base64.b64decode(d["body_b64"]) == body_bytes  # body 字节级一致
    assert d["content_type"].startswith("application/json")
    # query 原样透传不重编码（中文按 UTF-8 百分号编码）
    assert d["query"] == "a=1&b=%E4%B8%AD%E6%96%87"


# ==================================================== 7. 脱敏落盘
async def test_redact_keys_not_persisted(stack):
    client_key = "sk-client-secret-abcdef"
    previous_ids = {row["id"] for row in _index_rows(stack["records_dir"])}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{stack['proxy']}/v1/chat/completions", json=_chat_payload(),
            headers={"Authorization": f"Bearer {client_key}", "X-Api-Key": "xkey-123456"},
        )
    assert r.status_code == 200

    row = _wait_for_new_record(
        stack["records_dir"], previous_ids, path="/v1/chat/completions"
    )
    cid = row["id"]
    raw_text = (stack["records_dir"] / "calls"
                / f"{cid[1:5]}-{cid[5:7]}-{cid[7:9]}" / f"{cid}.json").read_text(encoding="utf-8")
    assert client_key not in raw_text  # 客户端 key 不落盘
    assert UPSTREAM_KEY not in raw_text  # 上游 key 不落盘
    assert "xkey-123456" not in raw_text

    rec = json.loads(raw_text)
    hdrs = {k.lower(): v for k, v in rec["request"]["headers"].items()}
    assert hdrs["authorization"] == "Bearer sk-c***"
    assert hdrs["x-api-key"] == "xkey***"


# ============================================ 8. index 与 calls 一一对应
async def test_index_matches_calls_files(stack):
    records_dir = stack["records_dir"]
    rows = _index_rows(records_dir)
    assert rows, "此前用例应已产生记录"
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))  # 无重复

    store = CallStore(records_dir)
    for r in rows:
        assert store.load_call(r["id"]) is not None

    partials = list((records_dir / "calls").rglob("*.partial.json"))
    assert partials == []  # 无残留 partial
    finals = [f for f in (records_dir / "calls").rglob("*.json")
              if not f.name.endswith(".partial.json")]
    assert len(finals) == len(rows)  # 最终记录数 == 索引行数


# ==================================================== 10. 并发流式
async def test_concurrent_streams(stack):
    before = len(_index_rows(stack["records_dir"]))

    async def one(client: httpx.AsyncClient, i: int) -> None:
        chunks = []
        async with client.stream(
            "POST", f"{stack['proxy']}/v1/chat/completions",
            json=_chat_payload(stream=True, messages=[{"role": "user", "content": f"q{i}"}]),
        ) as resp:
            assert resp.status_code == 200
            async for c in resp.aiter_bytes():
                chunks.append(c)
        raw = b"".join(chunks)
        assert raw.endswith(b"data: [DONE]\n\n")
        p = SSEParser()
        p.feed(raw)
        assert p.content_text == FULL_CONTENT

    async with httpx.AsyncClient(timeout=120) as client:
        await asyncio.gather(*(one(client, i) for i in range(10)))

    rows = _wait_for_records(stack["records_dir"], before + 10)
    assert len(rows) == before + 10
    for r in rows[-10:]:
        assert r["status"] == "ok"
        assert r["model"] == "mock-model"
        rec = _load_rec(stack["records_dir"], r["id"])
        assert rec["response"]["chunk_count"] == 7
        assert rec["response"]["content"] == {"role": "assistant", "content": FULL_CONTENT}
        assert rec["usage"]["total_tokens"] == 26


# ================================================= 9. 管理端点
def test_ping_identifies_desktop_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("SONACODE_DESKTOP_INSTANCE_ID", "test-instance")
    cfg = AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
    )
    with TestClient(create_app(cfg, str(tmp_path / "config.json"))) as client:
        assert client.get(f"{ADMIN}/api/ping").json() == {
            "ok": True,
            "instance_id": "test-instance",
        }


async def test_admin_ping_meta_settings_get(stack):
    async with httpx.AsyncClient(timeout=30) as client:
        ping = await client.get(f"{stack['proxy']}{ADMIN}/api/ping")
        assert ping.status_code == 200
        assert ping.json() == {"ok": True}

        meta = await client.get(f"{stack['proxy']}{ADMIN}/api/meta")
        assert meta.status_code == 200
        assert meta.json()["config_path"] == str(stack["config_path"])

        s = await client.get(f"{stack['proxy']}{ADMIN}/api/settings")
        assert s.status_code == 200
        d = s.json()
        assert d["config"]["recording"]["redact"] is True
        assert d["config_path"] == str(stack["config_path"])
        assert d["restart_fields"] == ["server.host", "server.port", "server.admin_prefix"]


async def test_admin_overview_aggregates(stack):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{stack['proxy']}{ADMIN}/api/overview")
    assert r.status_code == 200
    ov = r.json()

    rows = _index_rows(stack["records_dir"])
    assert ov["total_calls"] == len(rows)
    assert ov["total_prompt_tokens"] == sum(x.get("prompt_tokens") or 0 for x in rows)
    assert ov["total_completion_tokens"] == sum(x.get("completion_tokens") or 0 for x in rows)
    errors = sum(1 for x in rows if x.get("status") == "error")
    assert ov["error_rate"] == round(errors / len(rows), 4)

    by_model = {m["model"]: m for m in ov["by_model"]}
    mock_calls = sum(1 for x in rows if x.get("model") == "mock-model")
    assert by_model["mock-model"]["calls"] == mock_calls
    assert by_model["mock-model"]["prompt_tokens"] == sum(
        x.get("prompt_tokens") or 0 for x in rows if x.get("model") == "mock-model")

    today = datetime.now().astimezone().date().isoformat()
    today_slot = next(d for d in ov["by_day"] if d["date"] == today)
    assert today_slot["calls"] == len(rows)  # 全部记录均为今日产生
    assert len(ov["by_day"]) == 14
    assert len(ov["recent"]) <= 10


async def test_admin_calls_filter_pagination_detail(stack):
    rows = _index_rows(stack["records_dir"])
    base = f"{stack['proxy']}{ADMIN}/api/calls"
    async with httpx.AsyncClient(timeout=30) as client:
        # 分页
        r = await client.get(base, params={"page": 1, "page_size": 2})
        d = r.json()
        assert d["total"] == len(rows)
        assert d["page"] == 1 and d["page_size"] == 2
        assert len(d["items"]) == 2

        # status 过滤
        r2 = await client.get(base, params={"status": "error"})
        d2 = r2.json()
        assert d2["total"] == sum(1 for x in rows if x["status"] == "error")
        assert all(i["status"] == "error" for i in d2["items"])

        # model 过滤
        r3 = await client.get(base, params={"model": "mock-model"})
        assert all(i["model"] == "mock-model" for i in r3.json()["items"])

        # q 子串过滤（path）
        r4 = await client.get(base, params={"q": "/v1/echo"})
        expect = sum(1 for x in rows if "/v1/echo" in str(x.get("path") or ""))
        assert r4.json()["total"] == expect

        # 详情 + 404
        cid = rows[-1]["id"]
        r5 = await client.get(f"{base}/{cid}")
        assert r5.status_code == 200
        assert r5.json()["id"] == cid
        r6 = await client.get(f"{base}/cnotexist_000000_000000")
        assert r6.status_code == 404


async def test_admin_put_settings_redact_and_restart_flag(stack):
    config_path: Path = stack["config_path"]
    proxy = stack["proxy"]

    async def _next_rec(before: int) -> dict:
        rows = _wait_for_records(stack["records_dir"], before + 1)
        return _load_rec(stack["records_dir"], rows[-1]["id"])

    async with httpx.AsyncClient(timeout=30) as client:
        body = (await client.get(f"{proxy}{ADMIN}/api/settings")).json()["config"]

        # 1) 改 redact=false：200 + restart_required=False + 配置文件更新 + 行为变化
        body["recording"]["redact"] = False
        r = await client.put(f"{proxy}{ADMIN}/api/settings", json=body)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "restart_required": False}
        assert json.loads(config_path.read_text(encoding="utf-8"))["recording"]["redact"] is False

        before1 = len(_index_rows(stack["records_dir"]))
        await client.post(
            f"{proxy}/v1/chat/completions", json=_chat_payload(),
            headers={"Authorization": "Bearer sk-plain-see-me"},
        )
        rec = await _next_rec(before1)
        hdrs = {k.lower(): v for k, v in rec["request"]["headers"].items()}
        assert hdrs["authorization"] == "Bearer sk-plain-see-me"  # 不再脱敏

        # 2) 改 port：restart_required=True（server 段不热应用）
        body["recording"]["redact"] = True
        body["server"]["port"] = 8999
        r2 = await client.put(f"{proxy}{ADMIN}/api/settings", json=body)
        assert r2.json() == {"ok": True, "restart_required": True}
        # 代理仍在原端口正常服务
        assert (await client.get(f"{proxy}{ADMIN}/api/ping")).json() == {"ok": True}

        # 3) 恢复原始配置
        body["server"]["port"] = 8117
        r3 = await client.put(f"{proxy}{ADMIN}/api/settings", json=body)
        assert r3.status_code == 200
        final = json.loads(config_path.read_text(encoding="utf-8"))
        assert final["recording"]["redact"] is True
        assert final["server"]["port"] == 8117

        # 恢复脱敏后再请求 → 重新掩码
        before2 = len(_index_rows(stack["records_dir"]))
        await client.post(
            f"{proxy}/v1/chat/completions", json=_chat_payload(),
            headers={"Authorization": "Bearer sk-plain-see-me"},
        )
        rec2 = await _next_rec(before2)
        hdrs2 = {k.lower(): v for k, v in rec2["request"]["headers"].items()}
        assert hdrs2["authorization"] == "Bearer sk-p***"


# ============================================== 11. 会话轨迹（多轮对话聚合）
TRJ_U1 = {"role": "user", "content": "会话轨迹测试问题"}
TRJ_A1 = {"role": "assistant", "content": "第一答"}
TRJ_U2 = {"role": "user", "content": "第二问"}
TRJ_A2 = {"role": "assistant", "content": "第二答"}
TRJ_U3 = {"role": "user", "content": "第三问"}


async def _make_conversation(stack) -> None:
    """3 轮对话（非流式），每轮携带完整历史 → 同一 session_key。"""
    turns = [
        [TRJ_U1],
        [TRJ_U1, TRJ_A1, TRJ_U2],
        [TRJ_U1, TRJ_A1, TRJ_U2, TRJ_A2, TRJ_U3],
    ]
    before = len(_index_rows(stack["records_dir"]))
    async with httpx.AsyncClient(timeout=30) as client:
        for msgs in turns:
            r = await client.post(
                f"{stack['proxy']}/v1/chat/completions",
                json={"model": "mock-model", "messages": msgs},
            )
            assert r.status_code == 200
    rows = _wait_for_records(stack["records_dir"], before + 3)
    assert len(rows) == before + 3


async def test_trajectory_sessions_list_and_detail(stack):
    await _make_conversation(stack)

    async with httpx.AsyncClient(timeout=30) as client:
        # ---- 会话列表：3 轮对话聚合成一个会话
        r = await client.get(f"{stack['proxy']}{ADMIN}/api/trajectory/sessions")
        assert r.status_code == 200
        data = r.json()
        target = next(
            s for s in data["sessions"]
            if s["preview"] == "会话轨迹测试问题" and s["calls"] == 3
        )
        assert target["solo"] is False
        assert target["model"] == "mock-model"
        assert target["prompt_tokens"] == 12 * 3
        assert target["completion_tokens"] == 7 * 3
        assert target["total_tokens"] == 19 * 3
        assert target["last_status"] == "ok"

        # 关键字过滤
        r_q = await client.get(
            f"{stack['proxy']}{ADMIN}/api/trajectory/sessions",
            params={"q": "会话轨迹测试问题"},
        )
        assert any(s["session_key"] == target["session_key"] for s in r_q.json()["sessions"])

        # ---- 会话详情：Turn 账本 + 增量消息 + 累计用量
        r2 = await client.get(
            f"{stack['proxy']}{ADMIN}/api/trajectory/sessions/{target['session_key']}"
        )
        assert r2.status_code == 200
        detail = r2.json()
        assert detail["session_key"] == target["session_key"]
        assert detail["preview"] == "会话轨迹测试问题"
        turns = detail["turns"]
        assert len(turns) == 3

        t1, t2, t3 = turns
        assert t1["turn_no"] == 1 and t1["new_messages"] == [TRJ_U1]
        # 第 2 轮新增：上轮回复 + 新问题（system/历史前缀不重复展示）
        assert t2["turn_no"] == 2 and t2["new_messages"] == [TRJ_A1, TRJ_U2]
        assert t3["turn_no"] == 3 and t3["new_messages"] == [TRJ_A2, TRJ_U3]

        for t in turns:
            assert t["response_message"] == {"role": "assistant", "content": "你好，世界！"}
            assert t["finish_reason"] == "stop"
            assert t["duration_ms"] > 0
            assert t["usage"]["total_tokens"] == 19

        # Inspector Tab 化新增字段：system 文本 / 头部 / 上游 / 分块 / 上下文规模
        assert [t["messages_count"] for t in turns] == [1, 3, 5]
        assert all(t["system"] == "" for t in turns)  # 本会话无 system 消息
        for t in turns:
            assert t["method"] == "POST"
            assert t["path"] == "/v1/chat/completions"
            assert t["upstream_name"] == "main"
            assert isinstance(t["request_headers"], dict) and t["request_headers"]
            assert isinstance(t["response_headers"], dict) and t["response_headers"]
            assert isinstance(t["chunk_count"], int) and t["chunk_count"] >= 1

        # 累计用量递增
        assert t1["cumulative_usage"]["total_tokens"] == 19
        assert t2["cumulative_usage"]["total_tokens"] == 38
        assert t3["cumulative_usage"]["total_tokens"] == 57
        assert detail["cumulative_usage"] == {
            "prompt_tokens": 36, "completion_tokens": 21, "total_tokens": 57,
        }

        # 轮内搜索过滤
        r3 = await client.get(
            f"{stack['proxy']}{ADMIN}/api/trajectory/sessions/{target['session_key']}",
            params={"q": "第三问"},
        )
        assert [t["turn_no"] for t in r3.json()["turns"]] == [3]

        # 不存在的会话 → 空 turns
        r404 = await client.get(f"{stack['proxy']}{ADMIN}/api/trajectory/sessions/snonexist0")
        assert r404.status_code == 200
        assert r404.json()["turns"] == []


async def test_trajectory_solo_group_for_non_chat_calls(stack):
    """无 messages 的调用（echo 等）不属于任何会话：每条自成一组的 solo 键。"""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{stack['proxy']}{ADMIN}/api/trajectory/sessions")
    solos = [s for s in r.json()["sessions"] if s["solo"]]
    assert solos, "应存在 solo 分组（/v1/echo、/v1/err 等非对话调用）"
    for s in solos:
        assert s["session_key"].startswith("solo:")
        assert s["calls"] == 1


async def test_trajectory_session_header_attribution(stack):
    """会话归属头命中 → 头值优先聚合（h 键）：内容不同也同轨；不同头值分轨。"""
    before = len(_index_rows(stack["records_dir"]))
    async with httpx.AsyncClient(timeout=30) as client:
        # 两条内容完全不同的请求携带同一头值 → 同一会话（内容哈希会拆成两个）
        for content in ("头归属第一条问题", "内容完全不同的第二条"):
            r = await client.post(
                f"{stack['proxy']}/v1/chat/completions",
                json={"model": "mock-model", "messages": [{"role": "user", "content": content}]},
                headers={"X-DeepSeek-Harness-Session-Id": "trj-hdr-1"},
            )
            assert r.status_code == 200
        # 另一头名（默认列表第 2 位）不同头值 → 不同会话
        r = await client.post(
            f"{stack['proxy']}/v1/chat/completions",
            json={"model": "mock-model", "messages": [{"role": "user", "content": "头归属第一条问题"}]},
            headers={"x-session-id": "trj-hdr-2"},
        )
        assert r.status_code == 200
    rows = _wait_for_records(stack["records_dir"], before + 3)
    new_keys = {r["session_key"] for r in rows[before:]}
    assert len(new_keys) == 2
    assert all(k and k.startswith("h") and len(k) == 17 for k in new_keys)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{stack['proxy']}{ADMIN}/api/trajectory/sessions")
        target = next(
            s for s in r.json()["sessions"]
            if s["session_key"].startswith("h") and s["calls"] == 2
            and s["preview"] == "头归属第一条问题"
        )
        assert target["solo"] is False
        assert target["model"] == "mock-model"

        detail = (
            await client.get(f"{stack['proxy']}{ADMIN}/api/trajectory/sessions/{target['session_key']}")
        ).json()
        assert detail["calls"] == 2
        # 头值优先：第二条内容与第一条无公共前缀，仍归入同一会话且各成一笔增量
        assert detail["turns"][0]["new_messages"] == [{"role": "user", "content": "头归属第一条问题"}]
        assert detail["turns"][1]["new_messages"] == [{"role": "user", "content": "内容完全不同的第二条"}]
        assert detail["cumulative_usage"]["total_tokens"] == 19 * 2


# ============================================== 13. 上游中途断流：部分数据照样记录
async def test_upstream_broken_stream_records_partial(stack):
    """上游 SSE 流中途断开：客户端连接同步断开，但已收内容全部定稿展示。"""
    before = len(_index_rows(stack["records_dir"]))
    received = b""
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream(
            "POST", f"{stack['proxy']}/v1/stream-broken",
            json=_chat_payload(stream=True),
        ) as resp:
            assert resp.status_code == 200
            try:
                async for chunk in resp.aiter_bytes():
                    received += chunk
            except (httpx.ReadError, httpx.RemoteProtocolError):
                pass  # 预期：上游断开透明传导到客户端
    assert "你好" in received.decode("utf-8", errors="replace")  # 客户端确实收到部分流

    rows = _wait_for_records(stack["records_dir"], before + 1)
    rec = _load_rec(stack["records_dir"], rows[-1]["id"])
    # 记录定稿：错误类型标记断流，已收分片组装进响应
    assert rec["status"] == "error"
    assert rec["error"]["type"] == "upstream_stream_error"
    msg = rec["response"]["parsed"]["message"]
    assert msg == {"role": "assistant", "content": "你好，"}  # 前 2 个分片
    assert rec["response"]["chunk_count"] > 0
    # partial 占位已被定稿清理
    assert not _partial_path(stack["records_dir"], rec["id"]).exists()


async def test_records_cleanup_endpoints(stack):
    """数据清理端点：统计 / 单条删除 / 按日期删除 / 保留清理 / 清空全部。

    放在文件末尾执行：会清空全部记录，影响后续依赖历史数据的用例。
    """
    base = f"{stack['proxy']}{ADMIN}/api"
    async with httpx.AsyncClient(timeout=30) as client:
        # 统计：有数据、按日期列出
        stats = await client.get(f"{base}/records/stats")
        assert stats.status_code == 200
        s = stats.json()
        assert s["total_bytes"] > 0 and s["total_files"] > 0
        assert s["dates"], "前置用例应已产生记录"
        today = datetime.now().astimezone().date().isoformat()
        assert any(d["date"] == today for d in s["dates"])

        # 单条删除：列表取一条 → 删除 → 详情 404、索引行消失
        calls = await client.get(f"{base}/calls?page=1&page_size=1")
        cid = calls.json()["items"][0]["id"]
        total_before = calls.json()["total"]
        r = await client.delete(f"{base}/calls/{cid}")
        assert r.status_code == 200 and r.json() == {"ok": True, "deleted": 1}
        assert (await client.get(f"{base}/calls/{cid}")).status_code == 404
        calls_after = await client.get(f"{base}/calls?page=1&page_size=1")
        assert calls_after.json()["total"] == total_before - 1

        # 不存在的单条 → 404
        assert (await client.delete(f"{base}/calls/c99991231_000000_none")).status_code == 404

        # 保留清理：retention_days=0（默认配置）→ 无操作
        sweep = await client.post(f"{base}/records/cleanup")
        assert sweep.status_code == 200
        assert sweep.json()["removed_dates"] == []

        # 按日期删除今天（前置全部记录均为今日）→ 剩余为空
        r = await client.delete(f"{base}/records/date/{today}")
        assert r.status_code == 200 and r.json()["deleted"] >= 1
        assert (await client.delete(f"{base}/records/date/{today}")).status_code == 404
        assert (await client.get(f"{base}/calls")).json()["total"] == 0

        # 清空全部：再无日期
        r = await client.delete(f"{base}/records/all")
        assert r.status_code == 200
        assert r.json()["dates"] == 0
        s2 = (await client.get(f"{base}/records/stats")).json()
        assert s2["dates"] == [] and s2["total_bytes"] == 0
