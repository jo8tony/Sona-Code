# Sona Code 内置 OpenSpec 设计

状态：首版已实现。Windows 离线资源、项目准备、命令派发与 UI 已验证；macOS 打包配置已接入，仍需在 Mac 上验收。

## 目标与推荐方案

用户安装 Sona Code 后，在项目中点击一次「启用 OpenSpec」，即可通过 `/opsx-propose` 等命令使用 OpenSpec。安装、首次准备项目和日常 OpenSpec CLI 操作均无需用户安装 npm、Node.js 或 OpenSpec，也无需临时联网下载依赖。模型调用仍使用当前项目选择的 Provider。

推荐随客户端发布固定版本的官方 OpenSpec CLI、完整依赖、Node.js 运行时，以及由该版本生成的 OpenCode 技能和命令模板。项目启用后，把技能和命令安装到项目的原生 OpenCode 位置，继续通过 V1 API 调用。项目规范、变更和自定义 schema 保存在项目目录，便于 Git 管理和其他工具接续使用。

技能文件只是工作流指令。官方技能会执行 `openspec new change`、`status`、`instructions`、`list`、`archive` 等命令，因此完整的免安装体验需要同时提供 CLI。[官方工作原理](https://github.com/Fission-AI/OpenSpec/blob/main/docs/how-commands-work.md)

## 已核实的约束

- 当前 Sona Windows 打包清单固定 OpenCode `1.18.32`，项目对接 V1 `/command`、`/skill`、`/session/{id}/command`，保留这套 API。
- 内置 OpenSpec 固定为 `1.14.1`，已核实 GitHub 最新发布版和 npm 发布版一致；其 package.json 声明 Node.js `>=20.19.0`。[发布记录](https://github.com/Fission-AI/OpenSpec/releases/tag/v1.14.1)
- OpenCode 对应的官方产物是 `.opencode/commands/opsx-<id>.md` 和 `.opencode/skills/openspec-*/SKILL.md`。[工具路径说明](https://github.com/Fission-AI/OpenSpec/blob/main/docs/supported-tools.md)
- 官方默认 core profile 为 6 个工作流；custom profile 可选择更多。Sona 本方案选择全部 12 个已核实的工作流，满足完整命令菜单的需求。
- OpenSpec 的全局配置支持 `XDG_CONFIG_HOME/openspec/config.json`，`delivery` 默认是 `both`，custom profile 读取 `workflows` 数组。
- 隔离验证已确认：OpenCode 1.18.32 的实例在加载命令后，新增文件不会自动进入已有命令列表，重启服务后能够加载。Sona 菜单当前还有 12 条硬截断，需要取消。
- OpenSpec 使用 MIT 许可证。发布包保留其版权和许可证，并纳入 Node.js 与依赖许可证清单。[OpenSpec 许可证](https://github.com/Fission-AI/OpenSpec/blob/main/LICENSE)

OpenSpec 版本由 Sona 发布时固定，运行时不向 GitHub/npm 查询新版本。升级 OpenSpec 需要重新发布包含完整离线资源的 Sona 安装包；不能让内网客户端执行 `npm install`、`npx` 或在线自升级。

## 用户操作与项目状态

1. 添加项目后，项目菜单提供「启用 OpenSpec」。用户也可在添加项目时选择同时启用。
2. 已经包含 OpenSpec 的项目显示「已有 OpenSpec」，提供「接入到 Sona」。先检查现有版本和文件，再决定补齐哪些 OpenCode 集成文件。
3. 启用期间显示「正在准备」。只有运行时、文件和原生 API 检查全部成功后才显示「可用」。
4. 准备完成后，自动重建该项目的空闲 OpenCode 服务并刷新当前页面的命令和技能列表。输入 `/` 即可滚动浏览完整列表，输入 `/opsx` 可直接筛选。
5. Sona 更新后，若有新版本的项目集成模板，显示「可更新」，由用户在项目中执行更新。升级客户端本身不立即重写项目文件。

项目状态使用 `not_enabled`、`detected`、`preparing`、`ready`、`disabled`、`upgrade_available`、`conflict`、`error`。`ready` 表示三个条件同时成立：内置 CLI 能运行，项目文件准备成功，原生 API 已加载预期命令及技能。

普通页面只展示可用状态、启用/更新按钮和可理解的错误。运行时路径、版本和文件差异放到详情中。

## 发布内容与运行方式

安装包资源建议如下，实际目录由桌面壳按平台解析为绝对路径：

```text
resources/tools/openspec/<version>/
  manifest.json
  bin/openspec.exe                  # Windows 原生启动器
  bin/openspec                      # macOS 原生启动器
  runtime/node.exe 或 node
  package/node_modules/@fission-ai/openspec/bin/openspec.js
  package/node_modules/@fission-ai/openspec/dist/
  package/node_modules/@fission-ai/openspec/schemas/
  package/node_modules/             # 完整、锁定的生产依赖
  templates/.opencode/commands/
  templates/.opencode/skills/
  templates/openspec/config.yaml
  licenses/
```

启动器是很小的原生程序，按资源目录找到内置 Node.js，并把原参数、标准输入输出、工作目录和退出状态传递给官方 CLI。进程以参数数组启动，兼容空格和中文路径。Windows 用 `openspec.exe` 同时服务 PowerShell、Git Bash 和 OpenCode 工具调用，避免依赖 shell 启动脚本的解析方式。

通过 `terminal/manager.py` 的共享环境构建入口，把启动器 bin 目录加入 Sona 启动的 OpenCode 和终端进程 PATH。只增加启动器目录，模型调用 `openspec ...` 会使用与项目模板配套的内置版本。CLI 子进程使用 Sona 专用的 OpenSpec 配置和数据目录；普通 shell 的其他工具继续使用现有环境规则。

桌面壳向 Python 后端传入资源根路径。后端验证资源清单、版本及摘要。Python 后端的管理操作直接调用启动器绝对路径，避免被用户 PATH 中的其他版本抢占。

启动器强制设置 `OPENSPEC_TELEMETRY=0`、`OPENSPEC_NO_UPDATE_CHECK=1` 和 `DO_NOT_TRACK=1`，清除外部 `NODE_OPTIONS`。官方 1.14.1 的版本检查会因此跳过；额外预加载 `runtime/offline.cjs`，阻止 CLI 的 HTTP/HTTPS、fetch、TCP/TLS、DNS、UDP 和 HTTP/2 网络访问。拦截仅作用于内置 OpenSpec 子进程，不影响模型 Provider 的内网连接。资源缺失时失败并提示重新安装，不尝试在线下载或调用 PATH 中的其他 OpenSpec/Node。

界面「同步内置模板」只用当前安装包内的模板更新项目文件；它不检查、下载或升级 OpenSpec CLI。启用项目、模板同步和工作流中的 CLI 操作均可离线执行。

## 构建时生成，运行时安装

新增 `packaging/openspec.json`，记录 CLI、Node.js、各目标架构、资源来源、校验摘要、工作流及 schema 版本。用独立的 package.json 和 lockfile 固定 OpenSpec 的生产依赖，不把构建生成的 node_modules 或二进制提交到仓库。

构建脚本在隔离目录准备官方 CLI，并设置专用全局配置：

```json
{
  "profile": "custom",
  "delivery": "both",
  "workflows": [
    "propose", "explore", "new", "continue", "apply", "update",
    "ff", "sync", "archive", "bulk-archive", "verify", "onboard"
  ]
}
```

随后在空白 staging 项目执行固定版本的 `openspec init --tools opencode --profile custom`，收集该版本实际生成的命令、技能完整资源目录及默认项目配置，并逐文件生成摘要清单。构建阶段生成模板，用户机器运行阶段复制和校验这些模板。

这样既使用官方模板，也能在写入真实项目之前计算差异和冲突。应用不会直接对真实项目执行可能清理历史集成文件的 `init --force` 或批量 `update --force`。

## 项目文件和持久化

启用后使用官方目录结构：

```text
project/
  openspec/
    config.yaml
    specs/
    changes/
  .opencode/
    commands/opsx-*.md
    skills/openspec-*/SKILL.md
```

应用的版本、启用状态、文件所有权和更新记录保存在 Sona 配置目录下的 `openspec-projects.json`，以规范化项目路径作为键；不向用户业务配置注入 Sona 私有字段。

每个已管理文件至少记录路径、首次安装版本、最近安装版本和 SHA-256。写入前确认目标路径在项目根内，拒绝被符号链接或路径穿越引导到项目外。写入采用临时文件与原子替换，并记录可恢复的操作日志；多文件更新失败时恢复该次操作之前的文件与状态。

文件处理规则：

| 目标情况 | 行为 |
| --- | --- |
| 文件不存在 | 创建并登记所有权 |
| 是 Sona 管理的文件，当前摘要等于上次安装摘要 | 可更新到新版模板 |
| 是 Sona 管理的文件，但用户改过 | 标记冲突，展示差异，保留用户内容 |
| 是已有外部文件 | 保留，显示来源；兼容性校验通过后可直接使用 |
| 外部同名文件与内置模板冲突 | 接入操作报告冲突，不宣布完成 |
| 已有 config.yaml 或 config.yml | 使用现有配置，仅在两者都不存在时创建默认配置 |
| 已有 specs、changes、自定义 schema、其他工具集成 | 保留原有内容，补齐所需 OpenCode 集成 |

既有项目能否直接接入，取决于其 schema 和 CLI 兼容性。遇到需要旧版 CLI、未知布局或同名覆盖的问题，保留「已有 OpenSpec」状态并说明具体不兼容点，不能只凭文件夹存在宣布可用。

## 命令与技能加载

全套资源安装后，斜杠菜单主要展示下列 12 个工作流入口。技能保留原生发现、自动调用和 `/skills` 选择能力；菜单可合并同一工作流的技能别名，减少重复选项。

| 命令 | 对应技能 | 作用 |
| --- | --- | --- |
| `/opsx-propose` | openspec-propose | 一次生成变更规划 |
| `/opsx-explore` | openspec-explore | 分析需求与方案 |
| `/opsx-new` | openspec-new-change | 建立变更 |
| `/opsx-continue` | openspec-continue-change | 生成下一个产物 |
| `/opsx-ff` | openspec-ff-change | 批量生成规划产物 |
| `/opsx-apply` | openspec-apply-change | 执行任务 |
| `/opsx-update` | openspec-update-change | 修改规划 |
| `/opsx-sync` | openspec-sync-specs | 同步规范 |
| `/opsx-archive` | openspec-archive-change | 归档变更 |
| `/opsx-bulk-archive` | openspec-bulk-archive-change | 批量归档 |
| `/opsx-verify` | openspec-verify-change | 验证实现 |
| `/opsx-onboard` | openspec-onboard | 引导使用 |

具体映射由构建所用官方版本验证并保存到 manifest，不通过名称猜测。

命令仍委托 V1 `/session/{id}/command`，CLI 工作目录保持项目根目录，技能复制完整资源目录以保留相对资源路径。所有 shell 工具输出继续通过现有 OpenCode 消息事件显示，Provider 路由和录制链路保持现有处理方式。

原生 `/skill` 的 location 必须指向期望的项目技能位置；`/command` 必须包含预期命令。对同名全局/项目命令需要增加真实 V1 兼容测试，验证 native 返回的模板及来源能够确认实际加载版本。API 无法证明来源时保留冲突状态，不能只比较命令名称。

手动选择工作流的历史记录展示 OpenSpec 命令和参数；自动 skill 工具调用、资源读取及执行状态依据原生事件展示。选择一个命令不等同于成功加载技能。

## 生命周期、权限和刷新

- 新增按项目执行配置变更的入口，沿用 WorkspaceManager 的生命周期锁和任务派发保护，检查目标项目的运行任务、派发中的请求及队列状态。
- 目标项目有活跃任务时，启用、更新和禁用返回明确的冲突状态；前端保留当前操作详情，允许任务结束后重试。
- 准备成功后关闭目标项目的空闲服务，下一次读取时用新环境重建，再获取命令和技能。其他项目继续工作。
- 有已有原生终端时显示需要重启终端；后端服务刷新不能更新已经运行的 TUI 环境和缓存。
- 原生 deny skill 权限不能假定会删除对应 slash command。Sona 菜单和后端派发都校验工作流到技能的映射，尊重项目启用状态和选中 Agent 的权限。队列实际提交前重新校验。
- 禁用保留技能目录，通过应用进程的原生 permission.skill 设置隐藏相关技能，不移动技能文件。Sona 的工作流菜单与后端同时禁用。原生 TUI 的自定义命令行为须按 V1 单独验证，不把该 UI 的技能禁用误报为命令文件已移除。
- 技能管理页面增加「内置 OpenSpec」入口，显示功能说明及项目状态。内置资源由客户端版本管理；当前项目 picker 仍只展示原生 API 实际加载并允许使用的技能。
- 移除 Sona 项目记录或卸载客户端时保留项目的规范、变更与集成文件。

## API 与代码落点

建议增加以下管理接口，继续使用当前 admin API 前缀：

| 方法与路径 | 用途 |
| --- | --- |
| GET `/openspec/info` | 内置功能、可用工作流与运行时检查 |
| GET `/workspace/projects/{id}/openspec` | 项目状态、版本、来源与冲突摘要 |
| POST `/workspace/projects/{id}/openspec/enable` | 准备项目或接入已有集成 |
| POST `/workspace/projects/{id}/openspec/update` | 从安装包同步已管理模板，不联网 |
| POST `/workspace/projects/{id}/openspec/disable` | 禁用当前项目的 Sona 工作流入口 |

应用管理操作直接完成本地准备，不作为 AI 消息发送，不消耗模型调用。接口仅接受已登记项目 ID，通过现有项目解析得到路径；客户端不能提交任意执行命令。操作开始后使用持久状态或 operation ID 返回进度，重复请求不会重复覆盖文件。

建议实现位置：

| 文件/模块 | 职责 |
| --- | --- |
| `packaging/openspec.json` 与独立依赖锁 | 版本、平台、资源和模板映射 |
| `scripts/prepare-openspec.*` | 获取、校验、生成模板、组装离线资源 |
| 原生 OpenSpec launcher | 启动内置 Node.js 和官方 CLI |
| `sona_code/openspec/bundle.py` | 资源定位、校验、CLI 调用 |
| `sona_code/openspec/projects.py` | 状态、文件归属、初始化、冲突与更新 |
| `sona_code/admin/openspec_routes.py` | 管理端点 |
| `sona_code/terminal/manager.py` | 工作区和终端的启动器环境 |
| `sona_code/workspace/manager.py` | 按项目的派发保护与服务重建 |
| `sona_code/workspace/routes.py` | 命令/技能验证、工作流权限和队列提交检查 |
| `workspace.js`、`skills.js` | 一键启用、状态、完整命令菜单、重复入口合并 |
| Electron 与 Tauri 配置、桌面启动代码 | 安装包资源与路径传递 |

Node.js 与 OpenSpec 包放到桌面 resources，避免将大量资源塞进 PyInstaller 单文件，每次启动重复解压。Windows Electron 安装包和 macOS Tauri 安装包使用同一 bundle manifest，分别准备对应架构的运行时与 launcher。

固定版本为 OpenSpec 1.14.1、Node.js 22.23.3 和 Go 1.27.1。Go 启动器运行时要求 macOS 13，已将 Tauri 最低系统声明从 10.15 调整为 13.0；Mac 上仍需验证 arm64/x64、执行权限与签名后的启动。

## 构建与验证

Windows 执行 `npm run desktop:build:windows:electron`，macOS 执行 `npm run desktop:build`。两者的 sidecar 构建步骤都会运行 `scripts/prepare-openspec.py`；首次构建下载并校验固定版本的 Node/Go，按照独立锁文件安装依赖并生成官方完整模板。用户运行安装包时无需 npm、Node 或临时下载依赖。开发环境也可用 `.venv/Scripts/python.exe scripts/prepare-openspec.py` 单独生成 Windows 资源。

开发机获取新版依赖只发生在制作安装包时，最终安装包包含完整运行时和依赖。已有 Node/Go 归档和 npm 缓存时，运行 `python scripts/prepare-openspec.py --offline` 可以完全离线构建资源；缺少缓存则立即失败，不回退联网。构建时的官方 init 使用独立 HOME/USERPROFILE/CODEX_HOME，避免新版的旧命令清理逻辑扫描真实用户目录。

`tests/test_openspec_offline.py` 验证网络拦截、启动器强制覆盖外部遥测/升级设置、缺少内置 Node 时不回退，以及官方 `openspec update` 在生产环境中发起零次网络请求。

常规回归运行 `python -m pytest tests/ -q`。原生验收设置 `OPENSPEC_TEST_BUNDLE` 为资源包绝对路径、`OPENCODE_TEST_BINARIES` 为原生程序绝对路径，然后运行 `python -m pytest tests/test_openspec_native.py -q`。额外设置 `OPENSPEC_TEST_SIDECAR` 可直接通过最终打包的后端验收。测试使用本地脚本化 Provider，真实执行 bash/write 工具，完成 propose → apply → verify → archive，并检查工具状态、归档文件、规范同步和历史中的工作流信息；不会使用真实模型凭证。

## 首版验收与实施顺序

首版依次完成：固定离线资源与 launcher；项目准备与冲突保护；生命周期刷新与权限检查；一键启用 UI 和完整菜单；安装包验收。完整的 specs/changes 可视化管理可在后续版本加入，首版工作流由原生对话完成。

验收必须覆盖：

1. Windows 干净环境不安装 Node.js/npm/OpenSpec，离线启用新项目后显示全部 12 个命令；基础 CLI 检查和 schema 查询可执行。
2. 使用测试 Provider 完成 propose → apply → verify → archive 的实际流程，确认 CLI 产物、原生事件和历史一致，而非仅检查菜单名称。
3. 两个项目并行，启用/更新一个项目时不打断另一个；目标项目有活跃任务时拒绝变更。
4. 加载过命令后再启用，自动重建服务并立即显示新命令；已有终端的重启提示正确。
5. 中文、空格路径，以及 PowerShell/Git Bash 的 CLI 调用；参数、标准输入输出和退出码正确。
6. 已有 OpenSpec 配置、定制模板、自定义 schema、其他工具集成和同名全局命令得到正确识别与保护。
7. 更新失败、进程中断和文件冲突可恢复；重复启用保持幂等；路径穿越和符号链接不能扩大写入范围。
8. 技能 deny、Agent 权限、手动工作流、自动技能及队列提交的行为一致。
9. 客户端升级/回退与项目模板版本不同步时显示准确状态，保留已有 specs/changes；卸载不删项目内容。
10. macOS 支持架构、最低系统要求、应用资源执行权限和签名包中的运行时调用通过验收。

新增 `tests/test_openspec.py` 覆盖状态与文件操作，以及 opt-in `tests/test_openspec_native.py` 覆盖固定 OpenSpec/OpenCode 组合；与现有技能、终端、队列测试一并执行。发版前跑完整 tests，并实际验证 Windows 安装包和支持的 macOS 安装包。
