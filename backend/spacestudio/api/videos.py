"""Video, stage and workspace-file endpoints."""
from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlmodel import Session, select

from ..audit import audit
from ..db import get_session
from ..interchange import schema as ix
from ..models import Approval, Asset, AuditLog, ClaudeSession, Run, Stage, StageVersion, Video, now
from ..schemas import AnswerBody, FeedbackBody, FileEntry, HistoryItem, StageDetail, VideoCreate, VideoDetail
from ..security import PathRefused, guard, redact
from ..workflow.orchestrator import redact_stage
from .deps import call, orch, project_or_default, stage_or_404, video_or_404

router = APIRouter()

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
TEXT_EXT = {".md", ".txt", ".srt", ".vtt", ".log", ".jsonl", ".yaml", ".yml", ".csv", ".html"}
EDITABLE_EXT = {".json", ".md", ".txt", ".srt"}
EDITABLE_DIRS = ("scripts/", "storyboard/", "research/", "subtitles/")
EDIT_OWNER = {"scripts/": "script", "storyboard/": "storyboard", "subtitles/": "captions", "brief.json": "concept",
              "research/factcheck.json": "factcheck", "research/": "research"}
MAX_EDIT_BYTES = 2 * 1024 * 1024


def _kind(p: Path) -> str:
    if p.is_dir():
        return "dir"
    ext = p.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".json":
        return "json"
    if ext in TEXT_EXT:
        return "text"
    return "other"


def _entry(ws: Path, p: Path) -> dict:
    st = p.stat()
    return {"path": Path(os.path.relpath(p, ws)).as_posix(), "name": p.name, "is_dir": p.is_dir(),
            "size": 0 if p.is_dir() else st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(), "kind": _kind(p)}


def _ws(v: Video) -> Path:
    if not v.workspace:
        raise HTTPException(409, "the video has no workspace yet")
    return Path(v.workspace)


def _guard(v: Video, rel: str) -> Path:
    try:
        return guard(_ws(v), rel)
    except PathRefused as e:
        raise HTTPException(403, str(e)) from None


def _detail(s: Session, v: Video) -> dict:
    o = orch()
    sts = o.stages(s, v.id)
    sess = list(s.exec(select(ClaudeSession).where(ClaudeSession.video_id == v.id)).all())
    return {"video": v.model_dump(), "stages": [redact_stage(x) for x in sts], "sessions": sess,
            "simulated": o.runner.is_simulated()}


# ------------------------------------------------------------------------ videos
@router.get("/videos")
def list_videos(project_id: Optional[str] = None, batch_id: Optional[str] = None, status: Optional[str] = None,
                s: Session = Depends(get_session)) -> list[Video]:
    q = select(Video)
    if project_id:
        q = q.where(Video.project_id == project_id)
    if batch_id:
        q = q.where(Video.batch_id == batch_id)
    if status:
        q = q.where(Video.status == status)
    return list(s.exec(q.order_by(Video.created_at)).all())


@router.post("/videos", status_code=201, response_model=VideoDetail)
def create_video(body: VideoCreate, s: Session = Depends(get_session)) -> dict:
    o = orch()
    p = project_or_default(s, body.project_id)
    try:
        cfg = o.build_config(body.config)
    except ValueError as e:
        raise HTTPException(422, f"invalid config: {e}") from None
    v = call(o.create_video, s, project_id=p.id, title=body.title, idea=body.idea, config=cfg, mode=body.mode,
             primary_engine=body.primary_engine)
    if body.start:
        call(o.start_video, s, v)
    return _detail(s, v)


@router.get("/videos/{video_id}", response_model=VideoDetail)
def get_video(video_id: str, s: Session = Depends(get_session)) -> dict:
    return _detail(s, video_or_404(s, video_id))


def _video_action(name: str):
    def endpoint(video_id: str, s: Session = Depends(get_session)) -> Video:
        o = orch()
        v = video_or_404(s, video_id)
        return call(getattr(o, f"{name}_video"), s, v)
    endpoint.__name__ = f"{name}_video"
    return endpoint


for _name in ("start", "pause", "resume", "cancel", "retry"):
    router.add_api_route(f"/videos/{{video_id}}/{_name}", _video_action(_name), methods=["POST"])


@router.patch("/videos/{video_id}/config")
def patch_config(video_id: str, patch: dict[str, Any], s: Session = Depends(get_session)) -> Video:
    v = video_or_404(s, video_id)
    unknown = [k for k in patch if k not in ix.VideoConfig.model_fields]
    if unknown:
        raise HTTPException(422, f"unknown config fields: {', '.join(unknown)}")
    try:
        return call(orch().update_config, s, v, patch)
    except ValueError as e:
        raise HTTPException(422, f"invalid config: {e}") from None


@router.delete("/videos/{video_id}")
def delete_video(video_id: str, s: Session = Depends(get_session)) -> dict:
    v = video_or_404(s, video_id)
    if v.status in ("running", "queued"):
        raise HTTPException(409, "cancel the video before deleting it")
    for st in orch().stages(s, v.id):
        s.delete(st)
    s.delete(v)
    s.commit()
    audit("video.deleted", actor="user", video_id=video_id, workspace_kept=v.workspace)
    return {"ok": True, "workspace_kept": v.workspace}


# ------------------------------------------------------------------------ stages
def _log_lines(st: Stage, s: Session, tail: int) -> list[str]:
    run = s.exec(select(Run).where(Run.stage_id == st.id).order_by(Run.started_at.desc())).first()
    if run is None or not run.log_path or not Path(run.log_path).exists():
        return []
    with open(run.log_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    return [redact(x) for x in lines[-tail:]]


@router.get("/videos/{video_id}/stages/{key}", response_model=StageDetail)
def stage_detail(video_id: str, key: str, s: Session = Depends(get_session)) -> dict:
    video_or_404(s, video_id)
    st = stage_or_404(s, video_id, key)
    return {"stage": redact_stage(st),
            "versions": list(s.exec(select(StageVersion).where(StageVersion.stage_id == st.id)
                                    .order_by(StageVersion.version)).all()),
            "approvals": list(s.exec(select(Approval).where(Approval.stage_id == st.id)
                                     .order_by(Approval.created_at)).all()),
            "runs": list(s.exec(select(Run).where(Run.stage_id == st.id).order_by(Run.started_at)).all()),
            "log_tail": _log_lines(st, s, 100)}


@router.get("/videos/{video_id}/stages/{key}/logs")
def stage_logs(video_id: str, key: str, tail: int = Query(500, ge=1, le=20000),
               s: Session = Depends(get_session)) -> dict:
    st = stage_or_404(s, video_id, key)
    return {"lines": _log_lines(st, s, tail)}


@router.post("/videos/{video_id}/stages/{key}/approve")
def approve(video_id: str, key: str, body: Optional[FeedbackBody] = None, s: Session = Depends(get_session)) -> Stage:
    st = stage_or_404(s, video_id, key)
    return call(orch().approve, s, st, note=(body.feedback if body else ""))


@router.post("/videos/{video_id}/stages/{key}/request-changes")
def request_changes(video_id: str, key: str, body: FeedbackBody, s: Session = Depends(get_session)) -> Stage:
    st = stage_or_404(s, video_id, key)
    return call(orch().request_changes, s, st, body.feedback)


@router.post("/videos/{video_id}/stages/{key}/reject")
def reject(video_id: str, key: str, body: Optional[FeedbackBody] = None, s: Session = Depends(get_session)) -> Stage:
    st = stage_or_404(s, video_id, key)
    return call(orch().reject, s, st, body.feedback if body else "")


@router.post("/videos/{video_id}/stages/{key}/retry")
def retry_stage(video_id: str, key: str, s: Session = Depends(get_session)) -> Stage:
    return call(orch().retry_stage, s, stage_or_404(s, video_id, key))


@router.post("/videos/{video_id}/stages/{key}/skip")
def skip_stage(video_id: str, key: str, s: Session = Depends(get_session)) -> Stage:
    return call(orch().skip, s, stage_or_404(s, video_id, key))


class AllowToolsBody(BaseModel):
    rules: list[str]
    confirm: bool = False


@router.post("/videos/{video_id}/stages/{key}/allow-tools")
def allow_tools(video_id: str, key: str, body: AllowToolsBody, s: Session = Depends(get_session)) -> Stage:
    """Approve command rules (e.g. Bash(curl *)) that Claude was denied, for this video only, and continue."""
    if not body.confirm:
        raise HTTPException(400, "confirm must be true: these commands will be allowed for this video")
    return call(orch().allow_tools, s, stage_or_404(s, video_id, key), body.rules)


@router.post("/videos/{video_id}/stages/{key}/answer")
def answer_stage(video_id: str, key: str, body: AnswerBody, s: Session = Depends(get_session)) -> Stage:
    return call(orch().answer, s, stage_or_404(s, video_id, key), body.answer)


# ------------------------------------------------------------------------ files
@router.get("/videos/{video_id}/files", response_model=list[FileEntry])
def list_files(video_id: str, path: str = "", recursive: bool = False, s: Session = Depends(get_session)) -> list[dict]:
    v = video_or_404(s, video_id)
    ws = _ws(v)
    d = _guard(v, path)
    if not d.exists():
        raise HTTPException(404, f"{path or '.'} does not exist")
    if d.is_file():
        return [_entry(ws, d)]
    it = d.rglob("*") if recursive else d.iterdir()
    out = []
    for p in sorted(it, key=lambda x: (not x.is_dir(), str(x))):
        if p.name == "node_modules" or "node_modules" in p.parts:
            continue
        try:
            guard(ws, os.path.relpath(p, ws))  # skips symlinks pointing outside
        except PathRefused:
            continue
        out.append(_entry(ws, p))
        if len(out) >= 5000:
            break
    return out


@router.get("/videos/{video_id}/file")
def get_file(video_id: str, path: str, s: Session = Depends(get_session)) -> FileResponse:
    v = video_or_404(s, video_id)
    p = _guard(v, path)
    if not p.is_file():
        raise HTTPException(404, f"{path} not found")
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if p.suffix.lower() in (".json", ".jsonl", ".md", ".txt", ".srt", ".log"):
        media = "application/json" if p.suffix.lower() == ".json" else "text/plain; charset=utf-8"
    # Starlette's FileResponse answers HTTP Range requests (206) itself.
    return FileResponse(p, media_type=media, headers={"Accept-Ranges": "bytes"})


def _edit_owner(rel: str) -> Optional[str]:
    for prefix, key in sorted(EDIT_OWNER.items(), key=lambda kv: -len(kv[0])):
        if rel == prefix or rel.startswith(prefix):
            return key
    return None


@router.put("/videos/{video_id}/file", response_model=FileEntry)
async def put_file(video_id: str, path: str, request: Request) -> dict:
    from ..db import session_scope

    body = await request.body()
    if len(body) > MAX_EDIT_BYTES:
        raise HTTPException(413, "file too large for the editor")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "only UTF-8 text can be edited") from None
    # The UI sends {"content": "<text>"}; a raw text body is accepted too.
    if "json" in (request.headers.get("content-type") or ""):
        try:
            wrapped = json.loads(text)
        except json.JSONDecodeError:
            wrapped = None
        if isinstance(wrapped, dict) and set(wrapped) == {"content"} and isinstance(wrapped["content"], str):
            text = wrapped["content"]
    o = orch()
    with o.lock, session_scope() as s:
        v = video_or_404(s, video_id)
        ws = _ws(v)
        p = _guard(v, path)
        rel = p.relative_to(ws.resolve()).as_posix()
        if p.suffix.lower() not in EDITABLE_EXT or not (rel == "brief.json" or rel.startswith(EDITABLE_DIRS)):
            raise HTTPException(403, "only .json/.md/.txt/.srt files under scripts/, storyboard/, research/, "
                                     "subtitles/ or brief.json can be edited")
        if p.suffix.lower() == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise HTTPException(422, f"invalid JSON: {e}") from None
            model = ix.FILES.get(rel)
            if model:
                try:
                    ix.MODELS[model].model_validate(data)
                except Exception as e:
                    raise HTTPException(422, f"{rel} does not match the {model} schema: {str(e)[:500]}") from None
        owner_key = _edit_owner(rel)
        owner = s.exec(select(Stage).where(Stage.video_id == v.id, Stage.key == owner_key)).first() if owner_key else None
        if owner is not None and owner.status == "running":
            raise HTTPException(409, f"stage {owner.key} is running; wait until it finishes")
        sv = o.snapshot_file(s, v, rel, owner_key, note=f"before manual edit of {rel}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        s.commit()
        audit("file.edited", actor="user", video_id=v.id, path=rel, version=sv.version if sv else None)
        return _entry(ws.resolve(), p)


@router.get("/videos/{video_id}/assets")
def list_assets(video_id: str, s: Session = Depends(get_session)) -> list[Asset]:
    video_or_404(s, video_id)
    return list(s.exec(select(Asset).where(Asset.video_id == video_id).order_by(Asset.created_at)).all())


@router.get("/videos/{video_id}/history", response_model=list[HistoryItem])
def history(video_id: str, s: Session = Depends(get_session)) -> list[dict]:
    v = video_or_404(s, video_id)
    items: list[dict] = []
    for a in s.exec(select(AuditLog).where(AuditLog.video_id == v.id)).all():
        items.append({"at": a.at, "kind": "audit", "title": a.event, "details": {**(a.details or {}), "actor": a.actor}})
    sts = {st.id: st for st in orch().stages(s, v.id)}
    if sts:
        for sv in s.exec(select(StageVersion).where(StageVersion.stage_id.in_(list(sts)))).all():
            items.append({"at": sv.created_at, "kind": "version",
                          "title": f"{sts[sv.stage_id].key} v{sv.version}" + (" (approved)" if sv.approved else ""),
                          "details": {"stage": sts[sv.stage_id].key, "version": sv.version, "files": sv.files,
                                      "snapshot_dir": sv.snapshot_dir, "note": sv.note}})
    items.sort(key=lambda x: x["at"])
    return items


EXCLUDE_PARTS = {"node_modules", ".venv"}


def _fallback_zip(ws: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for p in ws.rglob("*"):
            rel = p.relative_to(ws)
            if EXCLUDE_PARTS & set(rel.parts) or rel.parts[:2] == (".spacestudio", "tmp") or not p.is_file():
                continue
            z.write(p, rel.as_posix())
    return dest


def zip_workspace(ws: Path, dest: Path) -> Path:
    try:
        from ..assembly.pipeline import export_zip  # agent D
    except ImportError:
        return _fallback_zip(ws, dest)
    return Path(export_zip(ws, dest))


@router.get("/videos/{video_id}/export")
def export_video(video_id: str, s: Session = Depends(get_session)) -> FileResponse:
    v = video_or_404(s, video_id)
    ws = _ws(v)
    if not ws.exists():
        raise HTTPException(404, "the workspace does not exist yet")
    tmpdir = Path(tempfile.mkdtemp(prefix="spacestudio-export-"))
    dest = zip_workspace(ws, tmpdir / f"{v.id}.zip")
    audit("video.exported", actor="user", video_id=v.id)
    return FileResponse(dest, media_type="application/zip", filename=f"{v.id}.zip",
                        background=BackgroundTask(lambda: _rm(tmpdir)))


def _rm(d: Path) -> None:
    import shutil
    shutil.rmtree(d, ignore_errors=True)
