"""State model transitions (spec §4) and video status derivation."""
import pytest

from spacestudio.models import Stage, Video
from spacestudio.workflow.orchestrator import Orchestrator
from spacestudio.workflow.stages import InvalidTransition, StageStatus as S, transition


@pytest.mark.parametrize("cur,action,new", [
    (S.pending, "enqueue", S.queued), (S.queued, "start", S.running), (S.running, "finish", S.completed),
    (S.running, "gate", S.awaiting_approval), (S.running, "ask", S.needs_input),
    (S.awaiting_approval, "approve", S.completed), (S.awaiting_approval, "request_changes", S.queued),
    (S.awaiting_approval, "reject", S.failed), (S.needs_input, "answer", S.queued), (S.failed, "retry", S.queued),
    (S.running, "interrupt", S.queued), (S.pending, "skip", S.skipped),
])
def test_valid_transitions(cur, action, new):
    assert transition(cur, action) == new


@pytest.mark.parametrize("cur,action", [(S.completed, "approve"), (S.pending, "finish"), (S.skipped, "retry"),
                                        (S.running, "approve"), (S.completed, "skip")])
def test_invalid_transitions(cur, action):
    with pytest.raises(InvalidTransition):
        transition(cur, action)


def _v(status="queued", paused=False):
    return Video(project_id="p", title="t", idea="i", status=status, paused=paused)


def _st(status, key="k"):
    return Stage(video_id="v", key=key, name=key, status=status)


@pytest.mark.parametrize("video,stages,expected", [
    (_v("draft"), [_st("pending")], "draft"),
    (_v(), [_st("completed"), _st("running")], "running"),
    (_v(), [_st("completed"), _st("awaiting_approval")], "awaiting_approval"),
    (_v(), [_st("needs_input"), _st("awaiting_approval")], "needs_input"),
    (_v(), [_st("completed"), _st("skipped")], "completed"),
    (_v(), [_st("failed"), _st("pending")], "failed"),
    (_v(paused=True), [_st("queued")], "paused"),
    (_v(paused=True), [_st("running")], "running"),
    (_v("cancelled"), [_st("running")], "cancelled"),
    (_v("interrupted"), [_st("queued")], "interrupted"),
    (_v(), [_st("completed"), _st("queued")], "queued"),
])
def test_derive_status(video, stages, expected):
    o = Orchestrator.__new__(Orchestrator)
    assert Orchestrator.derive_status(o, video, stages) == expected
