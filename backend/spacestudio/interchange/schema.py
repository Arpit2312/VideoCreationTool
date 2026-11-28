"""Neutral interchange files shared between engines (CONTRACT, docs/PHASE2_SPEC.md §5).

These are the only files one engine hands to another. Claude writes them; Python validates them.
`json_schema(name)` gives the JSON Schema that prompts embed so Claude writes exactly this shape.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class VideoConfig(BaseModel):
    video_type: str = "educational-explainer"
    visual_style: str = "cinematic"
    tone: str = "educational"
    duration_seconds: int = 60
    language: str = "en"
    narration_language: str = "en"
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:5", "4:3"] = "16:9"
    resolution: str = "1920x1080"
    fps: int = 24
    platforms: list[str] = Field(default_factory=lambda: ["youtube"])
    voiceover: Literal["generated", "user", "none"] = "generated"
    voice_engine: Optional[str] = None          # engine id providing TTS; None = auto
    narration_voice: Optional[str] = None       # e.g. hi-IN-SwaraNeural (edge-tts) or hf_alpha (Kokoro); None = default
    user_voice_file: Optional[str] = None       # workspace-relative, under assets/user/
    captions: bool = True
    burn_captions: bool = False
    subtitle_languages: list[str] = Field(default_factory=lambda: ["en"])
    music: bool = True
    sfx: bool = True
    pacing: Literal["slow", "medium", "fast"] = "medium"
    scene_density: Literal["low", "medium", "high"] = "medium"
    branding: dict = Field(default_factory=dict)  # {logo, intro, outro, colors}
    target_audience: str = "general"
    references: list[str] = Field(default_factory=list)
    budget_usd: float = 0.0
    zero_cost: bool = True
    approved_paid_providers: list[str] = Field(default_factory=list)
    approval_mode: Literal["manual", "auto_to_preview", "auto_low_risk"] = "manual"
    auto_stages: list[str] = Field(default_factory=list)  # for auto_low_risk
    engines: list[str] = Field(default_factory=list)       # allowed engines; empty = all enabled
    excluded_engines: list[str] = Field(default_factory=list)
    engine_options: dict = Field(default_factory=dict)     # e.g. {"lemo-opuscar": {"style": "whiteboard"}}
    extra_allowed_tools: list[str] = Field(default_factory=list)   # rules the user approved for this video
    claude_model: Optional[str] = None
    claude_effort: Optional[str] = "high"
    max_claude_budget_usd: Optional[float] = None
    brief: str = ""                                        # detailed creative brief


class Brief(BaseModel):
    title: str
    idea: str
    logline: str = ""
    angle: str = ""
    structure: list[str] = Field(default_factory=list)
    look: str = ""
    audience: str = ""
    config: VideoConfig


class Source(BaseModel):
    id: str
    title: str
    url: Optional[str] = None
    publisher: Optional[str] = None
    accessed: Optional[str] = None


class Research(BaseModel):
    summary: str
    key_facts: list[dict] = Field(default_factory=list)   # {id, claim, source_ids: []}
    sources: list[Source] = Field(default_factory=list)
    angles: list[str] = Field(default_factory=list)


class FactCheckItem(BaseModel):
    claim_id: str
    claim: str
    status: Literal["verified", "disputed", "unverified", "corrected"]
    note: str = ""
    source_ids: list[str] = Field(default_factory=list)


class FactCheck(BaseModel):
    items: list[FactCheckItem] = Field(default_factory=list)


class ScriptLine(BaseModel):
    id: str                      # "L01"
    text: str                    # as displayed / captioned
    spoken: str = ""             # as narrated (numbers as words); empty = text
    section: str = ""            # a "take" / section name
    fact_ids: list[str] = Field(default_factory=list)


class Script(BaseModel):
    version: int = 1
    title: str
    lines: list[ScriptLine]
    word_count: int = 0
    estimated_seconds: float = 0.0


class Scene(BaseModel):
    number: int
    segment: str                  # segment id, e.g. "s1"; scenes of one segment are rendered together
    line_ids: list[str]           # ScriptLine ids narrated in this scene
    visual: str                   # visual description
    duration_seconds: float
    engine: str                   # engine id that renders this segment
    method: str = ""              # e.g. "cinematic wall board", "lemo style whiteboard", "openmontage remotion"
    asset_ids: list[str] = Field(default_factory=list)
    status: str = "planned"
    preview: Optional[str] = None


class Segment(BaseModel):
    id: str
    engine: str
    engine_options: dict = Field(default_factory=dict)   # e.g. {"style": "whiteboard"} or {"look": "night"}
    scene_numbers: list[int]
    reason: str = ""                                     # why this engine was chosen


class Storyboard(BaseModel):
    scenes: list[Scene]
    segments: list[Segment]


class AssetItem(BaseModel):
    id: str
    kind: Literal["image", "footage", "music", "sfx", "voice", "font", "logo", "map", "data", "other"]
    path: Optional[str] = None            # workspace-relative once acquired
    source: str = ""                      # "wikimedia", "archive.org", "nasa", "user", "generated:<engine>", ...
    source_url: Optional[str] = None
    license: str = ""                     # e.g. "Public domain", "CC BY-SA 4.0"
    credit: str = ""
    provider: str = ""                    # engine id or tool that fetched/made it
    paid: bool = False
    status: Literal["planned", "acquired", "missing", "rejected"] = "planned"


class AssetManifest(BaseModel):
    items: list[AssetItem] = Field(default_factory=list)


class NarrationLineTiming(BaseModel):
    id: str
    start: float
    end: float
    text: str


class NarrationTiming(BaseModel):
    audio: str = "narration/narration.wav"
    engine: str = ""
    voice: str = ""
    duration: float
    lines: list[NarrationLineTiming]


class SegmentRender(BaseModel):
    """Written by an engine lane next to scenes/<segment>.mp4 as scenes/<segment>.json."""

    segment: str
    engine: str
    video: str                     # workspace-relative mp4
    start: float                   # start time on the narration timeline
    end: float
    has_audio: bool = False
    credits: list[str] = Field(default_factory=list)


MODELS = {
    "VideoConfig": VideoConfig, "Brief": Brief, "Research": Research, "FactCheck": FactCheck,
    "Script": Script, "Storyboard": Storyboard, "AssetManifest": AssetManifest,
    "NarrationTiming": NarrationTiming, "SegmentRender": SegmentRender,
}

# workspace-relative file -> model name
FILES = {
    "brief.json": "Brief",
    "research/research.json": "Research",
    "research/factcheck.json": "FactCheck",
    "scripts/script.json": "Script",
    "storyboard/storyboard.json": "Storyboard",
    "assets/manifest.json": "AssetManifest",
    "narration/timing.json": "NarrationTiming",
}


def json_schema(name: str) -> dict:
    return MODELS[name].model_json_schema()


def validate_file(path, name: str):
    """Parse and validate a JSON file; raises pydantic.ValidationError / ValueError."""
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return MODELS[name].model_validate(data)
