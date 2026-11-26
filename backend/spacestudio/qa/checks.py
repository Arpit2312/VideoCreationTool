"""Automated QA checks (docs/PHASE2_SPEC.md §9, BUILD_BRIEF "QA (D)").

``run_all(ws, config, stages, settings) -> list[dict]``; every item is
``{id, name, status: passed|warning|failed|not_run, blocking, detail}``. ``blocking`` is true exactly when
the status is ``failed``. A check whose tool (ffprobe, ffmpeg, a filter) is missing reports ``not_run``
with the reason; nothing is reported ``passed`` without being executed. Results are also written to
``.spacestudio/qa.json``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from ..assembly import ffmpeg as ff
from ..interchange.schema import AssetManifest, FactCheck, NarrationTiming, VideoConfig

QA_FILE = ".spacestudio/qa.json"
EXEMPT_STAGES = {"qa", "export"}
DONE_STAGES = {"completed", "skipped"}
LUFS_TARGET = -14.0
LUFS_TOLERANCE = 2.0
CLIP_TP = -0.5
BLACK_RATIO_WARN = 0.20


def _item(id_: str, name: str, status: str, detail: str) -> dict:
    return {"id": id_, "name": name, "status": status, "blocking": status == "failed", "detail": detail}


def _cfg(config) -> VideoConfig:
    return config if isinstance(config, VideoConfig) else VideoConfig.model_validate(config or {})


def pick_output(ws: Path, cfg: VideoConfig) -> Path:
    """The film QA inspects: captioned render if burn-in is on, else the final render, else latest export."""
    ws = Path(ws)
    cands = []
    if cfg.burn_captions:
        cands.append(ws / "renders" / "final_captioned.mp4")
    cands.append(ws / "renders" / "final.mp4")
    for c in cands:
        if c.exists():
            return c
    outs = sorted((ws / "outputs").glob("*.mp4"), key=lambda p: p.stat().st_mtime) if (ws / "outputs").is_dir() else []
    return outs[-1] if outs else cands[-1]


def _parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    ts = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")
    for b in blocks:
        if not b.strip():
            continue
        lines = b.strip().splitlines()
        idx = 1 if lines and lines[0].strip().isdigit() else 0
        if idx >= len(lines):
            raise ValueError(f"cue {lines[0]!r} has no timestamp line")
        m = ts.search(lines[idx])
        if not m:
            raise ValueError(f"bad timestamp line {lines[idx]!r}")
        g = [int(x) for x in m.groups()]
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        if e < s:
            raise ValueError(f"cue ends before it starts: {lines[idx]!r}")
        cues.append((s, e, "\n".join(lines[idx + 1:])))
    return cues


def _load_json(path: Path, model):
    return model.model_validate(json.loads(path.read_text(encoding="utf-8")))


def run_all(ws: Path, config, stages: list[dict], settings) -> list[dict]:
    ws, cfg = Path(ws), _cfg(config)
    ffmpeg_bin, ffprobe_bin = ff.binaries(settings)
    results: list[dict] = []
    add = results.append

    video = pick_output(ws, cfg)
    rel = video.relative_to(ws).as_posix() if video.is_relative_to(ws) else str(video)
    exists = video.is_file() and video.stat().st_size > 0
    if exists:
        add(_item("output_exists", "Output file exists", "passed", f"{rel} ({video.stat().st_size} bytes)"))
    elif video.is_file():
        add(_item("output_exists", "Output file exists", "failed", f"{rel} is empty; re-run the render stage"))
    else:
        add(_item("output_exists", "Output file exists", "failed", f"{rel} not found; run the render stage"))

    # ---- ffprobe-based checks
    info: Optional[dict] = None
    probe_ids = [("dimensions", "Resolution"), ("aspect", "Aspect ratio"), ("fps", "Frame rate"),
                 ("duration_target", "Duration vs target"), ("duration_narration", "Duration vs narration"),
                 ("audio_stream", "Audio stream present")]
    if not exists:
        add(_item("ffprobe", "ffprobe parses output", "not_run", "no output file to probe"))
        for i, n in probe_ids:
            add(_item(i, n, "not_run", "no output file"))
    else:
        try:
            info = ff.probe(ffprobe_bin, video)
            vs = ff.video_stream(info)
            if vs is None:
                add(_item("ffprobe", "ffprobe parses output", "failed", "ffprobe found no video stream"))
            else:
                add(_item("ffprobe", "ffprobe parses output", "passed",
                          f"{vs.get('codec_name')} {vs.get('width')}x{vs.get('height')}"))
        except ff.ToolMissing as exc:
            add(_item("ffprobe", "ffprobe parses output", "not_run", str(exc)))
        except ff.ToolFailed as exc:
            add(_item("ffprobe", "ffprobe parses output", "failed", f"file is not a readable video: {exc}"))
        if info is None or ff.video_stream(info) is None:
            reason = "ffprobe unavailable" if results[-1]["status"] == "not_run" else "output could not be probed"
            for i, n in probe_ids:
                add(_item(i, n, "not_run", reason))
            info = None
    duration: Optional[float] = None
    if info is not None:
        vs = ff.video_stream(info)
        w, h = int(vs.get("width") or 0), int(vs.get("height") or 0)
        try:
            tw, th = (int(x) for x in cfg.resolution.lower().split("x"))
        except ValueError:
            tw = th = 0
        if not tw:
            add(_item("dimensions", "Resolution", "not_run", f"config.resolution {cfg.resolution!r} is not WxH"))
        elif (w, h) == (tw, th):
            add(_item("dimensions", "Resolution", "passed", f"{w}x{h}"))
        else:
            add(_item("dimensions", "Resolution", "failed", f"output is {w}x{h}, config wants {tw}x{th}"))
        try:
            an, ad = (int(x) for x in cfg.aspect_ratio.split(":"))
            want = an / ad
            have = w / h if h else 0
            dev = abs(have - want) / want
            st = "passed" if dev <= 0.01 else ("warning" if dev <= 0.05 else "failed")
            add(_item("aspect", "Aspect ratio", st, f"output {have:.3f}, config {cfg.aspect_ratio} ({want:.3f})"))
        except (ValueError, ZeroDivisionError):
            add(_item("aspect", "Aspect ratio", "not_run", f"config.aspect_ratio {cfg.aspect_ratio!r} unparsable"))
        fps = ff.parse_rate(vs.get("avg_frame_rate")) or ff.parse_rate(vs.get("r_frame_rate"))
        if fps is None:
            add(_item("fps", "Frame rate", "warning", "ffprobe reported no frame rate"))
        else:
            st = "passed" if abs(fps - cfg.fps) <= 0.05 else "failed"
            add(_item("fps", "Frame rate", st, f"output {fps:.3f} fps, config {cfg.fps}"))
        duration = ff.duration_of(info)
        if duration is None:
            add(_item("duration_target", "Duration vs target", "warning", "ffprobe reported no duration"))
        else:
            target = float(cfg.duration_seconds or 0)
            if target <= 0:
                add(_item("duration_target", "Duration vs target", "not_run", "config.duration_seconds is not set"))
            else:
                signed = (duration - target) / target
                dev = abs(signed)
                st = "passed" if dev <= 0.10 else ("warning" if dev <= 0.35 else "failed")
                add(_item("duration_target", "Duration vs target", st,
                          f"{duration:.2f}s vs target {target:.0f}s ({signed * 100:+.0f}%)"))
        tpath = ws / "narration" / "timing.json"
        if duration is None:
            add(_item("duration_narration", "Duration vs narration", "not_run", "no output duration"))
        elif not tpath.exists():
            add(_item("duration_narration", "Duration vs narration", "not_run", "no narration/timing.json"))
        else:
            try:
                nd = _load_json(tpath, NarrationTiming).duration
                if duration + 0.5 < nd:
                    add(_item("duration_narration", "Duration vs narration", "failed",
                              f"film {duration:.2f}s is shorter than narration {nd:.2f}s; narration is cut off"))
                elif duration > nd + 3.0:
                    add(_item("duration_narration", "Duration vs narration", "warning",
                              f"film {duration:.2f}s runs {duration - nd:.1f}s past the narration ({nd:.2f}s)"))
                else:
                    add(_item("duration_narration", "Duration vs narration", "passed",
                              f"film {duration:.2f}s, narration {nd:.2f}s"))
            except (OSError, json.JSONDecodeError, ValidationError) as exc:
                add(_item("duration_narration", "Duration vs narration", "warning",
                          f"narration/timing.json is invalid: {str(exc).splitlines()[0]}"))
        a = ff.audio_stream(info)
        if a is None:
            add(_item("audio_stream", "Audio stream present", "failed", "the output has no audio stream"))
        else:
            add(_item("audio_stream", "Audio stream present", "passed",
                      f"{a.get('codec_name')} {a.get('sample_rate')} Hz, {a.get('channels')} ch"))

    has_audio = info is not None and ff.audio_stream(info) is not None
    results.append(_loudness(ffmpeg_bin, video, has_audio, info is not None))
    results.append(_black(ffmpeg_bin, video, duration, info is not None))
    results.append(_captions(ws, cfg, duration))
    results.extend(_assets(ws))
    results.append(_factcheck(ws))
    results.append(_stages(stages))

    write_report(ws, video, results)
    return results


def write_report(ws: Path, video: Path, results: list[dict]) -> Path:
    summary = {k: sum(1 for r in results if r["status"] == k) for k in ("passed", "warning", "failed", "not_run")}
    summary["blocking"] = sum(1 for r in results if r["blocking"])
    out = Path(ws) / QA_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        vid = video.relative_to(ws).as_posix()
    except ValueError:
        vid = str(video)
    out.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "video": vid, "summary": summary, "checks": results}, indent=2), encoding="utf-8")
    return out


def _ffmpeg_analysis(ffmpeg_bin: str, filt: str, args: list[str], timeout: float) -> tuple[Optional[str], str]:
    """Run an analysis filter; returns (stderr, '') or (None, not_run reason)."""
    try:
        if not ff.has_filter(ffmpeg_bin, filt):
            return None, f"ffmpeg has no {filt} filter"
        proc = ff.run([ffmpeg_bin, "-hide_banner", "-nostdin", "-nostats", *args, "-f", "null", "-"],
                      timeout=timeout, what=f"ffmpeg {filt}")
        return proc.stderr, ""
    except ff.ToolMissing as exc:
        return None, str(exc)
    except ff.ToolFailed as exc:
        return None, f"{filt} analysis failed: {exc}"


def _loudness(ffmpeg_bin: str, video: Path, has_audio: bool, probed: bool) -> dict:
    name = "Loudness (EBU R128)"
    if not probed:
        return _item("loudness", name, "not_run", "output not probed")
    if not has_audio:
        return _item("loudness", name, "not_run", "no audio stream")
    err, why = _ffmpeg_analysis(ffmpeg_bin, "ebur128", ["-i", str(video), "-map", "0:a:0", "-af",
                                                        "ebur128=peak=true"], 900)
    if err is None:
        return _item("loudness", name, "not_run", why)
    summary = err[err.rfind("Summary:"):] if "Summary:" in err else err
    mi = re.findall(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    mp = re.findall(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)
    if not mi:
        return _item("loudness", name, "not_run", "could not parse the ebur128 summary")
    il = float("-inf") if mi[-1] == "-inf" else float(mi[-1])
    tp = None if not mp else (float("-inf") if mp[-1] == "-inf" else float(mp[-1]))
    tp_s = "n/a" if tp is None else f"{tp:.1f} dBTP"
    detail = f"integrated {il:.1f} LUFS, true peak {tp_s}"
    if tp is not None and tp > CLIP_TP:
        return _item("loudness", name, "failed", f"clipping: {detail} (> {CLIP_TP} dBTP); re-run the sound mix")
    if il == float("-inf") or il < -50:
        return _item("loudness", name, "warning", f"audio is silent or nearly silent: {detail}")
    if abs(il - LUFS_TARGET) > LUFS_TOLERANCE:
        return _item("loudness", name, "warning", f"{detail}; target {LUFS_TARGET} LUFS ±{LUFS_TOLERANCE}")
    return _item("loudness", name, "passed", detail)


def _black(ffmpeg_bin: str, video: Path, duration: Optional[float], probed: bool) -> dict:
    name = "Black frames"
    if not probed or not duration:
        return _item("black_frames", name, "not_run", "output not probed or has no duration")
    err, why = _ffmpeg_analysis(ffmpeg_bin, "blackdetect",
                                ["-i", str(video), "-map", "0:v:0", "-vf",
                                 "scale=320:-2,blackdetect=d=0.1:pix_th=0.10", "-an"], 900)
    if err is None:
        return _item("black_frames", name, "not_run", why)
    total = sum(float(x) for x in re.findall(r"black_duration:\s*([\d.]+)", err))
    ratio = min(1.0, total / duration)
    st = "warning" if ratio > BLACK_RATIO_WARN else "passed"
    return _item("black_frames", name, st, f"{total:.2f}s black of {duration:.2f}s ({ratio * 100:.0f}%)")


def _captions(ws: Path, cfg: VideoConfig, duration: Optional[float]) -> dict:
    name = "Captions"
    if not cfg.captions:
        return _item("captions", name, "passed", "captions disabled in the config; none required")
    lang = cfg.narration_language or cfg.language
    srt = ws / "subtitles" / f"{lang}.srt"
    if not srt.exists():
        others = sorted((ws / "subtitles").glob("*.srt")) if (ws / "subtitles").is_dir() else []
        if not others:
            return _item("captions", name, "failed", "captions are enabled but subtitles/ has no .srt; run captions")
        srt = others[0]
    try:
        cues = _parse_srt(srt.read_text(encoding="utf-8", errors="replace"))
    except ValueError as exc:
        return _item("captions", name, "failed", f"{srt.name} does not parse: {exc}")
    if not cues:
        return _item("captions", name, "failed", f"{srt.name} has no cues")
    last = max(e for _, e, _ in cues)
    notes = [f"{srt.name}: {len(cues)} cues, last ends {last:.2f}s"]
    if duration is not None and last > duration + 0.05:
        return _item("captions", name, "failed", f"{notes[0]}, after the film ends ({duration:.2f}s)")
    missing_langs = [l for l in (cfg.subtitle_languages or []) if not (ws / "subtitles" / f"{l}.srt").exists()]
    if cfg.burn_captions and not (ws / "renders" / "final_captioned.mp4").exists():
        return _item("captions", name, "warning", f"{notes[0]}; burn-in requested but final_captioned.mp4 missing")
    if missing_langs:
        return _item("captions", name, "warning", f"{notes[0]}; missing languages: {', '.join(missing_langs)}")
    if duration is None:
        return _item("captions", name, "passed", f"{notes[0]} (film duration unknown; end not compared)")
    return _item("captions", name, "passed", notes[0])


def _assets(ws: Path) -> list[dict]:
    mf = ws / "assets" / "manifest.json"
    if not mf.exists():
        return [_item("credits", "Credits", "passed", "no asset manifest; no third-party assets declared"),
                _item("asset_licences", "Asset licences", "passed", "no asset manifest"),
                _item("asset_paths", "Asset paths", "passed", "no asset manifest")]
    try:
        manifest = _load_json(mf, AssetManifest)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        msg = f"assets/manifest.json is invalid: {str(exc).splitlines()[0]}"
        return [_item("credits", "Credits", "not_run", msg), _item("asset_licences", "Asset licences", "failed", msg),
                _item("asset_paths", "Asset paths", "not_run", msg)]
    items = [i for i in manifest.items if i.status != "rejected"]
    third = [i for i in items if (i.source or "").lower() not in ("", "user")
             and not (i.source or "").lower().startswith("generated")]
    out = []
    credit_files = [p for p in (ws / "credits").rglob("*") if p.is_file() and p.stat().st_size > 0] \
        if (ws / "credits").is_dir() else []
    credit_files += [p for p in (ws / "outputs").glob("credits*.md") if p.stat().st_size > 0] \
        if (ws / "outputs").is_dir() else []
    if not third:
        out.append(_item("credits", "Credits", "passed", "no third-party assets in the manifest"))
    elif credit_files or all(i.credit for i in third):
        out.append(_item("credits", "Credits", "passed",
                         f"{len(third)} third-party assets; credits from {len(credit_files)} file(s) + manifest"))
    else:
        out.append(_item("credits", "Credits", "failed",
                         f"{len(third)} third-party assets but no credits/ file and no credit lines in the manifest"))

    unlicensed = [i.id for i in items if (i.status == "acquired" or i.path) and not (i.license or "").strip()]
    pending = [i.id for i in items if i.status in ("planned", "missing") and not i.path]
    if unlicensed:
        out.append(_item("asset_licences", "Asset licences", "failed",
                         f"no licence recorded for: {', '.join(unlicensed[:20])}"))
    elif pending:
        out.append(_item("asset_licences", "Asset licences", "warning",
                         f"all acquired assets licensed; not acquired: {', '.join(pending[:20])}"))
    else:
        out.append(_item("asset_licences", "Asset licences", "passed", f"{len(items)} assets, all licensed"))

    broken = []
    base = ws.resolve()
    for i in items:
        if not i.path:
            continue
        p = (base / i.path).resolve()
        if (p != base and base not in p.parents) or not p.exists():
            broken.append(f"{i.id} → {i.path}")
    if broken:
        out.append(_item("asset_paths", "Asset paths", "failed", f"missing or outside workspace: {'; '.join(broken[:20])}"))
    else:
        out.append(_item("asset_paths", "Asset paths", "passed",
                         f"{sum(1 for i in items if i.path)} asset paths resolve"))
    return out


def _factcheck(ws: Path) -> dict:
    name = "Fact-check"
    f = ws / "research" / "factcheck.json"
    if not f.exists():
        return _item("factcheck", name, "warning", "research/factcheck.json not found; claims are unchecked")
    try:
        fc = _load_json(f, FactCheck)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return _item("factcheck", name, "failed", f"factcheck.json is invalid: {str(exc).splitlines()[0]}")
    disputed = [i.claim_id for i in fc.items if i.status == "disputed"]
    open_ = [i.claim_id for i in fc.items if i.status not in ("verified", "corrected", "disputed")]
    if disputed:
        return _item("factcheck", name, "failed",
                     f"disputed claims must be corrected or removed: {', '.join(disputed)}"
                     + (f"; unverified: {', '.join(open_)}" if open_ else ""))
    if open_:
        return _item("factcheck", name, "warning", f"unverified claims: {', '.join(open_)}")
    return _item("factcheck", name, "passed", f"{len(fc.items)} claims verified or corrected")


def _stages(stages: list[dict]) -> dict:
    name = "Stages complete"
    bad, soft = [], []
    for s in stages or []:
        key = str(s.get("key", "?"))
        status = str(s.get("status", "pending"))
        if hasattr(s.get("status"), "value"):
            status = s["status"].value
        if key in EXEMPT_STAGES or status in DONE_STAGES:
            continue
        (bad if s.get("required", True) else soft).append(f"{key} ({status})")
    if bad:
        return _item("stages", name, "failed", f"incomplete required stages: {', '.join(bad)}")
    if soft:
        return _item("stages", name, "warning", f"optional stages not done: {', '.join(soft)}")
    return _item("stages", name, "passed", f"{len(stages or [])} stages checked")
