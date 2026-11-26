"""Persistent queue scheduler (spec §8): the DB is the queue; this loop admits stages into slots.

Admission per candidate (queued stage of an active video, FIFO by video.created_at then stage.order):
  - its lane (video, lane) is idle
  - claude stage -> claude slots < max_claude_sessions
  - render-heavy stage -> render slots < max_render_jobs
  - free RAM >= min_free_ram_gb (psutil) for claude/render stages
  - engine cap (engine.yaml concurrency.max_jobs) not reached
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlmodel import select

from ..db import session_scope
from ..events import publish
from ..models import Stage, Video, now
from .stages import JobStatus, StageStatus, transition

log = logging.getLogger("spacestudio.scheduler")


@dataclass
class RunningInfo:
    stage_id: str
    video_id: str
    key: str
    lane: str
    owner: str
    claude: bool
    render: bool
    started_at: str = field(default_factory=now)
    task: Optional[asyncio.Task] = None


class Scheduler:
    def __init__(self, orchestrator: Any):
        self.orch = orchestrator
        orchestrator.scheduler = self
        self.running: dict[str, RunningInfo] = {}
        self.waiting_reason: dict[str, str] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    @property
    def settings(self) -> Any:
        return self.orch.settings

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="spacestudio-scheduler")

    async def stop(self) -> None:
        """Stop admitting; interrupt running stages (they resume on the next start)."""
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        tasks = [i.task for i in self.running.values() if i.task and not i.task.done()]
        for sid in list(self.running):
            self.orch.runner.cancel(sid)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.running.clear()

    def wake(self) -> None:
        loop, ev = self._loop, self._wake
        if loop is None or ev is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(ev.set)
        except RuntimeError:
            pass

    def cancel_task(self, stage_id: str) -> None:
        """Called when a running stage is cancelled/reset: the runner gets SIGTERM; python stages are left to
        finish in their thread and their result is discarded."""
        info = self.running.get(stage_id)
        if info is not None and not info.claude and info.task is not None:
            pass  # threads cannot be killed; _execute_python discards the result

    async def _run(self) -> None:
        interval = float(getattr(self.settings, "scheduler_interval_s", 1.0) or 1.0)
        while not self._stopping:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            assert self._wake is not None
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    # ------------------------------------------------------------------ admission
    def _free_ram_ok(self) -> bool:
        need = float(getattr(self.settings, "min_free_ram_gb", 0) or 0)
        if need <= 0:
            return True
        try:
            import psutil
        except ImportError:
            return True
        return psutil.virtual_memory().available / (1024 ** 3) >= need

    def counts(self) -> dict[str, int]:
        return {"claude": sum(1 for i in self.running.values() if i.claude),
                "render": sum(1 for i in self.running.values() if i.render)}

    async def tick(self) -> int:
        if self._stopping:
            return 0
        started = 0
        orch = self.orch
        with orch.lock, session_scope() as s:
            orch.promote(s)
            rows = s.exec(
                select(Stage, Video).where(Stage.video_id == Video.id, Stage.status == StageStatus.queued.value,
                                           Video.status.notin_([JobStatus.draft.value, JobStatus.cancelled.value]),
                                           Video.paused == False)  # noqa: E712
                .order_by(Video.created_at, Stage.order)
            ).all()
            busy_lanes = {(i.video_id, i.lane) for i in self.running.values()}
            counts = self.counts()
            per_engine: dict[str, int] = {}
            for i in self.running.values():
                per_engine[i.owner] = per_engine.get(i.owner, 0) + 1
            ram_ok: Optional[bool] = None
            self.waiting_reason = {}
            for st, v in rows:
                if st.id in self.running:
                    continue
                lane = (v.id, st.lane)
                if lane in busy_lanes:
                    self.waiting_reason[st.id] = f"lane {st.lane} busy"
                    continue
                is_claude = st.executor == "claude"
                heavy = orch.is_render_heavy(st)
                if is_claude and counts["claude"] >= self.settings.max_claude_sessions:
                    self.waiting_reason[st.id] = "waiting for a Claude slot"
                    continue
                if heavy and counts["render"] >= self.settings.max_render_jobs:
                    self.waiting_reason[st.id] = "waiting for a render slot"
                    continue
                if is_claude or heavy:
                    if ram_ok is None:
                        ram_ok = self._free_ram_ok()
                    if not ram_ok:
                        self.waiting_reason[st.id] = f"waiting for {self.settings.min_free_ram_gb} GB free RAM"
                        continue
                if st.owner not in ("spacestudio", ""):
                    cap = orch.engine_cap(st.owner)
                    if cap is not None and per_engine.get(st.owner, 0) >= cap:
                        self.waiting_reason[st.id] = f"engine {st.owner} at its limit ({cap})"
                        continue
                # admit
                st.status = transition(st.status, "start").value
                s.add(st)
                if v.status != JobStatus.running.value:
                    v.status = JobStatus.running.value
                    s.add(v)
                s.commit()
                info = RunningInfo(stage_id=st.id, video_id=v.id, key=st.key, lane=st.lane, owner=st.owner,
                                   claude=is_claude, render=heavy)
                self.running[st.id] = info
                busy_lanes.add(lane)
                counts["claude"] += int(is_claude)
                counts["render"] += int(heavy)
                per_engine[st.owner] = per_engine.get(st.owner, 0) + 1
                info.task = asyncio.create_task(self._execute(info), name=f"stage-{st.key}-{st.id}")
                started += 1
        if started:
            publish("queue.updated", **self.counts())
        return started

    async def _execute(self, info: RunningInfo) -> None:
        try:
            await self.orch.execute(info.stage_id)
        finally:
            self.running.pop(info.stage_id, None)
            publish("queue.updated", **self.counts())
            self.wake()

    # ------------------------------------------------------------------ state
    def queue_state(self) -> dict:
        with session_scope() as s:
            running = []
            for i in self.running.values():
                v = s.get(Video, i.video_id)
                running.append({"stage_id": i.stage_id, "video_id": i.video_id, "video_title": v.title if v else "",
                                "key": i.key, "lane": i.lane, "owner": i.owner, "started_at": i.started_at,
                                "claude": i.claude, "render": i.render})
            rows = s.exec(select(Stage, Video).where(Stage.video_id == Video.id,
                                                     Stage.status == StageStatus.queued.value)
                          .order_by(Video.created_at, Stage.order)).all()
            queued = [{"stage_id": st.id, "video_id": v.id, "video_title": v.title, "key": st.key, "lane": st.lane,
                       "owner": st.owner, "paused": v.paused, "reason": self.waiting_reason.get(st.id)}
                      for st, v in rows if st.id not in self.running]
        c = self.counts()
        return {"claude_slots": {"used": c["claude"], "max": self.settings.max_claude_sessions},
                "render_slots": {"used": c["render"], "max": self.settings.max_render_jobs},
                "running": running, "queued": queued}


_scheduler: Optional[Scheduler] = None


def get_scheduler() -> Optional[Scheduler]:
    return _scheduler


def set_scheduler(sch: Optional[Scheduler]) -> None:
    global _scheduler
    _scheduler = sch
