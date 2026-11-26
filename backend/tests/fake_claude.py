#!/usr/bin/env python3
"""Fake `claude` CLI for tests and the SIMULATED mode. Stdlib only.

Accepts the flags SpaceStudio passes (see claude/runner.py), prints stream-json (system init, assistant
tool_use events, result with total_cost_usd 0), parses the SPACESTUDIO_STAGE line and writes VALID outputs
for each stage, then appends stage_completed to <WORKSPACE>/.spacestudio/events.jsonl.

Env knobs:
  FAKE_CLAUDE_NEEDS_INPUT=<stage>  emit needs_input for that stage (unless the prompt has a USER ANSWER)
  FAKE_CLAUDE_FAIL=<stage>         append an error event for that stage
  FAKE_CLAUDE_SLEEP=<seconds>      sleep before doing the work (cancel/timeout tests)
  FAKE_CLAUDE_SLEEP_STAGE=<stage>  only sleep for that stage
  FAKE_CLAUDE_EXIT=<code>          exit with that code after the result
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

VALUE_FLAGS = {"--output-format", "--resume", "-r", "--permission-mode", "--append-system-prompt", "--agents",
               "--model", "--effort", "--max-budget-usd", "--json-schema", "--plugin-dir", "--tools",
               "--session-id", "--input-format"}
VARIADIC_FLAGS = {"--allowedTools", "--allowed-tools", "--add-dir", "--disallowedTools"}
BOOL_FLAGS = {"-p", "--print", "--verbose", "--version", "-v"}


def parse(argv: list[str]) -> dict:
    opts: dict = {"positional": [], "add_dir": [], "allowed": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in BOOL_FLAGS:
            opts[a.lstrip("-")] = True
            i += 1
        elif a in VALUE_FLAGS:
            opts[a.lstrip("-")] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        elif a in VARIADIC_FLAGS:
            i += 1
            while i < len(argv) and not argv[i].startswith("-"):
                (opts["add_dir"] if a == "--add-dir" else opts["allowed"]).append(argv[i])
                i += 1
        elif a.startswith("-"):
            i += 1  # unknown flag: ignore
        else:
            opts["positional"].append(a)
            i += 1
    return opts


def out(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def tool_use(name: str, **inp) -> None:
    out({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:12], "name": name, "input": inp}]}})


def ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def has_drawtext() -> bool:
    ff = ffmpeg()
    if not ff:
        return False
    p = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True)
    return " drawtext " in p.stdout


def read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(ws: Path, rel: str, data) -> str:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tool_use("Write", file_path=str(p))
    return rel


def project(ws: Path) -> dict:
    return read_json(ws / "project.json", {}) or {}


def config(ws: Path) -> dict:
    return project(ws).get("config") or {}


# ------------------------------------------------------------------ creative stages
def do_research(ws: Path, prompt: str) -> list[str]:
    title = project(ws).get("title", "the topic")
    tool_use("Task", subagent_type="researcher", description=f"research {title}")
    tool_use("Task", subagent_type="fact-checker", description="spot-check claims")
    data = {"summary": f"[SIMULATED] Research summary for {title}.",
            "key_facts": [{"id": "F1", "claim": f"{title} is interesting.", "source_ids": ["S1"]},
                          {"id": "F2", "claim": f"{title} has a history.", "source_ids": ["S1"]}],
            "sources": [{"id": "S1", "title": "Simulated source", "url": "https://example.org/simulated",
                         "publisher": "Example", "accessed": "2026-01-01"}],
            "angles": ["origins", "impact"]}
    (ws / "research").mkdir(parents=True, exist_ok=True)
    (ws / "research" / "sources.md").write_text("- S1: Simulated source (https://example.org/simulated)\n")
    return [write_json(ws, "research/research.json", data), "research/sources.md"]


def do_factcheck(ws: Path, prompt: str) -> list[str]:
    tool_use("Task", subagent_type="fact-checker", description="verify claims")
    r = read_json(ws / "research/research.json", {}) or {}
    items = [{"claim_id": f["id"], "claim": f["claim"], "status": "verified", "note": "simulated",
              "source_ids": f.get("source_ids", [])} for f in r.get("key_facts", [])]
    return [write_json(ws, "research/factcheck.json", {"items": items})]


def do_concept(ws: Path, prompt: str) -> list[str]:
    pj = project(ws)
    data = {"title": pj.get("title", "Untitled"), "idea": pj.get("idea", ""), "logline": "[SIMULATED] logline",
            "angle": "origins", "structure": ["hook", "story", "ending"], "look": "simulated",
            "audience": "general", "config": pj.get("config") or {}}
    return [write_json(ws, "brief.json", data)]


def do_script(ws: Path, prompt: str) -> list[str]:
    title = project(ws).get("title", "Untitled")
    texts = ["[SIMULATED] Hook line.", "Second line of the story.", "Third line with a fact.", "Closing line."]
    lines = [{"id": f"L{i + 1:02d}", "text": t, "spoken": t, "section": "main", "fact_ids": ["F1"] if i == 2 else []}
             for i, t in enumerate(texts)]
    dur = float(config(ws).get("duration_seconds", 8))
    rev = 1 + len(list((ws / ".spacestudio" / "versions" / "script").glob("*"))) \
        if (ws / ".spacestudio" / "versions" / "script").exists() else 1
    if "CHANGE REQUEST" in prompt:
        lines[0]["text"] = lines[0]["spoken"] = "[SIMULATED] Revised hook line."
    return [write_json(ws, "scripts/script.json", {"version": rev, "title": title, "lines": lines,
                                                   "word_count": sum(len(t.split()) for t in texts),
                                                   "estimated_seconds": dur})]


def do_storyboard(ws: Path, prompt: str) -> list[str]:
    m = re.search(r"^ENGINES_AVAILABLE=(.*)$", prompt, re.M)
    engines = [e for e in (m.group(1).split(",") if m else []) if e] or ["cinematic-skill"]
    e1, e2 = engines[0], engines[1] if len(engines) > 1 else engines[0]
    dur = float(config(ws).get("duration_seconds", 8))
    half = round(dur / 2, 3)
    scenes = [
        {"number": 1, "segment": "s1", "line_ids": ["L01", "L02"], "visual": "opening board", "duration_seconds": half,
         "engine": e1, "method": "simulated", "asset_ids": ["A1"]},
        {"number": 2, "segment": "s2", "line_ids": ["L03", "L04"], "visual": "closing style",
         "duration_seconds": round(dur - half, 3), "engine": e2, "method": "simulated", "asset_ids": []},
    ]
    segments = [{"id": "s1", "engine": e1, "engine_options": {}, "scene_numbers": [1], "reason": "simulated: first"},
                {"id": "s2", "engine": e2, "engine_options": {}, "scene_numbers": [2], "reason": "simulated: second"}]
    tool_use("Task", subagent_type="storyboard-artist", description="split into segments")
    return [write_json(ws, "storyboard/storyboard.json", {"scenes": scenes, "segments": segments})]


def do_asset_plan(ws: Path, prompt: str) -> list[str]:
    items = [{"id": "A1", "kind": "image", "source": "simulated", "license": "Public domain",
              "credit": "Simulated image", "provider": "fake", "status": "planned"}]
    return [write_json(ws, "assets/manifest.json", {"items": items})]


def do_assets(ws: Path, prompt: str) -> list[str]:
    man = read_json(ws / "assets/manifest.json", {"items": []}) or {"items": []}
    outs = []
    for it in man.get("items", []):
        if it.get("kind") == "image":
            rel = f"assets/images/{it['id']}.png"
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            ff = ffmpeg()
            if ff:
                subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=320x180",
                                "-frames:v", "1", str(ws / rel)], check=False)
            else:
                (ws / rel).write_bytes(b"")
            it.update(status="acquired", path=rel, license=it.get("license") or "Public domain")
            outs.append(rel)
    outs.append(write_json(ws, "assets/manifest.json", man))
    return outs


# ------------------------------------------------------------------ engine stages
def do_narration(ws: Path, prompt: str, engine: str) -> list[str]:
    script = read_json(ws / "scripts/script.json", {"lines": []}) or {"lines": []}
    sb = read_json(ws / "storyboard/storyboard.json", {"scenes": []}) or {"scenes": []}
    total = sum(float(s["duration_seconds"]) for s in sb.get("scenes", [])) or float(config(ws).get("duration_seconds", 8))
    lines = script.get("lines", [])
    step = total / max(1, len(lines))
    timing = {"audio": "narration/narration.wav", "engine": engine, "voice": "simulated-sine", "duration": round(total, 3),
              "lines": [{"id": ln["id"], "start": round(i * step, 3), "end": round((i + 1) * step, 3), "text": ln["text"]}
                        for i, ln in enumerate(lines)]}
    wav = ws / "narration" / "narration.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg()
    if ff:
        cmd = [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={total:.3f}",
               "-ac", "2", "-c:a", "pcm_s16le", str(wav)]
        tool_use("Bash", command=" ".join(cmd[:6]) + " ...")
        subprocess.run(cmd, check=False)
    return ["narration/narration.wav", write_json(ws, "narration/timing.json", timing)]


def _segment_window(ws: Path, seg_id: str) -> tuple[float, float]:
    sb = read_json(ws / "storyboard/storyboard.json", {"scenes": [], "segments": []}) or {}
    t, wins = 0.0, {}
    for sc in sorted(sb.get("scenes", []), key=lambda s: s["number"]):
        wins[sc["number"]] = (t, t + float(sc["duration_seconds"]))
        t += float(sc["duration_seconds"])
    seg = next((s for s in sb.get("segments", []) if s["id"] == seg_id), None)
    if not seg:
        return 0.0, 1.0
    ws_ = [wins[n] for n in seg["scene_numbers"] if n in wins]
    return min(w[0] for w in ws_), max(w[1] for w in ws_)


def do_segment(ws: Path, prompt: str, seg_id: str, engine: str) -> list[str]:
    start, end = _segment_window(ws, seg_id)
    m = re.search(r'"start":\s*([0-9.]+).*?"end":\s*([0-9.]+)', prompt, re.S)
    if m and float(m.group(2)) > float(m.group(1)):
        start, end = float(m.group(1)), float(m.group(2))
    cfg = config(ws)
    res = cfg.get("resolution", "1920x1080")
    fps = int(cfg.get("fps", 24))
    dur = max(0.1, end - start)
    rel = f"scenes/{seg_id}.mp4"
    out_p = ws / rel
    out_p.parent.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg()
    if ff:
        vf = "format=yuv420p"
        if has_drawtext():
            vf = f"drawtext=text='SIMULATED {engine}':fontcolor=white:fontsize=24:x=10:y=10,format=yuv420p"
        cmd = [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={res}:rate={fps}:duration={dur:.3f}",
               "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-t", f"{dur:.3f}", "-an", str(out_p)]
        tool_use("Bash", command=f"spacestudio-slot render -- ffmpeg testsrc2 {res} {dur:.3f}s")
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0 and "drawtext" in vf:  # e.g. no default font: fall back without the label
            cmd[cmd.index("-vf") + 1] = "format=yuv420p"
            subprocess.run(cmd, check=False)
    credits = f"credits/{engine}-{seg_id}.md"
    (ws / "credits").mkdir(parents=True, exist_ok=True)
    (ws / credits).write_text(f"# {engine} (simulated)\n\nSegment {seg_id} rendered by the fake Claude CLI.\n")
    meta = {"segment": seg_id, "engine": engine, "video": rel, "start": start, "end": end, "has_audio": False,
            "credits": [credits]}
    return [rel, write_json(ws, f"scenes/{seg_id}.json", meta), credits]


def do_expected(ws: Path, prompt: str, cwd: Path) -> list[str]:
    """Native / unknown stages: touch every expected output (mp4s get a real tiny video)."""
    m = re.search(r"^SPACESTUDIO_EXPECTED_OUTPUTS=(.*)$", prompt, re.M)
    try:
        outs = json.loads(m.group(1)) if m else []
    except json.JSONDecodeError:
        outs = []
    written = []
    cfg = config(ws)
    for rel in outs:
        if any(c in rel for c in "*?["):
            rel = re.sub(r"[*?\[\]]+", "simulated", rel)
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".mp4" and ffmpeg():
            subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"testsrc2=size={cfg.get('resolution', '640x360')}:rate={cfg.get('fps', 24)}:duration=1",
                            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(p)], check=False)
        elif p.suffix == ".json":
            p.write_text("{}")
        else:
            p.write_text("simulated\n")
        written.append(rel)
    return written


def main() -> int:
    argv = sys.argv[1:]
    if "--version" in argv or "-v" in argv:
        print("0.0.0 (fake Claude Code — SIMULATED)")
        return 0
    opts = parse(argv)
    prompt = opts["positional"][0] if opts["positional"] else sys.stdin.read()
    session_id = opts.get("resume") or str(uuid.uuid4())
    fmt = opts.get("output-format", "text")

    if fmt == "json" and opts.get("json-schema"):  # ideas
        ideas = [{"title": f"[SIMULATED] Idea {i + 1}", "idea": "A simulated idea."} for i in range(3)]
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": json.dumps({"ideas": ideas}),
                          "structured_output": {"ideas": ideas}, "total_cost_usd": 0, "session_id": session_id}))
        return 0

    m = re.search(r"SPACESTUDIO_STAGE=(\S+) VIDEO=(\S+) WORKSPACE=(.+?) LANE=(\S+)", prompt)
    out({"type": "system", "subtype": "init", "session_id": session_id, "model": opts.get("model") or "fake-model",
         "cwd": os.getcwd(), "tools": ["Read", "Write", "Bash", "Task"], "permissionMode": opts.get("permission-mode")})
    if not m:
        out({"type": "result", "subtype": "success", "is_error": False, "result": "no SpaceStudio stage line",
             "total_cost_usd": 0, "session_id": session_id})
        return 0
    key, video, ws_s, lane = m.groups()
    ws = Path(ws_s.strip())
    sleep_stage = os.environ.get("FAKE_CLAUDE_SLEEP_STAGE")
    if os.environ.get("FAKE_CLAUDE_SLEEP") and (not sleep_stage or sleep_stage == key):
        tool_use("Bash", command=f"sleep {os.environ['FAKE_CLAUDE_SLEEP']}")
        time.sleep(float(os.environ["FAKE_CLAUDE_SLEEP"]))
    events = ws / ".spacestudio" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)

    def append(ev: dict) -> None:
        with open(events, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")

    engine = lane.split(":", 1)[1] if lane.startswith("engine:") else "spacestudio"
    if os.environ.get("FAKE_CLAUDE_NEEDS_INPUT") == key and "USER ANSWER" not in prompt:
        append({"type": "needs_input", "stage": key, "question": f"[SIMULATED] Which angle should {key} take?"})
        out({"type": "result", "subtype": "success", "is_error": False, "result": "needs input",
             "total_cost_usd": 0, "session_id": session_id})
        return 0
    if os.environ.get("FAKE_CLAUDE_FAIL") == key:
        append({"type": "error", "stage": key, "message": f"[SIMULATED] {key} failed on purpose"})
        out({"type": "result", "subtype": "success", "is_error": False, "result": "failed",
             "total_cost_usd": 0, "session_id": session_id})
        return 0

    handlers = {"research": do_research, "factcheck": do_factcheck, "concept": do_concept, "script": do_script,
                "storyboard": do_storyboard, "asset_plan": do_asset_plan, "assets": do_assets}
    if key in handlers:
        outputs = handlers[key](ws, prompt)
    elif key == "narration":
        outputs = do_narration(ws, prompt, engine)
    elif key.startswith("segment:"):
        outputs = do_segment(ws, prompt, key.split(":", 1)[1], engine)
    else:
        outputs = do_expected(ws, prompt, Path.cwd())
    append({"type": "stage_completed", "stage": key, "outputs": outputs,
            "summary": f"[SIMULATED] {key} done by the fake Claude CLI."})
    out({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": f"Stage {key} complete (simulated)."}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": f"{key} done", "total_cost_usd": 0,
         "session_id": session_id, "num_turns": 3, "permission_denials": []})
    return int(os.environ.get("FAKE_CLAUDE_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main())
