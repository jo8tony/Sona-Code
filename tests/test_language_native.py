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


@pytest.mark.skipif(os.environ.get('SONACODE_TEST_LANGUAGE_NATIVE') != '1', reason='opt-in real local language services')
@pytest.mark.asyncio
async def test_real_java_interface_and_abstract_implementations(tmp_path):
    import asyncio
    import time
    from types import SimpleNamespace
    from sona_code.language_services.adapters import build_launch
    from sona_code.language_services.bundle import LanguageBundle
    from sona_code.language_services.manager import LanguageServiceManager
    from sona_code.language_services.settings import AnalysisSettings

    project = tmp_path / 'project'
    files = {
        'src/main/java/api/Worker.java': 'package api; public interface Worker { int run(); }\n',
        'src/main/java/impl/One.java': 'package impl; import api.Worker; public class One implements Worker { public int run() { return 1; } }\n',
        'src/main/java/impl/Two.java': 'package impl; import api.Worker; public class Two implements Worker { public int run() { return 2; } }\n',
        'src/main/java/api/Base.java': 'package api; public abstract class Base { public abstract int calculate(); }\n',
        'src/main/java/impl/Child.java': 'package impl; import api.Base; public class Child extends Base { public int calculate() { return 3; } }\n',
    }
    # Build descriptors must not prevent offline source-mode indexing.
    files['pom.xml'] = '<project><modelVersion>4.0.0</modelVersion><groupId>test</groupId><artifactId>source</artifactId><version>1</version></project>'
    for name, content in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    home = tmp_path / 'isolated-home'
    home.mkdir()
    def launch(*args):
        result = build_launch(*args)
        result.argv.insert(1, '-Duser.home=' + str(home))
        result.env.update(USERPROFILE=str(home), HOME=str(home))
        return result
    bundle = LanguageBundle(ROOT / 'build/language-services')
    messages = []
    async def emit(message):
        messages.append(message)
    manager = LanguageServiceManager(tmp_path / 'cache', bundle,
                                    SimpleNamespace(get=lambda _: AnalysisSettings()), launch=launch)
    heartbeat = None
    try:
        session = await manager.connect('java-implementation', project, 'isolated-test', 'java', emit)
        async def ping():
            while True:
                await asyncio.sleep(10)
                # Match the browser heartbeat while cold bundle validation runs.
                session._last_heartbeat = manager.clock()
        heartbeat = asyncio.create_task(ping())
        await asyncio.wait_for(session._init_task, 125)
        assert session.ready.is_set(), messages
        assert messages[-1]['capabilities']['implementation'] is True
        sequence = 0
        for name in ('src/main/java/api/Worker.java', 'src/main/java/api/Base.java'):
            sequence += 1
            await session.handle({'type': 'open', 'id': sequence, 'path': name, 'language': 'java', 'version': 1, 'text': files[name]})
        for kind, source, needle, expected in [
            ('implementation', 'src/main/java/api/Worker.java', 'Worker', {'src/main/java/impl/One.java', 'src/main/java/impl/Two.java'}),
            ('implementation', 'src/main/java/api/Worker.java', 'run(', {'src/main/java/impl/One.java', 'src/main/java/impl/Two.java'}),
            ('implementation', 'src/main/java/api/Base.java', 'calculate(', {'src/main/java/impl/Child.java'}),
        ]:
            deadline = time.monotonic() + 45
            response = None
            while time.monotonic() < deadline:
                sequence += 1
                await session.handle({'type': kind, 'id': sequence, 'path': source, 'version': 1,
                                      'position': smoke_module().symbol_position(files[source], needle)})
                async with asyncio.timeout(16):
                    while not any(m.get('id') == sequence for m in messages):
                        await asyncio.sleep(.02)
                response = next(m for m in messages if m.get('id') == sequence)
                assert response['type'] == kind + '-result', response
                if {target['path'] for target in response['targets']} == expected:
                    break
                await asyncio.sleep(.4)
            assert {target['path'] for target in response['targets']} == expected, response
        assert all((project / name).read_text(encoding='utf-8') == content for name, content in files.items())
        assert not list(project.rglob('.project')) and not list(project.rglob('.classpath'))
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await manager.shutdown()
