# Monaco 编辑器与内网交付实施计划

Goal: 在 Sona Code 集成本地代码编辑，完成离线构建与可验证 Windows 交付，再推送代码。
Architecture: 受限本地文件 API + 独立 Monaco 面板 + 已构建静态资源 + 原有 PyInstaller/Electron 打包链。
Tech Stack: FastAPI、原生 JavaScript、Monaco 0.57.0、esbuild、pytest、Electron。
Spec: `docs/superpowers/specs/2026-10-10-monaco-editor.md`

Global Constraints: 不改变 OpenCode V1、代理转发和原生持久化；无 CDN；原有源快照保留；不提交运行配置/构建缓存/安装包；用户已授权功能分支推送和 PR。
Review Focus: 文件路径边界、外部修改冲突、未保存状态、Worker 路由、打包资源、离线模式网络回退。

## Task 1: 文件 API
Interfaces: GET/PUT `/workspace/projects/{id}/file?path=…`; 返回 content、revision、encoding、eol、read_only、reason，PUT 输入 content/revision。
1. 先写 pytest 覆盖 UTF-8/BOM/换行、冲突、边界、大小/二进制、Origin，确认新增路由测试失败。
2. 创建 workspace/files.py，添加轻量路由；原子替换、保存前重复校验。
3. 运行新增测试及目录树回归，修复至通过。

## Task 2: 本地 Monaco 资源
Interfaces: vendor/monaco/monaco.js、monaco.css、五个 Worker、manifest.json；workspace-monaco.js 初始化本地模块。
1. 构建完整语言集并保留许可证；输出 SHA-256 清单；验证资源完整及本地引用。
2. 更新 npm scripts/lock、package-data、sidecar 构建入口；wheel 与 sidecar 包含资源。
3. 离线资源验证与真实浏览器加载验证。

## Task 3: 编辑界面
Interfaces: createWorkspaceEditor(root,{api,error}) 提供 open/suspend/resume/dispose，消费 Task 1/2。
1. 测试多文件状态和保存期间编辑的行为。
2. 实现文件树旁固定面板、只读/编辑及 Ctrl+I、语言选择、保存/Ctrl+S、持久化默认编辑/自动保存、冲突和关闭确认；目录树和变更面板接入。
3. 浏览器验证真实 Worker、语言切换、保存、冲突、标签、退出保护。

## Task 4: 内网交付与 Windows 构建
Interfaces: offline dependency kit + Windows Electron 安装包，消费本地静态资源与现有工具链。
1. 离线资源/依赖校验测试先失败，再实现内网准备、严格离线构建脚本。
2. 构建并验证 sidecar、Electron 安装包；从依赖包执行离线重建。
3. 说明前置 Node/Python、同平台要求、构建命令、校验和、许可证。

## Task 5: 验证与提交
1. 全量 pytest、浏览器和安装包冒烟验证，记录结果。
2. 按 executing-plans 要求安排一名 fresh reviewer 检查最终变更，修复重要问题。
3. 恢复 Git 元数据、只提交功能文件，推送功能分支并创建/附加 PR；提供安装包、内网包和文档链接。
