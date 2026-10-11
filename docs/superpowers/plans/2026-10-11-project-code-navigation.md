# Project Code Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 为现有 Monaco 编辑器增加 Java、Python、JS、JSX、TS、TSX 的项目内定义跳转、Ctrl＋左右键位置历史，并交付可离线安装及重打包的 Windows x64 产物。

**Architecture:** 浏览器通过应用专用 WebSocket 将草稿和定义查询交给 Python 管理器，管理器启动固定版本的本地 stdio LSP。按编辑器实例、项目和语言组隔离缓冲区；磁盘读写继续使用现有文件 API。位置历史与 LSP 分离，正常编辑和保存不等待分析。

**Tech Stack:** Python/FastAPI、现有 ProcessScope/Windows Job、原生 JavaScript、Monaco 0.57.0、JDT LS、Pyright、TypeScript Language Server、Node.js、Temurin JDK、Electron/NSIS。

**Spec:** `docs/superpowers/specs/2026-10-11-project-code-navigation-design.md`（用户于 2026-10-11 确认设计与本计划，选择当前会话执行）。

## Global Constraints

- “支持 Java、Python、JS、JSX、TS、TSX，包括尚未打开的目标文件。F12 使用同一套定义查询。”
- “预览和编辑模式均可使用跳转。”Ctrl＋左/右仅在代码文本焦点且无 IME 组合输入时生效，覆盖按单词移动。
- “目标已有 model 时复用草稿，绝不为了跳转重新读取磁盘覆盖草稿。”最多 12 个标签，满额不自动丢弃文件。
- “每个编辑器实例保存最多 100 条位置”。成功新导航清除前进分支，历史导航本身不追加条目。
- “单文档同步按 UTF-8 编码后不超过 2 MiB，WebSocket 单消息上限 4 MiB，LSP frame 上限 16 MiB”。
- 草稿合并 150 ms；定义前刷新同组全部变化；定义超时 15 秒，Node 初始化 45 秒，Java初始化 120 秒。
- “每连接最多 8 个待处理定义请求”，“应用级同时最多 6 个服务实例”。心跳间隔 20 秒，丢失 60 秒回收，不活动语言组 3 分钟关闭。
- “Java 堆上限 1 GiB，Node 各服务启动设置堆上限 512 MiB”；实际 RSS 另测，不当成总内存保证。
- JDT LS 1.61.0（202609031315 固定里程碑）、Temurin 21.0.12.1+1、Pyright 1.1.414、typescript-language-server 6.0.2、TypeScript 6.0.3、Node 22.23.3；不浮动到 latest。
- “默认关闭 Maven/Gradle 导入及注解处理，使用源码模式”。构建导入为显式项目设置且离线，禁止 wrapper/附件自动下载和自动类型包获取。
- “第一版覆盖项目内普通 UTF-8 文本文件的定义导航和位置历史”。项目外/虚拟源、重解析点不扩展为可写目标。
- “客户端只声明已实现的能力。拒绝 workspace/applyEdit 和未允许的 executeCommand”。
- 外部文件轮询 2 秒、最多 20,000 个匹配文件，排除依赖及编译缓存；磁盘变化不覆盖草稿。
- “内网重打包仅用离线目录且缺件报错，不自动回退外网下载”。运行时/服务/许可证/对应第三方源码随离线交付。
- 不更换 OpenCode V1 及其 1.18.32 基线，不修改真实 OpenCode 配置或对话数据库；新验证只用临时项目和独立应用配置。
- 遵守 `AGENTS.md`：代码按职责拆分，生成资源留在 build/dist/.runtime，全套常规测试后才能推送；用户已授权本地提交和 GitHub 推送。

## Review Focus

1. 关闭后重新打开同路径的 model 版本会重置；旧响应不能绑定新 model。Task 3/6 测文档 epoch、单调服务版本和迟到响应。
2. 同项目两个窗口及一个项目同名符号；未保存内容必须隔离且按 import 正确解析。Task 3/8 分别验证隔离和真实同名符号。
3. 查询源未变化但目标草稿发生变化；缓存必须按语言组 revision 失效。Task 6 在目标更新后查询，禁止返回旧位置。
4. 自定义 admin_prefix、恶意/缺失 WebSocket Origin 和项目删除；旧连接不能绕过项目边界。Task 4 测这些请求与撤销连接。
5. Windows CRLF 检出/干净源码归档、文件被安全软件移除；构建必须发现源输入/运行时校验错误。Task 2/7/8 测缺件、篡改及干净离线重建。

## 文件职责与统一接口

新增后端 `sona_code/language_services/`：

| 文件 | 职责 |
| --- | --- |
| `errors.py` | `NavigationError(code: str, detail: str)`，跨协议的有界错误类型 |
| `protocol.py` | Content-Length 帧、请求关联、通知、取消、服务端请求与退出 |
| `paths.py` | 相对路径/file URI 转换、UTF-16 范围验证、定义结果归一化 |
| `bundle.py` | 解析固定组件、清单与完整性；不在运行时下载 |
| `settings.py` | 类型化项目分析设置、独立 JSON 持久化和路径校验 |
| `adapters.py` | 三组服务启动参数、初始化选项和配置请求回答 |
| `watcher.py` | 有界项目源码/配置轮询，保存通知去重 |
| `manager.py` | 实例生命周期、文档同步、定义任务、资源限额及关闭 |
| `routes.py` | 状态、分析设置与专用 WebSocket 路由 |

新增前端：`workspace-code-history.js` 只管理位置历史；`workspace-language-client.js` 管理 WebSocket 和缓冲区；`workspace-code-navigation.js` 管理 Monaco 定义链接、Ctrl 点击/F12/左右键及候选菜单；`workspace-analysis-settings.js` 管理项目分析设置表单。

新增构建：`packaging/language-services.json`、`packaging/language-services-package/package.json` 与 lockfile、`scripts/prepare-language-services.py`、`scripts/smoke-code-navigation.py`。生成资源固定在 `build/language-services`，不提交 JDK、node_modules 或 JAR 到源码仓库。

### 后端类型契约

```python
# errors.py
class NavigationError(Exception):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(detail)

# settings.py，Pydantic BaseModel，所有可选路径默认 None，列表默认空列表
# JavaSettings: mode Literal['source','maven','gradle'] = 'source',
# source_roots list[str], libraries list[str], project_jdk str | None,
# gradle_home str | None
# PythonSettings: interpreter str | None
# AnalysisSettings: java JavaSettings, python PythonSettings

# adapters.py
# ServerLaunch(argv: list[str], env: dict[str,str], initialize: dict,
#              configuration: dict, timeout: float)
# LanguageBundle(root: Path, manifest: dict) 在 bundle.py 定义
```

协议用 JSON 对象。`Position` 为 `{line:int, character:int}`（零基 UTF-16），`Range` 为 `{start:Position,end:Position}`，`Target` 为 `{path:str,range:Range,origin:Range|None}`。`Document` 为 `{path:str,language:str,version:int,text:str}`。WS 的 `generation` 为服务实例代数，不等同于文档版本。

### 前端类型契约

```javascript
// PositionSnapshot: {projectId, path, selection, viewState}
// DocumentSnapshot: {projectId, path, language, version, epoch, text}
// Target: {path, range, origin}，range 使用后端零基 UTF-16。
// openTarget(projectId, path, selection, {recordHistory=true})
//   => Promise<boolean>：成功才提交历史；失败保留当前 model 和历史索引。
```

## 执行前检查

- [x] 读取设计、`AGENTS.md` 与使用的执行技能；按 `using-git-worktrees` 检查现有附属工作树/当前 feature 分支，保留已有 Monaco 提交，不从默认分支丢失它们。
- [x] 记录 `git status --short`、`git rev-parse HEAD` 和既有 Test4 的路径/hash，避免把旧包当成含新功能的产物。
- [x] 使用 `.venv/Scripts/python.exe` 运行测试；已有浏览器交互测试使用本机 Node。打包沿用当前 Electron 路径，真实安装版若运行中只读检查，不自动停止或覆盖安装。
- [x] 实现时在本计划勾选进度；每个任务先 red 测试再实现，验证通过后单独提交。文档/锁文件更新归入其相关任务。

## Task 1：stdio 协议、位置与文件边界

**Files:** Create `sona_code/language_services/__init__.py`, `errors.py`, `protocol.py`, `paths.py`; Modify `sona_code/workspace/files.py`; Create `tests/test_language_protocol.py`, `tests/test_language_paths.py`, `tests/helpers/navigation-lsp.py`。

**Interfaces:**

- `encode_frame(message: dict) -> bytes`、`read_frame(stream: BinaryIO) -> dict | None`，EOF 返回 None，格式错误抛 NavigationError。
- `JsonRpcPeer(process: subprocess.Popen, scope: ProcessScope, on_request, on_notification)`；`async request(method: str, params: dict, timeout: float = 15) -> object`；`async notify(method: str, params: dict) -> None`；`async close() -> None`。取消 request 所属 asyncio Task 自动发 `$/cancelRequest`。
- `project_file_path(root: Path, relative: str) -> Path` 在现有 files.py 暴露既有 `_entry` 校验。
- `file_uri(root: Path, relative: str) -> str`；`normalize_definitions(root: Path, result: object) -> tuple[list[dict], list[str]]`；`validate_position(text: str, position: dict) -> None`。后一个返回 reason code 列表以区分没有定义和不可打开目标。

- [x] **Step 1：写失败测试。** 测 Unicode Content-Length、分片/合并读取、重复/缺失/负数/超长头、非对象 JSON、EOF；测文件 URI round trip、Location/LocationLink、同目标去重、错误范围、越界与 UTF-16。

```python
import io
import pytest
from sona_code.language_services.protocol import encode_frame, read_frame
from sona_code.language_services.paths import file_uri, normalize_definitions
from sona_code.language_services.errors import NavigationError

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

def test_outside_and_virtual_definitions_do_not_open(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    outside = tmp_path / 'secret.py'; outside.write_text('secret', encoding='utf-8')
    span = {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 1}}
    targets, rejected = normalize_definitions(root, [
        {'uri': outside.as_uri(), 'range': span},
        {'uri': 'jdt://contents/a', 'range': span}])
    assert targets == []
    assert len(rejected) == 2
```

伪 LSP helper 支持 `initialize`、echo、可延迟 definition、服务端 configuration 请求及取消通知，通过命令行 `--mode split|reorder|exit|hang` 选择故障。测试以 ProcessScope 启动 `sys.executable tests/helpers/navigation-lsp.py`，不借助真实 LSP 验证协议。

- [x] **Step 2：确认 red。** Run `.venv/Scripts/python.exe -m pytest tests/test_language_protocol.py tests/test_language_paths.py -q`；初次应为缺少新模块，随后具体断言失败须有对应根因。
- [x] **Step 3：实现协议与边界。** 使用一个持续读任务和一个写锁。阻塞管道 IO 放在线程中，Future 回调通过事件循环投递；解析错误/子进程退出使所有 pending request 明确失败，不挂住 Future。头最多 8 KiB，frame 最多 16 MiB。request ID 与浏览器 ID 分离。服务端请求未实现时返回 -32601。

```python
import json

def encode_frame(message: dict) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > 16 * 1024 * 1024:
        raise NavigationError('frame_limit', '语言服务消息超过限制')
    return f'Content-Length: {len(payload)}\r\n\r\n'.encode('ascii') + payload

# 在 files.py 添加，现有 read_file/save_file 的调用逻辑保持。
def project_file_path(root: Path, relative: str) -> Path:
    return _entry(root, relative)
```

URI 用 pathlib/urllib 单次解码处理 file scheme；回到项目相对路径后必须调用 project_file_path，不能仅用字符串 startswith 判断。LocationLink 优先 targetSelectionRange。UTF-16 验证按 `len(line.encode('utf-16-le')) // 2`，拒绝越界/拆开 surrogate 的列。目标超出文本的旧位置由导航夹取，协议非法负数范围直接拒绝。

- [x] **Step 4：green 与回归。** 运行 Task 1 两文件及 `tests/test_workspace_files.py`；增加 Windows junction、ADS、百分号和大小写路径测试。额外验证 request 取消、乱序响应、shutdown hang 后清理自己所属进程。
- [x] **Step 5：commit。** `git add sona_code/language_services sona_code/workspace/files.py tests/test_language_protocol.py tests/test_language_paths.py tests/helpers/navigation-lsp.py`；`git commit -m "feat(navigation): add bounded stdio RPC and project URI validation"`。

## Task 2：固定服务组件、配置适配及源码/许可准备

**Files:** Create `bundle.py`, `adapters.py`, `packaging/language-services.json`, `packaging/language-services-package/package.json`, lockfile, `scripts/prepare-language-services.py`, `tests/test_language_bundle.py`, `tests/test_language_adapters.py`; Modify `.gitattributes`, `THIRD_PARTY_NOTICES.md`。

**Interfaces:**

- `LanguageBundle(root: Path | None = None)` 使用 `SONACODE_BUNDLED_LANGUAGE_SERVICES` 或开发目录 `build/language-services`；`check() -> dict`、`launch_paths(family: str) -> dict[str, Path]`。
- 运行清单 `{schema:1,platform:'windows-x64',components:{name:{version,entry}},inputs:{relative:sha256},files:{relative:sha256},sources:[{component,version,path,sha256}]}`；Node、JDK、JDT LS、Pyright、TS 服务和 TypeScript 均有入口及文件清单。
- `build_launch(bundle: LanguageBundle, family: str, project: Path, settings: AnalysisSettings, cache: Path) -> ServerLaunch`；Task 4 的设置模型完成前只在测试提供相同字段的对象，不安装半成品配置服务。
- 准备脚本 `--check` 只校验；`--prepare` 可按固定清单联网组装；缺件、过期及篡改均非零退出。运行时不调用准备脚本。

- [x] **Step 1：red 组件测试。** 用 tmp_path 构造具有真实 hash 的小清单，入口缺失、文件被改、清单漏入口、路径包含 `../`/ADS/junction、版本与源 manifest 不一致须失败。

```python
import json, hashlib
import pytest
from sona_code.language_services.bundle import LanguageBundle
from sona_code.language_services.errors import NavigationError

def test_bundle_rejects_removed_runtime(tmp_path):
    runtime = tmp_path / 'runtime/node.exe'; runtime.parent.mkdir()
    runtime.write_bytes(b'fixture runtime')
    digest = hashlib.sha256(runtime.read_bytes()).hexdigest()
    manifest = {'schema': 1, 'platform': 'windows-x64',
        'components': {'node': {'version': '22.23.3', 'entry': 'runtime/node.exe'}},
        'files': {'runtime/node.exe': digest}, 'inputs': {}, 'sources': []}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    runtime.unlink()
    with pytest.raises(NavigationError):
        LanguageBundle(tmp_path).check()
```

缺少其他必需组件可使此最小清单失败；另写完整小清单测试正常包可用，确保失败不是碰巧由其他缺件触发。Run `.venv/Scripts/python.exe -m pytest tests/test_language_bundle.py tests/test_language_adapters.py -q`，确认具体缺失/篡改错误。

- [x] **Step 2：生成精确输入。** Node 服务 package.json 如下，执行 `npm install --package-lock-only --ignore-scripts --no-audit --no-fund` 生成 lockfile，再在 build 子目录 `npm ci --ignore-scripts`。npm cache 定位 .runtime，不更新根项目 lockfile。

```json
{"name":"sona-language-services","private":true,"version":"1.0.0","dependencies":{"pyright":"1.1.414","typescript-language-server":"6.0.2","typescript":"6.0.3"}}
```

Node Windows x64 固定官方 ZIP 的 SHA 为 `2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71`，可复用现有已校验 OpenSpec 22.23.3 运行时但新清单独立记录。Temurin ZIP SHA 为 `f9d6e191ab098c0d416e7d588a24420a8621cd2f4720dab2459b8b7b2d2d8b4e`。读取固定 JDT LS 里程碑的官方 .sha256 并将其 64 位摘要写入提交的 manifest；准备脚本以后只信任该已提交值，不抓 latest。

- [x] **Step 3：实现安全组装和适配。** 资源落在 `runtime/node.exe`、`runtime/jdk/`、`servers/jdtls/`、`servers/node/node_modules/`。解压拒绝绝对路径、越界、链接、同路径重复条目；先校验档案再解压到全新 staging，完整成功才更换 build 目录。递归更换前验证绝对目标在 workspace 的 build 子目录。

```python
# adapters.py 中 argv 按以下数组构建，launch_paths 返回这些固定键。
def node_argv(paths: dict, family: str) -> list[str]:
    entry = paths['pyright'] if family == 'python' else paths['typescript_server']
    return [str(paths['node']), '--max-old-space-size=512', str(entry), '--stdio']

def java_argv(paths: dict, cache: Path) -> list[str]:
    return [str(paths['java']), '-Xmx1G',
        '-Declipse.application=org.eclipse.jdt.ls.core.id1',
        '-Dosgi.bundles.defaultStartLevel=4', '-Declipse.product=org.eclipse.jdt.ls.core.product',
        '--add-modules=ALL-SYSTEM', '--add-opens=java.base/java.util=ALL-UNNAMED',
        '--add-opens=java.base/java.lang=ALL-UNNAMED', '-jar', str(paths['equinox']),
        '-configuration', str(cache / 'configuration'), '-data', str(cache / 'workspace')]
```

启动前复制只读 config_win 到实例配置。显式删除 CLIENT_PORT/HOST、Java/Node 外部注入选项，env 只允许 OS 系统变量、语言工具环境及用户显式分析设置。initialize 声明 UTF-16、definition/linkSupport、configuration/workspaceFolders、进度及 watcher 支持，rootUri/workspaceFolders 指向注册项目。

Java 发送 autobuild=false、maven/gradle.enabled 按 mode、offline=true、wrapper.enabled=false、annotationProcessing=false、自动源码下载关闭；source_roots/local libraries 形成 unmanaged 项目分析设置。Python 传解释器路径且保留已有 pyright 配置优先级；TS initializationOptions 使用固定 tsserver 文件、禁用自动类型获取及本地 TS 插件加载。用所选版本真实能力核对这些字段，不借用其他版本的同名假设。

- [x] **Step 4：完成对应源码和许可证目录。** 从固定 release 取 Temurin 对应源码和 Temurin 构建配方，从 JDT LS tag 及其 p2/bundle 元数据取得对应 Eclipse 源码包；核对被再分发的每个受源码义务约束的 bundle 名称/版本与源码映射。build 下保存 LICENSE/NOTICE 原件和 `sources/manifest.json`、源码档案，更新第三方声明。`--check` 同时检查 source archive hash 与组件覆盖。`.gitattributes` 为新 package/lock/manifest/准备脚本固定 LF，避免清单输入经 Windows 检出改变。
- [x] **Step 5：green 和首次真实启动。** `.venv/Scripts/python.exe scripts/prepare-language-services.py --prepare` 后执行 `--check`；跑单元测试，再分别启动三服务并 initialize/shutdown，记录 capabilities.definitionProvider。Java source mode 验证两个不同包源码的定义，提前暴露 Maven 项目识别/源码根问题。无法真实解析必须在此修复适配，不推迟到前端完成后用文本搜索替代。
- [x] **Step 6：commit。** `git add sona_code/language_services/bundle.py sona_code/language_services/adapters.py packaging/language-services.json packaging/language-services-package scripts/prepare-language-services.py tests/test_language_bundle.py tests/test_language_adapters.py .gitattributes THIRD_PARTY_NOTICES.md`；`git commit -m "feat(navigation): prepare pinned offline language services"`。

## Task 3：隔离会话、草稿、查询和文件变化

**Files:** Create `manager.py`, `watcher.py`, `tests/test_language_sessions.py`, `tests/test_language_watcher.py`; extend fake LSP helper。

**Interfaces:**

- `LanguageServiceManager(cache_root: Path, bundle: LanguageBundle, settings_store)`；`async connect(project_id: str, root: Path, editor_id: str, family: str, emit) -> NavigationSession`；`async close_project(project_id: str) -> None`；`async shutdown() -> None`。
- `NavigationSession`：`async handle(message: dict) -> None`、`async close() -> None`；definition 启动异步任务，handle 不等待其结果，从而继续接收 change/cancel/ping。
- emit 为 `async emit(message: dict) -> None`，manager 可注入 launch/clock 供测试，生产默认 Task 2 的 build_launch、单调时钟与 ProcessScope。
- `ProjectWatcher(root: Path, family: str, emit_changes)`：`async start() -> None`、`async saved(path: str) -> None`、`async close() -> None`；emit_changes 接收 LSP FileEvent 数组。

- [x] **Step 1：red。** 写隔离、单调版本、2 MiB、8 pending/6实例、idle/hb、临时故障、重连generation、关闭后重开路径与 watcher 外部更新测试。

```python
# fake_service 为本文件定义的 fixture：创建完整 fixture bundle，
# 用 Task 1 helper 替换启动 argv，返回 manager、临时项目和收集消息的 emit。
async def test_unsaved_buffers_are_isolated(fake_service):
    manager, project, messages, emit = fake_service
    first = await manager.connect('p', project, 'window-one', 'python', emit)
    second = await manager.connect('p', project, 'window-two', 'python', emit)
    try:
        for session, text in [(first, 'value = 1'), (second, 'value = 2')]:
            await session.handle({'type': 'open', 'id': 1, 'path': 'a.py',
                'language': 'python', 'version': 1, 'text': text})
        assert first.documents['a.py'].text == 'value = 1'
        assert second.documents['a.py'].text == 'value = 2'
        assert (project / 'a.py').read_text(encoding='utf-8') == 'original'
    finally:
        await manager.shutdown()
```

fixture 在 project 下写 a.py='original'，manager 的实例字典和 session.documents 仅作测试检查，不作为路由公共 API。Run `.venv/Scripts/python.exe -m pytest tests/test_language_sessions.py tests/test_language_watcher.py -q`。

- [x] **Step 2：实现 ordered 同步。** 文档记录 path/language/text/version/epoch，拒绝重复或倒退版本；close 后 reopen 要建立新 epoch，并维持当前服务的单调文档版本。open/change 通知与 definition 写入同一 peer 的写队列。初始化前消息只保留有界文档快照，完成后 didOpen，查询返回 analyzing 状态而非永远等待。

```python
# manager.py 的文档记录。
from dataclasses import dataclass

@dataclass
class OpenDocument:
    path: str
    language: str
    text: str
    version: int
    epoch: int

# 一次 accepted change：先 validate，再更新 snapshot，再 await notify。
# didChange 的合法 full replacement：
change_params = {'textDocument': {'uri': uri, 'version': version},
                 'contentChanges': [{'text': text}]}
```

同步通知全文替换必须符合服务 TextDocumentSync 能力。definition 捕获文档 epoch/version 和组 revision，await peer.request 返回后再次核对再 normalize_definitions；cancel 对应 asyncio Task.cancel。saved 只在文件仍注册且磁盘 revision 对应时发 didSave/watcher，不接受浏览器提供任意 file URI。

- [x] **Step 3：实现生命周期和客户端请求。** key 为 project/editor/family，每次进程重建增加generation，缓存 key 带组件版本并有锁。初始化/查询 timeout 用 asyncio.wait_for。stdout 读任务、stderr 128 KiB 环形尾、watcher 与heartbeat任务均在 close 中回收。只有 manager 自己的 Scope 可以 close；shutdown/exit 超过 3 秒执行 Scope.close。客户端 configuration 回答按 section 返回白名单；registerCapability 只接受项目根内 watcher glob，applyEdit 返回 applied=false，不执行 commands。
- [x] **Step 4：实现有界 watcher。** 源码扩展按三family分类，追踪 tsconfig/jsconfig/pyright/pyproject/pom/gradle 等配置；记录 (mtime_ns,size) 和路径，新增/修改/删除生成 FileEvent type 1/2/3；排除 .git、node_modules、.venv、target/build/dist。2 秒扫描分批投递，不阻塞 ASGI 主循环；超过 20,000 回报状态并停止新增跟踪。扫描不穿越 reparse/link。应用保存通知与扫描去重，项目配置变化使状态提示重新分析。
- [x] **Step 5：green 与真实进程清理。** 单元测试及 `tests/test_workspace_processes.py tests/test_managed_lifecycle.py`；在 Windows 让伪 LSP 启动一个子进程并中途退出父进程，session.close 后子进程不存在；独立启动的对照进程由测试 finally 自行清理，断言未被 manager 终止。
- [x] **Step 6：commit。** `git add sona_code/language_services/manager.py sona_code/language_services/watcher.py tests/test_language_sessions.py tests/test_language_watcher.py tests/helpers/navigation-lsp.py`；`git commit -m "feat(navigation): isolate project language sessions and draft sync"`。

## Task 4：项目设置、WebSocket 与应用生命周期

**Files:** Create `settings.py`, `routes.py`, `tests/test_language_routes.py`, `tests/test_language_settings.py`; Modify `sona_code/app.py`, `sona_code/workspace/routes.py`。

**Interfaces:**

- `AnalysisSettingsStore(data_root: Path)`：`get(project_id: str) -> AnalysisSettings`；`save(project_id: str, root: Path, settings: AnalysisSettings) -> None`，自身 JSON atomic replace，不写项目目录。
- 路由固定为设计中的 languages、analysis-settings、code-navigation/{family}。query editor_id 是随机 UUID，连接属于当前 WS，不能用另一个人的 ID 接管现有session。
- RuntimeState 新增 `language_services`，置 closing 并纳入 `_close`；删除项目时调用 close_project。

- [x] **Step 1：red 路由测试。** 测 status 不启动进程、settings round trip、secret环境隔离、malformed JSON/消息种类、缺/错 Origin、自定义 prefix、未注册/删除项目、非法family、WS大小限制及关闭。

```python
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sona_code.app import create_app
from sona_code.config import AppConfig
import pytest

def test_analysis_rejects_unknown_project_without_starting_native(tmp_path):
    app = create_app(AppConfig(server={'admin_prefix': '/custom'},
        recording={'dir': str(tmp_path / 'records')}),
        config_path=str(tmp_path / 'config.json'))
    with TestClient(app) as client:
        response = client.get('/custom/api/workspace/projects/missing/analysis-settings')
        assert response.status_code == 404
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                '/custom/api/workspace/projects/missing/code-navigation/python?editor_id=one',
                headers={'origin': 'https://evil.example'}):
                pass
```

合法连接测试创建临时 project、注册 terminal_projects，注入 Task 3 fake manager，app.state.runtime.workspace.request 改为抛 AssertionError，确保没有因代码导航启动 OpenCode。Run `.venv/Scripts/python.exe -m pytest tests/test_language_routes.py tests/test_language_settings.py -q`。

- [x] **Step 2：实现 settings 模型。** 字段见统一契约；禁止额外字段。源码根/JAR 必须普通项目内路径，JDK/Gradle/Python 路径是允许的显式外部运行环境，验证入口存在、无 NUL，不拼 shell。持久化到 config_path 同级 language-services/settings.json，只写独立 app copy，临时保存失败保留原设置。

```python
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class JavaSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['source', 'maven', 'gradle'] = 'source'
    source_roots: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)
    project_jdk: str | None = None
    gradle_home: str | None = None

class PythonSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    interpreter: str | None = None

class AnalysisSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    java: JavaSettings = Field(default_factory=JavaSettings)
    python: PythonSettings = Field(default_factory=PythonSettings)
```

- [x] **Step 3：实现 routes/runtime。** 抽取现有 `_project_path` 为可供 HTTP/WS 使用的注册表解析器，不改 IDs。校验 Origin 与应用 base URL scheme/host/port 完全一致，缺失 Origin 的浏览器导航接口拒绝；PUT 同样校验。接收端限制 UTF-8 字节大小，单发锁串行输出，未知字段/负数version/id拒绝。settings 更新关闭项目旧服务，frontend 下一次 reanalyze 使用新设置。backend closing 后拒绝新connect。
- [x] **Step 4：green。** 跑 Task 4 测试与 `tests/test_workspace.py tests/test_workspace_files.py tests/test_cli.py`，测试 Context 退出始终调用 manager.shutdown，即使其他 shutdown 失败也能清理语言进程；项目删除移除设置/缓存引用但不递归误删其他项目。
- [x] **Step 5：commit。** `git add sona_code/language_services/settings.py sona_code/language_services/routes.py sona_code/app.py sona_code/workspace/routes.py tests/test_language_routes.py tests/test_language_settings.py`；`git commit -m "feat(navigation): expose local analysis API and lifecycle"`。

## Task 5：独立位置历史和 Ctrl＋左右键

**Files:** Create `workspace-code-history.js`, `tests/test_code_history.py`, `tests/code-history-state.cjs`; Modify `workspace-editor.js`, `workspace-editor.css`, `index.html`, `workspace.js`, `tests/workspace-editor-state.cjs`。

**Interfaces:**

- `createCodeHistory({capture, openTarget, onChange, onError}, limit=100)` 返回 `record(from,to)`、`move(direction)`、`clear()`、`get canBack/canForward`。capture 返回 PositionSnapshot；openTarget(snapshot) Promise<boolean>，404 已删除目标以 `{status:404}` 抛出，其他失败不移动index。
- editor 统一 `async openTarget(projectId,path,selection,{recordHistory=true}) -> boolean`；既有 `open(project,path)` 包装此函数，正常 load 完成才记历史。snapshots 不引用可被 dispose 的 model。
- editor callbacks 增加 projects() 和 locate(projectId,path)；workspace.js 的 locate 直接 tree.locate 到已注册目标，不创建/发送 AI session。

- [ ] **Step 1：red 状态测试。** Python wrapper 按已有测试的 subprocess.run(Node, .cjs, scenario) 运行。新 .cjs 用 fs/vm 读取 history source，以下案例直接验证状态机，另测 limit/deleted/busy/failed/cross-project。

```javascript
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
vm.runInThisContext(fs.readFileSync('sona_code/web/static/workspace-code-history.js', 'utf8'));
const place = (path, line) => ({projectId:'p', path,
  selection:{startLineNumber:line,startColumn:1,endLineNumber:line,endColumn:1}, viewState:{}});
let current = place('A.java',20);
const h = createCodeHistory({capture:()=>current,
  openTarget:async p=>{current=p;return true;},onChange(){},onError:message=>{throw Error(message);}});
(async()=>{
  const b=place('B.java',50); h.record(current,b); current=b;
  const c=place('C.java',10); h.record(current,c); current=c;
  await h.move(-1); assert.equal(current.path,'B.java');
  await h.move(-1); assert.equal(current.path,'A.java');
  await h.move(1); assert.equal(current.path,'B.java');
  const d=place('D.java',5); h.record(current,d); current=d;
  assert.equal(h.canForward,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
```

Run `.venv/Scripts/python.exe -m pytest tests/test_code_history.py tests/test_workspace_editor.py -q`；旧 editor 测试此时必须能定位预期变更导致的失败。

- [ ] **Step 2：实现历史事务。** record 把当前快照替换再追加成功目标；相等位置去重，截断 forward 与超limit最旧项。move 更新离开快照，串行打开候选，404 跳过，失败维持index；move pending 时不接收重复导航。clear 递增generation，阻止已关闭编辑区晚到promise恢复历史。
- [ ] **Step 3：接入 editor。** 把按钮/树/标签的 activate 入口分为内部 activate 与用户导航，避免 load/reload/render 误记历史。openTarget 先检查项目、容量和generation，load 失败不破坏原 active model；目标已开直接复用。保存回调继续只更新提交文本baseline，不动草稿。

```javascript
// 接到 editor 文本焦点的 capture keydown，不能使用“面板可见”代替焦点。
if (editor.hasTextFocus() && event.ctrlKey && !event.altKey && !event.shiftKey &&
    !event.isComposing && ['ArrowLeft','ArrowRight'].includes(event.key)) {
  event.preventDefault(); event.stopPropagation();
  void history.move(event.key === 'ArrowLeft' ? -1 : 1);
}
```

插入前进/后退按钮，渲染 canBack/canForward，恢复 viewState 后应用夹取到当前model范围的selection。通过 tree.locate 更新对应项目文件，保留对话选择/草稿，不调用 selectProject 的模型请求路径来实现文件历史。

- [ ] **Step 4：green 与交互。** 更新旧 mock 仅增加真实使用的 focus/selection API；测预览、编辑、IME、聊天焦点不拦截、关闭后迟到目标、标签12满、旧草稿不发GET、跨projecttree同步，以及 Ctrl＋I/S/autosave 原有案例。
- [ ] **Step 5：commit。** `git add sona_code/web/static/workspace-code-history.js sona_code/web/static/workspace-editor.js sona_code/web/static/workspace-editor.css sona_code/web/static/workspace.js sona_code/web/static/index.html tests/test_code_history.py tests/code-history-state.cjs tests/workspace-editor-state.cjs`；`git commit -m "feat(editor): add code position back and forward history"`。

## Task 6：语言客户端、定义动作和分析设置 UI

**Files:** Create `workspace-language-client.js`, `workspace-code-navigation.js`, `workspace-analysis-settings.js`, `tests/test_code_navigation.py`, `tests/code-navigation-state.cjs`; Modify `workspace-monaco.js`, `workspace-editor.js`, `workspace-editor.css`, `workspace.js`, `index.html`, editor测试 mock。

**Interfaces:**

- `createLanguageClient({urlFor, onStatus, onError})`：`open(DocumentSnapshot)`、`change(DocumentSnapshot)`、`close(projectId,path)`、`saved(projectId,path,revision)`、`setActive(projectId,family|null)`、`async definition(snapshot,position,signal) -> Target[]`、`reanalyze(projectId,family)`、`dispose()`。
- `createCodeNavigation({monaco,editor,canvas,client,snapshot,openTarget,history,onError}) -> {dispose()}`，snapshot() 返回当前DocumentSnapshot或null。
- `createAnalysisSettings({api,projectId,onSaved,onError}) -> {open(),dispose()}`，使用 Task 4 GET/PUT，onSaved 触发 reanalyze。
- urlFor(projectId,family,editorId) 从当前 admin API base 构造 ws/wss，禁止硬编码8117或/__recorder。

- [ ] **Step 1：red 客户端测试。** mock WebSocket 记录 messages/控制responses；覆盖150ms合并、definition前所有文档flush、重连snapshot、version/epoch/generation late结果、多结果菜单、取消、idle/hb、disposed状态。测 Review Focus 1/3。

```javascript
// 新 state harness 按已有 Element/vm 风格创建 FakeSocket 和可推进时钟。
class FakeSocket {
  static OPEN=1;
  static instances=[];
  constructor() {
    this.readyState=1;this.sent=[];this.handlers={};FakeSocket.instances.push(this);
    queueMicrotask(()=>{
      this.handlers.open?.({});
      this.receive({type:'status',state:'ready',generation:1});
    });
  }
  addEventListener(name,fn){this.handlers[name]=fn;}
  send(text){
    const message=JSON.parse(text);this.sent.push(message);
    queueMicrotask(()=>this.receive(message.type==='definition'
      ? {type:'definition-result',id:message.id,generation:1,version:message.version,targets:[]}
      : {type:'ack',id:message.id,generation:1,path:message.path,version:message.version}));
  }
  close(){this.readyState=3;this.handlers.close?.({});}
  receive(message){this.handlers.message?.({data:JSON.stringify(message)});}
}
globalThis.WebSocket=FakeSocket;
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
vm.runInThisContext(fs.readFileSync('sona_code/web/static/workspace-language-client.js','utf8'));
const client=createLanguageClient({urlFor:()=> 'ws://localhost/nav',onStatus(){},onError:message=>{throw Error(message);}});
const doc=(path,version,text)=>({projectId:'p',path,language:'typescript',epoch:1,version,text});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  client.open(doc('src/a.ts',1,'import {value} from "./b"; value;'));
  client.open(doc('src/b.ts',1,'export const value=1;'));
  await tick();
  client.change(doc('src/a.ts',2,'import {value} from "./b"; value();'));
  client.change(doc('src/b.ts',2,'export function value(){return 1;}'));
  const source=doc('src/a.ts',2,'import {value} from "./b"; value();');
  await client.definition(source,{line:0,character:32},new AbortController().signal);
  const socket=FakeSocket.instances[0];
  const queryIndex=socket.sent.findIndex(message=>message.type==='definition');
  assert.equal(socket.sent.slice(0,queryIndex).filter(message=>message.type==='change').length,2);
  client.change(doc('src/b.ts',3,'\nexport function value(){return 1;}'));
  await client.definition(source,{line:0,character:32},new AbortController().signal);
  assert.equal(socket.sent.filter(message=>message.type==='definition').length,2);
  client.dispose();
})().catch(error=>{client.dispose();console.error(error);process.exitCode=1;});
```

harness 直接运行 production client，不以源码字符串是否含关键词替代行为断言。Run `.venv/Scripts/python.exe -m pytest tests/test_code_navigation.py tests/test_workspace_editor.py -q`。

- [ ] **Step 2：实现 WS 客户端。** 每project/family一连接，editor_id=crypto.randomUUID，每connection request ID递增。客户端文档版本独立于Monaco getVersionId，model重建增加epoch；整个语言组revision用于缓存key。状态传递 generation，重连只保留最后文本快照，首批ack之前禁止查询。未知/过期reply不触发UI。ping20秒、inactive180秒回收，dispose 清除所有timer/provider/listener。
- [ ] **Step 3：接 Monaco 交互。** model 的同组 snapshot 包含完整草稿，在 create/change/close/save/reload处通知客户端；file extension 决定 javascriptreact/typescriptreact，用户切换language时close旧文档再open新文档。TS/JS defaults modeConfiguration 关闭内置definitions，保留其他内置能力。

```javascript
const toLsp = p => ({line:p.lineNumber-1,character:p.column-1});
const toMonaco = r => ({startLineNumber:r.start.line+1,startColumn:r.start.character+1,
  endLineNumber:r.end.line+1,endColumn:r.end.character+1});
// 应用级成功导航唯一入口：
await openTarget(source.projectId, target.path, toMonaco(target.range), {recordHistory:true});
```

addAction 注册应用F12动作；canvas capture Ctrl＋primary click 用 getTargetAtClientPoint 检查文本/符号，抑制内置异步GoTo/Peek，然后调同一definition动作。Ctrl链接提示的provider不弹菜单/不改model；多目标显示文件/行号按钮，Escape取消、上下键选择、Enter打开。单目标直接openTarget。光标/activefile变化、源版本或任意同组目标草稿变化后取消旧跳转意图；空白不产生请求。

- [ ] **Step 4：实现设置和状态 UI。** 工具栏“项目分析”打开本应用表单，字段对应 AnalysisSettings，Java模式source/maven/gradle、源码根/JAR逐行、项目JDK/本地Gradle、Python解释器。source为默认；gradle说明要评估构建脚本且只用本地工具；不塞入实现细节或额外授权流程。状态显示启动/索引/就绪/缺组件/超时/失败及“重新分析”。设置失败保留原值和草稿。组件status即使不可用也不影响editor.save。
- [ ] **Step 5：green、真实浏览器检查。** 跑 Task 5/6、旧 editor/drafts/tree/navigation/interaction 测试；用实际Monaco验证F12/精确Ctrlclick/候选选择/左右键及原快捷键，捕获page errors和网络请求。未打开目标须真实加载，目标草稿须无多余GET；页面返回settings再回workspace后无重复provider/listener。
- [ ] **Step 6：commit。** `git add sona_code/web/static tests/test_code_navigation.py tests/code-navigation-state.cjs tests/workspace-editor-state.cjs`；检查仅改本任务文件；`git commit -m "feat(editor): connect local definition navigation and analysis settings"`。

## Task 7：安装包、内网依赖与构建完整性

**Files:** Modify `scripts/build-sidecar.ps1`, `electron-builder.yml`, `electron/main.js`, `electron/verify-package.cjs`, `scripts/offline-kit.py`, `scripts/build-offline-windows.ps1`, `package.json`, `.gitattributes`, `tests/test_desktop_package.py`, `tests/test_offline_kit.py`; Create `tests/test_language_packaging.py`。

**Interfaces:** Electron resource 路径 `resources/tools/language-services`；启动 sidecar 设置 `SONACODE_BUNDLED_LANGUAGE_SERVICES`。开发用 build/language-services。组件不嵌入 PyInstaller onefile，避免每次启动解压大型JDK；sidecar包含后端Python代码，Electron extraResources复制固定组件。

- [ ] **Step 1：red 包校验测试。** fixture 小目录与manifest测试 verifyLanguageServicesBundle 缺/坏node.exe/java.exe/Pyright入口/JDT launcher/对应源码时抛错；offline INPUTS必须包含新manifest/lock/preparer且LF归档hash一致。

```python
# 在 Task 2 已有完整小组件fixture上创建缺件/篡改矩阵，
# 通过 node -e require('./electron/verify-package.cjs') 调用导出的新函数。
@pytest.mark.parametrize('missing', [
    'runtime/node.exe', 'runtime/jdk/bin/java.exe',
    'servers/node/node_modules/pyright/langserver.index.js'])
def test_after_pack_rejects_missing_language_entry(language_payload, missing):
    root, run_verifier = language_payload
    (root / missing).unlink()
    result = run_verifier(root)
    assert result.returncode != 0
    assert missing in result.stderr
```

language_payload fixture在此文件定义：复用 Task 2 manifest结构创建入口文件和hash，并以 subprocess.run(Node, '-e', script, root, capture_output=True,text=True) 执行。Run `.venv/Scripts/python.exe -m pytest tests/test_language_packaging.py tests/test_desktop_package.py tests/test_offline_kit.py -q`。

- [ ] **Step 2：接外网构建。** build-sidecar 在已有SDK/原组件流程旁调用 prepare-language-services --prepare；package增加语言组件 prepare/check快捷命令。electron-builder新增extraResources，main env增加固定路径；verifyDesktopPackage同时校验新资源和source manifest。

```yaml
# 加到 electron-builder.yml extraResources
- from: build/language-services
  to: tools/language-services
```

```javascript
// 加到 main.js 的 Object.assign(env, ...) 对象
SONACODE_BUNDLED_LANGUAGE_SERVICES: app.isPackaged
  ? path.join(process.resourcesPath,'tools','language-services')
  : path.join(__dirname,'..','build','language-services'),
```

- [ ] **Step 3：接内网重建。** offline-kit.py INPUTS加入新manifest/Node锁/prepare脚本；sources复制language-services和source archives，校验器检查这些来源。build-offline-windows.ps1 使用现有checked Copy-PreparedDirectory复制kit组件到build；只运行 --check，禁止在此路径 --prepare/npm install。冻结 source-input LF规则，所有输入在commit后的干净git archive上核对。
- [ ] **Step 4：green。** Task 7测试及旧openspec/native-sdk打包测试通过；`npm run editor:check`和准备脚本--check通过。手动从新bundle复制一个测试副本，删启动入口确认打包失败，不能删正在用的真实bundle。
- [ ] **Step 5：commit。** 按Files列表逐项stage；`git commit -m "feat(packaging): ship verified offline navigation runtimes"`。

## Task 8：六语言真实验收、离线重打包和交付

**Files:** Create `scripts/smoke-code-navigation.py`, `tests/test_language_native.py`, `docs/code-navigation.md`; Modify `docs/monaco-offline.md`, `scripts/smoke-windows-package.py`（仅增加组件env和可选导航probe，不混淆sidecar与桌面生命周期测试）。

**Interfaces:**

- smoke CLI：`--bundle PATH --report PATH [--package-resources PATH] [--offline]`；临时项目和独立配置放.report同级临时目录。结构化report含各语言返回path/range、服务版本、time/RSS和通过/失败。
- pytest opt-in `SONACODE_TEST_LANGUAGE_NATIVE=1`；组件缺件时原生测试失败，不能在启用后以skip冒充通过。未启用时正常suite只skip这组原生测试。

- [ ] **Step 1：写真实fixture和失败断言。** Java两包、继承、同名类与Maven多模块；Python包/import alias/类成员/同名模块；JS本地import、JSX React组件、TS路径别名与项目引用、TSX组件，目标最初未打开。测位置时使用 fixture 标注找到符号UTF-16列，不能固定一处错误列导致“无定义”误判。

```python
# 临时JS项目的真实跨未打开文件fixture；六语言各有同类明确输入。
files = {
  'main.js': 'import {answer} from "./lib.js";\nconsole.log(answer());\n',
  'lib.js': 'export function answer() { return 42; }\n',
  'jsconfig.json': '{"compilerOptions":{"allowJs":true,"checkJs":true}}',
}
# 正常path必须为lib.js；从main.js第二行answer处发definition，
# lib.js没有didOpen仍能被真实tsserver解析。之后修改其未保存model，
# 再query并断言回到新位置；磁盘lib.js保持原文本。
```

Tests/smoke使用Task 3 manager和Task 4 routes分别验证后端真实服务、用户入口；不是stub返回预写定义。JSX/TSX提供本地最小类型fixture，无需联网下载React。Java构建导入测试预备固定Maven/Gradle缓存和工具；source模式与build模式都有证据，缺缓存明确失败。

- [ ] **Step 2：真实服务 green 和回归。** 顺序运行真实六语言（不与全套并发以免现有时间敏感用例抖动），再跑全套 `.venv/Scripts/python.exe -m pytest tests -q`，记录启用/skip项及实际stdout。验证退出无孤儿，外部env密钥不出现在日志，WebSocket故障期间保存正常。
- [ ] **Step 3：离线用户路径。** blocked HTTP/HTTPS/ALL_PROXY、npm offline、PIP_NO_INDEX环境启动独立sidecar，首次查询每种语言。检查启动资源全为本地，类型包获取/源码附件/wrapper下载关闭；项目依赖在离线fixture中提前准备。真实浏览器A→B→C后退/前进，关闭标签重开、跨项目、12标签限额、reload/settings页draft保留、输入法与焦点均通过。
- [ ] **Step 4：构建并校验新安装包。** `npm run desktop:build:windows:electron`（固定原有ELECTRON_BUILDER_CACHE）；解包测试payload组件与files SHA，用新payload运行smoke并保存report。保留旧Test4，以含 Navigation 的新名字导出测试包。应用真实安装版运行中时只测试独立payload；需用户退出旧版后的桌面验收不能通过强杀或覆盖配置替代。
- [ ] **Step 5：形成交付文档并commit。** docs说明默认源码模式、显式构建导入、环境路径、六语言限制、快捷键冲突、清单版本、商业分发对应源码、实际体积/冷启动/RSS。补安装包/source/offline-kit用法，不把通过sidecar-smoke写成桌面生命周期已通过。Run `git diff --check` 和Task 8相关测试，`git commit -m "test(navigation): verify six-language offline definition workflows"`。
- [ ] **Step 6：干净内网重打包。** 从最终commit导出源码和新kit，用以下 PowerShell 命令在 workspace/build 内全新目录重建；开始前检查目标目录不存在，已有产物保留。

```powershell
$navigationRoot = (Resolve-Path -LiteralPath 'G:\workspace\sona-code').Path
$navigationPython = (Resolve-Path -LiteralPath '.venv\Scripts\python.exe').Path
$navigationKit = Join-Path $navigationRoot 'build\offline-kit-navigation'
$navigationSource = Join-Path $navigationRoot 'build\offline-source-navigation'
if ((Test-Path -LiteralPath $navigationKit) -or (Test-Path -LiteralPath $navigationSource)) {
    throw 'Navigation delivery directories already exist; preserve them and choose a fresh run directory'
}
git archive --format=zip --prefix=Sona-Code/ --output=dist/Sona-Code-source.zip HEAD
& $navigationPython scripts/offline-kit.py --prepare --kit $navigationKit
if ($LASTEXITCODE -ne 0) { throw 'Navigation kit export failed' }
Expand-Archive -LiteralPath 'dist/Sona-Code-source.zip' -DestinationPath $navigationSource
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $navigationSource 'Sona-Code\scripts\build-offline-windows.ps1') -Kit $navigationKit -Python $navigationPython
if ($LASTEXITCODE -ne 0) { throw 'Clean offline rebuild failed' }
```

再用重建产物运行六语言smoke，确认--check不联网、不重建上游OpenCode。故障和修复均记录在report，重新生成受影响的kit后才能交付。
- [ ] **Step 7：提交结果并推送。** 输出installer、source、offline-kit、third-party-sources的独立SHA-256；用户收到新包后可与我们并行测试。按现有授权推送feature分支，成功才报告上传成功；若GitHub仍403/网络重置，交付本地commit和source，明确阻塞，不重复索要密码/令牌或未经授权改repo权限。创建PR时按app要求attach。

## 自审与执行交接

设计第1–4节由Task 2/4/6覆盖；接口与草稿第5–6节由Task 1/3/4/6覆盖；历史第7节由Task 5覆盖；生命周期/协议/设置第8–10节由Task 1/3/4覆盖；离线/许可/验收第11–13节由Task 2/7/8覆盖。Review Focus五项均已指定行为测试归属，服务端协议单元测试和真实六语言测试分开报告。

任务接口名称、URI/UTF-16、message类型、version/epoch/generation和bundle目录在本文统一。settings数据模型由Task 4实现，但Task 2/3仅依赖契约且先用等形测试对象；实际组合在Task 4后验证。Task 7不重复嵌入JDK到PyInstaller；完整性、对应源码和干净LF归档都列入验收。

推荐 **Native（当前会话执行）**：我逐任务实现，最后安排一次独立全分支审查。八项任务对同一协议、文档版本和编辑器入口有顺序依赖，当前会话执行减少交接和重复上下文；如果选择 **Subagent-driven**，则每项由独立实现代理和独立审查代理完成后再进入下一项。

本计划保存后交给用户审阅并选择执行方式。批准计划前不安装新产品依赖、不创建产品模块、不把计划里的代码示例写成实现；批准后按所选执行技能持续完成至验收与交付。
