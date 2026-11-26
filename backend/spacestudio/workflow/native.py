"""Stage handlers that run in Python, without tokens: setup, silent/user narration timing, preview, sound,
render, captions, qa, export. Media work is delegated to ``assembly.pipeline`` and ``qa.checks`` (agent D).
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from .. import workspace
from ..interchange.schema import NarrationLineTiming, NarrationTiming, Script, Storyboard, VideoConfig


class NativeError(RuntimeError):
    pass


@dataclass
class NativeContext:
    video: Any
    stage: Any
    ws: Path
    config: VideoConfig
    settings: Any
    registry: Any = None
    stages: list[dict] = field(default_factory=list)
    log: Callable[[str], None] = lambda _m: None
    engines_in_use: list[str] = field(default_factory=list)


@dataclass
class NativeResult:
    outputs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""
    failed: bool = False
    error: Optional[str] = None


# ----------------------------------------------------------------------------- helpers
def load_pipeline():
    try:
        return importlib.import_module("spacestudio.assembly.pipeline")
    except ImportError as e:
        raise NativeError(f"the media pipeline (spacestudio.assembly.pipeline) is not available: {e}") from None


def load_qa():
    try:
        return importlib.import_module("spacestudio.qa.checks")
    except ImportError as e:
        raise NativeError(f"the QA checks (spacestudio.qa.checks) are not available: {e}") from None


@contextlib.contextmanager
def render_slot(settings: Any, timeout_s: float = 6 * 3600) -> Iterator[Optional[int]]:
    """Hold one of N file-lock render slots (shared with scripts/spacestudio-slot)."""
    n = max(1, int(os.environ.get("SPACESTUDIO_RENDER_SLOTS") or getattr(settings, "max_render_jobs", 1) or 1))
    d = Path(settings.data_dir) / "slots"
    d.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX: no cross-process semaphore
        yield None
        return
    deadline = time.monotonic() + timeout_s
    while True:
        for i in range(n):
            fh = open(d / f"render.{i}.lock", "a+")
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                fh.close()
                continue
            try:
                yield i
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()
            return
        if time.monotonic() > deadline:
            raise NativeError("timed out waiting for a render slot")
        time.sleep(0.5)


def _pipeline_call(fn: Callable, *args: Any) -> tuple[list[str], list[str]]:
    mod = importlib.import_module(fn.__module__)
    if hasattr(mod, "LAST_WARNINGS"):
        try:
            mod.LAST_WARNINGS.clear()
        except AttributeError:
            pass
    res = fn(*args)
    warnings: list[str] = []
    if isinstance(res, tuple) and len(res) == 2:
        files, warnings = list(res[0] or []), list(res[1] or [])
    else:
        files = list(res or [])
    warnings += [str(w) for w in (getattr(mod, "LAST_WARNINGS", None) or []) if str(w) not in warnings]
    return [str(f) for f in files], warnings


def _assembly_error(e: Exception) -> NativeResult:
    return NativeResult(failed=True, error=str(e) or e.__class__.__name__)


def _ffmpeg(settings: Any) -> str:
    b = getattr(settings, "ffmpeg", "ffmpeg") or "ffmpeg"
    found = shutil.which(b)
    if not found:
        raise NativeError(f"ffmpeg not found ({b!r}); install ffmpeg (sudo apt install ffmpeg) or set it in Settings")
    return found


def _read_model(ws: Path, rel: str, model):
    f = ws / rel
    if not f.exists():
        raise NativeError(f"{rel} is missing")
    try:
        return model.model_validate(json.loads(f.read_text(encoding="utf-8")))
    except Exception as e:
        raise NativeError(f"{rel} is not valid: {str(e)[:300]}") from None


# ----------------------------------------------------------------------------- handlers
def setup(ctx: NativeContext) -> NativeResult:
    snapshot = {"id": ctx.video.id, "title": ctx.video.title, "idea": ctx.video.idea, "mode": ctx.video.mode,
                "primary_engine": ctx.video.primary_engine, "config": ctx.config.model_dump()}
    ws = workspace.create(Path(ctx.ws).parent, ctx.video.id, snapshot)
    res = NativeResult(outputs=["project.json"])
    try:
        _ffmpeg(ctx.settings)
    except NativeError as e:
        return NativeResult(failed=True, error=str(e))
    health: dict[str, Any] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engines": {}}
    problems = []
    if ctx.registry is not None:
        for eid in ctx.engines_in_use:
            try:
                h = ctx.registry.get(eid).health(zero_cost=ctx.config.zero_cost)
            except Exception as e:  # health check crashed: report, don't hide
                res.warnings.append(f"health check of {eid} failed: {e}")
                continue
            items = [getattr(i, "__dict__", i) for i in (h.items or [])]
            health["engines"][eid] = {"ok": h.ok, "zero_cost_ready": h.zero_cost_ready, "items": items,
                                      "commit": h.commit}
            if not h.ok:
                bad = "; ".join(f"{i.get('name')}: {i.get('detail')} {i.get('fix', '')}".strip()
                                for i in items if i.get("status") == "error")
                res.warnings.append(f"engine {eid} is not healthy: {bad or 'see Engines page'}")
            if ctx.config.zero_cost and not h.zero_cost_ready:
                problems.append(f"engine {eid} is not zero-cost ready (paid keys configured in its repo?)")
    (ws / ".spacestudio" / "health.json").write_text(json.dumps(health, indent=2, default=str), encoding="utf-8")
    res.outputs.append(".spacestudio/health.json")
    if problems:
        return NativeResult(outputs=res.outputs, warnings=res.warnings, failed=True, error="; ".join(problems))
    res.summary = "Workspace ready; engine health recorded."
    return res


def _lines_for(ws: Path) -> tuple[Script, Storyboard]:
    return _read_model(ws, "scripts/script.json", Script), _read_model(ws, "storyboard/storyboard.json", Storyboard)


def _silence(ctx: NativeContext, out: Path, seconds: float) -> None:
    ff = _ffmpeg(ctx.settings)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(out)],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise NativeError(f"ffmpeg could not write {out.name}: {p.stderr.strip()[-300:]}")


def narration_silent(ctx: NativeContext) -> NativeResult:
    """voiceover == none: timing from storyboard scene durations, lines spread within their scene."""
    script, sb = _lines_for(ctx.ws)
    text = {ln.id: ln.text for ln in script.lines}
    lines: list[NarrationLineTiming] = []
    t = 0.0
    for sc in sorted(sb.scenes, key=lambda s: s.number):
        ids = [i for i in sc.line_ids if i in text] or []
        n = max(1, len(ids))
        step = float(sc.duration_seconds) / n
        for j, lid in enumerate(ids):
            lines.append(NarrationLineTiming(id=lid, start=round(t + j * step, 3), end=round(t + (j + 1) * step, 3),
                                             text=text[lid]))
        t += float(sc.duration_seconds)
    timing = NarrationTiming(audio="narration/narration.wav", engine="spacestudio", voice="none",
                             duration=round(t, 3), lines=lines)
    _silence(ctx, ctx.ws / "narration" / "narration.wav", t)
    (ctx.ws / "narration" / "timing.json").write_text(timing.model_dump_json(indent=2), encoding="utf-8")
    return NativeResult(outputs=["narration/narration.wav", "narration/timing.json"],
                        warnings=["no voice-over: the narration track is silent"],
                        summary=f"Silent track of {t:.1f}s; timing for {len(lines)} lines from scene durations.")


def narration_user(ctx: NativeContext) -> NativeResult:
    """voiceover == user without an engine: convert the recording, timing proportional to line length."""
    rel = ctx.config.user_voice_file
    if not rel:
        cands = sorted(p for p in (ctx.ws / "assets" / "user").glob("*")
                       if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac", ".ogg"))
        if not cands:
            raise NativeError("voiceover is 'user' but no recording was uploaded to assets/user/")
        src = cands[0]
    else:
        src = workspace.resolve(ctx.ws, rel)
        if not src.exists():
            raise NativeError(f"user voice file {rel} not found")
    script = _read_model(ctx.ws, "scripts/script.json", Script)
    ff = _ffmpeg(ctx.settings)
    out = ctx.ws / "narration" / "narration.wav"
    p = subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-ar", "48000", "-ac", "2",
                        "-c:a", "pcm_s16le", str(out)], capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        raise NativeError(f"could not convert the user recording: {p.stderr.strip()[-300:]}")
    probe = shutil.which(getattr(ctx.settings, "ffprobe", "ffprobe") or "ffprobe")
    if not probe:
        raise NativeError("ffprobe not found")
    q = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True, timeout=60)
    try:
        dur = float(q.stdout.strip())
    except ValueError:
        raise NativeError("could not read the duration of the user recording") from None
    weights = [max(1, len((ln.spoken or ln.text).split())) for ln in script.lines]
    total = sum(weights) or 1
    t, lines = 0.0, []
    for ln, w in zip(script.lines, weights):
        d = dur * w / total
        lines.append(NarrationLineTiming(id=ln.id, start=round(t, 3), end=round(t + d, 3), text=ln.text))
        t += d
    timing = NarrationTiming(engine="spacestudio", voice="user", duration=round(dur, 3), lines=lines)
    (ctx.ws / "narration" / "timing.json").write_text(timing.model_dump_json(indent=2), encoding="utf-8")
    return NativeResult(outputs=["narration/narration.wav", "narration/timing.json"],
                        warnings=["line timing is estimated from text length (no forced alignment)"],
                        summary=f"User recording ({dur:.1f}s) converted; estimated timing for {len(lines)} lines.")


def _media(fn_name: str, heavy: bool, *, needs_title: bool = False) -> Callable[[NativeContext], NativeResult]:
    def handler(ctx: NativeContext) -> NativeResult:
        pipeline = load_pipeline()
        fn = getattr(pipeline, fn_name, None)
        if fn is None:
            raise NativeError(f"assembly.pipeline has no {fn_name}()")
        args: tuple = (ctx.ws, ctx.config, ctx.settings) + ((ctx.video.title,) if needs_title else ())
        err_cls = getattr(pipeline, "AssemblyError", RuntimeError)
        try:
            if heavy:
                with render_slot(ctx.settings):
                    files, warns = _pipeline_call(fn, *args)
            else:
                files, warns = _pipeline_call(fn, *args)
        except err_cls as e:
            return _assembly_error(e)
        return NativeResult(outputs=files, warnings=warns, summary=f"{fn_name}: {len(files)} file(s) written.")
    handler.__name__ = fn_name
    return handler


def captions(ctx: NativeContext) -> NativeResult:
    if not ctx.config.captions:
        return NativeResult(summary="Captions are disabled for this video.")
    return _media("captions", heavy=bool(ctx.config.burn_captions))(ctx)


def qa(ctx: NativeContext) -> NativeResult:
    checks = load_qa()
    results = checks.run_all(ctx.ws, ctx.config, ctx.stages, ctx.settings)
    warnings, blocking = [], []
    for r in results:
        st = r.get("status")
        line = f"{r.get('name', r.get('id'))}: {r.get('detail', '')}".strip()
        if st == "failed" and r.get("blocking"):
            blocking.append(line)
        elif st in ("failed", "warning"):
            warnings.append(line)
        elif st == "not_run":
            warnings.append(f"not run — {line}")
    passed = sum(1 for r in results if r.get("status") == "passed")
    summary = f"{passed}/{len(results)} checks passed, {len(warnings)} warning(s), {len(blocking)} blocking failure(s)."
    if blocking:
        return NativeResult(outputs=[".spacestudio/qa.json"], warnings=warnings, summary=summary, failed=True,
                            error="QA failed: " + "; ".join(blocking))
    return NativeResult(outputs=[".spacestudio/qa.json"], warnings=warnings, summary=summary)


HANDLERS: dict[str, Callable[[NativeContext], NativeResult]] = {
    "setup": setup,
    "narration_silent": narration_silent,
    "narration_user": narration_user,
    "preview": _media("preview", heavy=True),
    "sound": _media("mix", heavy=False),
    "render": _media("render_final", heavy=True),
    "captions": captions,
    "qa": qa,
    "export": _media("export", heavy=False, needs_title=True),
}


def handler_for(stage: Any) -> Callable[[NativeContext], NativeResult]:
    name = (getattr(stage, "params", None) or {}).get("handler") or stage.key
    h = HANDLERS.get(name)
    if h is None:
        raise NativeError(f"no Python handler for stage {stage.key!r}")
    return h


def run_native(ctx: NativeContext) -> NativeResult:
    try:
        return handler_for(ctx.stage)(ctx)
    except NativeError as e:
        return NativeResult(failed=True, error=str(e))
