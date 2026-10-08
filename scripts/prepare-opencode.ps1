$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$BuildPython = if ($env:BUILD_PYTHON) {
    $env:BUILD_PYTHON
} elseif (Test-Path (Join-Path $ProjectDir ".venv-build\Scripts\python.exe")) {
    Join-Path $ProjectDir ".venv-build\Scripts\python.exe"
} else {
    "python"
}

& $BuildPython (Join-Path $PSScriptRoot "prepare-opencode.py")
if ($LASTEXITCODE -ne 0) {
    throw "Preparing patched bundled OpenCode failed with exit code $LASTEXITCODE"
}
