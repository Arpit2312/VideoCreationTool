"""Advance jobs: planning, gates, approvals, retries, verification after runs, restart recovery (spec §4, §7, §8).

The database is the queue. All state mutations go through this class under ``self.lock`` so the scheduler
loop and API threads never interleave a read-modify-write.
"""
from __future__ import annotations

import asyncio
import glob as globlib
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from sqlmodel import Session, select

from .. import workspace
from ..audit import audit
from ..claude import prompts
from ..claude.agents import agent_defs
from ..claude.runner import ClaudeNotFound, ClaudeRunner, RunRequest, RunResult
from ..config import get_settings
from ..db import session_scope
from ..events import publish
from ..interchange import schema as ix
from ..models import Approval, ClaudeSession, Engine, Run, Stage, StageVersion, Video, new_id, now
from ..security import child_env, redact
from . import native, planner
from .stages import DONE, InvalidTransition, StageSpec, StageStatus, JobStatus, transition

log = logging.getLogger("spacestudio.orchestrator")

DEFAULT_TOOLS = [
    "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Task", "TodoWrite",
    "Bash(python3 *)", "Bash(python *)", "Bash(ffmpeg *)", "Bash(ffprobe *)", "Bash(printf *)", "Bash(echo *)",
    "Bash(mkdir *)", "Bash(ls *)", "Bash(cat *)", "Bash(cp *)", "Bash(mv *)", "Bash(test *)", "Bash(wc *)",
    f"Bash({prompts.SLOT_BIN} *)",
]
MAIN_LANE_TOOLS = ["WebSearch", "WebFetch", "Bash(curl *)"]


class OrchestratorError(ValueError):
    """A user-facing error (bad request / conflict)."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Outcome:
    status: str                         # completed | gate | needs_input | failed
    outputs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: Optional[str] = None
    error: Optional[str] = None
    question: Optional[str] = None
    engine_gate: bool = False
    progress: Optional[float] = None
    cost_usd: float = 0.0


def stage_to_spec(st: Stage) -> StageSpec:
    return StageSpec(key=st.key, name=st.name, description=st.description, capability=st.capability,
                     owner=st.owner, executor=st.executor, lane=st.lane, depends_on=list(st.depends_on or []),
                     gate=st.gate, required=st.required, engine_gate=st.engine_gate, inputs=list(st.inputs or []),
                     outputs=list(st.expected_outputs or []), params=dict(st.params or {}))


def video_config(video: Video) -> ix.VideoConfig:
    return ix.VideoConfig.model_validate(video.config or {})


def _expand_outputs(ws: Path, patterns: Iterable[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        if any(c in pat for c in "*?["):
            for p in sorted(globlib.glob(str(ws / pat), recursive=True)):
                rel = os.path.relpath(p, ws)
                if rel not in out and Path(p).is_file():
                    out.append(rel)
        elif (ws / pat).exists() and pat not in out:
            out.append(pat)
    return out


def _missing_outputs(ws: Path, patterns: Iterable[str]) -> list[str]:
    missing = []
    for pat in patterns:
        if any(c in pat for c in "*?["):
            if not globlib.glob(str(ws / pat), recursive=True):
                missing.append(pat)
        elif not (ws / pat).exists() or ((ws / pat).is_file() and (ws / pat).stat().st_size == 0):
            missing.append(pat)
    return missing


def _validation_msg(e: Exception) -> str:
    errs = getattr(e, "errors", None)
    if callable(errs):
        try:
            items = errs()[:5]
            return "; ".join(f"{'.'.join(str(x) for x in it.get('loc', ()))}: {it.get('msg')}" for it in items)
        except Exception:
            pass
    return str(e)[:400]


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


class Orchestrator:
    def __init__(self, settings: Any = None, registry_provider: Optional[Callable[[], Any]] = None,
                 runner: Optional[ClaudeRunner] = None):
        self.settings = settings or get_settings()
        self._registry_provider = registry_provider
        self.runner = runner or ClaudeRunner(self.settings)
        self.lock = threading.RLock()
        self.scheduler: Any = None          # set by Scheduler
        self._activity_ts: dict[str, float] = {}

    # ------------------------------------------------------------------ dependencies
    def registry(self) -> Any:
        if self._registry_provider is not None:
            return self._registry_provider()
        from ..engines.registry import get_registry  # agent B
        return get_registry()

    def registry_or_none(self) -> Any:
        try:
            return self.registry()
        except Exception as e:  # registry missing/broken: report, don't crash the app
            log.warning("engine registry unavailable: %s", e)
            return None

    def disabled_engines(self, s: Session) -> set[str]:
        return {e.id for e in s.exec(select(Engine)).all() if not e.enabled or not e.approved}

    def paid_env_names(self) -> set[str]:
        reg = self.registry_or_none()
        try:
            return set(reg.paid_env_names()) if reg is not None else set()
        except Exception:
            return set()

    def wake(self) -> None:
        if self.scheduler is not None:
            self.scheduler.wake()

    # ------------------------------------------------------------------ events
    def _emit_stage(self, st: Stage) -> None:
        publish("stage.updated", video_id=st.video_id, stage=redact_stage(st))

    def _emit_video(self, v: Video) -> None:
        publish("video.updated", video_id=v.id, video=v.model_dump())

    # ------------------------------------------------------------------ creation / planning
    def build_config(self, *parts: Optional[dict]) -> dict:
        merged: dict[str, Any] = {"zero_cost": bool(getattr(self.settings, "zero_cost_default", True))}
        merged.update(getattr(self.settings, "default_video_config", None) or {})
        for p in parts:
            merged.update(p or {})
        return ix.VideoConfig.model_validate(merged).model_dump()

    def create_video(self, s: Session, *, project_id: str, title: str, idea: str, config: dict,
                     mode: str = "collaborative", primary_engine: Optional[str] = None,
                     batch_id: Optional[str] = None, raise_on_plan_error: bool = True) -> Video:
        if mode not in ("collaborative", "native"):
            raise OrchestratorError("mode must be 'collaborative' or 'native'", 400)
        v = Video(project_id=project_id, batch_id=batch_id, title=title.strip() or "Untitled", idea=idea,
                  config=config, mode=mode, primary_engine=primary_engine, status=JobStatus.draft.value)
        v.workspace = str(Path(self.settings.projects_dir) / v.id)
        try:
            specs = self._plan(s, v)
        except planner.PlanError as e:
            if raise_on_plan_error:
                raise OrchestratorError(str(e), 400) from None
            v.status, v.error, specs = JobStatus.failed.value, str(e), []
        s.add(v)
        self._persist_specs(s, v, specs)
        s.commit()
        audit("video.created", video_id=v.id, title=v.title, mode=mode, batch_id=batch_id)
        return v

    def _plan(self, s: Session, v: Video) -> list[StageSpec]:
        reg = self.registry_or_none()
        if reg is None:
            raise planner.PlanError("the engine registry is not available; check the engines/ manifests")
        return planner.plan(v, video_config(v), reg, disabled=self.disabled_engines(s))

    @staticmethod
    def _spec_to_stage(v: Video, sp: StageSpec, order: int) -> Stage:
        return Stage(video_id=v.id, key=sp.key, name=sp.name, description=sp.description, owner=sp.owner,
                     executor=sp.executor, lane=sp.lane, capability=sp.capability, depends_on=list(sp.depends_on),
                     gate=sp.gate, engine_gate=sp.engine_gate, required=sp.required, order=order,
                     inputs=list(sp.inputs), expected_outputs=list(sp.outputs), params=dict(sp.params),
                     warnings=[sp.params["note"]] if sp.params.get("note") else [])

    def _persist_specs(self, s: Session, v: Video, specs: list[StageSpec]) -> None:
        for i, sp in enumerate(specs):
            s.add(self._spec_to_stage(v, sp, (i + 1) * 100))

    def replan(self, s: Session, v: Video) -> None:
        if v.status != JobStatus.draft.value:
            raise OrchestratorError("the plan can only change while the video is a draft")
        specs = self._plan(s, v)
        for st in self.stages(s, v.id):
            s.delete(st)
        s.flush()
        self._persist_specs(s, v, specs)

    def update_config(self, s: Session, v: Video, patch: dict) -> Video:
        with self.lock:
            sts = self.stages(s, v.id)
            if v.status != JobStatus.draft.value and any(st.status == StageStatus.running.value for st in sts):
                raise OrchestratorError("config can only change while the video is a draft or between stages")
            merged = ix.VideoConfig.model_validate({**(v.config or {}), **patch}).model_dump()
            v.config = merged
            v.updated_at = now()
            if v.status == JobStatus.draft.value:
                try:
                    self.replan(s, v)
                except planner.PlanError as e:
                    raise OrchestratorError(str(e), 400) from None
            s.add(v)
            s.commit()
            audit("video.config_updated", actor="user", video_id=v.id, fields=sorted(patch))
            if v.workspace and (Path(v.workspace) / "project.json").exists():
                self._write_snapshot(v)
            self._emit_video(v)
            return v

    def _write_snapshot(self, v: Video) -> None:
        snap = {"id": v.id, "title": v.title, "idea": v.idea, "mode": v.mode, "primary_engine": v.primary_engine,
                "config": v.config}
        (Path(v.workspace) / "project.json").write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")

    # ------------------------------------------------------------------ queries
    @staticmethod
    def stages(s: Session, video_id: str) -> list[Stage]:
        return list(s.exec(select(Stage).where(Stage.video_id == video_id).order_by(Stage.order)).all())

    @staticmethod
    def get_stage(s: Session, video_id: str, key: str) -> Stage:
        st = s.exec(select(Stage).where(Stage.video_id == video_id, Stage.key == key)).first()
        if st is None:
            raise OrchestratorError(f"stage {key!r} not found", 404)
        return st

    # ------------------------------------------------------------------ status derivation
    def derive_status(self, v: Video, sts: list[Stage]) -> str:
        if v.status == JobStatus.cancelled.value:
            return JobStatus.cancelled.value
        if v.status == JobStatus.draft.value:
            return JobStatus.draft.value
        statuses = [st.status for st in sts]
        if sts and all(StageStatus(x) in DONE for x in statuses):
            return JobStatus.completed.value
        if v.error and not sts:
            return JobStatus.failed.value
        if "running" in statuses:
            return JobStatus.running.value
        if v.paused:
            return JobStatus.paused.value
        if "needs_input" in statuses:
            return JobStatus.needs_input.value
        if "awaiting_approval" in statuses:
            return JobStatus.awaiting_approval.value
        if "failed" in statuses:
            return JobStatus.failed.value
        if v.status == JobStatus.interrupted.value:
            return JobStatus.interrupted.value
        if "cancelled" in statuses and not any(x in ("queued", "pending") for x in statuses):
            return JobStatus.cancelled.value
        return JobStatus.queued.value

    def refresh_video(self, s: Session, v: Video, emit: bool = True) -> Video:
        sts = self.stages(s, v.id)
        new = self.derive_status(v, sts)
        cost = round(sum(st.cost_usd or 0.0 for st in sts), 6)
        if new != v.status or cost != v.cost_usd:
            v.status, v.cost_usd, v.updated_at = new, cost, now()
            s.add(v)
            s.commit()
            if emit:
                self._emit_video(v)
        return v

    # ------------------------------------------------------------------ video actions
    def start_video(self, s: Session, v: Video, actor: str = "user") -> Video:
        with self.lock:
            if v.status not in (JobStatus.draft.value,):
                if v.status in (JobStatus.paused.value,):
                    return self.resume_video(s, v, actor)
                raise OrchestratorError(f"video is {v.status}; only draft videos can be started")
            if not self.stages(s, v.id):
                raise OrchestratorError(v.error or "the video has no stages (planning failed)", 400)
            v.status, v.paused, v.error, v.updated_at = JobStatus.queued.value, False, None, now()
            s.add(v)
            s.commit()
            audit("video.started", actor=actor, video_id=v.id)
            self.refresh_video(s, v)
        self.wake()
        return v

    def pause_video(self, s: Session, v: Video, actor: str = "user") -> Video:
        with self.lock:
            if v.status in (JobStatus.draft.value, JobStatus.cancelled.value, JobStatus.completed.value):
                raise OrchestratorError(f"cannot pause a video that is {v.status}")
            v.paused = True
            s.add(v)
            s.commit()
            audit("video.paused", actor=actor, video_id=v.id)
            self.refresh_video(s, v)
            self._emit_video(v)
        return v

    def resume_video(self, s: Session, v: Video, actor: str = "user") -> Video:
        with self.lock:
            if not v.paused and v.status != JobStatus.interrupted.value:
                raise OrchestratorError("video is not paused")
            v.paused = False
            if v.status in (JobStatus.paused.value, JobStatus.interrupted.value):
                v.status = JobStatus.queued.value
            s.add(v)
            s.commit()
            audit("video.resumed", actor=actor, video_id=v.id)
            self.refresh_video(s, v)
            self._emit_video(v)
        self.wake()
        return v

    def cancel_video(self, s: Session, v: Video, actor: str = "user") -> Video:
        with self.lock:
            if v.status in (JobStatus.cancelled.value, JobStatus.completed.value):
                raise OrchestratorError(f"video is already {v.status}")
            for st in self.stages(s, v.id):
                if st.status == StageStatus.running.value:
                    self._cancel_running(st)
                if StageStatus(st.status) in (StageStatus.pending, StageStatus.queued, StageStatus.running,
                                              StageStatus.awaiting_approval, StageStatus.needs_input):
                    st.status = transition(st.status, "cancel").value
                    s.add(st)
                    self._emit_stage(st)
            v.status, v.paused, v.updated_at = JobStatus.cancelled.value, False, now()
            s.add(v)
            s.commit()
            audit("video.cancelled", actor=actor, video_id=v.id)
            self._emit_video(v)
        return v

    def retry_video(self, s: Session, v: Video, actor: str = "user") -> Video:
        with self.lock:
            sts = self.stages(s, v.id)
            again = [st for st in sts if st.status in (StageStatus.failed.value, StageStatus.cancelled.value)]
            if not again and v.status != JobStatus.cancelled.value:
                raise OrchestratorError("nothing to retry")
            for st in again:
                st.status, st.error = StageStatus.pending.value, None
                s.add(st)
                self._emit_stage(st)
            v.status, v.paused, v.error = JobStatus.queued.value, False, None
            s.add(v)
            s.commit()
            audit("video.retried", actor=actor, video_id=v.id, stages=[st.key for st in again])
            self.refresh_video(s, v)
        self.wake()
        return v

    def _cancel_running(self, st: Stage) -> None:
        self.runner.cancel(st.id)
        if self.scheduler is not None:
            self.scheduler.cancel_task(st.id)

    # ------------------------------------------------------------------ stage actions
    def _video(self, s: Session, st: Stage) -> Video:
        v = s.get(Video, st.video_id)
        if v is None:
            raise OrchestratorError("video not found", 404)
        return v

    def _record(self, s: Session, st: Stage, decision: str, feedback: str = "", actor: str = "user") -> None:
        s.add(Approval(stage_id=st.id, decision=decision, feedback=feedback))
        audit(f"stage.{decision}", actor=actor, video_id=st.video_id, stage=st.key, feedback=feedback[:2000])

    def approve(self, s: Session, st: Stage, actor: str = "user", note: str = "") -> Stage:
        with self.lock:
            v = self._video(s, st)
            if st.status != StageStatus.awaiting_approval.value:
                raise OrchestratorError(f"stage {st.key} is {st.status}, not awaiting approval")
            st.status = transition(st.status, "approve").value
            self._record(s, st, "approve", note, actor)
            self._snapshot(s, st, note="approved" + (f" ({note})" if note else ""), approved=True)
            self._complete(s, v, st)
            s.commit()
            self._after_change(s, v, st)
        self.wake()
        return st

    def request_changes(self, s: Session, st: Stage, feedback: str, actor: str = "user") -> Stage:
        if not (feedback or "").strip():
            raise OrchestratorError("feedback is required", 400)
        with self.lock:
            v = self._video(s, st)
            if v.status == JobStatus.cancelled.value:
                raise OrchestratorError("the video is cancelled; retry it first")
            st.status = transition(st.status, "request_changes").value
            self._snapshot(s, st, note=f"before changes: {feedback[:200]}")
            st.pending_feedback = feedback
            st.params = {**(st.params or {}), "_feedback_kind": "changes"}
            st.error, st.question, st.completed_at = None, None, None
            self._record(s, st, "request_changes", feedback, actor)
            s.add(st)
            self._reset_descendants(s, v, st)
            if v.status in (JobStatus.completed.value,):
                v.status = JobStatus.queued.value
                s.add(v)
            s.commit()
            self._after_change(s, v, st)
        self.wake()
        return st

    def reject(self, s: Session, st: Stage, feedback: str = "", actor: str = "user") -> Stage:
        with self.lock:
            v = self._video(s, st)
            st.status = transition(st.status, "reject").value
            st.error = f"rejected by the user{': ' + feedback if feedback else ''}"
            self._record(s, st, "reject", feedback, actor)
            s.add(st)
            s.commit()
            self._after_change(s, v, st)
        return st

    def retry_stage(self, s: Session, st: Stage, actor: str = "user") -> Stage:
        with self.lock:
            v = self._video(s, st)
            st.status = transition(st.status, "retry").value
            st.error = None
            self._record(s, st, "retry", "", actor)
            if v.status == JobStatus.cancelled.value:
                v.status = JobStatus.queued.value
                for other in self.stages(s, v.id):
                    if other.id != st.id and other.status == StageStatus.cancelled.value:
                        other.status = StageStatus.pending.value
                        s.add(other)
                s.add(v)
            s.add(st)
            s.commit()
            self._after_change(s, v, st)
        self.wake()
        return st

    def skip(self, s: Session, st: Stage, actor: str = "user") -> Stage:
        with self.lock:
            v = self._video(s, st)
            if st.required:
                raise OrchestratorError(f"stage {st.key} is required and cannot be skipped")
            st.status = transition(st.status, "skip").value
            self._record(s, st, "skip", "", actor)
            s.add(st)
            s.commit()
            self._after_change(s, v, st)
        self.wake()
        return st

    def answer(self, s: Session, st: Stage, answer: str, actor: str = "user") -> Stage:
        if not (answer or "").strip():
            raise OrchestratorError("answer is required", 400)
        with self.lock:
            v = self._video(s, st)
            st.status = transition(st.status, "answer").value
            q = st.question or ""
            st.pending_feedback = (f"Question: {q}\nAnswer: {answer}" if q else answer)
            st.params = {**(st.params or {}), "_feedback_kind": "answer"}
            st.question = None
            self._record(s, st, "answer", answer, actor)
            s.add(st)
            s.commit()
            self._after_change(s, v, st)
        self.wake()
        return st

    def _after_change(self, s: Session, v: Video, st: Stage) -> None:
        self._emit_stage(st)
        self.refresh_video(s, v)

    # ------------------------------------------------------------------ versions
    def _snapshot(self, s: Session, st: Stage, note: str, approved: bool = False) -> Optional[StageVersion]:
        v = s.get(Video, st.video_id)
        if v is None or not v.workspace:
            return None
        ws = Path(v.workspace)
        files = [f for f in (st.outputs or []) if (ws / f).is_file()]
        if not files:
            files = [f for f in _expand_outputs(ws, st.expected_outputs or []) if (ws / f).is_file()]
        if not files:
            return None
        prev = s.exec(select(StageVersion).where(StageVersion.stage_id == st.id)).all()
        n = max([pv.version for pv in prev], default=0) + 1
        safe_key = st.key.replace(":", "_")
        snap = ws / ".spacestudio" / "versions" / safe_key / str(n)
        for f in files:
            dst = snap / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ws / f, dst)
        if st.key == "script" and "scripts/script.json" in files:
            # the UI lists scripts/v<n>/script.json for old script versions
            legacy = ws / "scripts" / f"v{n}" / "script.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ws / "scripts" / "script.json", legacy)
        sv = StageVersion(stage_id=st.id, version=n, files=files,
                          snapshot_dir=os.path.relpath(snap, ws), note=note, approved=approved)
        s.add(sv)
        return sv

    def snapshot_file(self, s: Session, v: Video, rel: str, stage_key: Optional[str], note: str) -> Optional[StageVersion]:
        """Snapshot one workspace file (manual edit) as a version of the stage that owns it."""
        ws = Path(v.workspace)
        st = None
        if stage_key:
            st = s.exec(select(Stage).where(Stage.video_id == v.id, Stage.key == stage_key)).first()
        if st is None:
            return None
        prev = s.exec(select(StageVersion).where(StageVersion.stage_id == st.id)).all()
        n = max([pv.version for pv in prev], default=0) + 1
        snap = ws / ".spacestudio" / "versions" / st.key.replace(":", "_") / str(n)
        files = []
        if (ws / rel).is_file():
            (snap / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ws / rel, snap / rel)
            files = [rel]
        if st.key == "script" and rel == "scripts/script.json" and files:
            legacy = ws / "scripts" / f"v{n}" / "script.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ws / rel, legacy)
        sv = StageVersion(stage_id=st.id, version=n, files=files, snapshot_dir=os.path.relpath(snap, ws), note=note)
        s.add(sv)
        return sv

    def _descendants(self, sts: list[Stage], key: str) -> list[Stage]:
        children: dict[str, list[Stage]] = {}
        for st in sts:
            for d in st.depends_on or []:
                children.setdefault(d, []).append(st)
        seen: dict[str, Stage] = {}
        todo = [key]
        while todo:
            k = todo.pop()
            for c in children.get(k, []):
                if c.key not in seen:
                    seen[c.key] = c
                    todo.append(c.key)
        return list(seen.values())

    def _reset_descendants(self, s: Session, v: Video, st: Stage) -> None:
        for d in self._descendants(self.stages(s, v.id), st.key):
            if d.status in (StageStatus.pending.value, StageStatus.skipped.value):
                continue
            if d.status == StageStatus.running.value:
                self._cancel_running(d)
            if d.status in (StageStatus.completed.value, StageStatus.awaiting_approval.value):
                self._snapshot(s, d, note=f"reset: upstream stage {st.key} changed")
            d.status, d.error, d.question, d.completed_at, d.progress = StageStatus.pending.value, None, None, None, None
            s.add(d)
            self._emit_stage(d)

    # ------------------------------------------------------------------ completion hooks
    def _complete(self, s: Session, v: Video, st: Stage) -> None:
        st.completed_at = now()
        st.pending_feedback = None
        st.params = {k: val for k, val in (st.params or {}).items() if k not in ("_feedback_kind", "_resume")}
        s.add(st)
        if st.key == "storyboard" and v.mode == "collaborative":
            try:
                self._expand_segments(s, v)
            except (planner.PlanError, ValueError) as e:
                st.status = StageStatus.failed.value
                st.error = f"could not create the segment stages: {e}"
                st.completed_at = None
                s.add(st)
        if st.key == "assets":
            self._register_assets(s, v, st)

    def _expand_segments(self, s: Session, v: Video) -> None:
        ws = Path(v.workspace)
        sb = ix.validate_file(ws / "storyboard" / "storyboard.json", "Storyboard")
        reg = self.registry()
        specs = planner.expand_segments(v, sb, reg, ws=ws, disabled=self.disabled_engines(s))
        sts = self.stages(s, v.id)
        by_key = {x.key: x for x in sts}
        new_keys = {sp.key for sp in specs}
        for x in sts:
            if x.key.startswith(planner.SEGMENT_PREFIX) and x.key not in new_keys:
                s.delete(x)
        narration = by_key.get("narration")
        base = (narration.order if narration else 1000) + 1
        for i, sp in enumerate(specs):
            existing = by_key.get(sp.key)
            if existing is not None:
                if existing.owner != sp.owner or existing.params.get("engine_options") != sp.params.get("engine_options"):
                    existing.owner, existing.lane, existing.name = sp.owner, sp.lane, sp.name
                existing.params = {**(existing.params or {}), **sp.params}
                existing.description = sp.description
                existing.status = StageStatus.pending.value
                existing.order = base + i
                s.add(existing)
            else:
                s.add(self._spec_to_stage(v, sp, base + i))
        preview = by_key.get("preview")
        if preview is not None:
            preview.depends_on = [sp.key for sp in specs]
            s.add(preview)
        s.flush()
        audit("video.segments_planned", video_id=v.id,
              segments=[{"key": sp.key, "engine": sp.owner, "reason": sp.params.get("reason")} for sp in specs])

    def _register_assets(self, s: Session, v: Video, st: Stage) -> None:
        from ..models import Asset
        ws = Path(v.workspace)
        try:
            man = ix.validate_file(ws / "assets" / "manifest.json", "AssetManifest")
        except Exception:
            return
        known = {a.path for a in s.exec(select(Asset).where(Asset.video_id == v.id)).all()}
        for it in man.items:
            if it.status == "acquired" and it.path and it.path not in known:
                s.add(Asset(video_id=v.id, path=it.path, kind=it.kind, stage_key=st.key, source=it.source,
                            license=it.license, credit=it.credit))

    # ------------------------------------------------------------------ gating
    def should_gate(self, st: Stage, cfg: ix.VideoConfig, sts: list[Stage], engine_gate: bool) -> bool:
        if st.engine_gate or engine_gate:
            return True
        if not st.gate:
            return False
        mode = cfg.approval_mode
        if mode == "manual":
            return True
        if mode == "auto_to_preview":
            preview = next((x for x in sts if x.key == "preview"), None)
            if st.key in ("preview", "export"):
                return True
            if preview is not None:
                return st.order >= preview.order
            return False  # native mode: auto until export
        if mode == "auto_low_risk":
            return st.key not in set(cfg.auto_stages or [])
        return True

    # ------------------------------------------------------------------ scheduling helpers (called by Scheduler)
    def promote(self, s: Session) -> int:
        """pending -> queued for active videos whose dependencies are done."""
        n = 0
        videos = s.exec(select(Video).where(Video.status.notin_([JobStatus.draft.value, JobStatus.cancelled.value]))).all()
        for v in videos:
            if v.paused:
                continue
            sts = self.stages(s, v.id)
            done = {x.key for x in sts if StageStatus(x.status) in DONE}
            for x in sts:
                if x.status == StageStatus.pending.value and all(d in done for d in x.depends_on or []):
                    x.status = transition(x.status, "enqueue").value
                    s.add(x)
                    self._emit_stage(x)
                    n += 1
        if n:
            s.commit()
        return n

    def is_render_heavy(self, st: Stage) -> bool:
        from .stages import RENDER_HEAVY
        if st.capability in RENDER_HEAVY:
            return True
        if st.owner not in ("spacestudio", "") and st.executor == "claude":
            reg = self.registry_or_none()
            try:
                m = reg.get(st.owner).manifest if reg else {}
            except KeyError:
                m = {}
            heavy = ((m or {}).get("concurrency") or {}).get("render_heavy_stages") or []
            return st.key in heavy
        return False

    def engine_cap(self, engine_id: str) -> Optional[int]:
        reg = self.registry_or_none()
        if reg is None:
            return None
        try:
            conc = (reg.get(engine_id).manifest or {}).get("concurrency") or {}
        except KeyError:
            return None
        cap = conc.get("max_jobs") or conc.get("max_parallel")
        return int(cap) if cap else None

    # ------------------------------------------------------------------ execution
    async def execute(self, stage_id: str) -> None:
        """Run one stage that the scheduler already marked running."""
        with session_scope() as s:
            st = s.get(Stage, stage_id)
            if st is None:
                return
            executor = st.executor
        try:
            if executor == "python":
                await self._execute_python(stage_id)
            else:
                await self._execute_claude(stage_id)
        except asyncio.CancelledError:
            self._interrupted(stage_id)
            raise
        except Exception as e:  # never leave a stage stuck in running
            log.exception("stage %s crashed", stage_id)
            with self.lock, session_scope() as s:
                st = s.get(Stage, stage_id)
                if st is not None and st.status == StageStatus.running.value:
                    st.status, st.error = StageStatus.failed.value, redact(f"internal error: {e}")
                    s.add(st)
                    for r in s.exec(select(Run).where(Run.stage_id == stage_id, Run.state == "running")).all():
                        r.state, r.ended_at = "failed", now()
                        s.add(r)
                    s.commit()
                    self._after_change(s, self._video(s, st), st)

    def _interrupted(self, stage_id: str) -> None:
        with self.lock, session_scope() as s:
            for r in s.exec(select(Run).where(Run.stage_id == stage_id, Run.state == "running")).all():
                r.state, r.ended_at = "interrupted", now()
                s.add(r)
            st = s.get(Stage, stage_id)
            if st is not None and st.status == StageStatus.running.value:
                st.status = transition(st.status, "interrupt").value
                st.params = {**(st.params or {}), "_resume": True}
                s.add(st)
                v = s.get(Video, st.video_id)
                if v is not None and v.status not in (JobStatus.cancelled.value,):
                    v.status = JobStatus.interrupted.value
                    s.add(v)
            s.commit()

    def _start_run(self, s: Session, st: Stage, kind: str) -> tuple[Run, Path]:
        v = self._video(s, st)
        ws = Path(v.workspace)
        st.attempt = (st.attempt or 0) + 1
        st.started_at, st.last_activity, st.progress, st.error = now(), None, None, None
        st.question = None
        safe_key = st.key.replace(":", "_")
        log_path = ws / "logs" / f"{safe_key}.{st.attempt}.jsonl"
        r = Run(stage_id=st.id, video_id=st.video_id, kind=kind, log_path=str(log_path),
                simulated=(kind == "claude" and self.runner.is_simulated()))
        s.add(st)
        s.add(r)
        s.commit()
        audit("stage.started", video_id=st.video_id, stage=st.key, attempt=st.attempt, kind=kind,
              simulated=r.simulated)
        self._emit_stage(st)
        return r, log_path

    # ---- python
    async def _execute_python(self, stage_id: str) -> None:
        with self.lock, session_scope() as s:
            st = s.get(Stage, stage_id)
            v = self._video(s, st)
            run, log_path = self._start_run(s, st, "native")
            ws = Path(v.workspace)
            ws.mkdir(parents=True, exist_ok=True)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            cfg = video_config(v)
            sts = self.stages(s, v.id)
            engines_in_use = sorted({x.owner for x in sts if x.owner not in ("spacestudio", "")})
            ctx = native.NativeContext(
                video=Video.model_validate(v.model_dump()), stage=Stage.model_validate(st.model_dump()), ws=ws,
                config=cfg, settings=self.settings, registry=self.registry_or_none(),
                stages=[x.model_dump() for x in sts], engines_in_use=engines_in_use)
            run_id = run.id

        def _log(msg: str) -> None:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "native", "at": now(), "text": redact(msg)}) + "\n")
            with open(ws / "logs" / "native.log", "a", encoding="utf-8") as fh:
                fh.write(f"{now()} [{ctx.stage.key}] {redact(msg)}\n")

        ctx.log = _log
        _log(f"start {ctx.stage.key} (attempt {ctx.stage.attempt})")
        res = await asyncio.to_thread(native.run_native, ctx)
        _log(("FAILED: " + (res.error or "")) if res.failed else ("done: " + (res.summary or "")))
        for w in res.warnings:
            _log("warning: " + w)

        with self.lock, session_scope() as s:
            st = s.get(Stage, stage_id)
            run = s.get(Run, run_id)
            run.ended_at = now()
            if st is None or st.status != StageStatus.running.value:
                run.state = "cancelled"
                s.add(run)
                s.commit()
                return
            v = self._video(s, st)
            run.state, run.exit_code = ("failed", 1) if res.failed else ("done", 0)
            s.add(run)
            missing = [] if res.failed else _missing_outputs(ws, [o for o in (st.expected_outputs or [])
                                                                     if st.key not in ("captions",) or cfg.captions])
            if res.failed or missing:
                out = Outcome("failed", outputs=res.outputs, warnings=res.warnings,
                              error=res.error or f"expected output missing: {', '.join(missing)}")
            else:
                out = Outcome("completed", outputs=res.outputs or _expand_outputs(ws, st.expected_outputs or []),
                              warnings=res.warnings, summary=res.summary)
            self._apply_outcome(s, v, st, out)

    # ---- claude
    def _prepare_claude(self, s: Session, st: Stage, v: Video, log_path: Path) -> RunRequest:
        cfg = video_config(v)
        cfg_d = cfg.model_dump()
        ws = Path(v.workspace)
        reg = self.registry_or_none()
        disabled = self.disabled_engines(s)
        feedback = st.pending_feedback
        fkind = (st.params or {}).get("_feedback_kind", "changes")
        resume = bool((st.params or {}).get("_resume"))
        lane = st.lane
        paid = self.paid_env_names()
        engine_env: dict[str, str] = {}
        add_dirs: list[Path] = []
        plugin_dirs: list[Path] = []
        tools = list(DEFAULT_TOOLS)
        sb_engines = planner.available_engines(cfg, reg, disabled) if reg is not None else []

        if st.key.startswith(planner.SEGMENT_PREFIX):
            self._refresh_segment_window(st, ws)

        if st.owner in ("spacestudio", "") or st.key in prompts.CREATIVE_STAGES:
            cwd = ws
            tools += MAIN_LANE_TOOLS
            scene_engines = [a for a in sb_engines if a.supports("scene_render")]
            add_dirs = [Path(a.repo_path) for a in sb_engines if a.repo_path and Path(a.repo_path).exists()]
            prompt = prompts.creative_prompt(
                key=st.key, video=v, ws=ws, lane=lane, config=cfg, gate=st.gate,
                engines=prompts.engine_catalog(scene_engines, cfg) if st.key == "storyboard" else None,
                guidance=prompts.guidance_docs(sb_engines, st.key), feedback=feedback, feedback_kind=fkind,
                resume=resume, excluded=cfg.excluded_engines)
        else:
            if reg is None:
                raise OrchestratorError("engine registry unavailable")
            adapter = reg.get(st.owner)
            spec = stage_to_spec(st)
            ls = adapter.launch_spec(ws, spec, cfg_d)
            cwd = Path(ls.cwd)
            add_dirs = [Path(d) for d in ls.add_dirs]
            if adapter.repo_path and Path(adapter.repo_path) not in add_dirs:
                add_dirs.append(Path(adapter.repo_path))
            plugin_dirs = [Path(d) for d in ls.plugin_dirs]
            engine_env = dict(ls.env)
            tools = list(dict.fromkeys(list(ls.allowed_tools) + tools))
            prompt = adapter.stage_prompt(spec, ws, cfg_d, feedback=feedback, resume=resume)
            prompt = prompts.ensure_header(prompt, st.key, v.id, ws, lane, st.expected_outputs or [])
            if (feedback and feedback not in prompt) or (resume and "RESUME" not in prompt.upper()):
                prompt += "\n\n" + prompts.engine_stage_extras(feedback if feedback not in prompt else None,
                                                               fkind, resume and "RESUME" not in prompt.upper(),
                                                               False)
        tmp = ws / ".spacestudio" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        engine_env.update({
            "TMPDIR": str(tmp),
            "SPACESTUDIO_DATA_DIR": str(self.settings.data_dir),
            "SPACESTUDIO_RENDER_SLOTS": str(self.settings.max_render_jobs),
            "SPACESTUDIO_WORKSPACE": str(ws),
            "SPACESTUDIO_STAGE": st.key,
        })
        env = child_env(self.settings, engine_env, zero_cost=cfg.zero_cost,
                        approved_paid_providers=cfg.approved_paid_providers, paid_env_names=paid)
        sess = s.exec(select(ClaudeSession).where(ClaudeSession.video_id == v.id, ClaudeSession.lane == lane)).first()
        return RunRequest(
            prompt=prompt, cwd=cwd, lane=lane, log_path=log_path,
            resume_session=sess.session_id if sess else None, add_dirs=add_dirs, plugin_dirs=plugin_dirs,
            env=env, allowed_tools=tools, append_system_prompt=prompts.stage_protocol(cfg.zero_cost),
            agents=agent_defs(), model=cfg.claude_model, effort=cfg.claude_effort,
            max_budget_usd=cfg.max_claude_budget_usd, timeout_s=int(self.settings.stage_timeout_minutes) * 60,
            run_key=st.id)

    def _refresh_segment_window(self, st: Stage, ws: Path) -> None:
        try:
            sb = ix.validate_file(ws / "storyboard" / "storyboard.json", "Storyboard")
        except Exception:
            return
        wins = planner.segment_windows(sb, planner._load_timing(ws))
        seg = (st.params or {}).get("segment")
        if seg in wins:
            st.params = {**st.params, "start": wins[seg][0], "end": wins[seg][1]}

    async def _execute_claude(self, stage_id: str) -> None:
        with self.lock, session_scope() as s:
            st = s.get(Stage, stage_id)
            v = self._video(s, st)
            run, log_path = self._start_run(s, st, "claude")
            run_id = run.id
            ws = Path(v.workspace)
            events_from = _line_count(ws / workspace.EVENTS_FILE)
            try:
                req = self._prepare_claude(s, st, v, log_path)
                s.add(st)
                s.commit()
            except (ClaudeNotFound, OrchestratorError, KeyError, NotImplementedError) as e:
                msg = str(e) if not isinstance(e, KeyError) else f"engine {st.owner!r} is not registered"
                self._fail_run(s, st, run, msg)
                return
            video_id, lane, key = v.id, st.lane, st.key

        def on_event(ev: dict) -> None:
            kind = ev.get("kind")
            if kind == "spawned":
                with session_scope() as s2:
                    r = s2.get(Run, run_id)
                    if r:
                        r.pid = ev.get("pid")
                        s2.add(r)
            elif kind == "init" and ev.get("session_id"):
                with self.lock, session_scope() as s2:
                    self._upsert_session(s2, video_id, lane, ev["session_id"], ev.get("model"), str(req.cwd), 0.0)
                    r = s2.get(Run, run_id)
                    if r:
                        r.model = ev.get("model")
                        s2.add(r)
                    st2 = s2.get(Stage, stage_id)
                    if st2:
                        st2.session_id = ev["session_id"]
                        s2.add(st2)
            elif kind in ("activity", "text"):
                summary = ev.get("summary") or ev.get("text") or ""
                publish("log", video_id=video_id, stage_key=key, line=summary)
                t = time.monotonic()
                if t - self._activity_ts.get(stage_id, 0) >= 1.0:
                    self._activity_ts[stage_id] = t
                    with session_scope() as s2:
                        st2 = s2.get(Stage, stage_id)
                        if st2 and st2.status == StageStatus.running.value:
                            st2.last_activity = summary[:300]
                            s2.add(st2)
                    publish("stage.updated", video_id=video_id, stage_key=key, last_activity=summary[:300])

        try:
            result = await self.runner.run(req, on_event)
        except ClaudeNotFound as e:
            with self.lock, session_scope() as s:
                self._fail_run(s, s.get(Stage, stage_id), s.get(Run, run_id), str(e))
            return
        except OSError as e:
            with self.lock, session_scope() as s:
                self._fail_run(s, s.get(Stage, stage_id), s.get(Run, run_id), f"could not start Claude Code: {e}")
            return
        finally:
            self._activity_ts.pop(stage_id, None)

        with self.lock, session_scope() as s:
            st = s.get(Stage, stage_id)
            run = s.get(Run, run_id)
            run.ended_at, run.exit_code, run.cost_usd = now(), result.exit_code, result.cost_usd
            run.model = result.model or run.model
            if result.session_id:
                self._upsert_session(s, video_id, lane, result.session_id, result.model, str(req.cwd), result.cost_usd)
            if st is None or st.status != StageStatus.running.value or result.cancelled:
                run.state = "cancelled"
                s.add(run)
                if st is not None:
                    st.cost_usd = (st.cost_usd or 0.0) + result.cost_usd
                    s.add(st)
                s.commit()
                return
            v = self._video(s, st)
            st.cost_usd = (st.cost_usd or 0.0) + result.cost_usd
            if result.session_id:
                st.session_id = result.session_id
            new_events = [e for e in workspace.read_events(ws, events_from)
                          if not e.get("stage") or e.get("stage") == st.key]
            out = self.verify_claude(s, v, st, result, new_events)
            run.state = "done" if out.status != "failed" else "failed"
            s.add(run)
            self._apply_outcome(s, v, st, out)

    def _fail_run(self, s: Session, st: Optional[Stage], run: Optional[Run], msg: str) -> None:
        if run is not None:
            run.state, run.ended_at = "failed", now()
            s.add(run)
        if st is not None and st.status == StageStatus.running.value:
            st.status, st.error = StageStatus.failed.value, redact(msg)
            s.add(st)
            s.commit()
            audit("stage.failed", video_id=st.video_id, stage=st.key, error=msg)
            self._after_change(s, self._video(s, st), st)
        else:
            s.commit()

    def _upsert_session(self, s: Session, video_id: str, lane: str, session_id: str, model: Optional[str],
                        cwd: str, cost: float) -> None:
        cs = s.exec(select(ClaudeSession).where(ClaudeSession.video_id == video_id, ClaudeSession.lane == lane)).first()
        if cs is None:
            cs = ClaudeSession(video_id=video_id, lane=lane, session_id=session_id, model=model, cwd=cwd)
        cs.session_id = session_id
        cs.model = model or cs.model
        cs.last_used_at = now()
        cs.total_cost_usd = round((cs.total_cost_usd or 0.0) + (cost or 0.0), 6)
        s.add(cs)
        s.commit()

    # ---- verification
    def verify_claude(self, s: Session, v: Video, st: Stage, result: RunResult, events: list[dict]) -> Outcome:
        ws = Path(v.workspace)
        cfg = video_config(v)
        warnings: list[str] = []
        if result.permission_denials:
            names = sorted({str(d.get("tool_name", d)) if isinstance(d, dict) else str(d)
                            for d in result.permission_denials})
            warnings.append("permission denied for: " + ", ".join(names)[:300])
        if result.timed_out:
            return Outcome("failed", warnings=warnings,
                           error=f"timed out after {self.settings.stage_timeout_minutes} minutes")
        # protocol events: the last decisive one wins
        decisive = [e for e in events if e.get("type") in ("needs_input", "stage_completed", "awaiting_approval", "error")]
        last = decisive[-1] if decisive else None
        summary = None
        if last is not None and last.get("type") == "needs_input":
            return Outcome("needs_input", warnings=warnings, question=redact(str(last.get("question") or
                           "Claude needs input to continue (no question given)")))
        if last is not None and last.get("type") == "error":
            return Outcome("failed", warnings=warnings, error=redact(str(last.get("message") or "the stage reported an error")))
        if last is not None:
            summary = redact(str(last.get("summary") or "")) or None
        else:
            warnings.append("Claude did not append a stage_completed event; status taken from the files")
        if result.is_error and not result.got_result:
            tail = result.stderr_tail.strip().splitlines()[-1:] if result.stderr_tail else []
            return Outcome("failed", warnings=warnings,
                           error=f"Claude Code exited with code {result.exit_code}"
                                 + (f": {tail[0]}" if tail else "") )
        if result.is_error:
            warnings.append(f"Claude reported an error result: {result.result_text[:200]}")

        engine_gate = False
        progress = None
        outputs: list[str] = []
        detected_cost = 0.0
        if st.owner not in ("spacestudio", "") and st.key not in prompts.CREATIVE_STAGES:
            reg = self.registry_or_none()
            try:
                adapter = reg.get(st.owner)
                det = adapter.detect(ws, stage_to_spec(st))
            except Exception as e:
                return Outcome("failed", warnings=warnings, error=f"could not check the engine's files: {e}")
            progress = det.progress
            detected_cost = float(det.cost_usd or 0.0)
            if cfg.zero_cost and detected_cost > 0:
                return Outcome("failed", warnings=warnings,
                               error=f"paid provider used: the engine reports ${detected_cost:.2f} spend in zero-cost mode")
            if det.status == StageStatus.failed.value:
                return Outcome("failed", warnings=warnings, error=redact(det.detail or "the engine reports a failure"))
            if det.status == StageStatus.needs_input.value:
                return Outcome("needs_input", warnings=warnings, question=redact(det.detail or "the engine needs input"))
            if det.status == StageStatus.awaiting_approval.value:
                engine_gate = True
            elif det.status != StageStatus.completed.value:
                missing = _missing_outputs(ws, st.expected_outputs or [])
                if missing or last is None:
                    return Outcome("failed", warnings=warnings,
                                   error=f"the engine's files do not show this stage as done ({det.status}: "
                                         f"{det.detail or 'missing ' + ', '.join(missing)})")
                warnings.append(f"engine detection said {det.status}: {det.detail}")
            outputs = list(det.outputs or [])
            try:
                outputs += [o for o in adapter.collect_outputs(ws, stage_to_spec(st)) if o not in outputs]
            except Exception:
                pass
        missing = _missing_outputs(ws, st.expected_outputs or [])
        if missing and not engine_gate:
            return Outcome("failed", warnings=warnings, error=f"expected output missing: {', '.join(missing)}")
        # interchange validation
        problems = self._validate_interchange(ws, st, v, cfg, s)
        if problems:
            return Outcome("failed", warnings=warnings, error="; ".join(problems))
        outputs = outputs or _expand_outputs(ws, st.expected_outputs or [])
        if last is not None and isinstance(last.get("outputs"), list):
            for o in last["outputs"]:
                if isinstance(o, str):
                    try:
                        p = workspace.resolve(ws, o)
                    except workspace.WorkspaceError:
                        warnings.append(f"ignored output outside the workspace: {o}")
                        continue
                    rel = os.path.relpath(p, ws)
                    if p.is_file() and rel not in outputs:
                        outputs.append(rel)
        return Outcome("gate" if engine_gate else "completed", outputs=outputs, warnings=warnings, summary=summary,
                       engine_gate=engine_gate, progress=progress, cost_usd=detected_cost)

    def _validate_interchange(self, ws: Path, st: Stage, v: Video, cfg: ix.VideoConfig, s: Session) -> list[str]:
        problems = []
        for rel in st.expected_outputs or []:
            name = ix.FILES.get(rel)
            if rel.startswith("scenes/") and rel.endswith(".json"):
                name = "SegmentRender"
            if not name:
                continue
            try:
                obj = ix.validate_file(ws / rel, name)
            except FileNotFoundError:
                problems.append(f"{rel} is missing")
                continue
            except Exception as e:
                problems.append(f"{rel} does not match the {name} schema: {_validation_msg(e)}")
                continue
            if name == "Storyboard" and v.mode == "collaborative":
                reg = self.registry_or_none()
                engines = planner.available_engines(cfg, reg, self.disabled_engines(s)) if reg else []
                problems += [f"storyboard: {p}" for p in planner.validate_storyboard(obj, engines)]
            if name == "Script" and not obj.lines:
                problems.append("scripts/script.json has no lines")
        return problems

    # ---- apply
    def _apply_outcome(self, s: Session, v: Video, st: Stage, out: Outcome) -> None:
        st.warnings = list(dict.fromkeys([*(w for w in (st.warnings or []) if w.startswith("no enabled engine")),
                                          *[redact(w) for w in out.warnings]]))
        if out.outputs:
            st.outputs = out.outputs
        st.summary = out.summary or st.summary
        if out.progress is not None:
            st.progress = out.progress
        st.last_activity = None
        if out.status == "failed":
            st.status, st.error = transition(st.status, "fail").value, redact(out.error or "failed")
            audit("stage.failed", video_id=v.id, stage=st.key, error=st.error)
        elif out.status == "needs_input":
            st.status, st.question = transition(st.status, "ask").value, out.question
            st.pending_feedback = None
            audit("stage.needs_input", video_id=v.id, stage=st.key, question=out.question)
        else:
            cfg = video_config(v)
            if out.engine_gate:
                st.engine_gate = True
            st.pending_feedback = None
            st.params = {k: val for k, val in (st.params or {}).items() if k != "_resume"}
            if self.should_gate(st, cfg, self.stages(s, v.id), out.engine_gate):
                st.status = transition(st.status, "gate").value
                audit("stage.awaiting_approval", video_id=v.id, stage=st.key)
            else:
                st.status = transition(st.status, "finish").value
                if st.gate:
                    s.add(Approval(stage_id=st.id, decision="approve",
                                   feedback=f"auto-approved (approval_mode={cfg.approval_mode})"))
                    audit("stage.auto_approved", video_id=v.id, stage=st.key, mode=cfg.approval_mode)
                    self._snapshot(s, st, note="auto-approved", approved=True)
                audit("stage.completed", video_id=v.id, stage=st.key)
                self._complete(s, v, st)
        s.add(st)
        s.commit()
        self._after_change(s, v, st)
        self.wake()

    # ------------------------------------------------------------------ recovery
    def recover(self) -> list[str]:
        """Startup: runs left 'running' by a previous process -> interrupted; stages -> queued with resume."""
        import psutil  # dependency

        recovered = []
        with self.lock, session_scope() as s:
            for r in s.exec(select(Run).where(Run.state == "running")).all():
                alive = False
                if r.pid and r.kind == "claude":
                    try:
                        p = psutil.Process(r.pid)
                        alive = p.is_running() and Path(self.runner.claude_bin).name in " ".join(p.cmdline())
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        alive = False
                    if alive:  # orphan from a crashed backend: its output pipe is gone; stop it cleanly
                        from ..claude.runner import _signal_group
                        import signal
                        _signal_group(r.pid, signal.SIGTERM)
                r.state, r.ended_at = "interrupted", now()
                s.add(r)
            for st in s.exec(select(Stage).where(Stage.status == StageStatus.running.value)).all():
                st.status = transition(st.status, "interrupt").value
                st.params = {**(st.params or {}), "_resume": True}
                st.last_activity = None
                s.add(st)
                v = s.get(Video, st.video_id)
                if v is not None and v.status != JobStatus.cancelled.value:
                    v.status = JobStatus.interrupted.value
                    s.add(v)
                recovered.append(st.id)
            s.commit()
        if recovered:
            audit("app.recovered", stages=recovered)
        return recovered


def redact_stage(st: Stage) -> dict:
    d = st.model_dump()
    for k in ("error", "last_activity", "question", "summary"):
        if d.get(k):
            d[k] = redact(d[k])
    return d


_orch: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    global _orch
    if _orch is None:
        _orch = Orchestrator()
    return _orch


def set_orchestrator(o: Optional[Orchestrator]) -> None:
    global _orch
    _orch = o
