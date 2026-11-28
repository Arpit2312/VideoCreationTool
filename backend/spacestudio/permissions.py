"""Turn Claude Code permission denials into rules the user can approve for one video.

In headless (-p) mode Claude cannot ask "may I run curl?": the call is denied and listed in the result's
`permission_denials`. SpaceStudio shows those to the user with a suggested rule per command; approved rules are
stored in the video's config (extra_allowed_tools) and passed with --allowedTools on the next run.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Rules a user may approve. Blanket rules (Bash, Bash(*)) are never offered or accepted.
_RULE = re.compile(r"^(Bash\((?!\*\))[^()\n]{1,200}\)|WebFetch(\(domain:[A-Za-z0-9.-]{1,253}\))?|WebSearch)$")
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")
_ENV_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")


def is_valid_rule(rule: str) -> bool:
    return bool(_RULE.match(rule or ""))


def _words(command: str) -> list[str]:
    out = []
    for part in _SPLIT.split(command or ""):
        part = _ENV_PREFIX.sub("", part.strip().lstrip("("))
        if not part:
            continue
        word = part.split()[0].strip("'\"")
        if word and re.match(r"^[\w./:\\-]+$", word):
            out.append(word)
    return out


def _covered(word: str, allowed: Iterable[str]) -> bool:
    for a in allowed:
        m = re.match(r"^Bash\((\S+) \*\)$", a)
        if m and (m.group(1) == word or m.group(1).rsplit("/", 1)[-1] == word.rsplit("/", 1)[-1]):
            return True
    return False


def summarize(denials: Any, allowed: Iterable[str]) -> list[dict]:
    """[{tool, command, rules: [suggested rules]}] for the UI, newest first, deduplicated."""
    allowed = list(allowed)
    out, seen = [], set()
    for d in denials or []:
        if not isinstance(d, dict):
            continue
        tool = str(d.get("tool_name") or d.get("tool") or "")
        inp = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
        command = str(inp.get("command") or inp.get("url") or "")[:300]
        rules: list[str] = []
        if tool == "Bash":
            rules = [f"Bash({w} *)" for w in dict.fromkeys(_words(command)) if not _covered(w, allowed)]
        elif tool == "WebFetch":
            m = re.match(r"https?://([^/:]+)", command)
            rules = [f"WebFetch(domain:{m.group(1)})"] if m else ["WebFetch"]
        elif tool == "WebSearch":
            rules = ["WebSearch"]
        elif tool == "PowerShell":
            rules = []  # SpaceStudio runs engine commands in Git Bash; PowerShell is turned off instead
        key = (tool, command)
        if key in seen:
            continue
        seen.add(key)
        out.append({"tool": tool, "command": command, "rules": [r for r in rules if is_valid_rule(r)]})
    return out[:20]
