"""Per-video workspace layout (CONTRACT, docs/PHASE2_SPEC.md §5)."""
from __future__ import annotations

import json
from pathlib import Path

SUBDIRS = [
    "research", "scripts", "storyboard",
    "assets/images", "assets/footage", "assets/music", "assets/sfx", "assets/user",
    "narration/lines", "scenes", "subtitles", "renders", "outputs", "credits", "work", "logs",
    ".spacestudio/versions", ".spacestudio/tmp",
]

EVENTS_FILE = ".spacestudio/events.jsonl"


class WorkspaceError(ValueError):
    pass


def create(root: Path, video_id: str, snapshot: dict) -> Path:
    ws = Path(root) / video_id
    for sub in SUBDIRS:
        (ws / sub).mkdir(parents=True, exist_ok=True)
    (ws / "project.json").write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    return ws


def engine_dir(ws: Path, engine_id: str) -> Path:
    d = Path(ws) / "work" / engine_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve(ws: Path, rel: str) -> Path:
    """Resolve a workspace-relative path, refusing anything that escapes the workspace."""
    base = Path(ws).resolve()
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        raise WorkspaceError(f"path escapes the workspace: {rel}")
    return p


def read_events(ws: Path, since_line: int = 0) -> list[dict]:
    f = Path(ws) / EVENTS_FILE
    if not f.exists():
        return []
    out = []
    for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines()):
        if i < since_line or not line.strip():
            continue
        try:
            ev = json.loads(line)
            ev["_line"] = i
            out.append(ev)
        except json.JSONDecodeError:
            continue
    return out


def append_event(ws: Path, event: dict) -> None:
    f = Path(ws) / EVENTS_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
