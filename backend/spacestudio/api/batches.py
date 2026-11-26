"""Batch endpoints: create, CSV/TXT import preview, controls, export."""
from __future__ import annotations

import csv
import io
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional, get_args, get_origin

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlmodel import Session, select

from ..audit import audit
from ..db import get_session
from ..interchange.schema import VideoConfig
from ..models import Batch, Video
from ..schemas import BatchCreate, BatchItem, BatchStart, ImportPreview
from ..workflow.orchestrator import OrchestratorError
from .deps import call, orch, project_or_default
from .videos import zip_workspace

router = APIRouter()
MAX_IMPORT_BYTES = 2 * 1024 * 1024


# ------------------------------------------------------------------ status
def batch_status(videos: list[Video]) -> str:
    st = [v.status for v in videos]
    if not st:
        return "draft"
    if all(x == "draft" for x in st):
        return "draft"
    if all(x in ("completed",) for x in st):
        return "completed"
    if any(x == "running" for x in st):
        return "running"
    if any(x in ("queued", "interrupted") for x in st):
        return "queued"
    if any(x in ("awaiting_approval", "needs_input") for x in st):
        return "awaiting_approval"
    if any(x == "paused" for x in st):
        return "paused"
    if all(x in ("completed", "cancelled", "failed") for x in st):
        return "failed" if any(x == "failed" for x in st) else ("cancelled" if any(x == "cancelled" for x in st) else "completed")
    return "draft" if any(x == "draft" for x in st) else "running"


def batch_out(s: Session, b: Batch) -> dict:
    videos = list(s.exec(select(Video).where(Video.batch_id == b.id).order_by(Video.created_at)).all())
    status = batch_status(videos)
    if status != b.status:
        b.status = status
        s.add(b)
        s.commit()
    return {**b.model_dump(), "videos": [v.model_dump() for v in videos],
            "progress": {"total": len(videos), "completed": sum(1 for v in videos if v.status == "completed"),
                         "failed": sum(1 for v in videos if v.status == "failed"),
                         "running": sum(1 for v in videos if v.status == "running"),
                         "awaiting": sum(1 for v in videos if v.status in ("awaiting_approval", "needs_input"))}}


def _batch_or_404(s: Session, batch_id: str) -> Batch:
    b = s.get(Batch, batch_id)
    if b is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    return b


# ------------------------------------------------------------------ create
@router.post("/batches", status_code=201)
def create_batch(body: BatchCreate, s: Session = Depends(get_session)) -> dict:
    o = orch()
    p = project_or_default(s, body.project_id)
    try:
        o.build_config(body.shared_config)
    except ValueError as e:
        raise HTTPException(422, f"invalid shared config: {e}") from None
    configs = []
    for i, it in enumerate(body.items):
        try:
            configs.append(o.build_config(body.shared_config, it.overrides))
        except ValueError as e:
            raise HTTPException(422, f"item {i + 1} ({it.title}): invalid overrides: {e}") from None
    b = Batch(project_id=p.id, name=body.name, shared_config=body.shared_config)
    s.add(b)
    s.commit()
    for it, cfg in zip(body.items, configs):
        o.create_video(s, project_id=p.id, title=it.title, idea=it.idea, config=cfg, mode=body.mode,
                       primary_engine=body.primary_engine, batch_id=b.id, raise_on_plan_error=False)
    audit("batch.created", actor="user", batch_id=b.id, items=len(body.items))
    if body.start:
        _control(s, b, "start", None)
    return batch_out(s, b)


# ------------------------------------------------------------------ import
_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


def _coerce(field: str, raw: str) -> Any:
    """Turn a CSV cell into the VideoConfig field's type (bool/int/float/list/dict/str)."""
    ann = VideoConfig.model_fields[field].annotation
    raw = raw.strip()
    args = [a for a in get_args(ann) if a is not type(None)]
    base = ann
    if get_origin(ann) is not None and get_origin(ann) not in (list, dict) and len(args) == 1 \
            and isinstance(args[0], type):
        base = args[0]  # Optional[X] -> X
    origin = get_origin(base)
    if base is bool:
        if raw.lower() in _TRUE:
            return True
        if raw.lower() in _FALSE:
            return False
        raise ValueError(f"{field}: expected yes/no, got {raw!r}")
    if base is int:
        return int(float(raw))
    if base is float:
        return float(raw)
    if base is list or origin is list:
        return [x.strip() for x in re.split(r"[;|,]", raw) if x.strip()]
    if base is dict or origin is dict:
        import json
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError(f"{field}: expected a JSON object") from None
    return raw


def parse_csv(text: str) -> tuple[list[BatchItem], list[str]]:
    items: list[BatchItem] = []
    errors: list[str] = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ["the CSV has no header row"]
    cols = {c.strip().lower(): c for c in reader.fieldnames if c}
    if "idea" not in cols:
        return [], ["the CSV needs an 'idea' column (and optionally 'title' plus VideoConfig fields)"]
    known = set(VideoConfig.model_fields)
    ignored = [c for c in cols if c not in known and c not in ("title", "idea")]
    if ignored:
        errors.append(f"ignored unknown columns: {', '.join(ignored)}")
    for n, row in enumerate(reader, start=2):
        idea = (row.get(cols["idea"]) or "").strip()
        title = (row.get(cols["title"]) or "").strip() if "title" in cols else ""
        if not idea and not title:
            continue
        if not idea:
            errors.append(f"row {n}: missing idea")
            continue
        overrides: dict[str, Any] = {}
        bad = False
        for lc, orig in cols.items():
            if lc in known and (row.get(orig) or "").strip():
                try:
                    overrides[lc] = _coerce(lc, row[orig])
                except (ValueError, TypeError) as e:
                    errors.append(f"row {n}: {e}")
                    bad = True
        if bad:
            continue
        try:
            VideoConfig.model_validate(overrides)
        except ValueError as e:
            errors.append(f"row {n}: invalid values: {str(e).splitlines()[0]}")
            continue
        items.append(BatchItem(title=title or _title_from(idea), idea=idea, overrides=overrides))
    return items, errors


def _title_from(idea: str) -> str:
    words = idea.split()
    t = " ".join(words[:8])
    return (t + "…") if len(words) > 8 else t


def parse_txt(text: str) -> tuple[list[BatchItem], list[str]]:
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line)
        items.append(BatchItem(title=_title_from(line), idea=line))
    return items, [] if items else ["no ideas found (one idea per line)"]


@router.post("/batches/import", response_model=ImportPreview)
async def import_batch(file: UploadFile = File(...)) -> dict:
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "import file too large (max 2 MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "the file must be UTF-8 text") from None
    name = (file.filename or "").lower()
    if name.endswith(".csv") or (not name.endswith(".txt") and "," in text.splitlines()[0] if text else False):
        items, errors = parse_csv(text)
    else:
        items, errors = parse_txt(text)
    return {"items": [i.model_dump() for i in items], "errors": errors}


# ------------------------------------------------------------------ list / controls
@router.get("/batches")
def list_batches(project_id: Optional[str] = None, s: Session = Depends(get_session)) -> list[dict]:
    q = select(Batch)
    if project_id:
        q = q.where(Batch.project_id == project_id)
    return [batch_out(s, b) for b in s.exec(q.order_by(Batch.created_at)).all()]


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, s: Session = Depends(get_session)) -> dict:
    return batch_out(s, _batch_or_404(s, batch_id))


_ACTION_FROM = {
    "start": {"draft", "paused"},
    "pause": {"queued", "running", "awaiting_approval", "needs_input", "interrupted", "failed"},
    "resume": {"paused", "interrupted"},
    "cancel": {"draft", "queued", "running", "paused", "awaiting_approval", "needs_input", "interrupted", "failed"},
}


def _control(s: Session, b: Batch, action: str, video_ids: Optional[list[str]]) -> list[str]:
    o = orch()
    videos = list(s.exec(select(Video).where(Video.batch_id == b.id).order_by(Video.created_at)).all())
    if video_ids:
        wanted = set(video_ids)
        unknown = wanted - {v.id for v in videos}
        if unknown:
            raise HTTPException(400, f"not in this batch: {', '.join(sorted(unknown))}")
        videos = [v for v in videos if v.id in wanted]
    done, errors = [], []
    for v in videos:
        if v.status not in _ACTION_FROM[action] and not (action == "resume" and v.paused):
            continue
        try:
            getattr(o, f"{action}_video")(s, v)
            done.append(v.id)
        except OrchestratorError as e:
            errors.append(f"{v.title}: {e}")
    audit(f"batch.{action}", actor="user", batch_id=b.id, videos=done, errors=errors)
    return done


def _batch_action(action: str):
    def endpoint(batch_id: str, body: Optional[BatchStart] = None, s: Session = Depends(get_session)) -> dict:
        b = _batch_or_404(s, batch_id)
        _control(s, b, action, body.video_ids if body else None)
        return batch_out(s, b)
    endpoint.__name__ = f"batch_{action}"
    return endpoint


for _a in ("start", "pause", "resume", "cancel"):
    router.add_api_route(f"/batches/{{batch_id}}/{_a}", _batch_action(_a), methods=["POST"])


@router.get("/batches/{batch_id}/export")
def export_batch(batch_id: str, s: Session = Depends(get_session)) -> FileResponse:
    b = _batch_or_404(s, batch_id)
    videos = list(s.exec(select(Video).where(Video.batch_id == b.id)).all())
    tmpdir = Path(tempfile.mkdtemp(prefix="spacestudio-batch-"))
    dest = tmpdir / f"{b.id}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
        for v in videos:
            ws = Path(v.workspace)
            if not ws.exists():
                continue
            inner = zip_workspace(ws, tmpdir / f"{v.id}.zip")
            z.write(inner, f"{v.id}.zip")
    audit("batch.exported", actor="user", batch_id=b.id)
    import shutil
    return FileResponse(dest, media_type="application/zip", filename=f"{b.id}.zip",
                        background=BackgroundTask(lambda: shutil.rmtree(tmpdir, ignore_errors=True)))
