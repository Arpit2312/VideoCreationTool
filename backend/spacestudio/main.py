"""App factory, static frontend serving, startup recovery and the `spacestudio serve` CLI."""
from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import init_db, session_scope

log = logging.getLogger("spacestudio")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(registry_provider: Optional[Callable[[], Any]] = None, start_scheduler: bool = True,
               frontend_dist: Optional[Path] = None) -> FastAPI:
    from .api import api_router
    from .api.engines import sync_engines
    from .workflow.orchestrator import Orchestrator, set_orchestrator
    from .workflow.scheduler import Scheduler, set_scheduler

    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        orch = Orchestrator(settings, registry_provider=registry_provider)
        set_orchestrator(orch)
        app.state.orchestrator = orch
        reg = orch.registry_or_none()
        try:
            with session_scope() as s:
                sync_engines(s, reg)
        except Exception:
            log.exception("could not sync engines")
        recovered = orch.recover()
        if recovered:
            log.info("recovered %d interrupted stage(s); they resume with --resume", len(recovered))
        sch = Scheduler(orch)
        set_scheduler(sch)
        app.state.scheduler = sch
        if start_scheduler:
            await sch.start()
        try:
            yield
        finally:
            await sch.stop()
            set_scheduler(None)

    app = FastAPI(title="SpaceStudio", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.include_router(api_router)

    dist = frontend_dist or FRONTEND_DIST
    if (dist / "index.html").exists():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404, "not found")
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    return app


def cli(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="spacestudio", description="SpaceStudio local video studio")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="run the web app (default)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8000)
    uvicorn.run("spacestudio.main:create_app", factory=True, host=host, port=port,
                reload=getattr(args, "reload", False))


if __name__ == "__main__":  # pragma: no cover
    cli()
