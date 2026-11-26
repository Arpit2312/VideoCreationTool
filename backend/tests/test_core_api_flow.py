"""End-to-end through the API with the fake Claude CLI, manual approvals, to export."""
import json
from pathlib import Path

import pytest

from conftest import HAS_PIPELINE, SMALL_CONFIG, stage_of, wait_for, wait_stage

GATES_BEFORE_SEGMENTS = ["concept", "script", "storyboard", "asset_plan"]


def create(client, title="Moon landing", config=None, **kw):
    r = client.post("/api/videos", json={"title": title, "idea": f"An explainer about {title}",
                                         "config": {**SMALL_CONFIG, **(config or {})}, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def approve_through(client, vid, keys):
    for key in keys:
        wait_stage(client, vid, key, {"awaiting_approval"}, timeout=60)
        r = client.post(f"/api/videos/{vid}/stages/{key}/approve", json={"feedback": "ok"})
        assert r.status_code == 200, r.text


def test_health_and_simulated(make_client):
    c = make_client()
    assert c.get("/api/health").json()["ok"] is True
    rep = c.get("/api/system/health").json()
    assert rep["claude"]["simulated"] is True and rep["claude"]["ok"] is True
    ids = {t["id"] for t in rep["tools"]}
    assert {"ffmpeg", "ffprobe", "node", "python", "git", "disk", "espeak-ng", "piper"} <= ids
    assert {e["engine"] for e in rep["engines"]} == {"cinematic-skill", "lemo-opuscar", "openmontage"}
    engines = c.get("/api/engines").json()
    assert {"id", "name", "capabilities", "options", "cost", "health", "setup_steps", "enabled", "approved"} <= set(engines[0])
    q = c.get("/api/queue").json()
    assert q["claude_slots"]["max"] == 2 and q["render_slots"]["max"] == 1


def test_flow_to_segments(make_client):
    c = make_client()
    d = create(c)
    vid = d["video"]["id"]
    assert d["simulated"] is True and d["video"]["status"] == "draft"
    assert c.post(f"/api/videos/{vid}/start").json()["status"] in ("queued", "running")
    approve_through(c, vid, GATES_BEFORE_SEGMENTS)
    d = c.get(f"/api/videos/{vid}").json()
    seg_keys = [s["key"] for s in d["stages"] if s["key"].startswith("segment:")]
    assert seg_keys == ["segment:s1", "segment:s2"]
    owners = {s["owner"] for s in d["stages"] if s["key"] in seg_keys}
    assert len(owners) == 2, "the fake storyboard assigns two different engines"
    preview = next(s for s in d["stages"] if s["key"] == "preview")
    assert preview["depends_on"] == seg_keys
    for k in seg_keys:
        st = wait_stage(c, vid, k, {"completed"}, timeout=90)
        assert f"scenes/{k.split(':')[1]}.mp4" in st["outputs"]
    ws = Path(d["video"]["workspace"])
    assert (ws / "narration" / "timing.json").exists() and (ws / "scenes" / "s1.json").exists()
    # sessions: one per lane, reused across stages of that lane
    sessions = c.get(f"/api/videos/{vid}").json()["sessions"]
    lanes = {s["lane"] for s in sessions}
    assert "main" in lanes and any(l.startswith("engine:") for l in lanes)
    main = [s for s in sessions if s["lane"] == "main"]
    assert len(main) == 1
    research = stage_of(c, vid, "research")
    concept = stage_of(c, vid, "concept")
    assert research["session_id"] == concept["session_id"] == main[0]["session_id"]
    # stage detail + logs
    sd = c.get(f"/api/videos/{vid}/stages/research").json()
    assert sd["runs"][0]["simulated"] is True and sd["log_tail"]
    assert json.loads(sd["log_tail"][0])["type"] == "system"
    assert c.get(f"/api/videos/{vid}/stages/segment:s1/logs?tail=5").json()["lines"]


@pytest.mark.skipif(not HAS_PIPELINE, reason="assembly.pipeline / qa.checks (agent D) not present")
def test_flow_to_export(make_client):
    c = make_client()
    vid = create(c, title="Export test")["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    approve_through(c, vid, GATES_BEFORE_SEGMENTS + ["preview"])
    st = wait_stage(c, vid, "export", {"awaiting_approval", "failed"}, timeout=180)
    assert st["status"] == "awaiting_approval", st["error"]
    c.post(f"/api/videos/{vid}/stages/export/approve")
    v = wait_for(lambda: (lambda d: d if d["video"]["status"] == "completed" else None)(c.get(f"/api/videos/{vid}").json()),
                 timeout=30, what="video completed")
    ws = Path(v["video"]["workspace"])
    mp4s = list((ws / "outputs").glob("*.mp4"))
    assert mp4s and mp4s[0].stat().st_size > 0
    files = c.get(f"/api/videos/{vid}/files", params={"path": "outputs"}).json()
    vid_entry = next(f for f in files if f["kind"] == "video")
    r = c.get(f"/api/videos/{vid}/file", params={"path": vid_entry["path"]}, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    hist = c.get(f"/api/videos/{vid}/history").json()
    assert any(h["kind"] == "version" for h in hist) and any(h["title"] == "stage.approve" for h in hist)
    z = c.get(f"/api/videos/{vid}/export")
    assert z.status_code == 200 and z.content[:2] == b"PK"
