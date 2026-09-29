"""上游路由：/up/{name}/ 前缀 → 命名上游；其余 → 默认上游。"""

from __future__ import annotations

from sona_code.config import AppConfig, UpstreamConfig
from fastapi import HTTPException
from sona_code.admin.models import route_token


def resolve_upstream(path: str, cfg: AppConfig) -> tuple[UpstreamConfig, str]:
    """返回 (上游配置, 去除前缀后的上游路径)。

    - path 以 /up/{name}/ 开头且 name 在 cfg.upstreams → 该上游，路径去掉 /up/{name}
    - 否则（含 /up/未知名）→ 默认上游，路径原样
    """
    if path.startswith("/managed/"):
        pieces = path.split("/", 4)
        if len(pieces) != 5:
            raise HTTPException(404, "无效的模型代理路由")
        _, _, provider_token, model_token, tail = pieces
        for provider in cfg.upstreams:
            if route_token(provider.name) != provider_token:
                continue
            model = next((m for m in provider.models if route_token(m.id) == model_token), None)
            if model is None:
                raise HTTPException(404, "模型不存在")
            effective = provider.model_copy(update={"api_key": model.api_key or provider.api_key,
                                                     "key_strategy": "replace"})
            return effective, "/" + tail
        raise HTTPException(404, "提供商不存在")
    if not cfg.upstreams:
        raise HTTPException(503, "尚未配置模型提供商")
    prefix = "/up/"
    if path.startswith(prefix):
        name, sep, sub = path[len(prefix):].partition("/")
        if sep:  # 必须形如 /up/{name}/xxx
            for u in cfg.upstreams:
                if u.name == name:
                    return u, "/" + sub
    default = next(u for u in cfg.upstreams if u.name == cfg.default_upstream)
    return default, path


def build_upstream_url(base_url: str, upstream_path: str, query: str) -> str:
    """拼接上游 URL；query 为原始 query string，原样透传不重编码。"""
    url = base_url.rstrip("/") + "/" + upstream_path.lstrip("/")
    if query:
        url += "?" + query
    return url
