# 本地代码跳转

项目目录树单击文件后，Monaco 在对话区域打开代码，目录树仍可操作。Java、Python、JS、JSX、TS、TSX 支持 Ctrl＋点击或 F12 查找定义，自动打开项目内目标文件并定位。多个候选用方向键选择，Enter 打开，Escape 取消。其他 Monaco 语言可高亮和编辑，暂未连接项目语言服务。

| 快捷键 | 功能 |
| --- | --- |
| Ctrl＋点击 / F12 | 跳到定义 |
| Ctrl＋左箭头 / 右箭头 | 返回上一处位置 / 前进 |
| Ctrl/Cmd＋I | 切换预览、编辑 |
| Ctrl/Cmd＋S | 保存 |
| Ctrl/Cmd＋F | 文件内查找 |

前进后退仅在代码文本获得焦点时接管快捷键，输入法组合输入和 Alt/Shift 组合不接管；这会替代编辑器原有 Ctrl＋左右键按单词移动。历史最多 100 条，保留选区和滚动位置。关闭标签后可通过历史重新打开；被删除的目标会跳过并提示。每个编辑器最多 12 个文件，满额时先关闭一个标签。切换文件、项目分析设置和应用设置页保留当前草稿。关闭编辑区清空本次位置历史；关闭含修改的文件会提示。

“默认编辑”和“自动保存”分别配置。启用自动保存后停止输入 1 秒保存，磁盘冲突或失败会暂停并提示。跳转复用已打开的目标草稿，也将未保存文本交给本地语言服务；每个编辑器独立分析，另一个窗口不会读到该草稿。分析断开或失败时仍可编辑、保存。

## 项目分析设置

编辑区“项目分析”保存到应用自己的数据目录，与模型服务无关。修改设置会重启当前项目分析；“重新分析”刷新项目索引，保留草稿。

Java 默认源码模式，自动发现普通源码根及多模块的 `src/main/java`、`src/test/java`，可以补充项目内源码根及已有 JAR。源码模式不还原 profile、自定义 source set 或注解处理器生成成员。服务自带 JDK 21；可为项目选择本机已有 JDK 8+，路径应包含有效 `release` 和 `bin/java.exe`。

Maven/Gradle 构建导入需主动启用。Maven 使用预先准备的本地 `.m2` 缓存；缓存应包含项目依赖、父 POM 和构建插件及其依赖。Gradle 还需选择已有本地 Gradle 目录，并准备对应缓存。Gradle 会执行项目构建配置脚本。两种导入均启用离线模式、禁用 wrapper 和源码附件下载、自动构建和注解处理；缺少资源时检查错误提示并切回源码模式。项目构建依赖不属于本应用依赖包，需按项目准备。

Python 可选择已有虚拟环境的 `Scripts/python.exe`；服务不会运行 pip。JS/JSX/TS/TSX 读取项目内 jsconfig/tsconfig、路径别名、项目引用和本地类型定义，使用固定 TypeScript 版本，禁用自动类型获取和项目自带服务插件。React 等项目依赖应事先准备。

只能跳转到项目内普通 UTF-8 文件。项目外依赖、JAR/虚拟源码、链接及重解析点不会打开。动态运行时成员、生成代码和缺少类型信息的引用可能没有定义。单文件上限 2 MiB；草稿超限时只暂停该文件分析，正常文件仍可查询，超限目标不会使用旧磁盘位置跳转。项目监听最多 20,000 个文件，过大的项目可手动重新分析。源码根发现最多扫描 20,000 个目录。初始化和分析较大项目需要等待。导入错误会保留到“重新分析”，普通进度结束不会清除失败提示。

## 固定组件与内网交付

| 组件 | 版本 |
| --- | --- |
| Monaco | 0.57.0 |
| Node.js | 22.23.3 |
| Temurin JDK | 21.0.12.1+1 |
| Eclipse JDT LS | 1.61.0 / 202609031315 |
| Pyright | 1.1.414 |
| TypeScript Language Server | 6.0.2 |
| TypeScript | 6.0.3 |

安装包 `resources/tools/language-services` 内含运行时、服务、完整性清单、原始许可证与对应源码。JDK 不嵌入 sidecar 的 onefile，打开编辑器无需下载或再次解压 JDK。应用通过 `SONACODE_BUNDLED_LANGUAGE_SERVICES` 指向该目录；开发默认使用 `build/language-services`。外网准备使用 `npm run languages:prepare`；内网构建仅运行 `--check`，缺件、篡改、源码缺失或 producer input 指纹不匹配会停止。

内网 npm 可用于其他开发需求；本次完整依赖包自带锁定资源，不要求内网仓库恰好保留相同版本。完整内网重建步骤见 [monaco-offline.md](monaco-offline.md)。修改语言版本、Node 锁文件、准备脚本或第三方声明后，应重新在外网准备依赖包。

Monaco/Pyright 为 MIT，TypeScript/TLS 为 Apache-2.0，JDT/Eclipse 和 Temurin 有 EPL/GPL 等许可证。允许商业使用不等于免除再分发义务；保留逐项许可证、版权声明和对应源码。`sources/manifest.json` 对应每个 Eclipse bundle/source JAR，以及精确的 Temurin、Equinox 原生源码和构建配方。安装包、内网包均携带这些文件；另提供同版第三方源码 zip。[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 列出具体许可与清单位置。

## 验收和测量

真实服务验收覆盖六语言未打开目标、同名模块/类、Java 继承、Python import alias/类成员/本地 venv、JS/TS CRLF 和 emoji UTF-16 列、JSX/TSX 组件、TS 路径别名与项目引用、目标草稿改变后的新行号及两个编辑器隔离。另使用固定本地 Gradle 8.14.3 和预备 Maven 插件缓存验证离线多模块导入，空 Maven 缓存明确报离线缺件。测试工具与项目缓存是验收环境，不要求安装用户使用它们。

```powershell
$env:SONACODE_TEST_LANGUAGE_NATIVE = '1'
python -m pytest tests/test_language_native.py -q
python scripts/smoke-code-navigation.py --bundle build/language-services --report build/navigation-report.json --offline
python scripts/smoke-code-navigation.py --bundle dist/electron/win-unpacked/resources/tools/language-services --package-resources dist/electron/win-unpacked/resources --report build/navigation-package-report.json --offline
```

未启用 opt-in 时常规 suite 跳过真实语言服务组；启用后组件缺失会失败。测试使用随机端口和独立配置，报告记录服务版本、定义 path/range、冷启动时间及 Windows 工作集，退出只清理所属进程。

本次开发机单服务组首次分析约 8～11 秒，Windows 工作集包含服务后代进程：Python 99 MiB、JS/TS 304～308 MiB、Java 802 MiB。包内真实 WebSocket 测试首次分析约 12～16 秒；其内存报告包含 sidecar 和两个编辑器服务实例，不能当作单实例数据。Node 堆上限 512 MiB、Java 堆上限 1 GiB，实际工作集会包含运行时和子进程。安装包/依赖包大小、SHA-256 和最终产物验收详见交付报告。

包内 sidecar/HTTP/WebSocket 测试与桌面生命周期验收分别报告。用户已安装的应用继续运行时，本次不覆盖安装目录、不清理真实对话；已安装桌面的保存、退出、重启仍需在用户允许关闭旧版后验收。
