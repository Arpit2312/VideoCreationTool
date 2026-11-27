"""API request/response models (spec §6)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .models import ClaudeSession, Run, Stage, StageVersion, Approval, Video


# ---------------------------------------------------------------- requests
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class VideoCreate(BaseModel):
    project_id: Optional[str] = None          # None -> the default project
    title: str = Field(min_length=1, max_length=300)
    idea: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["collaborative", "native"] = "collaborative"
    primary_engine: Optional[str] = None
    start: bool = False


class FeedbackBody(BaseModel):
    feedback: str = ""


class AnswerBody(BaseModel):
    answer: str = Field(min_length=1)


class EnginePatch(BaseModel):
    enabled: Optional[bool] = None
    approved: Optional[bool] = None
    path: Optional[str] = None
    pinned_commit: Optional[str] = None


class EngineSetupBody(BaseModel):
    confirm: bool = False
    step: str


class OnboardBody(BaseModel):
    url_or_path: str = Field(min_length=1)


class IdeasRequest(BaseModel):
    topic: str = Field(min_length=1)
    count: int = Field(default=5, ge=1, le=20)
    video_type: str = "educational-explainer"


class BatchItem(BaseModel):
    title: str = Field(min_length=1)
    idea: str = Field(min_length=1)
    overrides: dict[str, Any] = Field(default_factory=dict)


class BatchCreate(BaseModel):
    project_id: Optional[str] = None
    name: str = Field(min_length=1)
    shared_config: dict[str, Any] = Field(default_factory=dict)
    items: list[BatchItem] = Field(min_length=1)
    mode: Literal["collaborative", "native"] = "collaborative"
    primary_engine: Optional[str] = None
    start: bool = False


class BatchStart(BaseModel):
    video_ids: Optional[list[str]] = None


# ---------------------------------------------------------------- responses
class VideoDetail(BaseModel):
    video: Video
    stages: list[Stage]
    sessions: list[ClaudeSession]
    simulated: bool


class StageDetail(BaseModel):
    stage: Stage
    versions: list[StageVersion]
    approvals: list[Approval]
    runs: list[Run]
    log_tail: list[str]


class FileEntry(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int
    modified: str
    kind: str


class HistoryItem(BaseModel):
    at: str
    kind: str
    title: str
    details: dict[str, Any] = Field(default_factory=dict)


class HealthItemOut(BaseModel):
    id: str
    name: str
    status: str
    detail: str = ""
    fix: str = ""


class ClaudeInfo(BaseModel):
    bin: str
    version: Optional[str]
    simulated: bool
    ok: bool
    warning: Optional[str] = None


class HealthReport(BaseModel):
    tools: list[HealthItemOut]
    engines: list[dict[str, Any]]
    claude: ClaudeInfo
    zero_cost_ready: bool
    engines_ready: list[str] = Field(default_factory=list)
    engines_total: int = 0


class ImportPreview(BaseModel):
    items: list[BatchItem]
    errors: list[str] = Field(default_factory=list)
