"""Catch unsafe definition URIs and UTF-16 position mistakes."""
import os
import subprocess

import pytest

from sona_code.language_services.errors import NavigationError
from sona_code.language_services.paths import file_uri, normalize_definitions, validate_position

SPAN = {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 1}}


def test_uri_location_links_dedup_and_percent_names(tmp_path):
    (tmp_path / '中文 % space.py').write_text('中文😀\r\nx = 1', encoding='utf-8')
    uri = file_uri(tmp_path, '中文 % space.py')
    assert '%25' in uri and '%20' in uri
    targets, rejected = normalize_definitions(tmp_path, [
        {'uri': uri, 'range': SPAN},
        {'targetUri': uri, 'targetRange': SPAN, 'targetSelectionRange': SPAN, 'originSelectionRange': SPAN},
    ])
    assert rejected == []
    assert len(targets) == 1
    assert targets[0]['path'] == '中文 % space.py'
    assert targets[0]['range'] == SPAN
    if os.name == 'nt':
        assert normalize_definitions(tmp_path, {'uri': uri.upper().replace('FILE:', 'file:'), 'range': SPAN})[0][0]['path'] == '中文 % space.py'


def test_outside_virtual_and_unsafe_paths_do_not_open(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    outside = tmp_path / 'secret.py'; outside.write_text('secret', encoding='utf-8')
    targets, rejected = normalize_definitions(root, [
        {'uri': outside.as_uri(), 'range': SPAN}, {'uri': 'jdt://contents/a', 'range': SPAN},
        {'uri': root.as_uri() + '/a.py:secret', 'range': SPAN},
        {'uri': root.as_uri() + '/.git/config', 'range': SPAN},
        {'uri': root.as_uri() + '/%2e%2e/secret.py', 'range': SPAN},
    ])
    assert not targets and len(rejected) == 5
    for path in ('../secret.py', '/secret.py', 'a.py:x', 'a\\b.py', '.git/config'):
        with pytest.raises(NavigationError):
            file_uri(root, path)


@pytest.mark.parametrize('position', [{'line': -1, 'character': 0}, {'line': 2, 'character': 0},
    {'line': 0, 'character': 3}, {'line': 0, 'character': 5}, {'line': True, 'character': 0}])
def test_invalid_utf16_positions(position):
    with pytest.raises(NavigationError):
        validate_position('中文😀\r\nx', position)


def test_utf16_positions_and_stale_target_ranges(tmp_path):
    validate_position('中文😀\r\nx', {'line': 0, 'character': 4})
    validate_position('中文😀\r\nx', {'line': 1, 'character': 1})
    file = tmp_path / 'a.py'; file.write_text('x', encoding='utf-8')
    stale = {'start': {'line': 100, 'character': 30}, 'end': {'line': 101, 'character': 30}}
    assert normalize_definitions(tmp_path, {'uri': file.as_uri(), 'range': stale})[0][0]['range'] == stale
    bad = {'start': {'line': 0, 'character': -1}, 'end': {'line': 0, 'character': 0}}
    assert normalize_definitions(tmp_path, {'uri': file.as_uri(), 'range': bad})[0] == []


def test_binary_and_large_targets_rejected(tmp_path):
    binary = tmp_path / 'a.py'; binary.write_bytes(b'\x00')
    large = tmp_path / 'b.py'; large.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
    assert normalize_definitions(tmp_path, [
        {'uri': p.as_uri(), 'range': SPAN} for p in (binary, large)])[0] == []


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction boundary')
def test_junction_target_rejected(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'a.py').write_text('x')
    link = root / 'link'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        assert normalize_definitions(root, {'uri': (link / 'a.py').as_uri(), 'range': SPAN})[0] == []
    finally:
        link.rmdir()
