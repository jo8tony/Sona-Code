"""Analysis settings are app-owned, typed, atomic and bounded by project paths."""
import json
import sys

import pytest
from pydantic import ValidationError

from sona_code.language_services.errors import NavigationError
from sona_code.language_services.settings import AnalysisSettings, AnalysisSettingsStore


def test_settings_round_trip_does_not_write_project(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    (project / 'src').mkdir(); (project / 'lib.jar').write_bytes(b'jar')
    settings = AnalysisSettings(java={'source_roots': ['src'], 'libraries': ['lib.jar']}, python={'interpreter': sys.executable})
    store = AnalysisSettingsStore(tmp_path / 'app')
    store.save('p', project, settings)
    restored = AnalysisSettingsStore(tmp_path / 'app').get('p')
    assert restored.model_dump() == settings.model_dump()
    assert sorted(path.name for path in project.iterdir()) == ['lib.jar', 'src']
    assert store.get('other').java.mode == 'source'
    restored.java.source_roots.append('unpersisted')
    assert store.get('p').java.source_roots == ['src']
    store.remove('p')
    assert store.get('p').java.source_roots == []


@pytest.mark.parametrize('body', [
    {'java': {'mode': 'shell'}}, {'java': {'command': 'evil'}},
    {'python': {'env': {'OPENAI_API_KEY': 'secret'}}}, {'root': '/outside'},
])
def test_unknown_settings_are_rejected(body):
    with pytest.raises(ValidationError):
        AnalysisSettings.model_validate(body)


@pytest.mark.parametrize('body', [
    {'java': {'source_roots': ['../outside']}}, {'java': {'source_roots': ['.git']}},
    {'java': {'libraries': ['lib.jar:secret']}}, {'python': {'interpreter': '--evil'}},
    {'java': {'project_jdk': '/missing-jdk'}}, {'java': {'mode': 'gradle'}},
])
def test_invalid_environment_paths_do_not_persist(tmp_path, body):
    project = tmp_path / 'project'; project.mkdir(); (project / '.git').mkdir()
    store = AnalysisSettingsStore(tmp_path / 'app')
    with pytest.raises(NavigationError):
        store.save('p', project, AnalysisSettings.model_validate(body))
    assert not store.path.exists()


def test_failed_atomic_save_preserves_previous_settings(tmp_path, monkeypatch):
    import sona_code.language_services.settings as module
    project = tmp_path / 'project'; project.mkdir(); (project / 'src').mkdir()
    store = AnalysisSettingsStore(tmp_path / 'app')
    store.save('p', project, AnalysisSettings())
    previous = store.path.read_bytes()
    def failed_replace(*args):
        raise OSError('fixture replacement failure')
    monkeypatch.setattr(module.os, 'replace', failed_replace)
    with pytest.raises(OSError):
        store.save('p', project, AnalysisSettings(java={'source_roots': ['src']}))
    assert store.path.read_bytes() == previous
    assert not list(store.path.parent.glob('.analysis-settings-*'))
