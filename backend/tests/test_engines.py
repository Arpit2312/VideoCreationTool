"""Engine registry, manifests, adapters (health, stages, launch specs, prompts) against the real clones."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacestudio.engines.base import EngineHealth, HealthItem, LaunchSpec
from spacestudio.engines.generic import fill, ffmpeg_major, version_satisfies
from spacestudio.engines.registry import Registry
from spacestudio.presets import get_presets
from spacestudio.workflow.stages import Capability, StageSpec

ENGINES_DIR = Path("~/.spacestudio/engines").expanduser()
need_clones = pytest.mark.skipif(not all((ENGINES_DIR / d).is_dir() for d in ("OpenMontage", "cinematic-skill", "lemo-opuscar")),
                                 reason="engine clones not present")
MARKER = re.compile(r"^SPACESTUDIO_STAGE=(\S+) VIDEO=(\S+) WORKSPACE=(/\S+) LANE=(\S+)$")


def make_settings(tmp_path, **kw):
    base = dict(data_dir=tmp_path / "data", engines_dir=ENGINES_DIR, engine_paths={}, python_bin="python3",
                render_workers=2, max_render_jobs=1, ffmpeg="ffmpeg", ffprobe="ffprobe")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    r = Registry(make_settings(tmp_path_factory.mktemp("reg")))
    r.load()
    return r


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "v_1a2b3c4d"
    (w / "work").mkdir(parents=True)
    return w


# ---------------------------------------------------------------- helpers
def test_helpers():
    assert fill("a {repo} {txt}", {"repo": "/r"}) == "a /r {txt}"
    assert version_satisfies((22, 23, 5), "^22.23.4 || ^24.22.1")
    assert not version_satisfies((22, 1, 0), "^22.23.4 || ^24.22.1")
    assert version_satisfies((24, 30), "^22.23.4 || ^24.22.1")
    assert version_satisfies((3, 12), ">=3.11") and not version_satisfies((3, 9), ">=3.11")
    assert ffmpeg_major("ffmpeg version 6.1.1-3ubuntu5 Copyright") == 6
    assert ffmpeg_major("ffmpeg version n7.1 Copyright") == 7
    assert ffmpeg_major("ffmpeg version N-117000-gabc Copyright") is None


# ---------------------------------------------------------------- registry + manifests
@need_clones
def test_registry_loads_three_engines(registry):
    assert registry.errors == []
    assert sorted(a.id for a in registry.all()) == ["cinematic-skill", "lemo-opuscar", "openmontage"]
    with pytest.raises(KeyError):
        registry.get("nope")
    caps = {c.value for c in Capability}
    for m in registry.manifests():
        for k in ("id", "name", "description", "license", "repo", "invocation", "requires", "capabilities",
                  "options", "cost", "concurrency", "setup", "allowed_tools", "env", "adapter"):
            assert k in m, (m["id"], k)
        assert set(m["capabilities"]) <= caps
        assert m["approved"] is True and m["source"] == "builtin"
        assert Path(m["repo_path"]).is_dir()
    assert registry.get("openmontage").repo_path == ENGINES_DIR / "OpenMontage"


@need_clones
def test_engine_path_override(tmp_path):
    r = Registry(make_settings(tmp_path, engine_paths={"cinematic-skill": str(tmp_path / "elsewhere")}))
    r.load()
    assert r.get("cinematic-skill").repo_path == tmp_path / "elsewhere"
    h = r.get("cinematic-skill").health()
    assert not h.ok and h.items[0].id == "repo" and h.items[0].status == "error" and "git clone" in h.items[0].fix


@need_clones
def test_paid_env(registry):
    paid = registry.paid_env_names()
    for k in ("FAL_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "RUNWAY_API_KEY", "GOOGLE_API_KEY", "SUNO_API_KEY"):
        assert k in paid
    for k in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "UNSPLASH_ACCESS_KEY", "MINIMAX_REGION", "GOOGLE_CLOUD_LOCATION"):
        assert k not in paid
    assert {"PEXELS_API_KEY", "PIXABAY_API_KEY", "UNSPLASH_ACCESS_KEY"} <= registry.free_env_names()
    # every paid key except ATLASCLOUD_API_KEY (named in a comment) is a real line of .env.example
    example = (ENGINES_DIR / "OpenMontage" / ".env.example").read_text()
    for k in paid - {"ATLASCLOUD_API_KEY"}:
        assert re.search(rf"^#? ?{k}=", example, re.M), k


@need_clones
def test_lemo_styles(registry):
    opts = registry.get("lemo-opuscar").options()
    styles = opts["styles"]
    assert len(styles) == 99 and opts["style_count"] == 99
    paid = sorted(s["id"] for s in styles if not s["zero_cost"])
    assert paid == ["comic-history", "manga-panels", "text-flash-cuts"]
    assert all(s["needs"] == ["image-generation"] for s in styles if not s["zero_cost"])
    assert opts["options_from"] == "styles/*/style.json"


@need_clones
def test_openmontage_pipelines(registry):
    opts = registry.get("openmontage").options()
    pipes = {p["id"]: p for p in opts["pipelines"]}
    assert len(pipes) == 13
    dm = pipes["documentary-montage"]
    assert [s["name"] for s in dm["stages"]] == ["idea", "scene_plan", "assets", "edit", "compose"]
    assert [s["gate"] for s in dm["stages"]] == [True, True, True, True, False]
    assert dm["stability"] == "beta" and dm["zero_cost"]
    assert pipes["animated-explainer"]["stability"] == "production"
    assert pipes["framework-smoke"]["hidden"]
    assert not pipes["cinematic"]["zero_cost"]
    assert {p["id"] for p in opts["playbooks"]} >= {"clean-professional", "flat-motion-graphics"}


@need_clones
def test_cinematic_options(registry):
    o = registry.get("cinematic-skill").options()
    assert [x["id"] for x in o["looks"]] == ["wall", "slate", "studio", "charcoal", "blueprint", "paper", "night"]
    assert {x["id"] for x in o["grades"]} == {"bw", "sepia", "natural"}
    assert {x["id"] for x in o["caption_styles"]} == {"highlighter", "clean", "paper", "redbar"}


# ---------------------------------------------------------------- health
@need_clones
@pytest.mark.parametrize("eid", ["cinematic-skill", "lemo-opuscar", "openmontage"])
def test_health_structured(registry, eid):
    h = registry.get(eid).health(zero_cost=True)
    assert isinstance(h, EngineHealth) and h.engine == eid
    assert h.items and all(isinstance(i, HealthItem) for i in h.items)
    assert all(i.status in {"ok", "warning", "error", "not_run"} for i in h.items)
    assert h.ok == (not any(i.status == "error" for i in h.items))
    assert h.commit and re.fullmatch(r"[0-9a-f]{40}", h.commit)
    for i in h.items:
        if i.status == "error":
            assert i.fix, (eid, i.id)


@need_clones
def test_lemo_health_ffmpeg6(registry):
    h = registry.get("lemo-opuscar").health()
    ff = next(i for i in h.items if i.id == "ffmpeg_version")
    if "version 6." in ff.detail:
        assert ff.status == "error" and "static" in ff.fix


@need_clones
def test_openmontage_dotenv_blocks_zero_cost(tmp_path):
    repo = tmp_path / "OpenMontage"
    shutil.copytree(ENGINES_DIR / "OpenMontage", repo, ignore=shutil.ignore_patterns(".git", "node_modules", ".venv", "projects"))
    (repo / ".env").write_text("FAL_KEY=abc123\nMINIMAX_REGION=global\nPEXELS_API_KEY=free\nOPENAI_API_KEY=  # note\n")
    r = Registry(make_settings(tmp_path, engine_paths={"openmontage": str(repo)}))
    r.load()
    a = r.get("openmontage")
    assert a.paid_keys_in_dotenv() == ["FAL_KEY"]
    h = a.health()
    assert h.zero_cost_ready is False
    item = next(i for i in h.items if i.id == "paid_keys")
    assert "FAL_KEY" in item.detail and "abc123" not in item.detail and item.fix


# ---------------------------------------------------------------- stages
@need_clones
def test_native_stages_shapes(registry):
    cin = registry.get("cinematic-skill").native_stages({})
    assert [s.key for s in cin] == ["story", "images", "voice", "look", "draft", "sound", "finish"]
    assert {s.key for s in cin if s.gate and s.engine_gate} == {"story", "look", "draft"}
    assert cin[0].depends_on == ["setup"] and cin[1].depends_on == ["story"]
    assert all(s.lane == "engine:cinematic-skill" and s.owner == "cinematic-skill" for s in cin)
    assert "work/cinematic-skill/film/beats.md" in cin[0].outputs

    lemo = registry.get("lemo-opuscar")
    assert [s.key for s in lemo.native_stages({})] == ["brief", "treatment", "look", "produce", "deliver"]
    sb = lemo.native_stages({"engine_options": {"lemo-opuscar": {"storyboard": True}}})
    assert [s.key for s in sb] == ["brief", "treatment", "look", "storyboard", "produce", "deliver"]
    assert sb[3].gate and sb[3].engine_gate and not sb[3].required

    om = registry.get("openmontage")
    st = om.native_stages({"engine_options": {"openmontage": {"pipeline": "documentary-montage"}}})
    assert [s.key for s in st] == ["idea", "scene_plan", "om_assets", "edit", "compose"]
    assert [s.gate for s in st] == [True, True, True, True, False] and all(s.gate == s.engine_gate for s in st)
    assert st[1].params["prev_gate"] == "idea"
    default = om.native_stages({})
    assert default[0].key == "research" and default[0].params["pipeline"] == "animated-explainer"
    with pytest.raises(ValueError):
        om.native_stages({"engine_options": {"openmontage": {"pipeline": "cinematic"}}})  # not verified free
    assert om.native_stages({"zero_cost": False, "engine_options": {"openmontage": {"pipeline": "cinematic"}}})


# ---------------------------------------------------------------- launch specs
@need_clones
def test_launch_specs(registry, ws):
    seg = StageSpec("segment:s1", "Segment s1", "", "scene_render", owner="x", lane="engine:x",
                    params={"segment": "s1", "start": 0.0, "end": 5.0})
    cin = registry.get("cinematic-skill").launch_spec(ws, seg, {})
    assert isinstance(cin, LaunchSpec) and cin.cwd == (ws / "work" / "cinematic-skill").resolve() and cin.cwd.is_dir()
    assert cin.plugin_dirs == [(ENGINES_DIR / "cinematic-skill").resolve()]
    assert (cin.plugin_dirs[0] / ".claude-plugin" / "marketplace.json").exists()
    assert ENGINES_DIR / "cinematic-skill" in cin.add_dirs

    lemo = registry.get("lemo-opuscar").launch_spec(ws, seg, {})
    assert lemo.plugin_dirs[0] == (ENGINES_DIR / "lemo-opuscar" / "plugin").resolve()
    assert (lemo.plugin_dirs[0] / "skills" / "lemo-opuscar" / "SKILL.md").exists()
    assert lemo.env["LEMO_OPUSCAR_HOME"] == str(ENGINES_DIR / "lemo-opuscar") and lemo.env["RENDER_SLOTS"] == "1"
    assert f"Bash({ENGINES_DIR / 'lemo-opuscar'}/.venv/bin/python *)" in lemo.allowed_tools

    om = registry.get("openmontage").launch_spec(ws, seg, {})
    assert om.env["OPENMONTAGE_PROJECTS_DIR"] == str((ws / "work" / "openmontage").resolve())
    assert om.plugin_dirs == [] and ENGINES_DIR / "OpenMontage" in om.add_dirs
    for spec in (cin, lemo, om):
        assert all("{" not in t for t in spec.allowed_tools)
        assert "Bash" not in spec.allowed_tools and "Bash(*)" not in spec.allowed_tools
        assert all(re.fullmatch(r"(Read|Write|Edit|Glob|Grep|WebSearch|WebFetch|Bash\([^()]+ \*\))", t) for t in spec.allowed_tools)


@need_clones
def test_setup_steps_resolved(registry):
    for a in registry.all():
        steps = a.setup_steps()
        assert steps
        for s in steps:
            assert {"id", "title", "command", "cwd"} <= set(s) and s["cwd"] == str(a.repo_path)
    lemo = registry.get("lemo-opuscar").setup_steps()
    assert lemo[1]["command"] == "sh plugin/skills/lemo-opuscar/scripts/setup.sh deps"
    assert lemo[1]["env"]["LEMO_OPUSCAR_HOME"] == str(ENGINES_DIR / "lemo-opuscar")
    assert registry.get("openmontage").setup_steps()[0]["command"] == "make setup"


# ---------------------------------------------------------------- prompts
def _stages():
    return [
        StageSpec("assets", "Assets", "", "asset_acquisition", lane="engine:e"),
        StageSpec("narration", "Narration", "", "narration", lane="engine:e"),
        StageSpec("segment:s2", "Segment s2", "", "scene_render", lane="engine:e",
                  params={"segment": "s2", "start": 12.5, "end": 30.0, "engine_options": {"style": "whiteboard"}}),
    ]


@need_clones
@pytest.mark.parametrize("eid", ["cinematic-skill", "lemo-opuscar", "openmontage"])
def test_collaborative_prompts(registry, ws, eid):
    a = registry.get(eid)
    cfg = {"resolution": "1920x1080", "fps": 24, "zero_cost": True,
           "engine_options": {"cinematic-skill": {"look": "night"}, "lemo-opuscar": {"style": "whiteboard"}}}
    for st in _stages():
        st.lane = f"engine:{eid}"
        p = a.stage_prompt(st, ws, cfg)
        m = MARKER.match(p.splitlines()[0])
        assert m, p.splitlines()[0]
        assert m.group(1) == st.key and m.group(2) == ws.name and m.group(3) == str(ws.resolve()) and m.group(4) == f"engine:{eid}"
        assert "SpaceStudio I/O contract" in p and "ZERO-COST MODE" in p
        assert "Approvals come from SpaceStudio" in p and "Never wait for interactive input" in p
        if st.key == "assets":
            assert "assets/manifest.json" in p and "storyboard/storyboard.json" in p and "license" in p
        if st.key == "narration":
            assert "narration/narration.wav" in p and "narration/timing.json" in p and "scripts/script.json" in p
        if st.key.startswith("segment:"):
            assert "scenes/s2.mp4" in p and "scenes/s2.json" in p and f"credits/{eid}-s2.md" in p
            assert "17.500" in p and "1920x1080" in p and "picture only" in p
    seg = a.stage_prompt(_stages()[2], ws, cfg)
    if eid == "cinematic-skill":
        assert "new_project.py" in seg and "assets/template/script.json" in seg and "--workers 2" in seg
        assert "--t1 17.500" in seg and 'look="night"' in seg and "narration/narration.wav" in seg
        assert "fetch_commons.py search" in a.stage_prompt(_stages()[0], ws, cfg)
    if eid == "lemo-opuscar":
        assert "window.render(t)" in seg and "core/render/video.mjs" in seg and "RENDER_SLOTS=1" in seg
        assert "STYLE.md" in seg and "--size 1920x1080" in seg
        assert "core/tts/tts.py" in a.stage_prompt(_stages()[1], ws, cfg)
    if eid == "openmontage":
        assert "AGENT_GUIDE.md" in seg and f"cd {a.repo_path}" in seg and "OPENMONTAGE_PROJECTS_DIR" in seg
        assert "piper_tts" in a.stage_prompt(_stages()[1], ws, cfg)
        assert "direct_clip_search" in a.stage_prompt(_stages()[0], ws, cfg)


@need_clones
def test_referenced_files_exist(registry, ws):
    """Every repo path a prompt names must exist in the clone."""
    for a in registry.all():
        root = str(a.repo_path)
        stages = _stages() + a.native_stages({"engine_options": {"lemo-opuscar": {"storyboard": True}}})
        for st in stages:
            p = a.stage_prompt(st, ws, {"engine_options": {"lemo-opuscar": {"style": "whiteboard"}}})
            for ref in set(re.findall(re.escape(root) + r"/[\w./-]+", p)):
                ref = ref.rstrip(".")
                if ref.endswith("/.venv/bin/python"):
                    continue  # created by setup
                assert Path(ref).exists(), (a.id, st.key, ref)


@need_clones
def test_native_prompts(registry, ws):
    a = registry.get("cinematic-skill")
    story, draft = a.native_stages({})[0], a.native_stages({})[4]
    p = a.stage_prompt(story, ws, {})
    assert MARKER.match(p.splitlines()[0]) and "make_beats.py" in p and "awaiting_approval" in p and "END YOUR TURN" in p
    assert "--t0 0 --t1 30 --size 1280x720" in a.stage_prompt(draft, ws, {})
    om = registry.get("openmontage")
    st = om.native_stages({"engine_options": {"openmontage": {"pipeline": "documentary-montage"}}})
    p0 = om.stage_prompt(st[0], ws, {})
    assert "init_project" in p0 and "awaiting_human" in p0 and "idea-director" in p0
    p1 = om.stage_prompt(st[1], ws, {})
    assert "human_approved=True" in p1 and "checkpoint_idea.json" in p1
    lemo = registry.get("lemo-opuscar")
    pb = lemo.stage_prompt(lemo.native_stages({})[0], ws, {"engine_options": {"lemo-opuscar": {"style": "manga-panels"}}})
    assert "zero-cost mode does not allow" in pb
    assert "needs_input" in lemo.stage_prompt(lemo.native_stages({})[0], ws, {})


def test_prompt_feedback_and_resume(tmp_path):
    from spacestudio.engines.generic import GenericAdapter
    a = GenericAdapter({"id": "x", "name": "X", "stages": [{"key": "draft", "produces": ["out.mp4"]}]}, tmp_path, {})
    st = a.native_stages({})[0]
    p = a.stage_prompt(st, tmp_path / "v_1", {}, feedback="Make it shorter", resume=True)
    assert p.startswith("SPACESTUDIO_STAGE=draft VIDEO=v_1 ") and "Make it shorter" in p and "interrupted" in p


# ---------------------------------------------------------------- presets
@need_clones
def test_presets(registry):
    p = get_presets(registry)
    ids = [v["id"] for v in p["video_types"]]
    assert ids == ["cinematic-documentary", "educational-explainer", "science-technology", "space-documentary",
                   "historical-documentary", "short-form-reel", "youtube-long-form", "product-founder-story",
                   "news-style-explainer", "story-driven", "custom"]
    space = next(v for v in p["video_types"] if v["id"] == "space-documentary")
    assert space["engine_suggestions"]["cinematic-skill"]["looks"] == ["night"]
    assert "satellite-briefing" in space["engine_suggestions"]["lemo-opuscar"]["styles"]
    assert "dark-documentary" in space["engine_suggestions"]["lemo-opuscar"]["styles"]  # Radar Sweep Documentary
    assert space["engine_suggestions"]["openmontage"]["pipelines"] == ["documentary-montage"]
    assert space["defaults"]["engine_options"]["cinematic-skill"] == {"look": "night"}
    # every suggested option exists in the loaded engine options
    for v in p["video_types"]:
        for eid, kinds in v["engine_suggestions"].items():
            for kind, vals in kinds.items():
                have = {o["id"] for o in p["engine_options"][eid][kind]}
                assert set(vals) <= have, (v["id"], eid, kind)
    assert {a["id"]: a["resolution"] for a in p["aspect_ratios"]} == {
        "16:9": "1920x1080", "9:16": "1080x1920", "1:1": "1080x1080", "4:5": "1080x1350"}
    assert all(pl["aspect_ratio"] in {"16:9", "9:16", "1:1", "4:5"} for pl in p["platforms"])
    gen = next(v for v in p["voiceover"] if v["id"] == "generated")
    assert set(gen["engines"]) == {"cinematic-skill", "lemo-opuscar", "openmontage"}
    assert len([s for s in p["visual_styles"] if s["engine"] == "lemo-opuscar"]) == 99
    assert set(p["engine_options"]) == {"cinematic-skill", "lemo-opuscar", "openmontage"}
