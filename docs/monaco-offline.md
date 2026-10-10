# Monaco 编辑器和内网打包

代码目录树单击文件即可在对话区域预览，文件树始终可操作，点击其他文件可切换标签。Ctrl/Cmd+I 或“编辑/预览”按钮切换模式，Ctrl/Cmd+S 保存；关闭编辑区返回对话。菜单保留系统打开、文件管理器定位和对话引用。切换文件或设置页会保留未保存内容；关闭文件/面板及退出应用时提示未保存修改。

编辑区的“默认编辑”和“自动保存”是两个独立开关，设置保存在本机浏览器/桌面应用中。启用自动保存后，停止输入 1 秒写入磁盘；保存期间的新输入会在下一次保存处理。外部修改冲突或写入失败时暂停该文件的自动保存，保留草稿；重新加载合并或手动保存成功后恢复。首次使用默认预览、手动保存。

支持 2 MiB 内 UTF-8 文本，保留 UTF-8 BOM 与 LF/CRLF/CR。混合换行、超过 20000 字符的行、不可写文件只读；二进制和其他编码请用系统应用打开。语言按文件名/扩展名匹配，可手动选择。Monaco 内置 JSON/JS/TS/CSS/HTML 语言服务；其他语言以语法高亮为主，未安装语言服务器，不执行代码。

JavaScript/TypeScript 提供 F12 跳转定义、查看定义和查找引用；跨文件跳转可以切换到已打开的文件标签。分析范围是编辑器已加载的模型，不扫描整个项目或安装 npm 依赖。Python/Java/Go 的项目级跳转需要后续接入对应 LSP 服务。

## 运行时离线

`sona_code/web/static/vendor/monaco/` 已包含构建资源，随源码、wheel、Python sidecar、桌面安装包分发。Monaco 0.57.0、所有内置语言、五个 Worker、CSS、字体及许可证全部从应用本地 HTTP 服务加载，无 CDN；JSON 的外部 schema 请求关闭。

Windows 推荐 Electron 安装包，附带 Chromium，不需要联网安装 WebView2。安装包位于 `dist/electron/Sona-Code-2.3.1-Setup.exe`。编辑器不依赖 OpenCode 或模型接口；AI 对话仍需要你配置可访问的内网模型服务。

## 在外网准备一次

需要 Windows x64、Python 3.12 x64 和 Node.js 22+：

```powershell
python -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install ".[dev,desktop]" wheel
npm ci
npm run editor:check
$env:BUILD_PYTHON = (Resolve-Path .venv-build\Scripts\python.exe).Path
$env:ELECTRON_BUILDER_CACHE = Join-Path $PWD '.runtime\electron-builder-cache'
npm run desktop:build:windows:electron
.venv-build\Scripts\python.exe scripts\offline-kit.py --prepare
```

编辑器资源已提交源码，正常打包只校验资源，不再下载 Monaco。升级编辑器或重建资源时执行 `npm run editor:build`，提交新的资源与 `manifest.json`。

最后一条命令产生 `dist/Sona-Code-Windows-x64-offline-build.zip` 及 `.sha256`。依赖包包含本机已安装的 Node 包与 Electron runtime、Node 可执行程序、Python wheels、NSIS 构建缓存，以及已验证的固定版本 OpenCode/OpenSpec 工具。不会包含用户配置、密钥、调用记录或本机虚拟环境。

## 在内网从源码重新打包

带入源码 zip/checkout、上述依赖包以及 Python 3.12 x64 安装程序（若内网尚未安装 Python）。先核对 zip 的 SHA-256，再解压，假设依赖目录为 `D:\sona-offline-kit`：

```powershell
Get-FileHash .\Sona-Code-Windows-x64-offline-build.zip -Algorithm SHA256
Expand-Archive .\Sona-Code-Windows-x64-offline-build.zip -DestinationPath D:\
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-offline-windows.ps1 -Kit D:\sona-offline-kit -Python python
```

脚本验证依赖包全部 SHA-256 和源码依赖清单，再安装本地 wheels、构建新的 Python sidecar 和 Electron 安装包。Node、Electron、NSIS、Monaco 都使用包内资源，不需要 Rust、Bun、Go 或外网 npm/pip。脚本会重新生成项目内 `node_modules`、`build/openspec` 和离线构建缓存，请在独立的源码副本中运行。缺失/损坏资源或依赖清单变更会停止，不会回退外网下载。

修改业务代码后可以复用依赖包；修改 `package-lock.json`、`pyproject.toml` 或 OpenCode/OpenSpec 的清单、构建脚本与启动器时，需要重新在外网准备依赖包。当前依赖包仅适用 Windows x64 与相同 Python 次版本；macOS/ARM64 需在对应平台准备。

## 安装后提示命令启动器缺失

AI 对话需要安装目录内 `resources/tools/openspec/bin/sona-command-runner.exe`。如果提示“客户端缺少 AI 命令启动器”或旧版的 `Managed command runner missing`，先核对该文件及安全软件的隔离记录。安全软件隔离该文件时，需要完成安全复核，再使用资源完整的安装包修复；仅重启应用或停止防护不会补回已经缺失的文件。安装用户无需运行仓库里的 `scripts/prepare-openspec.py`。

Electron 打包结束时会检查 AI 命令启动器、OpenSpec 运行时及资源清单中的全部 SHA-256。文件缺失、清单遗漏启动器或内容损坏都会终止打包。该检查不能替代安装后安全软件的鉴定。

## 许可证与校验

Monaco 使用 MIT，允许商业使用，随发布保留 `LICENSE.txt` 和 `ThirdPartyNotices.txt`。上游第三方声明随静态资源分发。完整应用其他依赖的许可证按原有项目声明保留。

```powershell
npm run editor:check
python scripts\offline-kit.py --kit D:\sona-offline-kit
python -m pytest tests -q
```
