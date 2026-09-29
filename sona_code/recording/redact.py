"""请求/响应头脱敏。"""

from __future__ import annotations

from sona_code.config import RecordingConfig


def _mask(value: str) -> str:
    """保留 scheme 词 + 前 4 字符 + ***；凭据部分 ≤6 时只留 ***。"""
    head, sep, rest = value.partition(" ")
    if sep:
        return f"{head} {rest[:4]}***" if len(rest) > 6 else f"{head} ***"
    return value[:4] + "***" if len(value) > 6 else "***"


def redact_headers(headers: dict[str, str], cfg: RecordingConfig) -> dict[str, str]:
    """按配置对敏感头掩码，保留原始 header 名大小写。"""
    if not cfg.redact:
        return dict(headers)
    targets = {h.lower() for h in cfg.redact_headers}
    if not targets:
        return dict(headers)
    return {k: (_mask(v) if k.lower() in targets else v) for k, v in headers.items()}
