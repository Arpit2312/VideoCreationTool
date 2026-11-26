"""Engine-internal gates: detect() -> awaiting_approval + pending_gate; approve resumes the same lane session
with adapter.approval_resume_prompt() and re-detects instead of completing directly."""
import json
from pathlib import Path

from conftest import ALL_CAPS, SMALL_CONFIG, StubAdapter, StubRegistry, stage_of, wait_for, wait_stage
from spacestudio.engines.base import DetectedStage
from spacestudio.workflow.stages import StageSpec


class GateAdapter(StubAdapter):
    resume_prompts: list = []

    def detect(self, ws, stage):
        f = Path(ws) / ".spacestudio" / "events.jsonl"
        done = sum(1 for ln in f.read_text().splitlines()
                   if json.loads(ln).get("stage") == stage.key and json.loads(ln)["type"] == "stage_completed")
        if done < 2:
            return DetectedStage(stage.key, "awaiting_approval", detail="edit checkpoint awaiting human",
                                 pending_gate="edit", last_activity="checkpoint_edit awaiting_human")
        return DetectedStage(stage.key, "completed", outputs=list(stage.outputs))

    def approval_resume_prompt(self, stage, om_stage=None, feedback=None, workspace=None):
        GateAdapter.resume_prompts.append((om_stage, feedback))
        return f"The user approved `{om_stage}` in SpaceStudio. Continue stage {stage.key}."


def test_pending_gate_resumes_same_session(make_client):
    native = [StageSpec("film", "Film", "native", "native_pipeline", owner="gated", lane="engine:gated",
                        outputs=["renders/final.mp4"])]
    reg = StubRegistry([GateAdapter("gated", ALL_CAPS, native=native)])
    c = make_client(reg)
    r = c.post("/api/videos", json={"title": "Gate", "idea": "gates", "mode": "native", "primary_engine": "gated",
                                    "config": SMALL_CONFIG})
    assert r.status_code == 201, r.text
    vid = r.json()["video"]["id"]
    c.post(f"/api/videos/{vid}/start")
    st = wait_stage(c, vid, "film", {"awaiting_approval"})
    assert st["params"]["pending_gate"] == "edit" and st["engine_gate"] is True
    assert st["last_activity"] == "checkpoint_edit awaiting_human"
    c.post(f"/api/videos/{vid}/stages/film/approve")
    st = wait_stage(c, vid, "film", {"completed"})
    assert "pending_gate" not in st["params"]
    assert GateAdapter.resume_prompts == [("edit", None)]
    sd = c.get(f"/api/videos/{vid}/stages/film").json()
    assert len(sd["runs"]) == 2
    sessions = [s for s in c.get(f"/api/videos/{vid}").json()["sessions"] if s["lane"] == "engine:gated"]
    assert len(sessions) == 1 and st["session_id"] == sessions[0]["session_id"]
