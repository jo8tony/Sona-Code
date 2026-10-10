# Sona-Code 代码文件预览与编辑可行性分析

分析日期：2026-10-10。结论：适合在现有工作区加入此功能，首版推荐 CodeMirror 6。当前需求的主要工作是文件读写、编辑状态管理和桌面资源打包，无需迁移整个前端框架。

## 1. 获取情况与分析边界

- 源码已下载到 `G:\workspace\sona-code`，使用 GitHub 官方 `main` 分支源码压缩包。
- Git 克隆多次遇到连接重置或连接超时，因此当前目录是源码快照，没有 `.git` 和提交历史。
- 对照 GitHub 递归文件树校验了全部 223 个文件的 Git blob SHA-1，文件数量、路径和内容全部一致。文件树标识为 `bd0b8194588b8aa36ce0bf8f249bdb2ab60496e1`。
- `pyproject.toml` 和 `package.json` 中项目版本均为 2.3.1。
- 本报告基于源码阅读、库的官方资料及下述临时资源测量。未修改应用代码、安装产品依赖、启动应用或执行桌面构建；性能和跨平台表现尚未通过原型验证。资源测量的工具依赖仅安装在忽略的 `.runtime/editor-benchmark/`。

## 2. 现有架构和可复用部分

后端是 Python / FastAPI；页面使用原生 JavaScript、CSS 和普通 script 标签。桌面使用 Tauri，Windows 另有 Electron 外壳。两种外壳使用同一套本地 Web UI 与 Python 服务。

| 现有能力 | 源码位置 | 对本功能的作用 |
| --- | --- | --- |
| 项目目录树、路径边界校验 | `sona_code/workspace/routes.py:184`、`:234` | 可复用项目定位与目录浏览；编辑写入需要更严格的文件检查 |
| 本地文件索引 | `sona_code/workspace/routes.py:202` | 不启动 OpenCode 即可索引项目文件 |
| 文件双击、右键菜单 | `sona_code/web/static/workspace-tree.js` | 可增加应用内预览/编辑入口 |
| 系统打开文件 | `sona_code/workspace/routes.py:261`、`sona_code/web/static/workspace.js:637` | 当前打开动作发送到后端，交给系统应用或外部浏览器 |
| AI 对话中的文件差异 | `sona_code/web/static/workspace.js:2429`、`sona_code/workspace/changes.py` | 可加入“打开当前文件”按钮，衔接人工查看和修改 |
| 配置编辑的内容哈希与原子写入 | `sona_code/admin/opencode_config.py:193`、`:320`、`tests/test_opencode_config.py` | 可借鉴 revision 和临时文件替换方式 |
| 静态资源服务 | `sona_code/app.py:305` | 浏览器、Tauri 和 Electron 共用一个编辑器实现 |

当前没有通用的项目文件内容读取、保存 API，也没有应用内代码编辑器。现有“文件改动”展示的是历史摘要或差异，不能直接当作磁盘上的最新完整文件。Markdown 代码块目前保留语言标签并显示文本，尚未接入通用语法高亮库。

## 3. 编辑与浏览库选型

| 库 | 预览 | 编辑 | 适用方向 | 接入考虑 |
| --- | --- | --- | --- | --- |
| **CodeMirror 6** | 只读编辑器 | 支持 | 在 AI 工作区内查看、修改代码 | 模块化；语言可按文件名匹配并动态加载；需增加小型资源打包步骤 |
| **Monaco Editor** | 只读编辑器、差异编辑器 | 支持 | 接近 VS Code 的开发界面 | 有文件模型和较完整的编辑交互；ESM 资源、语言 Worker 和生命周期配置更复杂 |
| **highlight.js** | 语法高亮 | 不提供编辑能力 | 消息中的代码块、简单只读预览 | 可自托管脚本，适合现有原生 JS 页面；不应把它与 textarea 拼接成完整编辑器 |
| **Shiki** | TextMate 语法和主题高亮 | 不提供编辑能力 | 对高亮效果要求较高的展示内容 | ESM、语言及主题按需加载；使用哪种正则引擎会影响资源组织 |

推荐判断：

1. 当前目标是“在应用内按语言预览、编辑并保存文件”，采用 **CodeMirror 6**。预览和编辑共用同一个组件，通过只读状态切换，避免两套显示逻辑。
2. 如果目标扩展为长期使用的开发环境，包含多文件语言服务、复杂补全和独立差异编辑界面，优先重新评估 **Monaco**。
3. 如果同时改善聊天代码块，可单独采用 **highlight.js**；首版文件编辑功能不必再叠加一个展示库。

两种编辑器都可以直接用于原生 JavaScript，无需引入 React 或 Vue。仓库原本要求前端保持无依赖；用户本次明确提出库选型，因此可以将必要依赖限制在编辑器模块内，其他页面继续按现有方式工作。

语法高亮与项目级智能提示是不同能力。不能因为加入 Monaco 或 CodeMirror，就承诺所有语言都有类型检查、跳转、格式化或跨文件补全。Python、Rust、Go 等的项目级能力需要进一步接入对应语言服务和运行环境。Monaco 也不会自动运行 VS Code 扩展。

资料核查中还发现：CodeMirror 仓库于 2026-04-15 迁移到维护者的 Forgejo，旧 GitHub 仓库归档属于迁移，不能据此判断项目停止维护。

## 4. 按代码类型识别语言

采用“特殊文件名 → 扩展名 → 有限的 shebang 判断 → 纯文本回退”的顺序，允许用户手动选择语言。首版优先接入项目中常见的语言，其他语言按需扩展。

| 文件示例 | 语言处理 |
| --- | --- |
| `.js`、`.mjs`、`.cjs`、`.jsx` | JavaScript；JSX 使用对应配置 |
| `.ts`、`.tsx` | TypeScript；TSX 同时启用 JSX |
| `.py`、`.rs`、`.go`、`.java`、`.c`、`.cpp` | 加载对应语言模块 |
| `.html`、`.css`、`.vue` | 对应 Web 语言模块；混合语言使用该模块的嵌套解析能力 |
| `.json`、`.jsonc`、`.yaml`、`.yml`、`.toml` | 配置文件语言；JSONC 需使用支持注释的处理方式，不能当严格 JSON 校验 |
| `.md`、`.sql`、`.sh` | Markdown、SQL、Shell 模式 |
| `Dockerfile`、`Makefile`、无扩展名脚本 | 按文件名或 shebang 匹配已接入的模式 |
| 未匹配或未安装对应语言模块的文本 | 纯文本，仍可预览和编辑 |

CodeMirror 的 `LanguageDescription.matchFilename()` 可匹配文件名和扩展名，`load()` 可动态加载语言支持。特殊文件补充规则可以维护在一个独立语言注册模块中。

## 5. 首版交互建议

- 在现有目录树增加“在应用内打开”，代码文件双击进入编辑面板；系统打开和文件管理器定位继续作为菜单动作。
- 编辑面板显示文件名、项目相对路径、语言、编码、换行格式和未保存标记。
- 支持只读预览与编辑切换、行号、高亮、撤销/重做、查找/替换，以及 `Ctrl+S` / `Cmd+S` 保存。
- 首版可支持少量文件标签页；状态以 `(project_id, relative_path)` 标识，避免不同项目同名文件串用内容。
- 关闭文件、切换项目或离开工作区前处理未保存内容；路由暂停/恢复保留必要状态，彻底销毁时释放编辑器资源。
- 在“文件改动”卡片增加“打开当前文件”，读取磁盘最新内容；历史差异仍使用对话记录，不混淆两者。

建议先完成纯代码文本预览。HTML/SVG 首版显示源码；若以后增加执行渲染，应独立设计隔离预览。Markdown 渲染预览、图片浏览、文件创建/删除、自动保存和完整 LSP 属于后续扩展。

## 6. 接入位置与接口草案

新增 `sona_code/workspace/files.py`，负责文本文件读取、语言/编码元信息和保存；在现有 `workspace/routes.py` 中加入薄接口层。直接操作已登记项目内的文件，不需要启动 OpenCode，也不需要修改其持久化数据。

建议接口：

```text
GET /workspace/projects/{project_id}/file?path=src/main.py
    → path, content, language, encoding, bom, newline, revision, writable

PUT /workspace/projects/{project_id}/file?path=src/main.py
    ← content, revision
    → new revision
```

`revision` 使用原始文件字节的内容哈希；编码、BOM 和换行格式由后端读取并控制保存，避免由客户端任意覆盖文件属性。错误区分路径非法、文件不存在、不可读/写、二进制文件、超限文件和内容冲突。

新增 `sona_code/web/static/workspace-editor.js`，集中管理编辑器挂载、文件切换、未保存状态、保存和释放；语言注册可放入独立模块。现有 `workspace.js` 约 254 KB，不适合继续堆入全部编辑器逻辑，只保留入口和控制器调用。`workspace-tree.js` 修改菜单及双击回调，样式放入独立 CSS 或现有工作区样式。

## 7. 保存和打包需要处理的细节

**文件边界：** 复用已登记项目身份和相对路径校验；读取/写入只接受普通文件，拒绝路径逃逸和符号链接，覆盖 Windows junction/reparse point 的边界情况。现有 `_project_entry()` 会解析链接，写接口不能只复制当前 helper 而忽略链接检查。文件树目前隐藏点开头的条目；如需编辑 `.gitignore`、`.editorconfig`，应有明确的可见性规则。

**并发修改：** 打开时保存 revision；保存前重新读取并校验，发现 AI、外部编辑器或其他标签页已修改时返回 409，保留用户未保存内容，并提供重载或对比。应用内部按文件加锁，写入前再次校验，可降低冲突风险。内容哈希检查与原子替换本身不能完全消除不配合锁的外部进程在最后检查后写入的竞态，不能宣称绝对不会覆盖。

**落盘：** 在同目录创建临时文件，再原子替换，保留文件权限/可执行位。Windows 文件占用导致替换失败时保留编辑内容，返回可理解的错误。原子替换避免半写入文件，不等于并发版本控制。

**编码和换行：** 优先支持 UTF-8 与 UTF-8 BOM，并保留 LF/CRLF、末尾换行。无法确认的编码显示明确状态或只读，后续再加手动 GB18030 等编码选择；不进行静默转换。编辑器对换行的规范化要和保存规则配合，混合换行文件需明确支持范围。

**资源和大文件：** 为加载内容设置文件大小与长行阈值，超限降级预览或交给外部编辑器。开启多个文件时限制缓存，避免长期积累编辑状态。具体阈值应在实际桌面环境验证。

**静态资源打包：** 当前 `package.json` 主要用于桌面工具，没有前端构建步骤。建议仅增加编辑器资源的 esbuild/Rollup 构建，版本写入 lockfile，生成本地 bundle 和语言资源。运行时不依赖 CDN，以符合已有内网安装场景。

PyInstaller 的 `packaging/sidecar.spec` 会收集整个静态资源目录；Python 包的 `pyproject.toml` 当前只有 `web/static/*` 和 `web/static/vendor/*` 等模式，若引入更深层目录，需要同步扩充 package-data，并检查普通安装包与桌面产物中的实际资源。还要在 `THIRD_PARTY_NOTICES.md` 保留新增依赖许可。

文件写接口还应限制到预期的本地 UI 来源，避免任意网页借用本地服务写文件；该控制应在后端执行。

## 8. 工作量判断与验证范围

只读预览属于较小改动；加入可靠保存后，整体属于中等复杂度。主要成本来自并发修改、文件编码、页面切换状态和桌面资源分发。完整 IDE / LSP 属于独立的较大工作。

实施后至少验证：

1. 路径逃逸、符号链接/junction、文件权限、二进制与超限文件。
2. UTF-8/BOM、CRLF/LF、中文及空格路径、末尾换行与可执行位。
3. AI/外部修改后的 409 冲突、同文件并发保存、保存失败保留编辑内容。
4. 语言识别、查找替换、撤销、未保存状态、项目/路由切换及资源释放。
5. 普通浏览器、Windows Electron/Tauri、macOS Tauri 的加载、键盘和中文输入。
6. Python 包与 PyInstaller 产物内离线资源完整，原有工作区测试通过。

本次没有实施上述功能，因此没有这些功能测试或打包验证结果。

## 9. 补充：体积、内网部署和商用许可

### 本地资源测量

2026-10-10 从官方 npm registry 获取并固定 Monaco 0.57.0、CodeMirror 入口包 6.0.2、`@codemirror/language-data` 6.5.2 和 esbuild 0.28.2。其他依赖版本记录在临时测量目录的 lockfile。使用 minify、ESM、无 source map 的构建配置，统计输出中的 JS、CSS、字体和 Worker；1 MB = 1,000,000 字节。

| 配置 | 实际打包资源合计 | 各文件 gzip 合计 |
| --- | ---: | ---: |
| CodeMirror：basicSetup + 常用语言 | 0.66 MB | 0.23 MB |
| CodeMirror：basicSetup + language-data 整套语言资源 | 1.68 MB | 0.62 MB |
| Monaco：编辑功能 + 常用语言 + 五类 Worker | 13.43 MB | 3.05 MB |
| Monaco：默认整套语言与功能 + 五类 Worker | 14.55 MB | 3.31 MB |

常用语言配置包含 JavaScript、TypeScript、JSX/TSX、JSON、HTML、CSS、Python 和 Markdown。Monaco 包含 JS/TS、JSON、HTML、CSS 语言服务及编辑器 Worker，能力范围比 CodeMirror 的 basicSetup 加语言解析更广，因此这不是完全等能力的对照。

本次常用语言配置下，Monaco 的资源约为 CodeMirror 的 20 倍，绝对增量约 12.78 MB；整套语言配置下约为 8.7 倍，绝对增量约 12.87 MB。数值对应这次构建配置，不能视为所有集成方式的固定倍率。Monaco 的 TS Worker 单文件约 6.72 MB，是主要体积来源；不需要对应语言服务时可以评估裁剪。

gzip 列用于说明代码的可压缩程度，不是 NSIS、DMG 或 PyInstaller 的安装包增量。测量构建成功，但没有在浏览器运行这些入口，不能作为离线功能已经验证的证据。构建方法与原始数据保存在 `.runtime/editor-benchmark/measure.mjs` 和 `results.json`。

### 复杂度判断

文件读取、路径校验、冲突检测、编码保存和未保存提示，两者都需要实现。Monaco 的额外工作主要是语言 Worker 分配、资源 URL、CSS/字体、文件模型 URI，以及两种桌面外壳中的离线验证；CodeMirror 常见编辑配置通常无需语言 Worker。

按当前项目的首版功能范围，工程上粗估 Monaco 会比 CodeMirror 多约 1–2 个工作日，主要用于首次资源打包和桌面兼容验证。该估算基于源码与构建调查，不是实施耗时测量，也不包含项目级 LSP、全语言智能补全等后续范围。

### 内网与许可

两者均可作为本地静态资源随安装包分发，运行时无需访问公网。依赖获取和构建可以在联网构建机完成；如构建机也完全离线，应提前准备依赖包、构建工具及所需桌面运行时的离线镜像。

Monaco Worker、语言资源、CSS、字体全部使用本地 URL。若使用 JSON Schema 校验等功能，Schema 本地内嵌/分发，并禁用外部 Schema 获取。项目现有 FastAPI 在 `127.0.0.1` 上提供页面，适合承载 Worker 资源；仍需通过阻断公网的实际运行测试确认未遗漏外部请求。

Monaco 与 CodeMirror 自身均使用 MIT，允许商业使用、修改和闭源分发，需保留版权和许可声明。分发时同时保留实际使用依赖的许可与 notices；Monaco 的 `ThirdPartyNotices.txt` 包含 TypeScript 等组件的声明。此次测量依赖中的 DOMPurify 是 `MPL-2.0 OR Apache-2.0` 双重许可，可选择 Apache-2.0 并履行相应声明要求。语言服务器和另行引入的扩展应按其自身许可核查。

对当前需求，内网和商用条件均不排除 Monaco。仅做预览和少量编辑仍优先 CodeMirror；若重视类似 VS Code 的编辑体验与内置差异编辑器，Monaco 的体积和接入成本在桌面应用里通常可以接受。

## 官方资料

- [CodeMirror 系统指南](https://codemirror.net/docs/guide/)
- [CodeMirror 只读编辑器示例](https://codemirror.net/examples/readonly/)
- [CodeMirror API：LanguageDescription](https://codemirror.net/docs/ref/#language.LanguageDescription)
- [CodeMirror 仓库迁移公告](https://discuss.codemirror.net/t/codemirrors-migration-to-forgejo/9706)
- [Monaco 官方仓库与集成说明](https://github.com/microsoft/monaco-editor)
- [Monaco ESM 集成](https://github.com/microsoft/monaco-editor/blob/main/docs/integrate-esm.md)
- [Monaco MIT 许可](https://github.com/microsoft/monaco-editor/blob/main/LICENSE.txt)
- [Monaco 第三方许可声明](https://github.com/microsoft/monaco-editor/blob/main/ThirdPartyNotices.txt)
- [CodeMirror MIT 许可](https://github.com/codemirror/dev/blob/main/LICENSE)
- [highlight.js 官方用法](https://highlightjs.org/)
- [Shiki 官方介绍](https://shiki.style/guide/)
