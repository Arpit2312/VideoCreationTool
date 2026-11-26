"""SpaceStudio media pipeline: normalise + concat segments, mix, render, captions, export.

Interface (docs/BUILD_BRIEF.md "Media pipeline (D)"): every step takes ``(ws, config, settings)`` and
returns the list of workspace-relative files it wrote. Failures raise :class:`AssemblyError` with an
actionable message. ``settings`` is any object with ``ffmpeg`` / ``ffprobe`` attributes (defaults
``"ffmpeg"`` / ``"ffprobe"``).

Warning reporting (decision)
----------------------------
Non-fatal problems (missing segment replaced by a slate, gaps, missing filters, silent mix, ...)
do not change the return type. They are reported two ways:

* ``LAST_WARNINGS[str(ws.resolve())]`` holds the warning list of the **most recent** step run on that
  workspace (replaced by each call; ``last_warnings(ws)`` is a convenience accessor).
* ``.spacestudio/assembly_warnings.json`` accumulates ``{step: {"at": iso, "warnings": [...]}}`` so the
  orchestrator / UI can read them after a restart. Written even when the step raises.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import textwrap
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from ..interchange.schema import (AssetManifest, NarrationTiming, SegmentRender, Storyboard,
                                  VideoConfig)
from . import ffmpeg as ff

LAST_WARNINGS: dict[str, list[str]] = {}
WARNINGS_FILE = ".spacestudio/assembly_warnings.json"

PREVIEW_WIDTH = 640
AUDIO_RATE = 48000
LUFS_TARGET = -14.0
TP_TARGET = -1.5
LRA_TARGET = 11.0
CAPTION_WIDTH = 42
CAPTION_MIN_S = 1.0
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


class AssemblyError(RuntimeError):
    """A pipeline step failed; the message says what to do about it."""


def last_warnings(ws: Path) -> list[str]:
    return list(LAST_WARNINGS.get(str(Path(ws).resolve()), []))


# --------------------------------------------------------------------------- internals

class _Step:
    """Collects warnings for one pipeline step and publishes them on exit (even on error)."""

    def __init__(self, ws: Path, name: str, settings):
        self.ws = Path(ws)
        self.name = name
        self.warnings: list[str] = []
        self.ffmpeg, self.ffprobe = ff.binaries(settings)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        LAST_WARNINGS[str(self.ws.resolve())] = list(self.warnings)
        f = self.ws / WARNINGS_FILE
        try:
            data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            if not isinstance(data, dict):
                data = {}
        except (OSError, json.JSONDecodeError):
            data = {}
        data[self.name] = {"at": _now(), "warnings": self.warnings,
                           "error": str(exc) if exc is not None else None}
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass
        if exc_type is not None and issubclass(exc_type, (ff.ToolMissing, ff.ToolFailed)):
            raise AssemblyError(f"{self.name}: {exc}") from exc
        return False

    # tool helpers -----------------------------------------------------
    def run(self, args: list, *, timeout: float = 1800, cwd: Optional[Path] = None, what: str = ""):
        return ff.run([self.ffmpeg, "-hide_banner", "-nostdin", "-y", *args], timeout=timeout, cwd=cwd,
                      what=what or f"ffmpeg ({self.name})")

    def has_filter(self, name: str) -> bool:
        return ff.has_filter(self.ffmpeg, name)

    def has_encoder(self, name: str) -> bool:
        return ff.has_encoder(self.ffmpeg, name)

    def probe(self, path: Path) -> dict:
        return ff.probe(self.ffprobe, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cfg(config) -> VideoConfig:
    if isinstance(config, VideoConfig):
        return config
    return VideoConfig.model_validate(config or {})


def _rel(ws: Path, p: Path) -> str:
    return Path(p).resolve().relative_to(Path(ws).resolve()).as_posix()


def _resolution(cfg: VideoConfig) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[x×:]\s*(\d+)\s*", cfg.resolution or "")
    if not m:
        raise AssemblyError(f"config.resolution {cfg.resolution!r} is not WIDTHxHEIGHT (e.g. 1920x1080)")
    w, h = int(m.group(1)), int(m.group(2))
    if w < 16 or h < 16:
        raise AssemblyError(f"config.resolution {cfg.resolution!r} is too small")
    return w - w % 2, h - h % 2


def _fps(cfg: VideoConfig) -> int:
    if not cfg.fps or cfg.fps <= 0 or cfg.fps > 240:
        raise AssemblyError(f"config.fps {cfg.fps!r} is invalid; use e.g. 24, 25 or 30")
    return int(cfg.fps)


def _tmpdir(ws: Path, name: str) -> Path:
    d = Path(ws) / ".spacestudio" / "tmp" / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(path: Path, model, label: str):
    try:
        return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise AssemblyError(f"{label} ({path.name}) is invalid: {first}. Fix the file or re-run the stage "
                            f"that writes it.") from exc


def _timing(ws: Path) -> Optional[NarrationTiming]:
    p = Path(ws) / "narration" / "timing.json"
    return _load(p, NarrationTiming, "narration timing") if p.exists() else None


def _narration_audio(ws: Path, timing: Optional[NarrationTiming], cfg: VideoConfig) -> Optional[Path]:
    cands = []
    if timing and timing.audio:
        cands.append(timing.audio)
    cands.append("narration/narration.wav")
    if cfg.voiceover == "user" and cfg.user_voice_file:
        cands.append(cfg.user_voice_file)
    for rel in cands:
        try:
            p = _resolve(ws, rel)
        except AssemblyError:
            continue
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def _resolve(ws: Path, rel: str) -> Path:
    base = Path(ws).resolve()
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        raise AssemblyError(f"path escapes the workspace: {rel}")
    return p


# --------------------------------------------------------------------------- timeline

@dataclass
class _Piece:
    label: str                 # segment id or "gap"
    start_frame: int
    frames: int
    source: Optional[Path]     # None → slate
    slate_text: str = ""


@dataclass
class _Timeline:
    pieces: list[_Piece]
    fps: int
    total: float               # seconds

    @property
    def frames(self) -> int:
        return sum(p.frames for p in self.pieces)


def _timeline(ws: Path, cfg: VideoConfig, step: _Step) -> _Timeline:
    ws = Path(ws)
    fps = _fps(cfg)
    sb_path = ws / "storyboard" / "storyboard.json"
    if not sb_path.exists():
        raise AssemblyError("storyboard/storyboard.json is missing; approve the storyboard stage first.")
    sb = _load(sb_path, Storyboard, "storyboard")
    if not sb.segments:
        raise AssemblyError("storyboard.json has no segments; the storyboard stage must assign scenes to segments.")
    timing = _timing(ws)
    line_times = {ln.id: (ln.start, ln.end) for ln in (timing.lines if timing else [])}
    scenes = {s.number: s for s in sb.scenes}

    windows = []  # (start, end, seg_id, video_path or None, order)
    cursor = 0.0
    for order, seg in enumerate(sb.segments):
        seg_scenes = [scenes[n] for n in seg.scene_numbers if n in scenes]
        sb_dur = sum(max(0.0, s.duration_seconds) for s in seg_scenes)
        start = end = None
        render_json = ws / "scenes" / f"{seg.id}.json"
        video = ws / "scenes" / f"{seg.id}.mp4"
        if render_json.exists():
            try:
                rend = _load(render_json, SegmentRender, f"segment render {seg.id}")
                start, end = float(rend.start), float(rend.end)
                if rend.video:
                    try:
                        cand = _resolve(ws, rend.video)
                        if cand.exists():
                            video = cand
                    except AssemblyError:
                        step.warn(f"segment {seg.id}: scenes/{seg.id}.json video path escapes the workspace; ignored")
            except AssemblyError as exc:
                step.warn(str(exc))
        if (start is None or end is None or end <= start) and line_times:
            ts = [line_times[i] for s in seg_scenes for i in s.line_ids if i in line_times]
            if ts:
                start, end = min(t[0] for t in ts), max(t[1] for t in ts)
        if start is None or end is None or end <= start:
            start = cursor
            end = cursor + sb_dur
        if end <= start:
            step.warn(f"segment {seg.id} has no duration (no render metadata, timing or scene durations); skipped")
            continue
        windows.append((start, end, seg.id, video, order))
        cursor = max(cursor, end)

    if not windows:
        raise AssemblyError("no segment has a usable time window; check storyboard scene durations and "
                            "narration/timing.json.")

    windows.sort(key=lambda w: (w[0], w[4]))
    total = max(w[1] for w in windows)
    if timing and timing.duration > total:
        total = timing.duration
    total_frames = max(1, round(total * fps))

    pieces: list[_Piece] = []
    cur = 0
    for start, end, seg_id, video, _ in windows:
        sf, ef = round(start * fps), round(end * fps)
        if sf > cur:
            gap = (sf - cur) / fps
            step.warn(f"gap of {gap:.2f}s before segment {seg_id} filled with a slate")
            pieces.append(_Piece("gap", cur, sf - cur, None, "Gap"))
            cur = sf
        if sf < cur:
            step.warn(f"segment {seg_id} overlaps the previous one by {(cur - sf) / fps:.2f}s; its start is trimmed")
            sf = cur
        if ef <= sf:
            step.warn(f"segment {seg_id} is fully covered by earlier segments; skipped")
            continue
        src = video if video and video.is_file() and video.stat().st_size > 0 else None
        if src is None:
            step.warn(f"segment {seg_id}: scenes/{seg_id}.mp4 is missing; a labelled slate was inserted "
                      f"({(ef - sf) / fps:.2f}s). Re-run stage segment:{seg_id} to replace it.")
        pieces.append(_Piece(seg_id, sf, ef - sf, src, f"Missing segment {seg_id}"))
        cur = ef
    if cur < total_frames:
        if total_frames - cur > 1:
            step.warn(f"segments end {(total_frames - cur) / fps:.2f}s before the narration; tail filled with a slate")
        pieces.append(_Piece("tail", cur, total_frames - cur, None, ""))
    return _Timeline(pieces, fps, total_frames / fps)


# --------------------------------------------------------------------------- video assembly

def _venc(step: _Step, crf: int, preset: str, fps: int, maxrate: Optional[str] = None) -> list[str]:
    if step.has_encoder("libx264"):
        args = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
        if maxrate:
            args += ["-maxrate", maxrate, "-bufsize", maxrate.replace("k", "") + "k"]
    else:
        step.warn("libx264 encoder not available; using mpeg4 (lower quality, larger files)")
        args = ["-c:v", "mpeg4", "-q:v", "3" if crf < 25 else "6", "-pix_fmt", "yuv420p"]
    return args + ["-r", str(fps), "-g", str(fps * 2), "-video_track_timescale", str(fps * 1000)]


def _slate(step: _Step, tmp: Path, name: str, w: int, h: int, fps: int, frames: int, text: str,
           venc: list[str]) -> Path:
    out = tmp / name
    dur = frames / fps + 1
    base = ["-f", "lavfi", "-i", f"color=c=0x101010:s={w}x{h}:r={fps}:d={dur:.3f}"]
    tail = ["-frames:v", str(frames), "-an", *venc, name]
    if text and step.has_filter("drawtext"):
        (tmp / f"{name}.txt").write_text(text, encoding="utf-8")
        size = max(10, h // 14)
        vf = (f"drawtext=textfile={name}.txt:fontcolor=white:fontsize={size}:"
              f"x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p")
        try:
            step.run([*base, "-vf", vf, *tail], cwd=tmp, timeout=600, what="ffmpeg slate")
            return out
        except ff.ToolFailed as exc:
            step.warn(f"drawtext failed ({str(exc).splitlines()[-1][:120]}); slate is unlabelled")
    elif text:
        step.warn("drawtext filter not available; slates are plain colour without a label")
    step.run([*base, "-vf", "format=yuv420p", *tail], cwd=tmp, timeout=600, what="ffmpeg slate")
    return out


def _normalise(step: _Step, tmp: Path, name: str, src: Path, w: int, h: int, fps: int, frames: int,
               venc: list[str], label: str) -> Optional[Path]:
    dur = frames / fps
    try:
        info = step.probe(src)
    except ff.ToolFailed as exc:
        step.warn(f"segment {label}: cannot read {src.name} ({exc}); slate inserted")
        return None
    vs = ff.video_stream(info)
    if vs is None:
        step.warn(f"segment {label}: {src.name} has no video stream; slate inserted")
        return None
    src_dur = ff.duration_of(info)
    if src_dur is not None:
        if src_dur < dur - 0.5:
            step.warn(f"segment {label}: render is {src_dur:.2f}s but its window is {dur:.2f}s; last frame held")
        elif src_dur > dur + 0.5:
            step.warn(f"segment {label}: render is {src_dur:.2f}s but its window is {dur:.2f}s; trimmed")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p,"
          f"tpad=stop_mode=clone:stop_duration={dur + 1:.3f},trim=end_frame={frames},setpts=PTS-STARTPTS")
    step.run(["-i", str(src), "-map", "0:v:0", "-vf", vf, "-frames:v", str(frames), "-an", *venc, name],
             cwd=tmp, what=f"ffmpeg normalise segment {label}")
    return tmp / name


def _assemble_video(ws: Path, cfg: VideoConfig, step: _Step, w: int, h: int, venc: list[str],
                    tmp: Path) -> tuple[Path, _Timeline]:
    tl = _timeline(ws, cfg, step)
    parts = []
    for i, piece in enumerate(tl.pieces):
        name = f"part_{i:03d}.mp4"
        out = None
        if piece.source is not None:
            out = _normalise(step, tmp, name, piece.source, w, h, tl.fps, piece.frames, venc, piece.label)
        if out is None:
            out = _slate(step, tmp, name, w, h, tl.fps, piece.frames, piece.slate_text, venc)
        parts.append(out)
    lst = tmp / "concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    joined = tmp / "joined.mp4"
    step.run(["-f", "concat", "-safe", "0", "-i", lst.name, "-c", "copy", joined.name], cwd=tmp,
             what="ffmpeg concat")
    return joined, tl


def _even(x: float) -> int:
    v = int(round(x))
    return max(2, v - v % 2)


# --------------------------------------------------------------------------- public steps

def preview(ws: Path, config, settings) -> list[str]:
    """renders/preview.mp4: 640 px wide, low bitrate, segments + narration audio."""
    ws, cfg = Path(ws), _cfg(config)
    with _Step(ws, "preview", settings) as step:
        W, H = _resolution(cfg)
        pw = min(PREVIEW_WIDTH, W) - min(PREVIEW_WIDTH, W) % 2
        ph = _even(H * pw / W)
        fps = _fps(cfg)
        tmp = _tmpdir(ws, "preview")
        venc = _venc(step, 30, "veryfast", fps, maxrate="900k")
        joined, tl = _assemble_video(ws, cfg, step, pw, ph, venc, tmp)
        out = ws / "renders" / "preview.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        narr = _narration_audio(ws, _timing(ws), cfg)
        if narr is None:
            step.warn("no narration audio found; preview has a silent soundtrack")
            audio_in = ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
        else:
            audio_in = ["-i", str(narr)]
        step.run(["-i", str(joined), *audio_in, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                  "-af", "apad", "-c:a", "aac", "-b:a", "96k", "-ar", str(AUDIO_RATE), "-ac", "2",
                  "-t", f"{tl.total:.3f}", "-movflags", "+faststart", str(out)], what="ffmpeg preview mux")
        shutil.rmtree(tmp, ignore_errors=True)
        return [_rel(ws, out)]


def _target_duration(ws: Path, cfg: VideoConfig, step: _Step) -> float:
    try:
        return _timeline(ws, cfg, _Step(ws, "_probe", None)).total
    except AssemblyError:
        pass
    timing = _timing(ws)
    if timing and timing.duration > 0:
        return float(timing.duration)
    step.warn(f"no storyboard timeline or narration timing; mix length uses config.duration_seconds "
              f"({cfg.duration_seconds}s)")
    return float(cfg.duration_seconds)


def _loudnorm_json(stderr: str) -> Optional[dict]:
    blocks = re.findall(r"\{[^{}]*\}", stderr or "", re.S)
    for b in reversed(blocks):
        try:
            d = json.loads(b)
            if "input_i" in d:
                return d
        except json.JSONDecodeError:
            continue
    return None


def mix(ws: Path, config, settings) -> list[str]:
    """narration/mix.wav: narration + ducked music + sfx cues, two-pass loudnorm to -14 LUFS / -1.5 dBTP."""
    ws, cfg = Path(ws), _cfg(config)
    with _Step(ws, "mix", settings) as step:
        T = _target_duration(ws, cfg, step)
        if T <= 0:
            raise AssemblyError("mix: target duration is 0 s; check the storyboard and narration timing")
        tmp = _tmpdir(ws, "mix")
        out = ws / "narration" / "mix.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        fmt = f"aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo"

        inputs: list[list[str]] = []
        graph: list[str] = []
        mix_labels: list[str] = []

        narr = _narration_audio(ws, _timing(ws), cfg)
        if narr is not None:
            inputs.append(["-i", str(narr)])
            graph.append(f"[0:a]{fmt},apad,atrim=0:{T:.3f}[narr]")

        music = None
        if cfg.music:
            mdir = ws / "assets" / "music"
            files = sorted(p for p in mdir.glob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXT) \
                if mdir.is_dir() else []
            if files:
                music = files[0]
                if len(files) > 1:
                    step.warn(f"several music files in assets/music; using {music.name}")
            else:
                step.warn("config.music is on but assets/music has no audio file; mixing without music")
        if music is not None:
            idx = len(inputs)
            inputs.append(["-stream_loop", "-1", "-t", f"{T + 1:.3f}", "-i", str(music)])
            fi, fo = min(1.5, T / 4), min(3.0, T / 4)
            gain = -16 if narr is not None else 0
            graph.append(f"[{idx}:a]{fmt},atrim=0:{T:.3f},asetpts=N/SR/TB,volume={gain}dB,"
                         f"afade=t=in:st=0:d={fi:.3f},afade=t=out:st={max(0.0, T - fo):.3f}:d={fo:.3f}[mus]")
            if narr is not None:
                if step.has_filter("sidechaincompress"):
                    graph.append("[narr]asplit=2[narrmix][narrsc]")
                    graph.append("[mus][narrsc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[bed]")
                    mix_labels += ["[narrmix]", "[bed]"]
                else:
                    step.warn("sidechaincompress not available; music is held at a static -10 dB under narration")
                    graph.append("[mus]volume=-10dB[bed]")
                    mix_labels += ["[narr]", "[bed]"]
            else:
                mix_labels.append("[mus]")
        elif narr is not None:
            mix_labels.append("[narr]")

        if cfg.sfx:
            cues_file = ws / "assets" / "sfx" / "cues.json"
            if cues_file.exists():
                try:
                    cues = json.loads(cues_file.read_text(encoding="utf-8"))
                    if isinstance(cues, dict):
                        cues = cues.get("cues", [])
                except (OSError, json.JSONDecodeError) as exc:
                    step.warn(f"assets/sfx/cues.json is not valid JSON ({exc}); sfx skipped")
                    cues = []
                for k, cue in enumerate(cues if isinstance(cues, list) else []):
                    try:
                        t = float(cue.get("t", 0))
                        gain = float(cue.get("gain_db", 0))
                        name = str(cue["file"])
                    except (AttributeError, KeyError, TypeError, ValueError):
                        step.warn(f"sfx cue #{k} malformed (need t, file, gain_db); skipped")
                        continue
                    try:
                        p = _resolve(ws, name if name.startswith("assets/") else f"assets/sfx/{name}")
                    except AssemblyError:
                        step.warn(f"sfx cue #{k}: path {name!r} escapes the workspace; skipped")
                        continue
                    if not p.is_file():
                        step.warn(f"sfx cue #{k}: file {name!r} not found; skipped")
                        continue
                    if t < 0 or t >= T:
                        step.warn(f"sfx cue #{k} at {t:.2f}s is outside the film (0–{T:.2f}s); skipped")
                        continue
                    idx = len(inputs)
                    inputs.append(["-i", str(p)])
                    ms = int(round(t * 1000))
                    graph.append(f"[{idx}:a]{fmt},volume={gain}dB,adelay=delays={ms}:all=1[sfx{k}]")
                    mix_labels.append(f"[sfx{k}]")

        premix = tmp / "premix.wav"
        if not mix_labels:
            step.warn("no narration, music or sfx; the mix is a silent track")
            step.run(["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo", "-t", f"{T:.3f}",
                      "-c:a", "pcm_s16le", str(out)], what="ffmpeg silent mix")
            shutil.rmtree(tmp, ignore_errors=True)
            return [_rel(ws, out)]

        if len(mix_labels) == 1:
            graph.append(f"{mix_labels[0]}apad,atrim=0:{T:.3f}[out]")
        else:
            graph.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:"
                         f"dropout_transition=0:normalize=0,apad,atrim=0:{T:.3f}[out]")
        args = [a for i in inputs for a in i]
        step.run([*args, "-filter_complex", ";".join(graph), "-map", "[out]", "-c:a", "pcm_f32le",
                  "-ar", str(AUDIO_RATE), "-ac", "2", "-t", f"{T:.3f}", str(premix)], what="ffmpeg mix")

        final_args = ["-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2", "-t", f"{T:.3f}", str(out)]
        if step.has_filter("loudnorm"):
            target = f"I={LUFS_TARGET}:TP={TP_TARGET}:LRA={LRA_TARGET}"
            p1 = step.run(["-i", str(premix), "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"],
                          what="ffmpeg loudnorm pass 1")
            meas = _loudnorm_json(p1.stderr)
            silent = meas is None or str(meas.get("input_i")) in ("-inf", "inf") or \
                float(meas.get("input_i", -99)) < -70
            if meas is None:
                step.warn("loudnorm pass 1 gave no measurement; mix written without normalisation")
            elif silent:
                step.warn("the mix is (near) silent; loudness normalisation skipped")
            if silent:
                step.run(["-i", str(premix), *final_args], what="ffmpeg write mix")
            else:
                ln = (f"loudnorm={target}:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
                      f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
                      f"offset={meas['target_offset']}:linear=true:print_format=summary,"
                      f"aresample={AUDIO_RATE}")
                step.run(["-i", str(premix), "-af", ln, *final_args], what="ffmpeg loudnorm pass 2")
        else:
            step.warn("loudnorm filter not available; mix is not normalised to -14 LUFS")
            step.run(["-i", str(premix), "-af", "alimiter=limit=0.84", *final_args], what="ffmpeg write mix")
        shutil.rmtree(tmp, ignore_errors=True)
        return [_rel(ws, out)]


def render_final(ws: Path, config, settings) -> list[str]:
    """renders/final.mp4: full resolution H.264 CRF 18 + AAC from narration/mix.wav, +faststart."""
    ws, cfg = Path(ws), _cfg(config)
    mix_path = ws / "narration" / "mix.wav"
    pre_warn: list[str] = []
    if not mix_path.is_file():
        mix(ws, cfg, settings)
        pre_warn = ["narration/mix.wav was missing; the sound mix was generated automatically"] + \
            [f"mix: {w}" for w in last_warnings(ws)]
    with _Step(ws, "render", settings) as step:
        for w_ in pre_warn:
            step.warn(w_)
        W, H = _resolution(cfg)
        fps = _fps(cfg)
        tmp = _tmpdir(ws, "render")
        venc = _venc(step, 18, "medium", fps)
        joined, tl = _assemble_video(ws, cfg, step, W, H, venc, tmp)
        out = ws / "renders" / "final.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        step.run(["-i", str(joined), "-i", str(mix_path), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                  "-af", "apad", "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE), "-ac", "2",
                  "-t", f"{tl.total:.3f}", "-movflags", "+faststart", str(out)], what="ffmpeg final mux")
        shutil.rmtree(tmp, ignore_errors=True)
        return [_rel(ws, out)]


# --------------------------------------------------------------------------- captions

def _srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_cues(timing: NarrationTiming, width: int = CAPTION_WIDTH, min_s: float = CAPTION_MIN_S
               ) -> list[tuple[float, float, str]]:
    """Split narration lines into cues of at most 2 lines of ~``width`` chars, each ≥ ``min_s``."""
    raw: list[tuple[float, float, str]] = []
    for ln in sorted(timing.lines, key=lambda l: l.start):
        text = " ".join((ln.text or "").split())
        if not text or ln.end <= ln.start:
            continue
        dur = ln.end - ln.start
        wrapped = textwrap.wrap(text, width=width, break_long_words=False) or [text]
        chunks = [wrapped[i:i + 2] for i in range(0, len(wrapped), 2)]
        max_chunks = max(1, int(dur // min_s))
        if len(chunks) > max_chunks:
            w2 = math.ceil(len(text) / (2 * max_chunks))
            wrapped = textwrap.wrap(text, width=max(width, w2), break_long_words=False) or [text]
            chunks = [wrapped[i:i + 2] for i in range(0, len(wrapped), 2)]
        weights = [sum(len(x) for x in c) for c in chunks]
        total_w = sum(weights) or 1
        t = ln.start
        for c, wgt in zip(chunks, weights):
            end = t + dur * wgt / total_w
            raw.append((t, end, "\n".join(c)))
            t = end
    cues = []
    for i, (s, e, txt) in enumerate(raw):
        nxt = raw[i + 1][0] if i + 1 < len(raw) else None
        limit = nxt if nxt is not None else (timing.duration if timing.duration > 0 else math.inf)
        if e - s < min_s:
            e = min(s + min_s, max(limit, e))
        if limit > s:
            e = min(e, limit)
        cues.append((s, e, txt))
    return cues


def write_srt(path: Path, cues: list[tuple[float, float, str]]) -> None:
    body = "".join(f"{i}\n{_srt_time(s)} --> {_srt_time(e)}\n{txt}\n\n" for i, (s, e, txt) in enumerate(cues, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def captions(ws: Path, config, settings) -> list[str]:
    """subtitles/<lang>.srt from narration timing (+ renders/final_captioned.mp4 if burn_captions)."""
    ws, cfg = Path(ws), _cfg(config)
    with _Step(ws, "captions", settings) as step:
        written: list[str] = []
        if not cfg.captions:
            step.warn("captions are disabled in the config; nothing written")
            return written
        timing = _timing(ws)
        if timing is None:
            if cfg.voiceover == "none":
                step.warn("no narration (voiceover = none) so there is no timing to caption from")
                return written
            raise AssemblyError("narration/timing.json is missing; captions are timed from narration. Re-run the "
                                "narration stage, or disable captions.")
        langs = list(dict.fromkeys(cfg.subtitle_languages or [cfg.narration_language]))
        narr_lang = cfg.narration_language or cfg.language
        primary: Optional[Path] = None
        for lang in langs:
            if lang != narr_lang:
                step.warn(f"subtitles in {lang!r} need a translation stage run by Claude; only the narration "
                          f"language ({narr_lang!r}) is generated from timing")
                continue
            cues = build_cues(timing)
            if not cues:
                step.warn("narration timing has no captionable lines")
                continue
            p = ws / "subtitles" / f"{lang}.srt"
            write_srt(p, cues)
            written.append(_rel(ws, p))
            primary = p
        if cfg.burn_captions:
            final = ws / "renders" / "final.mp4"
            if primary is None:
                step.warn("burn_captions is on but no subtitle file was generated; nothing burned in")
            elif not final.is_file():
                raise AssemblyError("renders/final.mp4 is missing; run the render stage before burning captions.")
            elif not step.has_filter("subtitles"):
                step.warn("ffmpeg has no subtitles filter (libass); captions not burned in — the .srt is still "
                          "exported as a sidecar")
            else:
                tmp = _tmpdir(ws, "captions")
                shutil.copyfile(primary, tmp / "captions.srt")
                out = ws / "renders" / "final_captioned.mp4"
                fps = _fps(cfg)
                step.run(["-i", str(final), "-map", "0:v:0", "-map", "0:a?", "-vf", "subtitles=captions.srt",
                          *_venc(step, 18, "medium", fps), "-c:a", "copy", "-movflags", "+faststart", str(out)],
                         cwd=tmp, what="ffmpeg burn captions")
                shutil.rmtree(tmp, ignore_errors=True)
                written.append(_rel(ws, out))
        return written


# --------------------------------------------------------------------------- export

def slugify(title: str) -> str:
    s = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80].strip("-") or "video"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _third_party(item) -> bool:
    src = (item.source or "").lower()
    return bool(src) and src != "user" and not src.startswith("generated")


def _credits_md(ws: Path, title: str) -> str:
    out = [f"# Credits: {title}", ""]
    mf = ws / "assets" / "manifest.json"
    if mf.exists():
        try:
            manifest = AssetManifest.model_validate(json.loads(mf.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError):
            manifest = AssetManifest()
        rows = []
        for it in manifest.items:
            if it.status == "rejected" or not (it.source or it.license or it.credit):
                continue
            parts = [it.credit or it.id]
            if it.source:
                parts.append(f"source: {it.source}" + (f" ({it.source_url})" if it.source_url else ""))
            parts.append(f"licence: {it.license or 'UNKNOWN'}")
            rows.append(f"- {it.kind}: " + "; ".join(parts))
        if rows:
            out += ["## Assets", "", *rows, ""]
    engine_lines = []
    for j in sorted((ws / "scenes").glob("*.json")) if (ws / "scenes").is_dir() else []:
        try:
            r = SegmentRender.model_validate(json.loads(j.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValidationError):
            continue
        engine_lines.append(f"- Segment {r.segment} rendered with {r.engine}")
        engine_lines += [f"  - {c}" for c in r.credits if c.strip()]
    if engine_lines:
        out += ["## Engines", "", *dict.fromkeys(engine_lines), ""]
    cdir = ws / "credits"
    for f in sorted(cdir.rglob("*")) if cdir.is_dir() else []:
        if f.is_file() and f.suffix.lower() in {".md", ".txt", ""}:
            text = f.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                out += [f"## {f.relative_to(cdir).as_posix()}", "", text, ""]
    out += ["---", "Assembled with SpaceStudio.", ""]
    return "\n".join(out)


def export(ws: Path, config, settings, title: str) -> list[str]:
    """outputs/<slug>.mp4 (+ .srt, poster.jpg, credits.md, manifest.json); never overwrites an existing mp4."""
    ws, cfg = Path(ws), _cfg(config)
    with _Step(ws, "export", settings) as step:
        captioned = ws / "renders" / "final_captioned.mp4"
        final = ws / "renders" / "final.mp4"
        src = captioned if cfg.burn_captions and captioned.is_file() else final
        if cfg.burn_captions and not captioned.is_file():
            step.warn("burn_captions is on but renders/final_captioned.mp4 is missing; exporting the uncaptioned render")
        if not src.is_file() or src.stat().st_size == 0:
            raise AssemblyError("renders/final.mp4 is missing; run the render stage before export.")
        outdir = ws / "outputs"
        outdir.mkdir(parents=True, exist_ok=True)
        slug = slugify(title)
        base, suffix = slug, ""
        if (outdir / f"{slug}.mp4").exists():
            n = 2
            while (outdir / f"{slug}-v{n}.mp4").exists():
                n += 1
            base, suffix = f"{slug}-v{n}", f"-v{n}"
            step.warn(f"outputs/{slug}.mp4 already exists; this export is {base}.mp4")
        written: list[Path] = []
        video_out = outdir / f"{base}.mp4"
        shutil.copyfile(src, video_out)
        written.append(video_out)

        subs = sorted((ws / "subtitles").glob("*.srt")) if (ws / "subtitles").is_dir() else []
        narr_lang = cfg.narration_language or cfg.language
        for s in subs:
            lang = s.stem
            dst = outdir / (f"{base}.srt" if lang == narr_lang or len(subs) == 1 else f"{base}.{lang}.srt")
            shutil.copyfile(s, dst)
            written.append(dst)
        if cfg.captions and not subs:
            step.warn("captions are enabled but subtitles/ has no .srt; exported without captions")

        poster = outdir / f"poster{suffix}.jpg"
        try:
            info = step.probe(video_out)
            dur = ff.duration_of(info) or 0.0
            step.run(["-ss", f"{dur / 3:.3f}", "-i", str(video_out), "-frames:v", "1", "-q:v", "2", str(poster)],
                     what="ffmpeg poster")
            written.append(poster)
        except (ff.ToolFailed, ff.ToolMissing) as exc:
            step.warn(f"poster frame not written: {exc}")

        credits = outdir / f"credits{suffix}.md"
        credits.write_text(_credits_md(ws, title), encoding="utf-8")
        written.append(credits)

        manifest_path = outdir / "manifest.json"
        prior = {}
        if manifest_path.exists():
            try:
                prior = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prior = {}
        exports = prior.get("exports", []) if isinstance(prior, dict) else []
        exports.append({
            "at": _now(), "title": title, "slug": slug, "video": video_out.name,
            "source_render": _rel(ws, src), "captions_burned": src == captioned,
            "files": [{"path": _rel(ws, p), "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in written],
            "warnings": list(step.warnings),
        })
        manifest = {"title": title, "slug": slug, "latest": video_out.name, "exports": exports}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        written.append(manifest_path)
        return [_rel(ws, p) for p in written]


ZIP_EXCLUDE_DIRS = {".venv", "node_modules", "__pycache__"}


def export_zip(ws: Path, dest: Path) -> Path:
    """Zip the workspace, excluding work/**/node_modules, .spacestudio/tmp and **/.venv.

    ``dest`` may be a directory (→ ``<dest>/<workspace-name>.zip``) or a .zip file path.
    """
    ws = Path(ws).resolve()
    if not ws.is_dir():
        raise AssemblyError(f"workspace {ws} does not exist")
    dest = Path(dest)
    if dest.is_dir() or (not dest.suffix and not dest.exists()):
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / f"{ws.name}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest_res = dest.resolve()
    tmp_dir = ws / ".spacestudio" / "tmp"
    partial = dest.with_suffix(dest.suffix + ".part")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for root, dirs, files in os.walk(ws, followlinks=False):
            rp = Path(root)
            keep = []
            for d in dirs:
                full = rp / d
                if d in {".venv"} or full == tmp_dir or full.is_symlink():
                    continue
                if d == "node_modules" and "work" in full.relative_to(ws).parts[:1]:
                    continue
                if d == "__pycache__":
                    continue
                keep.append(d)
            dirs[:] = sorted(keep)
            for f in sorted(files):
                p = rp / f
                if p.is_symlink() or not p.is_file():
                    continue
                if p.resolve() in (dest_res, partial.resolve()):
                    continue
                zf.write(p, arcname=f"{ws.name}/{p.relative_to(ws).as_posix()}")
    os.replace(partial, dest)
    return dest
