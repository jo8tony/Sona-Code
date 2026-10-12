"""Bounded, read-only previews of files in a registered project."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from fastapi import HTTPException


MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_TEXT_LINES = 20_000

IMAGE_TYPES = {
    ".png": "image/png", ".apng": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".bmp": "image/bmp",
    ".avif": "image/avif",
}
LANGUAGES = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx", ".py": "python",
    ".html": "markup", ".htm": "markup", ".xml": "markup", ".svg": "markup",
    ".css": "css", ".scss": "scss", ".less": "less", ".json": "json",
    ".jsonc": "javascript", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "ini", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".ps1": "powershell", ".go": "go", ".rs": "rust", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cs": "csharp", ".sql": "sql", ".diff": "diff", ".patch": "diff",
    ".md": "markdown", ".markdown": "markdown", ".mdx": "markdown",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".kts": "kotlin", ".dart": "dart", ".vue": "markup", ".svelte": "markup",
}
UNSUPPORTED_SUFFIXES = {
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".class",
    ".zip", ".gz", ".tar", ".7z", ".rar", ".woff", ".woff2", ".ttf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    ".sqlite", ".sqlite3", ".db", ".mp4", ".mp3", ".wav", ".mov",
}


def _file_stat(entry: Path) -> os.stat_result:
    try:
        result = entry.stat()
    except PermissionError as exc:
        raise HTTPException(403, "没有权限读取此文件") from exc
    except OSError as exc:
        raise HTTPException(404, "文件不存在或无法读取") from exc
    if not stat.S_ISREG(result.st_mode):
        raise HTTPException(400, "此路径不是普通文件")
    return result


def _read_bounded(entry: Path, limit: int, version: str = "") -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(entry, flags), "rb") as source:
            opened = os.fstat(source.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise HTTPException(400, "此路径不是普通文件")
            if version and version != f"{opened.st_mtime_ns}-{opened.st_size}":
                raise HTTPException(409, "文件已发生变化，请刷新预览")
            data = source.read(limit + 1)
    except PermissionError as exc:
        raise HTTPException(403, "没有权限读取此文件") from exc
    except OSError as exc:
        raise HTTPException(404, "文件不存在或无法读取") from exc
    if len(data) > limit:
        raise HTTPException(413, "文件超过预览大小限制，请使用系统应用打开")
    return data


def _decode_text(data: bytes) -> tuple[str, str] | None:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = [("utf-16", "UTF-16")]
    elif data.startswith(b"\xef\xbb\xbf"):
        encodings = [("utf-8-sig", "UTF-8 BOM")]
    else:
        if b"\x00" in data:
            return None
        encodings = [("utf-8", "UTF-8"), ("gb18030", "GB18030")]
    for codec, label in encodings:
        try:
            text = data.decode(codec)
        except UnicodeError:
            continue
        # Keep binary payloads out of the text renderer, including BOM-prefixed data.
        if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text):
            return None
        return text.replace("\r\n", "\n").replace("\r", "\n"), label
    return None


def file_preview(entry: Path, relative: str) -> dict:
    """Project-boundary validation belongs to the caller's shared path resolver."""
    info = _file_stat(entry)
    suffix = entry.suffix.lower()
    mime = IMAGE_TYPES.get(suffix, "application/pdf" if suffix == ".pdf" else "text/plain")
    kind = "image" if suffix in IMAGE_TYPES else "pdf" if suffix == ".pdf" else (
        "markdown" if suffix in {".md", ".markdown", ".mdx"} else "text")
    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_PDF_BYTES if kind == "pdf" else MAX_TEXT_BYTES
    result = {
        "path": relative, "name": relative.split("/")[-1], "kind": kind,
        "mime": mime, "size": info.st_size,
        "version": f"{info.st_mtime_ns}-{info.st_size}",
        "language": "docker" if entry.name.lower() == "dockerfile" else LANGUAGES.get(suffix, "plain"),
    }
    if info.st_size > limit:
        result.update(kind="unsupported", reason="too_large", message=(
            f"此文件超过 {limit // (1024 * 1024)} MB 的预览限制，请使用系统应用打开。"))
    elif suffix in UNSUPPORTED_SUFFIXES:
        result.update(kind="unsupported", reason="format", message="此格式暂不支持预览，可以使用系统应用打开。")
    elif kind in {"text", "markdown"}:
        decoded = _decode_text(_read_bounded(entry, MAX_TEXT_BYTES, result["version"]))
        if decoded is None:
            result.update(kind="unsupported", reason="binary", message="此文件不是可读取的文本，可以使用系统应用打开。")
        else:
            text, encoding = decoded
            lines = text.split("\n")
            result.update(content="\n".join(lines[:MAX_TEXT_LINES]), encoding=encoding,
                          line_count=len(lines), truncated=len(lines) > MAX_TEXT_LINES)
    return result


def file_content(entry: Path, version: str = "") -> tuple[bytes, str]:
    """Only passive images and PDF bytes may be served; never serve project HTML."""
    info = _file_stat(entry)
    if version and version != f"{info.st_mtime_ns}-{info.st_size}":
        raise HTTPException(409, "文件已发生变化，请刷新预览")
    suffix = entry.suffix.lower()
    if suffix in IMAGE_TYPES:
        return _read_bounded(entry, MAX_IMAGE_BYTES, version), IMAGE_TYPES[suffix]
    if suffix == ".pdf":
        return _read_bounded(entry, MAX_PDF_BYTES, version), "application/pdf"
    raise HTTPException(400, "此文件只能通过文本预览读取")
