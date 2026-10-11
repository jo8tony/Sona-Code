"""Real local LSP and packaged route acceptance with isolated disposable projects."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import urllib.request
import uuid
import venv
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sona_code.language_services.bundle import LanguageBundle, VERSIONS
from sona_code.language_services.manager import LanguageServiceManager
from sona_code.language_services.settings import AnalysisSettings
from sona_code.processes import ProcessScope


def symbol_position(text: str, needle: str) -> dict:
    offset = text.rindex(needle)
    prefix = text[:offset]
    return {'line': prefix.count('\n'), 'character': len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2}


def fixture_cases() -> list[dict]:
    cases = [
        ('java', 'java', 'src/main/java/b/App.java', 'src/main/java/a/Base.java', 'run(', {
            'src/main/java/b/App.java': 'package b;\r\nimport a.Child;\r\nclass App { int value = new Child().run(); }\r\n',
            'src/main/java/a/Base.java': 'package a;\npublic class Base {\n public int run() { return 42; }\n}\n',
            'src/main/java/a/Child.java': 'package a; public class Child extends Base {}\n',
            'src/main/java/b/Base.java': 'package b; public class Base { public int run() {return 0;} }\n'}),
        ('python', 'python', 'main.py', 'pkg/service.py', 'Worker().run(', {
            'main.py': 'from pkg.service import Worker as Selected\nfrom other.service import Worker as Other\nSelected().run()\n',
            'pkg/__init__.py': '', 'other/__init__.py': '',
            'pkg/service.py': 'class Worker:\n    def run(self):\n        return 42\n',
            'other/service.py': 'class Worker:\n    def run(self):\n        return 0\n'}),
        ('javascript', 'typescript', 'main.js', 'lib.js', 'answer(', {
            'main.js': 'import {answer} from "./lib.js";\r\nconst emoji="😀"; answer();\r\n',
            'lib.js': 'export function answer() {return 42;}\n',
            'other.js': 'export function answer() {return 0;}\n',
            'jsconfig.json': '{"compilerOptions":{"allowJs":true,"checkJs":true,"target":"ES2022"}}'}),
        ('javascriptreact', 'typescript', 'main.jsx', 'components/Card.jsx', 'Card />', {
            'main.jsx': 'import {Card} from "./components/Card";\nexport const view = <Card />;\n',
            'components/Card.jsx': 'export function Card() { return <div />; }\n',
            'jsx.d.ts': 'declare namespace JSX { interface IntrinsicElements {div: {};}}\n',
            'jsconfig.json': '{"compilerOptions":{"allowJs":true,"jsx":"preserve","target":"ES2022"}}'}),
        ('typescript', 'typescript', 'main.ts', 'lib/helper.ts', 'answer(', {
            'main.ts': 'import {answer} from "@local/helper";\nimport {refValue} from "./referenced";\nconst emoji="😀"; answer(); refValue();\n',
            'lib/helper.ts': 'export function answer() {return 42;}\n',
            'other/helper.ts': 'export function answer() {return 0;}\n',
            'tsconfig.json': '{"compilerOptions":{"baseUrl":".","paths":{"@local/*":["lib/*"]},"target":"ES2022"},"references":[{"path":"./referenced"}]}',
            'referenced/tsconfig.json': '{"compilerOptions":{"composite":true,"declaration":true},"files":["index.ts"]}',
            'referenced/index.ts': 'export function refValue() {return 7;}\n'}),
        ('typescriptreact', 'typescript', 'main.tsx', 'components/Card.tsx', 'Card />', {
            'main.tsx': 'import {Card} from "./components/Card";\nexport const view = <Card />;\n',
            'components/Card.tsx': 'export function Card() { return <div />; }\n',
            'jsx.d.ts': 'declare namespace JSX { interface IntrinsicElements {div: {};}}\n',
            'tsconfig.json': '{"compilerOptions":{"jsx":"preserve","target":"ES2022"}}'}),
    ]
    result = []
    for language, family, source, target, needle, files in cases:
        # For Python query the member, rather than the constructor at the same expression.
        if language == 'python':
            needle = 'run('
        result.append(dict(language=language, family=family, source=source, target=target, needle=needle, files=files))
    return result


def process_metrics(pid: int) -> dict:
    """Windows RSS includes child tsserver processes, independently of heap caps."""
    if os.name != 'nt':
        return {'pids': [pid], 'rss_bytes': None}
    from ctypes import wintypes as w
    class Entry(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD), ('heap', ctypes.c_size_t),
                    ('module', w.DWORD), ('threads', w.DWORD), ('parent', w.DWORD), ('priority', w.LONG),
                    ('flags', w.DWORD), ('exe', w.WCHAR * 260)]
    class Memory(ctypes.Structure):
        _fields_ = [('cb', w.DWORD), ('faults', w.DWORD)] + [(name, ctypes.c_size_t) for name in
                    ('peak', 'working', 'paged_peak', 'paged', 'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    memory_info = ctypes.WinDLL('psapi').GetProcessMemoryInfo
    memory_info.argtypes = [w.HANDLE, ctypes.POINTER(Memory), w.DWORD]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    entry = Entry(); entry.size = ctypes.sizeof(entry)
    parents = {}
    try:
        valid = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            parents[entry.pid] = entry.parent
            valid = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    pids = {pid}
    while True:
        expanded = pids | {child for child, parent in parents.items() if parent in pids}
        if expanded == pids:
            break
        pids = expanded
    rss = 0
    for member in pids:
        handle = kernel.OpenProcess(0x410, False, member)
        if handle:
            try:
                memory = Memory(); memory.cb = ctypes.sizeof(memory)
                if memory_info(handle, ctypes.byref(memory), memory.cb):
                    rss += memory.working
            finally:
                kernel.CloseHandle(handle)
    return {'pids': sorted(pids), 'rss_bytes': rss}


def write_fixture(base: Path, case: dict) -> Path:
    project = base / ('中文 ' + case['language'])
    project.mkdir()
    for name, content in case['files'].items():
        path = project / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8'))
    return project


class DirectChannel:
    def __init__(self, session, messages):
        self.session, self.messages, self.sequence = session, messages, 0
        self.heartbeat = asyncio.create_task(self._heartbeat())

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(20)
            await self.send('ping')

    async def send(self, kind, **values):
        self.sequence += 1
        await self.session.handle(dict(type=kind, id=self.sequence, **values))
        return self.sequence

    async def reply(self, request_id):
        deadline = time.monotonic() + 130
        while time.monotonic() < deadline:
            for message in self.messages:
                if message.get('id') == request_id and message['type'] in {'ack', 'error', 'definition-result'}:
                    if message['type'] == 'error':
                        raise RuntimeError(json.dumps(message, ensure_ascii=False))
                    return message
            await asyncio.sleep(.05)
        raise TimeoutError('Language reply deadline')

    async def close(self):
        self.heartbeat.cancel()
        await asyncio.gather(self.heartbeat, return_exceptions=True)
        await self.session.close()


class SocketChannel:
    def __init__(self, socket):
        self.socket, self.sequence = socket, 0
        self.heartbeat = asyncio.create_task(self._heartbeat())

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(20)
            await self.send('ping')

    async def send(self, kind, **values):
        self.sequence += 1
        await self.socket.send(json.dumps(dict(type=kind, id=self.sequence, **values)))
        return self.sequence

    async def reply(self, request_id):
        deadline = time.monotonic() + 130
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(self.socket.recv(), 130))
            if message.get('id') == request_id and message['type'] in {'ack', 'error', 'definition-result'}:
                if message['type'] == 'error':
                    raise RuntimeError(json.dumps(message, ensure_ascii=False))
                return message
        raise TimeoutError('Language reply deadline')

    async def close(self):
        self.heartbeat.cancel()
        await asyncio.gather(self.heartbeat, return_exceptions=True)
        await self.socket.close()


async def query(channel, case, source, version=1, target=None):
    target = target or case['target']
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        request_id = await channel.send('definition', path=case['source'], version=version,
                                        position=symbol_position(source, case['needle']))
        response = await channel.reply(request_id)
        matches = [item for item in response['targets'] if item['path'] == target]
        if matches:
            return matches[0]
        await asyncio.sleep(.4)
    raise AssertionError(f"{case['language']}: expected unopened definition {target}, got {response}")


async def verify_case(case, project, connect, metrics):
    started = time.monotonic()
    first = await connect()
    second = None
    try:
        source = case['files'][case['source']]
        await first.reply(await first.send('open', path=case['source'], language=case['language'], version=1, text=source))
        target = await query(first, case, source)
        cold = time.monotonic() - started
        baseline = target['range']['start']['line']
        original = case['files'][case['target']]
        await first.reply(await first.send('open', path=case['target'], language=case['language'], version=1, text='\n\n' + original))
        shifted = await query(first, case, source)
        assert shifted['range']['start']['line'] == baseline + 2, (target, shifted)
        second = await connect()
        await second.reply(await second.send('open', path=case['source'], language=case['language'], version=1, text=source))
        isolated = await query(second, case, source)
        assert isolated['range']['start']['line'] == baseline
        assert (project / case['target']).read_bytes() == original.encode('utf-8')
        reference = None
        if case['language'] == 'typescript':
            reference = await query(first, {**case, 'needle': 'refValue('}, source, target='referenced/index.ts')
        return dict(language=case['language'], passed=True, unopened=True, draft_isolation=True,
                    target=target, shifted_target=shifted, reference_target=reference,
                    cold_seconds=round(cold, 3), **metrics())
    finally:
        if second:
            await second.close()
        await first.close()


async def direct_cases(bundle, base, report):
    settings = AnalysisSettings()
    interpreter = base / 'local-venv'
    venv.EnvBuilder(with_pip=False).create(interpreter)
    settings.python.interpreter = str(interpreter / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
    from types import SimpleNamespace
    manager = LanguageServiceManager(base / 'analysis-cache', bundle, SimpleNamespace(get=lambda _: settings))
    try:
        for index, case in enumerate(fixture_cases()):
            project = write_fixture(base, case)
            sessions = []
            async def connect():
                messages = []
                async def emit(message):
                    messages.append(message)
                session = await manager.connect(str(index), project, str(uuid.uuid4()), case['family'], emit)
                sessions.append(session)
                return DirectChannel(session, messages)
            result = await verify_case(case, project, connect, lambda: process_metrics(sessions[0].rpc.process.pid))
            report['cases'].append(result)
            print(case['language'], 'PASS unopened definition and isolated drafts', flush=True)
    finally:
        await manager.shutdown()
    assert not manager.instances


async def build_import_cases(bundle, base, gradle_home, report, *, maven_repository=None):
    """Optional preprepared build tools exercise actual offline import, not wrappers."""
    from types import SimpleNamespace
    from sona_code.language_services.adapters import build_launch
    report['build_imports'] = []
    for mode in ('maven', 'gradle'):
        root = base / (mode + '-multi-module'); root.mkdir()
        files = {
            'a/src/main/java/pkg/Helper.java': 'package pkg; public class Helper { public static int answer() {return 42;} }\n',
            'b/src/main/java/app/Main.java': 'package app; import pkg.Helper; class Main {int value=Helper.answer();}\n'}
        if mode == 'maven':
            parent = '<modelVersion>4.0.0</modelVersion><groupId>sona.fixture</groupId><artifactId>parent</artifactId><version>1</version>'
            plugins = '<build><pluginManagement><plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-resources-plugin</artifactId><version>3.4.0</version></plugin><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.14.1</version></plugin></plugins></pluginManagement></build>'
            files['pom.xml'] = '<project>' + parent + '<packaging>pom</packaging><modules><module>a</module><module>b</module></modules><properties><maven.compiler.source>21</maven.compiler.source><maven.compiler.target>21</maven.compiler.target></properties>' + plugins + '</project>'
            for name in ('a', 'b'):
                dependencies = '<dependencies><dependency><groupId>sona.fixture</groupId><artifactId>a</artifactId><version>1</version></dependency></dependencies>' if name == 'b' else ''
                files[name + '/pom.xml'] = '<project><modelVersion>4.0.0</modelVersion><parent><groupId>sona.fixture</groupId><artifactId>parent</artifactId><version>1</version></parent><artifactId>' + name + '</artifactId>' + dependencies + '</project>'
        else:
            files.update({'settings.gradle': 'rootProject.name="sona-fixture"\ninclude "a", "b"\n',
                          'build.gradle': 'subprojects { apply plugin: "java" }\nproject(":b") { dependencies { implementation project(":a") } }\n'})
        for name, content in files.items():
            path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding='utf-8')
        settings = AnalysisSettings(); settings.java.mode = mode
        if mode == 'gradle':
            settings.java.gradle_home = str(gradle_home)
        home = base / ('isolated-home-' + mode); home.mkdir()
        if mode == 'maven':
            repository = maven_repository or gradle_home.parent / 'maven-repository'
            if not repository.is_dir():
                raise RuntimeError('Prepare the fixture Maven repository beside the optional Gradle directory first')
            shutil.copytree(repository, home / '.m2/repository')
        def isolated_launch(*args):
            launch = build_launch(*args)
            launch.argv.insert(1, '-Duser.home=' + str(home))
            launch.env.update(USERPROFILE=str(home), HOME=str(home), GRADLE_USER_HOME=str(home / '.gradle'))
            return launch
        manager = LanguageServiceManager(base / ('cache-' + mode), bundle, SimpleNamespace(get=lambda _: settings), launch=isolated_launch)
        messages = []
        async def emit(message):
            messages.append(message)
        try:
            session = await manager.connect(mode, root, 'build-import', 'java', emit)
            channel = DirectChannel(session, messages)
            source = 'b/src/main/java/app/Main.java'
            await channel.reply(await channel.send('open', path=source, language='java', version=1, text=files[source]))
            case = dict(language='java-' + mode, source=source, target='a/src/main/java/pkg/Helper.java', needle='answer(')
            report['build_imports'].append(dict(mode=mode, passed=False))
            projects = await session.rpc.request('workspace/executeCommand', {'command': 'java.project.getAll', 'arguments': []}, timeout=120)
            report['build_imports'][-1]['projects'] = projects
            target = await query(channel, case, files[source])
            projects = await session.rpc.request('workspace/executeCommand', {'command': 'java.project.getAll', 'arguments': []}, timeout=120)
            assert any('/a' in uri for uri in projects) and any('/b' in uri for uri in projects), projects
            assert not any(message.get('state') == 'import_failed' for message in messages), messages
            report['build_imports'][-1].update(passed=True, target=target, projects=projects,
                                               wrapper_download=False, offline=True)
            print('java', mode, 'PASS real offline multi-module import', flush=True)
        finally:
            if report['build_imports'] and report['build_imports'][-1]['mode'] == mode:
                report['build_imports'][-1]['status_messages'] = [message for message in messages if message['type'] == 'status'][-20:]
            logs = list((base / ('cache-' + mode)).rglob('.log'))
            if logs and report['build_imports']:
                report['build_imports'][-1]['service_log_tail'] = logs[0].read_text(encoding='utf-8', errors='replace')[-6000:]
            if 'channel' in locals():
                await channel.close()
            await manager.shutdown()


def request(base_url, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json', 'Origin': base_url})
    # Explicitly bypass system proxy for our loopback-only acceptance server.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as response:
        return json.load(response)


async def packaged_cases(bundle, resources, base, report):
    import websockets
    with socket.socket() as port_socket:
        port_socket.bind(('127.0.0.1', 0)); port = port_socket.getsockname()[1]
    base_url = f'http://127.0.0.1:{port}'
    env = os.environ.copy()
    for key in ('CONFIG', 'DATA', 'CACHE', 'STATE'):
        env['XDG_' + key + '_HOME'] = str(base / key.lower())
    env.update(SONACODE_BUNDLED_LANGUAGE_SERVICES=str(bundle.root),
               SONACODE_BUNDLED_OPENCODE=str(resources / 'opencode.exe'),
               SONACODE_BUNDLED_OPENSPEC=str(resources / 'tools/openspec'),
               OPENCODE_DB=str(base / 'isolated-opencode.db'))
    scope = ProcessScope()
    with (base / 'sidecar.log').open('wb') as output:
        process = scope.spawn([str(resources / 'sona-code-sidecar.exe'), '--host', '127.0.0.1', '--port', str(port),
                               '--config', str(base / 'config.json'), '--records-dir', str(base / 'records')],
                              env=env, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 60
            while True:
                try:
                    await asyncio.to_thread(request, base_url, 'GET', '/__recorder/api/ping')
                    break
                except Exception:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Isolated packaged sidecar failed to start')
                    await asyncio.sleep(.2)
            for case in fixture_cases():
                project = write_fixture(base, case)
                registered = await asyncio.to_thread(request, base_url, 'POST', '/__recorder/api/workspace/projects', {'path': str(project)})
                prefix = '/__recorder/api/workspace/projects/' + registered['id']
                async def connect():
                    url = base_url.replace('http:', 'ws:') + prefix + '/code-navigation/' + case['family'] + '?editor_id=' + str(uuid.uuid4())
                    return SocketChannel(await websockets.connect(url, origin=base_url, max_size=4 * 1024 * 1024))
                result = await verify_case(case, project, connect, lambda: process_metrics(process.pid))
                # Saving must still work with all analysis sockets disconnected.
                payload = await asyncio.to_thread(request, base_url, 'GET', prefix + '/file?path=' + quote(case['source']))
                saved = await asyncio.to_thread(request, base_url, 'PUT', prefix + '/file?path=' + quote(case['source']),
                                               {'content': payload['content'] + '\n', 'revision': payload['revision']})
                assert saved['revision'] != payload['revision']
                result['save_without_analysis'] = True
                report['cases'].append(result)
                await asyncio.to_thread(request, base_url, 'DELETE', prefix)
                print(case['language'], 'PASS packaged offline WS navigation and independent save', flush=True)
        finally:
            report['owned_processes_before_shutdown'] = process_metrics(process.pid)['pids']
            scope.close()
            process.wait(timeout=10)
    log = (base / 'sidecar.log').read_text(encoding='utf-8', errors='replace')
    assert 'SONA_SMOKE_SECRET' not in log
    report['owned_scope_closed'] = scope.closed


def run_smoke(bundle_path, report_path, *, package_resources=None, offline=False, gradle_home=None):
    report_path = Path(report_path).resolve(); report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'versions': VERSIONS, 'offline': offline, 'cases': [],
              'mode': 'packaged-routes' if package_resources else 'real-manager',
              'desktop_lifecycle_tested': False}
    previous = os.environ.copy()
    if offline:
        os.environ.update(HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9',
                          ALL_PROXY='http://127.0.0.1:9', NO_PROXY='127.0.0.1,localhost',
                          npm_config_offline='true', PIP_NO_INDEX='1')
    os.environ['OPENAI_API_KEY'] = 'SONA_SMOKE_SECRET'
    try:
        bundle = LanguageBundle(Path(bundle_path).resolve()); manifest = bundle.check()
        report['bundle_files'] = len(manifest['files'])
        with tempfile.TemporaryDirectory(prefix='navigation-smoke-', dir=report_path.parent, ignore_cleanup_errors=True) as directory:
            base = Path(directory)
            if package_resources:
                asyncio.run(packaged_cases(bundle, Path(package_resources).resolve(), base, report))
            else:
                asyncio.run(direct_cases(bundle, base, report))
            if gradle_home:
                asyncio.run(build_import_cases(bundle, base, Path(gradle_home).resolve(), report))
        report['passed'] = True
        return report
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.environ.clear(); os.environ.update(previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--package-resources', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--gradle-home', type=Path, help='Optional preprepared local Gradle for offline Maven/Gradle acceptance')
    options = parser.parse_args()
    run_smoke(options.bundle, options.report, package_resources=options.package_resources, offline=options.offline, gradle_home=options.gradle_home)


if __name__ == '__main__':
    main()
