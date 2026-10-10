# 退出与任务取消

桌面托盘“退出”（Windows）和应用退出（macOS）先调用仅回环地址可访问、带每次启动随机令牌的退出接口。后端关闭新任务入口，暂停待发送队列，取消原生会话，清理工作区命令、终端及其子进程，然后关闭 HTTP 服务。桌面端只在正常清理失败或超时时强制终止自己启动的后台进程。窗口关闭仍保留原来的隐藏到后台行为。

Windows 后台进程进入带 `KILL_ON_JOB_CLOSE` 的 Job Object。工作区服务及每条命令另有独立 Job Object；命令在挂起状态创建，加入 Job 后才运行，因此后台子进程不会因为中间父进程退出而失去归属。桌面主进程消失时，后台的所有者监视器执行正常清理；后台自身被强制终止时，Windows 自动关闭 Job 句柄并终止其子进程。macOS 使用独立进程组并在取消时清理进程组，即使组长已经退出也执行清理；macOS 没有 Windows Job Object 的强制退出保证，需要原生验证。

V1 插件通过 `config` 和 `shell.env` 接入内置命令工具及用户 shell。插件替换 shell 可执行路径，保留原 shell 名称、参数、环境和工作目录，使原生参数解析、权限检查、输出限制和历史记录继续生效。Go 桥接程序只转发请求和输出；真实 shell 由后端按项目、会话管理。会话进入 idle 后会清理尚存的后台命令。停止会话不会永久关闭对话，可以重新发送任务。

`workspace-executions.json` 位于应用配置文件旁，仅记录任务起止信息。异常退出留下的运行记录在下次启动时转为中断。历史接口用这些记录补齐未结束的助手消息和工具状态，界面显示“已停止”；不会写入、迁移 OpenCode SQLite 数据库。

命令桥接程序源码位于 `packaging/command-runner/main.go`。现有 `scripts/prepare-openspec.py` 使用相同的固定 Go 编译器编译它，并将其加入桌面资源和校验清单。桌面构建流程自动执行此步骤；从源码运行后端时需要先执行该准备脚本。终端和工作区不会在桥接程序缺失时降级为不托管的命令执行。

验证：

```powershell
.venv/Scripts/python.exe -m pytest tests/ -q
$env:OPENCODE_TEST_BINARIES = (Resolve-Path src-tauri/binaries/opencode-x86_64-pc-windows-msvc.exe).Path
.venv/Scripts/python.exe -m pytest tests/test_managed_lifecycle.py -q
```

原生回归包含后台子进程、同项目会话隔离、AI 命令工具取消、停止后继续对话、后台命令正常结束时的清理，以及后台正常退出、强制退出、桌面所有者退出。发布前仍需执行两种 Windows 安装包 smoke 测试和 macOS 原生退出验证。
