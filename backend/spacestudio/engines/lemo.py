"""lemo-opuscar adapter (lemomo-ai/lemo-opuscar; code MIT, docs CC BY-NC 4.0 — never bundled).

Verified against v2.0.0: AGENTS.md (workflow), DIRECTOR.md §4/§5/§11 (treatment, look, storyboard,
delivery list), TECHNIQUE.md, core/README.md (tool commands, page contract, RENDER_SLOTS, exit codes),
plugin/skills/lemo-opuscar/SKILL.md (skill mode: project = `<name>/` in the folder Claude starts from,
tools run from $LIB with the project's absolute path) and scripts/setup.sh.

setup.sh with no arguments is read-only for a library it does not manage: with LEMO_OPUSCAR_HOME set to
an existing clone it only fetches/resets when `git config lemo.managed` is true (its own sparse clone).
health() therefore runs it only when the clone is not managed; otherwise it reads the package markers
(`node_modules/.lemo-ok`, `.venv/.lemo-<tier>`) directly.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from ..workflow.stages import StageSpec
from .base import DetectedStage, EngineHealth, HealthItem
from .generic import (ManifestAdapter, config_dict, git_config_value, is_collaborative, run_cmd, segment_id,
                      slug)

NATIVE_PROJECT = "film"
_TIER = re.compile(r"packages:\s*core\s*(✓|✗)\s*·\s*voice\s*(✓|✗)\s*·\s*music\s*(✓|✗)")


def load_styles(repo: Path) -> list[dict[str, Any]]:
    """Every style in styles/*/style.json (folders starting with '_' are templates)."""
    out = []
    for f in sorted(Path(repo).glob("styles/*/style.json")):
        if f.parent.name.startswith("_"):
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        needs = list(d.get("needs") or [])
        out.append({
            "id": d.get("slug") or f.parent.name,
            "name": d.get("en") or f.parent.name,
            "name_cn": d.get("cn", ""),
            "category": d.get("category_en", ""),
            "category_cn": d.get("category_cn", ""),
            "uses": list(d.get("uses") or []),
            "line": d.get("line", ""),
            "needs": needs,
            "zero_cost": "image-generation" not in needs,
            "demo_seconds": d.get("dur"),
        })
    return out


class LemoAdapter(ManifestAdapter):
    id = "lemo-opuscar"

    # ---- options -------------------------------------------------------------------------------
    def load_options(self) -> None:
        opts = dict(self.manifest.get("options") or {})
        if self.repo_path and self.repo_path.is_dir():
            styles = load_styles(self.repo_path)
            opts["styles"] = styles
            opts["style_count"] = len(styles)
            opts["categories"] = sorted({s["category"] for s in styles if s["category"]})
        self.manifest["options"] = opts

    def styles(self) -> list[dict]:
        return list((self.manifest.get("options") or {}).get("styles") or [])

    def style(self, slug_: str) -> Optional[dict]:
        return next((s for s in self.styles() if s["id"] == slug_), None)

    @property
    def lib(self) -> Path:
        return self.repo_path or Path(".")

    @property
    def setup_sh(self) -> Path:
        return self.lib / "plugin" / "skills" / "lemo-opuscar" / "scripts" / "setup.sh"

    # ---- health --------------------------------------------------------------------------------
    def health(self, zero_cost: bool = True) -> EngineHealth:
        items, commit = self.common_health()
        if not self.repo_path or not self.repo_path.is_dir():
            return self.finish_health(items, commit, zero_cost)
        items.append(self.node_health(">=20"))
        ff = self.ffmpeg_health(">=7", fix=("lemo rejects FFmpeg 6.x (it under-reports audio length through loudnorm and"
                                            " mux.sh refuses the file): install a static FFmpeg 7+ build and put it first"
                                            " on PATH (lemo's tools call `ffmpeg` from PATH)."))
        items.append(ff)
        items.append(self._python_item())
        items.extend(self._packages())
        n = len(self.styles())
        items.append(HealthItem("styles", "Styles", "ok" if n else "error", f"{n} styles in styles/*/style.json",
                                "" if n else "the clone has no styles/ folder; re-clone the library"))
        star = [s["name"] for s in self.styles() if not s["zero_cost"]]
        codex = shutil.which("codex")
        items.append(HealthItem("image_generation", "Image generator for ✦ styles", "ok" if codex else "warning",
                                f"codex on PATH ({codex})" if codex else f"no Codex CLI: {', '.join(star)} cannot start",
                                "" if codex else "only needed for ✦ styles; they are hidden in zero-cost mode anyway"))
        if len(str(self.lib.resolve()).encode()) >= 160:
            items.append(HealthItem("path_length", "Library path length", "warning",
                                    "path is 160 bytes or longer: Kokoro/espeak-ng cannot find its data",
                                    "move the clone to a shorter path"))
        return self.finish_health(items, commit, zero_cost, zero_cost_ready=True)

    def _python_item(self) -> HealthItem:
        for c in ("python3.12", "python3.13", "python3.11", "python3", "python"):
            if not shutil.which(c):
                continue
            code, out = run_cmd([c, "-c", "import sys; print(sys.version.split()[0]); sys.exit(sys.version_info < (3, 11))"],
                                timeout=15)
            if code == 0:
                return HealthItem("python", "Python 3.11+ (or uv)", "ok", f"{c} {out.strip()}")
        if shutil.which("uv"):
            return HealthItem("python", "Python 3.11+ (or uv)", "ok", "uv on PATH")
        return HealthItem("python", "Python 3.11+ (or uv)", "error", "not found", "install Python 3.11-3.13 or uv")

    def _packages(self) -> list[HealthItem]:
        lib = self.lib
        managed = (git_config_value(lib, "lemo", "managed") or "").lower() == "true"
        is_lib = (lib / "AGENTS.md").is_file() and (lib / "core" / "render").is_dir()
        tiers: Optional[tuple[bool, bool, bool]] = None
        notes: list[str] = []
        if is_lib and not managed and self.setup_sh.is_file() and shutil.which("sh"):
            env = {**os.environ, "LEMO_OPUSCAR_HOME": str(lib)}
            code, out = run_cmd(["sh", str(self.setup_sh)], cwd=lib, timeout=60, env=env)
            m = _TIER.search(out or "")
            if m:
                tiers = tuple(x == "✓" for x in m.groups())  # type: ignore[assignment]
                notes = [ln.strip() for ln in out.splitlines() if ln.startswith("!")]
        source = "setup.sh status line"
        if tiers is None:
            source = "package markers" + (" (managed clone: setup.sh not run, it would git fetch)" if managed else "")
            tiers = ((lib / "node_modules" / ".lemo-ok").is_file() and (lib / ".venv" / ".lemo-core").is_file(),
                     (lib / ".venv" / ".lemo-voice").is_file() and (lib / "core" / "tts" / "kokoro-v1.0.onnx").is_file(),
                     (lib / ".venv" / ".lemo-music").is_file())
        core, voice, music = tiers
        items = [
            HealthItem("pkg_core", "Core packages (npm, headless Chromium, .venv)", "ok" if core else "error",
                       f"{'installed' if core else 'not installed'} ({source})",
                       "" if core else "run the setup step 'deps' (sh plugin/skills/lemo-opuscar/scripts/setup.sh deps)"),
            HealthItem("pkg_voice", "Voice tier (Kokoro, faster-whisper)", "ok" if voice else "warning",
                       "installed" if voice else "not installed: lemo cannot narrate",
                       "" if voice else "run the setup step 'deps-voice' when a film needs lemo narration"),
            HealthItem("pkg_music", "Music tier (numba for pluck.py)", "ok" if music else "warning",
                       "installed" if music else "not installed (only plucked-string scores need it)",
                       "" if music else "run the setup step 'deps-music' if a score needs pluck.py"),
        ]
        for n in notes:
            if "missing:" in n:
                items.append(HealthItem("setup_missing", "setup.sh reports", "warning", n.lstrip("! ")))
        return items

    # ---- stages --------------------------------------------------------------------------------
    def native_stages(self, config: dict) -> list[StageSpec]:
        cfg = config_dict(config)
        eo = (cfg.get("engine_options") or {}).get(self.id) or {}
        defs = []
        for d in self.manifest_stages():
            if d.get("when") and not eo.get(d["when"]):
                continue
            if d["key"] == "deliver":
                d = {**d, "produces": [f"{NATIVE_PROJECT}.mp4", *d.get("produces", [])]}
            defs.append(d)
        return self.stage_specs(defs, project_dir=f"work/{self.id}/{NATIVE_PROJECT}")

    def project_for(self, workspace: Path, stage: StageSpec) -> Path:
        lane = self.lane_dir(workspace)
        if stage.key.startswith("segment:"):
            return lane / slug(segment_id(stage), "segment")
        if stage.key == "narration":
            return lane / "narration"
        return lane / NATIVE_PROJECT

    # ---- prompts -------------------------------------------------------------------------------
    def _intro(self) -> str:
        L = self.lib
        return (f"Engine: lemo-opuscar, in skill mode. The library is LIB={L} (LEMO_OPUSCAR_HOME is set to it, so"
                f" `sh {self.setup_sh}` uses this clone as it is). Follow {L}/AGENTS.md with the skill-mode rules of"
                f" {L}/plugin/skills/lemo-opuscar/SKILL.md (also loaded as the plugin skill `lemo-opuscar`):"
                f" run tools from LIB with the project's absolute path, e.g. `cd {L} && node core/render/still.mjs"
                f" \"<project>\" 1.5 3`; Python is `{L}/.venv/bin/python`; pages reach library files with absolute URLs"
                " (/core/lib.js, /node_modules/three/...); build.sh starts with LIB=<path>. Commands and flags are in"
                f" {L}/core/README.md. Never edit core/ or styles/, never build inside styles/. Never run"
                " `setup.sh deps` yourself: installs are confirmed by the user in SpaceStudio; if a tier is missing,"
                " say which in a needs_input event.")

    def _style_rules(self, style_id: Optional[str], cfg: dict) -> str:
        if not style_id:
            return ("No style was chosen. Append a needs_input event suggesting two or three styles from "
                    f"{self.lib}/styles/README.md that fit the topic, and end your turn.")
        st = self.style(style_id)
        if st is None:
            return (f"Style `{style_id}` is not in {self.lib}/styles/. Append a needs_input event naming the closest two"
                    " or three styles, and end your turn.")
        txt = f"Style: {st['name']} (`{st['id']}`), {self.lib}/styles/{st['id']}/STYLE.md is fixed: keep all of it."
        if not st["zero_cost"]:
            if cfg.get("zero_cost", True):
                txt += (" This ✦ style draws with an image generator, which zero-cost mode does not allow: append an"
                        " error event saying so (suggest other styles) and stop.")
            else:
                txt += (f" This ✦ style needs an image generator: run `cd {self.lib} && python3 core/image/gen_image.py"
                        " --check` first (core/README.md, Image generation).")
        return txt

    def stage_prompt(self, stage: StageSpec, workspace: Path, config: dict, feedback: Optional[str] = None,
                     resume: bool = False) -> str:
        ws = Path(workspace).resolve()
        cfg = config_dict(config)
        eo = (cfg.get("engine_options") or {}).get(self.id) or {}
        L = self.lib
        PY = f"{L}/.venv/bin/python"
        P = self.project_for(ws, stage)
        slots = self.setting("max_render_jobs", 1) or 1
        lang = cfg.get("narration_language") or "en"
        if stage.key == "narration":
            tts = (f"`cd {L} && {PY} core/tts/tts.py {P}/lines.json {P}/voice`" if not lang.startswith("zh") else
                   f"`cd {L} && {PY} core/tts/tts_zh.py {P}/lines.json {P}/voice` (edge-tts: free online Microsoft"
                   f" service; offline alternative: tts.py with \"lang\": \"cmn\" and a zf_*/zm_* voice)")
            task = f"""Record the narration with lemo's offline Kokoro voice (TECHNIQUE.md §4, core/README.md "Audio").
1. The voice tier must be installed: `sh {self.setup_sh}` prints `packages: core … · voice ✓|✗`. If voice is ✗,
   append needs_input asking the user to run the SpaceStudio setup step "deps-voice" for lemo-opuscar, and end your turn.
2. mkdir -p {P}. Write {P}/lines.json = [{{"id": "<SpaceStudio line id>", "text": "<spoken text, or text>"}}, ...] for
   every line of {ws}/scripts/script.json, in order (optional per line: voice, speed, lang; defaults af_bella, 0.92, en-us).
   Voice option: {eo.get('voice') or 'default'}.
3. {tts} writes <id>.wav (24 kHz, trimmed) and dur.json. Exit 1 = bad input / missing model / path too long.
4. Speech check: `cd {L} && {PY} core/tts/asr_check.py {P}/lines.json {P}/voice --lang {lang.split('-')[0]}`; re-record
   any line that fails (an `asr` field can set the expected text for names).
5. Join: copy each clip to {ws}/narration/lines/<id>.wav, then concatenate them with ffmpeg into
   {ws}/narration/narration.wav (48 kHz) with short silences between lines (about 0.35 s; longer, about 0.8 s, where the
   script's `section` changes) and about 0.3 s lead-in. Measure every clip with ffprobe and write
   {ws}/narration/timing.json with each line's real start/end, duration = the WAV's ffprobe duration,
   engine "{self.id}", voice = the Kokoro voice used.
{self.config_summary(cfg)}"""
        elif stage.key.startswith("segment:"):
            seg = segment_id(stage)
            start, end = self.segment_window(stage, ws)
            dur = f"{end - start:.3f}" if start is not None else "<end - start>"
            t0 = f"{start:.3f}" if start is not None else "<start>"
            style_id = stage.params.get("engine_options", {}).get("style") or eo.get("style")
            task = f"""Render segment {seg} as a lemo-opuscar page, timed to SpaceStudio's shared narration.
{self._style_rules(style_id, cfg)}
1. Project folder {P} (skill mode). Read STYLE.md, then {L}/DIRECTOR.md (§3-§8) and {L}/TECHNIQUE.md (§2 page contract, §3 rhythm).
   Write {P}/TREATMENT.md (DIRECTOR.md §4, scaled to this segment) BEFORE opening the style's DEMO.md or demo code; then use
   the demo only for technique (`sh {self.setup_sh} demo <slug>` fetches it; an Edit & Documentary style follows AGENTS.md
   "Treatment first").
2. Build {P}/index.html with the page contract of {L}/core/README.md: window.DUR = {dur}, a deterministic window.render(t),
   window.READY = true once fonts and images load, window.TEXTS(t) for readcheck. Lay it out from innerWidth x innerHeight
   for {cfg['resolution']}. Time every cut and key action to the narration lines of this segment: their start/end in
   {ws}/narration/timing.json minus {t0}. Use the acquired assets from {ws}/assets/manifest.json that its scenes name
   (copy them into {P}/).
3. Look: `cd {L} && node core/render/still.mjs "{P}" <t> <t> ... --size {cfg['resolution']}` and check the stills against
   STYLE.md; `cd {L} && node core/render/readcheck.mjs "{P}" --size {cfg['resolution']}` must pass (exit 0).
4. Render picture only (RENDER_SLOTS={slots} is set; use 3 workers when other films may render):
   `cd {L} && spacestudio-slot render -- node core/render/video.mjs "{P}" --fps {cfg['fps']} --workers 3 --size {cfg['resolution']} --out "{ws}/scenes/{seg}.mp4"`
   then verify with ffprobe (duration {dur} s, {cfg['resolution']}, {cfg['fps']} fps).
5. Write {ws}/scenes/{seg}.json and {ws}/credits/{self.id}-{seg}.md in lemo's CREDITS form (TECHNIQUE.md §11: every
   third-party asset, font, sample or image with source and licence). No LemoLab credit or sign-off in the picture.
{self.config_summary(cfg)}"""
        else:
            task = self._native_task(stage, ws, cfg, eo, P, PY, slots)
        return self.compose_prompt(stage, ws, cfg, self._intro() + "\n\n" + task, feedback, resume,
                                   native=not is_collaborative(stage))

    def _native_task(self, stage, ws, cfg, eo, P, PY, slots) -> str:
        L = self.lib
        name = NATIVE_PROJECT
        style_id = eo.get("style")
        wants_sb = bool(eo.get("storyboard"))
        tasks = {
            "brief": f"""AGENTS.md Workflow step 1 (Brief). SpaceStudio has already collected the one round of questions; the answers are
the video config below, the idea in {ws}/project.json and {ws}/brief.json if present, and the user's own material in
{ws}/assets/user/. {self._style_rules(style_id, cfg)}
Run `sh {self.setup_sh}` (no arguments) and report missing system tools or package tiers in a needs_input event if
they block the film. Storyboard review: {'yes' if wants_sb else 'no'}. Fill every other gap with a sensible default and
write the brief summary (a few lines: style, topic, language, length, material, voice) to {P}/BRIEF.md. Do not ask more questions.""",
            "treatment": f"""AGENTS.md Workflow step 2. Write {P}/TREATMENT.md (DIRECTOR.md §4: three candidate structures and the choice,
logline and arc, benchmark, shot list, beat sheet, cue map, sound design table, subtitle design) BEFORE opening DEMO.md
or the demo code (Edit & Documentary styles: material first, AGENTS.md "Treatment first").""",
            "look": f"""AGENTS.md Workflow step 3 (DIRECTOR.md §5): style frames (three, one the signature shot) or a model sheet, drawn
with the real code in {P}/index.html: `cd {L} && node core/render/still.mjs "{P}" <t> ... --size {cfg['resolution']}`
(stills land in {P}/stills/). Check them against STYLE.md yourself.""",
            "storyboard": f"""AGENTS.md Workflow step 4: the user asked for a storyboard. Render 6-9 key shots with the real drawing code and
tile them with `cd {L} && {PY} core/render/sheet.py {P}/storyboard.jpg <stills...> --cols 3` with shot number, framing,
duration and line under each. Put the logline and what you are least sure about in the approval summary.""",
            "produce": f"""AGENTS.md Workflow step 5 (TECHNIQUE.md): voice -> speech check -> score -> animation -> mix -> render.
Voice: `cd {L} && {PY} core/tts/tts.py {P}/lines.json {P}/voice` and `{PY} core/tts/asr_check.py ...` (voice tier; if it is
missing, append needs_input asking for the SpaceStudio setup step "deps-voice"). Score from free sample libraries or
synthesis (sampler.py credits(names) writes the CREDITS lines). Render with RENDER_SLOTS={slots}:
`cd {L} && spacestudio-slot render -- node core/render/video.mjs "{P}" --fps {cfg['fps']} --size {cfg['resolution']} --out "{P}/out/video.mp4"`
then `cd {L} && sh core/render/mux.sh "{P}/out/video.mp4" "{P}/mix.wav" "{P}/{name}.mp4"` (-14 LUFS).""",
            "deliver": f"""AGENTS.md Workflow step 6 and DIRECTOR.md §11: run the review loop and the checks (speech check, loudness about
-14 LUFS, no black frames, subtitles in sync, `grep -rniE "lemo ?lab|opus 5\\.5" {P}` clean), one fix round. Deliver in
{P}: {name}.mp4, poster.jpg, {name}.srt (srt.py), TREATMENT.md, CREDITS, build.sh. Hand over to SpaceStudio: copy
{name}.mp4 to {ws}/renders/final.mp4, the SRT to {ws}/subtitles/{cfg['language']}.srt and CREDITS to
{ws}/credits/{self.id}.md.""",
        }
        return tasks.get(stage.key, stage.description) + "\n" + self.config_summary(cfg)

    def collect_credits(self, workspace: Path) -> list[str]:
        ws = Path(workspace)
        out = super().collect_credits(ws)
        native = ws / "work" / self.id / NATIVE_PROJECT / "CREDITS"
        if native.exists():
            out.append(f"work/{self.id}/{NATIVE_PROJECT}/CREDITS")
        return out
