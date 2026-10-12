"""The installed app must prepare fresh native plugin dependencies locally."""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

import pytest

from sona_code import native_sdk


def bundle(tmp_path, extra=None):
    source = tmp_path / 'bundle'
    source.mkdir()
    files = {'package.json': b'{"dependencies":{"@opencode-ai/plugin":"1.18.32"}}',
             'package-lock.json': b'{"lockfileVersion":3,"packages":{"":{"dependencies":{"@opencode-ai/plugin":"1.18.32"}}}}',
             'node_modules/@opencode-ai/plugin/package.json': b'{"version":"1.18.32"}',
             'node_modules/@opencode-ai/plugin/dist/index.js': b'export {};'}
    files.update(extra or {})
    with zipfile.ZipFile(source / 'config.zip', 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    (source / 'manifest.json').write_text(json.dumps({'version': '1.18.32',
        'archive_sha256': hashlib.sha256((source / 'config.zip').read_bytes()).hexdigest()}))
    return source


def test_fresh_native_config_is_seeded_without_changing_app_config(tmp_path):
    source = bundle(tmp_path)
    destination = tmp_path / 'config'
    destination.mkdir()
    config = destination / 'opencode.jsonc'
    config.write_text('// preserve\n{}')
    native_sdk.seed_config(destination, source)
    assert json.loads((destination / 'node_modules/@opencode-ai/plugin/package.json').read_text())['version'] == '1.18.32'
    assert (destination / 'package-lock.json').is_file()
    assert config.read_text() == '// preserve\n{}'
    native_sdk.seed_config(destination, source)


def test_existing_dependency_configuration_is_preserved(tmp_path):
    source = bundle(tmp_path)
    destination = tmp_path / 'config'
    destination.mkdir()
    (destination / 'package.json').write_text('{"dependencies":{"custom":"1"}}')
    native_sdk.seed_config(destination, source)
    assert json.loads((destination / 'package.json').read_text()) == {'dependencies': {'custom': '1'}}
    assert not (destination / 'node_modules').exists()


def test_bundle_only_seeds_the_app_config_without_directory_overrides(tmp_path, monkeypatch):
    source = bundle(tmp_path)
    source.rename(tmp_path / 'opencode-sdk')
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    config = tmp_path / 'app-config'
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config))
    custom = tmp_path / 'custom'
    native_sdk.prepare_bundled_sdk({'XDG_CONFIG_HOME': str(custom)})
    native_sdk.prepare_bundled_sdk({'XDG_CONFIG_HOME': str(config), 'OPENCODE_CONFIG_DIR': str(custom)})
    assert not custom.exists()
    assert not config.exists()
    native_sdk.prepare_bundled_sdk({'XDG_CONFIG_HOME': str(config)})
    assert (config / 'opencode/package-lock.json').is_file()


def test_current_project_openspec_config_is_seeded_without_creating_other_configs(tmp_path, monkeypatch):
    source = bundle(tmp_path)
    source.rename(tmp_path / 'opencode-sdk')
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    config = tmp_path / 'app-config'
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config))
    project = tmp_path / 'project'
    project.mkdir()
    native_sdk.prepare_bundled_sdk({'XDG_CONFIG_HOME': str(config)}, str(project))
    assert not (project / '.opencode').exists()
    skills = project / '.opencode/skills'
    skills.mkdir(parents=True)
    native_sdk.prepare_bundled_sdk({'XDG_CONFIG_HOME': str(config)}, str(project))
    assert skills.is_dir()
    assert (project / '.opencode/package-lock.json').is_file()


@pytest.mark.parametrize('dangling', [False, True])
def test_linked_destination_is_rejected(tmp_path, dangling):
    source = bundle(tmp_path)
    outside = tmp_path / 'outside'
    if not dangling:
        outside.mkdir()
    destination = tmp_path / 'linked'
    try:
        os.symlink(outside, destination, target_is_directory=True)
    except OSError:
        pytest.skip('symbolic link privileges required')
    with pytest.raises(RuntimeError, match='Linked'):
        native_sdk.seed_config(destination, source)
    assert not (outside / 'node_modules').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction regression')
@pytest.mark.parametrize('dangling', [False, True])
def test_junction_destination_is_rejected(tmp_path, dangling):
    source = bundle(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    destination = tmp_path / 'linked'
    result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(destination), str(outside)],
                            capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    try:
        if dangling:
            outside.rmdir()
        with pytest.raises(RuntimeError, match='Linked'):
            native_sdk.seed_config(destination, source)
        assert not (outside / 'node_modules').exists()
    finally:
        destination.rmdir()


@pytest.mark.skipif(os.name != 'nt', reason='Windows shared dependency regression')
def test_existing_shared_node_modules_is_preserved(tmp_path):
    source = bundle(tmp_path)
    outside = tmp_path / 'shared'
    outside.mkdir()
    destination = tmp_path / 'config'
    destination.mkdir()
    linked = destination / 'node_modules'
    result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(linked), str(outside)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    try:
        native_sdk.seed_config(destination, source)
        assert not (destination / 'package.json').exists()
        assert not list(outside.iterdir())
    finally:
        linked.rmdir()


@pytest.mark.parametrize('scenario', ['tampered', 'traversal'])
def test_invalid_bundle_is_rejected_before_writing_config(tmp_path, scenario):
    source = bundle(tmp_path, {'../outside': b'bad'} if scenario == 'traversal' else None)
    if scenario == 'tampered':
        with (source / 'config.zip').open('ab') as stream:
            stream.write(b'changed')
    destination = tmp_path / 'config'
    with pytest.raises(RuntimeError):
        native_sdk.seed_config(destination, source)
    assert not destination.exists()
    assert not (tmp_path / 'outside').exists()
