"""Planner: collaborative DAG, engine choice, voiceover variants, segment expansion, native mode."""
import json

import pytest

from spacestudio.interchange.schema import Storyboard, VideoConfig
from spacestudio.models import Video
from spacestudio.workflow import planner
from conftest import ALL_CAPS, StubAdapter, StubRegistry, stub_registry


def video(**cfg):
    return Video(project_id="p", title="T", idea="I", config=VideoConfig(**cfg).model_dump())


def by_key(specs):
    return {s.key: s for s in specs}


def test_collaborative_backbone_and_owners():
    v = video()
    specs = planner.plan(v, v.config, stub_registry())
    keys = [s.key for s in specs]
    assert keys == ["setup", "research", "factcheck", "concept", "script", "storyboard", "asset_plan", "assets",
                    "narration", "preview", "sound", "render", "captions", "qa", "export"]
    k = by_key(specs)
    assert k["assets"].owner == "cinematic-skill" and k["assets"].lane == "engine:cinematic-skill"
    assert k["narration"].owner == "lemo-opuscar" and k["narration"].depends_on == ["storyboard"]
    assert k["setup"].executor == "python" and k["concept"].gate and k["export"].gate


def test_exclusions_and_disabled_and_voice_engine():
    v = video(excluded_engines=["lemo-opuscar"])
    k = by_key(planner.plan(v, v.config, stub_registry()))
    assert k["narration"].owner == "cinematic-skill"
    v = video()
    k = by_key(planner.plan(v, v.config, stub_registry(), disabled={"cinematic-skill"}))
    assert k["assets"].owner == "lemo-opuscar"
    v = video(voice_engine="cinematic-skill")
    assert by_key(planner.plan(v, v.config, stub_registry()))["narration"].owner == "cinematic-skill"
    v = video(voice_engine="openmontage")
    with pytest.raises(planner.PlanError):
        planner.plan(v, v.config, stub_registry())


def test_voiceover_none_and_user():
    v = video(voiceover="none")
    n = by_key(planner.plan(v, v.config, stub_registry()))["narration"]
    assert n.executor == "python" and n.params["handler"] == "narration_silent"
    v = video(voiceover="user", user_voice_file="assets/user/me.wav")
    n = by_key(planner.plan(v, v.config, stub_registry()))["narration"]
    assert n.owner == "cinematic-skill" and n.params["user_voice_file"] == "assets/user/me.wav"
    reg = StubRegistry([StubAdapter("openmontage", ["scene_render", "asset_acquisition"])])
    n = by_key(planner.plan(v, v.config, reg))["narration"]
    assert n.executor == "python" and n.params["handler"] == "narration_user"


def test_no_asset_engine_falls_back_to_main_lane():
    reg = StubRegistry([StubAdapter("lemo-opuscar", ["narration", "scene_render"])])
    v = video()
    a = by_key(planner.plan(v, v.config, reg))["assets"]
    assert a.owner == "spacestudio" and a.lane == "main" and "note" in a.params


def _storyboard(e1="cinematic-skill", e2="lemo-opuscar"):
    return Storyboard.model_validate({
        "scenes": [{"number": 1, "segment": "s1", "line_ids": ["L01"], "visual": "a", "duration_seconds": 3, "engine": e1},
                   {"number": 2, "segment": "s1", "line_ids": ["L02"], "visual": "b", "duration_seconds": 2, "engine": e1},
                   {"number": 3, "segment": "s2", "line_ids": ["L03"], "visual": "c", "duration_seconds": 5, "engine": e2}],
        "segments": [{"id": "s1", "engine": e1, "scene_numbers": [1, 2], "reason": "archive"},
                     {"id": "s2", "engine": e2, "engine_options": {"style": "whiteboard"}, "scene_numbers": [3],
                      "reason": "drawn"}]})


def test_expand_segments_windows_from_durations_and_timing(tmp_path):
    v = video(engine_options={"lemo-opuscar": {"style": "plain"}})
    specs = planner.expand_segments(v, _storyboard(), stub_registry())
    k = by_key(specs)
    s1, s2 = k["segment:s1"], k["segment:s2"]
    assert s1.lane == "engine:cinematic-skill" and s2.owner == "lemo-opuscar"
    assert s1.depends_on == ["assets", "narration"] and s1.executor == "claude" and s1.capability == "scene_render"
    assert (s1.params["start"], s1.params["end"]) == (0.0, 5.0) and (s2.params["start"], s2.params["end"]) == (5.0, 10.0)
    assert s2.params["engine_options"] == {"style": "plain"}  # user-forced options win
    (tmp_path / "narration").mkdir()
    (tmp_path / "narration" / "timing.json").write_text(json.dumps({"duration": 12.0, "lines": [
        {"id": "L01", "start": 0.2, "end": 3.0, "text": "a"}, {"id": "L02", "start": 3.5, "end": 6.0, "text": "b"},
        {"id": "L03", "start": 6.5, "end": 11.0, "text": "c"}]}))
    k = by_key(planner.expand_segments(v, _storyboard(), stub_registry(), ws=tmp_path))
    assert (k["segment:s1"].params["start"], k["segment:s1"].params["end"]) == (0.0, 6.5)
    assert (k["segment:s2"].params["start"], k["segment:s2"].params["end"]) == (6.5, 12.0)


def test_expand_rejects_unavailable_engine():
    v = video(excluded_engines=["lemo-opuscar"])
    with pytest.raises(planner.PlanError, match="lemo-opuscar"):
        planner.expand_segments(v, _storyboard(), stub_registry())


def test_native_plan():
    v = video()
    v.mode, v.primary_engine = "native", "cinematic-skill"
    specs = planner.plan(v, v.config, stub_registry())
    assert [s.key for s in specs] == ["setup", "film", "qa", "export"]
    k = by_key(specs)
    assert k["film"].depends_on == ["setup"] and k["qa"].depends_on == ["film"] and k["film"].lane.startswith("engine:")
    v.primary_engine = "nope"
    with pytest.raises(planner.PlanError):
        planner.plan(v, v.config, stub_registry())


@pytest.mark.skipif(not __import__("conftest").HAS_REGISTRY, reason="engines/registry.py (agent B) not present")
def test_plan_with_real_registry(env):
    from spacestudio.engines.registry import Registry
    reg = Registry()
    reg.load()
    if not reg.all():
        pytest.skip("no engine manifests found")
    v = video()
    k = by_key(planner.plan(v, v.config, reg))
    assert k["assets"].owner in {a.id for a in reg.all()} | {"spacestudio"}
    assert k["narration"].owner in {a.id for a in reg.all()}
