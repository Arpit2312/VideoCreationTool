"""Collaborative flow with the REAL engine registry/adapters (agent B) and the fake Claude CLI."""
from pathlib import Path

import pytest

from conftest import HAS_PIPELINE, HAS_REGISTRY, stage_of, wait_for, wait_stage
from test_core_api_flow import GATES_BEFORE_SEGMENTS, approve_through, create

ENGINES = Path("~/.spacestudio/engines").expanduser()

pytestmark = pytest.mark.skipif(not HAS_REGISTRY, reason="engines/registry.py (agent B) not present")


@pytest.fixture()
def real_client(make_client, monkeypatch):
    from spacestudio import config
    from spacestudio.engines import registry as regmod

    if not ENGINES.is_dir():
        pytest.skip(f"engine clones not found in {ENGINES}")
    monkeypatch.setenv("SPACESTUDIO_ENGINES_DIR", str(ENGINES))
    config.reload_settings()
    regmod.reset_registry()
    reg = regmod.get_registry()
    if len([a for a in reg.all() if a.supports("scene_render")]) < 2:
        pytest.skip("fewer than two engines with scene_render are registered")
    yield make_client(reg)
    regmod.reset_registry()


def test_real_adapters_to_segments(real_client):
    c = real_client
    d = create(c, title="Real engines")
    vid = d["video"]["id"]
    owners = {s["key"]: s["owner"] for s in d["stages"]}
    assert owners["assets"] != "spacestudio" and owners["narration"] != "spacestudio"
    c.post(f"/api/videos/{vid}/start")
    st = wait_stage(c, vid, "setup", {"completed", "failed"})
    if st["status"] == "failed":
        pytest.skip(f"engine setup on this machine is not ready: {st['error']}")
    approve_through(c, vid, GATES_BEFORE_SEGMENTS)
    segs = [s for s in c.get(f"/api/videos/{vid}").json()["stages"] if s["key"].startswith("segment:")]
    assert len(segs) == 2 and len({s["owner"] for s in segs}) == 2
    for s in segs:
        done = wait_stage(c, vid, s["key"], {"completed", "awaiting_approval", "failed"}, timeout=120)
        assert done["status"] != "failed", done["error"]
    if not HAS_PIPELINE:
        pytest.skip("assembly.pipeline / qa.checks (agent D) not present: stopping after the segments")
    for s in segs:
        if stage_of(c, vid, s["key"])["status"] == "awaiting_approval":
            c.post(f"/api/videos/{vid}/stages/{s['key']}/approve")
    approve_through(c, vid, ["preview"])
    st = wait_stage(c, vid, "export", {"awaiting_approval", "failed"}, timeout=180)
    assert st["status"] == "awaiting_approval", st["error"]
