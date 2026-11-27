# SpaceStudio launcher for Windows (PowerShell). Builds what is missing, then serves http://127.0.0.1:8000
#   powershell -ExecutionPolicy Bypass -File run.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Everything heavy lives next to the install folder (e.g. E:\SpaceStudio\...), not on C:.
# Override any of these by setting the variable before running.
$Base = Split-Path -Parent $Root
function Set-Default($name, $value) {
    if (-not [Environment]::GetEnvironmentVariable($name)) { [Environment]::SetEnvironmentVariable($name, $value, "Process") }
    New-Item -ItemType Directory -Force -Path ([Environment]::GetEnvironmentVariable($name)) | Out-Null
}
Set-Default "SPACESTUDIO_DATA_DIR"     (Join-Path $Base "data")
Set-Default "SPACESTUDIO_ENGINES_DIR"  (Join-Path $Base "engines")
Set-Default "PIP_CACHE_DIR"            (Join-Path $Base "cache\pip")
Set-Default "npm_config_cache"         (Join-Path $Base "cache\npm")
Set-Default "HF_HOME"                  (Join-Path $Base "cache\huggingface")
Set-Default "PLAYWRIGHT_BROWSERS_PATH" (Join-Path $Base "cache\playwright")
Set-Default "TEMP"                     (Join-Path $Base "cache\tmp")
$env:TMP = $env:TEMP
Write-Host "Data:    $env:SPACESTUDIO_DATA_DIR"
Write-Host "Engines: $env:SPACESTUDIO_ENGINES_DIR"
Write-Host "Caches:  $(Join-Path $Base 'cache')"

Set-Location (Join-Path $Root "backend")

function Find-Python {
    foreach ($v in "3.12", "3.11", "3.13") {
        try { & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { return @("py", "-$v") } } catch {}
    }
    try {
        $ok = & python -c "import sys; print(int((3,11) <= sys.version_info[:2] <= (3,13)))" 2>$null
        if ($ok -eq "1") { return @("python") }
    } catch {}
    return $null
}

$venvPy = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $py = Find-Python
    if (-not $py) { Write-Host "Python 3.11-3.13 not found. Install it: winget install Python.Python.3.12" -ForegroundColor Red; exit 1 }
    Write-Host "==> Creating backend virtual environment" -ForegroundColor Cyan
    $pyArgs = @()
    if ($py.Length -gt 1) { $pyArgs = $py[1..($py.Length - 1)] }
    & $py[0] @pyArgs -m venv .venv
    & $venvPy -m pip install -q --upgrade pip
    & $venvPy -m pip install -q -e ".[dev]"
}

$dist = Join-Path $Root "frontend\dist\index.html"
if (-not (Test-Path $dist)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Write-Host "npm not found. Install Node.js: winget install OpenJS.NodeJS.LTS" -ForegroundColor Red; exit 1 }
    Write-Host "==> Building the dashboard" -ForegroundColor Cyan
    Push-Location (Join-Path $Root "frontend")
    npm install --no-audit --no-fund
    npm run build
    Pop-Location
}

foreach ($tool in "ffmpeg", "claude", "git") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "Warning: '$tool' is not on PATH. Repository Health in the app shows how to install it." -ForegroundColor Yellow
    }
}

$env:PATH = (Join-Path $Root "scripts") + ";" + $env:PATH
Write-Host "==> SpaceStudio: http://127.0.0.1:8000  (Ctrl+C to stop)" -ForegroundColor Green
Start-Job -ScriptBlock { Start-Sleep 4; Start-Process "http://127.0.0.1:8000" } | Out-Null
& (Join-Path $PWD ".venv\Scripts\spacestudio.exe") serve @args
