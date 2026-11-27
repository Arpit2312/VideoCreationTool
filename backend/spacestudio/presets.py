"""Wizard presets (BUILD_BRIEF "Presets"): video types, styles, aspect ratios, platforms, voice options.

Engine suggestions come only from what the inspected repos document:
- cinematic-skill SKILL.md "Pick the film" (looks per kind) and README "What it makes".
- lemo-opuscar styles/*/style.json (categories and "uses"), resolved by slug at runtime.
- OpenMontage pipeline_defs/*.yaml and AGENT_GUIDE "Available Pipelines".
An engine (or one of its options) is listed only when the registry has it and, for options, when the
option really exists in the loaded manifest (so a renamed lemo style simply drops out).
"""
from __future__ import annotations

from typing import Any

# (engine id, option kind, [ids]) suggestions per video type; filtered against the registry at runtime.
_CIN, _LEMO, _OM = "cinematic-skill", "lemo-opuscar", "openmontage"

VIDEO_TYPES: list[dict[str, Any]] = [
    {"id": "cinematic-documentary", "name": "Cinematic documentary",
     "description": "A narrated documentary over archive photos and documents, timed to the narration.",
     "suggest": {_CIN: {"looks": ["wall", "slate"], "grades": ["bw", "sepia"]},
                 _LEMO: {"styles": ["archive-lighttable", "newsreel", "visual-investigation"]},
                 _OM: {"pipelines": ["documentary-montage"]}},
     "defaults": {"tone": "cinematic", "duration_seconds": 90, "pacing": "medium", "visual_style": "cinematic-skill:wall"}},
    {"id": "educational-explainer", "name": "Educational explainer",
     "description": "Explains how something works, step by step.",
     "suggest": {_OM: {"pipelines": ["animated-explainer"]},
                 _LEMO: {"styles": ["whiteboard", "sketchnote", "stick-explainer", "marker-paper"]},
                 _CIN: {"looks": ["blueprint", "paper", "studio"]}},
     "defaults": {"tone": "educational", "duration_seconds": 60, "pacing": "medium", "visual_style": "openmontage:animated-explainer"}},
    {"id": "science-technology", "name": "Science & technology",
     "description": "Science, engineering and tech topics with diagrams and data.",
     "suggest": {_OM: {"pipelines": ["animated-explainer"]},
                 _LEMO: {"styles": ["blueprint", "teardown", "hardcore-info", "precise-mechanism", "colored-pencil",
                                    "oscilloscope-math", "dataviz"]},
                 _CIN: {"looks": ["blueprint", "paper"]}},
     "defaults": {"tone": "educational", "duration_seconds": 75, "pacing": "medium", "visual_style": "cinematic-skill:blueprint"}},
    {"id": "space-documentary", "name": "Space documentary",
     "description": "Space and astronomy stories: missions, scale, deep survey.",
     "suggest": {_CIN: {"looks": ["night"]},
                 _LEMO: {"styles": ["satellite-briefing", "dark-documentary", "flat-space-explainer", "one-bit-dither"]},
                 _OM: {"pipelines": ["documentary-montage"]}},
     "defaults": {"tone": "awe", "duration_seconds": 90, "pacing": "slow", "visual_style": "cinematic-skill:night"}},
    {"id": "historical-documentary", "name": "Historical documentary",
     "description": "History told with archive images, documents, maps and routes.",
     "suggest": {_CIN: {"looks": ["wall", "slate"], "grades": ["bw", "sepia"]},
                 _LEMO: {"styles": ["archive-lighttable", "newsreel", "vintage-collage", "engraving", "map-explainer"]},
                 _OM: {"pipelines": ["documentary-montage"]}},
     "defaults": {"tone": "dramatic", "duration_seconds": 90, "pacing": "medium", "visual_style": "cinematic-skill:wall"}},
    {"id": "short-form-reel", "name": "Short-form reel",
     "description": "Vertical shorts for Reels, Shorts and TikTok.",
     "suggest": {_OM: {"pipelines": ["animated-explainer", "clip-factory"]},
                 _LEMO: {"styles": ["caption-news", "game-show", "pop-art"]},
                 _CIN: {"looks": ["night", "studio"]}},
     "defaults": {"tone": "energetic", "duration_seconds": 30, "pacing": "fast", "aspect_ratio": "9:16",
                  "resolution": "1080x1920", "platforms": ["youtube_shorts", "tiktok", "instagram_reels"]}},
    {"id": "youtube-long-form", "name": "YouTube long-form",
     "description": "Longer YouTube videos. cinematic-skill and lemo are tuned for short films (long ones cost many tokens).",
     "suggest": {_OM: {"pipelines": ["animated-explainer", "documentary-montage"]}},
     "defaults": {"tone": "educational", "duration_seconds": 480, "pacing": "medium"}},
    {"id": "product-founder-story", "name": "Product / founder story",
     "description": "Product launches, brand and founder stories.",
     "suggest": {_CIN: {"looks": ["studio", "charcoal"], "grades": ["natural"]},
                 _LEMO: {"styles": ["dark-keynote", "glass-product", "teardown", "desktop-flythrough"]},
                 _OM: {"pipelines": ["animated-explainer"]}},
     "defaults": {"tone": "inspirational", "duration_seconds": 60, "pacing": "medium", "visual_style": "cinematic-skill:studio"}},
    {"id": "news-style-explainer", "name": "News-style explainer",
     "description": "Report-style explainers and news round-ups.",
     "suggest": {_LEMO: {"styles": ["caption-news", "news-package", "map-explainer", "satellite-briefing"]},
                 _OM: {"pipelines": ["animated-explainer"]}},
     "defaults": {"tone": "neutral", "duration_seconds": 60, "pacing": "fast"}},
    {"id": "story-driven", "name": "Story-driven",
     "description": "Character and story-led animation.",
     "suggest": {_OM: {"pipelines": ["animation", "character-animation"]},
                 _LEMO: {"styles": ["rubber-hose", "midcentury-toon", "cel-anime-80s", "scifi-toon", "watercolor"]}},
     "defaults": {"tone": "playful", "duration_seconds": 60, "pacing": "medium"}},
    {"id": "custom", "name": "Custom", "description": "Pick engines and options yourself.", "suggest": {},
     "defaults": {}},
]

ASPECT_RATIOS = [
    {"id": "16:9", "resolution": "1920x1080"},
    {"id": "9:16", "resolution": "1080x1920"},
    {"id": "1:1", "resolution": "1080x1080"},
    {"id": "4:5", "resolution": "1080x1350"},
]

PLATFORMS = [
    {"id": "youtube", "name": "YouTube", "aspect_ratio": "16:9"},
    {"id": "youtube_shorts", "name": "YouTube Shorts", "aspect_ratio": "9:16"},
    {"id": "tiktok", "name": "TikTok", "aspect_ratio": "9:16"},
    {"id": "instagram_reels", "name": "Instagram Reels", "aspect_ratio": "9:16"},
    {"id": "instagram_feed", "name": "Instagram feed", "aspect_ratio": "4:5"},
    {"id": "x", "name": "X", "aspect_ratio": "16:9"},
    {"id": "linkedin", "name": "LinkedIn", "aspect_ratio": "1:1"},
]

TONES = [
    {"id": "educational", "name": "Educational"}, {"id": "cinematic", "name": "Cinematic"},
    {"id": "dramatic", "name": "Dramatic"}, {"id": "awe", "name": "Awe and wonder"},
    {"id": "neutral", "name": "Neutral / news"}, {"id": "inspirational", "name": "Inspirational"},
    {"id": "energetic", "name": "Energetic"}, {"id": "playful", "name": "Playful"},
    {"id": "elegiac", "name": "Elegiac / reflective"},
]

DURATIONS = [
    {"id": "15s", "name": "15 seconds", "seconds": 15}, {"id": "30s", "name": "30 seconds", "seconds": 30},
    {"id": "60s", "name": "1 minute", "seconds": 60}, {"id": "90s", "name": "90 seconds", "seconds": 90},
    {"id": "3m", "name": "3 minutes", "seconds": 180}, {"id": "5m", "name": "5 minutes", "seconds": 300},
    {"id": "10m", "name": "10 minutes", "seconds": 600},
]


def _option_ids(options: dict, kind: str) -> set:
    return {o.get("id") for o in options.get(kind) or [] if isinstance(o, dict)}


def _engine_options(registry) -> dict[str, dict]:
    return {a.id: a.options() for a in registry.all()}


def _visual_styles(engine_opts: dict[str, dict]) -> list[dict]:
    out = []
    for o in engine_opts.get(_CIN, {}).get("looks") or []:
        out.append({"id": f"{_CIN}:{o['id']}", "name": f"{o['name']} (cinematic-skill look)", "engine": _CIN,
                    "option": {"look": o["id"]}, "zero_cost": o.get("zero_cost", True), "best_for": o.get("best_for", "")})
    for o in engine_opts.get(_LEMO, {}).get("styles") or []:
        out.append({"id": f"{_LEMO}:{o['id']}", "name": f"{o['name']} (lemo style)", "engine": _LEMO,
                    "option": {"style": o["id"]}, "zero_cost": o.get("zero_cost", True), "category": o.get("category", ""),
                    "best_for": ", ".join(o.get("uses") or [])})
    for o in engine_opts.get(_OM, {}).get("pipelines") or []:
        if o.get("hidden"):
            continue
        out.append({"id": f"{_OM}:{o['id']}", "name": f"{o['name']} (OpenMontage pipeline)", "engine": _OM,
                    "option": {"pipeline": o["id"]}, "zero_cost": o.get("zero_cost", False),
                    "best_for": o.get("description", "")[:160]})
    return out


def get_presets(registry) -> dict:
    engine_opts = _engine_options(registry)
    ids = set(engine_opts)
    video_types = []
    for vt in VIDEO_TYPES:
        engines, suggested = [], {}
        for eid, kinds in vt["suggest"].items():
            if eid not in ids:
                continue
            opts = engine_opts[eid]
            filtered = {k: [x for x in v if x in _option_ids(opts, k)] for k, v in kinds.items()}
            filtered = {k: v for k, v in filtered.items() if v}
            if kinds and not filtered:
                continue  # none of the verified options exist in this clone
            engines.append(eid)
            suggested[eid] = filtered
        if vt["id"] == "custom":
            engines = sorted(ids)
        defaults = dict(vt["defaults"])
        vs = defaults.get("visual_style")
        if vs and vs.split(":", 1)[0] not in engines:
            defaults.pop("visual_style")
        if suggested:
            defaults["engine_options"] = {e: {k[:-1] if k.endswith("s") else k: v[0] for k, v in o.items()}
                                          for e, o in suggested.items() if o}
        video_types.append({"id": vt["id"], "name": vt["name"], "description": vt["description"],
                            "engines": engines, "engine_suggestions": suggested, "defaults": defaults})
    narrators = [{"engine": a.id, "detail": a.capabilities().get("narration", {}).get("detail", "")}
                 for a in registry.all() if a.supports("narration")]
    voiceover = [
        {"id": "generated", "name": "Generated voice (free local TTS)", "engines": [n["engine"] for n in narrators],
         "voice_engines": narrators},
        {"id": "user", "name": "My own recording (upload to assets/user/)", "engines": sorted(ids)},
        {"id": "none", "name": "No narration", "engines": sorted(ids)},
    ]
    from .languages import presets as language_presets

    return {
        "languages": language_presets(),
        "video_types": video_types,
        "visual_styles": _visual_styles(engine_opts),
        "tones": TONES,
        "durations": DURATIONS,
        "aspect_ratios": ASPECT_RATIOS,
        "platforms": PLATFORMS,
        "voiceover": voiceover,
        "engine_options": engine_opts,
    }
