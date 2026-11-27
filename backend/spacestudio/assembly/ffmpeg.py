"""Small, shell-free ffmpeg/ffprobe helpers shared by assembly and QA.

Every call uses an argument list (never a shell), a timeout, and captures stderr so failures
can be reported with an actionable tail of ffmpeg's own message.
"""
from __future__ import annotations

import functools
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional


class ToolMissing(RuntimeError):
    """The configured ffmpeg/ffprobe binary could not be executed."""


class ToolFailed(RuntimeError):
    def __init__(self, message: str, stderr: str = "", returncode: int | None = None):
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


def binaries(settings) -> tuple[str, str]:
    ffmpeg = getattr(settings, "ffmpeg", None) or "ffmpeg"
    ffprobe = getattr(settings, "ffprobe", None) or "ffprobe"
    return str(ffmpeg), str(ffprobe)


def stderr_tail(text: str, lines: int = 8) -> str:
    keep = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(keep[-lines:])


def run(cmd: list[str], *, timeout: float = 600, cwd: Optional[Path] = None, what: str = "ffmpeg",
        check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            [str(c) for c in cmd], cwd=str(cwd) if cwd else None, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise ToolMissing(f"cannot run {cmd[0]!r}: not found. Install ffmpeg/ffprobe or fix the path in "
                          f"Settings (ffmpeg / ffprobe).") from exc
    except PermissionError as exc:
        raise ToolMissing(f"cannot run {cmd[0]!r}: permission denied.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolFailed(f"{what} timed out after {int(timeout)} s", stderr=str(exc.stderr or "")) from exc
    if check and proc.returncode != 0:
        raise ToolFailed(f"{what} failed (exit {proc.returncode}): {stderr_tail(proc.stderr)}",
                         stderr=proc.stderr, returncode=proc.returncode)
    return proc


@lru_cache(maxsize=16)
def _capabilities(ffmpeg: str) -> tuple[frozenset, frozenset]:
    filters_out = run([ffmpeg, "-hide_banner", "-filters"], timeout=30, what="ffmpeg -filters").stdout
    enc_out = run([ffmpeg, "-hide_banner", "-encoders"], timeout=30, what="ffmpeg -encoders").stdout
    filters, encoders = set(), set()
    for line in filters_out.splitlines():
        m = re.match(r"^\s*[TSC.]{3}\s+(\S+)\s+\S+->\S+", line)
        if m:
            filters.add(m.group(1))
    for line in enc_out.splitlines():
        m = re.match(r"^\s*[VASD][\w.]{5}\s+(\S+)\s", line)
        if m and m.group(1) != "=":
            encoders.add(m.group(1))
    return frozenset(filters), frozenset(encoders)


@functools.lru_cache(maxsize=256)
def _asks(ffmpeg: str, kind: str, name: str) -> bool:
    """Ask FFmpeg about one filter/encoder (`-h filter=NAME`). Unlike parsing the `-filters` table, whose
    column layout differs between FFmpeg versions, this answer has the same shape in 6.x, 7.x and later."""
    try:
        out = run([ffmpeg, "-hide_banner", "-h", f"{kind}={name}"], timeout=30, what=f"ffmpeg -h {kind}").stdout
    except (ToolFailed, ToolMissing):
        return False
    head = (out or "").lstrip().lower()
    return head.startswith(f"{kind} {name.lower()}")


def has_filter(ffmpeg: str, name: str) -> bool:
    return name in _capabilities(ffmpeg)[0] or _asks(ffmpeg, "filter", name)


def has_encoder(ffmpeg: str, name: str) -> bool:
    return name in _capabilities(ffmpeg)[1] or _asks(ffmpeg, "encoder", name)


def clear_cache() -> None:
    _capabilities.cache_clear()
    _asks.cache_clear()


def probe(ffprobe: str, path: Path, timeout: float = 60) -> dict:
    proc = run([ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
               timeout=timeout, what=f"ffprobe {Path(path).name}")
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ToolFailed(f"ffprobe returned unparsable output for {path}") from exc


def video_stream(info: dict) -> Optional[dict]:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)


def audio_stream(info: dict) -> Optional[dict]:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)


def duration_of(info: dict) -> Optional[float]:
    for src in [info.get("format", {})] + list(info.get("streams", [])):
        try:
            d = float(src.get("duration"))
            if d > 0:
                return d
        except (TypeError, ValueError):
            continue
    return None


def parse_rate(rate: str | None) -> Optional[float]:
    if not rate:
        return None
    try:
        if "/" in rate:
            n, d = rate.split("/", 1)
            return float(n) / float(d) if float(d) else None
        return float(rate)
    except ValueError:
        return None
