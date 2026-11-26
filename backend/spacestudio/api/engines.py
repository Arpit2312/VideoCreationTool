"""Engine endpoints: list, detail, health, patch, setup (confirmed + audited), onboarding."""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..audit import audit
from ..config import get_settings, save_settings
from ..db import get_session, session_scope
from ..models import Engine, Run, now
from ..schemas import EnginePatch, EngineSetupBody, OnboardBody
from ..security import child_env, redact, redact_obj
from . import health as health_mod
from .deps import orch

router = APIRouter()


def sync_engines(s: Session, registry: Any) -> None:
    """Make sure every registered engine has a row (enabled flag, approval, path)."""
    if registry is None:
        return
    for a in registry.all():
        m = getattr(a, "manifest", {}) or {}
        row = s.get(Engine, a.id)
        if row is None:
            row = Engine(id=a.id, name=m.get("name", a.id), enabled=m.get("enabled", True) is not False,
                         approved=bool(m.get("approved", not m.get("onboarded", False))))
        row.name = m.get("name", a.id)
        row.path = str(a.repo_path) if a.repo_path else row.path
        row.manifest = m
        s.add(row)
    s.commit()


def _adapter(engine_id: str) -> Any:
    reg = orch().registry_or_none()
    if reg is None:
        raise HTTPException(503, "the engine registry is not available")
    try:
        return reg.get(engine_id)
    except KeyError:
        raise HTTPException(404, f"engine {engine_id} not found") from None


def engine_info(a: Any, row: Optional[Engine]) -> dict:
    m = getattr(a, "manifest", {}) or {}
    health = row.last_health if row is not None and row.last_health else None
    return redact_obj({
        "id": a.id, "name": m.get("name", a.id), "description": m.get("description", ""),
        "license": m.get("license", "") if isinstance(m.get("license"), str) else (m.get("repo") or {}).get("license", ""),
        "enabled": row.enabled if row is not None else True,
        "approved": row.approved if row is not None else True,
        "path": str(a.repo_path) if a.repo_path else (row.path if row else ""),
        "pinned_commit": row.pinned_commit if row is not None else None,
        "capabilities": a.capabilities(), "options": a.options(), "cost": m.get("cost", {}),
        "health": health, "setup_steps": a.setup_steps(),
    })


@router.get("/engines")
def list_engines(s: Session = Depends(get_session)) -> list[dict]:
    reg = orch().registry_or_none()
    if reg is None:
        return []
    sync_engines(s, reg)
    return [engine_info(a, s.get(Engine, a.id)) for a in reg.all()]


@router.get("/engines/{engine_id}")
def get_engine(engine_id: str, s: Session = Depends(get_session)) -> dict:
    a = _adapter(engine_id)
    return engine_info(a, s.get(Engine, engine_id))


@router.post("/engines/{engine_id}/health")
def check_engine(engine_id: str, s: Session = Depends(get_session)) -> dict:
    a = _adapter(engine_id)
    h = health_mod.engine_health(a, get_settings().zero_cost_default)
    row = s.get(Engine, engine_id) or Engine(id=engine_id, name=a.manifest.get("name", engine_id))
    row.last_health = h
    s.add(row)
    s.commit()
    return engine_info(a, row)


@router.patch("/engines/{engine_id}")
def patch_engine(engine_id: str, body: EnginePatch, s: Session = Depends(get_session)) -> dict:
    a = _adapter(engine_id)
    row = s.get(Engine, engine_id) or Engine(id=engine_id, name=a.manifest.get("name", engine_id))
    changes = body.model_dump(exclude_none=True)
    if "enabled" in changes:
        row.enabled = changes["enabled"]
    if "approved" in changes:
        row.approved = changes["approved"]
    if "pinned_commit" in changes:
        row.pinned_commit = changes["pinned_commit"] or None
    s.add(row)
    s.commit()
    if "path" in changes:
        p = Path(changes["path"]).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"{p} is not a directory")
        paths = dict(get_settings().engine_paths)
        paths[engine_id] = str(p)
        new = save_settings({"engine_paths": paths})
        o = orch()
        o.settings = new
        try:
            from ..engines import registry as regmod
            for fn in ("get_registry",):
                f = getattr(regmod, fn, None)
                if f is not None and hasattr(f, "cache_clear"):
                    f.cache_clear()
            if hasattr(regmod, "reset_registry"):
                regmod.reset_registry()
        except ImportError:
            pass
        reg = o.registry_or_none()
        if reg is not None:
            sync_engines(s, reg)
            a = reg.get(engine_id)
        row = s.get(Engine, engine_id)
    audit("engine.updated", actor="user", engine=engine_id, changes=changes)
    return engine_info(a, row)


def _substitute(text: str, repo: Optional[Path], engines_dir: Path) -> str:
    return (text.replace("{repo}", str(repo or "")).replace("{path}", str(repo or ""))
            .replace("{engines_dir}", str(engines_dir)))


@router.post("/engines/{engine_id}/setup")
def setup_engine(engine_id: str, body: EngineSetupBody) -> Run:
    if not body.confirm:
        raise HTTPException(400, "setup commands run only after explicit confirmation: send {\"confirm\": true}")
    a = _adapter(engine_id)
    step = next((x for x in a.setup_steps() if x.get("id") == body.step), None)
    if step is None:
        raise HTTPException(404, f"engine {engine_id} has no setup step {body.step!r}")
    settings = get_settings()
    repo = Path(a.repo_path) if a.repo_path else None
    command = _substitute(str(step.get("command", "")), repo, settings.engines_dir)
    if not command.strip():
        raise HTTPException(400, "this setup step has no command")
    cwd_s = _substitute(str(step.get("cwd") or "."), repo, settings.engines_dir)
    cwd = Path(cwd_s).expanduser()
    if not cwd.is_absolute():
        cwd = (repo / cwd) if repo is not None and repo.exists() else (settings.engines_dir / cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    log_dir = settings.data_dir / "logs" / "setup"
    log_dir.mkdir(parents=True, exist_ok=True)
    run = Run(stage_id="", video_id="", kind="setup", state="running")
    log_path = log_dir / f"{engine_id}-{body.step}-{run.id}.log"
    run.log_path = str(log_path)
    with session_scope() as s:
        s.add(run)
    audit("engine.setup_started", actor="user", engine=engine_id, step=body.step, command=command, cwd=str(cwd))
    env = child_env(settings, (getattr(a, "manifest", {}) or {}).get("env") or {}, zero_cost=True,
                    paid_env_names=orch().paid_env_names())

    def worker() -> None:
        code = -1
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(f"$ {command}\n(cwd {cwd})\n\n")
            fh.flush()
            try:
                p = subprocess.Popen(command, shell=True, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, start_new_session=True)
                with session_scope() as s2:
                    r = s2.get(Run, run.id)
                    r.pid = p.pid
                    s2.add(r)
                assert p.stdout is not None
                for line in p.stdout:
                    fh.write(redact(line))
                    fh.flush()
                code = p.wait()
            except OSError as e:
                fh.write(f"could not run the command: {e}\n")
        with session_scope() as s2:
            r = s2.get(Run, run.id)
            r.exit_code, r.ended_at, r.state = code, now(), "done" if code == 0 else "failed"
            s2.add(r)
        audit("engine.setup_finished", actor="user", engine=engine_id, step=body.step, exit_code=code)

    threading.Thread(target=worker, name=f"setup-{engine_id}-{body.step}", daemon=True).start()
    return run


@router.get("/engines/{engine_id}/setup/{run_id}")
def setup_status(engine_id: str, run_id: str, s: Session = Depends(get_session)) -> dict:
    r = s.get(Run, run_id)
    if r is None or r.kind != "setup":
        raise HTTPException(404, "setup run not found")
    lines = []
    if r.log_path and Path(r.log_path).exists():
        lines = Path(r.log_path).read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
    return {"run": r, "lines": [redact(x) for x in lines]}


@router.post("/engines/onboard")
def onboard(body: OnboardBody) -> dict:
    try:
        from ..engines import onboarding  # agent B
    except ImportError:
        raise HTTPException(501, "engine onboarding is not available (spacestudio.engines.onboarding missing)") from None
    fn = getattr(onboarding, "start_onboarding", None) or getattr(onboarding, "start", None) \
        or getattr(onboarding, "onboard", None)
    if fn is None:
        raise HTTPException(501, "engine onboarding module has no start function")
    audit("engine.onboard_requested", actor="user", source=body.url_or_path)
    try:
        res = fn(body.url_or_path, get_settings())
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None
    job_id = res.get("job_id") if isinstance(res, dict) else res
    return {"job_id": str(job_id)}
