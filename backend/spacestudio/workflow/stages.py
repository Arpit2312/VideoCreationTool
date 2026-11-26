"""Stage and job state model (CONTRACT, docs/PHASE2_SPEC.md §4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StageStatus(str, Enum):
    pending = "pending"
    queued = "queued"
    running = "running"
    awaiting_approval = "awaiting_approval"
    needs_input = "needs_input"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    skipped = "skipped"


class JobStatus(str, Enum):
    draft = "draft"
    queued = "queued"
    running = "running"
    paused = "paused"
    awaiting_approval = "awaiting_approval"
    needs_input = "needs_input"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    interrupted = "interrupted"


DONE = {StageStatus.completed, StageStatus.skipped}
TERMINAL = {StageStatus.completed, StageStatus.skipped, StageStatus.failed, StageStatus.cancelled}

# action -> {from_status: to_status}
TRANSITIONS: dict[str, dict[StageStatus, StageStatus]] = {
    "enqueue": {StageStatus.pending: StageStatus.queued},
    "start": {StageStatus.queued: StageStatus.running},
    "finish": {StageStatus.running: StageStatus.completed},
    "gate": {StageStatus.running: StageStatus.awaiting_approval},
    "ask": {StageStatus.running: StageStatus.needs_input},
    "fail": {StageStatus.running: StageStatus.failed, StageStatus.queued: StageStatus.failed},
    "approve": {StageStatus.awaiting_approval: StageStatus.completed},
    "request_changes": {StageStatus.awaiting_approval: StageStatus.queued, StageStatus.completed: StageStatus.queued},
    "reject": {StageStatus.awaiting_approval: StageStatus.failed},
    "answer": {StageStatus.needs_input: StageStatus.queued},
    "retry": {StageStatus.failed: StageStatus.queued, StageStatus.cancelled: StageStatus.queued},
    "skip": {
        StageStatus.pending: StageStatus.skipped,
        StageStatus.failed: StageStatus.skipped,
        StageStatus.awaiting_approval: StageStatus.skipped,
        StageStatus.queued: StageStatus.skipped,
    },
    "cancel": {
        StageStatus.pending: StageStatus.cancelled,
        StageStatus.queued: StageStatus.cancelled,
        StageStatus.running: StageStatus.cancelled,
        StageStatus.awaiting_approval: StageStatus.cancelled,
        StageStatus.needs_input: StageStatus.cancelled,
    },
    "interrupt": {StageStatus.running: StageStatus.queued},
}


class InvalidTransition(ValueError):
    pass


def transition(current: StageStatus | str, action: str) -> StageStatus:
    cur = StageStatus(current)
    table = TRANSITIONS.get(action)
    if table is None:
        raise InvalidTransition(f"unknown action {action!r}")
    if cur not in table:
        raise InvalidTransition(f"cannot {action} a stage that is {cur.value}")
    return table[cur]


class Capability(str, Enum):
    setup = "setup"
    research = "research"
    factcheck = "factcheck"
    concept = "concept"
    script = "script"
    storyboard = "storyboard"
    asset_plan = "asset_plan"
    asset_acquisition = "asset_acquisition"
    narration = "narration"
    scene_render = "scene_render"
    preview = "preview"
    sound_design = "sound_design"
    render = "render"
    captions = "captions"
    qa = "qa"
    export = "export"
    native_pipeline = "native_pipeline"  # an engine stage in native mode


RENDER_HEAVY = {Capability.scene_render.value, Capability.render.value, Capability.preview.value}


@dataclass
class StageSpec:
    """A planned stage, before it is persisted as a models.Stage row."""

    key: str
    name: str
    description: str
    capability: str
    owner: str = "spacestudio"          # "spacestudio" or engine id
    executor: str = "claude"            # "claude" | "python"
    lane: str = "main"                  # "main" | "engine:<id>" | "python"
    depends_on: list[str] = field(default_factory=list)
    gate: bool = False
    required: bool = True               # required stages cannot be skipped
    engine_gate: bool = False           # gate demanded by the engine itself; cannot be disabled
    inputs: list[str] = field(default_factory=list)   # workspace-relative paths
    outputs: list[str] = field(default_factory=list)  # expected outputs (globs allowed)
    params: dict = field(default_factory=dict)        # free-form, e.g. {"segment": "s1"}


# Collaborative-mode backbone. Engine-owned stages (assets, narration, segments) are filled in by the planner.
COLLAB_BACKBONE: list[StageSpec] = [
    StageSpec("setup", "Project setup", "Create the workspace, snapshot the config, check the engines.", "setup",
              executor="python", lane="python"),
    StageSpec("research", "Research", "Research the topic with sources; produce research/research.json.", "research",
              depends_on=["setup"], outputs=["research/research.json"]),
    StageSpec("factcheck", "Fact-check", "Verify each claim against sources; produce research/factcheck.json.",
              "factcheck", depends_on=["research"], outputs=["research/factcheck.json"]),
    StageSpec("concept", "Concept", "Pitch the angle, structure and look; produce brief.json concept fields.",
              "concept", depends_on=["factcheck"], gate=True, outputs=["brief.json"]),
    StageSpec("script", "Script", "Write the narration script; produce scripts/script.json.", "script",
              depends_on=["concept"], gate=True, outputs=["scripts/script.json"]),
    StageSpec("storyboard", "Storyboard", "Break the script into scenes and segments; assign an engine to each.",
              "storyboard", depends_on=["script"], gate=True, outputs=["storyboard/storyboard.json"]),
    StageSpec("asset_plan", "Asset plan", "List every asset with its source and licence plan.", "asset_plan",
              depends_on=["storyboard"], gate=True, outputs=["assets/manifest.json"]),
]

COLLAB_TAIL: list[StageSpec] = [
    StageSpec("preview", "First preview", "Assemble a low-resolution preview from the segment renders.", "preview",
              executor="python", lane="python", gate=True, outputs=["renders/preview.mp4"]),
    StageSpec("sound", "Sound design & mix", "Mix narration, music and effects; normalise loudness.", "sound_design",
              executor="python", lane="python", depends_on=["preview"], outputs=["narration/mix.wav"]),
    StageSpec("render", "Final render", "Assemble the full-resolution film with the final mix.", "render",
              executor="python", lane="python", depends_on=["sound"], outputs=["renders/final.mp4"]),
    StageSpec("captions", "Captions", "Write SRT from narration timing; burn in if configured.", "captions",
              executor="python", lane="python", depends_on=["render"], outputs=["subtitles/*.srt"]),
    StageSpec("qa", "Quality checks", "Run automated checks on the outputs.", "qa", executor="python",
              lane="python", depends_on=["captions"], outputs=[".spacestudio/qa.json"]),
    StageSpec("export", "Final export", "Copy deliverables to outputs/ and write credits.", "export",
              executor="python", lane="python", depends_on=["qa"], gate=True, outputs=["outputs/*.mp4"]),
]
