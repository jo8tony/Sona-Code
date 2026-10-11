"""Catch byte framing corruption, lost requests and orphaned LSP processes."""
import asyncio
import io
import subprocess
import sys
from pathlib import Path

import pytest

from sona_code.language_services.errors import NavigationError
from sona_code.language_services.protocol import JsonRpcPeer, encode_frame, read_frame
from sona_code.processes import ProcessScope


def test_frames_count_utf8_bytes_and_preserve_next_frame():
    first = {'jsonrpc': '2.0', 'method': 'note', 'params': {'text': '中文😀'}}
    second = {'jsonrpc': '2.0', 'id': 2, 'result': None}
    stream = io.BytesIO(encode_frame(first) + encode_frame(second))
    assert read_frame(stream) == first
    assert read_frame(stream) == second
    assert read_frame(stream) is None


def test_fragmented_payload_is_read_completely():
    class SplitStream(io.BytesIO):
        def read(self, size=-1):
            return super().read(3 if size < 0 else min(size, 3))
    message = {'jsonrpc': '2.0', 'id': 4, 'result': '中文😀'}
    assert read_frame(SplitStream(encode_frame(message))) == message


@pytest.mark.parametrize('frame', [
    b'Content-Length: -1\r\n\r\n', b'Foo: 2\r\n\r\n{}',
    b'Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}',
    b'Content-Length: 16777217\r\n\r\n', b'Content-Length: x\r\n\r\n',
    b'Content-Length: 2\r\n\r\n[]', b'Content-Length: 3\r\n\r\n{}',
    b'Content-Length: 2\r\n', b'X: ' + b'x' * 8192 + b'\r\n\r\n',
    b'Content-Length: 1\r\n\r\n\xff',
])
def test_invalid_frames_fail_bounded(frame):
    with pytest.raises(NavigationError):
        read_frame(io.BytesIO(frame))


def peer(mode='normal', **kwargs):
    scope = ProcessScope()
    process = scope.spawn([sys.executable, '-u', str(Path(__file__).parent / 'helpers/navigation-lsp.py'), '--mode', mode],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return JsonRpcPeer(process, scope, **kwargs)


@pytest.mark.asyncio
async def test_reordered_split_responses_and_server_configuration():
    async def configuration(method, params):
        assert method == 'workspace/configuration'
        return [{'safe': True}]
    rpc = peer('split', on_request=configuration)
    try:
        result = await rpc.request('initialize', {})
        assert result['configuration'] == [{'safe': True}]
        slow = asyncio.create_task(rpc.request('echo', {'value': 'slow', 'delay': .1}))
        fast = asyncio.create_task(rpc.request('echo', {'value': '中文😀'}))
        assert await fast == '中文😀'
        assert await slow == 'slow'
        with pytest.raises(NavigationError) as error:
            await rpc.request('bad', {})
        assert error.value.code == 'rpc_error'
    finally:
        await rpc.close()
    assert rpc.process.poll() is not None


@pytest.mark.asyncio
async def test_cancel_timeout_and_exit_fail_pending_without_hanging():
    cancelled = asyncio.Event()
    async def notification(method, params):
        if method == 'cancelled':
            cancelled.set()
    rpc = peer(on_notification=notification)
    try:
        task = asyncio.create_task(rpc.request('echo', {'delay': 10, 'value': 'late'}))
        await asyncio.sleep(.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 2)
        with pytest.raises(NavigationError) as error:
            await rpc.request('echo', {'delay': 10}, timeout=.05)
        assert error.value.code == 'timeout'
        with pytest.raises(NavigationError) as error:
            await rpc.request('exit-now', {})
        assert error.value.code == 'service_exit'
    finally:
        await rpc.close()


@pytest.mark.asyncio
async def test_hung_shutdown_kills_owned_process():
    rpc = peer('hang')
    await asyncio.wait_for(rpc.close(), 5)
    assert rpc.process.poll() is not None
    assert rpc.scope.closed


@pytest.mark.asyncio
async def test_malformed_response_fails_pending_immediately():
    rpc = peer()
    try:
        with pytest.raises(NavigationError) as error:
            await rpc.request('bad-id', {}, timeout=.2)
        assert error.value.code != 'timeout'
    finally:
        await rpc.close()


@pytest.mark.asyncio
async def test_parent_exit_fails_request_even_when_child_holds_stdout():
    rpc = peer()
    try:
        await rpc.request('test/spawn-child', {})
        with pytest.raises(NavigationError) as error:
            await rpc.request('exit-now', {}, timeout=.3)
        assert error.value.code == 'service_exit'
    finally:
        await rpc.close()


@pytest.mark.asyncio
async def test_request_deadline_includes_a_blocked_stdin_write():
    rpc = peer()
    task = None
    try:
        await rpc.request('test/stop-reading', {})
        task = asyncio.create_task(rpc.request('echo', {'value': 'x' * (2 * 1024 * 1024)}, timeout=.1))
        await asyncio.wait({task}, timeout=.5)
        assert task.done(), 'request must not hang while writing to a stopped service'
        assert isinstance(task.exception(), NavigationError)
        assert task.exception().code == 'timeout'
    finally:
        await asyncio.to_thread(rpc.scope.close)
        if task:
            task.cancel(); await asyncio.gather(task, return_exceptions=True)
        await rpc.close()
