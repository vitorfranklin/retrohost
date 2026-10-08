# Contributing to RetroHost

Thank you for your interest in contributing. This document covers how to set up a local development environment, coding conventions, and how to add support for new consoles.

## Table of Contents

1. [Development environment](#development-environment)
2. [Running without Docker](#running-without-docker)
3. [Running the full pipeline manually](#running-the-full-pipeline-manually)
4. [Project conventions](#project-conventions)
5. [Adding a new console or core](#adding-a-new-console-or-core)
6. [Submitting changes](#submitting-changes)

---

## Development environment

**Requirements:**
- Python 3.11+
- Docker (for container testing)
- A Linux environment or WSL2 (the input subsystem uses Linux-only APIs)

**Clone and install:**

```bash
git clone https://github.com/vitorfranklin/retrohost.git retrohost
cd retrohost/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**On a Raspberry Pi**, use the all-in-one setup script instead:

```bash
git clone https://github.com/vitorfranklin/retrohost.git ~/retrohost
cd ~/retrohost
bash scripts/setup.sh
```

---

## Running without Docker

You can run the backend API standalone without RetroArch or ffmpeg — useful for working on API routes, the scanner, or the frontend.

```bash
cd retrohost/backend
source .venv/bin/activate
export HOMEGAMES_ROOT=$(pwd)/..
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` — the UI loads, `/health` and `/games` work. `/play` will fail unless RetroArch and MediaMTX are also running (expected).

**Testing API endpoints:**

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/scan
curl http://localhost:8000/games
curl http://localhost:8000/status
```

---

## Running the full pipeline manually

For debugging the streaming pipeline without the API:

```bash
# 1. Start MediaMTX
./bin/mediamtx config/mediamtx.yml

# 2. Run the pipeline manually (RetroArch + ffmpeg)
bash scripts/stream_pipeline.sh /path/to/core.so /path/to/rom.cue

# 3. Open scripts/whep_test.html in a browser to validate the stream
```

---

## Project conventions

**Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add GBA console support
fix: release all inputs on WebSocket disconnect
docs: update Docker quick start in README
refactor: extract encoder probe into separate module
```

**Code style:**
- Python: standard library + project dependencies only (no new heavy dependencies without discussion). Type hints on all public functions.
- JavaScript: vanilla ES2020, no frameworks, no build step.
- Shell scripts: `set -euo pipefail` at the top, idempotent where possible.

**Adding dependencies:** open an issue first. Every new Python dependency must have a wheel available for ARMv7 (Pi) or be pure Python — compiled extensions that require build tools on the Pi are a known pain point.

**Tests** — the project uses `pytest`. Unit tests live in `tests/unit/` and run on any OS (Linux-only APIs are mocked):

```bash
pip install pytest
python -m pytest
```

Run the suite before opening a PR. New code should come with tests where practical — `services/player.py` and `services/storage.py` are currently the least-covered areas and good candidates for new tests.

---

## Adding a new console or core

RetroHost is designed so that adding a new console requires no backend code changes.

**Steps:**

1. **Install or compile the libretro core** `.so` for the target console and place it in `emulator/cores/` (or any accessible path).

2. **Register the core** in `config/cores.json`:
   ```json
   {
     "ps1": "/path/to/pcsx_rearmed_libretro.so",
     "snes": "/path/to/snes9x_libretro.so",
     "gba":  "/path/to/mgba_libretro.so"
   }
   ```
   The key (`"gba"`) must match the subdirectory name under `emulator/roms/`.

3. **Register valid ROM extensions** in `backend/app/core/config.py`:
   ```python
   VALID_ROM_EXTENSIONS: dict[str, set[str]] = {
       "ps1":  {".cue", ".chd", ".pbp"},
       "snes": {".sfc", ".smc"},
       "gba":  {".gba"},           # add this
   }
   ```

4. **Place ROMs** under `emulator/roms/gba/<game-title>/` and click **Scan library** in the UI.

5. **(If the console requires BIOS)** Place the BIOS file in `emulator/bios/` and register it in `_BIOS_CONFIG` in `RetroArchDriver` (`backend/app/drivers/retroarch.py`) — `bios_names` (filenames the core expects) and `bios_subdir` (path relative to `SYSTEM_DIR`; most cores read BIOS directly from `SYSTEM_DIR`, but some, like `pcsx2_libretro`, expect a specific subdirectory).

6. **(If the core needs a real GPU context, e.g. RETRO_HW_RENDER cores like PCSX2)** Register the console in `_VIDEO_PROFILES` in `RetroArchDriver` with the `retroarch.cfg` overrides it needs (`video_driver`, `video_context_driver`, `video_gpu_record`, etc — applied per-launch via `--appendconfig`, without touching the shared `retroarch.cfg`). Consoles not listed keep the default `video_driver=null` behavior.

**Notes on PS2 (PCSX2):** solved in the `poc/ps2-support-headless-gl` branch — see `docs/ps2-poc.md`. `video_driver=null` is insufficient for PS2 (the `pcsx2_libretro` core is `RETRO_HW_RENDER` and needs a real OpenGL context); Xvfb + `video_driver=gl`/`video_context_driver=x11` + `video_gpu_record=true` resolves it, validated end-to-end (BIOS load, game recognition, shader compilation, frames reaching the FIFO) inside the container without a dedicated GPU (Mesa llvmpipe).

---

## Submitting changes

1. **Open an issue first** for any non-trivial change — describe the problem and proposed approach before writing code.
2. Fork the repository and create a branch from `main`. **Never commit directly to `main`.**
3. Name your branch after the type of change:
   ```
   feat/ps2-support
   fix/cue-path-windows
   docs/contributing-guide
   test/scanner-edge-cases
   refactor/encoder-profiles
   ```
4. Keep commits focused and follow the commit message convention above.
5. Open a pull request targeting `main` with a clear description of what changed and why.
6. For changes to the streaming pipeline or input subsystem, describe how you tested it (which hardware, which game, what you observed).

Bug reports are also welcome — please include the output of `docker logs retrohost` or `journalctl -u homegames` when reporting streaming or input issues.
