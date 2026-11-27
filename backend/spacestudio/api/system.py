"""System endpoints: health, settings, presets, ideas, queue, audit, SSE events."""
from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from ..audit import audit
from ..claude import prompts
from ..config import get_settings, save_settings
from ..db import get_session
from ..events import bus, publish, sse_format
from ..models import AuditLog, Engine
from ..schemas import HealthReport, IdeasRequest
from ..security import child_env, redact, redact_obj
from . import health as health_mod
from .deps import orch

router = APIRouter()
VERSION = "0.1.0"


@router.get("/health")
def health() -> dict:
    return {"ok": True, "version": VERSION}


@router.get("/system/health", response_model=HealthReport)
def system_health(s: Session = Depends(get_session)) -> dict:
    o = orch()
    reg = o.registry_or_none()
    enabled = {e.id for e in s.exec(select(Engine)).all() if e.enabled}
    rep = health_mod.report(o.settings, o.runner, reg, enabled if enabled else None)
    for eh in rep["engines"]:
        row = s.get(Engine, eh["engine"])
        if row is not None:
            row.last_health = eh
            s.add(row)
    s.commit()
    rep = redact_obj(rep)
    publish("health.updated", zero_cost_ready=rep["zero_cost_ready"])
    return rep


@router.get("/settings")
def read_settings() -> dict:
    return redact_obj(get_settings().public_dict())


@router.put("/settings")
def write_settings(body: dict[str, Any]) -> dict:
    body = {k: v for k, v in body.items() if k != "projects_dir"}
    current = get_settings().public_dict()
    shown = redact_obj(current)
    # only real changes; values echoed back from a redacted GET are ignored
    body = {k: v for k, v in body.items() if not (k in current and (current[k] == v or shown[k] == v))}
    try:
        new = save_settings(body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    o = orch()
    o.settings = new
    o.runner.settings = new
    audit("settings.updated", actor="user", fields=sorted(body))
    return redact_obj(new.public_dict())


@router.get("/presets")
def presets() -> dict:
    reg = orch().registry_or_none()
    if reg is None:
        raise HTTPException(503, "the engine registry is not available")
    try:
        from ..presets import get_presets  # agent B
    except ImportError:
        raise HTTPException(503, "presets are not available (spacestudio.presets missing)") from None
    return get_presets(reg)


def _sample_ideas(req: IdeasRequest) -> list[dict]:
    angles = ["the surprising origin of", "a day in the life of", "five myths about", "how we discovered",
              "the future of", "the hidden science behind", "what nobody tells you about", "a timeline of"]
    return [{"title": f"[SIMULATED] {a.capitalize()} {req.topic}",
             "idea": f"[SIMULATED sample idea — the configured Claude binary is not the real `claude`] "
                     f"A {req.video_type} video on {a} {req.topic}."}
            for a in (angles * 3)[: req.count]]


@router.post("/ideas/generate")
def generate_ideas(req: IdeasRequest) -> dict:
    o = orch()
    if o.runner.is_simulated():
        return {"ideas": _sample_ideas(req), "simulated": True}
    try:
        launcher = o.runner.launcher()
    except Exception as e:
        raise HTTPException(503, str(e)) from None
    cmd = [*launcher, "-p", "--output-format", "json",
           "--json-schema", json.dumps(prompts.IDEAS_SCHEMA), "--tools", ""]
    env = child_env(o.settings, {}, zero_cost=True, paid_env_names=o.paid_env_names())
    tmo = int(getattr(o.settings, "ideas_timeout_s", 90))
    try:
        p = subprocess.run(cmd, input=prompts.ideas_prompt(req.topic, req.count, req.video_type),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=tmo, env=env,
                           cwd=str(o.settings.data_dir))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"Claude did not answer within {tmo}s") from None
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise HTTPException(502, "unexpected output from Claude: " + redact(p.stdout[-300:] or p.stderr[-300:])) from None
    if data.get("is_error"):
        raise HTTPException(502, "Claude reported an error: " + redact(str(data.get("result", ""))[:300]))
    out = data.get("structured_output")
    if out is None:
        try:
            out = json.loads(data.get("result") or "{}")
        except json.JSONDecodeError:
            out = {}
    ideas = [{"title": str(i.get("title", "")), "idea": str(i.get("idea", ""))}
             for i in (out.get("ideas") or []) if isinstance(i, dict)][: req.count]
    if not ideas:
        raise HTTPException(502, "Claude returned no ideas")
    return {"ideas": ideas, "simulated": False}


@router.get("/queue")
def queue() -> dict:
    from ..workflow.scheduler import get_scheduler

    sch = get_scheduler()
    if sch is None:
        st = get_settings()
        return {"claude_slots": {"used": 0, "max": st.max_claude_sessions},
                "render_slots": {"used": 0, "max": st.max_render_jobs}, "running": [], "queued": []}
    return sch.queue_state()


@router.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=5000), video_id: Optional[str] = None,
              s: Session = Depends(get_session)) -> list[AuditLog]:
    q = select(AuditLog)
    if video_id:
        q = q.where(AuditLog.video_id == video_id)
    return list(s.exec(q.order_by(AuditLog.id.desc()).limit(limit)).all())


@router.get("/events")
async def events(request: Request, video_id: Optional[str] = None) -> StreamingResponse:
    sub = bus.subscribe(video_id)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield sse_format(msg)
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
