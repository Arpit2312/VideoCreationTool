# SpaceStudio

A local AI video-production studio that uses **Claude Code as the orchestrator** to make films with three open-source engines, working together on the same video:

| Engine | What it contributes |
|---|---|
| [cinematic-skill](https://github.com/Moh4696/cinematic-skill) (MIT) | Code-drawn documentary films (Skia). Licence-checked Wikimedia images. Word-synced hits. |
| [lemo-opuscar](https://github.com/lemomo-ai/lemo-opuscar) (code MIT, docs CC BY-NC 4.0) | 99 code-rendered film styles, offline Kokoro voice, generated scores |
| [OpenMontage](https://github.com/OpenMontageInc/OpenMontage) (AGPL-3.0) | 13 production pipelines, real-footage montage from open archives, Remotion/HyperFrames composition |

The project history is in `docs/PHASE1_AUDIT.md` (repository audit) and `docs/PHASE2_SPEC.md` (binding technical spec).

## How the engines collaborate

In **collaborative mode** (the default), one video runs through a single stage graph:

1. **SpaceStudio + Claude** handle research, fact-check, concept, script, storyboard and asset plan. These run in one resumed Claude session and use Claude subagents (researcher, fact-checker, scriptwriter, …).
2. **The storyboard splits the film into segments** and assigns each one to the engine that suits it, with a reason. For example, a cinematic-skill evidence wall for the history part and a lemo "Satellite Briefing" style for the orbital part.
3. **Engines do the work they're best at.**
   - Archive images: cinematic `fetch_commons.py`, which refuses non-free files.
   - Narration: lemo Kokoro, OpenMontage Piper, or your own recording.
   - Segment renders: each in its own Claude Code session.
4. **SpaceStudio assembles** the segments onto the narration timeline with FFmpeg. It then mixes to −14 LUFS, writes captions, runs QA and exports with merged credits.

Engines exchange only the neutral interchange files (`backend/spacestudio/interchange/schema.py`). In **native mode**, a single engine runs its own pipeline end to end.

Approval gates:

- concept, script, storyboard, asset plan, first preview and final export
- plus any gates an engine requires itself (e.g. OpenMontage manifest gates)

They're never bypassed. *Request changes* sends your feedback back into the same Claude session and keeps the previous versions.

## Requirements

Windows 10/11, Linux or macOS. Install these first:

- Python 3.11–3.13
- Node 22.23+ (or 24.22+)
- Git (on Windows, Git for Windows, which includes Git Bash)
- FFmpeg 7+
- [Claude Code](https://claude.com/claude-code), signed in

Optional: espeak-ng or Piper for free voices, and a GPU for lemo-opuscar's 3D/WebGL styles.

## Run on Windows (no WSL needed)

In PowerShell, install anything missing. Skip what you already have; open a new terminal afterwards.

```bash
winget install Git.Git Python.Python.3.12 OpenJS.NodeJS.LTS Gyan.FFmpeg
```

Update Claude Code. SpaceStudio was tested with 2.1.332:

```bash
claude update
```

Get SpaceStudio:

```bash
git clone -b claude/determined-cannon-396fzp https://github.com/Arpit2312/VideoCreationTool.git $HOME\VideoCreationTool
```

Get the three engine repos:

```bash
mkdir $HOME\.spacestudio\engines -Force; cd $HOME\.spacestudio\engines; git clone https://github.com/Moh4696/cinematic-skill.git; git clone https://github.com/lemomo-ai/lemo-opuscar.git; git clone https://github.com/OpenMontageInc/OpenMontage.git
```

Start SpaceStudio:

```bash
cd $HOME\VideoCreationTool; powershell -ExecutionPolicy Bypass -File run.ps1
```

The browser opens at http://127.0.0.1:8000. On Windows, Claude Code and the engines' shell scripts run in Git Bash. SpaceStudio finds it automatically, or you can set `CLAUDE_CODE_GIT_BASH_PATH`.

## Run on Linux / macOS / WSL

```bash
mkdir -p ~/.spacestudio/engines && cd ~/.spacestudio/engines && git clone https://github.com/Moh4696/cinematic-skill.git && git clone https://github.com/lemomo-ai/lemo-opuscar.git && git clone https://github.com/OpenMontageInc/OpenMontage.git
```

```bash
cd /path/to/VideoCreationTool && ./run.sh
```

Then open http://127.0.0.1:8000.

## First steps in the app

**Repository Health** lists what each engine is missing and the setup command each repository documents. A command runs only after you confirm it in the UI. Start with cinematic-skill, which is the quickest to set up. Then use **New Video**.

## Zero-cost mode

On by default. It covers external services only:

- Paid provider keys are stripped from every engine process.
- OpenMontage jobs are blocked while its `.env` holds paid keys.
- Any recorded spend fails the stage.
- Options that need paid services are hidden.

**Claude Code usage is not free.** It uses your Claude plan or API key. You can set a per-stage cap (`max_claude_budget_usd`).

## Batch production

**Batch Studio** takes many ideas at once: typed in, or imported from CSV/TXT. You can set shared settings and override them per video.

- A persistent queue runs at most `max_claude_sessions` Claude sessions (default 2) and `max_render_jobs` renders (default 1) at a time.
- Each video has an isolated workspace under `~/.spacestudio/data/projects/<video-id>/`.
- Jobs survive restarts; interrupted stages resume.

## Adding more repositories

Every engine is described by an `engines/<id>/engine.yaml` manifest, plus an adapter.

- To add a new repo: **Repository Health → Add repository** starts a Claude Code onboarding job. It reads the repo and drafts a manifest (capabilities, commands, stages, costs, health checks).
- Nothing runs until you review and approve the manifest.
- Repos that follow the agent-skill pattern work with the generic manifest-driven adapter. Others need a small adapter class like `backend/spacestudio/engines/cinematic.py`.

## Development

```bash
cd backend && .venv/bin/pytest -q
```

```bash
cd frontend && npm test -- --run && npm run build
```

```bash
SPACESTUDIO_CLAUDE_BIN=$PWD/backend/tests/fake_claude.py ./run.sh
```

The last command runs the app with a **simulated** Claude CLI (no tokens). Every run is then labelled SIMULATED in the UI.

## Licensing

SpaceStudio runs OpenMontage as a separate program and only reads its JSON output files. It doesn't import or copy its AGPL code. It doesn't bundle lemo-opuscar's CC BY-NC docs either; it uses your own clone. Films you make are yours. Credits for any third-party assets are collected into each export's `credits.md`.
