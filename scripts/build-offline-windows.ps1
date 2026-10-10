param(
    [Parameter(Mandatory=$true)][string]$Kit,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$KitDir = (Resolve-Path -LiteralPath $Kit).Path
$Manifest = Get-Content -LiteralPath (Join-Path $KitDir "manifest.json") -Raw | ConvertFrom-Json
if ($Manifest.platform -ne "windows-x64") { throw "This kit targets Windows x64" }
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne "X64") { throw "Windows x64 required" }
$PythonVersion = & $Python -c 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor))'
if ($LASTEXITCODE -ne 0 -or $PythonVersion.Trim() -ne ($Manifest.python -join ".")) { throw "Use Python $($Manifest.python -join '.') x64" }
& $Python (Join-Path $PSScriptRoot "offline-kit.py") --kit $KitDir
if ($LASTEXITCODE -ne 0) { throw "Offline kit verification failed" }

# No command can fall back to package registries/download mirrors in this build.
$env:HTTP_PROXY = "http://127.0.0.1:9"
$env:HTTPS_PROXY = "http://127.0.0.1:9"
$env:ALL_PROXY = "http://127.0.0.1:9"
$env:NO_PROXY = "127.0.0.1,localhost"
$env:PIP_NO_INDEX = "1"
$env:npm_config_offline = "true"
$env:ELECTRON_MIRROR = "http://127.0.0.1:9/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR = "http://127.0.0.1:9/"
$env:ELECTRON_SKIP_BINARY_DOWNLOAD = "1"
$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
$TemporaryDir = Join-Path $ProjectDir ".runtime\offline-temp"
New-Item -ItemType Directory -Force -Path $TemporaryDir | Out-Null
$env:TEMP = $TemporaryDir
$env:TMP = $TemporaryDir
$BuildNode = Join-Path $KitDir "tools\node.exe"
$env:PATH = (Split-Path -Parent $BuildNode) + ";" + $env:PATH

function Copy-PreparedDirectory([string]$Source, [string]$Target) {
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Target)
    if (-not $ResolvedTarget.StartsWith($ProjectDir + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Build target escapes project: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) { Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force }
    Copy-Item -LiteralPath $Source -Destination $ResolvedTarget -Recurse -Force
}
Copy-PreparedDirectory (Join-Path $KitDir "node_modules") (Join-Path $ProjectDir "node_modules")
Copy-PreparedDirectory (Join-Path $KitDir "openspec") (Join-Path $ProjectDir "build\openspec")
Copy-PreparedDirectory (Join-Path $KitDir "electron-builder-cache") (Join-Path $ProjectDir "build\offline-electron-builder-cache")
$env:ELECTRON_BUILDER_CACHE = Join-Path $ProjectDir "build\offline-electron-builder-cache"
& $BuildNode (Join-Path $PSScriptRoot "build-editor.mjs") --check
if ($LASTEXITCODE -ne 0) { throw "Local Monaco assets are missing or corrupt" }

$EnvironmentDir = Join-Path $ProjectDir ".venv-offline"
& $Python -m venv $EnvironmentDir
if ($LASTEXITCODE -ne 0) { throw "Creating offline Python environment failed" }
$BuildPython = Join-Path $EnvironmentDir "Scripts\python.exe"
& $BuildPython -m pip install --no-index --find-links (Join-Path $KitDir "wheels") -r (Join-Path $KitDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Offline wheel installation failed" }
& $BuildPython -m PyInstaller --noconfirm --clean --distpath (Join-Path $ProjectDir "build\sidecar-offline\dist") --workpath (Join-Path $ProjectDir "build\sidecar-offline\work") (Join-Path $ProjectDir "packaging\sidecar.spec")
if ($LASTEXITCODE -ne 0) { throw "Offline sidecar build failed" }
$BinaryDir = Join-Path $ProjectDir "src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $BinaryDir | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectDir "build\sidecar-offline\dist\sona-code-sidecar.exe") -Destination (Join-Path $BinaryDir "sona-code-sidecar-x86_64-pc-windows-msvc.exe") -Force
Copy-Item -LiteralPath (Join-Path $KitDir "binaries\opencode.exe") -Destination (Join-Path $BinaryDir "opencode-x86_64-pc-windows-msvc.exe") -Force
Push-Location $ProjectDir
try {
    & $BuildNode "node_modules\electron-builder\out\cli\cli.js" --win nsis --x64 --publish never "--config.electronDist=node_modules/electron/dist"
    if ($LASTEXITCODE -ne 0) { throw "Offline Electron installer build failed" }
} finally { Pop-Location }
Write-Host "Offline installer: $ProjectDir\dist\electron\Sona-Code-*-Setup.exe"
