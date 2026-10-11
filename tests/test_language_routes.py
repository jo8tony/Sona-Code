"""New analysis APIs must enforce origin, registry and lifecycle boundaries."""
import os
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sona_code.app import create_app
from sona_code.config import AppConfig
from sona_code.language_services.adapters import ServerLaunch
from sona_code.language_services.bundle import LanguageBundle
from sona_code.language_services.manager import LanguageServiceManager
from sona_code.workspace.routes import _project_id

EDITOR = '33333333-3333-4333-8333-333333333333'


@pytest.fixture
def analysis_client(tmp_path):
    root = tmp_path / 'project'; root.mkdir(); (root / 'a.py').write_text('original')
    (root / 'target.py').write_text('def target():\n    pass')
    (root / 'implementation.py').write_text('def target():\n    pass')
    app = create_app(AppConfig(default_upstream='', server={'admin_prefix': '/custom'}, recording={'dir': str(tmp_path / 'records')}),
                     config_path=str(tmp_path / 'config.json'))
    runtime = app.state.runtime
    runtime.terminal_projects.add(str(root), 'opencode')
    async def forbidden(*args, **kwargs):
        raise AssertionError('Code analysis must not start OpenCode')
    runtime.workspace.request = forbidden
    def launch(bundle, family, project, settings, cache):
        argv = [sys.executable, '-u', str(Path(__file__).parent / 'helpers/navigation-lsp.py'), '--mode', 'analysis']
        return ServerLaunch(argv, {key: value for key, value in os.environ.items() if key in {'SYSTEMROOT', 'TEMP', 'TMP'}},
                            {'rootUri': project.as_uri()}, {}, 3)
    runtime.language_services = LanguageServiceManager(tmp_path / 'cache', LanguageBundle(tmp_path / 'missing'),
                                                       runtime.analysis_settings, launch=launch)
    with TestClient(app) as client:
        yield client, root, runtime, '/custom/api/workspace/projects/' + _project_id(str(root))
    assert runtime.language_services.closing
    assert runtime.language_services.instances == {}


def test_status_and_settings_do_not_start_processes(analysis_client):
    client, root, runtime, base = analysis_client
    status = client.get('/custom/api/workspace/languages')
    assert status.status_code == 200 and status.json()['available'] is False
    assert runtime.language_services.instances == {}
    assert client.get(base + '/analysis-settings').json()['java']['mode'] == 'source'
    assert client.get('/custom/api/workspace/projects/missing/analysis-settings').status_code == 404
    (root / 'src').mkdir()
    saved = client.put(base + '/analysis-settings', json={'java': {'source_roots': ['src']}}, headers={'origin': 'http://testserver'})
    assert saved.status_code == 200
    assert client.get(base + '/analysis-settings').json()['java']['source_roots'] == ['src']


@pytest.mark.parametrize('origin', [None, 'https://evil.example', 'https://testserver', 'http://testserver:8080'])
def test_missing_or_wrong_origin_rejected(analysis_client, origin):
    client, _, runtime, base = analysis_client
    headers = {'origin': origin} if origin else {}
    assert client.put(base + '/analysis-settings', json={}, headers=headers).status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(base + '/code-navigation/python?editor_id=' + EDITOR, headers=headers):
            pass
    assert runtime.language_services.instances == {}


@pytest.mark.parametrize('suffix', [
    '/code-navigation/shell?editor_id=' + EDITOR,
    '/code-navigation/python?editor_id=invalid',
])
def test_invalid_socket_identity_and_language_rejected(analysis_client, suffix):
    client, _, runtime, base = analysis_client
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(base + suffix, headers={'origin': 'http://testserver'}):
            pass
    assert not runtime.language_services.instances


def until(socket, predicate):
    for _ in range(30):
        message = socket.receive_json()
        if predicate(message):
            return message
    raise AssertionError('Expected reply missing')


@pytest.mark.parametrize('kind', ['definition', 'implementation'])
def test_socket_drafts_definition_and_project_removal(analysis_client, kind):
    client, root, runtime, base = analysis_client
    with client.websocket_connect(base + '/code-navigation/python?editor_id=' + EDITOR, headers={'origin': 'http://testserver'}) as socket:
        socket.send_json({'type': 'open', 'id': 1, 'path': 'a.py', 'language': 'python', 'version': 1, 'text': 'unsaved'})
        until(socket, lambda m: m['type'] == 'ack' and m.get('id') == 1)
        socket.send_json({'type': kind, 'id': 2, 'path': 'a.py', 'version': 1, 'position': {'line': 0, 'character': 1}})
        target = until(socket, lambda m: m['type'] == kind + '-result' or m['type'] == 'error')
        assert target['type'] == kind + '-result'
        assert target['targets'][0]['path'] == ('implementation.py' if kind == 'implementation' else 'target.py')
        assert (root / 'a.py').read_text() == 'original'
        assert client.delete(base).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            while True:
                socket.receive_json()
    assert not runtime.language_services.instances
    assert client.get(base + '/analysis-settings').status_code == 404


def test_malformed_and_oversized_messages_are_bounded(analysis_client):
    client, _, _, base = analysis_client
    with client.websocket_connect(base + '/code-navigation/python?editor_id=' + EDITOR, headers={'origin': 'http://testserver'}) as socket:
        for body in ('[]', '{', '{"type":"ping","id":-1}', '{"type":"ping","id":1,"command":"evil"}'):
            socket.send_text(body)
            assert until(socket, lambda m: m['type'] == 'error')['code'] == 'invalid_message'
        socket.send_text('x' * (4 * 1024 * 1024 + 1))
        with pytest.raises(WebSocketDisconnect) as error:
            while True:
                socket.receive_json()
        assert error.value.code == 1009


def test_binary_messages_are_rejected_without_leaking_session(analysis_client):
    client, _, runtime, base = analysis_client
    with client.websocket_connect(base + '/code-navigation/python?editor_id=' + EDITOR, headers={'origin': 'http://testserver'}) as socket:
        socket.send_bytes(b'not JSON text')
        with pytest.raises(WebSocketDisconnect) as error:
            while True:
                socket.receive_json()
        assert error.value.code == 1003
    async def wait_cleanup():
        async with asyncio.timeout(4):
            while runtime.language_services.instances:
                await asyncio.sleep(.02)
    # A close frame can arrive while the shielded owned-process teardown finishes.
    client.portal.call(wait_cleanup)
    assert not runtime.language_services.instances


def test_second_connection_cannot_take_over_first(analysis_client):
    client, _, runtime, base = analysis_client
    url = base + '/code-navigation/python?editor_id=' + EDITOR
    with client.websocket_connect(url, headers={'origin': 'http://testserver'}) as first:
        with client.websocket_connect(url, headers={'origin': 'http://testserver'}) as second:
            assert second.receive_json()['code'] == 'connection_in_use'
            with pytest.raises(WebSocketDisconnect):
                second.receive_json()
        assert len(runtime.language_services.instances) == 1
        first.send_json({'type': 'ping', 'id': 1})
        assert until(first, lambda m: m['type'] == 'pong')['id'] == 1


def test_settings_update_revokes_existing_connection(analysis_client):
    client, _, runtime, base = analysis_client
    with client.websocket_connect(base + '/code-navigation/python?editor_id=' + EDITOR, headers={'origin': 'http://testserver'}) as socket:
        socket.send_json({'type': 'ping', 'id': 1})
        until(socket, lambda m: m['type'] == 'pong')
        assert client.put(base + '/analysis-settings', json={}, headers={'origin': 'http://testserver'}).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            while True:
                socket.receive_json()
    assert not runtime.language_services.instances


@pytest.mark.asyncio
async def test_application_shutdown_cleans_language_process_when_other_cleanup_fails(tmp_path):
    app = create_app(AppConfig(default_upstream='', recording={'dir': str(tmp_path / 'records')}),
                     config_path=str(tmp_path / 'config.json'))
    runtime = app.state.runtime
    project = tmp_path / 'project'; project.mkdir()
    def launch(bundle, family, root, settings, cache):
        return ServerLaunch([sys.executable, '-u', str(Path(__file__).parent / 'helpers/navigation-lsp.py'), '--mode', 'analysis'],
                            {key: value for key, value in os.environ.items() if key in {'SYSTEMROOT', 'TEMP', 'TMP'}}, {}, {}, 3)
    runtime.language_services.launch = launch
    session = await runtime.language_services.connect('p', project, EDITOR, 'python', lambda message: asyncio.sleep(0))
    await asyncio.wait_for(session.ready.wait(), 4)
    process = session.rpc.process
    async def failing_cleanup():
        raise RuntimeError('fixture cleanup failure')
    runtime.workspace_queue.shutdown = failing_cleanup
    with pytest.raises(RuntimeError):
        await runtime.aclose()
    assert process.poll() is not None
    assert session.closed and not runtime.language_services.instances
