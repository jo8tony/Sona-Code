"""Bounded, lossless editing of existing files in a registered project."""
from __future__ import annotations

import codecs
import hashlib
import os
import re
import stat
import tempfile
import threading
from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_LINE_LENGTH = 20000
_LOCKS = [threading.Lock() for _ in range(128)]


class FileError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(detail)


def _entry(root: Path, relative: str, *, directory: bool = False) -> Path:
    parts = relative.split("/")
    if (not relative or "\\" in relative or "\0" in relative
            or any(p in {"", ".", ".."} or ":" in p for p in parts)
            or any(p.lower() == ".git" for p in parts)):
        raise FileError(400, "无效的项目文件路径")
    current = root.resolve(strict=True)
    for part in parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise FileError(400, "不支持编辑符号链接或重解析点")
    if not current.resolve(strict=True).is_relative_to(root.resolve()):
        raise FileError(400, "文件必须位于项目目录内")
    if directory and not current.is_dir():
        raise FileError(400, "请选择普通目录")
    if not directory and not current.is_file():
        raise FileError(400, "请选择普通文件")
    return current


def _bytes(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise FileError(400, "请选择普通文件")
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise FileError(413, "文件超过 2 MiB，请用系统编辑器打开")
    return raw, info


def project_file_path(root: Path, relative: str) -> Path:
    """Validate an existing ordinary project file using the editor boundary."""
    return _entry(root, relative)


def project_directory_path(root: Path, relative: str) -> Path:
    """Apply the same component boundary to an analysis source directory."""
    if relative == '.':
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise FileError(400, "请选择普通目录")
        return resolved
    return _entry(root, relative, directory=True)


def _document(raw: bytes, info: os.stat_result) -> dict:
    if any(value < 32 and value not in {9, 10, 12, 13} for value in raw):
        raise FileError(415, "二进制文件无法在代码编辑器中打开")
    bom = raw.startswith(codecs.BOM_UTF8)
    try:
        text = (raw[len(codecs.BOM_UTF8):] if bom else raw).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FileError(415, "目前仅支持 UTF-8 文本，请用系统编辑器打开") from exc
    endings = set(re.findall(r"\r\n|\r|\n", text))
    eol = {"\r\n": "CRLF", "\r": "CR", "\n": "LF"}.get(next(iter(endings), "\n"), "LF")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    reason = ("文件包含混合换行，仅提供预览" if len(endings) > 1 else
              "单行超过 20000 字符，仅提供预览" if any(len(line) > MAX_LINE_LENGTH for line in normalized.split("\n")) else
              "文件没有写入权限，仅提供预览" if not info.st_mode & stat.S_IWUSR else "")
    return {"content": normalized, "revision": hashlib.sha256(raw).hexdigest(),
            "encoding": "utf-8-bom" if bom else "utf-8", "eol": eol,
            "read_only": bool(reason), "reason": reason, "size": len(raw)}


def read_file(root: Path, relative: str) -> dict:
    try:
        path = _entry(root, relative)
        raw, info = _bytes(path)
        _entry(root, relative)
        return _document(raw, info)
    except FileNotFoundError as exc:
        raise FileError(404, "文件不存在或已被移动") from exc
    except PermissionError as exc:
        raise FileError(403, "没有读取文件的权限") from exc
    except OSError as exc:
        raise FileError(400, "无法读取此项目文件") from exc


def _replace(temporary: str, destination: Path) -> None:
    if os.name != "nt":
        os.replace(temporary, destination)
        return
    # ReplaceFile preserves NTFS DACLs; os.replace would inherit the temp file's ACL.
    # A same-volume backup also allows restoring the original on partial API failure.
    import ctypes
    from ctypes import wintypes

    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                       wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    fd, backup_name = tempfile.mkstemp(prefix=".sona-backup-", dir=destination.parent)
    os.close(fd)
    backup = Path(backup_name)
    backup.unlink()

    def native_path(path: Path) -> str:
        value = str(path.resolve())
        return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value

    if not replace(native_path(destination), native_path(Path(temporary)), native_path(backup), 0, None, None):
        error = ctypes.WinError(ctypes.get_last_error())
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        backup.unlink(missing_ok=True)
        raise error
    backup.unlink(missing_ok=True)


def save_file(root: Path, relative: str, content: str, revision: str) -> dict:
    # Fixed lock stripes serialize application writes without accumulating paths.
    key = str(root.resolve()) + "/" + relative
    lock = _LOCKS[int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(_LOCKS)]
    temporary = None
    try:
        with lock:
            path = _entry(root, relative)
            raw, info = _bytes(path)
            document = _document(raw, info)
            if document["revision"] != revision:
                raise FileError(409, "磁盘文件已被其他程序修改；本地编辑已保留，请重新加载后合并")
            if document["read_only"]:
                raise FileError(422, document["reason"])
            text = content.replace("\r\n", "\n").replace("\r", "\n")
            ending = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}[document["eol"]]
            try:
                updated = (codecs.BOM_UTF8 if document["encoding"] == "utf-8-bom" else b"") + text.replace("\n", ending).encode("utf-8")
            except UnicodeEncodeError as exc:
                raise FileError(422, "文本包含无效的 Unicode 字符") from exc
            if len(updated) > MAX_FILE_BYTES:
                raise FileError(413, "保存后的文件超过 2 MiB")
            # Validate new data too: never create a binary/long-line file through this API.
            _document(updated, info)
            fd, temporary = tempfile.mkstemp(prefix=".sona-edit-", dir=path.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, stat.S_IMODE(info.st_mode))
            current = _entry(root, relative)
            latest, latest_info = _bytes(current)
            if latest != raw or (latest_info.st_dev, latest_info.st_ino) != (info.st_dev, info.st_ino):
                raise FileError(409, "保存期间磁盘文件发生变化，请重新加载后合并")
            _replace(temporary, current)
            temporary = None
            return _document(updated, current.stat())
    except FileNotFoundError as exc:
        raise FileError(404, "文件不存在或已被移动") from exc
    except PermissionError as exc:
        raise FileError(403, "没有写入文件的权限") from exc
    except OSError as exc:
        raise FileError(400, "保存失败，原文件已保留") from exc
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
