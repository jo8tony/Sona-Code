"""Launch behavior: fixed tools, constrained environment and offline configuration."""
from pathlib import Path
from types import SimpleNamespace

from sona_code.language_services.adapters import build_launch


def settings(mode='source'):
    return SimpleNamespace(java=SimpleNamespace(mode=mode, source_roots=[], libraries=[], project_jdk=None, gradle_home=None),
                           python=SimpleNamespace(interpreter=None))


class Bundle:
    def __init__(self, root):
        self.root = root
    def launch_paths(self, family):
        return {name: self.root / path for name, path in {
            'node': 'node.exe', 'java': 'jdk/bin/java.exe', 'pyright': 'langserver.index.js',
            'typescript_server': 'cli.mjs', 'typescript': 'tsserver.js', 'equinox': 'launcher.jar',
            'configuration': 'config_win'}.items()}


def test_node_launch_is_pinned_and_does_not_inherit_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sensitive')
    monkeypatch.setenv('NODE_OPTIONS', '--require malicious.js')
    monkeypatch.setenv('CLIENT_PORT', '1234')
    for family in ('python', 'typescript'):
        launch = build_launch(Bundle(tmp_path), family, tmp_path, settings(), tmp_path / 'cache')
        assert launch.argv[0] == str(tmp_path / 'node.exe')
        assert '--max-old-space-size=512' in launch.argv and launch.argv[-1] == '--stdio'
        assert all(key not in launch.env for key in ('OPENAI_API_KEY', 'NODE_OPTIONS', 'CLIENT_PORT'))
        assert launch.initialize['rootUri'] == tmp_path.as_uri()
        assert launch.initialize['capabilities']['general']['positionEncodings'] == ['utf-16']
    assert launch.initialize['initializationOptions']['tsserver']['path'] == str(tmp_path / 'tsserver.js')
    assert launch.initialize['initializationOptions']['disableAutomaticTypingAcquisition'] is True
    assert launch.initialize['initializationOptions']['plugins'] == []


def test_java_source_mode_isolated_cache_and_build_import_opt_in(tmp_path, monkeypatch):
    (tmp_path / 'config_win').mkdir(); (tmp_path / 'config_win/config.ini').write_text('fixture')
    source = tmp_path / 'src/main/java'; source.mkdir(parents=True)
    monkeypatch.setenv('JAVA_TOOL_OPTIONS', '-javaagent:malicious.jar')
    for mode in ('source', 'maven', 'gradle'):
        cache = tmp_path / ('cache-' + mode)
        config = settings(mode)
        if mode == 'gradle':
            config.java.gradle_home = str(tmp_path / 'local-gradle')
        launch = build_launch(Bundle(tmp_path), 'java', tmp_path, config, cache)
        assert '-Xmx1G' in launch.argv
        assert launch.argv[-1] == str(cache / 'workspace')
        assert (cache / 'configuration/config.ini').read_text() == 'fixture'
        assert 'JAVA_TOOL_OPTIONS' not in launch.env
        java = launch.configuration['java']
        assert java['autobuild']['enabled'] is False
        assert java['import']['maven']['enabled'] is (mode == 'maven')
        assert java['import']['gradle']['enabled'] is (mode == 'gradle')
        assert java['import']['gradle']['offline']['enabled'] is True
        assert java['import']['gradle']['wrapper']['enabled'] is False
        assert 'src/main/java' in java['project']['sourcePaths']


def test_java_does_not_activate_optional_annotation_processors(tmp_path):
    folder = tmp_path / 'config_win'; folder.mkdir()
    (folder / 'config.ini').write_text('osgi.bundles=reference\\:file\\:org.eclipse.m2e.apt.core_2.jar@4,reference\\:file\\:org.eclipse.jdt.apt.pluggable.core_1.jar@4,reference\\:file\\:org.eclipse.jdt.ls.core_1.jar@4\\:start\n')
    build_launch(Bundle(tmp_path), 'java', tmp_path, settings(), tmp_path / 'cache')
    config = (tmp_path / 'cache/configuration/config.ini').read_text()
    assert 'm2e.apt.core_' not in config
    assert 'jdt.apt.pluggable.core_' not in config
    assert 'org.eclipse.jdt.ls.core_' in config
