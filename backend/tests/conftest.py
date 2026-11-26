"""Shared fixtures for agent A's tests: isolated data dir, fake Claude, stub engine registry."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

TESTS = Path(__file__).resolve().parent
FAKE_CLAUDE = TESTS / "fake_claude.py"
sys.path.insert(0, str(TESTS))


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


HAS_PIPELINE = has_module("spacestudio.assembly.pipeline") and has_module("spacestudio.qa.checks")
HAS_REGISTRY = has_module("spacestudio.engines.registry")


# ------------------------------------------------------------------ stub engines (tests only)
from spacestudio.engines.base import DetectedStage, EngineAdapter, EngineHealth, HealthItem, LaunchSpec  # noqa: E402
from spacestudio.workflow.stages import StageSpec  # noqa: E402
from spacestudio import workspace  # noqa: E402


class StubAdapter(EngineAdapter):
    def __init__(self, eid: str, caps: list[str], native: Optional[list[StageSpec]] = None,
                 paid_env: Optional[list[str]] = None, cost_usd: float = 0.0):
        manifest = {"id": eid, "name": eid.title(), "description": f"stub {eid}", "license": "MIT",
                    "capabilities": {c: {"supported": True, "detail": f"stub {c}"} for c in caps},
                    "options": {"style": [{"id": "plain", "name": "Plain", "zero_cost": True}]},
                    "cost": {"zero_cost": True, "paid_env": paid_env or []},
                    "setup": [{"id": "hello", "title": "Say hello", "command": "echo hello-from-setup", "cwd": "."}]}
        super().__init__(manifest, None)
        self._native = native or []
        self.cost_usd = cost_usd

    def health(self, zero_cost: bool = True) -> EngineHealth:
        return EngineHealth(self.id, True, True, [HealthItem("stub", "Stub", "ok", "stub engine")])

    def launch_spec(self, ws: Path, stage: StageSpec, config: dict) -> LaunchSpec:
        return LaunchSpec(cwd=workspace.engine_dir(ws, self.id), env={"STUB_ENGINE": self.id},
                          allowed_tools=["Read", "Write"])

    def native_stages(self, config: dict) -> list[StageSpec]:
        return [StageSpec(**{**s.__dict__}) for s in self._native]

    def stage_prompt(self, stage: StageSpec, ws: Path, config: dict, feedback=None, resume=False) -> str:
        return (f"Stub engine {self.id}: run stage {stage.key}.\nparams: {json.dumps(stage.params)}"
                + (f"\nCHANGE REQUEST: {feedback}" if feedback else ""))

    def detect(self, ws: Path, stage: StageSpec) -> DetectedStage:
        missing = [o for o in stage.outputs if "*" not in o and not (Path(ws) / o).exists()]
        if missing:
            return DetectedStage(stage.key, "unknown", detail="missing " + ", ".join(missing))
        return DetectedStage(stage.key, "completed", outputs=[o for o in stage.outputs if "*" not in o],
                             cost_usd=self.cost_usd)


class StubRegistry:
    def __init__(self, adapters: list[EngineAdapter]):
        self._a = {a.id: a for a in adapters}

    def load(self) -> None:
        pass

    def all(self) -> list[EngineAdapter]:
        return list(self._a.values())

    def get(self, eid: str) -> EngineAdapter:
        return self._a[eid]

    def manifests(self) -> list[dict]:
        return [a.manifest for a in self._a.values()]

    def paid_env_names(self) -> set[str]:
        return {n for a in self._a.values() for n in a.manifest["cost"]["paid_env"]}


ALL_CAPS = ["asset_acquisition", "narration", "scene_render"]


def stub_registry() -> StubRegistry:
    native = [StageSpec("film", "Film", "native film", "native_pipeline", owner="cinematic-skill",
                        lane="engine:cinematic-skill", outputs=["renders/final.mp4"])]
    return StubRegistry([StubAdapter("cinematic-skill", ALL_CAPS, native=native, paid_env=["PAID_VOICE_KEY"]),
                         StubAdapter("lemo-opuscar", ALL_CAPS),
                         StubAdapter("openmontage", ["scene_render"])])


# ------------------------------------------------------------------ environment
@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("SPACESTUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("SPACESTUDIO_CLAUDE_BIN", str(FAKE_CLAUDE))
    monkeypatch.setenv("SPACESTUDIO_MIN_FREE_RAM_GB", "0")
    monkeypatch.setenv("SPACESTUDIO_SCHEDULER_INTERVAL_S", "0.2")
    monkeypatch.setenv("SPACESTUDIO_CANCEL_GRACE_S", "2")
    monkeypatch.setenv("SPACESTUDIO_ENGINES_DIR", str(tmp_path / "engines"))
    for k in ("FAKE_CLAUDE_NEEDS_INPUT", "FAKE_CLAUDE_FAIL", "FAKE_CLAUDE_SLEEP", "FAKE_CLAUDE_SLEEP_STAGE"):
        monkeypatch.delenv(k, raising=False)
    from spacestudio import config, db
    from spacestudio.workflow import orchestrator, scheduler

    config.reload_settings()
    db.reset_engine()
    db.init_db()
    orchestrator.set_orchestrator(None)
    scheduler.set_scheduler(None)
    yield config.get_settings()
    db.reset_engine()
    orchestrator.set_orchestrator(None)
    config.reload_settings()


SMALL_CONFIG = {"duration_seconds": 4, "resolution": "320x180", "aspect_ratio": "16:9", "fps": 12,
                "music": False, "sfx": False, "approval_mode": "manual"}


@pytest.fixture()
def make_client(env):
    from fastapi.testclient import TestClient

    from spacestudio.main import create_app

    clients = []

    def _make(registry: Any = None) -> TestClient:
        reg = registry or stub_registry()
        app = create_app(registry_provider=lambda: reg, frontend_dist=Path("/nonexistent"))
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def wait_for(fn: Callable[[], Any], timeout: float = 60.0, interval: float = 0.15, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}; last={last!r}")


def stage_of(client, vid: str, key: str) -> dict:
    d = client.get(f"/api/videos/{vid}").json()
    return next(s for s in d["stages"] if s["key"] == key)


def wait_stage(client, vid: str, key: str, statuses: set[str], timeout: float = 60.0) -> dict:
    def check():
        st = stage_of(client, vid, key)
        if st["status"] == "failed" and "failed" not in statuses:
            raise AssertionError(f"stage {key} failed: {st['error']}")
        return st if st["status"] in statuses else None
    return wait_for(check, timeout, what=f"{key} in {statuses}")


def fake_env(monkeypatch, **variables: str) -> None:
    """Pass FAKE_CLAUDE_* knobs to the child: the runner's env allowlist drops unknown vars, so they go
    through settings.extra_env. Call before make_client()."""
    import json as _json

    from spacestudio import config

    monkeypatch.setenv("SPACESTUDIO_EXTRA_ENV", _json.dumps(variables))
    config.reload_settings()
