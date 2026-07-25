"""Central configuration for RetroHost.

All paths derive from PROJECT_ROOT, which by default assumes the standard
Raspberry Pi layout (/home/YOUR_USER/retrohost). Can be overridden by the
HOMEGAMES_ROOT environment variable to allow local execution (Windows/dev).
"""
from __future__ import annotations

import os
from pathlib import Path


def _default_root() -> Path:
    env_root = os.environ.get("HOMEGAMES_ROOT")
    if env_root:
        return Path(env_root).resolve()
    # backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> root
    return Path(__file__).resolve().parents[3]


class Settings:
    PROJECT_ROOT: Path = _default_root()

    BACKEND_DIR: Path = PROJECT_ROOT / "backend"
    FRONTEND_DIR: Path = PROJECT_ROOT / "frontend"
    EMULATOR_DIR: Path = PROJECT_ROOT / "emulator"
    ROMS_DIR: Path = EMULATOR_DIR / "roms"
    BIOS_DIR: Path = EMULATOR_DIR / "bios"
    CORES_DIR: Path = EMULATOR_DIR / "cores"
    SAVES_DIR: Path = EMULATOR_DIR / "saves"
    STATES_DIR: Path = EMULATOR_DIR / "states"
    CONFIG_DIR: Path = PROJECT_ROOT / "config"
    LOGS_DIR: Path = PROJECT_ROOT / "logs"

    CORES_CONFIG_FILE: Path = CONFIG_DIR / "cores.json"
    SETTINGS_CONFIG_FILE: Path = CONFIG_DIR / "settings.json"
    # storage.json: default in config/ (Pi). In the container, HOMEGAMES_STORAGE_CONFIG
    # points to /data (volume) to survive container restarts, and the entrypoint
    # remounts CIFS on boot if mode=cifs. See storage.py / entrypoint.
    STORAGE_CONFIG_FILE: Path = Path(
        os.environ.get("HOMEGAMES_STORAGE_CONFIG", str(CONFIG_DIR / "storage.json"))
    )
    RECORDCONFIG_PATH: Path = CONFIG_DIR / "retroarch" / "record_raw_av.cfg"

    # System directory for RetroArch (where BIOS files etc. live). Empty = Pi's
    # default behavior (RetroArch uses its own default). In the container the
    # entrypoint exports HOMEGAMES_SYSTEM_DIR and the driver copies the PS1 BIOS
    # there at launch (the Beetle PSX core requires a BIOS). See drivers/retroarch.py.
    SYSTEM_DIR: str = os.environ.get("HOMEGAMES_SYSTEM_DIR", "")

    # On container WSL2 there is no udevd to create /dev/input/eventN when uinput
    # registers the virtual keyboard. =1 makes UInputKeyboardProvider create the node
    # via mknod (reading major:minor from sysfs). On the Pi this stays 0 (the system
    # udev already creates it).
    MKNOD_INPUT: bool = os.environ.get("HOMEGAMES_MKNOD_INPUT", "0") == "1"

    # Input provider: "uinput" (Pi, virtual keyboard via udev) or "sdl"
    # (container/WSL2, virtual SDL2 joystick inside RetroArch via LD_PRELOAD —
    # see container/sdl_input_preload.c and app/input/sdl_gamepad.py).
    INPUT_PROVIDER: str = os.environ.get("HOMEGAMES_INPUT_PROVIDER", "uinput")
    INPUT_SOCK_PATH: str = os.environ.get("HOMEGAMES_INPUT_SOCK", "/tmp/homegames_input.sock")
    # Path to the SDL input LD_PRELOAD (empty = don't use). In the container the
    # entrypoint/Dockerfile points to the compiled .so; RetroArchDriver injects
    # it into the RetroArch environment when INPUT_PROVIDER=sdl.
    INPUT_SDL_PRELOAD: str = os.environ.get("HOMEGAMES_SDL_PRELOAD", "")

    # ROM storage via network (CIFS/Samba) — see app/services/storage.py.
    # CIFS_CREDENTIALS_PATH: in the container it goes to /data (volume) via env, so
    # the entrypoint can remount on boot. On the Pi, it stays in /etc/samba (root-only).
    CIFS_CREDENTIALS_PATH: Path = Path(
        os.environ.get("HOMEGAMES_CIFS_CREDENTIALS", "/etc/samba/homegames-credentials")
    )
    CIFS_MOUNT_POINT: Path = Path("/mnt/homegames-roms")
    # SMB protocol version for mount -t cifs. Default 3.0 (modern, secure,
    # works on newer kernels like WSL2). Older kernels/servers that only
    # speak SMB1 may need 2.0/1.0 via HOMEGAMES_CIFS_VERS. The WSL2 kernel
    # (6.6) rejects vers=2.0 with mount error(22) for some servers.
    CIFS_VERS: str = os.environ.get("HOMEGAMES_CIFS_VERS", "3.0")

    STREAMING_FIFO_PATH: Path = Path("/tmp/homegames_av.fifo")
    MEDIAMTX_RTSP_URL: str = "rtsp://127.0.0.1:8554/live"
    # ffmpeg video encoder. Default h264_v4l2m2m (Raspberry Pi hardware encoder).
    # The x86 container sets HOMEGAMES_ENCODER=auto, which auto-detects the best
    # available encoder (nvenc > qsv > vaapi > libx264) — GPU-agnostic image.
    # An explicit value forces that encoder (override/debug).
    # See app/streaming/encoder_detect.py and encoder_profiles.py.
    VIDEO_ENCODER: str = os.environ.get("HOMEGAMES_ENCODER", "h264_v4l2m2m")
    # DRM render node for VAAPI/QSV (Intel/AMD GPU). Default /dev/dri/renderD128;
    # machines with multiple GPUs may need renderD129 etc.
    RENDER_NODE: str = os.environ.get("HOMEGAMES_RENDER_NODE", "/dev/dri/renderD128")
    MEDIAMTX_WHEP_PORT: int = 8889
    MEDIAMTX_WHEP_PATH: str = "/live/whep"
    MEDIAMTX_API_URL: str = "http://127.0.0.1:9997/v3/paths/get/live"

    # Default: backend/homegames.db (Pi layout). In the container, HOMEGAMES_DB_PATH
    # points to a mounted data directory as a volume, separate from the code —
    # see scripts/docker-entrypoint.sh / docker-run.sh.
    DATABASE_PATH: Path = Path(os.environ.get("HOMEGAMES_DB_PATH", str(BACKEND_DIR / "homegames.db")))
    DATABASE_URL: str = f"sqlite:///{DATABASE_PATH}"

    # Valid ROM extensions (playable game) per console.
    VALID_ROM_EXTENSIONS: dict[str, set[str]] = {
        "ps1": {".cue", ".chd", ".pbp"},
        "snes": {".sfc", ".smc"},
    }

    HOST: str = "0.0.0.0"
    PORT: int = 8000


settings = Settings()
