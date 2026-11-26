# Build brief: parallel implementation (for the implementation agents)

Read `docs/PHASE1_AUDIT.md` (what the repos are) and `docs/PHASE2_SPEC.md` (binding spec) first.
Fixed contract files: `backend/spacestudio/{models.py, workspace.py, workflow/stages.py, engines/base.py, interchange/schema.py}`.
You may make **additive** changes to a contract file (a new field or helper) when you need to. List each one in your final report. Never rename or remove anything.

Ground rules for every agent:

- **Run no git commands.** The coordinator commits.
- Stay inside the files you own (below). If you need something from another agent's area, code against the interface written here. Don't implement it yourself, except as a tiny test stub inside your own test files.
- Python 3.11+. The venv is at `backend/.venv` (`backend/.venv/bin/pip install …` is fine; add any new dependency to `backend/pyproject.toml`). Run tests with `cd backend && .venv/bin/pytest -q`.
- **No fake behaviour presented as real.** Simulated execution exists only through `tests/fake_claude.py`, and the UI must label it.
- **Never put secrets in logs or responses.** Use `security.redact`.
- The real engine clones (read-only reference, don't modify them) are at `~/.spacestudio/engines/{OpenMontage,cinematic-skill,lemo-opuscar}`.
- The machine has ffmpeg 6.1 and ffprobe, Node 22, Python 3.13, and **no** espeak-ng/piper.

## Ownership

| Agent | Owns |
|---|---|
| **A: backend core** | `config.py, db.py, schemas.py, audit.py, security.py, events.py, main.py, api/**, workflow/{planner,orchestrator,scheduler,native}.py, claude/**, scripts/spacestudio-slot, tests/fake_claude.py`, tests for these |
| **B: engines** | `engines/{registry,generic,cinematic,lemo,openmontage,onboarding}.py`, `presets.py`, top-level `engines/<id>/engine.yaml`, tests for these |
| **C: frontend** | `frontend/**` |
| **D: media** | `assembly/**`, `qa/**`, tests for these |

## Cross-agent interfaces (implement exactly these signatures)

### Settings (A, `spacestudio/config.py`)

`get_settings() -> Settings` (pydantic model, cached; `reload_settings()` resets the cache). Fields and defaults:

- Paths and identity:
  - `app_name="SpaceStudio"`
  - `data_dir=~/.spacestudio/data` (env `SPACESTUDIO_DATA_DIR`)
  - `projects_dir` (property: `data_dir/"projects"`)
  - `db_url` (default `sqlite:///<data_dir>/spacestudio.db`)
  - `engines_dir=~/.spacestudio/engines`
  - `engine_paths: dict[str,str]={}` (per-engine overrides)
- Binaries:
  - `claude_bin="claude"` (env `SPACESTUDIO_CLAUDE_BIN`)
  - `claude_permission_mode="acceptEdits"`
  - `ffmpeg="ffmpeg"`, `ffprobe="ffprobe"`
  - `python_bin="python3"`
- Limits:
  - `max_claude_sessions=2`, `max_render_jobs=1`
  - `render_workers=2`
  - `min_free_ram_gb=2.0`
  - `stage_timeout_minutes=90`
- Defaults and environment:
  - `default_video_config: dict={}`
  - `zero_cost_default=True`
  - `allowed_free_keys: list[str]=[]` (e.g. PEXELS_API_KEY, allowed even in zero-cost mode)
  - `extra_env: dict[str,str]={}`
- Persistence: settings are saved to `<data_dir>/settings.json`, and env vars override file values.

### Engine registry (B, `spacestudio/engines/registry.py`)

```python
class Registry:
    def __init__(self, settings): ...
    def load(self) -> None                          # reads repo-root engines/*/engine.yaml (+ data_dir/engines/*/engine.yaml for onboarded)
    def all(self) -> list[EngineAdapter]
    def get(self, engine_id: str) -> EngineAdapter  # KeyError if unknown
    def manifests(self) -> list[dict]
    def paid_env_names(self) -> set[str]            # union of every manifest cost.paid_env
def get_registry() -> Registry                      # cached singleton, loaded
```

Each `engine.yaml` has at least: `id, name, description, license, repo{url, default_dir}`, `invocation{kind, plugin_dir?, skill?}`, `requires{commands, python?, node?, ffmpeg?}`, `capabilities{<Capability>: {supported, detail}}`, `options{...}`, `cost{zero_cost, paid_env[], notes}`, `concurrency{render_heavy_stages[], env{}}`, `setup[{id,title,command,cwd}]`, `allowed_tools[]`, `env{}`, `adapter` (python dotted path or `generic`).

Adapters implement `engines/base.py`. Stage keys an adapter must handle in **collaborative** mode: `assets`, `narration`, `segment:<id>` (`stage.params = {"segment": id, "engine_options": {...}, "start": s, "end": e}`). Plus every key it returns from `native_stages()`.

**Collaborative I/O contract for engine lanes** (from the spec §5). Paths are relative to the workspace and the lane cwd is `work/<engine-id>/`.

- `assets`
  - reads `assets/manifest.json` and `storyboard/storyboard.json`
  - writes the files under `assets/images|footage|music|sfx/`
  - updates each manifest item: `status`, `path`, `license`, `credit`
- `narration`
  - reads `scripts/script.json`
  - writes `narration/narration.wav` and `narration/timing.json` (NarrationTiming, one entry per script line)
- `segment:<id>`
  - reads the storyboard scenes of that segment, `narration/timing.json` (for its time window) and `assets/manifest.json`
  - writes `scenes/<id>.mp4`: picture only, config resolution and fps, duration equal to `end - start`
  - writes `scenes/<id>.json` (SegmentRender)
  - copies the engine's credits into `credits/<engine-id>-<id>.md`

### Presets (B, `spacestudio/presets.py`)

`get_presets(registry) -> dict`:

```
{video_types: [{id, name, description, engines: [ids], defaults: {...VideoConfig fields}}],
 visual_styles: [...], tones: [...], durations: [{id, name, seconds}],
 aspect_ratios: [{id, resolution}], platforms: [{id, name, aspect_ratio}],
 voiceover: [...], engine_options: {<engine_id>: {...options with zero_cost flags...}}}
```

Only list what the inspected repos support.

### Media pipeline (D, `spacestudio/assembly/pipeline.py`)

Every function takes `(ws: Path, config: VideoConfig, settings)` and returns `list[str]` (the workspace-relative files written). On failure it raises `AssemblyError(message)`, and the message is actionable.

- `preview(ws, config, settings)`: `renders/preview.mp4`, 640px wide, segments plus narration
- `mix(ws, config, settings)`: `narration/mix.wav` (narration + `assets/music/*` ducked + `assets/sfx` cues if `assets/sfx/cues.json`), loudness −14 LUFS
- `render_final(ws, config, settings)`: `renders/final.mp4`
- `captions(ws, config, settings)`: `subtitles/<lang>.srt` from `narration/timing.json`, plus burn-in into `renders/final_captioned.mp4` if `config.burn_captions`
- `export(ws, config, settings, title: str)`: `outputs/<slug>.mp4`, `.srt`, `poster.jpg`, `credits.md` (merged from `credits/*` + manifest), `outputs/manifest.json`
- `export_zip(ws, dest: Path) -> Path`: zip of the workspace excluding `work/**/node_modules`, `.spacestudio/tmp`, `**/.venv`

Missing segments get a labelled black slate; the caller is told through a warning list (`pipeline.LAST_WARNINGS` or a returned tuple; document which).

### QA (D, `spacestudio/qa/checks.py`)

`run_all(ws: Path, config: VideoConfig, stages: list[dict], settings) -> list[dict]`. Each item is `{id, name, status: passed|warning|failed|not_run, blocking: bool, detail}`. It also writes `.spacestudio/qa.json`.

### Claude runner (A, `spacestudio/claude/runner.py`)

```python
@dataclass
class RunRequest: prompt: str; cwd: Path; lane: str; log_path: Path; resume_session: str|None
    add_dirs: list[Path]; plugin_dirs: list[Path]; env: dict; allowed_tools: list[str]
    append_system_prompt: str; agents: dict|None; model: str|None; effort: str|None
    max_budget_usd: float|None; timeout_s: int
@dataclass
class RunResult: exit_code: int; session_id: str|None; model: str|None; cost_usd: float
    is_error: bool; result_text: str; permission_denials: list; log_path: Path; cancelled: bool; timed_out: bool
class ClaudeRunner:
    async def run(self, req: RunRequest, on_event: Callable[[dict], None]) -> RunResult
    def cancel(self, run_key: str) -> None
    def is_simulated(self) -> bool     # claude_bin is not a real `claude`
```

Every stage prompt starts with the machine-readable line:
`SPACESTUDIO_STAGE=<key> VIDEO=<id> WORKSPACE=<abs path> LANE=<lane>`. `tests/fake_claude.py` parses it.
