"""Approvals/versioning, needs_input, restart recovery, batch concurrency + isolation, path guard, import."""
import io
import json
import os
from pathlib import Path

import psutil
import pytest

from conftest import SMALL_CONFIG, fake_env, stage_of, wait_for, wait_stage
from test_core_api_flow import approve_through, create


def test_request_changes_versions_and_resets_descendants(make_client):
    c = make_client()
    vid = create(c)["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    approve_through(c, vid, ["concept"])
    wait_stage(c, vid, "script", {"awaiting_approval"})
    r = c.post(f"/api/videos/{vid}/stages/script/request-changes", json={"feedback": "Make the hook punchier"})
    assert r.status_code == 200, r.text
    st = wait_stage(c, vid, "script", {"awaiting_approval"})
    assert st["attempt"] == 2
    ws = Path(c.get(f"/api/videos/{vid}").json()["video"]["workspace"])
    script = json.loads((ws / "scripts" / "script.json").read_text())
    assert script["lines"][0]["text"].startswith("[SIMULATED] Revised")
    old = json.loads((ws / "scripts" / "v1" / "script.json").read_text())
    assert old["lines"][0]["text"] == "[SIMULATED] Hook line."
    sd = c.get(f"/api/videos/{vid}/stages/script").json()
    assert sd["versions"][0]["version"] == 1 and (ws / sd["versions"][0]["snapshot_dir"] / "scripts/script.json").exists()
    assert [a["decision"] for a in sd["approvals"]] == ["request_changes"]
    # the change request reached Claude (logged prompt is in argv; fake read it) and the same session was resumed
    assert len({r_["model"] for r_ in sd["runs"]}) == 1
    # request changes on an already-approved stage resets completed descendants
    approve_through(c, vid, ["script"])
    wait_stage(c, vid, "storyboard", {"awaiting_approval"})
    r = c.post(f"/api/videos/{vid}/stages/concept/request-changes", json={"feedback": "Different angle"})
    assert r.status_code == 200
    d = c.get(f"/api/videos/{vid}").json()
    by = {s["key"]: s for s in d["stages"]}
    assert by["concept"]["status"] in ("queued", "running")
    assert by["script"]["status"] == "pending" and by["storyboard"]["status"] == "pending"
    sv = c.get(f"/api/videos/{vid}/stages/script").json()["versions"]
    assert any("reset" in v["note"] for v in sv)
    # invalid actions are refused with 409
    assert c.post(f"/api/videos/{vid}/stages/research/approve").status_code == 409
    assert c.post(f"/api/videos/{vid}/stages/setup/skip").status_code == 409


def test_needs_input_and_answer(make_client, monkeypatch):
    fake_env(monkeypatch, FAKE_CLAUDE_NEEDS_INPUT="research")
    c = make_client()
    vid = create(c)["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    st = wait_stage(c, vid, "research", {"needs_input"})
    assert "Which angle" in st["question"]
    assert c.get(f"/api/videos/{vid}").json()["video"]["status"] == "needs_input"
    assert c.post(f"/api/videos/{vid}/stages/research/answer", json={"answer": "The engineering angle"}).status_code == 200
    wait_stage(c, vid, "factcheck", {"completed", "running", "queued"})
    assert stage_of(c, vid, "research")["status"] == "completed"


def test_protocol_error_fails_stage_and_retry(make_client, monkeypatch):
    fake_env(monkeypatch, FAKE_CLAUDE_FAIL="research")
    c = make_client()
    vid = create(c)["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    st = wait_stage(c, vid, "research", {"failed"})
    assert "failed on purpose" in st["error"]
    assert c.get(f"/api/videos/{vid}").json()["video"]["status"] == "failed"
    from spacestudio.workflow.orchestrator import get_orchestrator
    get_orchestrator().settings.extra_env = {}
    assert c.post(f"/api/videos/{vid}/stages/research/retry").status_code == 200
    wait_stage(c, vid, "research", {"completed"})


def test_pause_resume_cancel(make_client, monkeypatch):
    c = make_client()
    vid = create(c)["video"]["id"]
    assert c.post(f"/api/videos/{vid}/pause").status_code == 409  # draft
    c.post(f"/api/videos/{vid}/start")
    c.post(f"/api/videos/{vid}/pause")
    wait_for(lambda: c.get(f"/api/videos/{vid}").json()["video"]["status"] == "paused", 30, what="paused")
    c.post(f"/api/videos/{vid}/resume")
    wait_stage(c, vid, "concept", {"awaiting_approval"})
    r = c.post(f"/api/videos/{vid}/cancel")
    assert r.json()["status"] == "cancelled"
    assert all(s["status"] in ("completed", "cancelled") for s in c.get(f"/api/videos/{vid}").json()["stages"])
    assert c.post(f"/api/videos/{vid}/retry").status_code == 200


def test_cancel_running_stage_kills_claude(make_client, monkeypatch):
    fake_env(monkeypatch, FAKE_CLAUDE_SLEEP="60", FAKE_CLAUDE_SLEEP_STAGE="research")
    c = make_client()
    vid = create(c)["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    wait_stage(c, vid, "research", {"running"})
    pid = wait_for(lambda: (c.get(f"/api/videos/{vid}/stages/research").json()["runs"] or [{}])[-1].get("pid"),
                   20, what="pid")
    c.post(f"/api/videos/{vid}/cancel")
    run = wait_for(lambda: (lambda r: r if r["state"] == "cancelled" else None)(
        c.get(f"/api/videos/{vid}/stages/research").json()["runs"][-1]), 20, what="run cancelled")
    assert run["ended_at"]
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_restart_recovery_resumes(make_client):
    from spacestudio.db import session_scope
    from spacestudio.models import Run, Stage, Video
    from sqlmodel import select

    c = make_client()
    vid = create(c)["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    wait_stage(c, vid, "research", {"completed"})
    c.post(f"/api/videos/{vid}/pause")
    wait_for(lambda: stage_of(c, vid, "factcheck")["status"] in ("queued", "completed", "pending"), 30)
    wait_for(lambda: not c.get("/api/queue").json()["running"], 30, what="idle")
    # simulate a crash in the middle of factcheck: stage running, run with a dead pid
    with session_scope() as s:
        st = s.exec(select(Stage).where(Stage.video_id == vid, Stage.key == "factcheck")).first()
        st.status = "running"
        s.add(st)
        s.add(Run(stage_id=st.id, video_id=vid, kind="claude", pid=999999, state="running"))
        v = s.get(Video, vid)
        v.status, v.paused = "running", False
        s.add(v)
    c.__exit__(None, None, None)  # "crash": stop the app (scheduler not running the fake stage)
    c2 = make_client()
    runs = c2.get(f"/api/videos/{vid}/stages/factcheck").json()["runs"]
    assert any(r["state"] == "interrupted" for r in runs)
    st = wait_stage(c2, vid, "factcheck", {"completed"})
    assert st["attempt"] >= 1
    assert any(a["event"] == "app.recovered" for a in c2.get("/api/audit").json())


def test_batch_concurrency_and_isolation(make_client):
    c = make_client()
    items = [{"title": f"Topic {i}", "idea": f"idea {i}", "overrides": {"duration_seconds": 4 + i}} for i in range(3)]
    r = c.post("/api/batches", json={"name": "B1", "shared_config": SMALL_CONFIG, "items": items})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["progress"]["total"] == 3 and b["status"] == "draft"
    seen_max = 0
    c.post(f"/api/batches/{b['id']}/start")
    def all_waiting():
        nonlocal seen_max
        q = c.get("/api/queue").json()
        seen_max = max(seen_max, q["claude_slots"]["used"])
        assert q["claude_slots"]["used"] <= 2
        vids = c.get(f"/api/batches/{b['id']}").json()["videos"]
        return all(stage_of(c, v["id"], "concept")["status"] == "awaiting_approval" for v in vids)
    wait_for(all_waiting, 120, interval=0.05, what="all at concept gate")
    assert seen_max >= 1
    vids = c.get(f"/api/batches/{b['id']}").json()["videos"]
    roots = [Path(v["workspace"]) for v in vids]
    assert len(set(roots)) == 3
    for v, root in zip(vids, roots):
        proj = json.loads((root / "project.json").read_text())
        assert proj["id"] == v["id"] and proj["config"]["duration_seconds"] == v["config"]["duration_seconds"]
        brief = json.loads((root / "brief.json").read_text())
        assert brief["title"] == v["title"]
        evs = [json.loads(x) for x in (root / ".spacestudio" / "events.jsonl").read_text().splitlines()]
        assert evs and all(e["stage"] in ("research", "factcheck", "concept") for e in evs)
        # no other video's id appears in this workspace's logs
        logs = "".join(p.read_text() for p in (root / "logs").glob("*.jsonl"))
        for other in vids:
            if other["id"] != v["id"]:
                assert other["id"] not in logs
    assert c.post(f"/api/batches/{b['id']}/cancel").status_code == 200
    assert c.get(f"/api/batches/{b['id']}").json()["status"] == "cancelled"


def test_path_traversal_refused_and_edit_rules(make_client):
    c = make_client()
    vid = create(c)["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    wait_stage(c, vid, "research", {"completed"})
    for bad in ("../../etc/passwd", "/etc/passwd", "research/../../x"):
        assert c.get(f"/api/videos/{vid}/file", params={"path": bad}).status_code == 403
        assert c.get(f"/api/videos/{vid}/files", params={"path": bad}).status_code == 403
        assert c.put(f"/api/videos/{vid}/file", params={"path": bad}, json={"content": "x"}).status_code == 403
    assert c.put(f"/api/videos/{vid}/file", params={"path": "project.json"}, json={"content": "{}"}).status_code == 403
    assert c.put(f"/api/videos/{vid}/file", params={"path": "research/x.py"}, json={"content": "x"}).status_code == 403
    bad_json = c.put(f"/api/videos/{vid}/file", params={"path": "research/research.json"}, json={"content": "{"})
    assert bad_json.status_code == 422
    wrong = c.put(f"/api/videos/{vid}/file", params={"path": "research/research.json"}, json={"content": "{}"})
    assert wrong.status_code == 422
    ok = c.put(f"/api/videos/{vid}/file", params={"path": "research/notes.md"}, json={"content": "# notes"})
    assert ok.status_code == 200 and ok.json()["kind"] == "text"
    good = json.dumps({"summary": "edited", "key_facts": [], "sources": [], "angles": []})
    r = c.put(f"/api/videos/{vid}/file", params={"path": "research/research.json"}, json={"content": good})
    assert r.status_code == 200
    vers = c.get(f"/api/videos/{vid}/stages/research").json()["versions"]
    assert any("manual edit" in v["note"] for v in vers)
    assert c.get(f"/api/videos/{vid}/file", params={"path": "research/research.json"}).json()["summary"] == "edited"


def test_batch_import_csv_and_txt(make_client):
    c = make_client()
    csv_text = ("title,idea,duration_seconds,captions,platforms,bogus\n"
                "Moon,Moon landing explainer,90,no,youtube;tiktok,1\n"
                ",Mars rovers in two minutes,,,,\n"
                "Bad,Broken row,abc,,,\n")
    r = c.post("/api/batches/import", files={"file": ("ideas.csv", csv_text, "text/csv")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [i["title"] for i in d["items"]] == ["Moon", "Mars rovers in two minutes"]
    assert d["items"][0]["overrides"] == {"duration_seconds": 90, "captions": False, "platforms": ["youtube", "tiktok"]}
    assert any("bogus" in e for e in d["errors"]) and any("row 4" in e for e in d["errors"])
    r = c.post("/api/batches/import", files={"file": ("ideas.txt", "# comment\n1. First idea here\n- Second idea\n\n", "text/plain")})
    assert [i["idea"] for i in r.json()["items"]] == ["First idea here", "Second idea"]


def test_settings_projects_engines_ideas(make_client):
    c = make_client()
    s = c.get("/api/settings").json()
    assert s["max_claude_sessions"] == 2
    r = c.put("/api/settings", json={**s, "max_render_jobs": 3})
    assert r.status_code == 200 and r.json()["max_render_jobs"] == 3
    assert c.put("/api/settings", json={"nonsense": 1}).status_code == 400
    p = c.post("/api/projects", json={"name": "Docs"}).json()
    assert c.get(f"/api/projects/{p['id']}").json()["name"] == "Docs"
    ideas = c.post("/api/ideas/generate", json={"topic": "volcanoes", "count": 3}).json()
    assert ideas["simulated"] and len(ideas["ideas"]) == 3 and ideas["ideas"][0]["title"].startswith("[SIMULATED]")
    assert c.post("/api/engines/lemo-opuscar/setup", json={"step": "hello"}).status_code == 400
    run = c.post("/api/engines/lemo-opuscar/setup", json={"step": "hello", "confirm": True}).json()
    out = wait_for(lambda: (lambda d: d if d["run"]["state"] != "running" else None)(
        c.get(f"/api/engines/lemo-opuscar/setup/{run['id']}").json()), 20, what="setup done")
    assert out["run"]["exit_code"] == 0 and any("hello-from-setup" in ln for ln in out["lines"])
    assert any(a["event"] == "engine.setup_started" for a in c.get("/api/audit").json())
    e = c.patch("/api/engines/openmontage", json={"enabled": False}).json()
    assert e["enabled"] is False
    # disabled engines are not offered to the planner
    vid = create(c, config={"engines": []})["video"]["id"]
    owners = {s_["owner"] for s_ in c.get(f"/api/videos/{vid}").json()["stages"]}
    assert "openmontage" not in owners
    assert c.patch(f"/api/videos/{vid}/config", json={"duration_seconds": 30}).json()["config"]["duration_seconds"] == 30
    assert c.patch(f"/api/videos/{vid}/config", json={"nope": 1}).status_code == 422



def test_denied_command_can_be_allowed_for_the_video(make_client, monkeypatch):
    fake_env(monkeypatch, FAKE_CLAUDE_DENY="research")
    c = make_client()
    vid = create(c)["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    st = wait_stage(c, vid, "research", {"needs_input"})
    denied = st["params"]["permission_denials"]
    assert denied[0]["command"].startswith("wget") and denied[0]["rules"] == ["Bash(wget *)"]
    # explicit confirmation required; blanket rules refused
    assert c.post(f"/api/videos/{vid}/stages/research/allow-tools", json={"rules": ["Bash(wget *)"]}).status_code == 400
    assert c.post(f"/api/videos/{vid}/stages/research/allow-tools",
                  json={"rules": ["Bash(*)"], "confirm": True}).status_code == 400
    r = c.post(f"/api/videos/{vid}/stages/research/allow-tools", json={"rules": ["Bash(wget *)"], "confirm": True})
    assert r.status_code == 200, r.text
    wait_stage(c, vid, "factcheck", {"completed", "running", "queued"})
    assert stage_of(c, vid, "research")["status"] == "completed"
    assert "Bash(wget *)" in c.get(f"/api/videos/{vid}").json()["video"]["config"]["extra_allowed_tools"]


def test_engine_stage_continues_after_restart(make_client):
    """Regression: resuming an engine-owned stage after a restart crashed with
    "'in <string>' requires string as left operand, not NoneType" (no feedback, resume=True)."""
    from spacestudio.db import session_scope
    from spacestudio.models import Run, Stage, Video
    from sqlmodel import select
    from conftest import stub_registry

    reg = stub_registry()
    for a in reg.all():  # real engines' prompts carry no "RESUME" note of their own (the stub's params JSON did)
        a.stage_prompt = (lambda eid: lambda stage, ws, config, feedback=None, resume=False:
                          f"Engine {eid}: run {stage.key}.")(a.id)
    c = make_client(reg)
    vid = create(c, config={"approval_mode": "auto_to_preview"})["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    wait_stage(c, vid, "assets", {"completed"}, timeout=90)
    c.post(f"/api/videos/{vid}/pause")
    wait_for(lambda: not c.get("/api/queue").json()["running"], 60, what="idle")
    seg = next(s for s in c.get(f"/api/videos/{vid}").json()["stages"] if s["key"].startswith("segment:"))
    with session_scope() as s:
        st = s.exec(select(Stage).where(Stage.video_id == vid, Stage.key == seg["key"])).first()
        st.status = "running"
        s.add(st)
        s.add(Run(stage_id=st.id, video_id=vid, kind="claude", pid=999999, state="running"))
        v = s.get(Video, vid)
        v.status, v.paused = "running", False
        s.add(v)
    c.__exit__(None, None, None)
    c2 = make_client(reg)
    st = wait_stage(c2, vid, seg["key"], {"completed", "failed"}, timeout=90)
    assert st["status"] == "completed" and st["error"] is None, st["error"]
