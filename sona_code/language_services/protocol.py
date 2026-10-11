"""Bounded Content-Length JSON-RPC over an owned subprocess's stdio."""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import BinaryIO

from .errors import NavigationError

MAX_HEADER = 8192
MAX_FRAME = 16 * 1024 * 1024


def encode_frame(message: dict) -> bytes:
    if not isinstance(message, dict):
        raise NavigationError('invalid_frame', '语言服务消息必须是对象')
    try:
        payload = json.dumps(message, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError) as exc:
        raise NavigationError('invalid_frame', '语言服务消息编码无效') from exc
    if len(payload) > MAX_FRAME:
        raise NavigationError('frame_limit', '语言服务消息超过限制')
    return f'Content-Length: {len(payload)}\r\n\r\n'.encode('ascii') + payload


def read_frame(stream: BinaryIO) -> dict | None:
    header = bytearray()
    while not header.endswith(b'\r\n\r\n'):
        char = stream.read(1)
        if not char:
            if not header:
                return None
            raise NavigationError('invalid_frame', '语言服务帧头不完整')
        header.extend(char)
        if len(header) > MAX_HEADER:
            raise NavigationError('frame_limit', '语言服务帧头超过限制')
    length = None
    try:
        for line in bytes(header[:-4]).decode('ascii').split('\r\n'):
            name, value = line.split(':', 1)
            if name.lower() == 'content-length':
                if length is not None or not value.strip().isdigit():
                    raise ValueError()
                length = int(value.strip())
        if length is None or length > MAX_FRAME:
            raise ValueError()
        payload = bytearray()
        while len(payload) < length:
            chunk = stream.read(length - len(payload))
            if not chunk:
                raise ValueError()
            payload.extend(chunk)
        message = json.loads(payload)
        if not isinstance(message, dict):
            raise ValueError()
        return message
    except (ValueError, UnicodeError) as exc:
        raise NavigationError('invalid_frame', '语言服务帧格式无效') from exc


class JsonRpcPeer:
    def __init__(self, process, scope, on_request=None, on_notification=None):
        self.process, self.scope = process, scope
        self.on_request, self.on_notification = on_request, on_notification
        self.pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._closing = False
        self._failure = None
        self.stderr_tail = bytearray()
        self._handlers = set()
        self._reader = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._drain_stderr())
        self._close_task = None

    async def _write(self, message: dict) -> None:
        data = encode_frame(message)
        async with self._write_lock:
            if self._failure:
                raise self._failure
            try:
                await asyncio.to_thread(self._write_bytes, data)
            except (OSError, ValueError) as exc:
                raise NavigationError('service_exit', '语言服务已退出') from exc

    def _write_bytes(self, data):
        self.process.stdin.write(data)
        self.process.stdin.flush()

    async def request(self, method: str, params: dict, timeout: float = 15) -> object:
        if self._closing and method != 'shutdown':
            raise NavigationError('service_exit', '语言服务正在关闭')
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._write({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
            return await asyncio.wait_for(future, timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError) as exc:
            try:
                await self.notify('$/cancelRequest', {'id': request_id})
            except NavigationError:
                pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise NavigationError('timeout', '语言服务请求超时') from exc
        finally:
            self.pending.pop(request_id, None)

    async def notify(self, method: str, params: dict) -> None:
        await self._write({'jsonrpc': '2.0', 'method': method, 'params': params})

    async def _call(self, callback, *args):
        result = callback(*args)
        return await result if inspect.isawaitable(result) else result

    async def _handle(self, message):
        method, request_id = message['method'], message.get('id')
        try:
            if 'id' in message:
                if self.on_request:
                    result = await self._call(self.on_request, method, message.get('params', {}))
                    response = {'result': result}
                else:
                    response = {'error': {'code': -32601, 'message': 'Unsupported client request'}}
                await self._write({'jsonrpc': '2.0', 'id': request_id, **response})
            elif self.on_notification:
                await self._call(self.on_notification, method, message.get('params', {}))
        except Exception:
            if 'id' in message and not self._failure:
                await self._write({'jsonrpc': '2.0', 'id': request_id,
                                   'error': {'code': -32601, 'message': 'Client request rejected'}})

    async def _read(self):
        try:
            while True:
                message = await asyncio.to_thread(read_frame, self.process.stdout)
                if message is None:
                    raise NavigationError('service_exit', '语言服务已退出')
                if isinstance(message.get('method'), str):
                    task = asyncio.create_task(self._handle(message))
                    self._handlers.add(task)
                    task.add_done_callback(self._handlers.discard)
                else:
                    future = self.pending.get(message.get('id'))
                    if future is not None and not future.done():
                        if 'error' in message:
                            future.set_exception(NavigationError('rpc_error', '语言服务无法完成此请求'))
                        else:
                            future.set_result(message.get('result'))
        except (NavigationError, OSError, ValueError) as exc:
            self._failure = exc if isinstance(exc, NavigationError) else NavigationError('service_exit', '语言服务连接已关闭')
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(self._failure)

    async def _drain_stderr(self):
        try:
            while True:
                data = await asyncio.to_thread(self.process.stderr.read1, 4096)
                if not data:
                    break
                self.stderr_tail.extend(data)
                del self.stderr_tail[:-128 * 1024]
        except (OSError, ValueError):
            pass

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        self._closing = True
        try:
            await self.request('shutdown', {}, timeout=2)
            await self.notify('exit', {})
            await asyncio.to_thread(self.process.wait, timeout=1)
        except Exception:
            pass
        finally:
            await asyncio.to_thread(self.scope.close)
            await asyncio.gather(self._reader, self._stderr, return_exceptions=True)
            for task in list(self._handlers):
                task.cancel()
            await asyncio.gather(*self._handlers, return_exceptions=True)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
