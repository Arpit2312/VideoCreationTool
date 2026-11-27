"""Path guard, child-process environment filter (zero-cost) and secret redaction (spec §10)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import workspace
from .platform_compat import WINDOWS_ENV
from .workspace import WorkspaceError

BASE_ALLOWLIST = (
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "SHELL", "TMPDIR",
) + WINDOWS_ENV
CLAUDE_AUTH_VARS = (
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "ANTHROPIC_BASE_URL",
)
CLAUDE_PREFIX = "CLAUDE_CODE_"
NETWORK_VARS = (
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
)


def _is_claude_var(name: str) -> bool:
    return name in CLAUDE_AUTH_VARS or name.startswith(CLAUDE_PREFIX)


def _provider_matches(name: str, provider: str) -> bool:
    """A paid env var belongs to an approved provider if it is named exactly, or starts with the provider
    name (upper-cased, '-' -> '_') followed by '_' (e.g. provider "elevenlabs" -> ELEVENLABS_API_KEY)."""
    p = provider.strip()
    if not p:
        return False
    if name == p:
        return True
    prefix = re.sub(r"[^A-Za-z0-9]", "_", p).upper() + "_"
    return name.upper().startswith(prefix)


def child_env(
    settings: Any,
    engine_env: Optional[Mapping[str, str]] = None,
    zero_cost: bool = True,
    approved_paid_providers: Iterable[str] = (),
    paid_env_names: Iterable[str] = (),
    base_env: Optional[Mapping[str, str]] = None,
    free_env_names: Iterable[str] = (),
) -> dict[str, str]:
    """Build the environment for a child process (Claude CLI, setup command).

    Starts from a minimal allowlist, adds Claude auth/proxy variables, the user's ``extra_env``, the
    ``allowed_free_keys`` / engines' ``cost.free_env`` keys and the engine's declared non-secret env. Paid provider keys (any engine's
    ``cost.paid_env``) are dropped in zero-cost mode; outside zero-cost only keys of approved providers pass.
    """
    src = dict(os.environ if base_env is None else base_env)
    paid = set(paid_env_names)
    approved = list(approved_paid_providers or [])
    env: dict[str, str] = {}
    for k, v in src.items():
        if k in BASE_ALLOWLIST or k in NETWORK_VARS or _is_claude_var(k):
            env[k] = v
    for k, v in (getattr(settings, "extra_env", None) or {}).items():
        env[str(k)] = str(v)
    free_keys = set(getattr(settings, "allowed_free_keys", None) or []) | set(free_env_names or [])
    for k in free_keys:
        if k in src:
            env[k] = src[k]
    for k, v in (engine_env or {}).items():
        env[str(k)] = str(v)
    if not zero_cost:
        for k in paid:
            if k in src and any(_provider_matches(k, p) for p in approved):
                env[k] = src[k]
    # Final filter: paid names never pass in zero-cost mode, nor outside it unless approved.
    for k in list(env):
        if k in paid and not _is_claude_var(k) and k not in free_keys:
            if zero_cost or not any(_provider_matches(k, p) for p in approved):
                env.pop(k, None)
    env.setdefault("PATH", os.defpath)
    return env


# --------------------------------------------------------------------------------------------- redaction
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|PRIVATE)", re.I)
MASK = "***"
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
]
_KV = re.compile(
    r"(?i)(\b[\w\-]*(?:api[_\-]?key|apikey|token|secret|password|passwd|access[_\-]?key|auth)[\w\-]*\b"
    r"[\"']?\s*[=:]\s*[\"']?)([^\s\"'&,;}]{4,})"
)
_BEARER = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-~+/]{8,}=*)")


def _secret_values(env: Optional[Mapping[str, str]] = None) -> list[str]:
    src = os.environ if env is None else env
    vals = [v for k, v in src.items() if _SECRET_NAME.search(k) and v and len(v) >= 6
            and k not in ("CLAUDE_CONFIG_DIR",) and not v.startswith("/")]
    return sorted(set(vals), key=len, reverse=True)


def redact(text: Any, env: Optional[Mapping[str, str]] = None) -> Any:
    """Mask secret values in a string. Non-strings are returned unchanged (see redact_obj)."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for v in _secret_values(env):
        if v in out:
            out = out.replace(v, MASK)
    for pat in _PATTERNS:
        out = pat.sub(MASK, out)
    out = _KV.sub(lambda m: m.group(1) + MASK, out)
    out = _BEARER.sub(lambda m: m.group(1) + MASK, out)
    return out


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: (MASK if isinstance(k, str) and _SECRET_NAME.search(k) and isinstance(v, str) and v
                    else redact_obj(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(x) for x in obj]
    return obj


# --------------------------------------------------------------------------------------------- path guard
class PathRefused(WorkspaceError):
    pass


def guard(ws: Path, rel: str) -> Path:
    """Resolve ``rel`` inside the workspace or raise PathRefused."""
    rel = (rel or "").strip()
    if "\x00" in rel:
        raise PathRefused("invalid path")
    if rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        raise PathRefused(f"absolute paths are not allowed: {rel}")
    try:
        return workspace.resolve(ws, rel or ".")
    except WorkspaceError as e:
        raise PathRefused(str(e)) from None
