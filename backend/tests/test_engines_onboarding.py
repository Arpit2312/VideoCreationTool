"""Onboarding: prompt, draft validation, registry loading of unapproved drafts, approval, generic adapter."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from spacestudio.engines.generic import GenericAdapter
from spacestudio.engines.onboarding import (approve_manifest, build_onboarding_prompt, load_draft,
                                            validate_manifest)
from spacestudio.engines.registry import Registry
from spacestudio.workflow.stages import StageSpec

GOOD = {
    "id": "toy-engine", "name": "Toy", "description": "Makes toy films.", "license": "MIT",
    "repo": {"url": "https://example.invalid/toy", "default_dir": "toy"}, "adapter": "generic",
    "invocation": {"kind": "claude-skill", "skill_file": "SKILL.md"},
    "requires": {"commands": ["ffmpeg"]},
    "health": {"expected_files": ["SKILL.md"]},
    "capabilities": {"scene_render": {"supported": True, "detail": "render.py"},
                     "narration": {"supported": False, "detail": "none"}},
    "options": {}, "cost": {"zero_cost": True, "paid_env": [], "notes": ""},
    "concurrency": {"render_heavy_stages": ["render"], "env": {}},
    "setup": [{"id": "pip", "title": "Install", "command": "pip install -r requirements.txt", "cwd": "{repo}"}],
    "allowed_tools": ["Read", "Write", "Bash(python3 *)"], "env": {},
    "stages": [{"key": "draft", "name": "Draft", "gate": True, "produces": ["draft.mp4"]},
               {"key": "render", "name": "Render", "produces": ["out/*.mp4"]}],
    "prompts": {"render": "Render the film in {project} using {repo}/render.py. {config}"},
}


def test_prompt_has_marker_and_rules(tmp_path):
    p = build_onboarding_prompt("https://github.com/owner/repo", tmp_path / "engines", clone_dir=tmp_path / "src",
                                existing_ids=["openmontage"])
    first = p.splitlines()[0]
    assert first == f"SPACESTUDIO_STAGE=onboard VIDEO=- WORKSPACE={(tmp_path / 'engines').resolve()} LANE=onboarding"
    assert "git clone --depth 1 https://github.com/owner/repo" in p and "<id>/engine.yaml" in p
    assert "scene_render" in p and "adapter must be `generic`" in p and "openmontage" in p


def test_validation_errors():
    assert validate_manifest(GOOD) == []
    bad = json.loads(json.dumps(GOOD))
    bad.update(id="Bad Id", adapter="spacestudio.engines.cinematic.CinematicAdapter", approved=True,
               allowed_tools=["Bash", "Bash(*)", "Bash(rm -rf *)", "Bash(node *)"], env={"OPENAI_API_KEY": "sk-1"})
    bad["capabilities"]["teleport"] = {"supported": True}
    bad["stages"].append({"key": "narration"})
    del bad["cost"]
    errs = " | ".join(validate_manifest(bad))
    for frag in ("id must be", "adapter: generic", "approve itself", "'Bash' is not allowed", "'Bash(*)' is too broad",
                 "Bash(rm -rf *)", "looks like a secret", "unknown capability 'teleport'", "reserved", "missing field: cost"):
        assert frag in errs, frag
    assert "Bash(node *)" not in errs
    assert any("already used" in e for e in validate_manifest(GOOD, existing_ids=["toy-engine"]))
    assert validate_manifest("nope") == ["the manifest is not a YAML mapping"]


def test_draft_load_registry_and_approve(tmp_path):
    data = tmp_path / "data"
    d = data / "engines" / "toy-engine"
    d.mkdir(parents=True)
    f = d / "engine.yaml"
    f.write_text(yaml.safe_dump({**GOOD, "adapter": "generic"}))
    m, errs = load_draft(f, existing_ids=["openmontage"])
    assert errs == [] and m["id"] == "toy-engine"
    repo = tmp_path / "engines" / "toy"
    repo.mkdir(parents=True)
    (repo / "SKILL.md").write_text("# toy")
    s = SimpleNamespace(data_dir=data, engines_dir=tmp_path / "engines", engine_paths={}, python_bin="python3")
    r = Registry(s)
    r.load()
    a = r.get("toy-engine")
    assert isinstance(a, GenericAdapter) and a.approved is False
    meta = next(x for x in r.manifests() if x["id"] == "toy-engine")
    assert meta["source"] == "onboarded" and meta["approved"] is False
    h = a.health()
    assert h.ok and not h.zero_cost_ready and any(i.id == "approved" for i in h.items)
    approve_manifest(f)
    r.load()
    assert r.get("toy-engine").approved is True and r.get("toy-engine").health().zero_cost_ready
    (f.parent / "bad.yaml")  # unused
    f.write_text(": : not yaml")
    _, errs = load_draft(f)
    assert errs and "YAML" in errs[0]


def test_onboarded_manifest_cannot_pick_code(tmp_path):
    d = tmp_path / "data" / "engines" / "sneaky"
    d.mkdir(parents=True)
    (d / "engine.yaml").write_text(yaml.safe_dump({**GOOD, "id": "sneaky", "adapter": "os.system"}))
    (tmp_path / "data" / "engines" / "dup").mkdir()
    (tmp_path / "data" / "engines" / "dup" / "engine.yaml").write_text(yaml.safe_dump({**GOOD, "id": "cinematic-skill"}))
    r = Registry(SimpleNamespace(data_dir=tmp_path / "data", engines_dir=tmp_path, engine_paths={}))
    r.load()
    assert type(r.get("sneaky")) is GenericAdapter
    assert r.get("cinematic-skill").manifest.get("adapter") != "generic"  # built-in wins
    assert any("already registered" in e["error"] for e in r.errors)


def test_generic_adapter_stages_detect_prompt(tmp_path):
    a = GenericAdapter({**GOOD, "approved": True}, tmp_path / "repo", {"python_bin": "python3"})
    stages = a.native_stages({})
    assert [s.key for s in stages] == ["draft", "render"]
    assert stages[0].gate and stages[1].outputs == ["work/toy-engine/film/out/*.mp4"]
    ws = tmp_path / "v_00000001"
    assert a.detect(ws, stages[1]).status == "pending"
    out = ws / "work/toy-engine/film/out"
    out.mkdir(parents=True)
    (out / "a.mp4").write_bytes(b"x")
    assert a.detect(ws, stages[1]).status == "completed"
    (ws / "work/toy-engine/film/draft.mp4").write_bytes(b"x")
    assert a.detect(ws, stages[0]).status == "awaiting_approval"
    p = a.stage_prompt(stages[1], ws, {})
    assert p.startswith(f"SPACESTUDIO_STAGE=render VIDEO=v_00000001 WORKSPACE={ws.resolve()} LANE=engine:toy-engine\n")
    assert f"{tmp_path / 'repo'}/render.py" in p and str(ws.resolve() / "work/toy-engine/film") in p
    seg = StageSpec("segment:s1", "Seg", "", "scene_render", lane="engine:toy-engine",
                    params={"segment": "s1", "start": 0, "end": 4})
    ps = a.stage_prompt(seg, ws, {})
    assert "SpaceStudio I/O contract" in ps and "scenes/s1.mp4" in ps
