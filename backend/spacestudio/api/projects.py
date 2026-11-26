"""Project endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..audit import audit
from ..db import get_session
from ..models import Batch, Project, Video
from ..schemas import ProjectCreate

router = APIRouter()


@router.get("/projects")
def list_projects(s: Session = Depends(get_session)) -> list[Project]:
    return list(s.exec(select(Project).order_by(Project.created_at)).all())


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, s: Session = Depends(get_session)) -> Project:
    p = Project(name=body.name.strip(), description=body.description)
    s.add(p)
    s.commit()
    audit("project.created", actor="user", project_id=p.id, name=p.name)
    return p


@router.get("/projects/{project_id}")
def get_project(project_id: str, s: Session = Depends(get_session)) -> Project:
    p = s.get(Project, project_id)
    if p is None:
        raise HTTPException(404, f"project {project_id} not found")
    return p


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, s: Session = Depends(get_session)) -> dict:
    p = s.get(Project, project_id)
    if p is None:
        raise HTTPException(404, f"project {project_id} not found")
    active = s.exec(select(Video).where(Video.project_id == project_id,
                                        Video.status.in_(["queued", "running", "awaiting_approval", "needs_input"]))).first()
    if active is not None:
        raise HTTPException(409, "the project has active videos; cancel them first")
    n_videos = len(s.exec(select(Video).where(Video.project_id == project_id)).all())
    if n_videos or s.exec(select(Batch).where(Batch.project_id == project_id)).first():
        raise HTTPException(409, f"the project still has {n_videos} video(s); delete or move them first")
    s.delete(p)
    s.commit()
    audit("project.deleted", actor="user", project_id=project_id)
    return {"ok": True}
