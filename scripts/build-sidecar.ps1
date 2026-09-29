$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$BuildPython = if ($env:BUILD_PYTHON) {
    $env:BUILD_PYTHON
} elseif (Test-Path (Join-Path $ProjectDir ".venv-build\Scripts\python.exe")) {
    Join-Path $ProjectDir ".venv-build\Scripts\python.exe"
} else {
    "python"
}

$Architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
$TargetTriple = switch ($Architecture) {
    "X64" { "x86_64-pc-windows-msvc" }
    "Arm64" { "aarch64-pc-windows-msvc" }
    default { throw "Unsupported Windows architecture: $Architecture" }
}

$OutputDir = Join-Path $ProjectDir "build\sidecar"
$BinaryDir = Join-Path $ProjectDir "src-tauri\binaries"
$BinaryName = "sona-code-sidecar"

New-Item -ItemType Directory -Force -Path $OutputDir, $BinaryDir | Out-Null
& $BuildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath (Join-Path $OutputDir "dist") `
    --workpath (Join-Path $OutputDir "work") `
    (Join-Path $ProjectDir "packaging\sidecar.spec")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$Source = Join-Path $OutputDir "dist\$BinaryName.exe"
$Destination = Join-Path $BinaryDir "$BinaryName-$TargetTriple.exe"
Copy-Item -Force $Source $Destination
Write-Host "sidecar: $Destination"

& (Join-Path $PSScriptRoot "prepare-opencode.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Preparing bundled OpenCode failed with exit code $LASTEXITCODE"
}
