"""回归测试：非 UTF-8 stdio 下 CLI 启动输出不应崩溃。"""

import io
import sys

from llm_api_proxy_recorder.cli import _banner, _harden_stdio


def test_banner_survives_non_utf8_stdio(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    _harden_stdio()
    assert stream.errors == "replace"
    assert stream.encoding == "utf-8"

    print(_banner("127.0.0.1", 8117, "/__recorder", "records", "config.json", {}))
    stream.flush()

    data = raw.getvalue()
    assert b"llm-api-proxy-recorder" in data
    assert "本地大模型".encode("utf-8") in data


def test_harden_stdio_keeps_utf8_streams_strict(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", stream)

    _harden_stdio()

    assert stream.errors == "strict"
