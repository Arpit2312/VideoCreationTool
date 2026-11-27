"""System health checks of real tools (claude, ffmpeg/ffprobe, node, python, git, espeak-ng/piper, disk)."""
from __future__ import annotations

import shutil
import subprocess
import re
from pathlib import Path
from typing import Any, Optional

from ..platform_compat import IS_WINDOWS, find_bash
from ..engines.base import EngineHealth, HealthItem


def _run(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


def check_version(id_: str, name: str, binary: str, args: list[str], fix: str, optional: bool = False) -> HealthItem:
    path = shutil.which(binary)
    if not path:
        return HealthItem(id_, name, "warning" if optional else "error",
                          f"{binary} not found" + (" (optional)" if optional else ""), fix)
    code, out = _run([path, *args])
    if code != 0:
        return HealthItem(id_, name, "warning" if optional else "error", f"{binary} failed: {_first_line(out)}", fix)
    return HealthItem(id_, name, "ok", f"{_first_line(out)} ({path})")


TESTED_CLAUDE = (2, 1, 332)


def claude_info(settings: Any, runner: Any) -> dict:
    b = settings.claude_bin
    simulated = runner.is_simulated()
    try:
        path = runner.resolve_bin()
        launcher = runner.launcher()
    except Exception:
        return {"bin": b, "version": None, "simulated": simulated, "ok": False}
    code, out = _run([*launcher, "--version"], timeout=30)
    version = _first_line(out) if code == 0 else None
    info = {"bin": path, "version": version, "simulated": simulated, "ok": code == 0}
    # SpaceStudio's CLI flags were verified against Claude Code 2.1.332; older builds may lack some.
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if m and not simulated and tuple(map(int, m.groups())) < TESTED_CLAUDE:
        info["warning"] = (f"Claude Code {m.group(0)} is older than the tested {'.'.join(map(str, TESTED_CLAUDE))}; "
                           "run `claude update` (some options SpaceStudio uses may be missing)")
    return info


def ffmpeg_items(settings: Any) -> list[HealthItem]:
    fix = ("Install FFmpeg 7+: winget install Gyan.FFmpeg (then open a new terminal), or set ffmpeg in Settings"
           if IS_WINDOWS else
           "Install FFmpeg 7+ with libx264 (a static build from johnvansickle.com/ffmpeg) or set ffmpeg in Settings")
    items = [check_version("ffmpeg", "FFmpeg", settings.ffmpeg, ["-hide_banner", "-version"], fix)]
    if items[0].status == "ok":
        path = shutil.which(settings.ffmpeg)
        _, enc = _run([path, "-hide_banner", "-encoders"])
        _, flt = _run([path, "-hide_banner", "-filters"])
        missing = [n for n, ok in (("libx264", "libx264" in enc), ("loudnorm", "loudnorm" in flt)) if not ok]
        items.append(HealthItem("ffmpeg_features", "FFmpeg libx264 + loudnorm", "error" if missing else "ok",
                                ("missing: " + ", ".join(missing)) if missing else "libx264 encoder and loudnorm filter",
                                fix if missing else ""))
    items.append(check_version("ffprobe", "ffprobe", settings.ffprobe, ["-hide_banner", "-version"], fix))
    return items


def disk_item(settings: Any) -> HealthItem:
    try:
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        du = shutil.disk_usage(settings.data_dir)
    except OSError as e:
        return HealthItem("disk", "Disk space", "error", str(e))
    free_gb = du.free / 1024 ** 3
    status = "ok" if free_gb >= 10 else ("warning" if free_gb >= 2 else "error")
    return HealthItem("disk", "Disk space", status, f"{free_gb:.1f} GB free in {settings.data_dir}",
                      "" if status == "ok" else "Free some disk space; renders need several GB")


def tool_items(settings: Any) -> list[HealthItem]:
    items = ffmpeg_items(settings)
    win = IS_WINDOWS
    items.append(check_version("node", "Node.js", "node", ["--version"],
                               "winget install OpenJS.NodeJS.LTS" if win else "Install Node.js 22.23+ (nodesource.com)"))
    items.append(check_version("python", "Python", settings.python_bin, ["--version"],
                               "winget install Python.Python.3.12" if win else "Install Python 3.11-3.13"))
    items.append(check_version("git", "git", "git", ["--version"],
                               "winget install Git.Git" if win else "Install git (sudo apt install git)"))
    if win:
        bash = find_bash()
        items.append(HealthItem("bash", "Git Bash", "ok" if bash else "error",
                                bash or "bash.exe from Git for Windows not found",
                                "" if bash else "winget install Git.Git, or set CLAUDE_CODE_GIT_BASH_PATH to bash.exe"))
    items.append(check_version("espeak-ng", "espeak-ng (free voice)", "espeak-ng", ["--version"],
                               ("Optional: install espeak-ng from github.com/espeak-ng/espeak-ng/releases (free scratch voice)"
                                if win else "Optional: sudo apt install espeak-ng (free scratch voice)"), optional=True))
    items.append(check_version("piper", "Piper (free voice)", "piper", ["--help"],
                               "Optional: install Piper TTS for a better free voice", optional=True))
    items.append(disk_item(settings))
    return items


def engine_health_dict(h: EngineHealth) -> dict:
    return {"engine": h.engine, "ok": h.ok, "zero_cost_ready": h.zero_cost_ready, "commit": h.commit,
            "items": [i.__dict__ if hasattr(i, "__dict__") else dict(i) for i in h.items]}


def engine_health(adapter: Any, zero_cost: bool) -> dict:
    try:
        return engine_health_dict(adapter.health(zero_cost=zero_cost))
    except Exception as e:
        return {"engine": adapter.id, "ok": False, "zero_cost_ready": False, "commit": None,
                "items": [{"id": "health", "name": "Health check", "status": "error",
                           "detail": f"health check crashed: {e}", "fix": ""}]}


def report(settings: Any, runner: Any, registry: Optional[Any], enabled: Optional[set[str]] = None) -> dict:
    tools = tool_items(settings)
    claude = claude_info(settings, runner)
    engines = []
    if registry is not None:
        for a in registry.all():
            if enabled is not None and a.id not in enabled:
                continue
            engines.append(engine_health(a, settings.zero_cost_default))
    tools_ok = all(t.status != "error" for t in tools if t.id in ("ffmpeg", "ffmpeg_features", "ffprobe"))
    # Engines that are installed AND can run without paid providers. An engine that is not installed is
    # not "ready" (it cannot run at all); one that is installed but has paid keys loaded blocks zero-cost.
    ready = [e["engine"] for e in engines if e["ok"] and e["zero_cost_ready"]]
    blocking = [e["engine"] for e in engines if e["ok"] and not e["zero_cost_ready"]]
    zc = claude["ok"] and tools_ok and bool(ready) and not blocking
    return {"tools": [t.__dict__ for t in tools], "engines": engines, "claude": claude, "zero_cost_ready": bool(zc),
            "engines_ready": ready, "engines_total": len(engines)}
