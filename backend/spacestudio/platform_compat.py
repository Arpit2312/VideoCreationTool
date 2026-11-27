"""Cross-platform helpers: process groups, killing process trees, file-lock slots, Git Bash.

SpaceStudio runs natively on Linux, macOS and Windows. On Windows the engines' shell commands run in
Git Bash (bundled with Git for Windows), which is also what Claude Code uses for its Bash tool there.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

IS_WINDOWS = os.name == "nt"

# Environment variables Windows programs (node, python, git, ffmpeg) need to start at all.
WINDOWS_ENV = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "USERNAME",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "HOMEDRIVE", "HOMEPATH", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
)


def new_process_group_kwargs() -> dict[str, Any]:
    """Popen / create_subprocess_exec kwargs that put the child in its own process group."""
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}
    return {"start_new_session": True}


def terminate_tree(pid: int, force: bool = False) -> None:
    """Stop a process and everything it started. Graceful first (force=False), then forced."""
    if not pid:
        return
    try:
        if IS_WINDOWS:
            args = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
            subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError, subprocess.SubprocessError):
        pass


@contextlib.contextmanager
def try_lock(path: Path) -> Iterator[bool]:
    """Non-blocking exclusive lock on a file; yields True if acquired. Released on exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    locked = False
    try:
        try:
            if IS_WINDOWS:
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            locked = False
        yield locked
    finally:
        if locked:
            try:
                if IS_WINDOWS:
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def find_bash() -> Optional[str]:
    """A bash for running engine shell scripts: PATH first, then Git for Windows' usual locations."""
    if not IS_WINDOWS:
        return shutil.which("bash") or shutil.which("sh")
    env_path = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if env_path and Path(env_path).is_file():
        return env_path
    candidates = []
    git = shutil.which("git")
    if git:  # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        root = Path(git).resolve().parent.parent
        candidates += [root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"]
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("bash")
    # C:\Windows\System32\bash.exe is the WSL launcher, not Git Bash
    if found and "system32" not in found.lower():
        return found
    return None


def shell_command(command: str) -> list[str]:
    """argv that runs a POSIX shell command line (setup steps, engine scripts) on any OS."""
    bash = find_bash()
    if bash:
        return [bash, "-lc", command] if IS_WINDOWS else [bash, "-c", command]
    if IS_WINDOWS:
        raise FileNotFoundError("Git Bash not found: install Git for Windows (https://git-scm.com/download/win) "
                                "or set CLAUDE_CODE_GIT_BASH_PATH to its bash.exe")
    return ["/bin/sh", "-c", command]


def shell_path(p: Any) -> str:
    """A path written the way both Git Bash and Windows programs accept it (C:/Users/...)."""
    return Path(p).as_posix() if IS_WINDOWS else str(p)


def default_python() -> str:
    """Interpreter name for engine scripts: python3 on POSIX; on Windows the Python running SpaceStudio's
    launcher is not the engines' one, so prefer the `py`/`python` on PATH (python3 there is often the
    Microsoft Store stub)."""
    if IS_WINDOWS:
        return "python"
    return "python3"


def slot_command() -> str:
    """How Claude invokes the render semaphore in shell commands, on any OS."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "spacestudio-slot"
    return f'"{shell_path(sys.executable)}" "{shell_path(script)}"'


_PATH_TOKEN_END = " \t\r\n\"'`<>|;,)("


def normalize_shell_paths(text: str, roots: list[Any]) -> str:
    """On Windows, rewrite absolute paths under the given roots to forward slashes (C:/a/b) inside prompt
    text, so commands Claude copies into Git Bash keep working. No-op elsewhere."""
    if not IS_WINDOWS or not text:
        return text
    prefixes = sorted({str(Path(r)) for r in roots if r}, key=len, reverse=True)
    out, i, n = [], 0, len(text)
    while i < n:
        hit = next((p for p in prefixes if text.startswith(p, i)), None)
        if hit is None:
            out.append(text[i])
            i += 1
            continue
        j = i + len(hit)
        while j < n and text[j] not in _PATH_TOKEN_END:
            j += 1
        out.append(text[i:j].replace("\\", "/"))
        i = j
    return "".join(out)


def windows_tool_aliases(tools: list[str]) -> list[str]:
    """Allowed-tool patterns written for POSIX (Bash(python3 *)) also allow the Windows spelling."""
    if not IS_WINDOWS:
        return tools
    extra = [t.replace("python3 ", "python ") for t in tools if "python3 " in t]
    return list(dict.fromkeys(tools + extra))
