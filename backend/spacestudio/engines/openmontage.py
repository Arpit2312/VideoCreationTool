"""OpenMontage adapter (AGPL-3.0: run as a separate program, never imported; only its JSON is read).

Verified against main @ 2e003e6:
- CLAUDE.md -> AGENT_GUIDE.md (Rule Zero, preflight via registry.provider_menu_summary(), init_project,
  checkpoint protocol, "Present Both Composition Runtimes").
- lib/paths.py: PROJECTS_DIR = $OPENMONTAGE_PROJECTS_DIR or <repo>/projects (read at import).
- lib/checkpoint.py + schemas/checkpoints/checkpoint.schema.json: projects/<id>/checkpoint_<stage>.json with
  status completed|failed|awaiting_human|in_progress, human_approved, cost_snapshot.total_spent_usd (a
  CostTracker snapshot, i.e. cumulative for the run), error; gated stages can't be written completed
  without human_approved=True (GATE VIOLATION).
- lib/events.py / tools/base_tool.py: projects/<id>/events.jsonl lines {ts, call_id, tool, event:
  start|finish|error, depth?, cost_usd?, ...}; cost is summed at depth 0 (no depth key).
- tools/base_tool._load_dotenv: <repo>/.env fills any env var that is missing or empty, at import.

Approach for the lane cwd (work/openmontage/): the repo is an --add-dir, the prompt says to read
<repo>/AGENT_GUIDE.md, every OpenMontage command runs as `cd <repo> && <repo python> ...` (tools import as
tools.* / lib.*), and OPENMONTAGE_PROJECTS_DIR=<workspace>/work/openmontage is set in the lane env so
checkpoints, artifacts and events land in the video's workspace.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from ..workflow.stages import StageSpec
from .base import DetectedStage, EngineHealth, HealthItem
from .generic import COLLAB_KEYS, ManifestAdapter, config_dict, is_collaborative, rel, run_cmd, segment_id, slug

STATUS_MAP = {"awaiting_human": "awaiting_approval", "completed": "completed", "failed": "failed",
              "in_progress": "running"}
PAID_DETAIL = "paid provider used"


def parse_dotenv(path: Path) -> dict[str, str]:
    """Same rules as OpenMontage's _load_dotenv: KEY=VALUE, quotes, inline ' #' comments."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"'):
            q = value[0]
            end = value.find(q, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            m = re.search(r"(^|\s)#", value)
            if m:
                value = value[: m.start()]
            value = value.strip()
        out[key] = value
    return out


def load_pipelines(repo: Path, zero_cost_ids: set[str], hidden: set[str]) -> list[dict[str, Any]]:
    out = []
    for f in sorted(Path(repo).glob("pipeline_defs/*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        name = d.get("name") or f.stem
        stages = [{"name": s.get("name"), "gate": bool(s.get("human_approval_default")), "skill": s.get("skill"),
                   "produces": list(s.get("produces") or [])}
                  for s in d.get("stages") or [] if isinstance(s, dict) and s.get("name")]
        out.append({
            "id": name, "name": name.replace("-", " ").title(),
            "description": " ".join(str(d.get("description", "")).split()),
            "category": d.get("category", ""), "stability": d.get("stability", ""),
            "budget_default_usd": (d.get("orchestration") or {}).get("budget_default_usd"),
            "stages": stages, "zero_cost": name in zero_cost_ids, "hidden": name in hidden,
        })
    return out


def load_playbooks(repo: Path) -> list[dict[str, Any]]:
    out = []
    for custom, pattern in ((False, "styles/*.yaml"), (True, "styles/custom/*.yaml")):
        for f in sorted(Path(repo).glob(pattern)):
            try:
                d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            ident = d.get("identity") or {}
            out.append({"id": f.stem, "name": ident.get("name") or f.stem, "category": ident.get("category", ""),
                        "mood": ident.get("mood", ""), "best_for": ident.get("best_for", ""), "custom": custom,
                        "zero_cost": True})
    return out


class OpenMontageAdapter(ManifestAdapter):
    id = "openmontage"

    # ---- options -------------------------------------------------------------------------------
    def load_options(self) -> None:
        opts = dict(self.manifest.get("options") or {})
        if self.repo_path and self.repo_path.is_dir():
            opts["pipelines"] = load_pipelines(self.repo_path, set(opts.get("zero_cost_pipelines") or []),
                                               set(opts.get("hidden_pipelines") or []))
            opts["playbooks"] = load_playbooks(self.repo_path)
        self.manifest["options"] = opts

    def pipelines(self) -> list[dict]:
        return list((self.manifest.get("options") or {}).get("pipelines") or [])

    def pipeline(self, name: str) -> Optional[dict]:
        return next((p for p in self.pipelines() if p["id"] == name), None)

    def default_pipeline(self) -> str:
        return str((self.manifest.get("options") or {}).get("default_pipeline") or "animated-explainer")

    # ---- paths ---------------------------------------------------------------------------------
    def om_python(self) -> str:
        if self.repo_path:
            for c in (self.repo_path / ".venv" / "bin" / "python", self.repo_path / ".venv" / "Scripts" / "python.exe"):
                if c.exists():
                    return str(c)
        return self.python_bin()

    def project_id(self, workspace: Path, stage: Optional[StageSpec] = None) -> str:
        base = slug(Path(workspace).name, "video")
        if stage is None or not is_collaborative(stage):
            return base
        if stage.key.startswith("segment:"):
            return f"{base}-{slug(segment_id(stage), 'segment')}"
        return f"{base}-{stage.key}"

    def project_dir(self, workspace: Path, stage: Optional[StageSpec] = None) -> Path:
        return Path(workspace).resolve() / "work" / self.id / self.project_id(workspace, stage)

    # ---- health --------------------------------------------------------------------------------
    def paid_keys_in_dotenv(self) -> list[str]:
        if not self.repo_path:
            return []
        env = parse_dotenv(self.repo_path / ".env")
        paid = set((self.manifest.get("cost") or {}).get("paid_env") or [])
        return sorted(k for k, v in env.items() if k in paid and v)

    def health(self, zero_cost: bool = True) -> EngineHealth:
        items, commit = self.common_health()
        repo = self.repo_path
        if not repo or not repo.is_dir():
            return self.finish_health(items, commit, zero_cost, zero_cost_ready=False)
        req = self.manifest.get("requires") or {}
        items.append(self.node_health(str(req.get("node") or ">=22")))
        venv_py = Path(self.om_python())
        if venv_py.is_absolute() and venv_py.exists() and ".venv" in venv_py.parts:
            code, out = run_cmd([str(venv_py), "-c", "import sys; print(sys.version.split()[0]); sys.exit(sys.version_info < (3, 10))"],
                                timeout=20)
            items.append(HealthItem("venv", "Python venv (.venv)", "ok" if code == 0 else "error",
                                    f"{venv_py} {out.strip()}", "" if code == 0 else "Python 3.10+ is required; re-run make setup"))
            items.append(self.python_modules_health(str(venv_py), req.get("python_modules") or [], cwd=repo,
                                                    pip_hint="run the setup step 'make setup' (pip install -r requirements.txt)"))
        else:
            items.append(HealthItem("venv", "Python venv (.venv)", "error", f"{repo}/.venv not found",
                                    "run the setup step 'make setup' (creates .venv and installs requirements.txt)"))
        nm = repo / "remotion-composer" / "node_modules"
        items.append(HealthItem("remotion", "Remotion composer (remotion-composer/node_modules)",
                                "ok" if nm.is_dir() else "error", "installed" if nm.is_dir() else "npm install not run",
                                "" if nm.is_dir() else "run the setup step 'make setup' (cd remotion-composer && npm install)"))
        import shutil

        piper = shutil.which("piper") or next((str(c) for c in (repo / ".venv" / "bin" / "piper",
                                                                  repo / ".venv" / "Scripts" / "piper.exe")
                                               if c.exists()), None)
        items.append(HealthItem("piper", "Piper TTS (free narration)", "ok" if piper else "warning",
                                piper or "piper not found: OpenMontage cannot narrate for free",
                                "" if piper else "run 'make setup' (pip install piper-tts) and the 'piper-voice' setup step"))
        model = Path.home() / ".piper" / "models" / "en_US-lessac-medium.onnx"
        items.append(HealthItem("piper_voice", "Piper voice en_US-lessac-medium", "ok" if model.exists() else "warning",
                                str(model) if model.exists() else "voice model not downloaded",
                                "" if model.exists() else "run the setup step 'piper-voice'"))
        if not (repo / ".env").exists():
            items.append(HealthItem("dotenv", ".env", "ok", "no .env: no provider keys are auto-loaded"))
            paid = []
        else:
            paid = self.paid_keys_in_dotenv()
            free = sorted(k for k, v in parse_dotenv(repo / ".env").items()
                          if k in set((self.manifest.get("cost") or {}).get("free_env") or []) and v)
            if paid:
                items.append(HealthItem(
                    "paid_keys", "Paid provider keys in .env", "warning",
                    f"{len(paid)} paid key(s) set in {repo}/.env: {', '.join(paid)} (values not shown). OpenMontage loads"
                    " .env at import, so they are live even when SpaceStudio drops them from the environment.",
                    f"zero-cost jobs are blocked: move these keys out of {repo}/.env (or point SpaceStudio at a separate"
                    " keyless clone in Settings > Engines)"))
            else:
                items.append(HealthItem("paid_keys", "Paid provider keys in .env", "ok", "none set"))
            if free:
                items.append(HealthItem("free_keys", "Free stock keys in .env", "ok", ", ".join(free) +
                                        " (free developer keys; allowed in zero-cost mode only if listed in allowed_free_keys)"))
        return self.finish_health(items, commit, zero_cost, zero_cost_ready=not paid)

    # ---- stages --------------------------------------------------------------------------------
    def chosen_pipeline(self, config: dict) -> str:
        cfg = config_dict(config)
        eo = (cfg.get("engine_options") or {}).get(self.id) or {}
        return str(eo.get("pipeline") or self.default_pipeline())

    def native_stages(self, config: dict) -> list[StageSpec]:
        name = self.chosen_pipeline(config)
        p = self.pipeline(name)
        if p is None:
            raise ValueError(f"OpenMontage pipeline {name!r} not found in {self.repo_path}/pipeline_defs/"
                             f" (available: {', '.join(x['id'] for x in self.pipelines()) or 'none: is the repo cloned?'})")
        cfg = config_dict(config)
        if cfg.get("zero_cost", True) and not p["zero_cost"]:
            raise ValueError(f"OpenMontage pipeline {name!r} has no verified zero-cost path; choose one of "
                             + ", ".join(x["id"] for x in self.pipelines() if x["zero_cost"]) + " or turn zero-cost off")
        specs: list[StageSpec] = []
        prev = ["setup"]
        heavy = set((self.manifest.get("concurrency") or {}).get("render_heavy_stages") or [])
        for i, s in enumerate(p["stages"]):
            om_stage = s["name"]
            # "assets" (and "narration") are collaborative stage keys; keep native keys distinct
            key = f"om_{om_stage}" if om_stage in COLLAB_KEYS else om_stage
            specs.append(StageSpec(
                key=key, name=om_stage.replace("_", " ").title(), description=f"OpenMontage {name} stage `{om_stage}` ({s.get('skill') or 'no director skill'}).",
                capability="native_pipeline", owner=self.id, executor="claude", lane=self.lane, depends_on=prev,
                gate=s["gate"], engine_gate=s["gate"], required=True,
                outputs=[f"work/{self.id}/*/checkpoint_{om_stage}.json"],
                params={"pipeline": name, "om_stage": om_stage, "skill": s.get("skill"), "index": i,
                        "render_heavy": om_stage in heavy,
                        "prev_gate": specs[-1].params["om_stage"] if specs and specs[-1].gate else None},
            ))
            prev = [key]
        return specs

    # ---- prompts -------------------------------------------------------------------------------
    def _intro(self, ws: Path, pid: str, cfg: dict) -> str:
        R, PY = self.repo_path, self.om_python()
        lane = self.lane_dir(ws)
        eo = (cfg.get("engine_options") or {}).get(self.id) or {}
        runtime = eo.get("render_runtime") or "remotion"
        return f"""Engine: OpenMontage at {R}. Its CLAUDE.md requires reading AGENT_GUIDE.md before anything else: read
{R}/AGENT_GUIDE.md now (architecture in {R}/PROJECT_CONTEXT.md). OpenMontage runs from its repo root, so run every
OpenMontage command as `cd {R} && {PY} -c "..."` (or `cd {R} && {PY} <script>`). Never pip/npm install anything.
OPENMONTAGE_PROJECTS_DIR is already set to {lane} in your environment, so lib.paths.PROJECTS_DIR (and
lib.checkpoint.PROJECTS_DIR) is {lane}: this job's OpenMontage project is `{pid}` at {lane}/{pid}/ and every tool call
gets an explicit output_path under it. Skip `python -m backlot open` (it starts a server; SpaceStudio shows the board).
Answers SpaceStudio collected up front (AGENT_GUIDE "Decision Communication Contract"): render_runtime = {runtime}
(record every available runtime in the render_runtime_selection decision's options_considered, as the guide requires),
playbook = {eo.get('playbook') or 'pipeline default'}. Do not ask the user about providers: use only what the rules below allow.
Preflight: `cd {R} && {PY} -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu_summary(), indent=2))"`."""

    def _zero_cost_om(self, cfg: dict) -> str:
        if not cfg.get("zero_cost", True):
            return ""
        return ("Zero-cost (OpenMontage): use only tools whose runtime is LOCAL or free open sources (piper_tts,"
                " video_compose with remotion/hyperframes/ffmpeg, Archive.org / NASA / Wikimedia stock sources; Pexels /"
                " Pixabay / Unsplash only if their free key is in the environment). Check estimate_cost(inputs) == 0"
                " before every tool call. Every checkpoint's cost_snapshot (CostTracker.cost_snapshot()) must show"
                " total_spent_usd 0: SpaceStudio fails the stage on any spend.")

    def stage_prompt(self, stage: StageSpec, workspace: Path, config: dict, feedback: Optional[str] = None,
                     resume: bool = False) -> str:
        ws = Path(workspace).resolve()
        cfg = config_dict(config)
        eo = (cfg.get("engine_options") or {}).get(self.id) or {}
        if stage.key.startswith("segment:") and not stage.engine_gate and \
                self.segment_engine_gate(cfg, stage.params.get("engine_options")):
            import dataclasses
            stage = dataclasses.replace(stage, engine_gate=True)
        R, PY = self.repo_path, self.om_python()
        pid = self.project_id(ws, stage)
        lane = self.lane_dir(ws)
        PD = lane / pid
        pipeline = stage.params.get("pipeline") or eo.get("pipeline") or self.default_pipeline()
        def init_cmd(pipe: str) -> str:
            pb = f", style_playbook='{eo['playbook']}'" if eo.get("playbook") else ""
            return (f"`cd {R} && {PY} -c \"from lib.checkpoint import init_project; init_project('{pid}',"
                    f" title='<title>', pipeline_type='{pipe}'{pb})\"` (idempotent)")
        init = init_cmd(pipeline)
        if stage.key == "assets":
            task = f"""Acquire the footage of the asset plan with OpenMontage's stock tools.
1. Initialise the project: {init}.
2. Read {R}/tools/video/direct_clip_search.py (inputs) and {R}/skills/pipelines/documentary-montage/asset-director.md
   (provenance rules). For the manifest items with status "planned" and kind footage (and image items no other engine
   took), call direct_clip_search through the registry:
   `cd {R} && {PY} -c "from tools.tool_registry import registry; import json; registry.discover(); t=registry.get('direct_clip_search'); r=t.execute({{...}}); print(json.dumps(r.data, default=str))"`
   with output_dir "{PD}/assets/video", one query per item (slot_id = asset id), sources ["archive_org", "nasa",
   "wikimedia"] (add "pexels"/"pixabay"/"unsplash" only if their free key is set) and clips_per_query 2-3.
3. Inspect the thumbnails, pick one clip per item, copy it to {ws}/assets/footage/<asset id><ext> and update the
   manifest from the result: status "acquired", path, license, credit (creator), source (the result's source),
   source_url, provider "{self.id}". No licence -> do not use it. Nothing usable -> status "missing" with the reason.
4. Write the provenance list to {ws}/credits/{self.id}-assets.md.
{self.config_summary(cfg)}"""
        elif stage.key == "narration":
            task = f"""Record the narration with OpenMontage's free offline Piper voice (tools/audio/piper_tts.py).
1. Initialise the project: {init}.
2. Check `cd {R} && {PY} -c "from tools.tool_registry import registry; registry.discover(); print(registry.get('piper_tts').get_status())"`.
   If it is not AVAILABLE, append needs_input asking the user to run the SpaceStudio setup steps 'make-setup' and
   'piper-voice' for OpenMontage, and end your turn. Never fall back to a paid TTS.
3. For each line of {ws}/scripts/script.json, in order, execute piper_tts with {{"text": <spoken or text>,
   "model": "{eo.get('piper_model') or 'en_US-lessac-medium'}", "output_path": "{PD}/assets/audio/<line id>.wav"}}.
4. Join: copy each clip to {ws}/narration/lines/<line id>.wav, concatenate them with ffmpeg into
   {ws}/narration/narration.wav with short silences between lines (about 0.35 s; about 0.8 s where `section` changes,
   0.3 s lead-in), measure every clip with ffprobe and write {ws}/narration/timing.json with each line's real start/end,
   duration = the WAV's ffprobe duration, engine "{self.id}", voice "piper:<model>".
{self.config_summary(cfg)}"""
        elif stage.key.startswith("segment:"):
            seg = segment_id(stage)
            start, end = self.segment_window(stage, ws)
            dur = f"{end - start:.3f}" if start is not None else "<end - start>"
            t0 = f"{start:.3f}" if start is not None else "<start>"
            seg_pipe = (stage.params.get("engine_options") or {}).get("pipeline") or pipeline
            task = f"""Render segment {seg} through OpenMontage pipeline `{seg_pipe}` (Rule Zero), timed to SpaceStudio's narration.
1. Initialise the project `{pid}` for pipeline `{seg_pipe}`: {init_cmd(seg_pipe)}.
2. Read {R}/pipeline_defs/{seg_pipe}.yaml and, for each stage, its director skill before working on it. Translate
   SpaceStudio's approved material into the stage artifacts instead of inventing new content: the storyboard scenes of
   segment {seg} ({ws}/storyboard/storyboard.json), their script lines ({ws}/scripts/script.json), the acquired assets
   ({ws}/assets/manifest.json) and the narration window {t0} s + {dur} s ({ws}/narration/timing.json). Target duration
   {dur} s at {cfg['resolution']}, {cfg['fps']} fps. The narration is SpaceStudio's: do not generate a new voice.
3. Gates follow OpenMontage's normal protocol; never approve a gate yourself. At every stage whose
   human_approval_default is true, write its checkpoint (lib.checkpoint.write_checkpoint with
   pipeline_dir=lib.checkpoint.PROJECTS_DIR, pipeline_type='{seg_pipe}') with status 'awaiting_human', append
   {{"type":"awaiting_approval","stage":"{stage.key}","pending_gate":"<OpenMontage stage>","summary":...}} to
   {ws}/.spacestudio/events.jsonl and END YOUR TURN. SpaceStudio shows it to the user and resumes this session with
   their decision. Only after a message saying the user approved that stage in SpaceStudio, re-write that checkpoint
   with status 'completed', human_approval_required=True, human_approved=True (same artifacts) and continue. If a
   stage needs a decision SpaceStudio did not make, append needs_input and end your turn.
4. Compose with video_compose (render_runtime {eo.get('render_runtime') or 'remotion'}), wrapping the render as
   `spacestudio-slot render -- ...`, to {PD}/renders/final.mp4.
5. Make the picture-only segment: `ffmpeg -y -i {PD}/renders/final.mp4 -an -t {dur} -vf "scale={cfg['resolution'].replace('x', ':')},fps={cfg['fps']}" -c:v libx264 -pix_fmt yuv420p {ws}/scenes/{seg}.mp4`
   (pad with tpad=stop_mode=clone if it is short), verify with ffprobe, write {ws}/scenes/{seg}.json and the asset
   provenance to {ws}/credits/{self.id}-{seg}.md.
{self.config_summary(cfg)}"""
        else:
            task = self._native_task(stage, ws, cfg, pid, PD, pipeline, init)
        intro = self._intro(ws, pid, cfg)
        zc = self._zero_cost_om(cfg)
        return self.compose_prompt(stage, ws, cfg, intro + ("\n" + zc if zc else "") + "\n\n" + task, feedback, resume,
                                   native=not is_collaborative(stage))

    def _native_task(self, stage, ws, cfg, pid, PD, pipeline, init) -> str:
        R = self.repo_path
        key = stage.params.get("om_stage") or stage.key
        skill = stage.params.get("skill")
        lines = [f"OpenMontage pipeline `{pipeline}`, stage `{key}` (project `{pid}`, {PD})."]
        if stage.params.get("index", 0) == 0:
            lines.append(f"First stage: run the preflight above, then initialise the project: {init}. Read"
                         f" {R}/pipeline_defs/{pipeline}.yaml. The user's request: the idea in {ws}/project.json,"
                         f" {ws}/brief.json if present, and the video config below.")
        prev = stage.params.get("prev_gate")
        if prev:
            lines.append(f"Stage `{prev}` before this one is gated. Check {PD}/checkpoint_{prev}.json: it must already be"
                         " 'completed' with human_approved=True (you re-write it only when SpaceStudio tells you the user"
                         " approved it). If it is still 'awaiting_human', do not approve it yourself: append needs_input"
                         f" saying `{prev}` is still awaiting approval, and end your turn.")
        lines.append(f"Read the stage director skill {R}/skills/{skill}.md before any work in this stage, and the Layer 3"
                     " skills of the tools you use." if skill else "This stage has no director skill; follow the manifest.")
        lines.append(f"Self-review with {R}/skills/meta/reviewer.md and checkpoint with {R}/skills/meta/checkpoint-protocol.md:"
                     f" write_checkpoint(lib.checkpoint.PROJECTS_DIR, '{pid}', '{key}', <status>, <artifacts>,"
                     f" pipeline_type='{pipeline}', cost_snapshot=<CostTracker snapshot>).")
        if stage.gate:
            lines.append(f"`{key}` is gated (human_approval_default: true): write status 'awaiting_human', then append the"
                         f" awaiting_approval event (with \"pending_gate\": \"{key}\") with the artifact summary and END"
                         " YOUR TURN. Never write it 'completed' yourself: when SpaceStudio resumes this session saying the"
                         f" user approved `{key}`, re-write checkpoint_{key}.json as 'completed' with"
                         " human_approval_required=True, human_approved=True (same artifacts) and stop.")
        else:
            lines.append(f"`{key}` is not gated: write status 'completed' when done.")
        if key in ("compose", "publish") or stage.params.get("index") == len(self.pipeline(pipeline)["stages"]) - 1:
            lines.append(f"When the film exists ({PD}/renders/final.mp4), hand it over: copy it to {ws}/renders/final.mp4,"
                         f" subtitles to {ws}/subtitles/{cfg['language']}.srt, and the asset provenance to"
                         f" {ws}/credits/{self.id}.md.")
        return "\n".join(lines) + "\n" + self.config_summary(cfg)

    # ---- detection -----------------------------------------------------------------------------
    def spend(self, project_dir: Path) -> tuple[float, str]:
        """Highest cost_snapshot.total_spent_usd over the project's checkpoints (cumulative snapshots) and the
        sum of depth-0 tool finish costs in events.jsonl; returns (max of both, explanation)."""
        snap, ev_sum = 0.0, 0.0
        for f in Path(project_dir).glob("checkpoint_*.json"):
            try:
                cs = (json.loads(f.read_text(encoding="utf-8")).get("cost_snapshot") or {})
                snap = max(snap, float(cs.get("total_spent_usd") or 0))
            except (OSError, ValueError, TypeError):
                continue
        for ev in self.events(project_dir):
            if ev.get("event") == "finish" and not ev.get("depth"):
                try:
                    ev_sum += float(ev.get("cost_usd") or 0)
                except (TypeError, ValueError):
                    pass
        total = max(snap, ev_sum)
        return total, f"cost_snapshot ${snap:.4f}, tool events ${ev_sum:.4f}"

    @staticmethod
    def events(project_dir: Path) -> list[dict]:
        f = Path(project_dir) / "events.jsonl"
        out = []
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return out

    def last_activity(self, project_dir: Path) -> Optional[str]:
        evs = self.events(project_dir)
        if not evs:
            return None
        e = evs[-1]
        bits = [str(e.get("ts", "")), str(e.get("tool", "")), str(e.get("event", ""))]
        if e.get("error"):
            bits.append(str(e["error"])[:120])
        return " ".join(b for b in bits if b)

    def _paid_check(self, ws: Path, stage: StageSpec, det: DetectedStage, project_dir: Path) -> DetectedStage:
        spent, how = self.spend(project_dir)
        det.last_activity = self.last_activity(project_dir)
        if spent > 0:
            det.cost_usd = spent
            if self.zero_cost_for(ws, stage):
                det.status = "failed"
                det.detail = f"{PAID_DETAIL} under zero-cost mode: ${spent:.4f} ({how})"
        return det

    def detect_native(self, workspace: Path, stage: StageSpec) -> DetectedStage:
        ws = Path(workspace)
        PD = self.project_dir(ws, stage)
        key = stage.params.get("om_stage") or stage.key
        f = PD / f"checkpoint_{key}.json"
        if not f.exists():
            det = DetectedStage(stage.key, "running" if (PD / "events.jsonl").exists() and stage.params.get("index", 0) == 0
                                and not list(PD.glob("checkpoint_*.json")) else "pending",
                                detail=f"checkpoint_{key}.json not written yet")
            return self._paid_check(ws, stage, det, PD) if PD.is_dir() else det
        try:
            cp = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return DetectedStage(stage.key, "failed", detail=f"checkpoint_{key}.json unreadable: {e}")
        raw = str(cp.get("status", ""))
        status = STATUS_MAP.get(raw, "unknown")
        detail = f"checkpoint status {raw}"
        if status == "completed" and stage.gate and not cp.get("human_approved"):
            status, detail = "awaiting_approval", "completed checkpoint without human_approved on a gated stage"
        if status == "failed":
            detail = f"OpenMontage: {cp.get('error') or 'stage failed'}"
        pending = key if status == "awaiting_approval" else None
        outputs = [rel(ws, f)]
        for pat in ("artifacts/*.json", "renders/*.mp4"):
            outputs += sorted(rel(ws, p) for p in PD.glob(pat) if p.is_file())
        cost = (cp.get("cost_snapshot") or {}).get("total_spent_usd")
        det = DetectedStage(stage.key, status, outputs, detail,
                            cost_usd=float(cost) if isinstance(cost, (int, float)) else None, pending_gate=pending)
        return self._paid_check(ws, stage, det, PD)

    def pending_gates(self, project_dir: Path) -> list[str]:
        """OpenMontage stages whose checkpoint is awaiting_human, in pipeline order when known."""
        out = []
        for f in Path(project_dir).glob("checkpoint_*.json"):
            try:
                cp = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if cp.get("status") == "awaiting_human":
                out.append(str(cp.get("stage") or f.stem[len("checkpoint_"):]))
        return sorted(out)

    def detect_collaborative(self, workspace: Path, stage: StageSpec) -> DetectedStage:
        det = super().detect_collaborative(workspace, stage)
        PD = self.project_dir(workspace, stage)
        if not PD.is_dir():
            return det
        if stage.key.startswith("segment:") and det.status != "completed":
            gates = self.pending_gates(PD)
            if gates:
                det = DetectedStage(stage.key, "awaiting_approval", det.outputs + sorted(
                    rel(Path(workspace), PD / f"checkpoint_{g}.json") for g in gates),
                    f"OpenMontage gate `{gates[0]}` awaiting approval (pending_gate={gates[0]})",
                    pending_gate=gates[0])
        return self._paid_check(Path(workspace), stage, det, PD)

    def segment_engine_gate(self, config: dict, engine_options: Optional[dict] = None) -> bool:
        name = (engine_options or {}).get("pipeline") or self.chosen_pipeline(config)
        p = self.pipeline(name)
        return bool(p and any(s["gate"] for s in p["stages"]))

    def approval_resume_prompt(self, stage: StageSpec, om_stage: Optional[str] = None, feedback: Optional[str] = None,
                               workspace: Optional[Path] = None) -> str:
        om_stage = om_stage or stage.params.get("om_stage") or stage.key
        head = self.marker(stage, workspace) + "\n" if workspace is not None else ""
        pid = self.project_id(workspace, stage) if workspace is not None else "<project-id>"
        if feedback:
            return (head + f"The user requested changes to OpenMontage stage `{om_stage}` in SpaceStudio:\n{feedback.strip()}\n"
                    f"Revise that stage following its director skill, re-write checkpoint_{om_stage}.json with status"
                    " 'awaiting_human' (the previous version goes to history/), append the awaiting_approval event with"
                    f" \"pending_gate\": \"{om_stage}\" and END YOUR TURN.")
        return (head + f"The user approved OpenMontage stage `{om_stage}` in SpaceStudio. Re-write checkpoint_{om_stage}.json"
                f" with lib.checkpoint.write_checkpoint(lib.checkpoint.PROJECTS_DIR, '{pid}', '{om_stage}', 'completed',"
                " <the same artifacts>, pipeline_type=<the project's pipeline>, human_approval_required=True,"
                " human_approved=True), then continue with the next step; stop again at the next gated stage, or append"
                " stage_completed when this SpaceStudio stage's outputs are written.")

    def collect_outputs(self, workspace: Path, stage: StageSpec) -> list[str]:
        outs = list(self.detect(workspace, stage).outputs)
        final = Path(workspace) / "renders" / "final.mp4"
        if not is_collaborative(stage) and final.exists() and stage.params.get("om_stage") in ("compose", "publish"):
            outs.append("renders/final.mp4")
        return outs
