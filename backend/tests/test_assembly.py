"""Media pipeline tests with tiny lavfi-generated media (fast: ≤3 s, tiny resolutions)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacestudio.assembly import ffmpeg as ff
from spacestudio.assembly import pipeline as pl
from spacestudio.interchange.schema import NarrationLineTiming, NarrationTiming, VideoConfig

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg/ffprobe not installed")

SETTINGS = SimpleNamespace(ffmpeg="ffmpeg", ffprobe="ffprobe")


def gen(args: list[str], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, str(out)], check=True, timeout=60)
    return out


def video(out: Path, size: str, dur: float, rate: int = 30) -> Path:
    return gen(["-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d={dur}", "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast"], out)


def tone(out: Path, dur: float, freq: int = 440, volume: float = 0.3) -> Path:
    return gen(["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate=48000",
                "-af", f"volume={volume}", "-ac", "1"], out)


def probe(p: Path) -> dict:
    return ff.probe("ffprobe", p)


def make_ws(tmp_path: Path, *, seg2: bool = True, narration: bool = True, seg_dur=(1.5, 1.5)) -> Path:
    ws = tmp_path / "v_test"
    for d in ["storyboard", "scenes", "narration", "assets/music", "assets/sfx", "subtitles", "renders",
              "outputs", "credits", ".spacestudio/tmp"]:
        (ws / d).mkdir(parents=True, exist_ok=True)
    d1, d2 = seg_dur
    sb = {
        "scenes": [
            {"number": 1, "segment": "s1", "line_ids": ["L01"], "visual": "a", "duration_seconds": d1, "engine": "e1"},
            {"number": 2, "segment": "s2", "line_ids": ["L02"], "visual": "b", "duration_seconds": d2, "engine": "e2"},
        ],
        "segments": [
            {"id": "s1", "engine": "e1", "scene_numbers": [1]},
            {"id": "s2", "engine": "e2", "scene_numbers": [2]},
        ],
    }
    (ws / "storyboard" / "storyboard.json").write_text(json.dumps(sb))
    video(ws / "scenes" / "s1.mp4", "320x240", d1)            # 4:3, will be pillarboxed
    (ws / "scenes" / "s1.json").write_text(json.dumps(
        {"segment": "s1", "engine": "e1", "video": "scenes/s1.mp4", "start": 0, "end": d1,
         "credits": ["Engine one by Someone (MIT)"]}))
    if seg2:
        video(ws / "scenes" / "s2.mp4", "200x200", d2 + 0.8, rate=25)   # square, wrong fps, too long
        (ws / "scenes" / "s2.json").write_text(json.dumps(
            {"segment": "s2", "engine": "e2", "video": "scenes/s2.mp4", "start": d1, "end": d1 + d2}))
    if narration:
        total = d1 + d2
        tone(ws / "narration" / "narration.wav", total, volume=0.2)
        timing = NarrationTiming(duration=total, lines=[
            NarrationLineTiming(id="L01", start=0.0, end=d1, text="Short opening line."),
            NarrationLineTiming(id="L02", start=d1, end=total,
                                text="A much longer second line of narration that certainly needs to be "
                                     "wrapped onto more than one caption line for readability."),
        ])
        (ws / "narration" / "timing.json").write_text(timing.model_dump_json())
    return ws


def cfg(**kw) -> VideoConfig:
    base = dict(resolution="160x90", fps=24, aspect_ratio="16:9", duration_seconds=3, music=False, sfx=False)
    base.update(kw)
    return VideoConfig(**base)


def test_render_two_segments_to_config(tmp_path):
    ws = make_ws(tmp_path)
    c = cfg()
    assert pl.mix(ws, c, SETTINGS) == ["narration/mix.wav"]
    files = pl.render_final(ws, c, SETTINGS)
    assert files == ["renders/final.mp4"]
    info = probe(ws / "renders/final.mp4")
    v = ff.video_stream(info)
    assert (v["width"], v["height"]) == (160, 90)
    assert ff.parse_rate(v["avg_frame_rate"]) == pytest.approx(24, abs=0.01)
    assert v["pix_fmt"] == "yuv420p"
    assert ff.duration_of(info) == pytest.approx(3.0, abs=0.12)
    assert ff.audio_stream(info) is not None
    warns = pl.last_warnings(ws)
    assert any("s2" in w and "trimmed" in w for w in warns)
    saved = json.loads((ws / pl.WARNINGS_FILE).read_text())
    assert "render" in saved and "mix" in saved


def test_preview_is_640_wide_with_audio(tmp_path):
    ws = make_ws(tmp_path)
    c = cfg(resolution="1280x720")
    assert pl.preview(ws, c, SETTINGS) == ["renders/preview.mp4"]
    info = probe(ws / "renders/preview.mp4")
    v = ff.video_stream(info)
    assert (v["width"], v["height"]) == (640, 360)
    assert ff.audio_stream(info) is not None
    assert ff.duration_of(info) == pytest.approx(3.0, abs=0.15)


def test_missing_segment_gets_slate_and_warning(tmp_path):
    ws = make_ws(tmp_path, seg2=False)
    c = cfg()
    pl.preview(ws, c, SETTINGS)
    warns = pl.last_warnings(ws)
    assert any("s2" in w and "slate" in w for w in warns), warns
    info = probe(ws / "renders/preview.mp4")
    assert ff.duration_of(info) == pytest.approx(3.0, abs=0.15)


def test_missing_storyboard_is_actionable(tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()
    with pytest.raises(pl.AssemblyError, match="storyboard"):
        pl.preview(ws, cfg(), SETTINGS)


def test_mix_loudness_near_target_with_music_and_sfx(tmp_path):
    if not ff.has_filter("ffmpeg", "loudnorm") or not ff.has_filter("ffmpeg", "ebur128"):
        pytest.skip("loudnorm/ebur128 not available")
    ws = make_ws(tmp_path)
    tone(ws / "assets/music/bed.wav", 1.0, freq=220, volume=0.5)   # shorter than the film → looped
    tone(ws / "assets/sfx/ding.wav", 0.3, freq=1200, volume=0.5)
    (ws / "assets/sfx/cues.json").write_text(json.dumps([{"t": 1.0, "file": "ding.wav", "gain_db": -6},
                                                        {"t": 2.0, "file": "nope.wav", "gain_db": 0}]))
    c = cfg(music=True, sfx=True)
    pl.mix(ws, c, SETTINGS)
    mixwav = ws / "narration/mix.wav"
    info = probe(mixwav)
    assert ff.duration_of(info) == pytest.approx(3.0, abs=0.05)
    assert any("nope.wav" in w for w in pl.last_warnings(ws))
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(mixwav), "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=60).stderr
    il = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])
    assert il == pytest.approx(-14.0, abs=1.5)


def test_silent_mix_when_nothing(tmp_path):
    ws = make_ws(tmp_path, narration=False)
    pl.mix(ws, cfg(), SETTINGS)
    assert (ws / "narration/mix.wav").stat().st_size > 0
    assert any("silent" in w for w in pl.last_warnings(ws))


def test_srt_split_and_timing(tmp_path):
    ws = make_ws(tmp_path, seg_dur=(1.0, 2.0))
    c = cfg(subtitle_languages=["en", "fr"])
    files = pl.captions(ws, c, SETTINGS)
    assert files == ["subtitles/en.srt"]
    assert not (ws / "subtitles/fr.srt").exists()
    assert any("'fr'" in w and "translation" in w for w in pl.last_warnings(ws))
    text = (ws / "subtitles/en.srt").read_text()
    blocks = [b for b in text.strip().split("\n\n") if b]
    assert len(blocks) >= 3          # long line split into several cues
    prev_end = 0.0
    for i, b in enumerate(blocks, 1):
        lines = b.splitlines()
        assert lines[0] == str(i)
        m = re.match(r"(\d\d):(\d\d):(\d\d),(\d{3}) --> (\d\d):(\d\d):(\d\d),(\d{3})$", lines[1])
        assert m
        g = [int(x) for x in m.groups()]
        s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        assert e > s and s >= prev_end - 1e-6 and e <= 3.0 + 1e-6
        prev_end = e
        assert 1 <= len(lines[2:]) <= 2
        assert all(len(x) <= 42 for x in lines[2:])
    assert blocks[0].splitlines()[1] == "00:00:00,000 --> 00:00:01,000"


def test_build_cues_min_duration():
    t = NarrationTiming(duration=5, lines=[NarrationLineTiming(id="a", start=0, end=0.3, text="Hi."),
                                           NarrationLineTiming(id="b", start=3, end=4, text="There.")])
    cues = pl.build_cues(t)
    assert cues[0][1] - cues[0][0] == pytest.approx(1.0)
    assert cues[1] == (3, 4, "There.")


def test_burn_captions(tmp_path):
    if not ff.has_filter("ffmpeg", "subtitles"):
        pytest.skip("libass subtitles filter not available")
    ws = make_ws(tmp_path)
    c = cfg(burn_captions=True)
    pl.mix(ws, c, SETTINGS)
    pl.render_final(ws, c, SETTINGS)
    files = pl.captions(ws, c, SETTINGS)
    assert "renders/final_captioned.mp4" in files
    assert ff.audio_stream(probe(ws / "renders/final_captioned.mp4")) is not None


def test_export_naming_versioning_and_zip(tmp_path):
    ws = make_ws(tmp_path)
    (ws / "assets").mkdir(exist_ok=True)
    (ws / "assets/manifest.json").write_text(json.dumps({"items": [
        {"id": "img1", "kind": "image", "path": None, "source": "wikimedia", "license": "CC BY-SA 4.0",
         "credit": "Photo by A. Person", "status": "planned"}]}))
    (ws / "credits/e1-s1.md").write_text("Engine one credits\n")
    c = cfg()
    pl.mix(ws, c, SETTINGS)
    pl.render_final(ws, c, SETTINGS)
    pl.captions(ws, c, SETTINGS)
    files = pl.export(ws, c, SETTINGS, "Hello, Wörld! Episode 1")
    assert "outputs/hello-world-episode-1.mp4" in files
    assert "outputs/hello-world-episode-1.srt" in files
    assert "outputs/poster.jpg" in files and "outputs/credits.md" in files
    credits = (ws / "outputs/credits.md").read_text()
    assert "Photo by A. Person" in credits and "CC BY-SA 4.0" in credits
    assert "Engine one by Someone (MIT)" in credits and "Engine one credits" in credits
    first = (ws / "outputs/hello-world-episode-1.mp4").read_bytes()
    files2 = pl.export(ws, c, SETTINGS, "Hello, Wörld! Episode 1")
    assert "outputs/hello-world-episode-1-v2.mp4" in files2
    assert (ws / "outputs/hello-world-episode-1.mp4").read_bytes() == first
    m = json.loads((ws / "outputs/manifest.json").read_text())
    assert m["latest"] == "hello-world-episode-1-v2.mp4" and len(m["exports"]) == 2
    f0 = m["exports"][0]["files"][0]
    assert f0["bytes"] > 0 and len(f0["sha256"]) == 64

    (ws / "work/e1/node_modules/x").mkdir(parents=True)
    (ws / "work/e1/node_modules/x/a.js").write_text("x")
    (ws / "work/e1/keep.txt").write_text("k")
    (ws / ".spacestudio/tmp/junk.bin").write_text("j")
    (ws / "work/e1/.venv").mkdir()
    (ws / "work/e1/.venv/pyvenv.cfg").write_text("v")
    z = pl.export_zip(ws, tmp_path / "zips")
    names = zipfile.ZipFile(z).namelist()
    assert "v_test/work/e1/keep.txt" in names
    assert "v_test/outputs/hello-world-episode-1.mp4" in names
    assert not any("node_modules" in n or ".spacestudio/tmp" in n or ".venv" in n for n in names)


def test_export_without_render_is_actionable(tmp_path):
    ws = make_ws(tmp_path)
    with pytest.raises(pl.AssemblyError, match="render stage"):
        pl.export(ws, cfg(), SETTINGS, "x")


def test_bad_ffmpeg_path_is_assembly_error(tmp_path):
    ws = make_ws(tmp_path)
    bad = SimpleNamespace(ffmpeg=str(tmp_path / "no-ffmpeg"), ffprobe="ffprobe")
    with pytest.raises(pl.AssemblyError, match="not found"):
        pl.preview(ws, cfg(), bad)


def test_slugify():
    assert pl.slugify("  ") == "video"
    assert pl.slugify("Ça va? L'été — 2024") == "ca-va-l-ete-2024"
