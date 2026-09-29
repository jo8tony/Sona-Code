; 自定义 NSIS 安装程序钩子
;
; 背景：在已安装旧版本的机器上覆盖安装时，旧版本遗留的进程仍然占用安装目录
;       里的可执行文件（Windows 下正在运行的 exe 无法被覆盖），安装程序会报
;       “error opening file for writing: ...sona-code-sidecar.exe”。
;
; 方案：在复制文件之前（NSIS_HOOK_PREINSTALL）先结束这些遗留进程，并等待文件
;       句柄释放，从而让新版本可以正常覆盖旧文件。
;
; 说明：
;   * 只结束本应用专属的进程名，不影响用户自行安装的 OpenCode 等其它软件；
;   * 使用 /T 一并结束后台子进程（例如 sidecar 启动的随包 opencode.exe）；
;   * 全部命令均为“尽力而为”，失败不会中断安装；静默安装（/S）下同样适用。

!macro sonacode_kill_leftover_processes
  DetailPrint "关闭旧版本遗留的进程..."
  nsExec::Exec 'taskkill /F /T /IM sona-code-sidecar.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /T /IM sona-code-desktop.exe'
  Pop $0
  Sleep 1000
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro sonacode_kill_leftover_processes
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro sonacode_kill_leftover_processes
!macroend
