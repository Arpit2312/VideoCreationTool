# SpaceStudio dependency installer for Windows. Checks everything, lists what is missing, asks ONCE, installs all.
#   powershell -ExecutionPolicy Bypass -File setup.ps1            (asks before installing)
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -Yes       (no question)
#   -CheckOnly : only report (exit 0 = nothing missing, 1 = something missing)
#   -SkipSystem: skip winget packages (FFmpeg / Node / Claude Code); only engine packages
param([switch]$Yes, [switch]$CheckOnly, [switch]$SkipSystem)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Base = Split-Path -Parent $Root

# Same storage layout as run.ps1: everything heavy next to the install folder.
foreach ($pair in @(
        @("SPACESTUDIO_DATA_DIR", (Join-Path $Base "data")),
        @("SPACESTUDIO_ENGINES_DIR", (Join-Path $Base "engines")),
        @("PIP_CACHE_DIR", (Join-Path $Base "cache\pip")),
        @("npm_config_cache", (Join-Path $Base "cache\npm")),
        @("HF_HOME", (Join-Path $Base "cache\huggingface")),
        @("PLAYWRIGHT_BROWSERS_PATH", (Join-Path $Base "cache\playwright")))) {
    if (-not [Environment]::GetEnvironmentVariable($pair[0])) { [Environment]::SetEnvironmentVariable($pair[0], $pair[1], "Process") }
    New-Item -ItemType Directory -Force -Path ([Environment]::GetEnvironmentVariable($pair[0])) | Out-Null
}
$env:PYTHONUTF8 = "1"
$Engines = $env:SPACESTUDIO_ENGINES_DIR
$Cinematic = Join-Path $Engines "cinematic-skill"
$Lemo = Join-Path $Engines "lemo-opuscar"
$OM = Join-Path $Engines "OpenMontage"

function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Ver($text) { if ($text -match '(\d+)\.(\d+)\.(\d+)') { [version]"$($matches[1]).$($matches[2]).$($matches[3])" } else { $null } }
function Find-Bash {
    $c = @()
    $git = (Get-Command git -ErrorAction SilentlyContinue).Source
    if ($git) { $r = Split-Path (Split-Path $git); $c += (Join-Path $r "bin\bash.exe"), (Join-Path $r "usr\bin\bash.exe") }
    $c += (Join-Path $env:ProgramFiles "Git\bin\bash.exe")
    foreach ($p in $c) { if ($p -and (Test-Path $p)) { return $p } }
    return $null
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Bash-Run($dir, $command) {
    $bash = Find-Bash
    if (-not $bash) { Write-Host "   Git Bash not found (install Git for Windows)" -ForegroundColor Red; return $false }
    $d = $dir -replace '\\', '/'
    & $bash -lc "cd '$d' && $command"
    return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------- what is missing
$todo = New-Object System.Collections.ArrayList
function Need($id, $title) { [void]$todo.Add([pscustomobject]@{ id = $id; title = $title }) }

if (-not (Has git)) { Write-Host "Git is missing: winget install Git.Git, then run this again." -ForegroundColor Red; exit 1 }
if (-not (Has python)) { Write-Host "Python is missing: winget install Python.Python.3.12, then run this again." -ForegroundColor Red; exit 1 }

if (-not $SkipSystem) {
    if (-not (Has ffmpeg)) { Need "ffmpeg" "FFmpeg (winget Gyan.FFmpeg)" }
    $nodeV = if (Has node) { Ver (node --version) } else { $null }
    $nodeOk = $nodeV -and (($nodeV.Major -eq 22 -and $nodeV -ge [version]"22.23.4") -or ($nodeV.Major -eq 24 -and $nodeV -ge [version]"24.22.1") -or ($nodeV.Major -ge 26))
    if (-not $nodeOk) { Need "node" "Node.js 22.23+/24.22+ (OpenMontage's Remotion needs it; found $nodeV)" }
    $claudeV = if (Has claude) { Ver (claude --version 2>$null) } else { $null }
    if (-not $claudeV -or $claudeV -lt [version]"2.1.332") { Need "claude" "Claude Code update (found $claudeV, tested with 2.1.332)" }
}
if (Test-Path $Cinematic) {
    python -c "import skia, numpy, PIL" 2>$null
    if ($LASTEXITCODE -ne 0) { Need "cinematic" "cinematic-skill Python packages (skia-python, numpy, pillow, scipy)" }
}
if (Test-Path $Lemo) {
    if (-not (Test-Path (Join-Path $Lemo "node_modules\.lemo-ok")) -or -not (Test-Path (Join-Path $Lemo ".venv\.lemo-core"))) { Need "lemo" "lemo-opuscar core packages (npm, headless Chromium, Python)" }
    if (-not (Test-Path (Join-Path $Lemo ".venv\.lemo-voice")) -or -not (Test-Path (Join-Path $Lemo "core\tts\kokoro-v1.0.onnx"))) { Need "lemo-voice" "lemo-opuscar offline voice (Kokoro, ~0.5 GB)" }
}
if (Test-Path $OM) {
    if (-not (Test-Path (Join-Path $OM ".venv\Scripts\python.exe")) -or -not (Test-Path (Join-Path $OM "remotion-composer\node_modules"))) { Need "openmontage" "OpenMontage setup (Python venv, requirements, Remotion, Piper voice)" }
}

if ($todo.Count -eq 0) { Write-Host "All dependencies are installed." -ForegroundColor Green; exit 0 }
Write-Host "`nMissing:" -ForegroundColor Yellow
foreach ($t in $todo) { Write-Host "  - $($t.title)" }
if ($CheckOnly) { exit 1 }
if (-not $Yes) {
    Write-Host "`nEverything goes to $Base (caches included), except FFmpeg/Node/Claude Code, which winget installs as usual." -ForegroundColor Gray
    $a = Read-Host "Install everything now? [Y/n]"
    if ($a -and $a.Trim().ToLower() -notin @("y", "yes")) { Write-Host "Skipped. SpaceStudio will start; Repository Health shows what is missing."; exit 1 }
}

# ---------------------------------------------------------------- install
$failed = @()
$wg = @("--accept-package-agreements", "--accept-source-agreements", "--silent")
foreach ($t in $todo) {
    Write-Host "`n==> $($t.title)" -ForegroundColor Cyan
    $ok = $true
    switch ($t.id) {
        "ffmpeg" { winget install Gyan.FFmpeg @wg; Refresh-Path; $ok = Has ffmpeg }
        "node" {
            winget upgrade OpenJS.NodeJS @wg
            if ($LASTEXITCODE -ne 0) { winget install OpenJS.NodeJS @wg }
            Refresh-Path
            $v = Ver (node --version); $ok = $v -and ($v -ge [version]"24.22.1" -or ($v.Major -eq 22 -and $v -ge [version]"22.23.4"))
        }
        "claude" {
            $src = (Get-Command claude -ErrorAction SilentlyContinue).Source
            if ($src -and $src -like "*WinGet*") { winget upgrade Anthropic.ClaudeCode @wg } elseif ($src) { claude update } else { winget install Anthropic.ClaudeCode @wg }
            Refresh-Path; $ok = Has claude
        }
        "cinematic" {
            python -m pip install -r (Join-Path $Cinematic "requirements.txt")
            python -c "import skia, numpy, PIL" 2>$null; $ok = ($LASTEXITCODE -eq 0)
        }
        "lemo" { $env:LEMO_OPUSCAR_HOME = $Lemo; $ok = Bash-Run $Lemo "sh plugin/skills/lemo-opuscar/scripts/setup.sh deps" }
        "lemo-voice" { $env:LEMO_OPUSCAR_HOME = $Lemo; $ok = Bash-Run $Lemo "sh plugin/skills/lemo-opuscar/scripts/setup.sh deps voice" }
        "openmontage" {
            # OpenMontage's README Windows steps (no `make` on Windows). No .env is created: no provider keys get auto-loaded.
            $ok = Bash-Run $OM "python -m venv .venv && .venv/Scripts/python -m pip install 'pip>=26.2.2' && .venv/Scripts/python -m pip install -r requirements.txt && (cd remotion-composer && npm install) && .venv/Scripts/python -m pip install piper-tts"
            if ($ok -and -not (Test-Path (Join-Path $HOME ".piper\models\en_US-lessac-medium.onnx"))) {
                $ok = Bash-Run $OM ".venv/Scripts/python -m piper.download_voices --download-dir ~/.piper/models en_US-lessac-medium"
            }
        }
    }
    if ($ok) { Write-Host "   done" -ForegroundColor Green } else { Write-Host "   FAILED (see the messages above)" -ForegroundColor Red; $failed += $t.title }
}

Write-Host ""
if ($failed.Count -eq 0) { Write-Host "All dependencies installed." -ForegroundColor Green; exit 0 }
Write-Host "These did not install:" -ForegroundColor Red
foreach ($f in $failed) { Write-Host "  - $f" }
Write-Host "SpaceStudio still starts; Repository Health shows each item and its fix. Send a screenshot of the errors above for help."
exit 1
