"""cinematic-skill adapter (Moh4696/cinematic-skill, MIT).

Verified against the repo (v1.0.1): every script lives in skills/cinematic-film/scripts/ and takes
``--project <folder>`` except ``new_project.py <folder>`` (positional) and ``check_setup.py`` (no args).
The film reads ``<project>/audio/timing.json`` ({total_duration, lead_in, tail_out, tempo,
segments:[{id, scene, caption, vo, start, end, duration}]}, written by build_narration.py and read by
engine.Doc), so a collaborative segment can be timed to SpaceStudio's shared narration by writing that
file directly. ``render.py --t0/--t1 --size --workers --out`` without ``--audio`` writes picture only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..workflow.stages import StageSpec
from .base import DetectedStage, EngineHealth, HealthItem
from .generic import ManifestAdapter, config_dict, is_collaborative, run_cmd, segment_id, slug

NATIVE_PROJECT = "film"
_LINE = re.compile(r"^\s*\[(ok|MISSING|--)\]\s+(.*?)(?:\s{2,}(.*))?$")


class CinematicAdapter(ManifestAdapter):
    id = "cinematic-skill"

    # ---- paths ---------------------------------------------------------------------------------
    @property
    def skill_dir(self) -> Path:
        return (self.repo_path or Path(".")) / "skills" / "cinematic-film"

    @property
    def scripts(self) -> Path:
        return self.skill_dir / "scripts"

    def workers(self) -> int:
        return max(1, int(self.setting("render_workers", 2) or 2))

    # ---- health --------------------------------------------------------------------------------
    def health(self, zero_cost: bool = True) -> EngineHealth:
        items, commit = self.common_health()
        if self.repo_path and (self.scripts / "check_setup.py").exists():
            items.extend(self._check_setup())
        return self.finish_health(items, commit, zero_cost, zero_cost_ready=True)

    def _check_setup(self) -> list[HealthItem]:
        """Run the skill's own check_setup.py with the configured Python and turn its lines into items."""
        py = self.python_bin()
        code, out = run_cmd([py, str(self.scripts / "check_setup.py")], cwd=self.repo_path, timeout=120)
        if code is None:
            return [HealthItem("check_setup", "check_setup.py", "error", out, f"install Python 3.9+ or fix python_bin ({py})")]
        items: list[HealthItem] = []
        for n, line in enumerate(out.splitlines()):
            m = _LINE.match(line)
            if m:
                tag, what, extra = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
                status = {"ok": "ok", "MISSING": "error", "--": "warning"}[tag]
                fix = extra if tag == "MISSING" else (extra if "optional" in extra else "")
                if what.startswith("no local voice"):
                    fix = ("install espeak-ng (apt install espeak-ng) or set engine option tts_cmd to a local TTS"
                           " command with {txt} and {out}; without it cinematic-skill cannot narrate")
                items.append(HealthItem(f"setup:{slug(what)[:40] or n}", what, status, extra if tag != "MISSING" else "", fix))
            elif line.strip().startswith("memory"):
                items.append(HealthItem("memory", "Render memory", "ok",
                                        line.strip() + f"; SpaceStudio passes --workers {self.workers()}"))
        if not items:
            items.append(HealthItem("check_setup", "check_setup.py", "error" if code else "warning", out.strip()[:300]))
        elif code not in (0, None) and not any(i.status == "error" for i in items):
            items.append(HealthItem("check_setup", "check_setup.py", "error", f"exit code {code}", "run it by hand to see why"))
        return items

    # ---- stages --------------------------------------------------------------------------------
    def native_stages(self, config: dict) -> list[StageSpec]:
        return self.stage_specs(self.manifest_stages(), project_dir=f"work/{self.id}/{NATIVE_PROJECT}")

    def project_for(self, workspace: Path, stage: StageSpec) -> Path:
        lane = self.lane_dir(workspace)
        if stage.key.startswith("segment:"):
            return lane / slug(segment_id(stage), "segment")
        if stage.key in ("assets", "narration"):
            return lane / stage.key
        return lane / NATIVE_PROJECT

    # ---- prompts -------------------------------------------------------------------------------
    def _intro(self, py: str) -> str:
        return (f"Engine: cinematic-skill. Read {self.skill_dir}/SKILL.md first (it is also loaded as the plugin skill"
                f" `cinematic-film`) and the references it names when a step needs them. Scripts are in {self.scripts};"
                f" run them as `{py} {self.scripts}/<script>.py ... --project <absolute project folder>` (new_project.py"
                " takes the folder as a positional argument). Never edit files inside the skill folder.")

    def stage_prompt(self, stage: StageSpec, workspace: Path, config: dict, feedback: Optional[str] = None,
                     resume: bool = False) -> str:
        ws = Path(workspace).resolve()
        cfg = config_dict(config)
        eo = (cfg.get("engine_options") or {}).get(self.id) or {}
        py = self.python_bin()
        S = self.scripts
        P = self.project_for(ws, stage)
        look = eo.get("look") or "wall"
        grade = eo.get("grade")
        tts_cmd = eo.get("tts_cmd")
        workers = self.workers()
        new = f"`{py} {S}/new_project.py {P}` (only if {P} does not exist yet; it copies assets/template/)"
        if stage.key == "assets":
            task = f"""Acquire the still images of the asset plan with cinematic-skill's licence-checked Wikimedia Commons fetcher.
1. Start a helper project: {new}. Use it only as a download folder.
2. For every manifest item with status "planned" and kind image, map, logo or other still: find a free file with
   `{py} {S}/fetch_commons.py search "<words from the item and its storyboard scene>" --limit 12` and
   `{py} {S}/fetch_commons.py category "Category:<name>" --limit 40`, then list it in {P}/photos.json as
   {{"id": "<asset id>", "kind": "photo" or "doc", "commons": "File:<title>"}} (see {S}/fetch_commons.py docstring).
3. `{py} {S}/fetch_commons.py get --project {P}` downloads each file to {P}/assets/photos/ and fills file, source,
   credit, license and license_ok; it REFUSES non-free licences: never pass --allow-nonfree. Then
   `{py} {S}/fetch_commons.py credits --project {P}` writes {P}/credits.md.
4. Copy each downloaded file to {ws}/assets/images/<asset id><ext> and update its manifest item from photos.json:
   status "acquired", path, license, credit, source "wikimedia", source_url (the file page), provider "{self.id}".
   Copy {P}/credits.md to {ws}/credits/{self.id}-assets.md.
5. Items this engine cannot fetch (footage, music, sfx, voice) or with no free match: status "missing" and the reason
   in credit, unless a user file for it already exists under {ws}/assets/user/ (then acquired, license "user-provided").
{self.config_summary(cfg)}"""
        elif stage.key == "narration":
            voice = (f"`{py} {S}/tts_say.py --project {P} --cmd '{tts_cmd}'`" if tts_cmd else
                     f"`{py} {S}/tts_say.py --project {P}` (macOS `say` or espeak-ng; check with `which espeak-ng say`;"
                     " if neither exists, append a needs_input event asking for a local TTS command for engine option"
                     " tts_cmd, e.g. Piper, and end your turn)")
            task = f"""Record the narration with cinematic-skill's voice tools and hand it over in SpaceStudio's format.
1. {new}.
2. Write {P}/script.json in the format of {self.skill_dir}/assets/template/script.json (read it): one take per run
   of consecutive script lines with the same `section` (take names like "T01_<section>"), each line with an integer
   `id` (1, 2, 3... in script order), `text`, `spoken` (the SpaceStudio line's spoken text, or text when empty),
   `hero` and `typed` (empty strings are fine). Keep a map integer id -> SpaceStudio line id.
3. Voice: {voice}. Then `{py} {S}/split_takes.py --project {P}`; listen to/inspect every line it flags in
   {P}/audio/lines/split_report.json and fix cuts with {P}/audio/overrides.json as split_takes.py documents.
   Then `{py} {S}/build_narration.py --project {P} --lead 0.3 --tail 0.5`.
4. Convert {P}/audio/narration.m4a to {ws}/narration/narration.wav with ffmpeg (`ffmpeg -y -i ... -ar 48000 -ac 1 ...`),
   cut per-line clips from it into {ws}/narration/lines/<line id>.wav, and write {ws}/narration/timing.json from
   {P}/audio/timing.json (segments[].start/end, mapped back to SpaceStudio line ids; duration = total_duration,
   which must match the WAV), engine "{self.id}", voice = the voice used.
{self.config_summary(cfg)}"""
        elif stage.key.startswith("segment:"):
            seg = segment_id(stage)
            start, end = self.segment_window(stage, ws)
            dur = f"{end - start:.3f}" if start is not None else "<end - start>"
            t0 = f"{start:.3f}" if start is not None else "<start>"
            task = f"""Render segment {seg} as a cinematic-skill board film, timed to SpaceStudio's shared narration.
1. {new}. Look: `{look}` (Film(..., look="{look}") in scene.py){f'; grade `{grade}`' if grade else ''}.
   Segment engine_options in the storyboard override these.
2. Write {P}/script.json in the format of {self.skill_dir}/assets/template/script.json: one take per storyboard scene of
   segment {seg}, the lines named in each scene's line_ids, integer ids 1..n in order, text, spoken, hero (the scene's
   visual for that line, one hero per line) and typed. Run `{py} {S}/make_beats.py --project {P}` and fix its lint.
3. Voice = the shared narration (do NOT record a new one). Write {P}/audio/timing.json exactly in the format
   build_narration.py writes and engine.Doc reads: {{"total_duration": {dur}, "lead_in": 0, "tail_out": 0, "tempo": 1.0,
   "segments": [{{"id", "scene", "caption", "vo", "start", "end", "duration"}}]}}, with each line's start/end from
   {ws}/narration/timing.json minus {t0} (vo = spoken text, caption = text). For probes and drafts cut the audio:
   `ffmpeg -y -ss {t0} -t {dur} -i {ws}/narration/narration.wav -c:a aac {P}/audio/narration.m4a`.
4. Images: copy the acquired manifest assets this segment's scenes use (asset_ids) into {P}/assets/photos/ and list
   them in {P}/photos.json with "file", "kind", "credit", "license", "source" taken from the manifest; then
   `{py} {S}/prep_assets.py --project {P}`{f' --grade {grade}' if grade else ''} (and make_map.py if a scene needs a map).
   On Linux run `{py} {S}/get_fonts.py --project {P}` once.
5. Write {P}/scene.py (read {self.skill_dir}/references/film.md; patterns in assets/template/scene.py and
   assets/wright-flyer/scene.py), with Film(doc.duration, fps={cfg['fps']}, look="{look}"). Self-review with
   `{py} {S}/render.py --project {P} --probe <one time per line> --size 1280x720` and look at every still.
6. Render picture only (no --audio) at the config size with capped workers:
   `spacestudio-slot render -- {py} {S}/render.py --project {P} --t0 0 --t1 {dur} --size {cfg['resolution']} --workers {workers} --out {ws}/scenes/{seg}.mp4`
   then check it with ffprobe (duration {dur} s, {cfg['resolution']}); re-encode with ffmpeg (-an, -t {dur}, fps {cfg['fps']}) if it is off.
7. Write {ws}/scenes/{seg}.json and copy {P}/credits.md (run `{py} {S}/fetch_commons.py credits --project {P}` if it
   is missing; list the recreations from photos.json too) to {ws}/credits/{self.id}-{seg}.md.
{self.config_summary(cfg)}"""
        else:
            task = self._native_task(stage, ws, cfg, P, py, look, grade, tts_cmd, workers)
        return self.compose_prompt(stage, ws, cfg, self._intro(py) + "\n\n" + task, feedback, resume,
                                   native=not is_collaborative(stage))

    def _native_task(self, stage, ws, cfg, P, py, look, grade, tts_cmd, workers) -> str:
        S = self.scripts
        cap = (cfg.get("engine_options") or {}).get(self.id, {}).get("caption_style") or "highlighter"
        steps = {
            "story": f"""SKILL.md Workflow steps 1-2 (Story, Beat table). `{py} {S}/new_project.py {P} --title "<title>"` if {P}
does not exist. Research (use SpaceStudio's {ws}/research/ and {ws}/scripts/script.json if they exist), write
{P}/script.json (takes of lines with text, spoken, hero, typed; fact notes alongside; ~{cfg['duration_seconds']} s),
run `{py} {S}/make_beats.py --project {P}` and fix every lint warning. Gate: the user approves {P}/beats.md, facts included.""",
            "images": f"""SKILL.md Workflow step 3 (Images). List every image in {P}/photos.json (id, kind, source). Archive images:
`{py} {S}/fetch_commons.py search|category ...` then `{py} {S}/fetch_commons.py get --project {P}` (never --allow-nonfree)
and `{py} {S}/fetch_commons.py credits --project {P}`. User files from {ws}/assets/user/ go in {P}/assets/photos/ with
credit and licence filled in by hand. No AI stills in zero-cost mode.""",
            "voice": f"""SKILL.md Workflow steps 4-5 (Scratch voice, Assets). {f"`{py} {S}/tts_say.py --project {P} --cmd '{tts_cmd}'`" if tts_cmd else f"`{py} {S}/tts_say.py --project {P}`"}
(if neither `say` nor `espeak-ng` exists and no tts_cmd option is set, append needs_input asking for a local TTS command),
then `{py} {S}/split_takes.py --project {P}` (check every flagged line) and
`{py} {S}/build_narration.py --project {P}`. If the user supplied a voice file under {ws}/assets/user/, use it as the
takes instead. Then `{py} {S}/make_map.py --project {P}` if the story travels, and
`{py} {S}/prep_assets.py --project {P}`{f' --grade {grade}' if grade else ''}; on Linux `{py} {S}/get_fonts.py --project {P}`.""",
            "look": f"""SKILL.md Workflow steps 6-7 (Scene, Look test). Write {P}/scene.py with Film(doc.duration, fps={cfg['fps']},
look="{look}") (read references/film.md). `{py} {S}/render.py --project {P} --probe <one time per line> --size 1280x720`
and `--strip t0:t1:n` on every big camera move; look at every still and fix the self-review mistakes in SKILL.md.
Gate: show the stills of the opening and the look (paths in the summary).""",
            "draft": f"""SKILL.md Workflow step 8 (Draft).
`spacestudio-slot render -- {py} {S}/render.py --project {P} --t0 0 --t1 30 --size 1280x720 --workers {workers} --out out/draft.mp4 --audio audio/narration.m4a`.
Gate: the user watches {P}/out/draft.mp4.""",
            "sound": f"""SKILL.md Workflow steps 9-10 (Final voice, Sound). Re-run split/build and probes if the final voice changed.
`{py} {S}/render.py --project {P} --cues`, `{py} {S}/build_sfx.py --project {P}`, then
`{py} {S}/mix_soundtrack.py --project {P}` (add --music <file> only with a free-licence score from {ws}/assets/music/).""",
            "finish": f"""SKILL.md Workflow step 11 (Finish).
`spacestudio-slot render -- {py} {S}/render.py --project {P} --size {cfg['resolution']} --workers {workers} --out out/final.mp4 --audio audio/mix.m4a`,
then `{py} {S}/burn_captions.py --project {P} --video out/final.mp4 --style {cap} --out out/final_captioned.mp4 --workers {workers}`
and `{py} {S}/burn_captions.py --project {P} --srt-only --out out/captions.srt`.
Hand over to SpaceStudio: copy the film to {ws}/renders/final.mp4, the SRT to {ws}/subtitles/{cfg['language']}.srt and
{P}/credits.md to {ws}/credits/{self.id}.md.""",
        }
        return steps.get(stage.key, f"Stage {stage.key}: {stage.description}") + "\n" + self.config_summary(cfg)

    # ---- detection -----------------------------------------------------------------------------
    def detect_native(self, workspace: Path, stage: StageSpec) -> DetectedStage:
        det = self.detect_globs(workspace, stage)
        if stage.key == "voice" and det.status == "completed":
            import json as _json
            tj = Path(workspace) / f"work/{self.id}/{NATIVE_PROJECT}/audio/timing.json"
            try:
                t = _json.loads(tj.read_text())
                if not t.get("segments") or not t.get("total_duration"):
                    return DetectedStage(stage.key, "failed", det.outputs, "audio/timing.json has no segments")
            except (OSError, ValueError) as e:
                return DetectedStage(stage.key, "failed", det.outputs, f"audio/timing.json unreadable: {e}")
        return det

    def collect_credits(self, workspace: Path) -> list[str]:
        ws = Path(workspace)
        out = super().collect_credits(ws)
        native = ws / "work" / self.id / NATIVE_PROJECT / "credits.md"
        if native.exists():
            out.append(f"work/{self.id}/{NATIVE_PROJECT}/credits.md")
        return out
