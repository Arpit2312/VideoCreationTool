"""Shared API helpers."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from ..models import Project, Stage, Video
from ..workflow.orchestrator import Orchestrator, OrchestratorError, get_orchestrator
from ..workflow.stages import InvalidTransition


def orch() -> Orchestrator:
    return get_orchestrator()


def video_or_404(s: Session, video_id: str) -> Video:
    v = s.get(Video, video_id)
    if v is None:
        raise HTTPException(404, f"video {video_id} not found")
    return v


def stage_or_404(s: Session, video_id: str, key: str) -> Stage:
    st = s.exec(select(Stage).where(Stage.video_id == video_id, Stage.key == key)).first()
    if st is None:
        raise HTTPException(404, f"stage {key} not found")
    return st


def default_project(s: Session) -> Project:
    p = s.exec(select(Project).order_by(Project.created_at)).first()
    if p is None:
        p = Project(name="Default", description="Created automatically")
        s.add(p)
        s.commit()
    return p


def project_or_default(s: Session, project_id: str | None) -> Project:
    if not project_id:
        return default_project(s)
    p = s.get(Project, project_id)
    if p is None:
        raise HTTPException(404, f"project {project_id} not found")
    return p


def call(fn, *args: Any, **kwargs: Any) -> Any:
    """Run an orchestrator action, mapping its errors to HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except OrchestratorError as e:
        raise HTTPException(e.status_code, str(e)) from None
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from None
