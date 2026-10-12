# sonacode 功能迁入记录

本次将 sonacode `2c660e4` 的新增功能迁入 llm-proxy `bd0b819`，应用版本统一为 2.3.2。两个仓库没有共同的 Git 祖先；以 sonacode `e045b43` 之后的功能增量做三方内容合并，再核对应用代码与最新 sonacode 的差异，避免复制 Windows 分支对跨平台能力的删除。

## 功能范围

- 普通对话：独立应用目录、原生历史、聊天 Agent 与工具权限隔离。
- 多模态图片：按模型输入能力验证图片，支持应用与网站模型的模态信息。
- 文件预览：代码/文本、Markdown、图片与 PDF，离线高亮、查找、缩放、系统打开和文件引用。
- 消息发送：即时显示本地发送内容，按原生消息 ID 确认；支持运行中补充、顺序排队及发送失败反馈。
- 项目与子代理：关联对话、子代理状态与展开行为、目录显示、会话导航和模型选择优化。
- 技能页面：搜索、来源和导入交互优化；保留原生 V1 技能调用及权限检查。
- 本地头像、登录/订阅入口、停止任务、退出清理、可选无 OpenSpec 构建修复。

## 兼容处理

保留 llm-proxy 的 macOS PTY、登录 shell PATH、系统文件打开/定位、Tauri 与 Electron 两种桌面壳、原有 GitHub Actions 和固定的 Windows OpenCode 源码补丁构建。Windows sidecar 与 OpenCode 继续共用 `src-tauri/binaries`；增加的离线 Electron 配置复用锁定的 44.4.5 运行时。

为 Tauri 增加 `open_website` 命令，与 Electron preload 的新增命令保持一致，并复用现有 URL 校验。Python、npm、Tauri 版本统一为 2.3.2；npm/Rust 依赖版本及 OpenCode 1.18.32 清单保持不变。预览资源及其许可证纳入 wheel 与 sidecar。

Windows CI 在准备 sidecar 和命令启动器后运行全量测试，以便执行新增 ConPTY 进程归属测试；原生兼容性步骤增加补充消息、普通对话、无 OpenSpec 与任务生命周期覆盖。

## 验证结果

2026-10-12，macOS arm64：

- `.venv/bin/python -m pytest tests/ -q`：501 passed，25 skipped。
- 使用官方 OpenCode 1.18.32 macOS arm64 可执行文件运行模型、原生排队、普通对话、原生补充消息与无 OpenSpec 回退测试：22 passed。测试使用隔离数据目录和本地模拟模型。
- `cargo fmt --manifest-path src-tauri/Cargo.toml -- --check` 通过。
- `TAURI_CONFIG='{"bundle":{"externalBin":[],"resources":null}}' cargo check --locked --manifest-path src-tauri/Cargo.toml` 通过。只验证 Rust 代码；本地缺少完整桌面资源，用环境覆盖跳过资源检查，没有修改发布配置。
- wheel 构建与资源哈希检查通过：199 个预览资源、16 个头像、托管插件齐全。
- macOS arm64 PyInstaller sidecar 构建通过；启动后 `/meta` 为 2.3.2，HTTP 返回的 199 个预览资源与 16 个头像逐项匹配源码。
- 浏览器验证项目/普通对话切换、目录树、Markdown/图片/PDF 预览与返回对话，未发现控制台错误。截图位于不跟踪的 `build/merge-verification/`。

Windows 两种安装包与绿色版、ConPTY 原生行为及受影响 Windows 普通用户验证需在对应机器执行。本次源码迁入不是 Windows 发布验收，不更新 `packaging/opencode.json` 的发布验证记录。
