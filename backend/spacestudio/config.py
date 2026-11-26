"""Application settings (docs/BUILD_BRIEF.md "Settings").

Precedence (highest first): environment variables ``SPACESTUDIO_<FIELD>`` > ``<data_dir>/settings.json`` >
defaults. ``data_dir`` itself only comes from ``SPACESTUDIO_DATA_DIR`` (or the default), because it decides
where ``settings.json`` lives.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

ENV_PREFIX = "SPACESTUDIO_"


def _default_data_dir() -> Path:
    return Path("~/.spacestudio/data").expanduser()


class Settings(BaseModel):
    # paths and identity
    app_name: str = "SpaceStudio"
    data_dir: Path = Field(default_factory=_default_data_dir)
    db_url: str = ""                                  # "" -> sqlite:///<data_dir>/spacestudio.db
    engines_dir: Path = Field(default_factory=lambda: Path("~/.spacestudio/engines").expanduser())
    engine_paths: dict[str, str] = Field(default_factory=dict)
    # binaries
    claude_bin: str = "claude"
    claude_permission_mode: str = "acceptEdits"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    python_bin: str = "python3"
    # limits
    max_claude_sessions: int = 2
    max_render_jobs: int = 1
    render_workers: int = 2
    min_free_ram_gb: float = 2.0
    stage_timeout_minutes: int = 90
    # defaults and environment
    default_video_config: dict[str, Any] = Field(default_factory=dict)
    zero_cost_default: bool = True
    allowed_free_keys: list[str] = Field(default_factory=list)
    extra_env: dict[str, str] = Field(default_factory=dict)
    # additive (agent A)
    scheduler_interval_s: float = 1.0
    ideas_timeout_s: int = 90
    cancel_grace_s: float = 10.0

    @field_validator("data_dir", "engines_dir", mode="before")
    @classmethod
    def _expand(cls, v: Any) -> Any:
        return Path(v).expanduser() if isinstance(v, (str, Path)) else v

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def effective_db_url(self) -> str:
        return self.db_url or f"sqlite:///{self.data_dir / 'spacestudio.db'}"

    @property
    def slots_dir(self) -> Path:
        return self.data_dir / "slots"

    def engine_path(self, engine_id: str, default_dir: Optional[str] = None) -> Path:
        if engine_id in self.engine_paths:
            return Path(self.engine_paths[engine_id]).expanduser()
        return self.engines_dir / (default_dir or engine_id)

    def public_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d["projects_dir"] = str(self.projects_dir)
        d["db_url"] = self.effective_db_url
        return d


# Fields that may not be changed through the API / settings.json (decided by the environment).
_ENV_ONLY = {"data_dir"}

_lock = threading.Lock()
_cached: Optional[Settings] = None


def _coerce_env(name: str, raw: str) -> Any:
    field = Settings.model_fields[name]
    ann = str(field.annotation)
    if "dict" in ann or "list" in ann:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            if "list" in ann:
                return [x.strip() for x in raw.split(",") if x.strip()]
            raise ValueError(f"{ENV_PREFIX}{name.upper()} must be JSON") from None
    return raw  # pydantic coerces str -> int/float/bool/Path


def _env_overrides() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in Settings.model_fields:
        raw = os.environ.get(ENV_PREFIX + name.upper())
        if raw is not None and raw != "":
            out[name] = _coerce_env(name, raw)
    return out


def load_settings() -> Settings:
    env = _env_overrides()
    data_dir = Path(env.get("data_dir") or _default_data_dir()).expanduser()
    file_values: dict[str, Any] = {}
    f = data_dir / "settings.json"
    if f.exists():
        try:
            file_values = json.loads(f.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            file_values = {}
    file_values = {k: v for k, v in file_values.items() if k in Settings.model_fields and k not in _ENV_ONLY}
    merged = {**file_values, **env, "data_dir": data_dir}
    return Settings(**merged)


def get_settings() -> Settings:
    global _cached
    with _lock:
        if _cached is None:
            _cached = load_settings()
            _cached.data_dir.mkdir(parents=True, exist_ok=True)
        return _cached


def reload_settings() -> Settings:
    global _cached
    with _lock:
        _cached = None
    return get_settings()


def save_settings(update: dict[str, Any]) -> Settings:
    """Persist the given fields to settings.json (env vars still win on the next load)."""
    current = get_settings()
    unknown = [k for k in update if k not in Settings.model_fields]
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
    blocked = [k for k in update if k in _ENV_ONLY]
    if blocked:
        raise ValueError(f"these settings can only be set through the environment: {', '.join(blocked)}")
    # validate the merged result before writing anything
    Settings(**{**current.model_dump(), **update})
    f = current.settings_file
    existing: dict[str, Any] = {}
    if f.exists():
        try:
            existing = json.loads(f.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.update(json.loads(json.dumps(update, default=str)))
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    tmp.replace(f)
    return reload_settings()
