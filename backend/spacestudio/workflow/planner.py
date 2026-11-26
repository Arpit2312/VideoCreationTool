"""config + engines -> stage DAG (spec §4)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Optional

from ..interchange.schema import NarrationTiming, Storyboard, VideoConfig
from .stages import COLLAB_BACKBONE, COLLAB_TAIL, Capability, StageSpec

ASSET_PREFERENCE = ["cinematic-skill", "openmontage"]
NARRATION_PREFERENCE = ["lemo-opuscar", "openmontage", "cinematic-skill"]
USER_VOICE_PREFERENCE = ["cinematic-skill"]
SEGMENT_PREFIX = "segment:"


class PlanError(ValueError):
    pass


def _cfg(config: Any) -> VideoConfig:
    if isinstance(config, VideoConfig):
        return config
    return VideoConfig.model_validate(config or {})


def available_engines(config: Any, registry: Any, disabled: Iterable[str] = ()) -> list[Any]:
    """Adapters usable for this video: enabled/approved, allowed by config.engines, not excluded."""
    cfg = _cfg(config)
    off = set(disabled)
    allowed = set(cfg.engines or [])
    excluded = set(cfg.excluded_engines or [])
    out = []
    for a in registry.all():
        m = getattr(a, "manifest", {}) or {}
        if a.id in off or m.get("enabled") is False:
            continue
        if allowed and a.id not in allowed:
            continue
        if a.id in excluded:
            continue
        out.append(a)
    return out


def _pick(adapters: list[Any], capability: str, preference: list[str]) -> Optional[Any]:
    supporting = [a for a in adapters if a.supports(capability)]
    if not supporting:
        return None
    rank = {eid: i for i, eid in enumerate(preference)}
    supporting.sort(key=lambda a: rank.get(a.id, len(rank)))
    return supporting[0]


def _supports_user_audio(adapter: Any) -> bool:
    cap = adapter.capabilities().get(Capability.narration.value) or {}
    if not cap.get("supported"):
        return False
    if cap.get("user_audio") or cap.get("user_voice"):
        return True
    opts = cap.get("options") or []
    if any("user" in str(o).lower() for o in opts):
        return True
    return "user" in str(cap.get("detail", "")).lower() or "own voice" in str(cap.get("detail", "")).lower()


def _engine_stage(base: StageSpec, adapter: Any) -> StageSpec:
    s = copy.deepcopy(base)
    s.owner = adapter.id
    s.executor = "claude"
    s.lane = f"engine:{adapter.id}"
    return s


def plan(video: Any, config: Any, registry: Any, disabled: Iterable[str] = ()) -> list[StageSpec]:
    cfg = _cfg(config)
    mode = getattr(video, "mode", "collaborative") or "collaborative"
    engines = available_engines(cfg, registry, disabled)
    if mode == "native":
        return _plan_native(video, cfg, engines)
    return _plan_collaborative(cfg, engines)


def _plan_collaborative(cfg: VideoConfig, engines: list[Any]) -> list[StageSpec]:
    specs = [copy.deepcopy(s) for s in COLLAB_BACKBONE]

    assets = StageSpec("assets", "Asset acquisition",
                       "Fetch the planned assets with licences; update assets/manifest.json.",
                       Capability.asset_acquisition.value, depends_on=["asset_plan"],
                       inputs=["assets/manifest.json", "storyboard/storyboard.json"],
                       outputs=["assets/manifest.json"])
    owner = _pick(engines, Capability.asset_acquisition.value, ASSET_PREFERENCE)
    if owner is not None:
        assets = _engine_stage(assets, owner)
    else:  # SpaceStudio's own Claude lane, asset-librarian subagent
        assets.params["note"] = "no enabled engine supports asset acquisition; SpaceStudio's main lane fetches them"
    specs.append(assets)

    narration = StageSpec("narration", "Narration", "Voice the script; write narration/narration.wav and timing.json.",
                          Capability.narration.value, depends_on=["storyboard"],
                          inputs=["scripts/script.json", "storyboard/storyboard.json"],
                          outputs=["narration/narration.wav", "narration/timing.json"])
    if cfg.voiceover == "none":
        narration.executor, narration.lane, narration.owner = "python", "python", "spacestudio"
        narration.name = "Timing (no narration)"
        narration.description = "No voice-over: derive line timing from the storyboard durations (silent track)."
        narration.params["handler"] = "narration_silent"
    elif cfg.voiceover == "user":
        narration.inputs.append(cfg.user_voice_file or "assets/user/")
        chosen = None
        if cfg.voice_engine:
            chosen = next((a for a in engines if a.id == cfg.voice_engine and a.supports("narration")), None)
        if chosen is None:  # cinematic-skill documents using the user's own recording
            chosen = next((a for a in engines if a.id in USER_VOICE_PREFERENCE and a.supports("narration")), None)
        if chosen is None:
            chosen = next((a for a in engines if _supports_user_audio(a)), None)
        if chosen is not None:
            narration = _engine_stage(narration, chosen)
            narration.params["user_voice_file"] = cfg.user_voice_file
        else:
            narration.executor, narration.lane, narration.owner = "python", "python", "spacestudio"
            narration.description = "Use the user's recording; timing proportional to each line's length."
            narration.params["handler"] = "narration_user"
    else:
        chosen = None
        if cfg.voice_engine:
            chosen = next((a for a in engines if a.id == cfg.voice_engine and a.supports("narration")), None)
            if chosen is None:
                raise PlanError(f"voice engine {cfg.voice_engine!r} is not available or does not support narration")
        chosen = chosen or _pick(engines, Capability.narration.value, NARRATION_PREFERENCE)
        if chosen is None:
            raise PlanError("no enabled engine supports narration; enable an engine with a voice, choose "
                            "voiceover 'user' (your recording) or 'none'")
        narration = _engine_stage(narration, chosen)
    specs.append(narration)

    tail = [copy.deepcopy(s) for s in COLLAB_TAIL]
    for s in tail:
        if s.key == "preview":
            # replaced by the segment keys once the storyboard is approved (expand_segments)
            s.depends_on = ["assets", "narration"]
    specs.extend(tail)
    return specs


def _plan_native(video: Any, cfg: VideoConfig, engines: list[Any]) -> list[StageSpec]:
    primary = getattr(video, "primary_engine", None) or (cfg.engines[0] if cfg.engines else None)
    if not primary:
        raise PlanError("native mode needs a primary engine")
    adapter = next((a for a in engines if a.id == primary), None)
    if adapter is None:
        raise PlanError(f"engine {primary!r} is not available (disabled, excluded or unknown)")
    setup = copy.deepcopy(COLLAB_BACKBONE[0])
    native = [copy.deepcopy(s) for s in adapter.native_stages(cfg.model_dump())]
    keys = [s.key for s in native]
    for s in native:
        if s.owner in ("", "spacestudio") and s.executor == "claude":
            s.owner = adapter.id
        if s.executor == "claude" and (not s.lane or s.lane == "main"):
            s.lane = f"engine:{adapter.id}"
        if not s.depends_on:
            s.depends_on = ["setup"]
    tail = {s.key: copy.deepcopy(s) for s in COLLAB_TAIL}
    qa, export = tail["qa"], tail["export"]
    qa.depends_on = keys or ["setup"]
    export.depends_on = ["qa"]
    return [setup, *native, qa, export]


# --------------------------------------------------------------------------- segments
def _load_timing(ws: Optional[Path]) -> Optional[NarrationTiming]:
    if ws is None:
        return None
    f = Path(ws) / "narration" / "timing.json"
    if not f.exists():
        return None
    try:
        return NarrationTiming.model_validate(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        return None


def segment_windows(storyboard: Storyboard, timing: Optional[NarrationTiming] = None) -> dict[str, tuple[float, float]]:
    """(start, end) per segment on the narration timeline: from timing when every scene line has timing,
    else from cumulative scene durations."""
    scenes = sorted(storyboard.scenes, key=lambda s: s.number)
    cum: dict[int, tuple[float, float]] = {}
    t = 0.0
    for sc in scenes:
        cum[sc.number] = (t, t + float(sc.duration_seconds))
        t += float(sc.duration_seconds)
    line_t = {ln.id: (ln.start, ln.end) for ln in timing.lines} if timing else {}
    # scene windows from timing: scene starts at its first line; ends where the next scene starts
    scene_win: dict[int, tuple[float, float]] = {}
    if timing and scenes and all(sc.line_ids and all(l in line_t for l in sc.line_ids) for sc in scenes):
        starts = [min(line_t[l][0] for l in sc.line_ids) for sc in scenes]
        for i, sc in enumerate(scenes):
            start = 0.0 if i == 0 else starts[i]
            end = starts[i + 1] if i + 1 < len(scenes) else max(timing.duration, max(line_t[l][1] for l in sc.line_ids))
            scene_win[sc.number] = (round(start, 3), round(end, 3))
    else:
        scene_win = {k: (round(a, 3), round(b, 3)) for k, (a, b) in cum.items()}
    out = {}
    for seg in storyboard.segments:
        wins = [scene_win[n] for n in seg.scene_numbers if n in scene_win]
        if wins:
            out[seg.id] = (min(w[0] for w in wins), max(w[1] for w in wins))
    return out


def validate_storyboard(storyboard: Storyboard, engines: list[Any]) -> list[str]:
    """Problems that block expansion (empty list = ok)."""
    problems = []
    usable = {a.id for a in engines if a.supports(Capability.scene_render.value)}
    seg_ids = [s.id for s in storyboard.segments]
    if not storyboard.segments:
        problems.append("the storyboard has no segments")
    if len(set(seg_ids)) != len(seg_ids):
        problems.append("segment ids are not unique")
    numbers = {sc.number for sc in storyboard.scenes}
    for seg in storyboard.segments:
        if seg.engine not in usable:
            problems.append(f"segment {seg.id} uses engine {seg.engine!r}, which is not enabled/allowed for this "
                            f"video or cannot render scenes (usable: {', '.join(sorted(usable)) or 'none'})")
        missing = [n for n in seg.scene_numbers if n not in numbers]
        if missing:
            problems.append(f"segment {seg.id} references unknown scenes {missing}")
        if not seg.scene_numbers:
            problems.append(f"segment {seg.id} has no scenes")
    for sc in storyboard.scenes:
        if sc.segment not in seg_ids:
            problems.append(f"scene {sc.number} names unknown segment {sc.segment!r}")
    return problems


def expand_segments(video: Any, storyboard: Storyboard, registry: Any, ws: Optional[Path] = None,
                    disabled: Iterable[str] = ()) -> list[StageSpec]:
    cfg = _cfg(getattr(video, "config", {}) or {})
    engines = available_engines(cfg, registry, disabled)
    problems = validate_storyboard(storyboard, engines)
    if problems:
        raise PlanError("; ".join(problems))
    windows = segment_windows(storyboard, _load_timing(ws))
    forced = cfg.engine_options or {}
    specs = []
    for seg in storyboard.segments:
        start, end = windows.get(seg.id, (0.0, 0.0))
        opts = {**(seg.engine_options or {}), **(forced.get(seg.engine) or {})}
        adapter = registry.get(seg.engine)
        name = (getattr(adapter, "manifest", {}) or {}).get("name", seg.engine)
        specs.append(StageSpec(
            key=f"{SEGMENT_PREFIX}{seg.id}", name=f"Segment {seg.id} ({name})",
            description=f"Render scenes {seg.scene_numbers} with {name}. {seg.reason}".strip(),
            capability=Capability.scene_render.value, owner=seg.engine, executor="claude",
            lane=f"engine:{seg.engine}", depends_on=["assets", "narration"],
            inputs=["storyboard/storyboard.json", "narration/timing.json", "assets/manifest.json"],
            outputs=[f"scenes/{seg.id}.mp4", f"scenes/{seg.id}.json"],
            params={"segment": seg.id, "engine_options": opts, "start": start, "end": end,
                    "scene_numbers": list(seg.scene_numbers), "reason": seg.reason},
        ))
    return specs
