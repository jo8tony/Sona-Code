"""Project status aggregation and parallel task/session isolation."""

import asyncio
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from llm_api_proxy_recorder.app import create_app
from llm_api_proxy_recorder.config import AppConfig, UpstreamConfig
from llm_api_proxy_recorder.workspace.manager import OpenCodeServer, WorkspaceManager
from llm_api_proxy_recorder.workspace.queue import WorkspaceQueue


async def test_existing_server_statuses_parallel_without_starting_projects():
    manager = WorkspaceManager()
    entered = set()
    ready = asyncio.Event()

    async def respond(request):
        path = request.url.params['directory']
        entered.add(path)
        if len(entered) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 1)
        if path == '/failed':
            return httpx.Response(503)
        return httpx.Response(200, json={'same_id': {'type': 'busy'}})

    clients = [httpx.AsyncClient(base_url='http://local', transport=httpx.MockTransport(respond)) for _ in range(3)]
    for path, client in zip(['/running', '/failed', '/exited'], clients):
        manager._servers[path] = OpenCodeServer(SimpleNamespace(poll=lambda p=path: 0 if p == '/exited' else None), client, 1)
    try:
        statuses = await manager.running_statuses()
        assert statuses['/running']['statuses'] == {'same_id': {'type': 'busy'}}
        assert 'error' in statuses['/failed']
        assert statuses['/exited']['statuses'] == {}
        assert entered == {'/running', '/failed'}
    finally:
        for client in clients:
            await client.aclose()


def test_status_route_maps_only_registered_projects(tmp_path):
    app = create_app(AppConfig(upstreams=[UpstreamConfig(name='local', base_url='http://127.0.0.1:1')], default_upstream='local'), str(tmp_path / 'config.json'))
    paths = []
    for name in ['one', 'two', 'unused']:
        path = tmp_path / name
        path.mkdir()
        paths.append(str(path))
        app.state.runtime.terminal_projects.add(str(path), 'opencode')

    async def statuses():
        return {paths[0]: {'statuses': {'same_id': {'type': 'busy'}}},
                paths[1]: {'statuses': {'same_id': {'type': 'retry'}}},
                '/unregistered': {'statuses': {'secret': {'type': 'busy'}}}}

    app.state.runtime.workspace.running_statuses = statuses
    with TestClient(app) as client:
        projects = client.get('/__recorder/api/workspace/projects').json()['items']
        result = client.get('/__recorder/api/workspace/status').json()['projects']
        assert len(result) == 3
        by_path = {p['path']: result[p['id']]['statuses'] for p in projects}
        assert by_path == {paths[0]: {'same_id': {'type': 'busy'}}, paths[1]: {'same_id': {'type': 'retry'}}, paths[2]: {}}


async def test_queue_workers_and_task_dispatch_do_not_serialize_conversations(tmp_path):
    queue = WorkspaceQueue(str(tmp_path / 'config.json'))
    manager = WorkspaceManager()
    queue.interval = .01
    held = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    seen = []
    messages = {}

    async def inspect(entry):
        return {}, messages.get((entry['project_id'], entry['session_id']), [])

    async def dispatch(entry, item):
        async with manager.task_dispatch():
            key = (entry['project_id'], entry['session_id'])
            seen.append((key, item['payload']['text']))
            if key == ('p1', 'same'):
                held.set()
                await release.wait()
            else:
                messages[key] = [{'info': {'id': item['message_id'], 'role': 'user'}},
                    {'info': {'role': 'assistant', 'parentID': item['message_id'], 'time': {'completed': 1}}}]
                if len(seen) == 3:
                    completed.set()

    queue.inspect, queue.dispatch = inspect, dispatch
    try:
        await queue.add('p1', '/one', 'same', 'prompt', {'text': 'held'})
        await asyncio.wait_for(held.wait(), 1)
        await queue.add('p1', '/one', 'different', 'prompt', {'text': 'same-project'})
        await queue.add('p2', '/two', 'same', 'prompt', {'text': 'different-project'})
        await asyncio.wait_for(completed.wait(), 1)
        assert set(seen) == {(('p1', 'same'), 'held'), (('p1', 'different'), 'same-project'), (('p2', 'same'), 'different-project')}
        assert queue.snapshot('p1', 'same')['items'][0]['payload']['text'] == 'held'
        assert manager._task_requests == 1
    finally:
        release.set()
        await queue.shutdown()
