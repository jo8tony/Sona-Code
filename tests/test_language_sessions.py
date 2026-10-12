"""Exercise the manager against a real child stdio service, not an RPC mock."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio

from sona_code.language_services.adapters import ServerLaunch
from sona_code.language_services.errors import NavigationError
from sona_code.language_services.manager import LanguageServiceManager


def fake_launch(bundle, family, root, settings, cache):
    return ServerLaunch([sys.executable, '-u', str(Path(__file__).parent / 'helpers/navigation-lsp.py'), '--mode', 'analysis'],
                        dict(os.environ), {'rootUri': root.as_uri(), 'capabilities': {}}, {'python': {'safe': True}}, 3)


@pytest_asyncio.fixture
async def service(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    (project / 'a.py').write_text('original', encoding='utf-8')
    (project / 'target.py').write_text('def target():\n    pass\n', encoding='utf-8')
    (project / 'implementation.py').write_text('def target():\n    pass\n', encoding='utf-8')
    messages = []
    async def emit(message):
        messages.append(message)
    manager = LanguageServiceManager(tmp_path / 'cache', NS(manifest=None), NS(get=lambda p: NS()), launch=fake_launch)
    yield manager, project, messages, emit
    await manager.shutdown()


async def connect(service, editor='window-one'):
    manager, project, messages, emit = service
    session = await manager.connect('p', project, editor, 'python', emit)
    await asyncio.wait_for(session.ready.wait(), 4)
    return session


def opened(text='value = 1', version=1, request_id=1):
    return {'type': 'open', 'id': request_id, 'path': 'a.py', 'language': 'python', 'version': version, 'text': text}


@pytest.mark.asyncio
@pytest.mark.parametrize('method,payload', [
    ('window/showMessage', {'type': 1, 'message': 'Offline build import: dependency unavailable'}),
    ('language/status', {'type': 'Error', 'message': 'Offline build import: dependency unavailable'})])
async def test_offline_import_error_is_visible_and_preserves_drafts(service, method, payload):
    session = await connect(service)
    await session.handle(opened('unsaved'))
    await session._notification(method, payload)
    assert any(m['type'] == 'status' and m['state'] == 'import_failed' and 'dependency' in m['message'] for m in service[2])
    for kind in ('begin', 'end'):
        await session._notification('$/progress', {'value': {'kind': kind}})
        assert service[2][-1]['state'] == 'import_failed' and 'dependency' in service[2][-1]['message']
    assert session.documents['a.py'].text == 'unsaved' and session.ready.is_set()
    await session.handle({'type': 'reanalyze', 'id': 2})
    await asyncio.wait_for(session._restart_task, 4)
    await asyncio.wait_for(session._init_task, 4)
    assert service[2][-1]['state'] == 'ready'


@pytest.mark.asyncio
async def test_build_file_diagnostics_report_missing_cache_until_resolved(service):
    session = await connect(service)
    session.family = 'java'
    session.launch.configuration = {'java': {'import': {'maven': {'enabled': True}}}}
    payload = {'uri': (service[1] / 'pom.xml').as_uri(), 'diagnostics': [
        {'severity': 1, 'message': 'Cannot access central in offline mode: missing artifact'}]}
    await session._notification('textDocument/publishDiagnostics', payload)
    await session._status('ready', 'ready')
    assert service[2][-1]['state'] == 'import_failed' and 'missing artifact' in service[2][-1]['message']
    await session._notification('textDocument/publishDiagnostics', {**payload, 'diagnostics': []})
    assert service[2][-1]['state'] == 'ready'


@pytest.mark.asyncio
async def test_unsaved_buffers_are_isolated_and_never_written(service):
    first = await connect(service)
    second = await connect(service, 'window-two')
    for session, text in ((first, 'value = 1'), (second, 'value = 2')):
        await session.handle(opened(text))
        remote = await session.rpc.request('test/documents', {})
        assert list(remote.values())[0]['text'] == text
    assert (service[1] / 'a.py').read_text() == 'original'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['definition', 'implementation'])
async def test_ordered_changes_reopen_versions_and_target_staleness(service, kind):
    session = await connect(service)
    await session.handle(opened('中文😀\nfoo'))
    await session.handle({'type': 'change', 'id': 2, 'path': 'a.py', 'version': 2, 'text': 'draft'})
    with pytest.raises(NavigationError):
        await session.handle({'type': 'change', 'id': 3, 'path': 'a.py', 'version': 2, 'text': 'stale'})
    before = list((await session.rpc.request('test/documents', {})).values())[0]
    assert before['text'] == 'draft'
    await session.handle({'type': 'close', 'id': 4, 'path': 'a.py'})
    await session.handle(opened('reopened', version=3, request_id=5))
    after = list((await session.rpc.request('test/documents', {})).values())[0]
    assert after['version'] > before['version']
    await session.handle({'type': kind, 'id': 6, 'path': 'a.py', 'version': 3, 'position': {'line': 0, 'character': 1}})
    await session.handle({'type': 'change', 'id': 7, 'path': 'a.py', 'version': 4, 'text': 'changed during query'})
    await asyncio.sleep(.3)
    assert not any(m.get('id') == 6 and m['type'] == kind + '-result' for m in service[2])
    assert any(m.get('id') == 6 and m.get('code') == 'stale' for m in service[2])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['definition', 'implementation'])
async def test_definition_targets_and_cancellation_are_real(service, kind):
    session = await connect(service)
    await session.handle(opened())
    await session.handle({'type': kind, 'id': 2, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
    await asyncio.sleep(.3)
    result = next(m for m in service[2] if m.get('id') == 2 and m['type'] == kind + '-result')
    assert result['targets'][0]['path'] == ('implementation.py' if kind == 'implementation' else 'target.py')
    if kind == 'implementation':
        assert len(result['targets']) == 1
        assert len(result['rejected']) == 2
    assert result['generation'] == session.generation
    await session.handle({'type': kind, 'id': 3, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
    await asyncio.sleep(.03)
    await session.handle({'type': 'cancel', 'id': 4, 'request_id': 3})
    await asyncio.sleep(.1)
    assert await session.rpc.request('test/cancellations', {}) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['definition', 'implementation'])
async def test_limits_and_invalid_messages_leave_snapshot_intact(service, kind):
    session = await connect(service)
    await session.handle(opened())
    for index, message in enumerate([
        {'type': 'change', 'path': 'a.py', 'version': 2, 'text': 'x' * (2 * 1024 * 1024 + 1)},
        {'type': 'open', 'path': '../outside.py', 'version': 2, 'text': 'outside', 'language': 'python'},
        {'type': 'executeCommand', 'command': 'evil'},
        {'type': kind, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': -1}},
    ], 2):
        with pytest.raises(NavigationError):
            await session.handle({'id': index, **message})
    assert session.documents['a.py'].text == 'value = 1'
    for request_id in range(6, 14):
        await session.handle({'type': kind, 'id': request_id, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
    with pytest.raises(NavigationError) as error:
        await session.handle({'type': kind, 'id': 14, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
    assert error.value.code == 'request_limit'


@pytest.mark.asyncio
async def test_duplicate_connection_capacity_and_shutdown(service):
    manager, project, _, emit = service
    sessions = [await manager.connect('p', project, 'window-' + str(index), 'python', emit) for index in range(6)]
    with pytest.raises(NavigationError):
        await manager.connect('p', project, 'window-0', 'python', emit)
    with pytest.raises(NavigationError) as error:
        await manager.connect('p', project, 'window-7', 'python', emit)
    assert error.value.code == 'service_limit'
    await asyncio.wait_for(asyncio.gather(*(s._init_task for s in sessions)), 10)
    assert all(s.ready.is_set() for s in sessions), 'Concurrent services must not starve the default executor'
    await asyncio.wait_for(manager.shutdown(), 10)
    assert all(s.closed for s in sessions)
    assert all(s.rpc is None or s.rpc.process.poll() is not None for s in sessions)
    with pytest.raises(NavigationError):
        await manager.connect('p', project, 'new', 'python', emit)


@pytest.mark.asyncio
async def test_heartbeat_idle_reconnect_and_reanalyze_keep_drafts(service):
    manager = service[0]
    now = [100.0]; manager.clock = lambda: now[0]
    session = await connect(service)
    await session.handle(opened('unsaved'))
    generation = session.generation
    await session.handle({'type': 'reanalyze', 'id': 2})
    await asyncio.wait_for(session.ready.wait(), 4)
    assert session.generation > generation
    assert list((await session.rpc.request('test/documents', {})).values())[0]['text'] == 'unsaved'
    now[0] += 61
    await session.check_expiry()
    assert session.closed
    reopened = await connect(service)
    assert reopened.generation > session.generation
    # Healthy heartbeats do not keep a group with no active documents forever.
    now[0] += 181
    await reopened.handle({'type': 'ping', 'id': 1})
    await reopened.check_expiry()
    assert reopened.closed


@pytest.mark.asyncio
async def test_saved_revision_and_client_requests_do_not_write_project(service):
    from sona_code.workspace.files import read_file
    session = await connect(service)
    await session.handle(opened('unsaved'))
    with pytest.raises(NavigationError):
        await session.handle({'type': 'saved', 'id': 2, 'path': 'a.py', 'revision': 'wrong'})
    await session.handle({'type': 'saved', 'id': 3, 'path': 'a.py', 'revision': read_file(service[1], 'a.py')['revision']})
    assert session.documents['a.py'].text == 'unsaved'
    result = await session.rpc.request('test/client-applyEdit', {})
    assert result['applied'] is False
    with pytest.raises(NavigationError):
        await session.rpc.request('test/client-command', {})
    assert (service[1] / 'a.py').read_text() == 'original'


@pytest.mark.asyncio
async def test_initialization_snapshots_are_bounded_and_ack_only_after_write(service):
    manager, project, messages, emit = service
    session = await manager.connect('p', project, 'early', 'python', emit)
    await session.handle(opened('first'))
    await session.handle({'type': 'change', 'id': 2, 'path': 'a.py', 'version': 2, 'text': 'latest'})
    await asyncio.wait_for(session.ready.wait(), 4)
    assert list((await session.rpc.request('test/documents', {})).values())[0]['text'] == 'latest'
    assert any(m.get('id') == 2 and m['type'] == 'ack' and m['version'] == 2 for m in messages)
    assert any(m.get('id') == 1 and m.get('code') == 'superseded' for m in messages)


@pytest.mark.asyncio
async def test_crashed_service_restarts_with_current_draft(service):
    session = await connect(service)
    await session.handle(opened('unsaved after crash'))
    with pytest.raises(NavigationError):
        await session.rpc.request('exit-now', {})
    await session.check_expiry()
    assert not session.ready.is_set()
    await session.handle({'type': 'reanalyze', 'id': 2})
    await asyncio.wait_for(session.ready.wait(), 4)
    assert list((await session.rpc.request('test/documents', {})).values())[0]['text'] == 'unsaved after crash'


@pytest.mark.asyncio
async def test_close_deleted_file_releases_draft(service):
    session = await connect(service)
    await session.handle(opened())
    (service[1] / 'a.py').unlink()
    await session.handle({'type': 'close', 'id': 2, 'path': 'a.py'})
    assert 'a.py' not in session.documents
    assert await session.rpc.request('test/documents', {}) == {}


@pytest.mark.skipif(os.name != 'nt', reason='Windows owned descendant job')
@pytest.mark.asyncio
async def test_exited_parent_child_is_killed_but_independent_process_survives(service):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    independent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    handle = None
    try:
        session = await connect(service)
        pid = await session.rpc.request('test/spawn-child', {})
        handle = kernel.OpenProcess(0x1000, False, pid)
        assert handle
        with pytest.raises(NavigationError):
            await session.rpc.request('exit-now', {})
        await session.close()
        code = wintypes.DWORD()
        assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        assert code.value != 259
        assert independent.poll() is None
    finally:
        if handle:
            kernel.CloseHandle(handle)
        independent.terminate(); independent.wait(timeout=3)


@pytest.mark.asyncio
async def test_missing_implementation_capability_preserves_definitions(service):
    manager, project, messages, emit = service
    def without_implementation(*args):
        launch = fake_launch(*args)
        launch.argv.append('--no-implementation')
        return launch
    manager.launch = without_implementation
    session = await connect(service)
    await session.handle(opened())
    assert next(m for m in reversed(messages) if m['type'] == 'status')['capabilities']['implementation'] is False
    with pytest.raises(NavigationError) as error:
        await session.handle({'type': 'implementation', 'id': 2, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
    assert error.value.code == 'unsupported_implementation'
    await session.handle({'type': 'definition', 'id': 3, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
    await asyncio.sleep(.3)
    assert next(m for m in messages if m.get('id') == 3)['targets'][0]['path'] == 'target.py'


@pytest.mark.asyncio
async def test_definitions_and_implementations_share_request_limit(service):
    session = await connect(service)
    await session.handle(opened())
    for request_id in range(2, 10):
        await session.handle({'type': 'definition' if request_id % 2 else 'implementation', 'id': request_id, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
    with pytest.raises(NavigationError) as error:
        await session.handle({'type': 'implementation', 'id': 10, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
    assert error.value.code == 'request_limit'
