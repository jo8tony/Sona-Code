"""A missing or modified offline component must never reach process launch."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from sona_code.language_services.bundle import LanguageBundle
from sona_code.language_services.errors import NavigationError

ENTRIES = {
    'node': ('22.23.3', 'runtime/node.exe'),
    'jdk': ('21.0.12.1+1', 'runtime/jdk/bin/java.exe'),
    'jdtls': ('1.61.0', 'servers/jdtls/plugins/org.eclipse.equinox.launcher_1.jar'),
    'pyright': ('1.1.414', 'servers/node/node_modules/pyright/langserver.index.js'),
    'typescript_server': ('6.0.2', 'servers/node/node_modules/typescript-language-server/lib/cli.mjs'),
    'typescript': ('6.0.3', 'servers/node/node_modules/typescript/lib/tsserver.js'),
}


@pytest.fixture
def bundle_dir(tmp_path):
    manifest = {'schema': 1, 'platform': 'windows-x64', 'components': {}, 'files': {}, 'inputs': {}, 'sources': [], 'source_coverage': {}}
    for name, (version, entry) in ENTRIES.items():
        path = tmp_path / entry; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(name.encode())
        manifest['components'][name] = {'version': version, 'entry': entry}
        manifest['files'][entry] = hashlib.sha256(path.read_bytes()).hexdigest()
        source = f'sources/{name}.zip'; (tmp_path / 'sources').mkdir(exist_ok=True)
        (tmp_path / source).write_bytes(b'source')
        digest = hashlib.sha256(b'source').hexdigest()
        manifest['files'][source] = digest
        manifest['sources'].append({'component': name, 'version': version, 'path': source, 'sha256': digest})
    (tmp_path / 'servers/jdtls/config_win').mkdir()
    manifest['source_coverage'][ENTRIES['jdtls'][1]] = 'sources/jdtls.zip'
    for entry in ['runtime/jdk/bin/server/jvm.dll', 'runtime/jdk/lib/modules', 'runtime/jdk/release',
                  'servers/jdtls/config_win/config.ini',
                  'servers/node/node_modules/pyright/dist/pyright-langserver.js',
                  'servers/node/node_modules/typescript/lib/_tsserver.js', 'servers/node/node_modules/typescript/lib/typescript.js',
                  'licenses/node/LICENSE', 'licenses/temurin/java.base/LICENSE', 'licenses/jdtls/LICENSE',
                  'licenses/pyright/LICENSE.txt', 'licenses/typescript/LICENSE.txt', 'licenses/typescript-language-server/LICENSE',
                  'licenses/SONA_THIRD_PARTY_NOTICES.md']:
        path = tmp_path / entry; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
        manifest['files'][entry] = hashlib.sha256(b'fixture').hexdigest()
    index = json.dumps({'sources': manifest['sources'], 'coverage': manifest['source_coverage'], 'eclipse': []}).encode()
    (tmp_path / 'sources/manifest.json').write_bytes(index)
    manifest['files']['sources/manifest.json'] = hashlib.sha256(index).hexdigest()
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return tmp_path


def test_complete_bundle_and_fixed_launch_paths(bundle_dir):
    bundle = LanguageBundle(bundle_dir)
    assert bundle.check()['components']['node']['version'] == '22.23.3'
    assert bundle.launch_paths('python')['pyright'].name == 'langserver.index.js'
    assert bundle.launch_paths('java')['java'].name == 'java.exe'


@pytest.mark.parametrize('relative', ['runtime/jdk/bin/server/jvm.dll',
                                     'servers/jdtls/config_win/config.ini', 'sources/manifest.json',
                                     'licenses/temurin/java.base/LICENSE'])
def test_runtime_rejects_omitted_transitive_components(bundle_dir, relative):
    path = bundle_dir / 'manifest.json'; manifest = json.loads(path.read_text())
    del manifest['files'][relative]; (bundle_dir / relative).unlink()
    path.write_text(json.dumps(manifest))
    with pytest.raises(NavigationError):
        LanguageBundle(bundle_dir).check()


@pytest.mark.parametrize('failure', ['removed', 'modified', 'omitted', 'traversal', 'ads', 'version', 'source', 'source-version', 'source-coverage'])
def test_bundle_rejects_incomplete_or_changed_artifacts(bundle_dir, failure):
    path = bundle_dir / 'manifest.json'; manifest = json.loads(path.read_text())
    entry = ENTRIES['node'][1]
    if failure == 'removed':
        (bundle_dir / entry).unlink()
    elif failure == 'modified':
        (bundle_dir / entry).write_bytes(b'tampered')
    elif failure == 'omitted':
        del manifest['files'][entry]
    elif failure in {'traversal', 'ads'}:
        manifest['files']['../outside' if failure == 'traversal' else 'runtime/node.exe:secret'] = '0' * 64
    elif failure == 'version':
        manifest['components']['node']['version'] = '999.0.0'
    elif failure == 'source':
        (bundle_dir / 'sources/jdk.zip').unlink()
    elif failure == 'source-version':
        manifest['sources'][0]['version'] = '0'
    else:
        del manifest['source_coverage']
    path.write_text(json.dumps(manifest))
    with pytest.raises(NavigationError):
        LanguageBundle(bundle_dir).check()


def test_prepare_check_never_downloads_and_rejects_unsafe_archives(tmp_path):
    import zipfile
    spec = importlib.util.spec_from_file_location('prepare_languages', Path(__file__).parents[1] / 'scripts/prepare-language-services.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    archive = tmp_path / 'bad.zip'
    for name in ('../escape', 'a:b', 'a\\b', '/absolute'):
        with zipfile.ZipFile(archive, 'w') as z:
            info = zipfile.ZipInfo(); info.filename = name
            z.writestr(info, 'bad')
        with pytest.raises((NavigationError, RuntimeError)):
            module.extract(archive, tmp_path / 'stage')
        assert not (tmp_path / 'escape').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows preparation entry point')
def test_prepare_reads_component_manifest_independently_of_input_inventory_order(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('prepare_languages', Path(__file__).parents[1] / 'scripts/prepare-language-services.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'INPUTS', ['THIRD_PARTY_NOTICES.md', 'packaging/language-services.json'])
    (tmp_path / 'packaging').mkdir()
    (tmp_path / 'THIRD_PARTY_NOTICES.md').write_text('License notice, not component JSON')
    (tmp_path / 'packaging/language-services.json').write_text(json.dumps({
        'versions': module.VERSIONS, 'archives': {'node.zip': {'url': 'https://example.invalid/pinned'}}}))
    class DownloadReached(Exception):
        pass
    def download(*args):
        raise DownloadReached()
    monkeypatch.setattr(module, 'download', download)
    with pytest.raises(DownloadReached):
        module.prepare(tmp_path / 'build/languages')
