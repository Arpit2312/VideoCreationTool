"""Resumed Claude sessions report a cumulative total_cost_usd; stages must be charged only the increase."""
from sqlmodel import Session, SQLModel, create_engine

from spacestudio.models import ClaudeSession
from spacestudio.workflow.orchestrator import Orchestrator


def _session():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    return Session(eng)


def test_fresh_session_charges_reported_total():
    with _session() as s:
        assert Orchestrator._stage_cost_delta(s, "v1", "main", None, "sess", 1.03) == 1.03


def test_resumed_session_charges_only_the_increase():
    with _session() as s:
        s.add(ClaudeSession(video_id="v1", lane="main", session_id="sess", total_cost_usd=1.0313377))
        s.commit()
        assert Orchestrator._stage_cost_delta(s, "v1", "main", "sess", "sess", 1.6568573) == round(1.6568573 - 1.0313377, 6)


def test_forked_or_reset_session_falls_back_to_reported():
    with _session() as s:
        s.add(ClaudeSession(video_id="v1", lane="main", session_id="sess", total_cost_usd=2.0))
        s.commit()
        assert Orchestrator._stage_cost_delta(s, "v1", "main", "sess", "other", 0.4) == 0.4   # new session id
        assert Orchestrator._stage_cost_delta(s, "v1", "main", "sess", "sess", 0.5) == 0.5    # total went down
