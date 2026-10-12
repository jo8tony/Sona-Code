"""Translate LSP locations without expanding the editor's file boundary."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sona_code.workspace.files import FileError, project_file_path, read_file
from .errors import NavigationError


def file_uri(root: Path, relative: str) -> str:
    try:
        return project_file_path(root, relative).as_uri()
    except (FileError, OSError, ValueError) as exc:
        raise NavigationError('invalid_path', '无法分析此项目文件') from exc


def _position(position: object) -> tuple[int, int]:
    if not isinstance(position, dict):
        raise NavigationError('invalid_position', '无效的代码位置')
    line, column = position.get('line'), position.get('character')
    if type(line) is not int or type(column) is not int or min(line, column) < 0:
        raise NavigationError('invalid_position', '无效的代码位置')
    return line, column


def validate_position(text: str, position: dict) -> None:
    line, column = _position(position)
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    if line >= len(lines):
        raise NavigationError('invalid_position', '代码行超出文本范围')
    boundaries = {0}
    length = 0
    for char in lines[line]:
        length += 2 if ord(char) > 0xFFFF else 1
        boundaries.add(length)
    if column not in boundaries:
        raise NavigationError('invalid_position', '代码列超出文本范围或拆开 Unicode 字符')


def _range(value: object) -> dict:
    if not isinstance(value, dict) or _position(value.get('start')) > _position(value.get('end')):
        raise NavigationError('invalid_range', '定义范围无效')
    return {'start': dict(value['start']), 'end': dict(value['end'])}


def _relative(root: Path, uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme.lower() != 'file':
        raise NavigationError('virtual_source', '定义位于暂不支持的虚拟源码中')
    if parsed.netloc not in {'', 'localhost'} or parsed.query or parsed.fragment:
        raise NavigationError('external_source', '定义位于项目外')
    value = unquote(parsed.path, errors='strict')
    if os.name == 'nt' and len(value) > 2 and value[0] == '/' and value[2] == ':':
        value = value[1:]
    path = Path(value)
    # Do not resolve a junction before the component-by-component file check.
    relative = path.relative_to(root.resolve()).as_posix()
    checked = project_file_path(root, relative)
    # Retain actual disk casing so Windows URIs cannot create duplicate tabs.
    if os.name == 'nt':
        current = root.resolve()
        parts = []
        for part in Path(relative).parts:
            actual = next(child.name for child in current.iterdir() if child.name.casefold() == part.casefold())
            parts.append(actual)
            current /= actual
        relative = '/'.join(parts)
    read_file(root, relative)
    return relative


def normalize_definitions(root: Path, result: object) -> tuple[list[dict], list[str]]:
    items = result if isinstance(result, list) else [result] if result is not None else []
    targets, rejected, seen = [], [], set()
    paths = {}
    for item in items[:1000]:
        try:
            if not isinstance(item, dict):
                raise NavigationError('invalid_result', '定义结果无效')
            uri = item.get('targetUri', item.get('uri'))
            if not isinstance(uri, str):
                raise NavigationError('invalid_result', '定义地址无效')
            # Reference queries often return hundreds of uses in one file.
            # Validate/read each URI once per response, not once per use.
            if uri not in paths:
                paths[uri] = _relative(root, uri)
            path = paths[uri]
            span = _range(item.get('targetSelectionRange', item.get('range', item.get('targetRange'))))
            origin = item.get('originSelectionRange')
            if origin is not None:
                origin = _range(origin)
            key = (os.path.normcase(path), *_position(span['start']), *_position(span['end']))
            if key not in seen:
                seen.add(key)
                targets.append({'path': path, 'range': span, 'origin': origin})
        except NavigationError as exc:
            rejected.append(exc.code)
        except (FileError, OSError, ValueError, StopIteration):
            rejected.append('unavailable_target')
    return targets, rejected
