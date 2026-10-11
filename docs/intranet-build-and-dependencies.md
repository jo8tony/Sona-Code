# 内网打包、依赖保留与 Git 提交

内网打包优先从内网 npm、pip 源安装锁定版本，源中缺少对应库或版本时才使用本地依赖包。源码仓库只提交源码、构建清单和必要的静态资源；下载的依赖、运行时、安装包放在 Git 之外。

目前 `scripts/build-offline-windows.ps1` 是**完整本地依赖包模式**，不会先访问内网源。下面提供内网源优先的手动流程，以及缺版本时切换到这个脚本的步骤。没有实现自动逐包切换来源。

## 1. 推荐目录布局

```text
D:\sona\                           # Git checkout，源码
D:\sona-deps\
  Sona-Code-Windows-x64-offline-build.zip
  Sona-Code-Windows-x64-offline-build.zip.sha256
  sona-offline-kit\                 # 从上述 zip 解压，依赖包
```

把依赖包放在源码目录之外，上传内网 Git 时无需携带它。也可以将 zip、SHA-256 文件和对应第三方源码包上传内网制品库或共享盘，构建时下载到本机。

本次交付的完整依赖包压缩后约 1018.4 MiB。当前 Git 跟踪文件合计约 18.9 MiB，源码 zip 约 7.2 MiB；工作目录中数 GB 的 `build/` 等目录不是已提交的源码。数字是这次交付的测量值，后续版本会变化。

## 2. 哪些依赖来自哪里

| 内容 | 版本依据 | 内网来源优先选择 | 本地备用位置（相对 `sona-offline-kit`） |
| --- | --- | --- | --- |
| Python 应用及打包库 | `requirements.txt`，每项固定 `==` 版本 | 内网 pip 源中的 Python 3.12 / Windows x64 wheel | `wheels/`，约 12.2 MiB |
| Electron Builder、Monaco、esbuild 等 npm 库 | 仓库根 `package-lock.json` | 内网 npm 源，使用 `npm ci` | `node_modules/`，包含 Electron 二进制，合计约 809.6 MiB |
| Electron / Chromium 运行时 | 根 `package.json`、锁文件，目前 Electron 44.4.5 | 内网二进制制品库，或本地备用包 | `node_modules/electron/dist/`，另有 `node_modules/electron/path.txt` |
| NSIS 安装包工具 | 已准备的 Electron Builder 缓存 | 内网二进制制品库，或本地备用包 | `electron-builder-cache/`，约 9.8 MiB |
| OpenCode 可执行程序 | `packaging/opencode.json`，含项目所需的 Windows 构建修正 | 同版内网构建制品，或本地备用包 | `binaries/opencode.exe`，约 173.2 MiB |
| OpenCode 插件 SDK | `packaging/opencode-sdk/package-lock.json` | npm 库可由内网源准备；打包需要校验后的 SDK bundle | `opencode-sdk/`，约 11.6 MiB |
| OpenSpec、Node 与命令启动器 | `packaging/openspec.json`、`packaging/openspec-package/package-lock.json`、Go 启动器源码 | 校验后的内网构建制品，或本地备用包 | `openspec/`，约 103.3 MiB |
| Java / Python / JS / JSX / TS / TSX 语言服务 | `packaging/language-services.json`、`packaging/language-services-package/package-lock.json` | 校验后的内网语言服务 bundle，或本地备用包 | `language-services/`，约 811.8 MiB，含 JDK、Node、服务、许可证和对应源码 |
| 构建时 Node 可执行程序 | 依赖包提供的构建工具；手动流程需本机 Node 22.x、npm 10.x | 内网安装的 Node，或备用工具 | `tools/node.exe`，约 81.8 MiB；该单文件不含 npm CLI |

表中大小是解压后的大小，不能与 zip 大小直接相加比较。语言服务 bundle 的 npm 部分包括 Pyright、TypeScript 和 TypeScript Language Server；其 JDK、JDT LS、Node 以及许可证和对应源码不会因 pip/npm 源存在而自动补齐。

**只有 npm、pip 源具备对应版本，并不足以删除整个离线依赖包。** Electron 的 npm 包与 Chromium 二进制也分开分发，详见 [Electron 安装说明](https://www.electronjs.org/docs/latest/tutorial/installation)。需要先把这些非 registry 组件迁入内网制品库，或者保留本地包作为它们的来源。

`pyproject.toml` 中部分依赖是最低版本范围。为了复现本次安装包，使用交付依赖包的 `requirements.txt`，不要直接以无锁定的升级安装替代它。将该小文件与 `manifest.json` 一起长期保存在依赖制品目录；缺少 wheel 时可依照清单让内网源补齐。

## 3. 优先内网 npm / pip 的手动打包流程

适用 Windows x64、Python 3.12 x64、Node 22.x 和 npm 10.x。使用新的、路径较短的源码 checkout；以下示例在 `D:\sona` 中执行。先保留完整本地包作为备用，完成一次内网构建和安装验证后再清理。

这是根据现有构建步骤整理的混合来源流程，尚未连接你的实际内网源验证。已经完成的无外网重建验收使用第 4 节的完整本地模式。

### 3.1 配置地址并校验备用包

打开新的 PowerShell 窗口，避免沿用完整离线脚本设置的禁止联网环境变量。把下面两个示例地址换成你实际的内网地址；URL 中不要写入账户密码或 token。

```powershell
Set-Location D:\sona
$ErrorActionPreference = 'Stop'
$KitDir = (Resolve-Path -LiteralPath 'D:\sona-deps\sona-offline-kit').Path
$NpmRegistry = 'https://npm.example.internal/repository/npm-group/'
$PipIndex = 'https://pip.example.internal/simple/'

python -c "import sys, struct; assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8"
if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.12 x64' }
python scripts\offline-kit.py --kit $KitDir
if ($LASTEXITCODE -ne 0) { throw '备用依赖包或源码版本不匹配' }
```

内网源认证使用本机的 npm/pip 配置。已有配置中的其他索引、scope registry 和外网下载地址需由内网管理员核对；企业 CA 通过本机证书配置处理。不要将含凭据的 `.npmrc`、`pip.ini` 或环境配置提交 Git。

### 3.2 先从内网源安装 Python 库

```powershell
python -m venv .venv-build
if ($LASTEXITCODE -ne 0) { throw '创建构建环境失败' }
$BuildPython = (Resolve-Path -LiteralPath '.venv-build\Scripts\python.exe').Path
& $BuildPython -m pip install --index-url $PipIndex --only-binary=:all: -r (Join-Path $KitDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw '内网 pip 安装失败：查看原因，缺版本时使用第 4 节的本地模式' }
& $BuildPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖不一致' }
```

先只使用内网索引；缺库/缺版本时再切换本地 wheels。不要把 `--find-links` 和索引一起使用来表达“索引优先”：pip 会同时搜索这些位置，并不保证该优先级。配置也不要额外指定外网索引。参考 [pip install 官方说明](https://pip.pypa.io/en/stable/cli/pip_install/)。

### 3.3 再从内网源安装根 npm 库

```powershell
npm ci --registry $NpmRegistry --replace-registry-host=always --include=dev --include=optional --ignore-scripts --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw '内网 npm 安装失败：查看原因，缺版本时使用第 4 节的本地模式' }
```

使用 `npm ci` 保持锁文件版本及完整性校验；它会先清理根 `node_modules/`，不要在正在运行的开发服务目录里执行。使用 `--include=optional` 安装 esbuild 等平台包；这里跳过安装脚本，Electron 二进制在下一步单独还原。本流程通过现有 Monaco 静态资源打包，不重新生成编辑器资源。参见 [npm ci 说明](https://docs.npmjs.com/cli/v10/commands/npm-ci)。

当前根锁文件含 `registry.npmjs.org` 和 `registry.npmmirror.com` 的下载地址。`--replace-registry-host=always` 将锁文件中的 registry 地址指向指定内网源，同时保留包版本及 integrity；仅传 `--registry` 可能仍访问另一个镜像地址。内网源返回的 tarball 地址也必须能在内网访问，scope registry 配置需与之相符。参见 [npm registry 地址替换配置](https://docs.npmjs.com/cli/v10/using-npm/config#replace-registry-host)。

两次安装都成功时，Python/npm 库来自内网源。若报错是认证、证书或连接配置问题，先修正再重试；不要把这些报错都解释为缺少版本。确认缺少锁定版本时，可以让内网源补库，或执行第 4 节的本地备用流程。

### 3.4 还原 npm/pip 无法提供的已校验组件

这一步仍使用备用包中的原生组件和已经准备好的服务 bundle。SDK、OpenSpec、语言服务的 npm 部分也随这些 bundle 保留；本手动流程不重建它们。如果以后迁入内网制品库，需提供同版、同布局、同许可证及源码的完整目录，并通过现有清单校验。

在新的 checkout 中运行以下命令；目标目录已存在时停止，防止重复复制出嵌套目录。无需删除任何已有目录来执行它。

```powershell
New-Item -ItemType Directory -Force -Path '.\build', '.\src-tauri\binaries' | Out-Null
$PreparedDirs = @(
    @('openspec', 'build\openspec'),
    @('opencode-sdk', 'build\opencode-sdk'),
    @('language-services', 'build\language-services'),
    @('electron-builder-cache', 'build\intranet-electron-builder-cache'),
    @('node_modules\electron\dist', 'node_modules\electron\dist')
)
foreach ($Pair in $PreparedDirs) {
    if (Test-Path -LiteralPath $Pair[1]) { throw "请使用新 checkout，目标已存在：$($Pair[1])" }
    Copy-Item -LiteralPath (Join-Path $KitDir $Pair[0]) -Destination $Pair[1] -Recurse
}
Copy-Item -LiteralPath (Join-Path $KitDir 'node_modules\electron\path.txt') -Destination 'node_modules\electron\path.txt'
Copy-Item -LiteralPath (Join-Path $KitDir 'binaries\opencode.exe') -Destination 'src-tauri\binaries\opencode-x86_64-pc-windows-msvc.exe'

node scripts\build-editor.mjs --check
if ($LASTEXITCODE -ne 0) { throw 'Monaco 资源校验失败' }
& $BuildPython scripts\prepare-native-sdk.py --check
if ($LASTEXITCODE -ne 0) { throw 'SDK 校验失败' }
& $BuildPython scripts\prepare-language-services.py --check
if ($LASTEXITCODE -ne 0) { throw '语言服务、许可证或对应源码校验失败' }
```

### 3.5 只用已准备好的组件生成安装包

```powershell
$env:ELECTRON_BUILDER_CACHE = (Resolve-Path -LiteralPath 'build\intranet-electron-builder-cache').Path
$env:CSC_IDENTITY_AUTO_DISCOVERY = 'false'
# 依赖安装已结束；打包阶段禁止从下载镜像补取组件。
$env:HTTP_PROXY = 'http://127.0.0.1:9'
$env:HTTPS_PROXY = 'http://127.0.0.1:9'
$env:ALL_PROXY = 'http://127.0.0.1:9'
$env:NO_PROXY = '127.0.0.1,localhost'
$env:npm_config_offline = 'true'
$env:PIP_NO_INDEX = '1'
$env:ELECTRON_SKIP_BINARY_DOWNLOAD = '1'
$env:ELECTRON_MIRROR = 'http://127.0.0.1:9/'
$env:ELECTRON_BUILDER_BINARIES_MIRROR = 'http://127.0.0.1:9/'

& $BuildPython -m PyInstaller --noconfirm --clean --distpath build\sidecar-intranet\dist --workpath build\sidecar-intranet\work packaging\sidecar.spec
if ($LASTEXITCODE -ne 0) { throw 'Python sidecar 构建失败' }
Copy-Item -LiteralPath 'build\sidecar-intranet\dist\sona-code-sidecar.exe' -Destination 'src-tauri\binaries\sona-code-sidecar-x86_64-pc-windows-msvc.exe' -Force

node node_modules\electron-builder\out\cli\cli.js --win nsis --x64 --publish never --config.electronDist=node_modules/electron/dist
if ($LASTEXITCODE -ne 0) { throw 'Electron 安装包构建失败' }
```

输出是 `dist/electron/Sona-Code-2.3.1-Setup.exe`，后续版本号由 `package.json` 决定。构建结束关闭这个 PowerShell 窗口，后续访问内网源使用新窗口。验证安装、AI 对话和六语言跳转后再删除备用库包。

此流程直接调用 PyInstaller 和 Electron Builder；`npm run desktop:build:windows:electron` 会执行通用准备器，其中有上游下载步骤，因此不能替代上面的内网构建命令。缺失 `sona-command-runner.exe`、语言服务或源码时应恢复对应 bundle，不能只补根 `node_modules/`。

## 4. 内网源缺少库/版本时，使用完整本地依赖包

在新的 PowerShell 窗口、独立源码副本中执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-offline-windows.ps1 -Kit D:\sona-deps\sona-offline-kit -Python python
```

该模式会重新安装所有本地 Python wheels、复制备用根 `node_modules/`，并还原原生组件。它按整个 bundle 回退，不逐包混装。所有文件及构建输入必须匹配清单；删掉包内 `wheels/`、`node_modules/` 或其他目录后，完整校验会失败，不能继续用这个脚本。

因此建议把完整 zip 保留在内网制品库/共享盘，在本机清理重复解压目录即可。业务代码变更通常可复用依赖包；20 项依赖输入指纹变化时，需要重新准备匹配版本的包。详细的外网准备方式见 [monaco-offline.md](monaco-offline.md)。

## 5. 哪些大目录可以删除，哪些必须保留

以下“可删除”是清理建议，不会自动执行删除。完成构建、保留所需安装包及依赖备份、停止使用该目录的开发进程后再清理。

| 仓库内路径 | Git 状态 | 清理条件及影响 |
| --- | --- | --- |
| `node_modules/` | 已忽略 | 打包后可删除；下次根 npm 库从内网 `npm ci` 重装，Electron 二进制仍需恢复 |
| `.venv/`、`.venv-build/`、`.venv-offline/` | 已忽略 | 可删除未使用的虚拟环境；内网 pip 版本齐全时重建，不影响源码 |
| `build/offline-kit*` | 已忽略 | 本地依赖包的多个解压副本，通常是占空间的大目录；保留外部完整 zip 后，可删除不用的重复副本 |
| `build/offline-source*`、`build/n7/`、`build/nav-final/` | 已忽略 | 重建源码副本及验收产物；所需安装包/验收记录已另存后可删除 |
| `build/sidecar*`、`build/bdist.win-amd64/`、`build/lib/` | 已忽略 | Python 中间产物；可以重新构建 |
| `build/language-services-cache/`、`build/openspec-cache/`、`build/opencode/`、`build/native-sdk-package/` | 已忽略 | 上游下载、解压或构建工作目录；保留最终校验 bundle 后可删除，重新准备上游组件时可能要恢复 |
| `build/navigation-build-fixtures/`、`build/navigation-smoke-*` | 已忽略 | 临时验收项目及测试缓存；没有进程使用后可删除 |
| `build/language-services/`、`build/openspec/`、`build/opencode-sdk/` | 已忽略 | 当前安装包的构建输入，打包前必须存在；打包后可清理，下一次从对应完整制品还原 |
| `build/intranet-electron-builder-cache/`、`build/offline-electron-builder-cache/` | 已忽略 | 构建工具缓存，下一次打包前从本地包或内网制品还原 |
| `src-tauri/target/`、`src-tauri/gen/`、`src-tauri/binaries/` 中生成的 exe | 已忽略，保留 `.gitkeep` | 可重新构建/恢复；Electron 打包前需要 sidecar 和固定 OpenCode exe |
| `dist/` | 已忽略 | 安装包、依赖 zip、源码 zip 和验收资料；先另存需要的交付文件，再清理不用的产物 |
| `.runtime/electron-builder-cache/`、`.runtime/offline-temp/` | 已忽略 | 确认仅为构建缓存且不再使用后可清理；**不要删除整个 `.runtime/`**，里面可能还有配置、数据库和调用记录 |
| `sona_code/web/static/vendor/monaco/` | 已提交 | 约 12.7 MiB 的离线静态资源，正常源码交付保留；删除会让编辑器/打包校验失败 |
| `packaging/`、`scripts/`、`third_party/`、`THIRD_PARTY_NOTICES.md`、各锁文件 | 已提交 | 构建规则、版本清单、启动器源码及许可证；正常源码交付保留 |

对于**外部依赖包内部**：内网 pip 的全部锁定 wheel 已验证可重新安装后，`wheels/` 不再是混合流程的必需来源；根 npm 库可重新安装后，其备用 `node_modules/` 可以不用，但必须先另外保留 `electron/dist/` 和 `path.txt`。SDK/OpenSpec/语言服务 bundle 中的依赖不能只因根 npm 安装成功就删除，最终应用仍使用它们。

若要真正移除整个本地依赖包，需同时满足：pip/npm 的固定版本及转依赖全部可重装；非 registry 组件已迁入内网制品库；清单、许可证和精确对应源码仍可恢复；已经从干净源码完成一次打包和安装验证。混合流程中的 `$KitDir` 也需改为从这些制品还原的完整资源目录。不要修改 manifest 来掩盖缺失资源。完整本地模式需要原包保持完整。

这些目录已经被 Git 忽略，**无需删除它们才能让源码仓库变小**。删除只减少本机磁盘用量，不会进一步缩小本来就未提交这些目录的 Git 仓库。

## 6. 上传内网 Git 的检查

保留根 `.gitignore`，不要使用 `git add -f` 添加外部依赖。提交前检查：

```powershell
git status --short
git diff --cached --stat
git check-ignore build/offline-kit-navigation/manifest.json node_modules/electron/dist/electron.exe dist/Sona-Code-Windows-x64-offline-build.zip
git ls-files build dist node_modules .runtime .venv .venv-build .venv-offline src-tauri/target
```

最后一条正常应无输出；它只检查已跟踪文件。`src-tauri/binaries/.gitkeep` 应保留，生成的 exe 不提交。若你自己的分支曾强行提交过大目录，新增 ignore 规则不会取消其跟踪，需要另行取消跟踪；历史中已有的大文件也不会因删除工作目录而消失。本次交付分支没有跟踪上面的大目录。

源码可用 `git archive` 导出，而不要压缩整个工作目录：

```powershell
git archive --format=zip --prefix=Sona-Code/ -o D:\sona-deps\Sona-Code-source.zip HEAD
```

它只包含已提交的文件，不包含下载依赖、虚拟环境或本机配置。你所编辑的业务项目需要的 Maven/Gradle、pip/npm 依赖属于各项目本身，也要由内网源或各项目的缓存准备，不属于 Sona Code 的应用依赖包。

## 7. GitHub 推送需要什么

目标仓库已经确定为 `jo8tony/Sona-Code`，当前功能分支为 `feat/monaco-offline-editor`。需要一个拥有该仓库写权限的 GitHub 账户，并在本机完成 Git 登录。当前本机配置的凭据助手是 Git Credential Manager，可在本机终端执行：

```powershell
git credential-manager github login --browser
```

在打开的浏览器中登录并完成授权，然后告知“本机 Git 已登录”即可继续推送。密码、token、私钥无需发到聊天中。GitHub 推荐使用凭据助手管理 HTTPS 登录，参见 [GitHub 官方登录说明](https://docs.github.com/en/get-started/git-basics/caching-your-github-credentials-in-git?platform=windows)；上面命令已通过本机 CLI 帮助核对。

GitHub 连接授权和本机 Git 凭据是两条独立途径，仅能读取仓库的连接不能写入，也不代表命令行 Git 已登录。若使用连接上传，连接所绑定账户也必须具有目标仓库写权限，并完成相应连接授权。优先使用本机 Git 登录，可一次性推送完整分支和已有提交历史。
