"""Manifest-driven engine adapter, plus the helpers every adapter shares.

`ManifestAdapter` implements the parts of `EngineAdapter` that are the same for every engine:
launch specs (cwd = ``work/<engine-id>/``, ``--add-dir`` the repo, plugin dir, env and allowed tools
with ``{repo}``/``{lane}``/``{workspace}`` placeholders), common health checks (repo, expected files,
git commit, commands, versions), the collaborative I/O contract text, and file-based detection of the
collaborative stages (``assets``, ``narration``, ``segment:<id>``) through the interchange models and
ffprobe.

`GenericAdapter` is what a future repo gets with only a manifest: native stages come from the
manifest's ``stages`` list (``produces`` globs), detection is by those globs, and stage prompts come
from the manifest's ``prompts`` / ``prompt_template`` with placeholders.
"""
from __future__ import annotations

import glob as _glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

from pydantic import ValidationError

from ..interchange import schema as ix
from ..workflow.stages import StageSpec
from .base import DetectedStage, EngineAdapter, EngineHealth, HealthItem, LaunchSpec

COLLAB_KEYS = ("assets", "narration")
SEGMENT_PREFIX = "segment:"
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


# --------------------------------------------------------------------------------------- small helpers
def fill(value: Any, mapping: dict[str, str]) -> Any:
    """Replace ``{name}`` placeholders that are in ``mapping``; leave every other brace alone
    (engine commands such as cinematic's ``--cmd '... {txt} {out}'`` keep theirs)."""
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: str(mapping[m.group(1)]) if m.group(1) in mapping else m.group(0), value)
    if isinstance(value, list):
        return [fill(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, mapping) for k, v in value.items()}
    return value


def run_cmd(cmd: list[str], cwd: Optional[Path] = None, timeout: float = 60, env: Optional[dict] = None
            ) -> tuple[Optional[int], str]:
    """Run a command; returns (exit code or None when it could not run, combined output)."""
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return None, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return None, f"{' '.join(cmd)}: timed out after {timeout:.0f}s"
    except OSError as e:  # pragma: no cover - permission errors etc.
        return None, f"{cmd[0]}: {e}"


def parse_version(text: str) -> Optional[tuple[int, ...]]:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups() if x is not None)


def ffmpeg_major(version_output: str) -> Optional[int]:
    """`ffmpeg -version` first line: 'ffmpeg version 6.1.1-3ubuntu5', 'ffmpeg version n7.1', or a git
    build 'ffmpeg version N-117000-g...' (no number: unknown)."""
    first = (version_output or "").splitlines()[0] if version_output else ""
    m = re.search(r"version n?(\d+)\.", first)
    return int(m.group(1)) if m else None


def _cmp(v: tuple[int, ...], w: tuple[int, ...]) -> int:
    n = max(len(v), len(w))
    a, b = v + (0,) * (n - len(v)), w + (0,) * (n - len(w))
    return (a > b) - (a < b)


def version_satisfies(version: tuple[int, ...], spec: str) -> bool:
    """Small subset of npm/pep440 ranges: '>=20', '>=3.11', '^22.23.4 || ^24.22.1', '<3.14'."""
    for alt in str(spec).split("||"):
        ok = True
        for part in alt.split():
            m = re.match(r"(>=|<=|>|<|==|\^|~)?\s*(\d+(?:\.\d+)*)", part)
            if not m:
                continue
            op, num = m.group(1) or ">=", tuple(int(x) for x in m.group(2).split("."))
            c = _cmp(version, num)
            if op == ">=":
                ok &= c >= 0
            elif op == ">":
                ok &= c > 0
            elif op == "<=":
                ok &= c <= 0
            elif op == "<":
                ok &= c < 0
            elif op == "==":
                ok &= c == 0
            elif op == "^":
                ok &= c >= 0 and version[0] == num[0]
            elif op == "~":
                ok &= c >= 0 and version[:2] == num[:2]
        if ok:
            return True
    return False


def git_commit(repo: Path) -> Optional[str]:
    """Read HEAD from .git without running git."""
    gd = Path(repo) / ".git"
    try:
        if gd.is_file():  # worktree / submodule: "gitdir: <path>"
            target = gd.read_text().split(":", 1)[1].strip()
            gd = (Path(repo) / target).resolve()
        head = (gd / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head.split(":", 1)[1].strip()
        f = gd / ref
        if f.exists():
            return f.read_text().strip() or None
        packed = gd / "packed-refs"
        if packed.exists():
            for line in packed.read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split()[0]
    except (OSError, IndexError):
        return None
    return None


def git_config_value(repo: Path, section: str, key: str) -> Optional[str]:
    """Read a value from <repo>/.git/config without running git (e.g. lemo.managed)."""
    f = Path(repo) / ".git" / "config"
    try:
        text = f.read_text()
    except OSError:
        return None
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1].strip().split()[0].lower()
            continue
        if cur == section.lower() and "=" in s:
            k, _, v = s.partition("=")
            if k.strip().lower() == key.lower():
                return v.strip()
    return None


def probe_media(path: Path, ffprobe: str = "ffprobe") -> Optional[dict]:
    """ffprobe a media file: {duration, width, height, fps, has_video, has_audio}; None when ffprobe is
    missing; raises ValueError when the file can't be parsed."""
    if not shutil.which(ffprobe):
        return None
    code, out = run_cmd([ffprobe, "-v", "error", "-show_entries",
                         "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                        timeout=60)
    if code != 0:
        raise ValueError(f"ffprobe could not read {path.name}: {out.strip()[:300]}")
    try:
        data = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"ffprobe output unreadable for {path.name}") from e
    info: dict[str, Any] = {"duration": None, "width": None, "height": None, "fps": None,
                            "has_video": False, "has_audio": False}
    try:
        info["duration"] = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not info["has_video"]:
            info["has_video"] = True
            info["width"], info["height"] = s.get("width"), s.get("height")
            try:
                n, d = str(s.get("r_frame_rate", "0/1")).split("/")
                info["fps"] = float(n) / float(d) if float(d) else None
            except (ValueError, ZeroDivisionError):
                pass
        elif s.get("codec_type") == "audio":
            info["has_audio"] = True
    return info


def config_dict(config: Any) -> dict:
    """A VideoConfig (or partial dict) as a full dict with defaults."""
    if isinstance(config, ix.VideoConfig):
        return config.model_dump()
    raw = dict(config or {})
    try:
        return ix.VideoConfig.model_validate(raw).model_dump()
    except ValidationError:
        base = ix.VideoConfig().model_dump()
        base.update(raw)
        return base


def workspace_config(ws: Path) -> dict:
    """The video config snapshot in project.json (best effort, defaults otherwise)."""
    f = Path(ws) / "project.json"
    try:
        snap = json.loads(f.read_text(encoding="utf-8"))
        cfg = snap.get("config") if isinstance(snap, dict) else None
        return config_dict(cfg or {})
    except (OSError, json.JSONDecodeError):
        return config_dict({})


def slug(text: str, fallback: str = "film") -> str:
    """lowercase letters, digits, hyphens (lemo's project-folder rule; also OpenMontage kebab-case)."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or fallback


def segment_id(stage: StageSpec) -> str:
    return str(stage.params.get("segment") or stage.key[len(SEGMENT_PREFIX):])


def is_collaborative(stage: StageSpec) -> bool:
    return stage.key in COLLAB_KEYS or stage.key.startswith(SEGMENT_PREFIX)


def rel(ws: Path, p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(Path(ws).resolve()).as_posix()
    except ValueError:
        return str(p)


# --------------------------------------------------------------------------------------- base adapter
class ManifestAdapter(EngineAdapter):
    """Shared behaviour for every engine (manifest-driven)."""

    def __init__(self, manifest: dict[str, Any], repo_path: Optional[Path], settings: Any = None):
        super().__init__(manifest, repo_path, settings)
        self.approved: bool = bool(manifest.get("approved", True))
        self.options_error: Optional[str] = None
        try:
            self.load_options()
        except Exception as e:  # options must never break the registry
            self.options_error = f"{type(e).__name__}: {e}"

    # ---- settings ------------------------------------------------------------------------------
    def setting(self, name: str, default: Any = None) -> Any:
        s = self.settings
        if s is None:
            try:
                from ..config import get_settings  # A's module; optional at import time

                s = self.settings = get_settings()
            except Exception:
                return default
        if isinstance(s, dict):
            return s.get(name, default)
        return getattr(s, name, default)

    # ---- description ---------------------------------------------------------------------------
    def load_options(self) -> None:
        """Subclasses expand dynamic options (styles, pipelines) into manifest['options']."""

    def options(self) -> dict[str, Any]:
        opts = dict(self.manifest.get("options") or {})
        if self.options_error:
            opts["error"] = self.options_error
        return opts

    @property
    def lane(self) -> str:
        return f"engine:{self.id}"

    def python_bin(self) -> str:
        return str(self.setting("python_bin", "python3") or "python3")

    def placeholders(self, workspace: Optional[Path] = None) -> dict[str, str]:
        m = {
            "repo": str(self.repo_path) if self.repo_path else "",
            "python": self.python_bin(),
            "render_slots": str(self.setting("max_render_jobs", 1) or 1),
            "render_workers": str(self.setting("render_workers", 2) or 2),
            "engine": self.id,
        }
        if workspace is not None:
            ws = Path(workspace).resolve()
            m["workspace"] = str(ws)
            m["lane"] = str(ws / "work" / self.id)
        return m

    def setup_steps(self) -> list[dict[str, str]]:
        m = self.placeholders()
        return [fill(dict(step), m) for step in self.manifest.get("setup", []) or []]

    # ---- execution -----------------------------------------------------------------------------
    def lane_dir(self, workspace: Path) -> Path:
        d = Path(workspace).resolve() / "work" / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def launch_spec(self, workspace: Path, stage: StageSpec, config: dict) -> LaunchSpec:
        ws = Path(workspace).resolve()
        cwd = self.lane_dir(ws)
        m = self.placeholders(ws)
        inv = self.manifest.get("invocation") or {}
        add_dirs: list[Path] = []
        if self.repo_path:
            add_dirs.append(self.repo_path)
        add_dirs.append(ws)  # the lane cwd is inside the workspace; the shared files are one level up
        plugin_dirs: list[Path] = []
        if inv.get("plugin_dir") and self.repo_path:
            plugin_dirs.append((self.repo_path / str(inv["plugin_dir"])).resolve())
        env: dict[str, str] = {}
        for src in (self.manifest.get("env") or {}, (self.manifest.get("concurrency") or {}).get("env") or {}):
            for k, v in src.items():
                env[str(k)] = str(fill(v, m))
        tools: list[str] = []
        for t in self.manifest.get("allowed_tools") or []:
            t = fill(str(t), m)
            if t not in tools and "{" not in t:
                tools.append(t)
        return LaunchSpec(cwd=cwd, add_dirs=add_dirs, plugin_dirs=plugin_dirs, env=env, allowed_tools=tools)

    # ---- health --------------------------------------------------------------------------------
    def common_health(self) -> tuple[list[HealthItem], Optional[str]]:
        items: list[HealthItem] = []
        commit = None
        repo = self.repo_path
        url = (self.manifest.get("repo") or {}).get("url", "")
        if not repo or not repo.is_dir():
            items.append(HealthItem("repo", "Repository", "error", f"not found at {repo}",
                                    f"git clone {url} {repo}" if url and repo else "set the engine path in Settings"))
            return items, None
        items.append(HealthItem("repo", "Repository", "ok", str(repo)))
        commit = git_commit(repo)
        items.append(HealthItem("commit", "Git commit", "ok" if commit else "warning",
                                commit[:12] if commit else "not a git checkout (version unknown)"))
        missing = [f for f in (self.manifest.get("health") or {}).get("expected_files", []) if not (repo / f).exists()]
        if missing:
            items.append(HealthItem("files", "Expected files", "error", "missing: " + ", ".join(missing),
                                    "the clone is incomplete or a different version; re-clone or update it"))
        else:
            items.append(HealthItem("files", "Expected files", "ok", "all present"))
        req = self.manifest.get("requires") or {}
        for c in req.get("commands", []) or []:
            path = shutil.which(self._command_name(c))
            items.append(HealthItem(f"cmd:{c}", f"{c} on PATH", "ok" if path else "error",
                                    path or "not found", "" if path else f"install {c} and make sure it is on PATH"))
        return items, commit

    def _command_name(self, c: str) -> str:
        if c == "ffmpeg":
            return str(self.setting("ffmpeg", "ffmpeg") or "ffmpeg")
        if c == "ffprobe":
            return str(self.setting("ffprobe", "ffprobe") or "ffprobe")
        return c

    def node_health(self, spec: str) -> HealthItem:
        code, out = run_cmd(["node", "-v"], timeout=15)
        v = parse_version(out) if code == 0 else None
        if v is None:
            return HealthItem("node", f"Node {spec}", "error", out.strip()[:200] or "node not found",
                              f"install Node {spec}")
        ok = version_satisfies(v, spec)
        return HealthItem("node", f"Node {spec}", "ok" if ok else "error", out.strip(),
                          "" if ok else f"install Node {spec} (found {out.strip()})")

    def ffmpeg_health(self, spec: str, fix: str = "") -> HealthItem:
        ff = self._command_name("ffmpeg")
        code, out = run_cmd([ff, "-version"], timeout=15)
        if code != 0:
            return HealthItem("ffmpeg_version", f"FFmpeg {spec}", "error", out.strip()[:200], "install FFmpeg")
        major = ffmpeg_major(out)
        first = out.splitlines()[0] if out else ""
        if major is None:
            return HealthItem("ffmpeg_version", f"FFmpeg {spec}", "warning", f"version not recognised: {first}")
        ok = version_satisfies((major,), spec)
        return HealthItem("ffmpeg_version", f"FFmpeg {spec}", "ok" if ok else "error", first,
                          "" if ok else (fix or f"install FFmpeg {spec}"))

    def python_modules_health(self, python: str, modules: Iterable[str], cwd: Optional[Path] = None,
                              item_id: str = "python_modules", pip_hint: str = "") -> HealthItem:
        modules = list(modules)
        code_s = ("import importlib,sys\nmiss=[]\nfor m in sys.argv[1:]:\n    try: importlib.import_module(m)\n"
                  "    except Exception as e: miss.append(m)\nprint('MISSING:'+','.join(miss))")
        code, out = run_cmd([python, "-c", code_s, *modules], cwd=cwd, timeout=90)
        if code is None:
            return HealthItem(item_id, "Python packages", "error", out, f"install Python or fix {python}")
        m = re.search(r"MISSING:(.*)", out)
        miss = [x for x in (m.group(1).strip().split(",") if m else ["?"]) if x]
        if miss:
            return HealthItem(item_id, "Python packages", "error", f"{python}: cannot import " + ", ".join(miss),
                              pip_hint or "pip install the engine's requirements")
        return HealthItem(item_id, "Python packages", "ok", f"{python}: " + ", ".join(modules))

    def health(self, zero_cost: bool = True) -> EngineHealth:
        items, commit = self.common_health()
        return self.finish_health(items, commit, zero_cost)

    def finish_health(self, items: list[HealthItem], commit: Optional[str], zero_cost: bool,
                      zero_cost_ready: Optional[bool] = None) -> EngineHealth:
        ok = not any(i.status == "error" for i in items)
        zc = ok if zero_cost_ready is None else (ok and zero_cost_ready)
        return EngineHealth(engine=self.id, ok=ok, zero_cost_ready=zc, items=items, commit=commit)

    # ---- prompt pieces -------------------------------------------------------------------------
    def marker(self, stage: StageSpec, workspace: Path) -> str:
        ws = Path(workspace).resolve()
        return f"SPACESTUDIO_STAGE={stage.key} VIDEO={ws.name} WORKSPACE={ws} LANE={stage.lane or self.lane}"

    def segment_window(self, stage: StageSpec, workspace: Path) -> tuple[Optional[float], Optional[float]]:
        start, end = stage.params.get("start"), stage.params.get("end")
        if start is not None and end is not None:
            return float(start), float(end)
        return None, None

    def io_contract(self, stage: StageSpec, workspace: Path, config: dict) -> str:
        ws = Path(workspace).resolve()
        cfg = config_dict(config)
        lines = [f"## SpaceStudio I/O contract (workspace {ws}; your cwd is {self.lane_dir(ws)})",
                 "Shared interchange files are the only hand-off between engines. Write them exactly to the schema;"
                 " SpaceStudio validates them with pydantic and ffprobe and trusts the files, not your summary."]
        if stage.key == "assets":
            lines += [
                f"- READ  {ws}/assets/manifest.json (AssetManifest) and {ws}/storyboard/storyboard.json (Storyboard).",
                f"- WRITE each file under {ws}/assets/images|footage|music|sfx/ (workspace-relative path in the manifest).",
                "- UPDATE every manifest item you handle: status (acquired | missing | rejected), path, license, credit,"
                f" source, source_url, provider=\"{self.id}\", paid=false. Never leave an item 'planned'; an item you"
                " cannot get with a free licence is 'missing' with the reason in credit. Keep the JSON valid.",
            ]
        elif stage.key == "narration":
            lines += [
                f"- READ  {ws}/scripts/script.json (Script). Use each line's `spoken` text (or `text` when empty).",
                f"- WRITE {ws}/narration/narration.wav (one track, mono or stereo PCM WAV) and per-line clips in"
                f" {ws}/narration/lines/<line id>.wav.",
                f"- WRITE {ws}/narration/timing.json (NarrationTiming): audio=\"narration/narration.wav\", engine=\"{self.id}\","
                " voice, duration (seconds, equal to the WAV's ffprobe duration), and lines: one entry per script line"
                " in order with id, start, end (seconds on the narration timeline) and text.",
            ]
        else:
            seg = segment_id(stage)
            start, end = self.segment_window(stage, ws)
            win = (f"{start:.3f}-{end:.3f} s (duration {end - start:.3f} s)" if start is not None
                   else "the start/end of its lines in narration/timing.json")
            lines += [
                f"- READ  {ws}/storyboard/storyboard.json: the scenes whose segment is \"{seg}\" (and the Segment entry"
                f" with its engine_options), {ws}/narration/timing.json for the window, {ws}/assets/manifest.json for"
                " acquired assets (use only items with status 'acquired').",
                f"- Time window on the narration timeline: {win}.",
                f"- WRITE {ws}/scenes/{seg}.mp4: picture only (no audio stream), {cfg['resolution']} at {cfg['fps']} fps,"
                " H.264 yuv420p, duration exactly the window length (trim/pad with ffmpeg if needed).",
                f"- WRITE {ws}/scenes/{seg}.json (SegmentRender): segment=\"{seg}\", engine=\"{self.id}\","
                f" video=\"scenes/{seg}.mp4\", start, end, has_audio=false, credits=[...].",
                f"- COPY the engine's credits for this segment to {ws}/credits/{self.id}-{seg}.md (every third-party"
                " asset with source and licence; write the file even if the list is empty).",
            ]
        return "\n".join(lines)

    def rules(self, stage: StageSpec, workspace: Path, config: dict, native: bool = False) -> str:
        cfg = config_dict(config)
        ws = Path(workspace).resolve()
        out = ["## Rules"]
        if cfg.get("zero_cost", True):
            out.append("- ZERO-COST MODE is on: use only free, local or free-licence tools and sources. Never call a"
                       " paid API or a tool whose runtime is a paid API, never add API keys. If the work needs a paid"
                       " capability, do not substitute silently: report it with a needs_input event.")
        else:
            approved = ", ".join(cfg.get("approved_paid_providers") or []) or "none"
            out.append(f"- Paid providers approved for this video: {approved}. Use no other paid provider.")
        out.append(f"- Work only inside {ws} and read the engine repo {self.repo_path}; never modify the engine repo.")
        if native and stage.gate:
            out.append("- This stage is a review gate. When its output is ready, append"
                       f" {{\"type\":\"awaiting_approval\",\"stage\":\"{stage.key}\",\"summary\":...}} to"
                       f" {ws}/.spacestudio/events.jsonl and END YOUR TURN. The approval comes from SpaceStudio, which"
                       " resumes this session with the user's decision and feedback.")
        else:
            out.append("- Approvals come from SpaceStudio. The engine's own review gates are mapped to SpaceStudio"
                       " gates that the user has already passed for this work; do not stop for them.")
        out.append("- Never wait for interactive input and never use AskUserQuestion. If only the user can decide"
                   f" something, append {{\"type\":\"needs_input\",\"stage\":\"{stage.key}\",\"question\":...}} to"
                   f" {ws}/.spacestudio/events.jsonl and end your turn.")
        out.append(f"- When finished, append {{\"type\":\"stage_completed\",\"stage\":\"{stage.key}\",\"outputs\":[...]}}"
                   " with workspace-relative paths. On failure append {\"type\":\"error\",...} with the real error.")
        out.append("- Wrap every heavy render command as `spacestudio-slot render -- <command>` (shared render slots).")
        return "\n".join(out)

    def tail(self, stage: StageSpec, feedback: Optional[str], resume: bool) -> str:
        out = []
        if resume:
            out.append("## Resuming\nThis stage was interrupted. First check which outputs already exist and are"
                       " valid; redo only what is missing or broken.")
        if feedback:
            out.append("## The user asked for changes\n" + feedback.strip() + "\nApply them, keep everything else.")
        return "\n\n".join(out)

    def compose_prompt(self, stage: StageSpec, workspace: Path, config: dict, task: str,
                       feedback: Optional[str], resume: bool, native: bool) -> str:
        parts = [self.marker(stage, workspace),
                 f"# {stage.name} ({self.manifest.get('name', self.id)}, lane {stage.lane or self.lane})",
                 task.strip()]
        if not native:
            parts.append(self.io_contract(stage, workspace, config))
        parts.append(self.rules(stage, workspace, config, native=native))
        t = self.tail(stage, feedback, resume)
        if t:
            parts.append(t)
        return "\n\n".join(parts) + "\n"

    def config_summary(self, config: dict) -> str:
        c = config_dict(config)
        eo = (c.get("engine_options") or {}).get(self.id) or {}
        return (f"Video config: {c['duration_seconds']} s, {c['aspect_ratio']} {c['resolution']} @ {c['fps']} fps, "
                f"tone {c['tone']}, language {c['language']} (narration {c['narration_language']}), pacing {c['pacing']}, "
                f"voiceover {c['voiceover']}, captions {c['captions']}, music {c['music']}, sfx {c['sfx']}, "
                f"audience {c['target_audience']}. Engine options: {json.dumps(eo)}."
                + (f"\nCreative brief: {c['brief']}" if c.get("brief") else ""))

    # ---- detection -----------------------------------------------------------------------------
    def zero_cost_for(self, workspace: Path, stage: StageSpec) -> bool:
        if "zero_cost" in stage.params:
            return bool(stage.params["zero_cost"])
        return bool(workspace_config(workspace).get("zero_cost", True))

    def detect_collaborative(self, workspace: Path, stage: StageSpec) -> DetectedStage:
        ws = Path(workspace)
        if stage.key == "assets":
            return self._detect_assets(ws, stage)
        if stage.key == "narration":
            return self._detect_narration(ws, stage)
        return self._detect_segment(ws, stage)

    def _detect_assets(self, ws: Path, stage: StageSpec) -> DetectedStage:
        f = ws / "assets" / "manifest.json"
        if not f.exists():
            return DetectedStage(stage.key, "pending", detail="assets/manifest.json not written yet")
        try:
            man = ix.validate_file(f, "AssetManifest")
        except (ValidationError, ValueError) as e:
            return DetectedStage(stage.key, "failed", detail=f"assets/manifest.json is invalid: {str(e)[:400]}")
        wanted = set(stage.params.get("asset_ids") or [])
        items = [i for i in man.items if not wanted or i.id in wanted]
        planned = [i.id for i in items if i.status == "planned"]
        outputs, problems, missing = [], [], []
        for it in items:
            if it.status == "acquired":
                if not it.path or not (ws / it.path).is_file() or (ws / it.path).stat().st_size == 0:
                    problems.append(f"{it.id}: file {it.path!r} missing or empty")
                elif not it.license.strip():
                    problems.append(f"{it.id}: no licence recorded")
                else:
                    outputs.append(it.path)
            elif it.status == "missing":
                missing.append(it.id)
        if problems:
            return DetectedStage(stage.key, "failed", outputs, "; ".join(problems)[:600])
        if planned:
            return DetectedStage(stage.key, "running", outputs, f"{len(planned)} item(s) still planned: {', '.join(planned[:8])}")
        detail = f"{len(outputs)} acquired" + (f", {len(missing)} missing: {', '.join(missing[:8])}" if missing else "")
        return DetectedStage(stage.key, "completed", ["assets/manifest.json", *outputs], detail)

    def _detect_narration(self, ws: Path, stage: StageSpec) -> DetectedStage:
        wav, tj = ws / "narration" / "narration.wav", ws / "narration" / "timing.json"
        if not wav.exists() and not tj.exists():
            return DetectedStage(stage.key, "pending", detail="no narration files yet")
        if not wav.exists() or not tj.exists():
            return DetectedStage(stage.key, "running", [rel(ws, p) for p in (wav, tj) if p.exists()],
                                 "narration.wav and timing.json are not both written yet")
        try:
            timing = ix.validate_file(tj, "NarrationTiming")
        except (ValidationError, ValueError) as e:
            return DetectedStage(stage.key, "failed", detail=f"narration/timing.json is invalid: {str(e)[:400]}")
        sf = ws / "scripts" / "script.json"
        if sf.exists():
            try:
                script = ix.validate_file(sf, "Script")
                want = [ln.id for ln in script.lines]
                have = [ln.id for ln in timing.lines]
                lost = [i for i in want if i not in have]
                if lost:
                    return DetectedStage(stage.key, "failed", detail=f"timing.json lacks script lines: {', '.join(lost[:10])}")
            except (ValidationError, ValueError):
                pass
        for ln in timing.lines:
            if ln.end < ln.start or ln.end > timing.duration + 0.5:
                return DetectedStage(stage.key, "failed", detail=f"line {ln.id} has an impossible time {ln.start}-{ln.end}")
        try:
            info = probe_media(wav, str(self.setting("ffprobe", "ffprobe") or "ffprobe"))
        except ValueError as e:
            return DetectedStage(stage.key, "failed", detail=str(e))
        if info is None:
            return DetectedStage(stage.key, "unknown", ["narration/narration.wav", "narration/timing.json"],
                                 "ffprobe not found: cannot verify the narration duration")
        if not info["has_audio"] or not info["duration"]:
            return DetectedStage(stage.key, "failed", detail="narration.wav has no audio stream")
        if abs(info["duration"] - timing.duration) > max(0.5, 0.02 * timing.duration):
            return DetectedStage(stage.key, "failed", detail=f"timing.json duration {timing.duration:.2f}s but the WAV is"
                                                            f" {info['duration']:.2f}s")
        return DetectedStage(stage.key, "completed", ["narration/narration.wav", "narration/timing.json"],
                             f"{len(timing.lines)} lines, {info['duration']:.1f}s")

    def _detect_segment(self, ws: Path, stage: StageSpec) -> DetectedStage:
        seg = segment_id(stage)
        mp4, js = ws / "scenes" / f"{seg}.mp4", ws / "scenes" / f"{seg}.json"
        if not mp4.exists():
            return DetectedStage(stage.key, "pending", detail=f"scenes/{seg}.mp4 not rendered yet")
        if not js.exists():
            return DetectedStage(stage.key, "running", [f"scenes/{seg}.mp4"], f"scenes/{seg}.json not written yet")
        try:
            sr = ix.SegmentRender.model_validate(json.loads(js.read_text(encoding="utf-8")))
        except (ValidationError, ValueError) as e:
            return DetectedStage(stage.key, "failed", detail=f"scenes/{seg}.json is invalid: {str(e)[:400]}")
        start, end = self.segment_window(stage, ws)
        if start is None:
            start, end = sr.start, sr.end
        want = end - start
        try:
            info = probe_media(mp4, str(self.setting("ffprobe", "ffprobe") or "ffprobe"))
        except ValueError as e:
            return DetectedStage(stage.key, "failed", detail=str(e))
        outputs = [f"scenes/{seg}.mp4", f"scenes/{seg}.json"]
        credit = ws / "credits" / f"{self.id}-{seg}.md"
        if credit.exists():
            outputs.append(rel(ws, credit))
        if info is None:
            return DetectedStage(stage.key, "unknown", outputs, "ffprobe not found: cannot verify the segment")
        if not info["has_video"] or not info["duration"]:
            return DetectedStage(stage.key, "failed", detail=f"scenes/{seg}.mp4 has no video stream")
        if abs(info["duration"] - want) > max(0.25, 0.02 * want):
            return DetectedStage(stage.key, "failed", outputs,
                                 f"duration {info['duration']:.2f}s, expected {want:.2f}s ({start:.2f}-{end:.2f})")
        cfg = workspace_config(ws)
        try:
            w, h = (int(x) for x in str(cfg.get("resolution", "")).lower().split("x"))
            if info["width"] and (info["width"], info["height"]) != (w, h):
                return DetectedStage(stage.key, "failed", outputs,
                                     f"resolution {info['width']}x{info['height']}, expected {w}x{h}")
        except ValueError:
            pass
        detail = f"{info['duration']:.2f}s {info['width']}x{info['height']}"
        if not credit.exists():
            detail += f"; credits/{self.id}-{seg}.md missing"
        return DetectedStage(stage.key, "completed", outputs, detail)

    def collect_outputs(self, workspace: Path, stage: StageSpec) -> list[str]:
        return list(self.detect(workspace, stage).outputs)

    def collect_credits(self, workspace: Path) -> list[str]:
        ws = Path(workspace)
        return sorted(rel(ws, p) for p in (ws / "credits").glob(f"{self.id}*") if p.is_file())

    # ---- native helpers ------------------------------------------------------------------------
    def manifest_stages(self) -> list[dict]:
        return [dict(s) for s in self.manifest.get("stages") or [] if isinstance(s, dict) and s.get("key")]

    def stage_specs(self, defs: list[dict], first_depends: Optional[list[str]] = None,
                    project_dir: str = "") -> list[StageSpec]:
        specs: list[StageSpec] = []
        prev = list(first_depends if first_depends is not None else ["setup"])
        render_heavy = set((self.manifest.get("concurrency") or {}).get("render_heavy_stages") or [])
        for d in defs:
            produces = [f"{project_dir}/{p}" if project_dir else p for p in d.get("produces") or []]
            spec = StageSpec(
                key=str(d["key"]), name=str(d.get("name") or d["key"]), description=str(d.get("description", "")),
                capability="native_pipeline", owner=self.id, executor="claude", lane=self.lane,
                depends_on=prev, gate=bool(d.get("gate")), required=not d.get("optional", False),
                engine_gate=bool(d.get("engine_gate")), outputs=produces,
                params={"maps_to": d.get("maps_to", ""), "render_heavy": d["key"] in render_heavy,
                        "project_dir": project_dir},
            )
            specs.append(spec)
            prev = [spec.key]
        return specs

    def detect_globs(self, workspace: Path, stage: StageSpec) -> DetectedStage:
        """Native-stage detection by the stage's expected output globs (workspace-relative)."""
        ws = Path(workspace)
        patterns = stage.outputs or []
        if not patterns:
            return DetectedStage(stage.key, "unknown", detail="the manifest declares no outputs for this stage")
        found, missing = [], []
        for pat in patterns:
            hits = [p for p in _glob.glob(str(ws / pat)) if Path(p).is_file() and Path(p).stat().st_size > 0]
            if hits:
                found.extend(sorted(rel(ws, Path(h)) for h in hits))
            else:
                missing.append(pat)
        if not found:
            return DetectedStage(stage.key, "pending", detail="no outputs yet")
        if missing:
            return DetectedStage(stage.key, "running", found, "missing: " + ", ".join(missing))
        status = "awaiting_approval" if stage.gate else "completed"
        return DetectedStage(stage.key, status, found, f"{len(found)} file(s)")

    def detect_native(self, workspace: Path, stage: StageSpec) -> DetectedStage:
        return self.detect_globs(workspace, stage)

    def detect(self, workspace: Path, stage: StageSpec) -> DetectedStage:
        if is_collaborative(stage):
            return self.detect_collaborative(workspace, stage)
        return self.detect_native(workspace, stage)


# --------------------------------------------------------------------------------------- generic adapter
class GenericAdapter(ManifestAdapter):
    """For repos with only a manifest (onboarded engines).

    Manifest keys used beyond the common ones:
      stages:  [{key, name, description, gate, engine_gate, optional, produces: [globs]}]
               produces are relative to the native project folder work/<engine-id>/<project_dir>
      project_dir: native project folder name (default "film")
      prompts: {<stage key> | "assets" | "narration" | "segment": "<template>"}
      prompt_template: fallback template
    Template placeholders: {marker} {workspace} {lane} {repo} {stage_key} {stage_name} {stage_description}
      {segment} {start} {end} {project} {config} {io_contract} {rules} {produces}
    Unknown placeholders are left as they are. The marker line, I/O contract and rules are always
    included even when a template omits them.
    """

    DEFAULT_TEMPLATE = (
        "{stage_description}\n\nEngine repository: {repo}. Read its documented instructions (README, SKILL.md,"
        " AGENTS.md or similar) and follow them, using only the commands it documents.\nProject folder: {project}.\n"
        "Expected outputs: {produces}.\n{config}"
    )

    def health(self, zero_cost: bool = True) -> EngineHealth:
        items, commit = self.common_health()
        req = self.manifest.get("requires") or {}
        if req.get("node"):
            items.append(self.node_health(str(req["node"])))
        if req.get("ffmpeg"):
            items.append(self.ffmpeg_health(str(req["ffmpeg"])))
        if not self.approved:
            items.append(HealthItem("approved", "Manifest approved", "warning", "drafted by onboarding; not approved yet",
                                    "review the engine.yaml and approve it in Settings > Engines"))
        cost = self.manifest.get("cost") or {}
        zc = bool(cost.get("zero_cost", False))
        return self.finish_health(items, commit, zero_cost, zero_cost_ready=zc and self.approved)

    def project_name(self) -> str:
        return slug(self.manifest.get("project_dir", "film"))

    def native_stages(self, config: dict) -> list[StageSpec]:
        cfg = config_dict(config)
        eo = (cfg.get("engine_options") or {}).get(self.id) or {}
        defs = [d for d in self.manifest_stages() if not d.get("when") or eo.get(d["when"])]
        return self.stage_specs(defs, project_dir=f"work/{self.id}/{self.project_name()}")

    def stage_prompt(self, stage: StageSpec, workspace: Path, config: dict, feedback: Optional[str] = None,
                     resume: bool = False) -> str:
        ws = Path(workspace).resolve()
        native = not is_collaborative(stage)
        prompts = self.manifest.get("prompts") or {}
        key = "segment" if stage.key.startswith(SEGMENT_PREFIX) else stage.key
        template = str(prompts.get(key) or self.manifest.get("prompt_template") or self.DEFAULT_TEMPLATE)
        start, end = self.segment_window(stage, ws)
        m = self.placeholders(ws)
        m.update({
            "marker": "", "stage_key": stage.key, "stage_name": stage.name, "stage_description": stage.description,
            "segment": segment_id(stage) if not native else "", "start": "" if start is None else f"{start:.3f}",
            "end": "" if end is None else f"{end:.3f}", "project": str(self.lane_dir(ws) / self.project_name()),
            "config": self.config_summary(config), "io_contract": "", "rules": "",
            "produces": ", ".join(stage.outputs) or "(see the repo docs)",
        })
        task = fill(template, m)
        return self.compose_prompt(stage, ws, config, task, feedback, resume, native)
