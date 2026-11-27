# SpaceStudio system check for Windows. Read-only: it only reports versions and changes nothing.
# Run in PowerShell:  powershell -ExecutionPolicy Bypass -File check-system.ps1

function Show($name, $ok, $detail, $fix) {
    if ($ok) { Write-Host ("OK       {0,-14} {1}" -f $name, $detail) -ForegroundColor Green }
    else     { Write-Host ("MISSING  {0,-14} {1}" -f $name, $fix) -ForegroundColor Yellow }
}

Write-Host "`n== Windows ==" -ForegroundColor Cyan
foreach ($c in "git", "python", "node", "ffmpeg", "claude") {
    $p = Get-Command $c -ErrorAction SilentlyContinue
    $v = ""
    if ($p) { try { $v = (& $c --version 2>&1 | Select-Object -First 1) } catch {} }
    Show $c ([bool]$p) $v "not on Windows PATH (fine if installed inside WSL)"
}

Write-Host "`n== WSL ==" -ForegroundColor Cyan
$wsl = Get-Command wsl -ErrorAction SilentlyContinue
if (-not $wsl) {
    Show "wsl" $false "" "PowerShell as Administrator: wsl --install -d Ubuntu  (then restart)"
    exit
}
$distros = (wsl -l -q 2>$null) -replace "`0", "" | Where-Object { $_.Trim() }
if (-not $distros) {
    Show "ubuntu" $false "" "PowerShell as Administrator: wsl --install -d Ubuntu  (then restart)"
    exit
}
Show "wsl distros" $true ($distros -join ", ") ""

$linux = @'
chk() { if command -v "$1" >/dev/null 2>&1; then echo "OK       $1: $($1 ${2:---version} 2>&1 | head -1)"; else echo "MISSING  $1"; fi; }
for c in git python3 node npm ffmpeg ffprobe claude espeak-ng; do chk $c; done
python3 -c "import sys; v=sys.version_info; print(('OK      ' if (3,11)<=v[:2]<=(3,13) else 'WRONG   ')+' python version %d.%d (need 3.11-3.13)'%v[:2])" 2>/dev/null
python3 -c "import venv" 2>/dev/null && echo "OK       python3-venv" || echo "MISSING  python3-venv (sudo apt install -y python3-venv)"
command -v node >/dev/null && node -e "const [a,b]=process.versions.node.split('.').map(Number);console.log((a>22||(a===22&&b>=23)?'OK      ':'WRONG   ')+' node '+process.versions.node+' (need 22.23+)')"
command -v ffmpeg >/dev/null && ffmpeg -version | head -1 | awk '{split($3,v,"."); sub(/^n/,"",v[1]); print ((v[1]+0>=7)?"OK      ":"WRONG   ")" ffmpeg "$3" (need 7+)"}'
for r in cinematic-skill lemo-opuscar OpenMontage; do [ -d ~/.spacestudio/engines/$r ] && echo "OK       engine $r" || echo "MISSING  engine $r (~/.spacestudio/engines/$r)"; done
[ -d ~/VideoCreationTool ] && echo "OK       SpaceStudio (~/VideoCreationTool)" || echo "MISSING  SpaceStudio (~/VideoCreationTool)"
'@
Write-Host "`n== Inside WSL (default distro) ==" -ForegroundColor Cyan
$linux -replace "`r", "" | wsl bash -s
