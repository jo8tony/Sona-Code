# Monaco 编辑器和内网打包

Java、Python、JS、JSX、TS、TSX 的本地项目跳转及 Ctrl＋左右键历史见 [code-navigation.md](code-navigation.md)。完整安装包和内网依赖包包括固定语言服务、JDK/Node、原始许可证及精确对应源码；首次打开无需联网获取这些组件。

代码目录树单击文件即可在对话区域预览，文件树始终可操作，点击其他文件可切换标签。Ctrl/Cmd+I 或“编辑/预览”按钮切换模式，Ctrl/Cmd+S 保存；关闭编辑区返回对话。菜单保留系统打开、文件管理器定位和对话引用。切换文件或设置页会保留未保存内容；关闭文件/面板及退出应用时提示未保存修改。

编辑区的“默认编辑”和“自动保存”是两个独立开关，设置保存在本机浏览器/桌面应用中。启用自动保存后，停止输入 1 秒写入磁盘；保存期间的新输入会在下一次保存处理。外部修改冲突或写入失败时暂停该文件的自动保存，保留草稿；重新加载合并或手动保存成功后恢复。首次使用默认预览、手动保存。

支持 2 MiB 内 UTF-8 文本，保留 UTF-8 BOM 与 LF/CRLF/CR。混合换行、超过 20000 字符的行、不可写文件只读；二进制和其他编码请用系统应用打开。语言按文件名/扩展名匹配，可手动选择。Monaco 提供内置 JSON/JS/TS/CSS/HTML 语言能力；Java、Python、JS、JSX、TS、TSX 另接本地项目语言服务，其他语言以语法高亮为主。

Java、Python、JS、JSX、TS、TSX 支持 Ctrl＋点击和 F12 项目级跳转，可以打开尚未加载的目标文件，Ctrl＋左右箭头回退和前进。语言服务读取项目已有配置、源码和本地依赖，不自动安装依赖。具体设置与限制见 [code-navigation.md](code-navigation.md)；Go 等其他语言尚未接入项目语言服务。

## 运行时离线

`sona_code/web/static/vendor/monaco/` 已包含构建资源，随源码、wheel、Python sidecar、桌面安装包分发。Monaco 0.57.0、所有内置语言、五个 Worker、CSS、字体及许可证全部从应用本地 HTTP 服务加载，无 CDN；JSON 的外部 schema 请求关闭。

Windows 推荐 Electron 安装包，附带 Chromium，不需要联网安装 WebView2。安装包位于 `dist/electron/Sona-Code-2.3.1-Setup.exe`。编辑器不依赖 OpenCode 或模型接口；AI 对话仍需要你配置可访问的内网模型服务。

Windows sidecar 内含固定版本的 OpenCode 插件 SDK；新隔离配置目录和当前项目已有的 `.opencode` 目录会从本地准备依赖，首次创建对话无需从 npm 下载这些目录的 SDK。已有 `package.json`、锁文件或 `node_modules` 不会被覆盖，也不会给普通项目创建 `.opencode`。祖先目录、用户 home 的 `.opencode`、用户自行安装的插件和显式覆盖的配置目录由用户管理依赖；内网 npm 需按 `packaging/opencode-sdk/package-lock.json` 的版本清单准备缺失包。

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

最后一条命令产生 `dist/Sona-Code-Windows-x64-offline-build.zip` 及 `.sha256`。依赖包包含本机已安装的 Node 包与 Electron runtime、Node 可执行程序、Python wheels、NSIS 构建缓存、固定版本的 OpenCode 插件 SDK、OpenCode/OpenSpec 工具，以及完整 `language-services` 运行时、许可证和对应源码。不会包含用户配置、密钥、调用记录或本机虚拟环境。

## 在内网从源码重新打包

带入源码 zip/checkout、上述依赖包以及 Python 3.12 x64 安装程序（若内网尚未安装 Python）。先核对 zip 的 SHA-256，再解压，假设依赖目录为 `D:\sona-offline-kit`：

```powershell
Get-FileHash .\Sona-Code-Windows-x64-offline-build.zip -Algorithm SHA256
Expand-Archive .\Sona-Code-Windows-x64-offline-build.zip -DestinationPath D:\
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-offline-windows.ps1 -Kit D:\sona-offline-kit -Python python
```

脚本验证依赖包全部 SHA-256 和源码依赖清单，再安装本地 wheels、构建新的 Python sidecar 和 Electron 安装包。Node、Electron、NSIS、Monaco、语言服务都使用包内资源，不需要 Rust、Bun、Go 或外网 npm/pip。语言准备器仅执行 `--check`。脚本会重新生成项目内 `node_modules`、`build/openspec`、`build/language-services` 和离线构建缓存，请在独立的源码副本中运行。缺失/损坏资源、对应源码缺失或依赖清单变更会停止，不会回退外网下载。

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
