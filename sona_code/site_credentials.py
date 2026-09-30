"""Private, atomic website credential storage, separate from editable config."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from pathlib import Path


def _dpapi(data: bytes, *, decrypt: bool = False) -> bytes:
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("Website credential encryption failed")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def read_credentials(path: Path) -> list:
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            return []
        raw = path.read_bytes()
        if raw.startswith(b"DPAPI\n"):
            raw = _dpapi(raw[6:], decrypt=True)
        elif os.name == "nt":
            return []
        data = json.loads(raw)
        sessions = data.get("sessions") if data.get("version") == 1 else None
        return sessions if isinstance(sessions, list) else []
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # A corrupt/unreadable file requires login; never expose its contents.
        return []


def write_credentials(path: Path, sessions: list) -> None:
    raw = json.dumps({"version": 1, "sessions": sessions}, ensure_ascii=False).encode("utf-8")
    if os.name == "nt":
        raw = b"DPAPI\n" + _dpapi(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
