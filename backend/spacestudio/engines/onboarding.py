"""Engine onboarding: a Claude job reads a new repository and drafts an engine.yaml.

Flow: the API (agent A) starts a Claude run with ``build_onboarding_prompt(url_or_path, target_dir)``;
Claude writes ``<target_dir>/<id>/engine.yaml`` (target_dir is normally ``<data_dir>/engines``).
``load_draft()`` / ``validate_manifest()`` check it; the registry loads it with ``approved: false`` and
the GenericAdapter; ``approve_manifest()`` flips the flag after the user reviewed it. Drafts can never
select Python code (adapter must be ``generic``) and can't grant unscoped shell access.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from ..workflow.stages import Capability

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_SAFE_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"}
_BASH_RE = re.compile(r"^Bash\(([^()]+)\)$")
_FORBIDDEN_BASH = re.compile(r"^(\*|sudo\b|rm\b|curl\b|wget\b|chmod\b|chown\b|git push\b|ssh\b|scp\b|dd\b|mkfs)")
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)
REQUIRED_TOP = ["id", "name", "description", "license", "repo", "invocation", "requires", "capabilities",
                "options", "cost", "concurrency", "setup", "allowed_tools", "env", "adapter", "stages"]

EXAMPLE = """id: my-engine
name: My Engine
description: One paragraph: what the repo makes and how (from its README).
license: MIT
repo: {url: https://github.com/<owner>/<repo>, default_dir: <folder name under the engines dir>}
adapter: generic
invocation: {kind: claude-plugin | claude-skill | claude-cwd, plugin_dir: <relative dir with .claude-plugin/plugin.json or skills/, if any>, skill: <skill name, if any>, skill_file: <relative path to SKILL.md / AGENTS.md / guide>}
requires: {commands: [ffmpeg, node], python: ">=3.10", node: ">=20", ffmpeg: ">=6"}
health: {expected_files: [README.md, <the files the workflow depends on>]}
capabilities:            # keys: SpaceStudio capabilities; only what the repo really does
  scene_render: {supported: true, detail: "<tool and documented command>"}
  narration: {supported: false, detail: "no TTS"}
options: {}              # styles/looks/presets the repo documents, each {id, name, zero_cost}
cost: {zero_cost: true, paid_env: [<every paid API key env var it reads>], free_env: [], notes: "..."}
concurrency: {render_heavy_stages: [<stage keys>], env: {}}
setup: [{id: install, title: "...", command: "<exact documented command>", cwd: "{repo}"}]
allowed_tools: [Read, Write, Edit, Glob, Grep, "Bash(python3 *)", "Bash(ffmpeg *)"]
env: {}
project_dir: film
stages:                  # the repo's own workflow for native mode, in order
  - {key: draft, name: "Draft", description: "...", gate: false, engine_gate: false, produces: ["out/*.mp4"]}
prompts: {}              # optional per-stage templates; placeholders {repo} {workspace} {lane} {project} {segment} {start} {end} {config}
"""


def build_onboarding_prompt(url_or_path: str, target_dir: Path, clone_dir: Optional[Path] = None,
                            existing_ids: Iterable[str] = ()) -> str:
    target = Path(target_dir).expanduser().resolve()
    caps = ", ".join(c.value for c in Capability)
    is_url = bool(re.match(r"^(https?://|git@)", str(url_or_path)))
    where = (f"Clone it read-only first: `git clone --depth 1 {url_or_path} {clone_dir}` (only if {clone_dir} does not exist)."
             if is_url and clone_dir else
             f"The repository is at {url_or_path}." if not is_url else
             f"Read the repository at {url_or_path} (clone it with `git clone --depth 1` into a folder under {target}/_src/).")
    taken = ", ".join(sorted(existing_ids)) or "none"
    return f"""SPACESTUDIO_STAGE=onboard VIDEO=- WORKSPACE={target} LANE=onboarding
# Onboard a new engine repository into SpaceStudio

Repository: {url_or_path}
{where}

Read it thoroughly and draft a SpaceStudio engine manifest. Read the README, LICENSE, every agent guide
(CLAUDE.md, AGENTS.md, SKILL.md files, .claude-plugin/, docs/) and the argparse / usage text of each script it
tells an agent to run. Every command, path, flag, file and capability you write must exist in the repo: quote
the documented commands exactly, never invent one. When something is unclear, say so in the field's detail.

Write the manifest to {target}/<id>/engine.yaml, where <id> is a short lowercase slug (letters, digits, hyphens)
not already used ({taken}). Schema, with an example of every field:

```yaml
{EXAMPLE}```

Rules:
- capabilities keys must be among: {caps}. List supported: false for what the repo cannot do.
- adapter must be `generic`. Do not set `approved` (the user approves the draft in SpaceStudio).
- cost.paid_env: every environment variable that unlocks a paid provider; zero_cost true only if the documented
  default path costs nothing. No secret values anywhere in the file.
- allowed_tools: only Read, Write, Edit, Glob, Grep, WebSearch, WebFetch and scoped Bash patterns such as
  "Bash(python3 *)" for the interpreters/commands the workflow runs. Never bare "Bash", "Bash(*)", sudo, rm, curl.
- setup: the repo's documented install commands, with cwd "{{repo}}". Do NOT run them.
- stages: the repo's workflow in order, with `produces` globs relative to the project folder and gate/engine_gate
  true where the repo itself stops for the user's approval.
- Do not modify the repository. Write nothing outside {target}.

When done, append {{"type":"stage_completed","stage":"onboard","outputs":["<id>/engine.yaml"]}} to
{target}/.spacestudio/events.jsonl (create the folder) and end your turn with a short summary of what the repo
does and anything you could not verify.
"""


def validate_manifest(data: Any, existing_ids: Iterable[str] = (), onboarded: bool = True) -> list[str]:
    """Return human-readable problems (empty list = valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["the manifest is not a YAML mapping"]
    for k in REQUIRED_TOP:
        if k not in data:
            errors.append(f"missing field: {k}")
    eid = data.get("id")
    if not isinstance(eid, str) or not ID_RE.match(eid):
        errors.append("id must be a lowercase slug (letters, digits, hyphens)")
    elif eid in set(existing_ids):
        errors.append(f"id {eid!r} is already used by another engine")
    for k in ("name", "description", "license"):
        if k in data and not (isinstance(data[k], str) and data[k].strip()):
            errors.append(f"{k} must be a non-empty string")
    repo = data.get("repo")
    if "repo" in data and not (isinstance(repo, dict) and repo.get("default_dir")):
        errors.append("repo must have url and default_dir")
    elif isinstance(repo, dict) and "url" not in repo:
        errors.append("repo.url is missing")
    inv = data.get("invocation")
    if "invocation" in data and not (isinstance(inv, dict) and inv.get("kind")):
        errors.append("invocation.kind is required")
    req = data.get("requires")
    if "requires" in data and not (isinstance(req, dict) and isinstance(req.get("commands", []), list)):
        errors.append("requires.commands must be a list")
    caps = data.get("capabilities")
    valid_caps = {c.value for c in Capability}
    if "capabilities" in data:
        if not isinstance(caps, dict) or not caps:
            errors.append("capabilities must be a non-empty mapping")
        else:
            for k, v in caps.items():
                if k not in valid_caps:
                    errors.append(f"unknown capability {k!r}")
                if not isinstance(v, dict) or not isinstance(v.get("supported"), bool):
                    errors.append(f"capabilities.{k} needs supported: true|false and a detail")
    if "options" in data and not isinstance(data.get("options"), dict):
        errors.append("options must be a mapping")
    cost = data.get("cost")
    if "cost" in data:
        if not isinstance(cost, dict) or cost.get("zero_cost") not in (True, False, "partial") \
                or not isinstance(cost.get("paid_env", None), list):
            errors.append("cost needs zero_cost: true | false | partial and paid_env: [list]")
    conc = data.get("concurrency")
    if "concurrency" in data and not (isinstance(conc, dict) and isinstance(conc.get("render_heavy_stages", []), list)
                                      and isinstance(conc.get("env", {}), dict)):
        errors.append("concurrency needs render_heavy_stages: [list] and env: {mapping}")
    setup = data.get("setup")
    if "setup" in data:
        if not isinstance(setup, list):
            errors.append("setup must be a list")
        else:
            for i, s in enumerate(setup):
                if not isinstance(s, dict) or not all(isinstance(s.get(k), str) and s.get(k) for k in ("id", "title", "command")):
                    errors.append(f"setup[{i}] needs id, title, command (and cwd)")
    tools = data.get("allowed_tools")
    if "allowed_tools" in data:
        if not isinstance(tools, list) or not tools:
            errors.append("allowed_tools must be a non-empty list")
        else:
            for t in tools:
                if not isinstance(t, str):
                    errors.append(f"allowed_tools entry {t!r} is not a string")
                    continue
                if t in _SAFE_TOOLS:
                    continue
                m = _BASH_RE.match(t)
                if not m:
                    errors.append(f"allowed_tools entry {t!r} is not allowed (use scoped Bash(<cmd> *) patterns)")
                elif _FORBIDDEN_BASH.match(m.group(1).strip()):
                    errors.append(f"allowed_tools entry {t!r} is too broad or dangerous")
    for section in ("env",):
        env = data.get(section)
        if section in data and not isinstance(env, dict):
            errors.append(f"{section} must be a mapping")
        elif isinstance(env, dict):
            for k, v in env.items():
                if _SECRET_NAME.search(str(k)) and v not in (None, ""):
                    errors.append(f"env.{k} looks like a secret; manifests must not contain secrets")
    if onboarded:
        if data.get("adapter") not in (None, "generic"):
            errors.append("an onboarded manifest must use adapter: generic")
        if data.get("approved"):
            errors.append("a draft must not approve itself (remove approved)")
    stages = data.get("stages")
    if "stages" in data:
        if not isinstance(stages, list) or not stages:
            errors.append("stages must be a non-empty list")
        else:
            seen = set()
            for i, s in enumerate(stages):
                if not isinstance(s, dict) or not isinstance(s.get("key"), str) or not s["key"]:
                    errors.append(f"stages[{i}] needs a key")
                    continue
                if s["key"] in seen:
                    errors.append(f"duplicate stage key {s['key']!r}")
                seen.add(s["key"])
                if s["key"] in ("assets", "narration") or s["key"].startswith("segment:"):
                    errors.append(f"stage key {s['key']!r} is reserved for collaborative stages")
                if not isinstance(s.get("produces", []), list):
                    errors.append(f"stages[{i}].produces must be a list of globs")
    return errors


def load_draft(path: Path, existing_ids: Iterable[str] = ()) -> tuple[Optional[dict], list[str]]:
    """Parse and validate a drafted engine.yaml."""
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as e:
        return None, [f"cannot read {p}: {e}"]
    except yaml.YAMLError as e:
        return None, [f"invalid YAML: {e}"]
    errors = validate_manifest(data, existing_ids=existing_ids, onboarded=True)
    return (data if isinstance(data, dict) else None), errors


def find_drafts(target_dir: Path) -> list[Path]:
    return sorted(Path(target_dir).glob("*/engine.yaml"))


def approve_manifest(path: Path, existing_ids: Iterable[str] = ()) -> dict:
    """Mark a reviewed draft approved (after validating it again). Raises ValueError with the problems."""
    p = Path(path)
    data, errors = load_draft(p, existing_ids=existing_ids)
    if errors or data is None:
        raise ValueError("; ".join(errors) or "empty manifest")
    data["approved"] = True
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return data
