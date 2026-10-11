"""App-specific navigation protocol; never forward arbitrary browser RPC."""
from __future__ import annotations

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sona_code.workspace.files import FileError
from sona_code.workspace.routes import registered_project_path
from .bundle import VERSIONS
from .errors import NavigationError
from .settings import AnalysisSettings

router = APIRouter()
MAX_MESSAGE = 4 * 1024 * 1024
Identifier = Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]
RelativePath = Annotated[str, Field(min_length=1, max_length=4096)]


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Identifier


class Open(Message):
    type: Literal['open']
    path: RelativePath
    language: Literal['java', 'python', 'javascript', 'javascriptreact', 'typescript', 'typescriptreact']
    version: Identifier
    text: str = Field(max_length=2 * 1024 * 1024)


class Change(Message):
    type: Literal['change']
    path: RelativePath
    version: Identifier
    text: str = Field(max_length=2 * 1024 * 1024)


class Close(Message):
    type: Literal['close']
    path: RelativePath


class Saved(Message):
    type: Literal['saved']
    path: RelativePath
    revision: str = Field(pattern=r'^[0-9a-f]{64}$')


class Position(BaseModel):
    model_config = ConfigDict(extra='forbid')
    line: int = Field(strict=True, ge=0, le=2147483647)
    character: int = Field(strict=True, ge=0, le=2147483647)


class Definition(Message):
    type: Literal['definition']
    path: RelativePath
    version: Identifier
    position: Position


class Cancel(Message):
    type: Literal['cancel']
    request_id: Identifier


class Reanalyze(Message):
    type: Literal['reanalyze']


class Ping(Message):
    type: Literal['ping']


NAVIGATION_MESSAGE = TypeAdapter(Annotated[Open | Change | Close | Saved | Definition | Cancel | Reanalyze | Ping,
                                         Field(discriminator='type')])


def same_origin(connection) -> bool:
    expected = str(connection.base_url).rstrip('/')
    if expected.startswith('ws:'):
        expected = 'http:' + expected[3:]
    elif expected.startswith('wss:'):
        expected = 'https:' + expected[4:]
    return connection.headers.get('origin', '').rstrip('/') == expected and bool(connection.headers.get('origin'))


@router.get('/workspace/languages')
async def languages(request: Request):
    manager = request.app.state.runtime.language_services
    try:
        await asyncio.to_thread(manager.bundle.check)
        available, message = True, '本地语言服务资源已校验'
    except NavigationError as exc:
        available, message = False, exc.detail
    return {'available': available, 'versions': VERSIONS,
            'families': [{'id': family, 'available': available, 'message': message}
                         for family in ('java', 'python', 'typescript')]}


@router.get('/workspace/projects/{project_id}/analysis-settings')
async def get_settings(project_id: str, request: Request):
    registered_project_path(request.app.state.runtime, project_id)
    try:
        return await asyncio.to_thread(request.app.state.runtime.analysis_settings.get, project_id)
    except NavigationError as exc:
        raise HTTPException(422, exc.detail) from exc


@router.put('/workspace/projects/{project_id}/analysis-settings')
async def save_settings(project_id: str, body: AnalysisSettings, request: Request):
    if not same_origin(request):
        raise HTTPException(403, '仅允许从应用内修改项目分析设置')
    runtime = request.app.state.runtime
    root = registered_project_path(runtime, project_id)
    try:
        await asyncio.to_thread(runtime.analysis_settings.save, project_id, root, body)
        await runtime.language_services.close_project(project_id)
        return body
    except NavigationError as exc:
        raise HTTPException(422, exc.detail) from exc


@router.websocket('/workspace/projects/{project_id}/code-navigation/{family}')
async def navigation_socket(socket: WebSocket, project_id: str, family: str, editor_id: str = ''):
    runtime = socket.app.state.runtime
    try:
        if not same_origin(socket) or family not in {'java', 'python', 'typescript'}:
            raise ValueError()
        editor_id = str(UUID(editor_id))
        root = registered_project_path(runtime, project_id)
        if runtime.closing:
            raise ValueError()
    except (ValueError, HTTPException):
        await socket.close(code=1008)
        return
    await socket.accept()
    connected = True
    send_lock = asyncio.Lock()
    async def emit(message):
        nonlocal connected
        async with send_lock:
            if not connected:
                return
            try:
                await socket.send_json(message)
                if message['type'] == 'status' and message['state'] == 'closed':
                    connected = False
                    await socket.close(code=1001)
            except (RuntimeError, WebSocketDisconnect, OSError):
                connected = False
    session = None
    try:
        session = await runtime.language_services.connect(project_id, root, editor_id, family, emit)
        while connected:
            packet = await socket.receive()
            if packet['type'] == 'websocket.disconnect':
                break
            raw = packet.get('text')
            if not isinstance(raw, str):
                connected = False
                await socket.close(code=1003)
                break
            if len(raw) > MAX_MESSAGE or len(raw.encode('utf-8')) > MAX_MESSAGE:
                await emit({'type': 'error', 'id': None, 'code': 'message_limit', 'message': '导航消息超过 4 MiB'})
                connected = False
                await socket.close(code=1009)
                break
            request_id = None
            try:
                # Verify registry membership on every message; a deleted project
                # cannot keep an old connection alive through settings or ping.
                if registered_project_path(runtime, project_id) != root:
                    raise NavigationError('project_removed', '项目已被移除')
                message = NAVIGATION_MESSAGE.validate_json(raw)
                request_id = message.id
                await session.handle(message.model_dump())
            except (ValidationError, ValueError):
                await emit({'type': 'error', 'id': request_id, 'code': 'invalid_message', 'message': '无效的导航消息'})
            except (NavigationError, FileError) as exc:
                await emit({'type': 'error', 'id': request_id, 'code': getattr(exc, 'code', 'file_unavailable'), 'message': exc.detail})
            except HTTPException:
                connected = False
                await socket.close(code=1008)
    except NavigationError as exc:
        await emit({'type': 'error', 'id': None, 'code': exc.code, 'message': exc.detail})
        connected = False
        await socket.close(code=1013)
    except (WebSocketDisconnect, RuntimeError, UnicodeError):
        connected = False
    finally:
        connected = False
        if session:
            await session.close()
