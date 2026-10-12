# 内网不附带 OpenSpec 的构建

OpenSpec 离线资源可以省略。普通消息、模型调用与原生命令执行不依赖
`tools/openspec`。缺少 `sona-command-runner` 时，工作区仍加载状态事件插件，
但保留 OpenCode 原生 shell 和权限配置，不再覆盖命令执行器。
Windows 终端直接启动原始程序，并在启动后加入应用的进程 Job。

这种构建不能启用或执行内置 OpenSpec 工作流。命令停止使用 OpenCode
原生行为，缺少托管执行器时不具备按会话清理脱离父进程的后台命令能力；
工作区服务关闭时仍清理其进程范围。需要完整的命令管理能力时，应恢复
完整离线工具包。

## 构建步骤

使用更新后的源码重新生成 sidecar，再打包 Electron。只修改
`electron/verify-package.cjs` 或重新打包旧 sidecar 无法修复消息发送。

同步到内网的修复文件包括：

- `sona_code/workspace/commands.py`
- `sona_code/workspace/managed-shell.js`
- `sona_code/terminal/manager.py`
- `electron/verify-package.cjs`
- `scripts/build-sidecar.ps1`

如果使用 `scripts/build-sidecar.ps1`，添加 `-SkipOpenSpec`：

```powershell
.\scripts\build-sidecar.ps1 -SkipOpenSpec
```

该选项只跳过 OpenSpec 准备，OpenCode 的准备仍使用原来的流程。
`build/openspec` 必须不存在，避免将旧的、不完整的资源复制进包。
已有离线目录请先移动到不参与构建的位置；脚本不会删除它。

使用内网现有 PyInstaller 环境手动构建也可以：

```powershell
.\.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean `
  --distpath build\sidecar\dist --workpath build\sidecar\work packaging\sidecar.spec
Copy-Item build\sidecar\dist\sona-code-sidecar.exe `
  src-tauri\binaries\sona-code-sidecar-x86_64-pc-windows-msvc.exe -Force
& .\node_modules\.bin\electron-builder.cmd build `
  --config packaging/electron-builder-offline.yml --win `
  "-c.directories.output=dist/electron-no-openspec"
```

OpenCode 仍使用 `scripts/prepare-opencode.py` 中固定的源码与补丁构建流程，
不要替换为未经兼容性验证的同版本可执行文件。离线 Electron 配置复用
`node_modules/electron/dist`，版本与 `package.json` 一致（44.4.5），sidecar
与 OpenCode 均来自 `src-tauri/binaries`，供两种 Windows 桌面壳共用。
确保 `package.json` 的版本与后端版本一致。独立输出目录避免影响正在运行的实例。

使用本次更新后的 `verify-package.cjs`，无需另行修改：资源目录不存在或
为空时警告并允许打包；目录包含文件时，清单、完整性和哈希校验仍必须通过。
不要用捕获所有清单读取/解析错误后直接返回的方式跳过损坏资源。

## 验证

```powershell
python -m pytest tests/test_optional_managed_tools.py tests/test_packaging_tools.py -q
```

设置 `OPENCODE_TEST_BINARIES` 为现有 V1 可执行文件的完整路径后，上面的命令
也会运行缺少 OpenSpec 时的原生消息回复和命令执行验证。该验证使用本地模拟
模型服务及隔离配置，不需要真实模型凭据。

在目标内网机器退出旧客户端后运行新构建，选择模型并发送普通消息，再执行
一次文件查看或命令任务。日志应提示 `using native OpenCode shell`，消息不再
因缺少托管执行器而失败；OpenSpec 页面应显示资源不可用。
