"""Engine registry: loads engine manifests and picks each engine's adapter class.

Sources (BUILD_BRIEF "Engine registry"):
- built-in manifests: <repo root>/engines/*/engine.yaml (approved)
- onboarded manifests: <data_dir>/engines/*/engine.yaml, drafted by the onboarding job; their
  ``approved`` flag stays false until the user approves them (onboarding.approve_manifest).

Repo path per engine = settings.engine_paths[id] or settings.engines_dir / repo.default_dir.
Adapter = manifest ``adapter``: ``generic`` or a dotted path. Only ``spacestudio.engines.*`` classes are
importable; onboarded manifests always get GenericAdapter (a drafted manifest never selects code).
"""
from __future__ import annotations

import importlib
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from .base import EngineAdapter
from .generic import GenericAdapter

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILTIN_DIR = REPO_ROOT / "engines"
_ALLOWED_PREFIX = "spacestudio.engines."


def _get(settings: Any, name: str, default: Any = None) -> Any:
    if isinstance(settings, dict):
        return settings.get(name, default)
    return getattr(settings, name, default)


def resolve_adapter_class(spec: Optional[str], onboarded: bool = False) -> type[EngineAdapter]:
    if onboarded or not spec or spec == "generic":
        return GenericAdapter
    if not spec.startswith(_ALLOWED_PREFIX):
        raise ValueError(f"adapter {spec!r} is not a spacestudio.engines class")
    mod, _, cls = spec.rpartition(".")
    klass = getattr(importlib.import_module(mod), cls)
    if not (isinstance(klass, type) and issubclass(klass, EngineAdapter)):
        raise TypeError(f"{spec} is not an EngineAdapter")
    return klass


class Registry:
    def __init__(self, settings: Any = None, builtin_dir: Optional[Path] = None,
                 onboarded_dir: Optional[Path] = None):
        if settings is None:
            from ..config import get_settings

            settings = get_settings()
        self.settings = settings
        self.builtin_dir = Path(builtin_dir) if builtin_dir else BUILTIN_DIR
        data_dir = _get(settings, "data_dir")
        self.onboarded_dir = Path(onboarded_dir) if onboarded_dir else (
            Path(data_dir).expanduser() / "engines" if data_dir else None)
        self._adapters: dict[str, EngineAdapter] = {}
        self._meta: dict[str, dict] = {}
        self.errors: list[dict[str, str]] = []

    # ---- loading -------------------------------------------------------------------------------
    def repo_path_for(self, manifest: dict) -> Path:
        eid = manifest["id"]
        paths = _get(self.settings, "engine_paths", {}) or {}
        if eid in paths and paths[eid]:
            return Path(paths[eid]).expanduser()
        engines_dir = Path(_get(self.settings, "engines_dir", "~/.spacestudio/engines")).expanduser()
        default_dir = (manifest.get("repo") or {}).get("default_dir") or eid
        return engines_dir / default_dir

    def _load_file(self, f: Path, source: str) -> None:
        try:
            manifest = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or not manifest.get("id"):
                raise ValueError("manifest has no id")
            eid = str(manifest["id"])
            if eid in self._adapters:
                raise ValueError(f"engine id {eid!r} is already registered (built-in engines win)")
            onboarded = source == "onboarded"
            manifest["approved"] = bool(manifest.get("approved", False)) if onboarded else True
            klass = resolve_adapter_class(manifest.get("adapter"), onboarded=onboarded)
            adapter = klass(manifest, self.repo_path_for(manifest), self.settings)
            self._adapters[eid] = adapter
            self._meta[eid] = {"source": source, "manifest_path": str(f), "adapter_class":
                               f"{klass.__module__}.{klass.__name__}"}
        except Exception as e:
            self.errors.append({"path": str(f), "error": f"{type(e).__name__}: {e}"})

    def load(self) -> None:
        self._adapters.clear()
        self._meta.clear()
        self.errors = []
        for f in sorted(self.builtin_dir.glob("*/engine.yaml")):
            self._load_file(f, "builtin")
        if self.onboarded_dir and self.onboarded_dir.is_dir():
            for f in sorted(self.onboarded_dir.glob("*/engine.yaml")):
                self._load_file(f, "onboarded")

    # ---- queries -------------------------------------------------------------------------------
    def all(self) -> list[EngineAdapter]:
        return list(self._adapters.values())

    def get(self, engine_id: str) -> EngineAdapter:
        if engine_id not in self._adapters:
            raise KeyError(engine_id)
        return self._adapters[engine_id]

    def manifests(self) -> list[dict]:
        out = []
        for eid, a in self._adapters.items():
            m = dict(a.manifest)
            m.update(self._meta.get(eid, {}))
            m["repo_path"] = str(a.repo_path) if a.repo_path else None
            m["approved"] = bool(a.manifest.get("approved", True))
            m["options"] = a.options()
            out.append(m)
        return out

    def paid_env_names(self) -> set[str]:
        names: set[str] = set()
        for a in self._adapters.values():
            names.update(str(x) for x in ((a.manifest.get("cost") or {}).get("paid_env") or []))
        return names

    def free_env_names(self) -> set[str]:
        """Additive: free developer keys (e.g. PEXELS_API_KEY) that engines declare as cost.free_env."""
        names: set[str] = set()
        for a in self._adapters.values():
            names.update(str(x) for x in ((a.manifest.get("cost") or {}).get("free_env") or []))
        return names


_lock = threading.Lock()
_registry: Optional[Registry] = None


def get_registry() -> Registry:
    global _registry
    with _lock:
        if _registry is None:
            r = Registry()
            r.load()
            _registry = r
        return _registry


def reset_registry() -> None:
    """Drop the cached registry (after settings change, onboarding approval, ...)."""
    global _registry
    with _lock:
        _registry = None
