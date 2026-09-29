"""httpx 上游客户端管理（支持出站代理热更新）。"""

from __future__ import annotations

import httpx

# 流式不能有读超时：read=None；连接 10s、写 120s。
_TIMEOUT = httpx.Timeout(connect=10, read=None, write=120, pool=None)


def _build_client(proxy_url: str) -> httpx.AsyncClient:
    kwargs: dict = {
        "timeout": _TIMEOUT,
        "follow_redirects": False,
        # 出站代理仅由配置决定，不受系统环境代理影响
        "trust_env": False,
    }
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return httpx.AsyncClient(**kwargs)


class UpstreamClient:
    def __init__(self, proxy_url: str = ""):
        self._proxy_url = proxy_url
        self._client = _build_client(proxy_url)

    def get(self) -> httpx.AsyncClient:
        return self._client

    async def rebuild(self, proxy_url: str) -> None:
        """热更新出站代理：先建新的再关旧的，切换期不拒服务。"""
        old = self._client
        self._proxy_url = proxy_url
        self._client = _build_client(proxy_url)
        await old.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()
