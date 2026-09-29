; Updating an installed Electron shell must stop the old main process before
; replacing its EXE. /T also ends a normally attached Python sidecar.
!macro customInit
  nsExec::Exec 'taskkill /F /T /IM "Sona Code Offline.exe"'
  Pop $0
!macroend
