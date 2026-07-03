# RetroHost — Architecture & Design Decisions

This document describes the full system architecture, explains why each component exists, and provides a step-by-step replication guide. For daily usage and quick start, see [README.md](README.md).

## What it is (and what it is not)

RetroHost is a **server-side emulation streaming** system. The emulator runs on the server; the browser receives a compressed video stream.

This is explicitly **not** RetroArch WebPlayer (the official Emscripten/WASM build that runs the emulator entirely inside the browser). The difference matters:

| | RetroArch WebPlayer | RetroHost |
| --- | --- | --- |
| Where the emulator runs | Inside the browser (WASM) | On the server (native binary) |
| What the browser receives | ROM + `.wasm` core, runs locally | Compressed H.264+Opus via WebRTC |
| Client requirement | Browser must handle heavy WASM | Any modern browser decodes video |
| Input path | JS reads keyboard → emulator in memory | Browser → WebSocket → server → virtual input device → emulator |

Same principle as Steam Link, Moonlight, or Stadia: capture, encode, transmit, decode, display. The heavy lifting (emulation) stays on the server; the client only decodes video.

---

## Full pipeline diagram

```text
┌─────────────────────────── Server (Pi or x86_64) ─────────────────────────────┐
│                                                                                  │
│  RetroArch (headless)                                                           │
│  video_driver=null, input_driver=udev, audio_driver=alsa                        │
│  writes raw video + PCM audio ──► /tmp/retrohost_av.fifo (Matroska/MKV)        │
│                                            │                                    │
│                                            ▼                                    │
│  ffmpeg                                                                          │
│  reads FIFO ──► H.264 (hw encoder) + Opus ──► RTSP → 127.0.0.1:8554           │
│                                            │                                    │
│                                            ▼                                    │
│  MediaMTX (always-on service)                                                   │
│  receives RTSP ──► serves WHEP (WebRTC) ──────────────────────────────────┐   │
│                                                                            │   │
│  virtual input (uinput / SDL2) ◄── WebSocket /ws/input ◄──────────────┐  │   │
│                                                                         │  │   │
│  FastAPI (port 8000)                                                    │  │   │
│  orchestrates RetroArch + ffmpeg + input, serves frontend HTML/JS       │  │   │
│                                                                         │  │   │
└─────────────────────────────────────────────────────────────────────────┼──┼───┘
                                                                          │  │
                  Browser ───── WebSocket (input) ────────────────────────┘  │
                          ◄──── WebRTC video (WHEP) ────────────────────────┘
                          ◄──── WebRTC audio (WHEP, separate connection) ────┘
```

Video and audio are fetched over **two independent WHEP requests / `RTCPeerConnection`s**, not two tracks on one connection — see "Why two WebRTC connections" below.

---

## Components

### 1. RetroArch (headless emulation)

- Runs with `video_driver = "null"` — no display or GPU shader required. The default fallback renderer (GLES/VC4 on Pi) fails due to GLSL incompatibilities; null avoids this entirely.
- Uses `input_driver = "udev"` — no X11/Wayland session required.
- Uses `audio_driver = "alsa"` (Pi) or `pulse` (Docker) — without a functioning audio clock, RetroArch's `audio_sync` cannot throttle the core, causing ~4x fast-forward. This was a real bug observed and fixed.
- Writes video + audio to a **named pipe (FIFO)** using `--recordconfig`, not to a file. The recordconfig uses Matroska container (`format = "matroska"`) with `rawvideo` + `pcm_s16le`. MKV was chosen over `yuv4mpegpipe` because MKV is streamable via pipe (EBML doesn't require a seek to finalize) and supports audio in the same container.
- **Why not encode inside RetroArch?** The Pi's hardware encoder (`h264_v4l2m2m`, driver `bcm2835-codec`) fails inside RetroArch with `Failed to set timeperframe` — that driver doesn't implement the `VIDIOC_S_PARM` ioctl RetroArch calls. Raw output + external ffmpeg process is the workaround.

### 2. ffmpeg (encoding)

- Reads the FIFO, encodes video via hardware encoder and audio via Opus.
- **Why Opus and not AAC?** MediaMTX silently drops AAC tracks when publishing via WHEP (`WAR [WebRTC] skipping track 2 (MPEG-4 Audio)`) because browsers do not natively support AAC in WebRTC — only Opus and G.711.
- `-bf 0` is set on **all** encoder profiles. WebRTC rejects H.264 streams that contain B-frames ("WebRTC doesn't support H264 streams with B-frames"). `-tune zerolatency` in libx264 is supposed to disable them but is not guaranteed across all builds — explicit `-bf 0` is the reliable fix.
- `-fflags nobuffer -flags low_delay` and `-fps_mode passthrough` reduce buffering latency.
- GOP of 15 frames (`-g 15`) was validated to reduce jitter buffer from ~200 ms to ~100 ms on the Pi.
- `-af aresample=async=1000:first_pts=0` resamples audio to a constant rate anchored at PTS 0, compensating for drift introduced by the FIFO/Matroska path now that audio and video are consumed by two independent WebRTC connections (see below) with no shared clock to resync them.
- Implemented in `backend/app/streaming/ffmpeg_webrtc.py` (`FFmpegStreamingProvider`).

### 2.1 Why two WebRTC connections (video/audio split)

Initially, a single `RTCPeerConnection` carried both the video and audio tracks (two transceivers on one connection), matching how WHEP is normally used. Measured with `RTCPeerConnection.getStats()` on the Pi 3, this configuration showed a **video jitter buffer of ~289-459 ms** — far above what the encoder alone accounts for (see "Known hardware limits" below).

Root cause: when audio and video share one `RTCPeerConnection`, the browser's jitter buffer logic holds the video queue back to preserve lip-sync with the audio track's own (larger) buffering requirements — inflating video latency to match audio, not the other way around.

**Fix**: the frontend opens two independent WHEP requests against the same `whep_url`, each negotiating a single `recvonly` transceiver (`connectWhepTrack("video", ...)` and `connectWhepTrack("audio", ...)` in `frontend/app.js`), resulting in two separate `RTCPeerConnection`s and two `<video>`/`<audio>` elements. Measured result on the same Pi 3 hardware:

| Connection | buffer_ms | jitter_ms |
| --- | --- | --- |
| video | ~16 | ~7 |
| audio | ~89 | ~10 |

Video latency dropped roughly 94% (289-459 ms → ~16 ms). Audio keeps a higher buffer (expected — audio glitches are more perceptible than a few extra ms of buffering), and the two connections are no longer synchronized by the browser. The ~70 ms gap between video and audio buffers is below the ITU-R BT.1359 perceptibility threshold (~125 ms) and was not noticeable in manual play-testing, but there is no hard guarantee against drift over a long play session — if this becomes noticeable, periodic resync (e.g. comparing `HTMLMediaElement.currentTime` between the two elements) would be the next step.

Audio failing to connect (`connectWhepTrack("audio", ...)` throwing) is treated as non-fatal — the game keeps running with video only rather than failing `/play` entirely.

### 3. MediaMTX (WebRTC/WHEP server)

- Receives RTSP from ffmpeg, exposes the stream via WHEP for browsers.
- Runs as an always-on process (`mediamtx.service`), independent of game sessions.
- Its control API (`localhost:9997`) is polled by the backend to confirm the stream is live before returning `200` to `/play`. Without this, the browser connects via WHEP before ffmpeg finishes publishing → black screen (real race condition observed and fixed via `PlayerService._wait_stream_ready()`).
- `webrtcAdditionalHosts` in `mediamtx.yml` is injected at container startup with the host's LAN IP (`HOMEGAMES_WEBRTC_HOST`). Without it, MediaMTX announces the container's internal IP (`172.17.x.x`) in ICE candidates, unreachable by other LAN devices.

### 4. Input subsystem

The browser captures keyboard (`keydown`/`keyup`) and gamepad (`navigator.getGamepads()` polled via `requestAnimationFrame`) and sends only state-change events (`{"key": "right", "pressed": true}`) over a WebSocket to `/ws/input`.

Two backend implementations, selected by `HOMEGAMES_INPUT_PROVIDER`:

**`uinput` (Raspberry Pi):**

- Uses `python3-evdev` (installed via `apt`, not pip — compiling the C extension on Pi 3 would OOM/timeout) to create a virtual keyboard device via `/dev/uinput`.
- RetroArch reads it exactly like a physical USB keyboard because it already uses `input_driver = "udev"`.
- Design choice: virtual keyboard, not virtual gamepad — RetroArch already has default keyboard binds for the RetroPad (`input_player1_a = "x"`, `input_player1_up = "up"`, etc.), so no RetroArch configuration is needed.
- `release_all()` is called when the WebSocket disconnects to prevent stuck keys if the tab is closed mid-press.

**`sdl` (Docker/WSL2):**

- WSL2 has no `udevd` — `libudev` cannot enumerate devices even when `uinput` creates a device node. Exhaustively tested: keyboard uinput, gamepad uinput, manual `mknod` of `/dev/input/eventN`, manual libudev metadata, running `udevd` — all fail in the same way.
- Solution: `container/sdl_input_preload.c`, a shared library that intercepts `SDL_Init` in the RetroArch process via `LD_PRELOAD` and creates a virtual SDL2 joystick inside it. No udev dependency.
- A UNIX DGRAM socket (`/tmp/retrohost_input.sock`) receives `"b <btn_index> <0|1>"` messages from `backend/app/input/sdl_gamepad.py`.
- D-pad is exposed as **buttons** b11–b14 (not hat), confirmed via `SDL_GameControllerMappingForDeviceIndex`.

**Multi-device / session transfer:**

- Only one WebSocket client is active at a time. When a new client connects, the previous one is closed with code `4000` ("control taken"). The previous client's browser detects this code and returns to the idle view without stopping the game.

### 5. ROM storage

- Default: `emulator/roms/` on the server's local disk.
- Optional: CIFS/Samba network share (e.g. a USB drive shared by an OpenWrt router). Mounted via `sudo mount -t cifs` into a fixed mount point (`/mnt/homegames-roms`).
- Privilege scope is minimized: a sudoers rule (`config/sudoers/homegames-mount`) allows only the exact `mount`/`umount` command to the fixed destination, not unrestricted sudo.
- Samba credentials are written to a `chmod 600` file, never stored in `config/storage.json` or returned by `GET /config`.
- Switching storage modes clears the game catalog and re-scans — `launch_file` is stored as an absolute path; if the root changes, old entries become orphaned. Relative-path migration would be over-engineering for a single-user home project.

### 6. FastAPI backend

- `uvicorn` without `[standard]` — avoids compiling `uvloop` on Pi 3 (OOM risk on 1 GB RAM).
- `wsproto` for WebSocket support — pure Python, no build step. `websockets` has no ARMv7 wheel.
- All emulator/streaming/input logic goes through interfaces (`EmulatorDriver`, `StreamingProvider`, `InputProvider`) injected into `PlayerService`. The API never calls RetroArch or ffmpeg directly.
- Frontend is plain HTML/CSS/JS with no build step, served as static files by FastAPI.

---

## Session lifecycle

```text
POST /play
  │
  ├─ InputProvider.connect()        # create virtual input device BEFORE RetroArch starts
  ├─ RetroArchDriver.launch()       # create FIFO, start RetroArch headless
  ├─ sleep(2s)                      # wait for RetroArch to open the FIFO for writing
  ├─ FFmpegStreamingProvider.start() # start ffmpeg reading FIFO, publishing RTSP
  └─ _wait_stream_ready()           # poll MediaMTX API until stream is live (timeout 10s)
     └─ return 200 OK

Browser: connectWhep() + connectInputSocket()
  ├─ RTCPeerConnection → WHEP → video+audio in <video>
  └─ WebSocket /ws/input → input events → emulator

POST /stop
  ├─ InputProvider.disconnect()
  ├─ FFmpegStreamingProvider.stop()
  └─ RetroArchDriver.stop()
```

---

## Docker mode (GPU-agnostic)

The Docker image (`Dockerfile`) packages the full pipeline into a single container that runs on any x86_64 machine: Windows (Docker Desktop + WSL2), Linux native; NVIDIA, Intel, AMD, or CPU-only.

**Encoder auto-detection** runs at container startup via real encode-probes:

```bash
ffmpeg -f lavfi -i testsrc=size=256x240:rate=30 -t 0.3 -c:v <encoder> -f null -
```

Exit code 0 = encoder works (tests the full path: library + driver + device). Probes run in order: `h264_nvenc` → `h264_qsv` → `h264_vaapi` → `libx264`. The result is cached for the container's lifetime.

**GPU flags are optional:**

| Situation | Flag |
| --- | --- |
| NVIDIA (NVENC) | `--gpus all` (requires nvidia-container-toolkit on host) |
| Intel/AMD (VAAPI/QSV) | `--device /dev/dri` |
| No GPU | _(no flag)_ — falls back to libx264 |

**What changes vs Raspberry Pi:**

| | Raspberry Pi | Docker |
| --- | --- | --- |
| Video encoder | `h264_v4l2m2m` | auto-detected |
| Base | Raspberry Pi OS | `ubuntu:24.04` |
| Input | uinput/udev | SDL2 virtual joystick via LD_PRELOAD |
| Audio clock | ALSA | PulseAudio null-sink (no `/dev/snd` needed) |
| Process supervision | systemd | bash supervisor in `docker-entrypoint.sh` |

**What does NOT change:** the entire backend (`drivers/`, `services/`, `api/`), the frontend, and the conceptual pipeline. The architecture was decoupled from the start — the driver calls `retroarch` from `$PATH`; all paths derive from `HOMEGAMES_ROOT`. Pi and Docker share the same codebase.

---

## Project structure

```text
retrohost/
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py                    # FastAPI app, mounts frontend + API
│       ├── api/
│       │   ├── routes.py              # REST endpoints
│       │   └── ws_input.py            # WebSocket /ws/input
│       ├── core/config.py             # all settings, env var resolution
│       ├── db/                        # SQLAlchemy engine + session
│       ├── models/game.py             # games table
│       ├── schemas/                   # Pydantic request/response schemas
│       ├── services/
│       │   ├── player.py              # orchestrates play/stop/status
│       │   ├── scanner.py             # scans ROM storage → SQLite
│       │   └── storage.py             # local vs CIFS, mount/unmount
│       ├── drivers/
│       │   ├── base.py                # EmulatorDriver interface
│       │   └── retroarch.py           # RetroArch headless via subprocess
│       ├── streaming/
│       │   ├── base.py                # StreamingProvider interface
│       │   ├── ffmpeg_webrtc.py       # ffmpeg FIFO→RTSP
│       │   ├── encoder_profiles.py    # per-encoder ffmpeg arg sets
│       │   └── encoder_detect.py      # encode-probe auto-detection
│       └── input/
│           ├── base.py                # InputProvider interface
│           ├── keymap.py              # logical button → evdev keycode
│           ├── sdl_keymap.py          # logical button → SDL button index
│           ├── uinput_keyboard.py     # uinput virtual keyboard (Pi)
│           └── sdl_gamepad.py         # SDL2 virtual joystick via socket (Docker)
├── frontend/                          # plain HTML/CSS/JS, no build step
├── container/
│   └── sdl_input_preload.c            # LD_PRELOAD SDL2 virtual joystick
├── config/
│   ├── cores.json                     # console → core .so path mapping
│   ├── mediamtx.yml                   # MediaMTX config (RTSP, WHEP, API)
│   ├── retroarch/record_raw_av.cfg    # RetroArch FIFO recordconfig
│   ├── sudoers/homegames-mount        # minimal-scope CIFS sudoers rule
│   └── udev/99-uinput.rules           # grants /dev/uinput to "input" group
├── emulator/
│   ├── bios/                          # BIOS files (not versioned)
│   ├── cores/                         # libretro .so files (not versioned)
│   ├── roms/                          # ROM files (not versioned)
│   ├── saves/
│   └── states/
├── scripts/
│   ├── docker-entrypoint.sh           # container supervisor + setup
│   ├── deploy.sh                      # rsync local → Pi
│   ├── install_mediamtx.sh            # downloads MediaMTX binary (ARMv7)
│   ├── setup_streaming.sh             # configures RetroArch headless (idempotent)
│   ├── setup_input.sh                 # configures uinput/udev (idempotent)
│   ├── setup_network_storage.sh       # configures CIFS sudoers (idempotent)
│   ├── homegames.service              # systemd unit (API)
│   ├── mediamtx.service               # systemd unit (MediaMTX)
│   ├── stream_pipeline.sh             # manual pipeline test (debug)
│   └── whep_test.html                 # standalone WHEP player (debug)
├── tests/
│   └── unit/                          # pytest suite (cross-platform, mocked)
├── Dockerfile
├── compose.yml                        # docker compose alternative to docker run
├── docker-run.sh                      # docker run helper (Linux)
├── docker-run-windows.sh              # docker run helper (Windows/WSL2)
├── pytest.ini
├── README.md
├── ARCHITECTURE.md                    # this file
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE
```

---

## Replication guide (Raspberry Pi, from scratch)

### Hardware / OS

- Raspberry Pi 3 or 4 (tested on Pi 3, kernel `6.18.34+rpt-rpi-v7`)
- Raspberry Pi OS Lite (Debian Trixie) — no desktop needed
- SSH access

### Step 1 — Base system

```bash
sudo apt-get update
sudo apt-get install -y retroarch ffmpeg python3-venv git
```

### Step 2 — Clone and install

```bash
git clone https://github.com/vitorfranklin/retrohost.git ~/retrohost
cd ~/retrohost/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Configure cores

Edit `config/cores.json` with the paths to your libretro core `.so` files. See [Adding a New Console / Core](README.md#adding-a-new-console--core) in the README.

### Step 4 — Run the setup script

```bash
bash scripts/setup.sh
```

The script is interactive and idempotent. It handles all remaining steps automatically:

- Configures RetroArch headless mode
- Downloads MediaMTX (auto-selects ARMv7 or ARM64 binary)
- Installs uinput remote input
- Creates Python venv and installs dependencies
- Detects installed libretro cores and generates `config/cores.json`
- Installs and starts systemd services
- Optionally configures CIFS/Samba network storage
- Runs a health check at the end

### Step 5 — Validate

```bash
curl http://localhost:8000/health
# Open http://<PI_IP>:8000 in browser
```

> **Note:** The individual scripts (`setup_streaming.sh`, `install_mediamtx.sh`, `setup_input.sh`, `setup_network_storage.sh`) still work independently for advanced users or partial re-runs.

---

## Known design trade-offs

| Trade-off | Decision | Reason |
| --- | --- | --- |
| External ffmpeg process instead of RetroArch encoder | External | Pi's `h264_v4l2m2m` fails inside RetroArch (`VIDIOC_S_PARM` not implemented) |
| Virtual keyboard instead of virtual gamepad (Pi) | Keyboard | RetroArch already has default keyboard binds; no config needed |
| `wsproto` instead of `websockets` | `wsproto` | No ARMv7 wheel for `websockets`; `wsproto` is pure Python |
| `uvicorn` without `[standard]` | No standard | Avoids compiling `uvloop` on Pi 3 (OOM/timeout risk on 1 GB RAM) |
| Absolute paths in SQLite | Absolute | Simple and correct for single-user local storage; relative paths would complicate multi-root storage switching |
| `--privileged` in Docker | Required | `mount -t cifs` needs elevated privileges; reducible to `--cap-add SYS_ADMIN` when CIFS is not used (not yet implemented) |
