"""Real six-language acceptance is explicit opt-in, never a fixture server."""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def smoke_module():
    spec = importlib.util.spec_from_file_location('navigation_smoke', ROOT / 'scripts/smoke-code-navigation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_fixture_inventory_and_utf16_positions():
    smoke = smoke_module()
    cases = smoke.fixture_cases()
    assert {case['language'] for case in cases} == {
        'java', 'python', 'javascript', 'javascriptreact', 'typescript', 'typescriptreact'}
    for case in cases:
        assert case['source'] != case['target'] and case['target'] in case['files']
        assert smoke.symbol_position(case['files'][case['source']], case['needle'])['character'] >= 0
    assert smoke.symbol_position('const x="😀"; answer();\r\n', 'answer(') == {'line': 0, 'character': 14}


@pytest.mark.skipif(os.environ.get('SONACODE_TEST_LANGUAGE_NATIVE') != '1', reason='opt-in real local language services')
def test_real_six_language_offline_navigation(tmp_path):
    report = smoke_module().run_smoke(ROOT / 'build/language-services', tmp_path / 'native.json', offline=True)
    assert report['passed'] and len(report['cases']) == 6
    assert all(case['draft_isolation'] and case['unopened'] for case in report['cases'])
