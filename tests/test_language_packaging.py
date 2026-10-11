"""Packaged navigation components and corresponding source must be complete."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture
def language_payload(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required for package verification')
    root = tmp_path / 'language-services'
    subprocess.run([node, '-e', "require('./tests/helpers/language-bundle.cjs').languageBundleFixture(process.argv[1])", str(root)], cwd=ROOT, check=True)
    def run():
        return subprocess.run([node, '-e', "require('./electron/verify-package.cjs').verifyLanguageServicesBundle(process.argv[1])", str(root)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=20)
    return root, run


def test_verified_language_package(language_payload):
    _, run = language_payload
    result = run()
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('relative', [
    'runtime/node.exe', 'runtime/jdk/bin/java.exe', 'runtime/jdk/bin/server/jvm.dll',
    'servers/node/node_modules/pyright/langserver.index.js',
    'servers/node/node_modules/typescript/lib/_tsserver.js',
    'servers/jdtls/plugins/org.eclipse.equinox.launcher_1.jar',
    'servers/jdtls/config_win/config.ini', 'sources/jdk.zip',
    'licenses/temurin/java.base/LICENSE', 'sources/manifest.json',
])
@pytest.mark.parametrize('change', ['missing', 'corrupt', 'unlisted'])
def test_package_rejects_missing_corrupt_or_omitted_components(language_payload, relative, change):
    root, run = language_payload
    if change == 'missing':
        (root / relative).unlink()
    elif change == 'corrupt':
        (root / relative).write_bytes(b'corrupt')
    else:
        manifest = json.loads((root / 'manifest.json').read_text())
        del manifest['files'][relative]
        (root / 'manifest.json').write_text(json.dumps(manifest))
    result = run()
    assert result.returncode != 0
    assert relative in result.stderr


@pytest.mark.parametrize('change', ['version', 'source-version', 'coverage', 'inputs', 'source-index'])
def test_package_checks_versions_sources_and_producer_inputs(language_payload, change):
    root, run = language_payload
    path = root / 'manifest.json'; manifest = json.loads(path.read_text())
    if change == 'version':
        manifest['components']['jdk']['version'] = '99'
    elif change == 'source-version':
        manifest['sources'][0]['version'] = '99'
    elif change == 'coverage':
        manifest['source_coverage'] = {}
    elif change == 'inputs':
        manifest['inputs']['packaging/language-services.json'] = '0' * 64
    else:
        (root / 'sources/manifest.json').write_text('{}')
        import hashlib
        manifest['files']['sources/manifest.json'] = hashlib.sha256(b'{}').hexdigest()
    path.write_text(json.dumps(manifest))
    result = run()
    assert result.returncode != 0, change


@pytest.mark.parametrize('unsafe', ['../escape', 'runtime/node.exe:secret', 'runtime\\node.exe', '/absolute', 'runtime//node.exe'])
def test_package_rejects_unsafe_manifest_paths(language_payload, unsafe):
    root, run = language_payload
    path = root / 'manifest.json'; manifest = json.loads(path.read_text())
    manifest['files'][unsafe] = '0' * 64; path.write_text(json.dumps(manifest))
    assert run().returncode != 0
