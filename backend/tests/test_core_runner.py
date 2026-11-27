"""Claude runner with the fake CLI: streaming, session/cost parsing, cancel, timeout, missing binary."""
import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from spacestudio.claude.agents import agent_defs
from spacestudio.claude.prompts import stage_header, stage_protocol
from spacestudio.claude.runner import ClaudeNotFound, ClaudeRunner, RunRequest
from conftest import FAKE_CLAUDE


def settings(**kw):
    base = dict(claude_bin=str(FAKE_CLAUDE), claude_permission_mode="acceptEdits", cancel_grace_s=2.0)
    base.update(kw)
    return SimpleNamespace(**base)


def make_ws(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".spacestudio").mkdir(parents=True)
    (ws / "project.json").write_text(json.dumps({"id": "v_1", "title": "Moon", "idea": "moon", "config": {}}))
    return ws


def req(ws, key="research", **kw):
    base = dict(prompt=stage_header(key, "v_1", ws, "main") + "\nDo it.", cwd=ws, lane="main",
                log_path=ws / "logs" / f"{key}.1.jsonl", env=dict(os.environ), allowed_tools=["Read", "Write"],
                append_system_prompt=stage_protocol(True), agents=agent_defs(), model="sonnet", effort="high",
                max_budget_usd=1.5, timeout_s=60, add_dirs=[ws], plugin_dirs=[ws])
    base.update(kw)
    return RunRequest(**base)


def test_build_command_uses_verified_flags(tmp_path):
    r = ClaudeRunner(settings())
    cmd = r.build_command(req(make_ws(tmp_path), resume_session="abc"))
    for flag in ("-p", "--output-format", "--verbose", "--resume", "--permission-mode", "--allowedTools",
                 "--add-dir", "--plugin-dir", "--append-system-prompt-file", "--agents", "--model", "--effort",
                 "--max-budget-usd"):
        assert flag in cmd, flag
    # the prompt travels on stdin, long texts in side files: nothing near the Windows command-line limit
    assert not any(a.startswith("SPACESTUDIO_STAGE=") for a in cmd)
    assert sum(len(a) for a in cmd) < 8000
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert Path(cmd[cmd.index("--append-system-prompt-file") + 1]).read_text().strip()
    agents = json.loads(Path(cmd[cmd.index("--agents") + 1]).read_text())
    assert {"researcher", "fact-checker", "scriptwriter", "storyboard-artist", "asset-librarian",
            "voice-director", "qa-reviewer"} <= set(agents)
    assert all("description" in a and "prompt" in a for a in agents.values())


async def test_run_streams_and_parses(tmp_path):
    ws = make_ws(tmp_path)
    events = []
    r = ClaudeRunner(settings())
    assert r.is_simulated()
    res = await r.run(req(ws), events.append)
    assert res.exit_code == 0 and not res.is_error and res.got_result
    assert res.session_id and res.model == "sonnet" and res.cost_usd == 0.0
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "spawned" and "init" in kinds and "activity" in kinds and kinds[-1] == "result"
    lines = res.log_path.read_text().splitlines()
    assert json.loads(lines[0])["type"] == "system"
    assert (ws / "research" / "research.json").exists()
    evs = [json.loads(x) for x in (ws / ".spacestudio" / "events.jsonl").read_text().splitlines()]
    assert evs[-1]["type"] == "stage_completed" and evs[-1]["stage"] == "research"
    # resume keeps the session id
    res2 = await r.run(req(ws, resume_session=res.session_id, log_path=ws / "logs" / "r2.jsonl"))
    assert res2.session_id == res.session_id


async def test_cancel_terminates_process_group(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    ws = make_ws(tmp_path)
    r = ClaudeRunner(settings())
    pids = []
    task = asyncio.create_task(r.run(req(ws, run_key="k1", env=dict(os.environ)),
                                     lambda e: pids.append(e.get("pid")) if e["kind"] == "spawned" else None))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if pids and "k1" in r.running_keys():
            break
    t0 = time.monotonic()
    r.cancel("k1")
    res = await task
    assert res.cancelled and res.is_error and time.monotonic() - t0 < 10
    assert not psutil.pid_exists(pids[0]) or psutil.Process(pids[0]).status() == psutil.STATUS_ZOMBIE


async def test_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "30")
    ws = make_ws(tmp_path)
    r = ClaudeRunner(settings())
    t0 = time.monotonic()
    res = await r.run(req(ws, timeout_s=1, env=dict(os.environ)))
    assert res.timed_out and res.is_error and not res.got_result
    assert time.monotonic() - t0 < 10


async def test_missing_binary(tmp_path):
    r = ClaudeRunner(settings(claude_bin="/nonexistent/claude"))
    assert not r.is_simulated()
    with pytest.raises(ClaudeNotFound, match="not found"):
        await r.run(req(make_ws(tmp_path)))


async def test_stderr_is_redacted(tmp_path):
    # a .py fake runs through the current Python on every OS (a #!/bin/sh script cannot run on Windows)
    script = tmp_path / "noisy_claude.py"
    script.write_text("import sys\nsys.stdin.read()\nprint('error: api_key=supersecret123 failed', file=sys.stderr)\n"
                      "print('{\"type\":\"result\",\"is_error\":true,\"subtype\":\"error\",\"total_cost_usd\":0.01}')\n")
    ws = make_ws(tmp_path)
    res = await ClaudeRunner(settings(claude_bin=str(script))).run(req(ws))
    log = res.log_path.read_text()
    assert "supersecret123" not in log and "api_key=***" in log
    assert res.is_error and res.cost_usd == 0.01


def test_zero_claude_budget_means_no_cap(tmp_path):
    """Claude Code rejects --max-budget-usd 0.00 ('must be a number greater than 0'); 0 means no cap."""
    r = ClaudeRunner(settings())
    ws = make_ws(tmp_path)
    for v in (0, 0.0, -1):
        assert "--max-budget-usd" not in r.build_command(req(ws, max_budget_usd=v))
    cmd = r.build_command(req(ws, max_budget_usd=1.5))
    assert cmd[cmd.index("--max-budget-usd") + 1] == "1.50"
