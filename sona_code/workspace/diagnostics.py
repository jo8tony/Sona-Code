"""Bounded subprocess output and credential-safe OpenCode startup diagnostics."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import threading
from typing import BinaryIO
from urllib.parse import quote, unquote, urlsplit

from sona_code.config import AppConfig

OUTPUT_LIMIT = 64 * 1024
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
_LOCALE_ERROR = re.compile(
    r"\b(?:EPERM|EUNKNOWN)\b[^\r\n]*\bopen\s+['\"]?"
    r"B:[\\/]+~BUN[\\/]+locales[\\/]+[^\\/\r\n'\"]+\.json(?=['\"\s,;)]|$)",
    re.IGNORECASE,
)


class ProcessOutput:
    """Drain an owned pipe without blocking on descendants that inherit it."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._buffer = bytearray()
        self._truncated = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.error: str | None = None
        self._thread = threading.Thread(target=self._read, name="opencode-output", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            fd = self._stream.fileno()
            if os.name == "nt":
                import msvcrt

                handle = msvcrt.get_osfhandle(fd)
                peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
                peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                                 ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
                peek.restype = ctypes.c_int
            else:
                os.set_blocking(fd, False)
            closing_reads = OUTPUT_LIMIT // 4096
            while True:
                if os.name == "nt":
                    available = ctypes.c_ulong()
                    if not peek(handle, None, 0, None, ctypes.byref(available), None):
                        error = ctypes.get_last_error()
                        if error in (109, 232):  # broken/closing pipe
                            break
                        raise OSError(error, "Unable to read OpenCode output pipe")
                    if available.value:
                        chunk = os.read(fd, min(available.value, 4096))
                    else:
                        chunk = None
                else:
                    try:
                        chunk = os.read(fd, 4096)
                    except BlockingIOError:
                        chunk = None
                if chunk == b"":
                    break
                if chunk:
                    with self._lock:
                        self._buffer.extend(chunk)
                        excess = len(self._buffer) - OUTPUT_LIMIT
                        if excess > 0:
                            del self._buffer[:excess]
                            self._truncated = True
                    if self._stop.is_set():
                        closing_reads -= 1
                        if closing_reads == 0:
                            break
                    continue
                if self._stop.wait(.02):
                    break
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        finally:
            self._stream.close()

    def text(self) -> str:
        with self._lock:
            raw = bytes(self._buffer)
            truncated = self._truncated
        # Never expose a credential fragment cut off at the ring's boundary.
        if truncated:
            separator = raw.find(b"\n")
            raw = raw[separator + 1:] if separator >= 0 else b""
        text = _ANSI.sub("", raw.decode("utf-8", errors="replace"))
        return "".join(char for char in text if char in "\n\r\t" or ord(char) >= 32)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


class StartupRedactor:
    """Mask known credentials and credential fields before logging or truncation."""

    def __init__(self, config: AppConfig, env: dict[str, str]) -> None:
        names = {"authorization", "proxy-authorization", "x-api-key", "api-key", "cookie",
                 "set-cookie", "apikey", "api_key", "password", "token", "secret",
                 "accesstoken", "access_token", "refreshtoken", "refresh_token",
                 "clientsecret", "client_secret",
                 *[name.lower() for name in config.recording.redact_headers]}
        values = set()
        for provider in config.upstreams:
            values.add(provider.api_key)
            values.update(model.api_key for model in provider.models)
            values.update(value for name, value in provider.extra_headers.items() if name.lower() in names)
        for name, value in env.items():
            if re.search(r"(?:KEY|TOKEN|PASSWORD|SECRET|AUTHORIZATION|COOKIE)$", name, re.IGNORECASE):
                values.add(value)
        for value in (config.outbound.proxy_url, *env.values()):
            if "://" in value:
                try:
                    url = urlsplit(value)
                    if url.password:
                        values.add(url.password)
                        values.add(unquote(url.password))
                except ValueError:
                    pass
        try:
            inline = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        except ValueError:
            inline = {}

        def collect(obj: object) -> None:
            if isinstance(obj, dict):
                for name, value in obj.items():
                    if str(name).lower() in names and isinstance(value, str):
                        values.add(value)
                    else:
                        collect(value)
            elif isinstance(obj, list):
                for value in obj:
                    collect(value)

        collect(inline)
        password = env.get("OPENCODE_SERVER_PASSWORD")
        if password:
            basic = f"{env.get('OPENCODE_SERVER_USERNAME', 'opencode')}:{password}".encode()
            values.add(base64.b64encode(basic).decode())
        values.discard("")
        values.update(quote(value, safe="") for value in list(values))
        values.update(json.dumps(value, ensure_ascii=True)[1:-1] for value in list(values))
        self._values = sorted(values, key=len, reverse=True)
        fields = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        self._fields = re.compile(
            rf"([\"']?(?:{fields})[\"']?\s*[:=]\s*)"
            r"(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\r\n,}]+)", re.IGNORECASE,
        )

    def redact(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, "[redacted]")
        return self._fields.sub(r"\1[redacted]", text)


def is_locale_error(text: str) -> bool:
    return bool(_LOCALE_ERROR.search(text))


def failure_detail(source: str, output: str, exit_code: int | None, *, locale_error: bool = False) -> str:
    if locale_error:
        action = ("请升级 Sona Code 获取修补版，并查看应用日志。" if source == "bundled"
                  else "请在设置中切换到应用随包修补版，并查看应用日志。")
        return "OpenCode 服务启动失败：原生 CLI 语言资源加载失败（B:\\~BUN\\locales）。" + action
    reason = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "未输出错误详情")
    status = f"退出码 {exit_code}" if exit_code is not None else "启动超时"
    return f"OpenCode 服务启动失败（来源 {source}，{status}）：{reason[:240]}；请查看应用日志。"
