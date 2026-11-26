"""Database tables (CONTRACT, docs/PHASE2_SPEC.md §3)."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def _json(default_factory=dict):
    return Field(default_factory=default_factory, sa_column=Column(JSON))


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("p"), primary_key=True)
    name: str
    description: str = ""
    created_at: str = Field(default_factory=now)


class Batch(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("b"), primary_key=True)
    project_id: str = Field(index=True)
    name: str
    shared_config: dict[str, Any] = _json()
    status: str = "draft"
    created_at: str = Field(default_factory=now)


class Video(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("v"), primary_key=True)
    project_id: str = Field(index=True)
    batch_id: Optional[str] = Field(default=None, index=True)
    title: str
    idea: str
    config: dict[str, Any] = _json()
    mode: str = "collaborative"           # collaborative | native
    primary_engine: Optional[str] = None
    status: str = "draft"                 # JobStatus
    paused: bool = False
    workspace: str = ""
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    cost_usd: float = 0.0
    error: Optional[str] = None


class Stage(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("s"), primary_key=True)
    video_id: str = Field(index=True)
    key: str
    name: str
    description: str = ""
    owner: str = "spacestudio"
    executor: str = "claude"
    lane: str = "main"
    capability: str = ""
    depends_on: list[str] = _json(list)
    gate: bool = False
    engine_gate: bool = False
    required: bool = True
    status: str = "pending"               # StageStatus
    attempt: int = 0
    order: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: Optional[float] = None
    last_activity: Optional[str] = None
    inputs: list[str] = _json(list)
    outputs: list[str] = _json(list)
    expected_outputs: list[str] = _json(list)
    params: dict[str, Any] = _json()
    warnings: list[str] = _json(list)
    error: Optional[str] = None
    pending_feedback: Optional[str] = None
    question: Optional[str] = None
    summary: Optional[str] = None
    cost_usd: float = 0.0
    session_id: Optional[str] = None


class StageVersion(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("sv"), primary_key=True)
    stage_id: str = Field(index=True)
    version: int
    created_at: str = Field(default_factory=now)
    files: list[str] = _json(list)
    snapshot_dir: str = ""
    note: str = ""
    approved: bool = False


class Approval(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("a"), primary_key=True)
    stage_id: str = Field(index=True)
    decision: str                          # approve | request_changes | reject | skip | answer
    feedback: str = ""
    created_at: str = Field(default_factory=now)


class ClaudeSession(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("cs"), primary_key=True)
    video_id: str = Field(index=True)
    lane: str
    session_id: str
    model: Optional[str] = None
    cwd: str = ""
    created_at: str = Field(default_factory=now)
    last_used_at: str = Field(default_factory=now)
    total_cost_usd: float = 0.0


class Run(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("r"), primary_key=True)
    stage_id: str = Field(index=True)
    video_id: str = Field(index=True)
    kind: str = "claude"                   # claude | native | setup
    pid: Optional[int] = None
    started_at: str = Field(default_factory=now)
    ended_at: Optional[str] = None
    exit_code: Optional[int] = None
    cost_usd: float = 0.0
    log_path: str = ""
    state: str = "running"                 # running | done | failed | cancelled | interrupted
    model: Optional[str] = None
    simulated: bool = False


class Asset(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("as"), primary_key=True)
    video_id: str = Field(index=True)
    path: str
    kind: str
    stage_key: str = ""
    source: str = ""
    license: str = ""
    credit: str = ""
    created_at: str = Field(default_factory=now)


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    at: str = Field(default_factory=now)
    actor: str = "system"
    event: str
    video_id: Optional[str] = Field(default=None, index=True)
    details: dict[str, Any] = _json()


class Engine(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    enabled: bool = True
    approved: bool = True                 # onboarded engines start unapproved
    path: str = ""
    pinned_commit: Optional[str] = None
    manifest: dict[str, Any] = _json()
    last_health: dict[str, Any] = _json()


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))
