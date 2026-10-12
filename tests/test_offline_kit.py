"""Offline build payloads cannot silently use stale dependencies or escaped paths."""
import hashlib
import importlib.util
import json
import shutil
import sys
import os
import re
import subprocess
from pathlib import Path

import pytest
from tests.test_language_bundle import bundle_dir


def kit_module():
    spec = importlib.util.spec_from_file_location("offline_kit", Path(__file__).parents[1] / "scripts/offline-kit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_inputs_exist_in_repository():
    module = kit_module()
    root = Path(__file__).parents[1]
    assert all((root / path).is_file() for path in module.INPUTS)


def test_offline_inputs_include_language_pins_and_license_producer():
    assert {'packaging/language-services.json', 'packaging/language-services-package/package.json',
            'packaging/language-services-package/package-lock.json', 'scripts/prepare-language-services.py',
            'THIRD_PARTY_NOTICES.md'} <= set(kit_module().INPUTS)


@pytest.fixture
def payload(tmp_path, bundle_dir):
    kit, source = tmp_path / "kit", tmp_path / "source"
    kit.mkdir(); source.mkdir()
    (kit / "asset").write_bytes(b"pinned dependency")
    for name in kit_module().INPUTS:
        path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'lock')
    target = kit / 'language-services'; target.mkdir()
    for name in ['runtime', 'servers', 'sources', 'licenses']:
        shutil.copytree(bundle_dir / name, target / name)
    language_manifest = json.loads((bundle_dir / 'manifest.json').read_text())
    language_manifest['inputs'] = {name: hashlib.sha256(b'lock').hexdigest() for name in [
        'packaging/language-services.json', 'packaging/language-services-package/package.json',
        'packaging/language-services-package/package-lock.json', 'scripts/prepare-language-services.py', 'THIRD_PARTY_NOTICES.md']}
    (target / 'manifest.json').write_text(json.dumps(language_manifest))
    manifest = {'platform': 'windows-x64', 'python': list(sys.version_info[:2]),
                'files': {path.relative_to(kit).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in kit.rglob('*') if path.is_file()},
                'source_inputs': {name: hashlib.sha256(b'lock').hexdigest() for name in kit_module().INPUTS}}
    (kit / "manifest.json").write_text(json.dumps(manifest))
    return kit, source, manifest


def test_verified_payload_and_corruption(payload):
    kit, source, _ = payload
    module = kit_module()
    module.verify_kit(kit, source)
    (kit / "asset").write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="asset"):
        module.verify_kit(kit, source)


def test_changed_dependency_lock_requires_new_kit(payload):
    kit, source, _ = payload
    (source / "package-lock.json").write_bytes(b"new lock")
    with pytest.raises(RuntimeError, match="package-lock"):
        kit_module().verify_kit(kit, source)


@pytest.mark.parametrize('section,name', [
    ('source_inputs', 'packaging/language-services.json'),
    ('files', 'language-services/manifest.json'), ('files', 'language-services/sources/jdk.zip')])
def test_offline_manifest_cannot_omit_language_inputs_or_payload(payload, section, name):
    kit, source, manifest = payload
    del manifest[section][name]
    (kit / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        kit_module().verify_kit(kit, source)


def test_language_producer_inputs_must_match_outer_kit_inputs(payload):
    kit, source, manifest = payload
    name = 'packaging/language-services.json'
    (source / name).write_bytes(b'new pin')
    manifest['source_inputs'][name] = hashlib.sha256(b'new pin').hexdigest()
    (kit / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='input'):
        kit_module().verify_kit(kit, source)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/absolute", "a\\escape"])
def test_manifest_paths_must_be_relative(payload, path):
    kit, source, manifest = payload
    manifest["files"] = {path: "0" * 64}
    (kit / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="path"):
        kit_module().verify_kit(kit, source)


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction deletion boundary')
@pytest.mark.parametrize('link_kind', ['ancestor', 'target', 'none'])
def test_offline_copy_rejects_target_and_ancestor_junctions(tmp_path, link_kind):
    project, external, prepared = [tmp_path / name for name in ('project', 'external', 'prepared')]
    for directory in (project, external, prepared):
        directory.mkdir()
    (prepared / 'asset').write_text('new')
    victim = external / 'language-services'; victim.mkdir()
    sentinel = victim / 'sentinel'; sentinel.write_text('retain')
    target = project / 'build' / 'language-services'
    if link_kind != 'ancestor':
        target.parent.mkdir()
    root = Path(__file__).parents[1]
    script = (root / 'scripts/build-offline-windows.ps1').read_text(encoding='utf-8')
    helper = re.search(r'(?ms)^function Copy-PreparedDirectory\b.*?^}', script).group(0)
    quote = lambda path: "'" + str(path).replace("'", "''") + "'"
    setup = ''
    if link_kind != 'none':
        link = target.parent if link_kind == 'ancestor' else target
        destination = external if link_kind == 'ancestor' else victim
        setup = f'New-Item -ItemType Junction -Path {quote(link)} -Target {quote(destination)} | Out-Null\n'
    test_script = tmp_path / 'copy-test.ps1'
    test_script.write_text("$ErrorActionPreference = 'Stop'\n$ProjectDir = " + quote(project) + '\n' +
                           setup + helper + '\nCopy-PreparedDirectory ' + quote(prepared) + ' ' + quote(target), encoding='utf-8')
    result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(test_script)],
                            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert sentinel.read_text() == 'retain', 'Recursive copy must never delete outside the project'
    if link_kind == 'none':
        assert result.returncode == 0, result.stderr
        assert (target / 'asset').read_text() == 'new'
    else:
        assert result.returncode != 0, 'Junction must be rejected before deletion or copying'
        assert not (victim / 'asset').exists()
