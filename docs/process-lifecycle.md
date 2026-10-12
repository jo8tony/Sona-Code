# 退出与停止会话

Electron 桌面“退出”先请求后端停止任务，等自己启动的 sidecar 真正退出后再关闭窗口与托盘。等待期间重复退出共用同一次清理。退出失败保留窗口并提示重试；正常请求有 20 秒总期限，随后最多等 5 秒，再按自己持有的 ChildProcess 清理进程树。复用其它实例的后端不在退出时终止。窗口关闭、Alt+F4 仍隐藏到托盘。

后端进入退出状态后拒绝新 HTTP 请求、WebSocket 和进程启动，但允许经过认证的原生收尾事件写入执行记录。先暂停持久化队列，再并行取消项目中的活动会话，关闭项目服务、终端、托管命令及上游连接。HTTP 服务在任务清理后才停止，并在连接排空后等待录制落盘。一个项目失败不会跳过其他项目；失败项保留归属记录供重试。状态仅在成功清理后记为结束。

Windows 后端持有带 `KILL_ON_JOB_CLOSE` 的 Job Object，所有自己启动的后代继承它；后台被强杀时也会清理已脱离父子 PID 链的进程。工作区服务、终端和命令另外持有独立 Job。命令以挂起状态启动，加入 Job 后再恢复。ConPTY 使用带放行标记的启动器：Python 将启动器加入终端 Job 后才允许它运行用户 shell，避免启动脚本提前创建后台进程。桌面所有者消失时，后端监视器触发相同的退出清理。

V1 插件用 `config` 与 `shell.env` 钩子把原生命令交给小型 Go 桥接程序；真实 shell 仍由后端执行，保留原 shell 名称、参数、工作目录、环境、原生权限和输出处理。命令按项目与会话归属；原生命令结束后仍保留 Job，直到会话 idle 或停止才清理残留后台程序。停止一个会话先暂停它的队列，再调用 V1 `/abort` 并确认 idle。不能确认时保留该会话的停止门禁并返回失败，不杀共享项目服务，可以重试；其它会话继续运行。

应用配置旁的 `workspace-executions.json` 记录任务起止。异常退出留下的运行记录在下次启动时转为中断。历史接口只对匹配的中断执行补齐消息、思考和工具结束状态，界面显示“已停止”，保留已经成功完成的内容。OpenCode SQLite 始终保持只读，不写入或迁移原生数据库。

命令启动器源码在 `packaging/command-runner/main.go`。`scripts/prepare-openspec.py` 使用现有固定 Go 编译器生成 `build/openspec/bin/sona-command-runner.exe`，纳入工具 SHA-256 清单。PyInstaller 包含插件，Electron 包含启动器和退出模块，打包校验会拒绝缺失或不一致的资源。手动离线构建需先执行 `python scripts/prepare-openspec.py --offline`，再重建 sidecar 和桌面包；完整工具包需要启动器；可选无 OpenSpec 构建使用原生 shell 回退，限制见 `docs/optional-openspec.md`。应用各版本清单统一为 2.3.2，OpenCode 仍固定 1.18.32。

验证命令：

```powershell
python -m pytest tests/ -q
$env:OPENCODE_TEST_BINARIES = '<绝对路径>/opencode.exe'
python -m pytest tests/test_managed_lifecycle.py tests/test_exit_rebuild.py tests/test_shutdown_ui.py -q
```

原生测试使用隔离数据目录和本地模拟模型，检查命令子进程确实退出、会话隔离、停止后继续发送、正常结束的后台命令清理、终端启动时的进程归属、sidecar 强杀、桌面所有者退出和执行记录恢复。安装包尚需重建，并按 `docs/opencode-windows.md` 执行安装版与绿色版 smoke 验证；源码测试不能替代这些分发验证。

macOS 保留 Tauri 桌面壳、系统 PTY、登录 shell 的 PATH 查找与系统文件打开能力。托管命令在 macOS 使用独立进程组清理，包括组长已经退出的后台子进程。Tauri 继续通过自己的 sidecar 句柄与退出流程停止后端；Electron 的退出模块仅用于 Windows Electron 桌面壳。
