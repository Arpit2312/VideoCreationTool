#!/usr/bin/env python3
"""spacestudio-slot: render semaphore (docs/PHASE2_SPEC.md §8). Works on Linux, macOS and Windows.

Usage: spacestudio-slot render [--timeout SECONDS] -- <command> [args...]

Acquires one of N exclusive file locks (fcntl, or msvcrt on Windows) <data_dir>/slots/render.<i>.lock (N = $SPACESTUDIO_RENDER_SLOTS,
else max_render_jobs from <data_dir>/settings.json, else 1), runs the command while holding it and exits
with the command's exit code. data_dir = $SPACESTUDIO_DATA_DIR or ~/.spacestudio/data.
Standalone on purpose (stdlib only): Claude runs it from engine directories.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _data_dir() -> Path:
    return Path(os.environ.get("SPACESTUDIO_DATA_DIR") or "~/.spacestudio/data").expanduser()


def _slots(data_dir: Path) -> int:
    env = os.environ.get("SPACESTUDIO_RENDER_SLOTS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    try:
        return max(1, int(json.loads((data_dir / "settings.json").read_text()).get("max_render_jobs", 1)))
    except (OSError, ValueError, TypeError, AttributeError):
        return 1


def _try_lock(path: Path):
    """Open and exclusively lock a file without blocking; the open handle, or None if it is taken."""
    fh = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def _unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    fh.close()


def main(argv: list) -> int:
    if len(argv) < 1 or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    if argv[0] != "render":
        print(f"spacestudio-slot: unknown slot kind {argv[0]!r} (only 'render')", file=sys.stderr)
        return 2
    rest = argv[1:]
    timeout = 6 * 3600.0
    if rest[:1] == ["--timeout"] and len(rest) >= 2:
        timeout, rest = float(rest[1]), rest[2:]
    if rest[:1] != ["--"] or len(rest) < 2:
        print("usage: spacestudio-slot render -- <command> [args...]", file=sys.stderr)
        return 2
    cmd = rest[1:]
    data_dir = _data_dir()
    d = data_dir / "slots"
    d.mkdir(parents=True, exist_ok=True)
    n = _slots(data_dir)
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        for i in range(n):
            fh = _try_lock(d / f"render.{i}.lock")
            if fh is None:
                continue
            try:
                (d / f"render.{i}.info").write_text(f"{os.getpid()} {' '.join(cmd)[:200]}\n")
                try:
                    proc = subprocess.Popen(cmd)
                except OSError as e:
                    print(f"spacestudio-slot: cannot run {cmd[0]}: {e}", file=sys.stderr)
                    return 127
                try:
                    return proc.wait()
                except KeyboardInterrupt:
                    proc.terminate()
                    return proc.wait()
            finally:
                _unlock(fh)
        if not announced:
            print(f"spacestudio-slot: waiting for a render slot ({n} in use)", file=sys.stderr, flush=True)
            announced = True
        if time.monotonic() > deadline:
            print("spacestudio-slot: timed out waiting for a render slot", file=sys.stderr)
            return 75
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
