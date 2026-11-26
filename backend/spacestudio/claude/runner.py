"""Spawn, stream and cancel ``claude -p`` (docs/PHASE2_SPEC.md §7).

Flags verified against ``claude --help`` (2.1.332): -p/--print, --output-format stream-json, --verbose,
--resume, --permission-mode, --allowedTools, --add-dir, --plugin-dir, --append-system-prompt,
--agents <json>, --model, --effort, --max-budget-usd.

Note: the CLI records the system prompt on a conversation's first request and reuses it on --resume
(``--system-prompt-snapshot`` default "on"), so the appended stage protocol must be stage-independent;
everything stage-specific goes in the user prompt.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..security import redact

STDOUT_LIMIT = 32 * 1024 * 1024  # stream-json lines can be large (tool results)


class ClaudeNotFound(RuntimeError):
    pass


@dataclass
class RunRequest:
    prompt: str
    cwd: Path
    lane: str
    log_path: Path
    resume_session: Optional[str] = None
    add_dirs: list[Path] = field(default_factory=list)
    plugin_dirs: list[Path] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    append_system_prompt: str = ""
    agents: Optional[dict] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    max_budget_usd: Optional[float] = None
    timeout_s: int = 90 * 60
    run_key: str = ""                 # additive: key used by cancel(); defaults to str(log_path)
    permission_mode: Optional[str] = None  # additive: overrides settings.claude_permission_mode


@dataclass
class RunResult:
    exit_code: int
    session_id: Optional[str]
    model: Optional[str]
    cost_usd: float
    is_error: bool
    result_text: str
    permission_denials: list
    log_path: Path
    cancelled: bool
    timed_out: bool
    pid: Optional[int] = None          # additive
    stderr_tail: str = ""              # additive, redacted
    got_result: bool = False           # additive: a `result` event was seen


def _summarise_tool_use(block: dict) -> str:
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    detail = ""
    for k in ("command", "file_path", "path", "pattern", "url", "description", "prompt", "subagent_type"):
        if isinstance(inp.get(k), str) and inp.get(k):
            detail = inp[k]
            break
    detail = " ".join(detail.split())
    if len(detail) > 160:
        detail = detail[:157] + "..."
    return redact(f"{name}: {detail}" if detail else name)


class ClaudeRunner:
    def __init__(self, settings: Any):
        self.settings = settings
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._loops: dict[str, asyncio.AbstractEventLoop] = {}

    # ------------------------------------------------------------------ info
    @property
    def claude_bin(self) -> str:
        return str(getattr(self.settings, "claude_bin", "claude") or "claude")

    def is_simulated(self) -> bool:
        return Path(self.claude_bin).name != "claude"

    def resolve_bin(self) -> str:
        b = self.claude_bin
        found = shutil.which(b) if os.sep not in b else (b if os.access(b, os.X_OK) else None)
        if not found:
            raise ClaudeNotFound(
                f"Claude Code CLI not found ({b!r}). Install it (npm install -g @anthropic-ai/claude-code), "
                "log in with `claude`, or set claude_bin in Settings."
            )
        return found

    # ------------------------------------------------------------------ command
    def build_command(self, req: RunRequest) -> list[str]:
        cmd = [self.resolve_bin(), "-p", req.prompt, "--output-format", "stream-json", "--verbose"]
        if req.resume_session:
            cmd += ["--resume", req.resume_session]
        mode = req.permission_mode or getattr(self.settings, "claude_permission_mode", "acceptEdits")
        if mode:
            cmd += ["--permission-mode", mode]
        if req.allowed_tools:
            cmd += ["--allowedTools", ",".join(req.allowed_tools)]
        for d in req.add_dirs:
            cmd += ["--add-dir", str(d)]
        for d in req.plugin_dirs:
            cmd += ["--plugin-dir", str(d)]
        if req.append_system_prompt:
            cmd += ["--append-system-prompt", req.append_system_prompt]
        if req.agents:
            cmd += ["--agents", json.dumps(req.agents)]
        if req.model:
            cmd += ["--model", req.model]
        if req.effort:
            cmd += ["--effort", req.effort]
        if req.max_budget_usd is not None:
            cmd += ["--max-budget-usd", f"{float(req.max_budget_usd):.2f}"]
        return cmd

    # ------------------------------------------------------------------ run
    async def run(self, req: RunRequest, on_event: Optional[Callable[[dict], None]] = None) -> RunResult:
        emit = on_event or (lambda _e: None)
        key = req.run_key or str(req.log_path)
        cmd = self.build_command(req)
        req.log_path.parent.mkdir(parents=True, exist_ok=True)
        Path(req.cwd).mkdir(parents=True, exist_ok=True)

        popen_kw: dict[str, Any] = {}
        if os.name == "posix":
            popen_kw["start_new_session"] = True
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(req.cwd), env={k: str(v) for k, v in req.env.items()},
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=STDOUT_LIMIT, **popen_kw,
        )
        self._procs[key] = proc
        self._loops[key] = asyncio.get_running_loop()
        self._cancelled.discard(key)
        _safe_emit(emit, {"kind": "spawned", "pid": proc.pid})

        state: dict[str, Any] = {"session_id": req.resume_session, "model": None, "cost": 0.0, "is_error": False,
                                 "result": "", "denials": [], "got_result": False}
        stderr_lines: list[str] = []
        log_fh = open(req.log_path, "a", encoding="utf-8")

        async def read_stdout() -> None:
            assert proc.stdout is not None
            while True:
                try:
                    raw = await proc.stdout.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    raw = await proc.stdout.read(STDOUT_LIMIT)
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line.strip():
                    continue
                log_fh.write(redact(line) + "\n")
                log_fh.flush()
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    _safe_emit(emit, {"kind": "text", "text": redact(line[:500])})
                    continue
                if isinstance(ev, dict):
                    self._handle_event(ev, state, emit)

        async def read_stderr() -> None:
            assert proc.stderr is not None
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                txt = redact(raw.decode("utf-8", errors="replace").rstrip("\n"))
                stderr_lines.append(txt)
                del stderr_lines[:-200]
                log_fh.write(json.dumps({"type": "stderr", "text": txt}) + "\n")
                log_fh.flush()

        timed_out = False
        readers = asyncio.gather(read_stdout(), read_stderr())
        try:
            await asyncio.wait_for(asyncio.shield(readers), timeout=req.timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            await self._terminate(proc)
        except asyncio.CancelledError:
            self._cancelled.add(key)
            await self._terminate(proc)
            raise
        finally:
            try:
                await asyncio.wait_for(readers, timeout=15)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                readers.cancel()
            try:
                exit_code = await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                await self._terminate(proc, grace=0)
                exit_code = await proc.wait()
            log_fh.close()
            self._procs.pop(key, None)
            self._loops.pop(key, None)

        cancelled = key in self._cancelled
        self._cancelled.discard(key)
        return RunResult(
            exit_code=exit_code if exit_code is not None else -1,
            session_id=state["session_id"], model=state["model"], cost_usd=float(state["cost"] or 0.0),
            is_error=bool(state["is_error"]) or timed_out or cancelled,
            result_text=redact(state["result"] or ""), permission_denials=state["denials"],
            log_path=req.log_path, cancelled=cancelled, timed_out=timed_out, pid=proc.pid,
            stderr_tail="\n".join(stderr_lines[-20:]), got_result=state["got_result"],
        )

    @staticmethod
    def _handle_event(ev: dict, state: dict, emit: Callable[[dict], None]) -> None:
        etype = ev.get("type")
        if etype == "system" and ev.get("subtype") == "init":
            state["session_id"] = ev.get("session_id") or state["session_id"]
            state["model"] = ev.get("model") or state["model"]
            _safe_emit(emit, {"kind": "init", "session_id": state["session_id"], "model": state["model"]})
        elif etype == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    _safe_emit(emit, {"kind": "activity", "tool": block.get("name"),
                                      "summary": _summarise_tool_use(block)})
                elif block.get("type") == "text" and block.get("text"):
                    _safe_emit(emit, {"kind": "text", "text": redact(str(block["text"])[:500])})
        elif etype == "result":
            state["got_result"] = True
            state["session_id"] = ev.get("session_id") or state["session_id"]
            try:
                state["cost"] = float(ev.get("total_cost_usd") or 0.0)
            except (TypeError, ValueError):
                state["cost"] = 0.0
            state["is_error"] = bool(ev.get("is_error")) or ev.get("subtype") not in (None, "success")
            state["result"] = ev.get("result") or ""
            denials = ev.get("permission_denials") or []
            state["denials"] = denials if isinstance(denials, list) else []
            _safe_emit(emit, {"kind": "result", "cost_usd": state["cost"], "is_error": state["is_error"],
                              "permission_denials": state["denials"]})

    # ------------------------------------------------------------------ cancel
    async def _terminate(self, proc: asyncio.subprocess.Process, grace: Optional[float] = None) -> None:
        if proc.returncode is not None:
            return
        grace = getattr(self.settings, "cancel_grace_s", 10.0) if grace is None else grace
        _signal_group(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while proc.returncode is None and time.monotonic() < deadline:
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                pass
        if proc.returncode is None:
            _signal_group(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))

    def cancel(self, run_key: str) -> None:
        """SIGTERM the run's process group now, SIGKILL after the grace period. Thread-safe."""
        proc = self._procs.get(run_key)
        if proc is None or proc.returncode is not None:
            return
        self._cancelled.add(run_key)
        _signal_group(proc.pid, signal.SIGTERM)
        grace = float(getattr(self.settings, "cancel_grace_s", 10.0))
        loop = self._loops.get(run_key)

        def _kill() -> None:
            if proc.returncode is None:
                _signal_group(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))

        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: loop.call_later(grace, _kill))

    def running_keys(self) -> list[str]:
        return [k for k, p in self._procs.items() if p.returncode is None]


def _signal_group(pid: int, sig: int) -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(pid), sig)
        else:  # pragma: no cover
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _safe_emit(emit: Callable[[dict], None], ev: dict) -> None:
    try:
        emit(ev)
    except Exception:  # a broken listener must not kill the run
        pass
