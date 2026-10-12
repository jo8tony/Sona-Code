# 项目级代码跳转与位置历史设计

日期：2026-10-11

状态：概念方案已确认；本文供用户审阅，产品代码尚未实现。

基线分支：`feat/monaco-offline-editor`，现有编辑器及 Test4 安装包。

## 1. 用户目标与交付范围

在现有文件树旁的 Monaco 编辑区中，按住 Ctrl 点击函数、方法、类或导入符号，打开定义所在的项目文件并定位。支持 Java、Python、JS、JSX、TS、TSX，包括尚未打开的目标文件。F12 使用同一套定义查询。

新增 Ctrl＋左箭头返回上一次代码位置，Ctrl＋右箭头前进到回退前的位置。历史保存项目、文件、光标/选区和滚动位置，而不是切换浏览器页面。预览和编辑模式均可使用跳转。

继续保留文件树切换、Ctrl＋I 切换编辑、Ctrl＋S 保存、默认编辑设置、自动保存、未保存草稿和保存冲突处理。语言分析独立于 AI 对话，未配置模型、未启动 OpenCode 会话也可使用。

交付仍包括 Windows x64 安装包、源码和可在内网重打包的依赖包。安装后首次打开代码不下载语言服务。用户已说明内网有 npm；离线包仍携带锁定版本，避免依赖内网镜像保留这些版本。

第一版覆盖项目内普通 UTF-8 文本文件的定义导航和位置历史。调试、运行、查找所有引用、重命名、LSP 格式化、完整 IDE 补全、Java 字节码反编译及项目外依赖源码预览不纳入本次交付。Monaco 原有基础功能继续使用。

## 2. 已有实现与需要扩展的边界

`sona_code/web/static/workspace-editor.js` 管理最多 12 个文件标签、草稿、保存和 Monaco opener。目前 model 使用 `inmemory://<project-id>/<relative-path>`，opener 只能打开已有 model。新实现保留这套 URI，扩展 opener 使其能加载未打开的项目文件。

`workspace-monaco.js` 已从本地加载 Monaco 0.57.0、语言 worker、样式和字体。新增一个独立的导航客户端，负责定义 provider、缓冲区同步、服务状态及快捷键；位置历史另设一个小模块，避免把语言协议逻辑塞进保存逻辑。

`sona_code/workspace/files.py` 和现有 GET/PUT 文件接口继续承担磁盘读写、UTF-8/BOM/换行处理、2 MiB 限制、revision 冲突、Windows DACL 及路径校验。语言服务收到的编辑缓冲区只存在内存中，定义查询不调用保存接口。

后端新增 `sona_code/language_services/`，包含固定组件解析、stdio JSON-RPC、会话管理、语言适配和路由。`RuntimeState` 负责创建和关闭管理器，进程归属复用 `sona_code/processes.py` 的 `ProcessScope` / Windows Job。OpenCode API、代理和历史数据库无需调整。

## 3. 选型与固定版本

采用 Monaco 定义 provider → 本应用 WebSocket → Python 语言服务管理器 → 本地 stdio LSP 的结构。浏览器不直接连接任意语言服务，不引入完整 VS Code 工作台，也不安装 VS Code 扩展。

| 文件类型 | LSP 文档语言 ID | 本地服务 | 运行时 |
| --- | --- | --- | --- |
| `.java` | `java` | Eclipse JDT LS 1.61.0 | Temurin JDK 21.0.12.1+1 |
| `.py`、`.pyi` | `python` | Pyright 1.1.414 | Node.js 22.23.3 |
| `.js`、`.mjs`、`.cjs` | `javascript` | typescript-language-server 6.0.2 + TypeScript 6.0.3 | Node.js 22.23.3 |
| `.jsx` | `javascriptreact` | 同上，共用 JS/TS 服务 | 同上 |
| `.ts`、`.mts`、`.cts` | `typescript` | 同上 | 同上 |
| `.tsx` | `typescriptreact` | 同上，共用 JS/TS 服务 | 同上 |

这些是本设计选定的首次验证版本，已核对官方发布元数据，尚未完成本项目的兼容性验证。实现时写入提交到仓库的精确 manifest 和 npm lockfile；若真实验证发现不兼容，改用明确的精确版本并记录原因，不在构建时自动追随 latest。

JDT LS 使用固定里程碑文件 `jdt-language-server-1.61.0-202609031315.tar.gz`，不使用移动的 snapshot/latest 地址。其运行要求至少 Java 21。Node 22.23.3 复用现有离线组件中的版本，满足所选 TypeScript 服务的 Node ≥22.22.2 要求；语言服务不会自动选择项目中的另一个 tsserver。依据：[JDT LS 1.61.0 文档](https://raw.githubusercontent.com/eclipse-jdtls/eclipse.jdt.ls/v1.61.0/README.md)、[固定里程碑目录](https://download.eclipse.org/justj/?file=jdtls/milestones/1.61.0)、[TypeScript 服务版本及运行要求](https://raw.githubusercontent.com/typescript-language-server/typescript-language-server/master/package.json)、[Pyright 包信息](https://raw.githubusercontent.com/microsoft/pyright/main/packages/pyright/package.json)。

## 4. 项目分析方式

### Java

JDT LS 使用项目根 URI、项目源码目录和本地库进行分析。为每个服务实例分配独立的 Eclipse 配置副本和 `-data` 目录，均放在应用自己的缓存目录。服务 JDK 使用内置 21；项目若要求其他 JDK，可在项目分析设置中指定已有 JDK 路径，不修改系统 JAVA_HOME。

默认关闭 Maven/Gradle 导入及注解处理，使用源码模式。自动识别普通源码根、`src/main/java`、`src/test/java` 和多模块下的这些目录；允许在应用的项目分析设置中补充源码根和项目内 `lib` JAR 路径。源码模式不承诺还原 Maven profile、Gradle 自定义 source set 或注解处理器生成的方法。

项目设置提供“导入 Maven/Gradle 构建配置”选项，默认关闭。用户启用后，服务以离线模式导入，读取用户预先准备好的 Maven/Gradle 缓存及本地 Gradle；不自动下载 wrapper、构建工具、依赖或源码附件。Gradle 导入需要评估构建脚本；这一点写在选项说明中。缺少缓存/本地工具时显示导入失败，并允许切回源码模式。

关闭自动构建和注解处理；服务所需的内部源码解析和索引仍运行。具体配置能力以锁定的 JDT LS 验证结果为准，不能以发送了配置就宣称已禁止下载或导入。相关配置依据：[官方 Java 客户端配置](https://raw.githubusercontent.com/redhat-developer/vscode-java/master/package.json)。

### Python

Pyright 读取项目已有 `pyrightconfig.json` 或 `pyproject.toml` 中的分析配置，并分析尚未打开的源码。应用设置可指定本地 Python 解释器/虚拟环境；不执行安装依赖操作，不改写项目配置。Python 运行时由用户已有环境提供，语言服务本身运行在内置 Node 上。

静态可解析的导入、函数、类和类型成员可跳转；运行时 monkey patch、动态导入及缺少第三方依赖的情况可能无法解析。找不到定义时显示原因类别，不能用同名文本搜索冒充语义跳转。环境配置依据：[Pyright 配置文档](https://github.com/microsoft/pyright/blob/main/docs/configuration.md)。

### JS、JSX、TS、TSX

同一服务分析四种语言，读取项目已有 `tsconfig.json` / `jsconfig.json`、路径别名、项目引用和已安装的依赖。没有配置文件时使用服务的 inferred project，仍验证本地 import 的跨文件跳转。

服务使用随包固定的 TypeScript；禁用自动类型包获取，不执行 npm install，不注入或修改 tsconfig。第三方类型依赖由项目在内网自行准备。JSX/TSX 的 React 组件、import alias 和 TS 类型定义列入真实验证范围。服务启动和定义能力依据：[TypeScript Language Server 文档](https://github.com/typescript-language-server/typescript-language-server/blob/master/README.md)。

## 5. 浏览器与后端接口

所有接口挂在现有 `{admin_prefix}/api` 下，沿用已注册项目 ID，不能由浏览器传任意项目根、可执行文件、命令参数或 LSP 方法。

| 接口 | 职责 |
| --- | --- |
| `GET /workspace/languages` | 返回三组组件的版本、完整性和可用状态，不启动进程 |
| `GET/PUT /workspace/projects/{project_id}/analysis-settings` | 读取/保存应用内的项目分析设置，不改写项目源文件 |
| `WS /workspace/projects/{project_id}/code-navigation/{family}` | `family` 仅允许 `java`、`python`、`typescript`，创建属于此连接的语言会话 |

WebSocket 使用小型应用协议，而非透传任意 JSON-RPC。支持的客户端消息为：`open`、`change`、`close`、`saved`、`definition`、`cancel`、`reanalyze`、`ping`。服务端回复 `status`、`ack`、`definition-result`、`error`、`pong`。每条消息携带递增 request ID，文档消息携带相对路径、语言 ID 和单调递增版本；定义请求携带其依赖的文档版本和位置。

例如定义请求使用 `{type:"definition", id:17, path:"src/main.ts", version:8, position:{line:9, character:14}}`。位置为零基 UTF-16；结果统一为项目相对路径、目标范围及可选原始符号范围，不把未经验证的本机路径交给浏览器。

同步完成的 `ack` 表示对应通知已按顺序写入 LSP，并非索引已完成。定义请求在同一队列中排在此前 change 之后；索引中的响应仍可能为空，此时状态说明服务正在分析，可稍后重试。

## 6. 草稿同步与定义导航

1. 打开支持的文件时按需建立其项目/语言组连接。后端 `initialize` / `initialized` 完成后，发送该组所有已有 model 的 `didOpen`，包括未保存内容。
2. 编辑变化合并 150 ms 后发送完整文本替换，按服务的同步能力转换成合法 `didChange`。点击定义前立即刷新该组所有待发送变化，再发送查询。同步与保存完全独立。
3. 浏览器将 Monaco 一基位置转换成 LSP 零基 UTF-16；后端验证位置边界。CRLF、中文、非 BMP 字符和 Windows 大小写路径不能导致错误列或重复标签。
4. 后端请求 `textDocument/definition`，接受 `Location` 或 `LocationLink`，验证 URI、范围、项目边界和文件可读性后归一化。标准定义能返回项目源码时直接使用；TS 类型声明优先由服务结果决定，不额外承诺“总能跳到运行时实现”。
5. 单个目标使用扩展后的 opener 加载或复用标签，定位并居中。多个目标显示轻量候选菜单（项目相对路径、行号，支持键盘选择），选定目标后才加载文件、打开标签和写入位置历史。候选列表不依赖尚未创建的 Monaco model。
6. 目标已有 model 时复用草稿，绝不为了跳转重新读取磁盘覆盖草稿。目标未打开时沿用文件 GET 接口。12 标签已满时提示先关闭标签，不自动丢弃任何文件。

Monaco 的 model URI 与磁盘 file URI 由后端按注册项目根转换；浏览器仅持有项目相对路径。JS/TS 对此应用 model 的定义查询统一使用新增 provider，关闭内置 TS/JS 定义 provider，保留其语法、基础补全等能力，防止同时返回两套定义。

Ctrl 点击和 F12 由应用导航动作统一接管，调用同一个查询客户端并处理候选菜单，不触发依赖目标 model 已存在的内置多结果 Peek。provider 用于标准定义位置和 Ctrl 链接提示；悬停查询不显示候选菜单、不打开文件、不追加历史。交互动作与 provider 共用查询结果缓存，缓存以会话 generation 和文档版本失效。

源 model 改变版本、切换项目、服务 generation 变化或查询被取消后，忽略旧响应。Ctrl 点击的跳转意图还要检查当前文件和光标；不能由晚到的响应把用户从另一个文件拉回。取消会传播为 LSP `$/cancelRequest`，无法及时取消的结果同样丢弃。

只有项目内经过现有文件安全规则验证的普通文件可作为第一版目标。项目内实际存在的 `.d.ts` / `.pyi` 可打开；项目外文件、符号链接/重解析点、`jdt://` / JAR 虚拟源及非 UTF-8 文件显示“此定义暂不支持在应用内打开”。不启动外部 URL 或向现有 PUT 接口扩大写入范围。

## 7. Ctrl＋左右箭头与位置历史

在代码编辑器具有文本焦点、没有输入法组合输入时，Ctrl＋左箭头执行后退，Ctrl＋右箭头执行前进。拦截 Monaco 原有 Ctrl＋左右键按单词移动行为；聊天输入框、设置输入框、终端和浏览器页面不受影响。添加对应的后退/前进按钮、可访问标签、禁用状态和快捷键说明。

每个编辑器实例保存最多 100 条位置，包含 `{projectId, path, selection, viewState}`。成功的定义跳转、文件树打开其他文件和点击不同标签都记录离开时与到达时的位置；同文件同位置不重复记录。普通光标移动、编辑、保存、格式化、服务重启不新建历史条目。

例如 `A.java:20 → B.java:50 → C.java:10`，连续后退到 B、A，再前进恢复 B、C。在 B 上进行一次新的文件/定义导航会清除 B 后的前进分支。历史导航本身不再次追加条目；离开当前位置时更新选区和滚动快照。

后退到已有标签直接复用草稿；已关闭标签通过文件接口重新打开。不会自动保存、撤销或丢弃代码。文件仍存在时将越界行列夹到当前文本范围；项目已删除或目标文件已删除时跳过失效条目并提示。其他加载失败或标签已满时保持原历史索引，以便重试。

历史允许跨已注册项目，但编辑器标签明确显示目标项目；不能绕过项目注册校验。激活目标时由编辑器回调把文件树定位到目标项目和文件，不发送或创建 AI 会话。刷新页面或关闭编辑器后清空历史，不持久化源代码或历史缓存到 localStorage。

## 8. 会话生命周期与资源限制

会话按“浏览器编辑器实例＋项目＋语言组”隔离。同一项目在两个窗口中编辑时使用独立服务，防止不同未保存缓冲区互相覆盖。JS/JSX/TS/TSX 共享一组，Java/Python 各一组。

状态为 `starting`、`indexing`、`ready`、`unavailable`、`failed`、`stopping`。状态显示在现有编辑器状态栏；正常编辑和保存不等待分析。组件缺失/校验失败时指出组件及修复包，服务崩溃时提供“重新分析”，不循环启动失败进程。连接重建会增加服务 generation，重新同步所有仍打开的文档；不能复用旧 request ID 对应的查询结果。

初始化上限：Node 服务 45 秒，Java 120 秒。初始化完成后的定义请求上限 15 秒；大型项目索引可以继续，并显示进度。取消不阻塞输入。每连接最多 8 个待处理定义请求，超过限制合并或拒绝；应用级同时最多 6 个服务实例，满额提示正在使用的服务数量。

连接保持期间每 20 秒发送心跳，60 秒收不到心跳即回收。没有任何活动文件使用该语言组 3 分钟后关闭连接和进程；返回该文件时重新发送当前 model 快照。后台窗口只要仍在查看该文件便不因失去焦点而关闭。JDT 的磁盘索引可复用，缓存键包含项目 ID、编辑器实例 ID 和组件版本；重连须在旧进程结束后取得同一缓存的互斥锁，同一缓存目录不得被两个进程同时使用。

Java 堆上限 1 GiB，Node 各服务启动设置堆上限 512 MiB；这不是进程总内存保证，交付时记录实际占用。单文档同步按 UTF-8 编码后不超过 2 MiB，WebSocket 单消息上限 4 MiB，LSP frame 上限 16 MiB；超过上限暂停该文件的语义分析，其他文件和编辑/保存错误提示继续工作。

连接断开、项目删除、退出应用时先尝试 LSP `shutdown` / `exit`，3 秒内未结束则关闭所属 ProcessScope，回收自己启动的后代进程。应用关闭流程必须调用此管理器，不按映像名称终止用户独立的 Java/Node/IDE 进程。

## 9. LSP 服务端请求与文件变化

stdio 使用 Content-Length 字节帧，支持分片读、合并读、UTF-8 与乱序响应；stdout 只用于协议，stderr 持续排空并保存有界错误摘要。不得在调试日志记录完整源码、进程环境或模型凭据。

处理本次服务需要的 `workspace/configuration`、`workspace/workspaceFolders`、受限 `client/registerCapability` / `unregisterCapability`、进度和消息通知。客户端只声明已实现的能力。拒绝 `workspace/applyEdit` 和未允许的 executeCommand，不让定义查询隐式改写源码。

服务自己的文件监听继续使用；桥接对需要的项目源码和配置变更发送 `workspace/didChangeWatchedFiles`。应用内保存立即发送 `didSave` 及文件变化通知；外部磁盘修改采用活动项目的有界轮询，2 秒一轮，排除 `.git`、依赖缓存和编译输出。每组最多跟踪 20,000 个匹配文件，超出时提示并保留保存通知与手动重新分析。

磁盘变更不会覆盖已有 model 草稿；目标文件再次从磁盘读取仍遵守原编辑器的重新加载和 revision 规则。分析设置或关键项目配置改变时，“重新分析”关闭旧进程、重建服务并重新同步草稿。

## 10. 配置、路径和状态隔离

项目分析设置保存在应用自己的数据目录，以项目 ID 区分；Java 源码根/库路径只接受项目内有效目录或文件，解释器、项目 JDK、已有 Gradle 路径通过专用字段验证。不能把设置当成 shell 字符串执行。

WebSocket 和设置写入校验同源 Origin，复用现有应用的访问约束。项目 ID 必须来自已注册列表；项目根变化后旧连接失效。文档路径拒绝绝对路径、`..`、混合分隔符、NTFS ADS、`.git`、符号链接和 Windows 重解析点，复用/提取现有文件校验而不是另写宽松规则。

服务用参数数组、`shell=False` 和绝对组件路径启动，环境只包含系统启动必需变量、组件运行时和明确的分析配置，不继承 Sona 的上游 API 密钥。项目可有空格和中文路径。临时配置、Eclipse 状态与服务日志放在应用的 `language-services` 缓存，不写真实 OpenCode 配置、AI 记录或原有历史数据库。

## 11. 离线打包和商业分发

第一版 Windows 安装包内置三组服务、Node 和 Temurin JDK，不把 Java 另做一个必须在线安装的插件。按需启动降低平时的资源使用；包体增加在完成真实构建后报告实测数值，本设计不虚报最终压缩体积。

新增精确的 `packaging/language-services.json` 和 Node 服务 npm lockfile，准备脚本下载/组装固定产物并生成 SHA-256 清单。JDK 固定官方 `jdk-21.0.12.1+1` Windows x64 ZIP，官方 API 公布 SHA-256 为 `f9d6e191ab098c0d416e7d588a24420a8621cd2f4720dab2459b8b7b2d2d8b4e`；JDT LS 校验其固定版本的官方摘要。Node 依赖使用 lockfile integrity，再校验交付文件清单。[Temurin 固定发布页](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.12.1%2B1)。

安装包及离线重打包目录同时包含组件产物、精确版本表、许可证和完整性清单。构建和 `afterPack` 校验缺失/篡改的启动入口、运行时、JAR 和 Node 依赖；缺失任一必需组件即构建失败，不生成一个仅 UI 可见但服务不可用的包。外网准备阶段按固定清单下载；内网重打包仅用离线目录且缺件报错，不自动回退外网下载。

Monaco/Pyright 为 MIT，TypeScript 及其语言服务为 Apache-2.0，JDT LS 为 EPL-2.0，Temurin 为 GPLv2 with Classpath Exception。选型允许按相应条款用于商业分发，同时保留每个产物的原始 LICENSE、NOTICE、版权和附带第三方声明。依据：[Pyright 许可证](https://raw.githubusercontent.com/microsoft/pyright/main/LICENSE.txt)、[TypeScript 许可证](https://raw.githubusercontent.com/microsoft/TypeScript/v6.0.3/LICENSE.txt)、[TypeScript 服务包声明](https://raw.githubusercontent.com/typescript-language-server/typescript-language-server/master/package.json)、[JDT LS 许可证声明](https://raw.githubusercontent.com/eclipse-jdtls/eclipse.jdt.ls/v1.61.0/README.md)、[Temurin 许可说明](https://adoptium.net/docs/faq)。

JDT LS 和 Temurin 不修改上游二进制。为满足这些组件的源码分发义务，交付与安装包同版的第三方源码归档，包含对应 JDT/Eclipse 组件源码及 Temurin 完整对应源码和构建配方，写明版本、来源与哈希；不能只给一个在内网打不开的网页链接。更新 `THIRD_PARTY_NOTICES.md`，核对每个打包依赖的实际许可证。不能把“允许商用”写成“没有再分发义务”。

## 12. 验收条件

| 类别 | 必须提供的证据 |
| --- | --- |
| 六种语言 | 每种语言至少一个真实服务的跨未打开文件定义测试；Java 验证包/方法与继承，Python 验证 import alias/类成员，JS/TS 验证路径别名，JSX/TSX 验证组件引用 |
| 项目配置 | Java 源码模式、离线 Maven/Gradle 导入的成功和缺件失败；Python 虚拟环境；JS/TS 有/无配置及本地依赖 |
| 草稿 | 未保存修改参与分析，跳转复用目标草稿，查询等待期间继续输入、保存冲突、服务重启和两个窗口隔离 |
| 位置历史 | A→B→C 连续后退/前进、同文件不同定义、回退后新分支、跨项目、关闭标签后重开、目标删除、标签满额、滚动/选区恢复 |
| 快捷键 | 预览/编辑模式的 Ctrl 点击、F12、Ctrl＋左右键；聊天/设置/终端焦点不拦截；Ctrl＋I/S 和输入法组合输入正常 |
| Windows 与协议 | 中文/空格/大小写路径、CRLF、UTF-16 列位置、Location/LocationLink、多定义、取消/超时/乱序/分片消息、拒绝越界 URI 与重解析点 |
| 资源与退出 | 按需启动、空闲回收、崩溃后重启、关闭全部所属后代进程；不终止用户自己启动的 Java/Node；初始化时编辑仍响应 |
| 完全离线 | 屏蔽外网并使用已准备的项目依赖，首次安装运行六种语言的跳转；确认没有语言组件下载、npm 自动类型获取、Java 源码附件下载或 wrapper 下载 |
| 重打包 | 用干净源码归档和离线依赖目录完成构建，再测该产物的服务启动与跨文件跳转；缺失和篡改产物必须使构建失败 |
| 既有功能 | 运行相关文件/保存、进程、打包测试及完整常规 pytest；桌面普通用户测试记录当前产物的保存、退出和重新启动 |

模拟协议测试只能验证桥接边界，不能代替真实语言服务。交付文档记录各服务版本、安装包 SHA-256、压缩/解压体积、冷启动耗时及内存。新测试使用临时项目和独立应用配置，避免影响用户正在运行的安装版和真实对话。

## 13. 交付判断与已知限制

只有六种语言的真实跨文件测试、位置历史及新安装包离线验证通过，才报告“项目级跳转已实现”。当前 Test4 仅作为原编辑器和打包修复的测试版本，不含本设计的新功能。

语言服务负责语义准确性；项目依赖缺失、动态 Python、Java 构建配置未导入以及超大型项目会影响结果。状态栏必须把不可用、索引中、超时和确实没有可打开定义区分开来。不能用文本搜索掩盖解析失败，不能把语言服务支持的全部 IDE 功能都宣称已接入。

实现后提交本地代码，按用户已有授权推送 `jo8tony/Sona-Code`。当前 GitHub 连接仍缺仓库写权限/原生推送网络不通；未成功推送前不能宣称代码已上传，源码归档与离线依赖包仍须交付。

## 14. 本文自审记录

已核对本文与现有编辑器 URI、12 标签限制、文件读写边界、应用进程归属及内网交付需求一致。位置历史纳入最新要求；Java 源码模式与构建导入、项目外定义及动态语言限制均已明确。版本列为已选验证基线，真实运行验证属于实现验收，不把只读元数据检查当成兼容性结论。本文没有待填章节；下一阶段先形成实现计划。
