"""上游路由与 URL 拼接测试。"""

from sona_code.config import AppConfig, UpstreamConfig
from sona_code.proxy.router import build_upstream_url, resolve_upstream


def make_cfg() -> AppConfig:
    return AppConfig(
        upstreams=[
            UpstreamConfig(name="main", base_url="http://127.0.0.1:9001"),
            UpstreamConfig(name="second", base_url="http://127.0.0.1:9002"),
        ],
        default_upstream="main",
    )


def test_prefix_routes_to_named_upstream_and_strips_prefix():
    cfg = make_cfg()
    up, path = resolve_upstream("/up/second/v1/chat/completions", cfg)
    assert up.name == "second"
    assert path == "/v1/chat/completions"


def test_plain_path_routes_to_default_unchanged():
    cfg = make_cfg()
    up, path = resolve_upstream("/v1/chat/completions", cfg)
    assert (up.name, path) == ("main", "/v1/chat/completions")
    up, path = resolve_upstream("/", cfg)
    assert (up.name, path) == ("main", "/")


def test_unknown_upstream_name_falls_back_to_default():
    cfg = make_cfg()
    up, path = resolve_upstream("/up/nope/v1/chat/completions", cfg)
    assert (up.name, path) == ("main", "/up/nope/v1/chat/completions")  # 路径原样


def test_up_prefix_without_trailing_subpath_falls_back():
    # /up/second（无后续 /xxx）不视为命名路由
    cfg = make_cfg()
    up, path = resolve_upstream("/up/second", cfg)
    assert (up.name, path) == ("main", "/up/second")


def test_build_upstream_url_join_and_query_passthrough():
    assert build_upstream_url("http://h:1", "v1/x", "") == "http://h:1/v1/x"
    assert build_upstream_url("http://h:1/", "/v1/x", "") == "http://h:1/v1/x"
    assert build_upstream_url("http://h:1/base/", "/v1/x", "a=1") == "http://h:1/base/v1/x?a=1"
    # query 原样拼接，不重编码
    url = build_upstream_url("http://h:1", "v1/x", "q=a%20b&zh=%E4%B8%AD&n=1")
    assert url == "http://h:1/v1/x?q=a%20b&zh=%E4%B8%AD&n=1"
