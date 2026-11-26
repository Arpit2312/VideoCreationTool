"""QA check tests with tiny lavfi-generated renders."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacestudio.assembly import ffmpeg as ff
from spacestudio.interchange.schema import VideoConfig
from spacestudio.qa import checks

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg/ffprobe not installed")

SETTINGS = SimpleNamespace(ffmpeg="ffmpeg", ffprobe="ffprobe")
STAGES_OK = [{"key": "script", "status": "completed"}, {"key": "segment:s1", "status": "completed"},
             {"key": "qa", "status": "running"}, {"key": "export", "status": "pending"}]


def render(ws: Path, *, size="160x90", dur=2.0, audio="quiet", name="renders/final.mp4") -> Path:
    out = ws / name
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["-f", "lavfi", "-i", f"testsrc2=s={size}:r=24:d={dur}"]
    if audio == "quiet":
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}:sample_rate=48000", "-af", "volume=0.15"]
    elif audio == "loud":
        args += ["-f", "lavfi", "-i", f"sine=frequency=997:duration={dur}:sample_rate=48000", "-af", "volume=9.0"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(out)], check=True, timeout=60)
    return out


def srt(ws: Path, end="00:00:01,900"):
    (ws / "subtitles").mkdir(parents=True, exist_ok=True)
    (ws / "subtitles/en.srt").write_text(f"1\n00:00:00,000 --> 00:00:01,000\nHello\n\n2\n00:00:01,000 --> {end}\nWorld\n")


def cfg(**kw) -> VideoConfig:
    base = dict(resolution="160x90", fps=24, aspect_ratio="16:9", duration_seconds=2)
    base.update(kw)
    return VideoConfig(**base)


def by_id(results):
    return {r["id"]: r for r in results}


def good_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "v_qa"
    render(ws)
    srt(ws)
    (ws / "research").mkdir(parents=True)
    (ws / "research/factcheck.json").write_text(json.dumps({"items": [
        {"claim_id": "F1", "claim": "x", "status": "verified"}]}))
    return ws


def test_good_output_passes_and_writes_qa_json(tmp_path):
    ws = good_ws(tmp_path)
    res = checks.run_all(ws, cfg(), STAGES_OK, SETTINGS)
    r = by_id(res)
    for k in ["output_exists", "ffprobe", "dimensions", "aspect", "fps", "duration_target", "audio_stream",
              "captions", "factcheck", "stages", "black_frames", "credits", "asset_licences", "asset_paths"]:
        assert r[k]["status"] == "passed", (k, r[k])
    assert r["loudness"]["status"] in ("passed", "warning")   # quiet tone, not -14 LUFS
    assert all(set(x) == {"id", "name", "status", "blocking", "detail"} for x in res)
    saved = json.loads((ws / ".spacestudio/qa.json").read_text())
    assert saved["checks"] == res and saved["summary"]["failed"] == 0


def test_wrong_dimensions_and_fps_duration(tmp_path):
    ws = good_ws(tmp_path)
    render(ws, size="128x128", dur=2.0)
    r = by_id(checks.run_all(ws, cfg(duration_seconds=10), STAGES_OK, SETTINGS))
    assert r["dimensions"]["status"] == "failed" and r["dimensions"]["blocking"]
    assert r["aspect"]["status"] == "failed"
    assert r["duration_target"]["status"] == "failed"
    r = by_id(checks.run_all(ws, cfg(resolution="128x128", aspect_ratio="1:1", duration_seconds=2),
                             STAGES_OK, SETTINGS))
    assert r["dimensions"]["status"] == "passed"
    assert r["duration_target"]["status"] == "passed"


def test_missing_audio(tmp_path):
    ws = good_ws(tmp_path)
    render(ws, audio=None)
    r = by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))
    assert r["audio_stream"]["status"] == "failed"
    assert r["loudness"]["status"] == "not_run"


def test_clipping_detected(tmp_path):
    if not ff.has_filter("ffmpeg", "ebur128"):
        pytest.skip("ebur128 not available")
    ws = good_ws(tmp_path)
    render(ws, audio="loud")
    r = by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))
    assert r["loudness"]["status"] == "failed" and "clipping" in r["loudness"]["detail"]


def test_missing_and_bad_captions(tmp_path):
    ws = good_ws(tmp_path)
    (ws / "subtitles/en.srt").unlink()
    r = by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))
    assert r["captions"]["status"] == "failed"
    srt(ws, end="00:00:09,000")                      # past the end of a 2 s film
    assert by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))["captions"]["status"] == "failed"
    (ws / "subtitles/en.srt").write_text("1\nnot a timestamp\nHello\n")
    assert by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))["captions"]["status"] == "failed"
    assert by_id(checks.run_all(ws, cfg(captions=False), STAGES_OK, SETTINGS))["captions"]["status"] == "passed"


def test_licence_credits_and_broken_paths(tmp_path):
    ws = good_ws(tmp_path)
    (ws / "assets/images").mkdir(parents=True)
    (ws / "assets/images/a.jpg").write_bytes(b"x")
    (ws / "assets/manifest.json").write_text(json.dumps({"items": [
        {"id": "a", "kind": "image", "path": "assets/images/a.jpg", "source": "wikimedia", "license": "",
         "status": "acquired"},
        {"id": "b", "kind": "image", "path": "assets/images/gone.jpg", "source": "nasa", "license": "PD",
         "status": "acquired"},
    ]}))
    r = by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))
    assert r["asset_licences"]["status"] == "failed" and "a" in r["asset_licences"]["detail"]
    assert r["asset_paths"]["status"] == "failed" and "gone.jpg" in r["asset_paths"]["detail"]
    assert r["credits"]["status"] == "failed"
    (ws / "credits").mkdir()
    (ws / "credits/credits.md").write_text("Photos: Wikimedia, NASA")
    assert by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))["credits"]["status"] == "passed"


def test_factcheck_disputed_is_blocking(tmp_path):
    ws = good_ws(tmp_path)
    (ws / "research/factcheck.json").write_text(json.dumps({"items": [
        {"claim_id": "F1", "claim": "x", "status": "verified"},
        {"claim_id": "F2", "claim": "y", "status": "unverified"}]}))
    r = by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))
    assert r["factcheck"]["status"] == "warning" and not r["factcheck"]["blocking"]
    (ws / "research/factcheck.json").write_text(json.dumps({"items": [
        {"claim_id": "F3", "claim": "z", "status": "disputed"}]}))
    r = by_id(checks.run_all(ws, cfg(), STAGES_OK, SETTINGS))
    assert r["factcheck"]["status"] == "failed" and r["factcheck"]["blocking"]


def test_incomplete_stages(tmp_path):
    ws = good_ws(tmp_path)
    stages = STAGES_OK + [{"key": "segment:s2", "status": "failed"},
                          {"key": "sound", "status": "pending", "required": False}]
    r = by_id(checks.run_all(ws, cfg(), stages, SETTINGS))
    assert r["stages"]["status"] == "failed" and "segment:s2" in r["stages"]["detail"]
    assert "sound" not in r["stages"]["detail"]


def test_not_run_when_ffprobe_missing(tmp_path):
    ws = good_ws(tmp_path)
    bad = SimpleNamespace(ffmpeg=str(tmp_path / "nope-ffmpeg"), ffprobe=str(tmp_path / "nope-ffprobe"))
    r = by_id(checks.run_all(ws, cfg(), STAGES_OK, bad))
    assert r["output_exists"]["status"] == "passed"
    for k in ["ffprobe", "dimensions", "fps", "duration_target", "audio_stream", "loudness", "black_frames"]:
        assert r[k]["status"] == "not_run", (k, r[k])
    assert not any(x["status"] == "passed" for k, x in r.items() if k in {"dimensions", "fps", "loudness"})


def test_missing_output(tmp_path):
    ws = tmp_path / "v_empty"
    ws.mkdir()
    r = by_id(checks.run_all(ws, cfg(), [], SETTINGS))
    assert r["output_exists"]["status"] == "failed" and r["output_exists"]["blocking"]
    assert r["dimensions"]["status"] == "not_run"
    assert (ws / ".spacestudio/qa.json").exists()
