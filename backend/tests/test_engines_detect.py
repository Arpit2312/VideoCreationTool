"""File-based stage detection on synthetic workspaces (collaborative + native, all three engines)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacestudio.engines.registry import Registry
from spacestudio.workflow.stages import StageSpec

ENGINES_DIR = Path("~/.spacestudio/engines").expanduser()
pytestmark = pytest.mark.skipif(not all((ENGINES_DIR / d).is_dir() for d in ("OpenMontage", "cinematic-skill", "lemo-opuscar")),
                                reason="engine clones not present")
need_ff = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe missing")


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    s = SimpleNamespace(data_dir=tmp_path_factory.mktemp("d"), engines_dir=ENGINES_DIR, engine_paths={},
                        python_bin="python3", render_workers=2, max_render_jobs=1, ffmpeg="ffmpeg", ffprobe="ffprobe")
    r = Registry(s)
    r.load()
    return r


def make_ws(tmp_path, zero_cost=True, resolution="320x240"):
    ws = tmp_path / "v_deadbeef"
    for d in ("scripts", "storyboard", "assets/images", "narration/lines", "scenes", "credits", "work", ".spacestudio"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    (ws / "project.json").write_text(json.dumps({"id": ws.name, "config": {"zero_cost": zero_cost, "resolution": resolution, "fps": 24}}))
    return ws


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def seg_stage(eid, seg="s1", start=0.0, end=2.0):
    return StageSpec(f"segment:{seg}", f"Segment {seg}", "", "scene_render", owner=eid, lane=f"engine:{eid}",
                     params={"segment": seg, "start": start, "end": end})


# ---------------------------------------------------------------- collaborative
def test_assets_detection(registry, tmp_path):
    a = registry.get("cinematic-skill")
    ws = make_ws(tmp_path)
    st = StageSpec("assets", "Assets", "", "asset_acquisition", owner=a.id, lane=f"engine:{a.id}")
    assert a.detect(ws, st).status == "pending"
    man = {"items": [{"id": "A1", "kind": "image", "status": "planned"}, {"id": "A2", "kind": "footage", "status": "planned"}]}
    (ws / "assets/manifest.json").write_text(json.dumps(man))
    assert a.detect(ws, st).status == "running"
    (ws / "assets/images/A1.jpg").write_bytes(b"x" * 100)
    man["items"][0].update(status="acquired", path="assets/images/A1.jpg", license="")
    man["items"][1].update(status="missing", credit="no free footage")
    (ws / "assets/manifest.json").write_text(json.dumps(man))
    d = a.detect(ws, st)
    assert d.status == "failed" and "licence" in d.detail
    man["items"][0]["license"] = "Public domain"
    (ws / "assets/manifest.json").write_text(json.dumps(man))
    d = a.detect(ws, st)
    assert d.status == "completed" and "assets/images/A1.jpg" in d.outputs and "A2" in d.detail
    (ws / "assets/manifest.json").write_text('{"items": [{"id": 1}]}')
    assert a.detect(ws, st).status == "failed"


@need_ff
def test_narration_detection(registry, tmp_path):
    a = registry.get("lemo-opuscar")
    ws = make_ws(tmp_path)
    st = StageSpec("narration", "Narration", "", "narration", owner=a.id, lane=f"engine:{a.id}")
    assert a.detect(ws, st).status == "pending"
    (ws / "scripts/script.json").write_text(json.dumps({"title": "T", "lines": [{"id": "L01", "text": "a"}, {"id": "L02", "text": "b"}]}))
    ff("-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(ws / "narration/narration.wav"))
    assert a.detect(ws, st).status == "running"
    timing = {"duration": 3.0, "engine": a.id, "lines": [{"id": "L01", "start": 0.2, "end": 1.4, "text": "a"}]}
    (ws / "narration/timing.json").write_text(json.dumps(timing))
    d = a.detect(ws, st)
    assert d.status == "failed" and "L02" in d.detail
    timing["lines"].append({"id": "L02", "start": 1.6, "end": 2.8, "text": "b"})
    (ws / "narration/timing.json").write_text(json.dumps(timing))
    assert a.detect(ws, st).status == "completed"
    timing["duration"] = 9.0
    (ws / "narration/timing.json").write_text(json.dumps(timing))
    d = a.detect(ws, st)
    assert d.status == "failed" and "WAV" in d.detail


@need_ff
def test_segment_detection(registry, tmp_path):
    a = registry.get("cinematic-skill")
    ws = make_ws(tmp_path)
    st = seg_stage(a.id, start=1.0, end=3.0)
    assert a.detect(ws, st).status == "pending"
    ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=2", "-pix_fmt", "yuv420p", str(ws / "scenes/s1.mp4"))
    assert a.detect(ws, st).status == "running"
    (ws / "scenes/s1.json").write_text(json.dumps({"segment": "s1", "engine": a.id, "video": "scenes/s1.mp4", "start": 1.0, "end": 3.0}))
    (ws / "credits" / f"{a.id}-s1.md").write_text("# credits\n")
    d = a.detect(ws, st)
    assert d.status == "completed", d.detail
    assert f"credits/{a.id}-s1.md" in d.outputs and a.collect_credits(ws) == [f"credits/{a.id}-s1.md"]
    d = a.detect(ws, seg_stage(a.id, start=0.0, end=5.0))
    assert d.status == "failed" and "expected 5.00s" in d.detail
    ws2 = make_ws(tmp_path / "b", resolution="1920x1080")
    shutil.copytree(ws / "scenes", ws2 / "scenes", dirs_exist_ok=True)
    d = a.detect(ws2, st)
    assert d.status == "failed" and "resolution" in d.detail
    (ws / "scenes/s1.mp4").write_bytes(b"not a video")
    assert a.detect(ws, st).status == "failed"


# ---------------------------------------------------------------- OpenMontage checkpoints
def write_cp(pd: Path, stage: str, status: str, spent: float = 0.0, **extra):
    pd.mkdir(parents=True, exist_ok=True)
    cp = {"version": "1.0", "project_id": pd.name, "pipeline_type": "documentary-montage", "stage": stage,
          "status": status, "timestamp": "2026-11-26T10:00:00Z", "artifacts": {},
          "cost_snapshot": {"total_spent_usd": spent, "total_reserved_usd": 0.0, "budget_remaining_usd": 1.0}}
    cp.update(extra)
    (pd / f"checkpoint_{stage}.json").write_text(json.dumps(cp))


def om_stages(registry):
    return registry.get("openmontage").native_stages({"engine_options": {"openmontage": {"pipeline": "documentary-montage"}}})


def test_openmontage_checkpoint_status_mapping(registry, tmp_path):
    om = registry.get("openmontage")
    ws = make_ws(tmp_path)
    idea, scene_plan, assets, edit, compose = om_stages(registry)
    assert assets.key == "om_assets" and assets.params["om_stage"] == "assets"
    pd = ws / "work" / "openmontage" / om.project_id(ws)
    assert pd.name == "v-deadbeef"
    assert om.detect(ws, idea).status == "pending"
    write_cp(pd, "idea", "awaiting_human", human_approval_required=True)
    d = om.detect(ws, idea)
    assert d.status == "awaiting_approval" and d.outputs == ["work/openmontage/v-deadbeef/checkpoint_idea.json"]
    assert d.pending_gate == "idea"
    write_cp(pd, "idea", "completed", human_approval_required=True, human_approved=True)
    d = om.detect(ws, idea)
    assert d.status == "completed" and d.pending_gate is None
    write_cp(pd, "scene_plan", "in_progress")
    assert om.detect(ws, scene_plan).status == "running"
    write_cp(pd, "assets", "failed", error="clip download timed out")
    d = om.detect(ws, assets)
    assert d.status == "failed" and "clip download timed out" in d.detail
    write_cp(pd, "compose", "completed")
    (pd / "renders").mkdir()
    (pd / "renders/final.mp4").write_bytes(b"x")
    d = om.detect(ws, compose)
    assert d.status == "completed" and "work/openmontage/v-deadbeef/renders/final.mp4" in d.outputs and d.cost_usd == 0.0
    (pd / "events.jsonl").write_text('{"ts": "2026-11-26T10:01:00Z", "tool": "video_compose", "event": "start"}\n'
                                     'garbage\n{"ts": "2026-11-26T10:02:00Z", "tool": "video_compose", "event": "finish", "cost_usd": 0.0}\n')
    d = om.detect(ws, compose)
    assert d.last_activity == "2026-11-26T10:02:00Z video_compose finish"


def test_openmontage_paid_spend_fails_under_zero_cost(registry, tmp_path):
    om = registry.get("openmontage")
    ws = make_ws(tmp_path, zero_cost=True)
    idea, scene_plan, *_ = om_stages(registry)
    pd = ws / "work" / "openmontage" / om.project_id(ws)
    write_cp(pd, "idea", "completed", human_approved=True)
    write_cp(pd, "scene_plan", "awaiting_human", spent=0.42)
    d = om.detect(ws, scene_plan)
    assert d.status == "failed" and "paid provider used" in d.detail and d.cost_usd == pytest.approx(0.42)
    # the earlier stage is also flagged: the run as a whole spent money
    assert om.detect(ws, idea).status == "failed"
    # paid tool event without any checkpoint spend
    ws2 = make_ws(tmp_path / "b")
    pd2 = ws2 / "work" / "openmontage" / om.project_id(ws2)
    write_cp(pd2, "idea", "awaiting_human")
    (pd2 / "events.jsonl").write_text('{"ts": "t", "tool": "flux_image", "event": "finish", "cost_usd": 0.05}\n'
                                      '{"ts": "t", "tool": "fal_x", "event": "finish", "cost_usd": 0.05, "depth": 1}\n')
    d = om.detect(ws2, idea)
    assert d.status == "failed" and d.cost_usd == pytest.approx(0.05)
    # outside zero-cost the spend is reported, not failed
    ws3 = make_ws(tmp_path / "c", zero_cost=False)
    write_cp(ws3 / "work" / "openmontage" / om.project_id(ws3), "scene_plan", "awaiting_human", spent=0.42)
    d = om.detect(ws3, scene_plan)
    assert d.status == "awaiting_approval" and d.cost_usd == pytest.approx(0.42)


@need_ff
def test_openmontage_collaborative_segment_paid(registry, tmp_path):
    om = registry.get("openmontage")
    ws = make_ws(tmp_path)
    st = seg_stage(om.id, start=0.0, end=2.0)
    ff("-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=2", "-pix_fmt", "yuv420p", str(ws / "scenes/s1.mp4"))
    (ws / "scenes/s1.json").write_text(json.dumps({"segment": "s1", "engine": om.id, "video": "scenes/s1.mp4", "start": 0, "end": 2}))
    assert om.detect(ws, st).status == "completed"
    pd = ws / "work" / "openmontage" / om.project_id(ws, st)
    assert pd.name == "v-deadbeef-s1"
    # an internal gate before the segment exists -> awaiting_approval with pending_gate
    ws2 = make_ws(tmp_path / "g")
    pd2 = ws2 / "work" / "openmontage" / om.project_id(ws2, st)
    write_cp(pd2, "research", "completed")
    write_cp(pd2, "proposal", "awaiting_human", human_approval_required=True)
    d = om.detect(ws2, st)
    assert d.status == "awaiting_approval" and d.pending_gate == "proposal" and "proposal" in d.detail
    assert "work/openmontage/v-deadbeef-s1/checkpoint_proposal.json" in d.outputs
    write_cp(pd, "compose", "completed", spent=1.5)
    d = om.detect(ws, st)
    assert d.status == "failed" and "paid provider used" in d.detail


# ---------------------------------------------------------------- native cinematic / lemo
def test_cinematic_native_detection(registry, tmp_path):
    a = registry.get("cinematic-skill")
    ws = make_ws(tmp_path)
    story, images, voice, look, draft, sound, finish = a.native_stages({})
    P = ws / "work" / "cinematic-skill" / "film"
    assert a.detect(ws, story).status == "pending"
    P.mkdir(parents=True)
    (P / "script.json").write_text("{}")
    assert a.detect(ws, story).status == "running"
    (P / "beats.md").write_text("| beat |")
    d = a.detect(ws, story)
    assert d.status == "awaiting_approval" and "work/cinematic-skill/film/beats.md" in d.outputs
    (P / "audio").mkdir()
    (P / "audio/narration.m4a").write_bytes(b"x")
    (P / "audio/timing.json").write_text(json.dumps({"total_duration": 0, "segments": []}))
    assert a.detect(ws, voice).status == "failed"
    (P / "audio/timing.json").write_text(json.dumps({"total_duration": 5.0, "segments": [{"id": 1, "start": 0.3, "end": 2}]}))
    assert a.detect(ws, voice).status == "completed"
    (P / "out").mkdir()
    (P / "out/final.mp4").write_bytes(b"x")
    assert a.detect(ws, finish).status == "completed"
    (P / "credits.md").write_text("credits")
    assert "work/cinematic-skill/film/credits.md" in a.collect_credits(ws)


def test_lemo_native_detection(registry, tmp_path):
    a = registry.get("lemo-opuscar")
    ws = make_ws(tmp_path)
    stages = {s.key: s for s in a.native_stages({})}
    P = ws / "work" / "lemo-opuscar" / "film"
    P.mkdir(parents=True)
    (P / "TREATMENT.md").write_text("# t")
    assert a.detect(ws, stages["treatment"]).status == "completed"
    for f in ("CREDITS", "poster.jpg", "build.sh"):
        (P / f).write_text("x")
    d = a.detect(ws, stages["deliver"])
    assert d.status == "running" and "film.mp4" in d.detail
    (P / "film.mp4").write_bytes(b"x")
    assert a.detect(ws, stages["deliver"]).status == "completed"
    assert "work/lemo-opuscar/film/CREDITS" in a.collect_credits(ws)
