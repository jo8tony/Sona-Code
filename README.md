# Sona Code

本地大模型 API 代理记录器：**透明转发**所有 LLM API 请求/响应（含流式 SSE），同时**旁路落盘**完整调用明细，并提供 Web 管理界面与轨迹分析。

> 设计原则：代理转发零侵入——所有请求/响应逐字节透传、不改任何头；解析与记录全部在旁路异步完成，任何写盘失败只记日志告警，绝不中断代理流。

## 功能特性

- **透明代理**：按上游 `base_url` 拼接转发，请求/响应（含流式 SSE 分块、gzip、JSON）原样透传
- **完整录制**：请求体/响应体、SSE 分块、时间戳（TTFT/首字节/解码耗时/token 吞吐）、headers、工具调用，落盘 JSON + JSONL 索引
- **多上游路由**：`/up/{name}/...` 前缀选择命名上游，其余走默认上游
- **会话轨迹（Trajectory）**：按轮次（Turn）聚合的调用时间轴，支持 Inspector 多 Tab 结构化查看
  - Summary / Preview（Markdown 渲染）/ Raw / System Prompt（行级 diff）/ Tools（增删对比）/ Options / Usage（Token 柱状图）/ Timing / Source / Headers / Chunks
- **数据清理**：按日期删除、清空全部、保留策略（`retention_days` 自动清理），调用详情页单条删除
- **脱敏**：请求头可配置脱敏（`authorization` 等），记录时替换为 `***`
- **凭据透传**：`key_strategy` 默认 `keep`，完整透传客户端凭据头；可选 `replace` 注入上游 key
- **Web 终端**（Windows / macOS）：浏览器中启动多个 opencode / shell 会话，保存项目目录、多 tab 并行、断线自动重连、会话在服务重启前保活
- **桌面工作区**：按项目查看 OpenCode 对话历史，在自定义界面中发送消息、查看工具步骤与文件改动；代理的轨迹、调用列表、仪表盘和设置保持独立入口

## 环境要求

- Python ≥ 3.10
- 依赖：FastAPI / uvicorn / httpx / pydantic（见 `pyproject.toml`）
- Web 终端支持 Windows（依赖 `pywinpty` ConPTY）与 macOS（系统 PTY）；Windows x64 安装包已内置固定版本的 OpenCode CLI，macOS 仍从 PATH 查找

## 安装

```bash
cd llm-proxy  # 进入含 pyproject.toml 的项目根目录

# 创建虚拟环境并安装（含开发依赖）
python3.10 -m venv .venv  # 也可使用其他 Python 3.10+ 版本
.venv/bin/python -m pip install -e ".[dev]"
```

## 快速开始

### 1. 准备配置

首次启动若配置文件不存在会自动生成默认配置并写盘。**本仓库约定**使用项目本地配置，避免 macOS 沙盒无法写入家目录的问题：

```bash
# 复制一份配置文件（首次启动后会自动生成，也可手工创建）
# .runtime/ 已在 .gitignore 中，仅本机有效
```

`.runtime/config.json` 结构示例：

```jsonc
{
  "server": {
    "host": "127.0.0.1",
    "port": 8117,
    "admin_prefix": "/__recorder"
  },
  "upstreams": [
    {
      "name": "deepseek",
      "base_url": "https://api.deepseek.com",
      "api_key": "sk-xxxxxxxxxxxxxxxx",   // key_strategy=keep 时不注入，仅 replace 用
      "models": [                       // 可选：OpenCode 手动模型 ID 与输入能力
        { "id": "deepseek-chat", "input_modalities": [] },
        { "id": "deepseek-vision", "input_modalities": ["image"] }
      ],
      "extra_headers": {},
      "key_strategy": "keep"              // keep=透传客户端凭据 | replace=注入上游 key
    }
  ],
  "default_upstream": "deepseek",
  "outbound": { "proxy_url": "" },        // 出站代理，支持 http/https/socks5，空=直连
  "recording": {
    "dir": "/path/to/.runtime/records",   // 记录存储目录
    "redact": true,                       // 请求头脱敏
    "redact_headers": ["authorization", "x-api-key", "api-key", "cookie"],
    "session_id_headers": ["x-deepseek-harness-session-id", "x-session-id"],
    "record_request_headers": true,
    "record_response_headers": true,
    "record_raw_chunks": false,           // 是否记录原始 SSE 分块
    "max_capture_mb": 20.0,               // 单次请求/响应体截断阈值
    "retention_days": 0                   // 保留天数，0=永久；>0 启动+每小时自动清理
  }
}
```

### 2. 启动

```bash
.venv/bin/llm-api-proxy-recorder --config .runtime/config.json
# 等价：.venv/bin/python -m llm_api_proxy_recorder --config .runtime/config.json
```

CLI 参数：

| 参数 | 说明 |
|---|---|
| `--host` | 监听地址（默认 `127.0.0.1`） |
| `--port` | 监听端口（默认 `8117`） |
| `--config` | 配置文件路径（默认 `~/.llm-api-proxy-recorder/config.json`） |
| `--records-dir` | 记录目录（覆盖 `recording.dir`，仅本次生效） |
| `--admin-prefix` | 管理路径前缀（默认 `/__recorder`） |

### 3. 访问地址

| 用途 | 地址 |
|---|---|
| Web 管理界面 | http://127.0.0.1:8117/__recorder/ |
| 代理入口 | http://127.0.0.1:8117 |
| 多上游路由 | http://127.0.0.1:8117/up/{name}/v1/chat/completions |

### 4. 通过代理调用

```bash
# 非流式（走默认上游）
curl http://127.0.0.1:8117/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_API_KEY>" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"你好"}]}'

# 流式 SSE
curl -N http://127.0.0.1:8117/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_API_KEY>" \
  -d '{"model":"deepseek-reasoner","stream":true,"messages":[{"role":"user","content":"1+1=?"}]}'

# 指定上游
curl http://127.0.0.1:8117/up/deepseek/v1/chat/completions -H "Content-Type: application/json" ...
```

调用后访问 Web UI，即可在「调用列表」「轨迹」中查看记录。

## Web 管理界面

| 页面 | 说明 |
|---|---|
| 工作区 | 左侧项目与 OpenCode 对话，右侧消息、文件改动和工具活动；默认首页 |
| 仪表盘 | 概览统计（记录数、错误、token 等） |
| 调用列表 | 分页/过滤/搜索全部调用，可进入详情，详情页可删除单条 |
| 轨迹 | 按会话聚合的时间轴 + 轮次账本，右侧 Inspector 多 Tab 分析 |
| 终端 | 旧版终端界面，仍可通过 `#/terminal` 进入 |
| 模型 | 提供商与模型管理、独立密钥、接口类型、能力与上下文限制、默认模型、代理/直连 |
| 设置 | 出站代理、记录设置、**数据清理**、OpenCode 程序与终端、高级配置 |

## 桌面工作区

打开 Web UI 默认进入「工作区」。点击左侧「项目与对话」旁的 `＋` 添加项目目录；项目列表默认折叠，点击项目标题可展开对话。右侧可发送文字、粘贴截图或添加图片；输入 `@` 搜索并选择项目文件，引用以蓝色文件图标和文件名内联显示在正文中，删除标记即可移除引用。可查看按日期排列的 Markdown 回复、汇总的工具步骤、文件改动统计与逐行差异、活动时间线、待办和子对话，并处理权限请求与交互式提问。输入 `/` 查看应用快捷命令和 OpenCode 项目命令，可用方向键选择并回车选中；输入 `!` 在项目目录执行 shell 命令。对话底部显示上下文占用、输入/输出 Token、生成速度、请求耗时和轮次数。对话菜单可重命名、创建分支、压缩上下文和删除。模型选择器按提供商展示应用模型，可搜索模型并选择 Agent 和思考强度；原生模型开启显示后仍可管理其原生凭据。模型认证失败会显示服务商、HTTP 状态和错误详情，可填回上次提问后切换模型重试。工作区在后台运行 `opencode serve`；顶部「OpenCode 终端」可进入原生 TUI 使用尚未移植到工作区的功能。Windows 安装包使用随包 OpenCode，macOS 从 PATH 查找。模型及代理接口来源在独立「模型」页面配置。

提供商、模型、密钥与调用路由统一在顶部「模型」页面管理，设置页不再重复编辑。支持手动配置 OpenAI Chat Completions 和 Responses，可按模型覆盖接口类型。提供商代理开关默认开启，密钥按「模型独立 Key → 提供商 Key → 无认证」使用。关闭代理后，工作区对话与工具活动正常显示，但不会产生本地代理调用记录和轨迹。上下文和输出长度按服务实际填写；已有模型缺失的限制不会被自动猜测。

工作区选择器默认只显示应用模型；开启「显示 OpenCode 原有模型」后，按当前项目合并显示原生模型，其接口和路由保持原生行为。保存模型配置有版本冲突及运行中任务保护，空闲时自动重建后台服务；已有原生终端需重启。

工作区的对话历史由 OpenCode 持久化；顶部「轨迹」与「调用列表」继续显示本代理录制的模型请求。架构和首版范围见 [工作区设计](docs/workspace.md)。

工作区右侧的「技能」菜单支持导入、删除、启用和停用技能。桌面版新增时使用系统目录选择器，选择含 `SKILL.md` 的单个目录；普通浏览器可填写绝对路径。应用将完整目录和资源复制到应用 OpenCode 配置目录的 `skills/<name>`，保留源目录。技能需包含 YAML frontmatter 的 `name` 和 `description`，名称不能与工作区快捷命令重名。启停写入官方 `permission.skill.<name>` 的 `allow` / `deny` 权限，文件仍留在原位置；旧 `skills-disabled` 副本会在启动时迁回并保留停用状态。

技能页面直接展示本应用与本机 OpenCode 的全部技能，可按名称和描述搜索。本机技能直接读取原始 XDG 配置目录下的 `opencode/skills` 和 `opencode/skill`（默认 `~/.config/opencode`），无需复制；启停仅修改本应用的隔离配置，不能在页面删除本机原文件。同名时优先本应用副本，其他来源显示冲突。

输入 `/skills` 选择已启用且实际被当前 OpenCode 和 Agent 允许的技能，技能以蓝色图标和名称内联显示在输入正文中，与任务文字一起编辑；拼错或无法识别的命令显示错误提示。补充任务并发送，通过 V1 原生命令接口加载技能和资源目录。历史消息同样将技能名称与任务文字内联显示，可展开技能内容；AI 自动调用原生 `skill` 工具时显示技能名称和加载状态，读取技能文件、参考资料时也有独立记录，失败则显示失败及详情。手动调用标记会随历史保存。

技能变更须等待所有工作区任务空闲；应用会重启空闲 OpenCode 服务以刷新技能缓存和外部目录，原生终端需重新启动。OpenCode 原生技能命令可能仍列出已禁用技能，因此本应用在菜单和发送接口额外执行权限校验。

## Web 终端

在浏览器中直接操作命令行版 opencode（或通用 shell），无需手动 cd 目录：

1. 打开 Web UI → 地址栏切换到 `#/terminal` →「+ 新建会话」
2. 选择会话类型（opencode / shell），通过**目录浏览器**（Windows 盘符或 macOS 根目录 → 逐级子目录）或直接粘贴绝对路径选择项目目录
3. 「启动会话」即在该目录下通过 ConPTY 启动 opencode TUI，完整界面渲染在浏览器 xterm.js 中
4. 多个项目可同时开启会话，tab / 侧栏切换；关闭 tab 即终止进程；意外退出可一键重启（同目录同类型）

行为细节：

- 会话由服务端托管，**浏览器关闭/断网不杀进程**，重新打开自动恢复；WebSocket 断线自动重连并回放缓冲。服务停止会结束会话
- 成功启动过的项目目录自动保存于配置文件旁的 `terminal-projects.json`；会话结束或服务重启后仍显示在侧栏，可再次启动，也可单独移除项目
- 设置页「Web 终端 → OpenCode 接口来源」可选**使用本代理接口**或**使用 OpenCode 原始配置**。本代理模式会为新启动的 OpenCode 进程临时设置对应 provider 的 `baseURL`（不修改项目的 `opencode.json`）；可指定代理上游及 OpenCode provider ID。原始配置模式不注入代理地址
- Windows 默认运行安装包中的 OpenCode，设置页可切换为自定义命令。随包版禁止自更新，只随本应用发布升级；如随包文件不可用，自动模式才回退到 PATH
- 上游服务中可逐个填写 OpenCode 的模型 ID，并标记图片、音频、视频、PDF 输入能力。模型 ID 是上游接口接受的 `model` 值，不含 `provider/` 前缀。配置手动模型后，新建的代理模式会话会从本地配置添加这些模型并停用在线模型目录更新；原有缓存中的模型可能继续显示。此模式使用 OpenAI 兼容的 `/chat/completions` 接口，勾选能力仅控制 OpenCode 的输入识别，实际调用仍需上游模型支持
- 设置页另有 **OpenCode 全局配置** 编辑区，读取并保存本应用隔离目录中的 `opencode.jsonc`（优先使用已有的 `.jsonc`，其次 `.json`）。支持 JSONC 注释及末尾逗号，保存时保留原文并校验语法，在文件被外部修改时阻止覆盖。可一次性导入用户已有的配置、扩展和 `auth.json`；已有文件不覆盖，历史会话、缓存和日志不导入
- 如果上游 `base_url` 自带 `/v1`，代理会保留该路径；请使所选上游与 OpenCode provider 的接口格式一致

## 管理端 API

前缀 `{admin_prefix}/api`，默认 `/__recorder/api`：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/overview` | 仪表盘概览统计 |
| GET | `/calls` | 调用列表（分页/过滤） |
| GET | `/calls/{id}` | 调用详情 |
| DELETE | `/calls/{id}` | 删除单条记录 |
| GET | `/records/stats` | 存储统计（每日期文件数/字节数） |
| DELETE | `/records/date/{date}` | 删除某日期全部记录（`YYYY-MM-DD`） |
| DELETE | `/records/all` | 清空全部记录 |
| POST | `/records/cleanup` | 手动执行保留策略清理 |
| GET | `/trajectory/sessions` | 轨迹会话列表 |
| GET | `/trajectory/sessions/{key}` | 轨迹会话详情（Turns） |
| GET | `/settings` | 读取配置 |
| PUT | `/settings` | 保存配置（部分项需重启） |
| POST | `/settings/test-upstream` | 测试上游连通性 |
| GET/PUT | `/settings/opencode-config` | 读取/保存 OpenCode 全局 JSONC 配置 |
| GET/POST | `/settings/opencode-import` | 预览/执行已有 OpenCode 配置、扩展与凭据导入 |
| GET | `/meta` | 服务元信息 |
| GET | `/terminal/check` | 检测 opencode / shell 命令 |
| GET | `/terminal/sessions` | 终端会话列表 |
| POST | `/terminal/sessions` | 创建会话（`cwd` / `kind` / 尺寸） |
| DELETE | `/terminal/sessions/{id}` | 终止会话 |
| GET | `/terminal/projects` | 已保存的项目目录 |
| DELETE | `/terminal/projects` | 移除项目目录（请求体含 `path`） |
| GET | `/terminal/fs` | 目录浏览（无 `path` → 盘符列表） |
| WS | `/terminal/ws/{id}` | 终端流（输入/resize；输出二进制帧） |

## 数据存储

记录文件存放于 `recording.dir`（默认 `~/.llm-api-proxy-recorder/records`，本仓库为 `.runtime/records`）：

```
records/
├── calls/
│   └── 2026-08-16/
│       ├── c20260816_182552_a1b272.json          # 最终记录
│       └── c20260816_182552_a1b272.partial.json  # 请求时占位（响应后定稿删除）
└── index/
    └── 2026-08-16.jsonl                          # 当日索引行
```

- 写盘采用**原子写**（临时文件 + `os.replace`），崩溃不产生半截文件
- 上游中途断流也会以 `status=error` 定稿，保留已收到分片

## 测试

```bash
.venv/bin/python -m pytest tests/ -q
```

覆盖：代理转发、SSE 解析、录制/索引、轨迹聚合、数据清理、终端会话与项目持久化等。

## macOS 桌面版

桌面版使用 Tauri 2 作为原生窗口，现有 FastAPI 服务通过 PyInstaller sidecar 随应用分发。当前构建脚本支持 Apple Silicon 与 Intel macOS；产物只能在对应架构的 Mac 上运行。

首次准备构建环境：

```bash
python3.12 -m venv .venv-build
.venv-build/bin/python -m pip install -U pip setuptools wheel
.venv-build/bin/python -m pip install -e ".[dev,desktop]"
npm install
```

生成 sidecar、`Sona Code.app` 和 `.dmg`：

```bash
npm run desktop:build
```

构建产物位于 `src-tauri/target/release/bundle/`。默认使用 ad-hoc 签名，适合本机测试；公开分发时应在 `src-tauri/tauri.conf.json` 中换用 Developer ID Application 身份，并完成 Apple notarization。

推送到 `main` 分支或在 GitHub Actions 中手动运行 `Build macOS Installers`，会分别在 Apple Silicon 和 Intel 构建机上运行测试、打包，并上传 `sona-code-macos-arm64` 与 `sona-code-macos-x86_64` 两个 artifact。下载对应架构的 artifact 后解压，即可取得 `.dmg` 安装盘。CI 产物沿用 ad-hoc 签名，尚未经过 Apple notarization。

### GitHub Release 发布

推送 `v*` 版本标签或发布 GitHub Release 时，Windows x64、macOS Apple Silicon 和 Intel 工作流会构建并验证安装包，然后将 `.exe` / `.dmg` 上传到对应 Release；标签尚无 Release 时会自动创建。已有 Release 的说明会保留，同名安装包会替换。普通 `main` 推送仍只保存 Actions artifact。

为已有版本补发安装包：在 GitHub Actions 中分别打开 `Build Windows Installer` 和 `Build macOS Installers`，点击 `Run workflow`，选择包含最新工作流的 `main` 分支，并填写 `release_tag`（例如 `v1.2.0`）。工作流会检出该标签的代码，构建并上传到该版本 Release。留空则仅构建并保存 artifact。

### Windows 安装包

推送到 `main` 分支会触发 `.github/workflows/build-windows.yml`：在 Windows x64 环境运行测试、构建 PyInstaller sidecar，并生成两种 NSIS 安装程序：原有 Tauri 版与内置 Chromium 的 Electron 离线版。Actions 分别上传 `sona-code-windows-x64`、`sona-code-windows-x64-offline`。

Windows 本机也可执行：

```powershell
python -m venv .venv-build
.venv-build\Scripts\python -m pip install -U pip setuptools wheel
.venv-build\Scripts\python -m pip install -e ".[dev,desktop]"
npm install
npm run desktop:build:windows
# 或构建无需 WebView2 的 Electron 安装包
npm run desktop:build:windows:electron
```

Windows 构建会根据 `packaging/opencode.json` 下载并校验固定的 OpenCode x64 baseline 发布资产，并将其与 Python sidecar 一起写入安装包。构建机需能访问 GitHub Releases；Electron 构建机还需下载 Electron、electron-builder 和 NSIS 的构建依赖。把 `dist/electron/Sona-Code-Offline-*-Setup.exe` 拷贝到内网 Win10 x64 机器安装即可；安装或启动时无需下载 WebView2、OpenCode、Python 或 Chromium。Electron 版沿用原有本地服务和数据目录，界面、托盘及目录选择均可使用，安装包比 Tauri 版大。Windows 原生终端仍需 Win10 1809 或更新版本提供 ConPTY。

如可接受继续使用 WebView2，也可以给 Tauri 配置随包运行时以避免安装时联网；本仓库新增的 Electron 版则完全不依赖 WebView2。两种桌面版都监听 `127.0.0.1:8117`，请勿同时运行。

## 目录结构

```
llm_api_proxy_recorder/
├── admin/api.py        # 管理端 API
├── app.py              # FastAPI 应用工厂 + 保留清理后台任务
├── cli.py              # CLI 入口
├── config.py           # Pydantic 配置模型 + 加载/保存
├── proxy/
│   ├── client.py       # 上游 HTTP 客户端
│   ├── handler.py      # 透明代理转发 + 旁路录制
│   └── router.py       # 多上游路由
├── recording/
│   ├── models.py       # 录制数据模型
│   ├── parse.py        # 请求/响应解析（含会话归属、轨迹 Turn）
│   ├── redact.py       # 头脱敏
│   ├── sse.py          # SSE 解析器
│   └── store.py        # 文件存储/索引/清理/统计
├── terminal/           # Web 终端（Windows ConPTY / macOS PTY）
│   ├── manager.py      # PTY 会话管理（spawn/IO 泵/缓冲/生命周期）
│   ├── posix_pty.py    # macOS PTY 进程封装
│   ├── projects.py     # 项目目录持久化
│   └── routes.py       # 会话 CRUD + 目录浏览 + WebSocket
└── web/static/         # Web UI（原生 JS：app/calls/trajectory/settings/terminal 等）
```

### Windows 桌面运行行为（2.0.1）

- 点击窗口关闭按钮或按 `Alt+F4`，应用隐藏到系统托盘，代理、工作区任务和终端继续运行。
- 左键点击托盘图标，或右键选择「打开 Sona Code」，恢复主窗口；再次打开桌面快捷方式也会恢复已有窗口，不会启动第二个后端。
- 完全退出请右键托盘图标选择「退出」，应用会清理后端及其子进程。
- Windows 流水线安装实际 NSIS 产物后，检查窗口关闭/再次启动、中文及空格项目路径、随包 OpenCode V1 会话持久化，以及 ConPTY 命令执行。
- 当前 Windows 安装包支持 x64；原生终端需要支持 ConPTY 的 Windows 10 1809 或更新系统。AI shell 命令建议安装 Git for Windows，应用会自动检测 Git Bash。
