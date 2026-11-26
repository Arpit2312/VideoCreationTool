"""Stage protocol (appended system prompt) and prompts for SpaceStudio-owned creative stages (spec §7).

Every stage prompt starts with the machine-readable line
``SPACESTUDIO_STAGE=<key> VIDEO=<id> WORKSPACE=<abs path> LANE=<lane>`` and ends with
``SPACESTUDIO_EXPECTED_OUTPUTS=<json list>``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

from ..interchange import schema as ix
from .agents import STAGE_DELEGATION

REPO_ROOT = Path(__file__).resolve().parents[3]
SLOT_BIN = REPO_ROOT / "scripts" / "spacestudio-slot"

# stage key -> (output file, interchange model name)
CREATIVE_OUTPUTS: dict[str, tuple[str, str]] = {
    "research": ("research/research.json", "Research"),
    "factcheck": ("research/factcheck.json", "FactCheck"),
    "concept": ("brief.json", "Brief"),
    "script": ("scripts/script.json", "Script"),
    "storyboard": ("storyboard/storyboard.json", "Storyboard"),
    "asset_plan": ("assets/manifest.json", "AssetManifest"),
    "assets": ("assets/manifest.json", "AssetManifest"),
}
CREATIVE_STAGES = ("research", "factcheck", "concept", "script", "storyboard", "asset_plan")

# Engine guidance docs that may be read as reference, by engine id and stage key (repo-relative).
GUIDANCE: dict[str, dict[str, list[str]]] = {
    "openmontage": {
        "research": ["skills/pipelines/explainer/research-director.md"],
        "factcheck": ["skills/pipelines/explainer/research-director.md"],
        "concept": ["skills/pipelines/explainer/idea-director.md",
                    "skills/pipelines/explainer/proposal-director.md"],
        "script": ["skills/pipelines/explainer/script-director.md"],
        "storyboard": ["skills/pipelines/explainer/scene-director.md"],
        "asset_plan": ["skills/pipelines/explainer/asset-director.md"],
        "assets": ["skills/pipelines/explainer/asset-director.md"],
    },
    "cinematic-skill": {
        "research": ["skills/cinematic-film/references/story.md"],
        "concept": ["skills/cinematic-film/references/story.md", "skills/cinematic-film/references/film.md"],
        "script": ["skills/cinematic-film/references/story.md", "skills/cinematic-film/references/voice.md"],
        "storyboard": ["skills/cinematic-film/references/story.md", "skills/cinematic-film/references/film.md"],
        "asset_plan": ["skills/cinematic-film/references/archive.md"],
        "assets": ["skills/cinematic-film/references/archive.md"],
    },
}


def stage_header(key: str, video_id: str, ws: Path, lane: str) -> str:
    return f"SPACESTUDIO_STAGE={key} VIDEO={video_id} WORKSPACE={Path(ws).resolve()} LANE={lane}"


def expected_outputs_footer(outputs: Iterable[str]) -> str:
    return "SPACESTUDIO_EXPECTED_OUTPUTS=" + json.dumps(list(outputs))


def ensure_header(prompt: str, key: str, video_id: str, ws: Path, lane: str, outputs: Iterable[str]) -> str:
    """Engine adapters write their own prompts; make sure the protocol lines are there."""
    text = prompt or ""
    if not text.startswith("SPACESTUDIO_STAGE="):
        text = stage_header(key, video_id, ws, lane) + "\n\n" + text
    if "SPACESTUDIO_EXPECTED_OUTPUTS=" not in text:
        text = text.rstrip() + "\n\n" + expected_outputs_footer(outputs)
    return text


def stage_protocol(zero_cost: bool, slot_bin: Path = SLOT_BIN) -> str:
    """Appended system prompt. Stage-independent on purpose (the CLI snapshots it per session)."""
    zc = (
        "ZERO-COST MODE IS ON: use only free, local or public-domain/CC resources. Never call paid APIs, paid "
        "TTS, paid image/video generation or paid stock, even if a key seems available. If a step would need a "
        "paid provider, use a free alternative or report it with a needs_input event."
        if zero_cost else
        "Zero-cost mode is off: paid providers may be used only if their keys are present in your environment "
        "(they were approved for this video). Keep spend minimal and mention it in your summary."
    )
    return f"""# SpaceStudio stage protocol (binding)
You are running one stage of a SpaceStudio video production, non-interactively. Each prompt starts with
`SPACESTUDIO_STAGE=<key> VIDEO=<id> WORKSPACE=<abs path> LANE=<lane>`.

1. Workspace boundary: read and write only inside WORKSPACE (and your current directory, which is inside it).
   Engine repositories added with --add-dir are reference/tools: run their scripts but never modify them.
   Never touch other videos' workspaces.
2. {zc}
3. Never ask interactive questions and never wait for an answer. If you cannot continue without the user,
   append ONE line to WORKSPACE/.spacestudio/events.jsonl:
   {{"type":"needs_input","stage":"<key>","question":"<one clear question>"}}
   and end your turn.
4. When the stage is done, append ONE line to WORKSPACE/.spacestudio/events.jsonl:
   {{"type":"stage_completed","stage":"<key>","outputs":["<workspace-relative paths>"],"summary":"<2-4 sentences>"}}
   If the prompt says the stage is gated, you may use "type":"awaiting_approval" with the same fields
   instead; SpaceStudio runs the approval gate, so end your turn right after.
   If the stage failed, append {{"type":"error","stage":"<key>","message":"<what failed and how to fix it>"}}.
   Append with a single shell `printf '%s\\n' '<json>' >> .../events.jsonl` or an equivalent append; never
   rewrite the file.
5. Wrap every heavy render or encode (full video renders, long ffmpeg encodes, headless-browser frame renders)
   in the render semaphore: `{slot_bin} render -- <command> <args>`.
6. Interchange files (JSON) must match the JSON Schema given in the prompt exactly: valid JSON, all required
   fields, no extra wrapping, workspace-relative paths.
7. SpaceStudio verifies your work from the files, not from your words. Do not claim outputs you did not write.
8. If the prompt says the stage was interrupted, first check which outputs already exist and are valid, and
   only redo what is missing.
"""


def _engine_lines(engines: list[dict]) -> str:
    if not engines:
        return "(no rendering engine is available)"
    out = []
    for e in engines:
        caps = ", ".join(f"{k}" + (f" ({v.get('detail')})" if v.get("detail") else "")
                         for k, v in e.get("capabilities", {}).items() if v.get("supported"))
        opts = json.dumps(e.get("options", {}), default=str)
        if len(opts) > 1500:
            opts = opts[:1500] + "…"
        out.append(f"- `{e['id']}` — {e.get('name', e['id'])}: {e.get('description', '')}\n"
                   f"  zero_cost: {e.get('zero_cost')}; capabilities: {caps or 'none'}\n"
                   f"  options: {opts}" + (f"\n  forced options from the user: {json.dumps(e['forced_options'])}"
                                           if e.get("forced_options") else ""))
    return "\n".join(out)


def engine_catalog(adapters: Iterable[Any], config: Any) -> list[dict]:
    """Summaries of the given (already filtered) adapters for the storyboard prompt."""
    cat = []
    for a in adapters:
        m = getattr(a, "manifest", {}) or {}
        cat.append({
            "id": a.id, "name": m.get("name", a.id), "description": m.get("description", ""),
            "capabilities": a.capabilities(), "options": a.options(),
            "zero_cost": (m.get("cost") or {}).get("zero_cost"),
            "repo_path": str(a.repo_path) if a.repo_path else None,
            "forced_options": (getattr(config, "engine_options", {}) or {}).get(a.id),
        })
    return cat


def guidance_docs(adapters: Iterable[Any], key: str) -> list[str]:
    docs: list[str] = []
    for a in adapters:
        if not a.repo_path:
            continue
        rels = list(GUIDANCE.get(a.id, {}).get(key, []))
        extra = ((getattr(a, "manifest", {}) or {}).get("guidance") or {}).get(key) or []
        rels += [r for r in extra if isinstance(r, str)]
        for rel in rels:
            p = Path(a.repo_path) / rel
            if p.exists() and str(p) not in docs:
                docs.append(str(p))
    return docs


_TASKS = {
    "research": (
        "Research the topic of this video. Delegate in parallel: the `researcher` subagent gathers sources and "
        "key facts; the `fact-checker` subagent spot-checks the most important claims as they come in. Merge the "
        "results. Write `research/research.json` (schema below) with a summary, key_facts "
        "({{id, claim, source_ids}}), sources and 3-5 possible angles. Also write a human-readable "
        "`research/sources.md` listing every source."),
    "factcheck": (
        "Fact-check every key fact in `research/research.json`. Delegate to the `fact-checker` subagent (split "
        "the facts across several parallel fact-checker calls if there are many). Write "
        "`research/factcheck.json` with one item per claim (claim_id = the key fact id)."),
    "concept": (
        "Pitch the film. Read `research/research.json`, `research/factcheck.json` and `project.json`. Use the "
        "`scriptwriter` subagent to draft the logline, angle, structure (list of acts/sections) and look. Write "
        "`brief.json`. Its `config` field must be the video config from `project.json`, copied unchanged."),
    "script": (
        "Write the narration script. Read `brief.json` and the research/fact-check files. Delegate the draft to "
        "the `scriptwriter` subagent, then have the `fact-checker` subagent check every factual line against "
        "`research/factcheck.json` (only verified or corrected facts may be stated). Lines get ids L01, L02, …; "
        "the total spoken length must fit the target duration (about 150 words per minute at medium pacing). "
        "Fill word_count and estimated_seconds. Write `scripts/script.json`."),
    "storyboard": (
        "Break the script into scenes and segments and decide which engine renders each segment. Delegate to the "
        "`storyboard-artist` subagent. Rules:\n"
        "- Every script line id appears in exactly one scene, in order. Scene durations add up to the target.\n"
        "- Consecutive scenes rendered by the same engine with the same options form one segment (ids s1, s2, …).\n"
        "- Each segment names an `engine` from the list below that supports scene_render, its `engine_options` "
        "(only options that engine offers) and a `reason` explaining why that engine fits these scenes. Each "
        "scene repeats its segment's engine.\n"
        "- Use the engines' strengths together (collaboration): e.g. archival/photo boards, code-drawn styles, "
        "motion graphics — pick per segment, not one engine for everything, unless only one engine fits or the "
        "user restricted the engines.\n"
        "- Honour the user's engine restrictions and forced options exactly.\n"
        "Write `storyboard/storyboard.json`."),
    "asset_plan": (
        "Plan every asset the storyboard needs (images, footage, maps, music, sfx, logos, data). Delegate to the "
        "`asset-librarian` subagent. For each item give id, kind, source, source_url when known, licence, credit "
        "and provider (the engine or tool that will fetch/make it); status stays \"planned\". Reference the asset "
        "ids from the storyboard scenes (`asset_ids`) where they are already present. Write `assets/manifest.json`."),
    "assets": (
        "Acquire the planned assets in `assets/manifest.json` using only free, licence-checked sources. Delegate "
        "to the `asset-librarian` subagent. Save files under assets/images|footage|music|sfx/, then update each "
        "item's status (acquired/missing/rejected), path, licence and credit. Write the updated "
        "`assets/manifest.json`."),
}


def creative_prompt(
    *, key: str, video: Any, ws: Path, lane: str, config: Any, gate: bool,
    engines: Optional[list[dict]] = None, guidance: Optional[list[str]] = None,
    feedback: Optional[str] = None, feedback_kind: str = "changes", resume: bool = False,
    excluded: Iterable[str] = (),
) -> str:
    if key not in _TASKS:
        raise KeyError(f"no SpaceStudio prompt for stage {key!r}")
    out_file, model = CREATIVE_OUTPUTS[key]
    cfg = config.model_dump() if hasattr(config, "model_dump") else dict(config)
    parts = [stage_header(key, video.id, ws, lane), ""]
    parts.append(f"# Stage: {key}" + (" (gated: SpaceStudio asks the user to approve the result)" if gate else ""))
    parts.append(f"Video title: {video.title}\nIdea: {video.idea}")
    if cfg.get("brief"):
        parts.append(f"Creative brief from the user:\n{cfg['brief']}")
    parts.append("Video config (JSON):\n" + json.dumps(cfg, indent=1, default=str))
    parts.append("## Task\n" + _TASKS[key].replace("{{", "{").replace("}}", "}"))
    subs = STAGE_DELEGATION.get(key)
    if subs:
        parts.append("Subagents available for this stage (Task tool): " + ", ".join(f"`{s}`" for s in subs) + ".")
    if key == "storyboard":
        engines = engines or []
        parts.append("## Engines you may assign (enabled, allowed for this video, support scene_render)\n"
                     + _engine_lines(engines))
        if cfg.get("engines"):
            parts.append("The user restricted this video to: " + ", ".join(cfg["engines"]))
        ex = list(excluded) or cfg.get("excluded_engines") or []
        if ex:
            parts.append("Never assign: " + ", ".join(ex))
        if cfg.get("zero_cost", True):
            parts.append("Zero-cost mode: only choose options flagged zero_cost (not paid).")
        parts.append("ENGINES_AVAILABLE=" + ",".join(e["id"] for e in engines))
    if guidance:
        parts.append("## Reference (read as guidance if useful; do not follow their own approval flow or write "
                     "into those repos)\n" + "\n".join(f"- {g}" for g in guidance))
    parts.append(f"## Output\nWrite `{out_file}` (workspace-relative) matching this JSON Schema ({model}):\n"
                 + json.dumps(ix.json_schema(model), separators=(",", ":")))
    if feedback:
        if feedback_kind == "answer":
            parts.append("## USER ANSWER\nYou asked a question earlier in this stage. The user answered:\n"
                         + feedback + "\nContinue the stage with this answer.")
        else:
            parts.append("## CHANGE REQUEST\nThe user reviewed the previous version and asked for changes:\n"
                         + feedback + f"\nRevise `{out_file}` accordingly (the previous version is kept by "
                         "SpaceStudio under .spacestudio/versions/). Keep what the user did not ask to change.")
    if resume:
        parts.append("## RESUME\nThis stage was interrupted (the app restarted). Check which outputs already exist "
                     "and are valid before redoing any work.")
    parts.append("When done, append the stage_completed event (see the protocol) and end your turn.")
    parts.append(expected_outputs_footer([out_file]))
    return "\n\n".join(parts)


def engine_stage_extras(feedback: Optional[str], feedback_kind: str, resume: bool, gate: bool) -> str:
    """Appended to adapter-written prompts when the adapter did not handle feedback/resume itself."""
    parts = []
    if feedback and feedback_kind == "answer":
        parts.append("## USER ANSWER\n" + feedback)
    elif feedback:
        parts.append("## CHANGE REQUEST\n" + feedback)
    if resume:
        parts.append("## RESUME\nThis stage was interrupted. Verify existing outputs before redoing work.")
    if gate:
        parts.append("This stage is gated: after writing the outputs append awaiting_approval (or stage_completed) "
                     "and end your turn.")
    return "\n\n".join(parts)


IDEAS_SCHEMA = {
    "type": "object",
    "properties": {"ideas": {"type": "array", "items": {
        "type": "object", "properties": {"title": {"type": "string"}, "idea": {"type": "string"}},
        "required": ["title", "idea"]}}},
    "required": ["ideas"],
}


def ideas_prompt(topic: str, count: int, video_type: str) -> str:
    return (f"Suggest {count} distinct short-video ideas about: {topic}. Video type: {video_type}. "
            "Each idea needs a catchy title and a 1-2 sentence description of the angle. Do not use any tools. "
            "Return only the structured output.")
