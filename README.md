# RetroHost

> Self-hosted retro game streaming over WebRTC — no client install required. Open your browser, pick a game, play.

RetroHost turns a Raspberry Pi or an older PC into a shared retro console for every screen in your home. Games run centrally on the host; TVs, phones, tablets, and computers connect using only a modern web browser — no client application, no emulator installation, no device pairing.

**⚠️ LAN-only by design.** There is no authentication. Never expose RetroHost ports to the internet or deploy on a public cloud server. Read [SECURITY.md](SECURITY.md) before use.

---

## What makes RetroHost different

RetroHost turns a Raspberry Pi, an older PC, or a modern machine with a dedicated GPU into a shared retro console that follows you from screen to screen. Start playing on your laptop, open RetroHost on your phone, tablet, or TV, click **Play here**, and continue the same running game without restarting the emulator.

The project is built around one continuous game session with one active controller. When another device takes over, the previous device releases control and returns to the library. This is intentional: RetroHost behaves like a single physical console available from any browser-enabled screen in your home.

Unlike general-purpose desktop-streaming platforms, RetroHost does not create virtual desktops or capture X11 or Wayland sessions. RetroArch runs headlessly and sends audio and video directly into the streaming pipeline. This allows RetroHost to run on lightweight Linux installations, Raspberry Pis, and older computers without a graphical desktop or physical display attached — and equally on modern hardware with NVIDIA, Intel, or AMD GPUs for lower latency encoding.

**RetroHost focuses on:**

- Moving a running game between devices with one click
- Reusing Raspberry Pis, older computers, and any x86_64 PC
- Playing from TVs, phones, tablets, and computers
- A browser-only client with nothing to install — keyboard input works immediately; a physical gamepad must be paired with the device running the browser
- Headless operation without X11 or Wayland
- An integrated ROM library with local and network storage
- Automatic hardware or software video encoding (NVENC, QSV, VAAPI, or libx264)
- Simple, single-session use on a trusted local network
- A small and understandable architecture that is easy to modify

Projects such as [Sunshine](https://github.com/LizardByte/Sunshine), [Wolf](https://github.com/games-on-whales/wolf), and [Pod Arcade](https://github.com/pod-arcade/pod-arcade) address broader streaming scenarios — remote access, multiple simultaneous sessions, virtual desktops, or general-purpose application streaming. RetroHost occupies a deliberately narrower niche and does not aim to replace them. It trades multi-user infrastructure and remote-access features for a focused home-console experience:

*One retro console. Every browser-enabled screen in your home. Pick up where you left off.*

> RetroHost does not include, distribute, or provide access to game ROMs or console BIOS files. Users are responsible for supplying their own legally obtained content. See [SECURITY.md](SECURITY.md).

---

## Table of Contents

1. [How it works](#how-it-works)
2. [Architecture & Pipeline](#architecture--pipeline)
3. [Performance Metrics](#performance-metrics)
4. [Quick Start — Docker (x86_64)](#quick-start--docker-x86_64)
5. [Raspberry Pi Setup](#raspberry-pi-setup)
6. [Configuration Reference](#configuration-reference)
7. [Adding a New Console / Core](#adding-a-new-console--core)
8. [Known Limitations](#known-limitations)
9. [Security Considerations](#security-considerations)
10. [Contributing](#contributing)
11. [Credits](#credits)
12. [License](#license)

---

## How it works

RetroHost is a **server-side emulation streaming** system. The emulator (RetroArch + libretro core) runs headless on the server and writes raw video + audio to a named pipe. FFmpeg reads that pipe, encodes H.264 + Opus, and publishes RTSP to MediaMTX, which delivers the stream to any browser via WebRTC (WHEP) — over two independent connections, one for video and one for audio (see [ARCHITECTURE.md](ARCHITECTURE.md#21-why-two-webrtc-connections-videoaudio-split)). The browser captures keyboard/gamepad input and sends it back over a WebSocket, which the server injects as a virtual input device.

No browser plugin. No client app. No JavaScript framework. Just native browser APIs: `RTCPeerConnection`, `Gamepad API`, `WebSocket`.

---

## Architecture & Pipeline

```text
┌─────────────────────────── Server (Pi or x86_64) ─────────────────────────────┐
│                                                                                  │
│  RetroArch (headless, video_driver=null)                                        │
│  writes raw video + PCM audio ──► /tmp/retrohost_av.fifo (Matroska container)  │
│                                            │                                    │
│                                            ▼                                    │
│  ffmpeg                                                                          │
│  reads FIFO, encodes H.264 (hw or sw) + Opus ──► RTSP → 127.0.0.1:8554        │
│                                            │                                    │
│                                            ▼                                    │
│  MediaMTX                                                                        │
│  receives RTSP, serves WHEP (WebRTC) ──────────────────────────────────────┐   │
│                                                                              │   │
│  virtual input device (uinput / SDL2) ◄── WebSocket /ws/input ◄────────┐   │   │
│                                                                          │   │   │
│  FastAPI (port 8000) — orchestrates all of the above                    │   │   │
│                                                                          │   │   │
└──────────────────────────────────────────────────────────────────────────┼───┼───┘
                                                                           │   │
                          Browser ─────────── WebSocket (input) ──────────┘   │
                                  ◄─────────── WebRTC video+audio ────────────┘
```

For a full component-by-component breakdown, design decisions, and replication guide, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Performance Metrics

Measured via `RTCPeerConnection.getStats()` on the client browser.

Video and audio are delivered over **two independent WebRTC (WHEP) connections** rather than two tracks on one connection — see [ARCHITECTURE.md](ARCHITECTURE.md#21-why-two-webrtc-connections-videoaudio-split) for why. The numbers below are for the **video** connection, which is what drives perceived input latency.

| Hardware | Encoder | jitter buffer delay | Notes |
| --- | --- | --- | --- |
| Raspberry Pi 3 | h264_v4l2m2m (HW) | ~16 ms | Measured after the video/audio WHEP split; audio connection buffers separately at ~89 ms, not synchronized with video |
| x86_64 + NVIDIA RTX 4050 | h264_nvenc (NVENC) | ~93 ms | Measured before the video/audio split (single connection); expected to drop similarly with the split, not yet re-measured |
| x86_64 (no GPU) | libx264 (CPU) | ~120–160 ms | Varies by CPU; functional but heavier load; measured before the video/audio split |

Input round-trip (WebSocket send → emulator reaction) is sub-millisecond on the server side; perceived input latency is dominated by the video pipeline delay above.

---

## Quick Start — Docker (x86_64)

**Requirements:** Docker (Engine on Linux, Desktop on Windows/macOS Intel). Architecture must be x86_64 — the build will fail early with a clear message on ARM.

### 1. Clone and build

```bash
git clone https://github.com/vitorfranklin/retrohost.git retrohost
cd retrohost
docker build -t retrohost .
```

The build compiles PCSX-ReARMed from source (PS1 core) — expect ~10 minutes on first build.

### 2. Run

```bash
# No GPU — works on any machine (libx264 software encoder)
docker run -d --name retrohost --privileged \
  -e HOMEGAMES_WEBRTC_HOST=<YOUR_LAN_IP> \
  -p 8000:8000 -p 8889:8889 -p 8554:8554 -p 8189:8189/udp \
  -v retrohost-data:/data retrohost

# NVIDIA GPU (NVENC) — requires nvidia-container-toolkit on host
docker run -d --name retrohost --privileged --gpus all \
  -e HOMEGAMES_WEBRTC_HOST=<YOUR_LAN_IP> \
  -p 8000:8000 -p 8889:8889 -p 8554:8554 -p 8189:8189/udp \
  -v retrohost-data:/data retrohost

# Intel / AMD GPU (VAAPI/QSV)
docker run -d --name retrohost --privileged --device /dev/dri \
  -e HOMEGAMES_WEBRTC_HOST=<YOUR_LAN_IP> \
  -p 8000:8000 -p 8889:8889 -p 8554:8554 -p 8189:8189/udp \
  -v retrohost-data:/data retrohost
```

Alternatively, use Docker Compose (see [compose.yml](compose.yml) for GPU options):

```bash
RETROHOST_WEBRTC_HOST=<YOUR_LAN_IP> docker compose up -d
```

> `HOMEGAMES_WEBRTC_HOST` must be the LAN IP of the host machine (e.g. `192.168.1.100`). Without it, WebRTC ICE candidates will advertise the container's internal IP (`172.17.x.x`) and video will not reach other devices on your network.

### 3. Configure storage and play

Open `http://<YOUR_LAN_IP>:8000` in your browser. Click **Configure storage** to point RetroHost to your ROMs (local volume mount or CIFS/Samba network share), then **Scan library**, then click **Play** on any game.

### 4. Transfer a session to another device

When a game is running, any device on the LAN can open the same URL and click **Play here** to receive the stream and take over input. The previous controller is disconnected automatically.

---

## Raspberry Pi Setup

### Prerequisites

- Raspberry Pi 3 or 4 (tested on Pi 3, kernel `6.18.34+rpt-rpi-v7`)
- Raspberry Pi OS Lite (Debian Trixie) — no desktop required
- SSH access with public key authentication
- `retroarch`, `ffmpeg`, `python3`, `git` installed (`sudo apt-get install -y retroarch ffmpeg python3-venv git`)

### 1. Clone the repository

```bash
git clone https://github.com/vitorfranklin/retrohost.git ~/retrohost
cd ~/retrohost
```

### 2. Run the setup script

```bash
bash scripts/setup.sh
```

The script is **interactive and idempotent** — it asks for your username and install directory (defaults to `~/retrohost`), then does everything automatically:

- Configures RetroArch for headless mode (no TV/monitor needed)
- Downloads MediaMTX (WebRTC server)
- Installs and enables remote input via uinput
- Creates the Python venv and installs backend dependencies
- Detects installed libretro cores and generates `config/cores.json`
- Installs and starts the `mediamtx` and `homegames` systemd services
- Optionally configures CIFS/Samba network storage
- Validates the installation with a health check

At the end it prints the URL to open in your browser and any remaining manual steps (e.g. placing ROM files).

> **Note:** If you are setting up a headless Pi for the first time, the script will add `hdmi_force_hotplug=1` to `/boot/firmware/config.txt` and ask you to reboot before streaming works.

### 3. Place your ROMs and play

```text
emulator/roms/
  ps1/
    Magic Castle/
      Magic Castle.cue
      Magic Castle.bin
  snes/
    Super Boss Gaiden/
      Super Boss Gaiden.sfc
```

> The examples above are freely distributed homebrew games you can legally download to test your setup: [Magic Castle](https://archive.org/details/magic-castle-2021-07-may) (PS1, Net Yaroze) and [Super Boss Gaiden](https://superbossgaiden.superfamicom.org/) (SNES).

Open `http://<PI_IP>:8000`, click **Scan library**, then **Play**.

### Manual setup (advanced)

If you prefer to run each step individually, the individual scripts still work:

```bash
bash scripts/setup_streaming.sh      # configure RetroArch headless mode
bash scripts/install_mediamtx.sh     # download MediaMTX binary (ARMv7)
bash scripts/setup_input.sh          # enable uinput remote input
bash scripts/setup_network_storage.sh # (optional) CIFS/Samba network storage
```

Then install the systemd services manually:

```bash
# Replace pi with your actual username
sed -i 's/YOUR_USER/pi/g' scripts/homegames.service scripts/mediamtx.service
sudo cp scripts/mediamtx.service scripts/homegames.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mediamtx homegames
curl http://localhost:8000/health
```

---

## Configuration Reference

All configuration is via environment variables. Docker defaults are in the `Dockerfile`; Pi defaults are in `backend/app/core/config.py`.

| Variable | Default | Description |
| --- | --- | --- |
| `HOMEGAMES_ROOT` | auto-detected | Root directory of the project |
| `HOMEGAMES_ENCODER` | `h264_v4l2m2m` (Pi) / `auto` (Docker) | Video encoder. `auto` probes nvenc → qsv → vaapi → libx264 |
| `HOMEGAMES_RENDER_NODE` | `/dev/dri/renderD128` | DRM render node for VAAPI/QSV encoders |
| `HOMEGAMES_WEBRTC_HOST` | *(unset)* | LAN IP to announce in WebRTC ICE candidates. **Required** for multi-device access |
| `HOMEGAMES_INPUT_PROVIDER` | `uinput` (Pi) / `sdl` (Docker) | Input backend: `uinput` (Linux udev) or `sdl` (SDL2 virtual joystick) |
| `HOMEGAMES_DB_PATH` | `backend/homegames.db` | SQLite database path |
| `HOMEGAMES_STORAGE_CONFIG` | `config/storage.json` | ROM storage config (auto-written by the API) |
| `HOMEGAMES_CIFS_CREDENTIALS` | `/etc/samba/homegames-credentials` | CIFS credential file path (chmod 600) |
| `HOMEGAMES_CIFS_VERS` | `3.0` | SMB protocol version for CIFS mounts |
| `HOMEGAMES_AUDIO_DRIVER` | `pulse` (Docker) / `alsa` (Pi) | RetroArch audio driver |

---

## Adding a New Console / Core

1. Install or compile the libretro core `.so` for the target console.
2. Add an entry to `config/cores.json`:

   ```json
   {
     "ps1": "/path/to/pcsx_rearmed_libretro.so",
     "snes": "/path/to/bsnes_mercury_performance_libretro.so",
     "gba": "/path/to/mgba_libretro.so"
   }
   ```

3. Add the valid ROM extensions to `VALID_ROM_EXTENSIONS` in `backend/app/core/config.py`:

   ```python
   "gba": {".gba"},
   ```

4. Place ROMs under `emulator/roms/gba/` and run **Scan library** in the UI.

No code changes to the backend logic are needed — `POST /play` resolves the core from `cores.json` at runtime.

---

## Known Limitations

| Limitation | Status |
| --- | --- |
| **Single user / one game at a time** | By design for home use. No multi-session support. |
| **x86_64 only (Docker)** | The Docker image downloads a `linux_amd64` MediaMTX binary and is validated on x86_64. ARM64 / Mac M-series not supported (build fails early with a clear message). |
| **PS2 (PCSX2) not supported yet** | `video_driver=null` is insufficient for PS2 — it requires real OpenGL/Vulkan (Xvfb or EGL headless). Planned. |
| **Safari / iOS not tested** | Tested on Chrome/Firefox (Android + desktop). WebRTC WHEP support in Safari is not validated. |
| **VAAPI/QSV not tested on real hardware** | Implemented and auto-detected; fallback to `libx264` is guaranteed. Not validated on a real Intel/AMD machine. |
| **No authentication** | All API routes and WebSocket are open. Use only on a trusted LAN. |
| **`--privileged` required for CIFS** | The Docker container requires `--privileged` to run `mount -t cifs`. If you don't use network storage, this could be reduced to `--cap-add SYS_ADMIN` (not yet implemented). |
| **Gamepad button mapping assumes Xbox-style** | `GAMEPAD_BUTTON_TO_KEY` in `frontend/app.js` maps physical button 0 → RetroPad B (Xbox convention). If buttons appear swapped with your controller, swap `"a"` and `"b"` in that map. |
| **Gamepad must be paired with the device running the browser** | RetroHost receives input from the Gamepad API of whichever browser is in control. If you transfer the session to a TV, any physical controller must already be paired with the TV (via Bluetooth or USB) — RetroHost cannot relay a gamepad from one device to another. Keyboard input from the controlling browser always works without pairing. |

---

## Security Considerations

RetroHost is a **home project** designed to run on a **trusted local network**. It deliberately prioritizes simplicity over hardened security. Please read [SECURITY.md](SECURITY.md) in full before use.

**TL;DR:**

- **Never expose to the internet.** No authentication exists on any route.
- **Never deploy on a public cloud server** (AWS, GCP, VPS, etc.).
- If you need remote access from outside your home, use a **VPN** to reach your LAN.
- The Docker container runs with `--privileged` — do not run untrusted images with this flag.
- RetroHost does **not** provide BIOS or ROM files. You must supply your own legally obtained copies.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for environment setup, coding conventions, and how to add support for new consoles.

Bug reports and pull requests are welcome. Please open an issue before submitting large changes so we can discuss the approach first.

---

## Credits

RetroHost is glue code: Python orchestration over a set of excellent open source projects that do the real work.

| Project | Role |
| --- | --- |
| [RetroArch](https://www.retroarch.com/) / [libretro](https://www.libretro.com/) | Headless emulation frontend |
| [PCSX-ReARMed](https://github.com/libretro/pcsx_rearmed) | PS1 libretro core (compiled from source) |
| [FFmpeg](https://ffmpeg.org/) | Video/audio encoding pipeline |
| [MediaMTX](https://github.com/bluenviron/mediamtx) | RTSP→WebRTC/WHEP server |
| [FastAPI](https://fastapi.tiangolo.com/) | REST API + WebSocket backend |
| [python-evdev](https://github.com/gvalkov/python-evdev) | Virtual input device (uinput) |

---

## License

[MIT](LICENSE) — free to use, modify, and redistribute, including commercially, provided the copyright notice is retained.

Note: RetroArch and most libretro cores are GPL-licensed. RetroHost invokes them as external processes (`subprocess`) and does not link against them — the MIT license applies to RetroHost's own code only.
