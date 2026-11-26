"""Engine adapter interface (CONTRACT, docs/PHASE2_SPEC.md §5.2 of Phase 1, §1 of Phase 2).

An engine is a repository that Claude Code can drive to make (part of) a video. Each engine has a
manifest at engines/<id>/engine.yaml (data) and an adapter class (code). Future repos can use
GenericAdapter with only a manifest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..workflow.stages import StageSpec


@dataclass
class HealthItem:
    id: str
    name: str
    status: str                 # "ok" | "warning" | "error" | "not_run"
    detail: str = ""
    fix: str = ""               # actionable instruction


@dataclass
class EngineHealth:
    engine: str
    ok: bool
    zero_cost_ready: bool
    items: list[HealthItem] = field(default_factory=list)
    commit: Optional[str] = None


@dataclass
class DetectedStage:
    """What the files on disk say about a stage. Source of truth over Claude's claims."""

    key: str
    status: str                 # a StageStatus value, or "unknown"
    outputs: list[str] = field(default_factory=list)
    detail: str = ""
    cost_usd: Optional[float] = None
    progress: Optional[float] = None   # only when the engine really reports it
    last_activity: Optional[str] = None  # additive (agent B): latest real engine event, e.g. OpenMontage events.jsonl


@dataclass
class LaunchSpec:
    """Everything the Claude runner needs for one engine-lane invocation."""

    cwd: Path
    add_dirs: list[Path] = field(default_factory=list)
    plugin_dirs: list[Path] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)        # non-secret engine env
    allowed_tools: list[str] = field(default_factory=list)


class EngineAdapter:
    """Base class. Subclasses override what differs; manifest drives the rest."""

    id: str = "generic"

    def __init__(self, manifest: dict[str, Any], repo_path: Optional[Path], settings: Any = None):
        self.manifest = manifest
        self.id = manifest.get("id", self.id)
        self.repo_path = Path(repo_path).expanduser() if repo_path else None
        self.settings = settings

    # ---- description -------------------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        """{capability: {"supported": bool, "detail": str, "options": [...]}}, from the manifest."""
        return self.manifest.get("capabilities", {})

    def options(self) -> dict[str, Any]:
        """UI options this engine contributes (styles, looks, pipelines), each with zero_cost flag."""
        return self.manifest.get("options", {})

    def supports(self, capability: str) -> bool:
        return bool(self.capabilities().get(capability, {}).get("supported"))

    # ---- health ------------------------------------------------------------------
    def health(self, zero_cost: bool = True) -> EngineHealth:
        raise NotImplementedError

    def setup_steps(self) -> list[dict[str, str]]:
        """Documented setup commands: [{id, title, command, cwd}]. Run only after user confirmation."""
        return self.manifest.get("setup", [])

    # ---- execution ---------------------------------------------------------------
    def launch_spec(self, workspace: Path, stage: StageSpec, config: dict) -> LaunchSpec:
        """cwd/env/plugin dirs for this engine's lane inside a video workspace."""
        raise NotImplementedError

    def native_stages(self, config: dict) -> list[StageSpec]:
        """Stage list for native (single-engine) mode, excluding setup/qa/export."""
        raise NotImplementedError

    def stage_prompt(self, stage: StageSpec, workspace: Path, config: dict, feedback: Optional[str] = None,
                     resume: bool = False) -> str:
        """The user prompt sent to Claude for an engine-owned stage (assets, narration, segment, native)."""
        raise NotImplementedError

    def detect(self, workspace: Path, stage: StageSpec) -> DetectedStage:
        """Inspect files to decide a stage's real status after (or during) a run."""
        raise NotImplementedError

    def collect_outputs(self, workspace: Path, stage: StageSpec) -> list[str]:
        """Workspace-relative files produced by the stage (for the UI and versioning)."""
        return []

    def collect_credits(self, workspace: Path) -> list[str]:
        """Workspace-relative credits files this engine wrote."""
        return []
