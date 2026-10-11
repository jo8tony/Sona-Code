"""Isolated editor/project/language sessions and ordered draft synchronization."""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from sona_code.processes import ProcessScope
from sona_code.workspace.files import MAX_FILE_BYTES, read_file
from .adapters import build_launch
from .bundle import VERSIONS
from .errors import NavigationError
from .paths import file_uri, normalize_definitions, validate_position
from .protocol import JsonRpcPeer
from .watcher import CONFIGS, ProjectWatcher

LANGUAGES = {'java': {'java'}, 'python': {'python'},
             'typescript': {'javascript', 'javascriptreact', 'typescript', 'typescriptreact'}}


@dataclass
class OpenDocument:
    path: str
    language: str
    text: str
    version: int
    epoch: int


def _positive(value):
    return type(value) is int and 0 < value <= 9007199254740991


def _end(text):
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return {'line': len(lines) - 1, 'character': len(lines[-1].encode('utf-16-le')) // 2}


class LanguageServiceManager:
    def __init__(self, cache_root: Path, bundle, settings_store, *, launch=build_launch, clock=time.monotonic):
        self.cache_root, self.bundle, self.settings_store = cache_root, bundle, settings_store
        self.launch, self.clock = launch, clock
        self.instances = {}
        self.closing = False
        self._generation = 0

    def generation(self):
        self._generation += 1
        return self._generation

    async def connect(self, project_id: str, root: Path, editor_id: str, family: str, emit):
        if self.closing:
            raise NavigationError('closing', '应用正在关闭')
        if family not in LANGUAGES:
            raise NavigationError('unsupported_language', '此语言暂不支持项目跳转')
        key = (project_id, editor_id, family)
        if key in self.instances:
            raise NavigationError('connection_in_use', '此编辑器的语言连接已存在')
        if len(self.instances) >= 6:
            raise NavigationError('service_limit', '已有 6 个语言服务在运行，请关闭其他编辑器或稍后重试')
        session = NavigationSession(self, key, root.resolve(), emit)
        self.instances[key] = session
        session.start()
        return session

    async def close_project(self, project_id: str) -> None:
        await asyncio.gather(*(s.close() for key, s in list(self.instances.items()) if key[0] == project_id))

    async def shutdown(self) -> None:
        self.closing = True
        await asyncio.gather(*(s.close() for s in list(self.instances.values())))


class NavigationSession:
    def __init__(self, manager, key, root, emit):
        self.manager, self.key, self.root, self.emit = manager, key, root, emit
        self.project_id, self.editor_id, self.family = key
        self.documents = {}
        self.generation = manager.generation()
        self.revision = 0
        self.rpc = self.watcher = None
        self.ready = asyncio.Event()
        self.closed = False
        self._failure = None
        self._build_errors = {}
        self._sync_kind = 1
        self._save_text = False
        self._versions, self._epochs, self._pending_acks, self._queries = {}, {}, {}, {}
        self._last_id = 0
        self._last_heartbeat = self._last_activity = manager.clock()
        self._sync_lock = asyncio.Lock()
        self._init_task = self._restart_task = self._expiry_task = self._close_task = None

    def start(self):
        self._init_task = asyncio.create_task(self._initialize())
        self._expiry_task = asyncio.create_task(self._expiry())

    async def _status(self, state, message):
        if state == 'ready' and self._build_errors:
            state, message = 'import_failed', '离线构建导入失败，可检查缓存或切回源码模式：' + next(iter(self._build_errors.values()))
        await self.emit({'type': 'status', 'state': state, 'message': str(message)[:512], 'generation': self.generation})

    async def _initialize(self):
        try:
            await self._status('initializing', '正在启动本地语言服务')
            identity = hashlib.sha256(('\0'.join(self.key) + '\0' + str(self.root) + '\0'
                                       + ','.join(VERSIONS.values())).encode()).hexdigest()
            cache = self.manager.cache_root / self.family / identity
            settings = self.manager.settings_store.get(self.project_id)
            self.launch = await asyncio.to_thread(self.manager.launch, self.manager.bundle, self.family, self.root, settings, cache)
            if self.closed:
                return
            scope = ProcessScope()
            process = scope.spawn(self.launch.argv, cwd=self.root, env=self.launch.env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.rpc = JsonRpcPeer(process, scope, self._client_request, self._notification)
            result = await self.rpc.request('initialize', self.launch.initialize, self.launch.timeout)
            capabilities = result.get('capabilities', {})
            if not capabilities.get('definitionProvider'):
                raise NavigationError('unsupported_service', '语言服务未提供定义查询')
            sync = capabilities.get('textDocumentSync', 0)
            self._sync_kind = sync.get('change', 0) if isinstance(sync, dict) else sync
            save = sync.get('save', {}) if isinstance(sync, dict) else {}
            self._save_text = isinstance(save, dict) and save.get('includeText', False)
            if self._sync_kind not in {1, 2}:
                raise NavigationError('unsupported_service', '语言服务不支持草稿同步')
            await self.rpc.notify('initialized', {})
            await self.rpc.notify('workspace/didChangeConfiguration', {'settings': self.launch.configuration})
            async with self._sync_lock:
                for document in self.documents.values():
                    await self._did_open(document)
                self.ready.set()
                for path, request_id in list(self._pending_acks.items()):
                    await self._ack(request_id, self.documents[path])
                self._pending_acks.clear()
            self.watcher = ProjectWatcher(self.root, self.family, self._watched)
            await self.watcher.start()
            await self._status('ready', '本地语言服务已就绪')
            if self.watcher.limited:
                await self._status('limited', '项目文件过多，部分外部变化需手动重新分析')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.ready.clear()
            self._failure = exc if isinstance(exc, NavigationError) else NavigationError('service_unavailable', '本地语言服务启动失败')
            if self.rpc:
                await self.rpc.close()
            await self._status('unavailable', self._failure.detail)
            for request_id in self._pending_acks.values():
                await self._error(request_id, self._failure)
            self._pending_acks.clear()

    async def _client_request(self, method, params):
        if method == 'workspace/configuration':
            result = []
            for item in params.get('items', [])[:100]:
                value = self.launch.configuration
                for part in item.get('section', '').split('.'):
                    if part:
                        value = value.get(part) if isinstance(value, dict) else None
                result.append(value)
            return result
        if method == 'workspace/workspaceFolders':
            return self.launch.initialize.get('workspaceFolders', [{'uri': self.root.as_uri(), 'name': self.root.name}])
        if method == 'workspace/applyEdit':
            return {'applied': False, 'failureReason': 'Sona language analysis does not edit files'}
        if method in {'window/workDoneProgress/create', 'client/unregisterCapability'}:
            return None
        if method == 'client/registerCapability':
            for registration in params.get('registrations', []):
                if registration.get('method') != 'workspace/didChangeWatchedFiles':
                    raise NavigationError('unsupported_request', '不支持此动态能力')
                for watcher in registration.get('registerOptions', {}).get('watchers', []):
                    pattern = watcher.get('globPattern')
                    if isinstance(pattern, dict):
                        base = pattern.get('baseUri')
                        base = base.get('uri') if isinstance(base, dict) else base
                        if base != self.root.as_uri():
                            raise NavigationError('invalid_watcher', '监听目录必须为项目根')
                        pattern = pattern.get('pattern')
                    if not isinstance(pattern, str) or len(pattern) > 2048 or '\\' in pattern or ':' in pattern or pattern.startswith('/') or '..' in pattern.split('/'):
                        raise NavigationError('invalid_watcher', '监听路径必须位于项目内')
            return None
        raise NavigationError('unsupported_request', '不支持此语言服务请求')

    async def _notification(self, method, params):
        if self.closed:
            return
        if method == '$/progress':
            value = params.get('value', {})
            if isinstance(value, dict):
                await self._status('ready' if value.get('kind') == 'end' else 'indexing',
                                   '本地语言服务已就绪' if value.get('kind') == 'end' else '正在分析项目')
        elif isinstance(params, dict) and ((method == 'window/showMessage' and params.get('type') == 1)
                                          or (method == 'language/status' and params.get('type') == 'Error')):
            await self._status('import_failed', '项目分析失败，可检查离线依赖或切回源码模式：' + str(params.get('message', '未知错误'))[:400])
        elif method == 'textDocument/publishDiagnostics' and self.family == 'java' and isinstance(params, dict):
            imports = self.launch.configuration.get('java', {}).get('import', {})
            uri = params.get('uri', '')
            if (isinstance(uri, str) and uri.endswith(('/pom.xml', '/build.gradle', '/build.gradle.kts', '/settings.gradle', '/settings.gradle.kts'))
                    and any(imports.get(mode, {}).get('enabled') for mode in ('maven', 'gradle'))):
                errors = [str(item.get('message', '构建配置错误'))[:400] for item in params.get('diagnostics', [])[:100]
                          if isinstance(item, dict) and item.get('severity') == 1]
                if errors:
                    if uri in self._build_errors or len(self._build_errors) < 100:
                        self._build_errors[uri] = errors[0]
                    await self._status('ready', '本地语言服务已就绪')
                elif self._build_errors.pop(uri, None) is not None:
                    await self._status('ready', '本地语言服务已就绪')

    async def _watched(self, events):
        if self.closed or not self.ready.is_set():
            return
        self.revision += 1
        await self.rpc.notify('workspace/didChangeWatchedFiles', {'changes': events})
        if any(event['uri'].rsplit('/', 1)[-1] in CONFIGS for event in events):
            await self._status('configuration_changed', '项目配置已变化，可重新分析以更新索引')

    async def _ack(self, request_id, document=None):
        message = {'type': 'ack', 'id': request_id, 'generation': self.generation, 'revision': self.revision}
        if document:
            message.update(path=document.path, version=document.version)
        await self.emit(message)

    async def _error(self, request_id, error):
        await self.emit({'type': 'error', 'id': request_id, 'code': error.code, 'message': error.detail, 'generation': self.generation})

    def _next_version(self, path):
        self._versions[path] = self._versions.get(path, 0) + 1
        return self._versions[path]

    async def _did_open(self, document):
        text = document.text
        if self.family == 'python':
            # Pyright's setFileOpened does not invalidate existing importers.
            # Replay a restored draft as open(disk), change(draft), just as a
            # normal editing session, so already analyzed members move too.
            text = (await asyncio.to_thread(read_file, self.root, document.path))['content']
        await self.rpc.notify('textDocument/didOpen', {'textDocument': {
            'uri': file_uri(self.root, document.path), 'languageId': document.language,
            'version': self._next_version(document.path), 'text': text}})
        if text != document.text:
            await self.rpc.notify('textDocument/didChange', {'textDocument': {
                'uri': file_uri(self.root, document.path), 'version': self._next_version(document.path)},
                'contentChanges': [{'text': document.text}]})

    async def handle(self, message: dict) -> None:
        if self.closed:
            raise NavigationError('service_exit', '语言服务连接已关闭')
        if not isinstance(message, dict) or not _positive(message.get('id')) or message['id'] <= self._last_id:
            raise NavigationError('invalid_message', '请求编号必须递增')
        self._last_id = message['id']
        kind, request_id = message.get('type'), message['id']
        if kind == 'ping':
            self._last_heartbeat = self.manager.clock()
            await self.emit({'type': 'pong', 'id': request_id, 'generation': self.generation})
            return
        self._last_activity = self.manager.clock()
        if kind == 'cancel':
            task = self._queries.get(message.get('request_id'))
            if task:
                task.cancel()
            await self._ack(request_id)
            return
        if kind == 'reanalyze':
            if self._restart_task and not self._restart_task.done():
                raise NavigationError('indexing', '语言服务正在重新分析')
            self.ready.clear()
            self._restart_task = asyncio.create_task(self._restart())
            await self._ack(request_id)
            return
        if kind not in {'open', 'change', 'close', 'saved', 'definition'}:
            raise NavigationError('invalid_message', '不支持此导航消息')
        path = message.get('path')
        if not isinstance(path, str) or len(path) > 4096:
            raise NavigationError('invalid_path', '无效的项目文件路径')
        # didClose must still release a previously validated document after its
        # disk entry disappears; it never opens or reads the current entry.
        uri = ((self.root / path).as_uri() if kind == 'close' and path in self.documents
               else await asyncio.to_thread(file_uri, self.root, path))
        async with self._sync_lock:
            document = self.documents.get(path)
            if kind in {'open', 'change'}:
                text, version = message.get('text'), message.get('version')
                if not isinstance(text, str) or len(text) > MAX_FILE_BYTES:
                    raise NavigationError('document_limit', '文档超过 2 MiB，已暂停语义分析')
                try:
                    size = len(text.encode('utf-8'))
                except UnicodeError as exc:
                    raise NavigationError('invalid_text', '文本包含无效的 Unicode 字符') from exc
                if size > MAX_FILE_BYTES:
                    raise NavigationError('document_limit', '文档超过 2 MiB，已暂停语义分析')
                if not _positive(version) or document and version <= document.version:
                    raise NavigationError('stale_version', '文档版本过期')
                if kind == 'open':
                    if document or message.get('language') not in LANGUAGES[self.family]:
                        raise NavigationError('invalid_document', '文档已打开或语言组不匹配')
                    if len(self.documents) >= 12:
                        raise NavigationError('document_limit', '此语言组最多同步 12 个文件')
                    self._epochs[path] = self._epochs.get(path, 0) + 1
                    document = OpenDocument(path, message['language'], text, version, self._epochs[path])
                    self.documents[path] = document
                    if self.ready.is_set():
                        await self._did_open(document)
                else:
                    if not document:
                        raise NavigationError('invalid_document', '文档尚未打开')
                    previous = document.text
                    document.text, document.version = text, version
                    if self.ready.is_set():
                        change = {'text': text}
                        if self._sync_kind == 2:
                            change['range'] = {'start': {'line': 0, 'character': 0}, 'end': _end(previous)}
                        await self.rpc.notify('textDocument/didChange', {'textDocument': {'uri': uri, 'version': self._next_version(path)}, 'contentChanges': [change]})
                self.revision += 1
                if self.ready.is_set():
                    await self._ack(request_id, document)
                elif self._failure:
                    await self._error(request_id, self._failure)
                else:
                    previous_id = self._pending_acks.get(path)
                    if previous_id is not None:
                        await self._error(previous_id, NavigationError('superseded', '初始化期间已由较新的草稿替换'))
                    self._pending_acks[path] = request_id
                return
            if not document:
                raise NavigationError('invalid_document', '文档尚未打开')
            if kind == 'close':
                del self.documents[path]
                pending = self._pending_acks.pop(path, None)
                if pending is not None:
                    await self._error(pending, NavigationError('cancelled', '文档已关闭'))
                if self.ready.is_set():
                    await self.rpc.notify('textDocument/didClose', {'textDocument': {'uri': uri}})
                self.revision += 1
                await self._ack(request_id)
            elif kind == 'saved':
                disk = await asyncio.to_thread(read_file, self.root, path)
                if disk['revision'] != message.get('revision'):
                    raise NavigationError('stale_save', '文件保存版本与磁盘不一致')
                if self.ready.is_set():
                    params = {'textDocument': {'uri': uri}}
                    if self._save_text:
                        params['text'] = disk['content']
                    await self.rpc.notify('textDocument/didSave', params)
                    if self.watcher:
                        await self.watcher.saved(path)
                await self._ack(request_id)
            elif kind == 'definition':
                if not self.ready.is_set():
                    raise self._failure or NavigationError('indexing', '语言服务仍在初始化，请稍后重试')
                if document.version != message.get('version'):
                    raise NavigationError('stale_version', '定义查询的文档版本已过期')
                validate_position(document.text, message.get('position'))
                if len(self._queries) >= 8:
                    raise NavigationError('request_limit', '待处理定义查询已达上限')
                stamp = (document.epoch, document.version, self.revision, self.generation)
                task = asyncio.create_task(self._definition(request_id, document, message['position'], stamp))
                self._queries[request_id] = task

    async def _definition(self, request_id, document, position, stamp):
        try:
            async with asyncio.timeout(15):
                result = await self.rpc.request('textDocument/definition', {'textDocument': {'uri': file_uri(self.root, document.path)}, 'position': position})
                targets, rejected = await asyncio.to_thread(normalize_definitions, self.root, result)
            current = self.documents.get(document.path)
            if current is None or stamp != (current.epoch, current.version, self.revision, self.generation):
                raise NavigationError('stale', '草稿已变化，请重新查询定义')
            await self.emit({'type': 'definition-result', 'id': request_id, 'path': document.path,
                             'version': document.version, 'epoch': document.epoch, 'generation': self.generation,
                             'revision': self.revision, 'targets': targets, 'rejected': rejected})
        except asyncio.CancelledError:
            if not self.closed:
                await self._error(request_id, NavigationError('cancelled', '定义查询已取消'))
        except (NavigationError, TimeoutError) as exc:
            await self._error(request_id, exc if isinstance(exc, NavigationError) else NavigationError('timeout', '定义查询超时'))
        finally:
            self._queries.pop(request_id, None)

    async def _stop_service(self):
        for task in list(self._queries.values()):
            task.cancel()
        await asyncio.gather(*self._queries.values(), return_exceptions=True)
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
            await asyncio.gather(self._init_task, return_exceptions=True)
        if self.watcher:
            await self.watcher.close()
            self.watcher = None
        if self.rpc:
            await self.rpc.close()
            self.rpc = None

    async def _restart(self):
        await self._stop_service()
        if self.closed:
            return
        self.generation = self.manager.generation()
        self.revision += 1
        self._failure = None
        self._build_errors.clear()
        self._init_task = asyncio.create_task(self._initialize())

    async def check_expiry(self):
        now = self.manager.clock()
        if now - self._last_heartbeat > 60 or not self.documents and now - self._last_activity > 180:
            await self.close()
        elif self.rpc and self.rpc._failure and not self._failure:
            self.ready.clear()
            self._failure = NavigationError('service_exit', '语言服务已退出，可重新分析后重试')
            await self.rpc.close()
            self.rpc = None
            await self._status('unavailable', self._failure.detail)

    async def _expiry(self):
        while not self.closed:
            await asyncio.sleep(1)
            await self.check_expiry()

    async def close(self) -> None:
        if self._close_task is None:
            self.closed = True
            self.ready.clear()
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        if self._restart_task:
            self._restart_task.cancel()
            await asyncio.gather(self._restart_task, return_exceptions=True)
        # Do not cancel an expiry task awaiting this same close task.
        if self._expiry_task and self._expiry_task is not asyncio.current_task():
            self._expiry_task.cancel()
        await self._stop_service()
        if self.manager.instances.get(self.key) is self:
            del self.manager.instances[self.key]
        self.documents.clear()
        await self._status('closed', '语言服务连接已关闭')
