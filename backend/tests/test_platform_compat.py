"""Cross-platform helpers. Windows-only branches are exercised on the Windows CI runner; the pure
string helpers are also tested here by forcing IS_WINDOWS."""
import subprocess
import sys
import time
from pathlib import Path, PureWindowsPath

import pytest

from spacestudio import platform_compat as pc


def test_try_lock_is_exclusive_across_processes(tmp_path):
    lock = tmp_path / "render.0.lock"
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys,time; sys.path.insert(0, %r)\n"
        "from spacestudio.platform_compat import try_lock\n"
        "from pathlib import Path\n"
        "with try_lock(Path(%r)) as got:\n"
        "    print('held' if got else 'busy', flush=True); time.sleep(3)\n") % (str(Path(__file__).parents[1]), str(lock))],
        stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    with pc.try_lock(lock) as got:
        assert got is False
    holder.wait(timeout=10)
    with pc.try_lock(lock) as got:
        assert got is True


def test_slot_script_runs_command_and_returns_its_exit_code(tmp_path, monkeypatch):
    script = Path(__file__).parents[2] / "scripts" / "spacestudio_slot.py"
    env = {"SPACESTUDIO_DATA_DIR": str(tmp_path), "SPACESTUDIO_RENDER_SLOTS": "1", "PATH": "/usr/bin:/bin",
           "SYSTEMROOT": "C:\\Windows"}
    r = subprocess.run([sys.executable, str(script), "render", "--", sys.executable, "-c", "raise SystemExit(7)"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 7, r.stderr


def test_normalize_shell_paths_on_windows(monkeypatch):
    monkeypatch.setattr(pc, "IS_WINDOWS", True)
    monkeypatch.setattr(pc, "Path", PureWindowsPath)
    ws = r"C:\Users\DELL\.spacestudio\data\projects\v_1"
    text = f'run `cd {ws}\\work && ffmpeg -i "{ws}\\scenes\\s1.mp4" out.mp4` then read {ws}\\brief.json.'
    out = pc.normalize_shell_paths(text, [ws])
    assert "C:/Users/DELL/.spacestudio/data/projects/v_1/work" in out
    assert '"C:/Users/DELL/.spacestudio/data/projects/v_1/scenes/s1.mp4"' in out
    assert "C:/Users/DELL/.spacestudio/data/projects/v_1/brief.json." in out
    assert "\\" not in out


def test_normalize_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr(pc, "IS_WINDOWS", False)
    assert pc.normalize_shell_paths(r"C:\a\b", [r"C:\a"]) == r"C:\a\b"


def test_windows_tool_aliases(monkeypatch):
    monkeypatch.setattr(pc, "IS_WINDOWS", True)
    assert pc.windows_tool_aliases(["Bash(python3 *)", "Read"]) == ["Bash(python3 *)", "Read", "Bash(python *)"]


def test_terminate_tree_stops_children(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **pc.new_process_group_kwargs())
    pc.terminate_tree(p.pid)
    t0 = time.time()
    while p.poll() is None and time.time() - t0 < 10:
        time.sleep(0.1)
    if p.poll() is None:
        pc.terminate_tree(p.pid, force=True)
    assert p.wait(timeout=10) is not None


def test_shell_command_uses_a_shell():
    argv = pc.shell_command("echo hi")
    assert argv[-1] == "echo hi" and len(argv) == 3
    assert subprocess.run(argv, capture_output=True, text=True).stdout.strip() == "hi"
