# RetroHost - GPU-agnostic container image for Linux x86_64.
#
# Mirrors the Raspberry Pi pipeline (headless RetroArch -> FIFO -> ffmpeg ->
# mediamtx -> WebRTC + input + FastAPI), but the SAME image runs on any
# machine: NVIDIA, Intel (QuickSync), AMD/Intel (VAAPI), or CPU-only (libx264).
# The encoder is auto-detected at boot (HOMEGAMES_ENCODER=auto -> nvenc > qsv >
# vaapi > libx264). See ARCHITECTURE.md ("Container mode").
#
# Base ubuntu:24.04 (generic, lightweight). For NVENC, nvidia-container-toolkit
# injects the host libnvidia-encode at runtime when `--gpus all` is passed; no
# CUDA base image is needed. For Intel/AMD, pass `--device /dev/dri`. Without
# any GPU flag, auto-detection falls back to libx264 (CPU) and starts anywhere.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Hints for nvidia-container-toolkit when it is present (`--gpus all`):
# `all` includes video capabilities (without them libnvidia-encode.so.1 is not
# mounted and NVENC fails). Harmless when there is no NVIDIA GPU/toolkit.
ENV NVIDIA_DRIVER_CAPABILITIES=all \
    NVIDIA_VISIBLE_DEVICES=all

# - retroarch + libretro cores (PS1 via beetle-psx/mednafen; SNES via snes9x).
#   Ubuntu 24.04 has no pcsx-rearmed package; libretro-beetle-psx installs the
#   mednafen_psx_libretro.so (Beetle PSX) core, which is also more accurate.
# - ffmpeg: Ubuntu 24.04 includes ALL H.264 encoders (libx264, h264_nvenc,
#   h264_qsv, h264_vaapi), so one image covers every hardware path. Auto-detect
#   chooses the best available one.
# - libva2/libva-drm2/intel-media-va-driver-non-free/mesa-va-drivers/vainfo:
#   VAAPI/QSV runtime (Intel QuickSync and AMD/Intel via Mesa). Dormant unless
#   /dev/dri is passed; enable Intel/AMD GPU encoding when it is.
#   (NVENC does not need these; it comes from the host via nvidia-container-toolkit.)
# - python3-evdev: uinput input on the Pi (the container uses SDL, but this
#   keeps the uinput provider importable).
# - cifs-utils: ROM storage over the network (CIFS/Samba).
# - pulseaudio: dummy audio clock (null-sink) INSIDE the container; without it
#   RetroArch runs in fast-forward because audio_sync needs a clock. See entrypoint.
RUN apt-get update && apt-get install -y --no-install-recommends \
        retroarch \
        libretro-beetle-psx \
        libretro-snes9x \
        ffmpeg \
        libva2 \
        libva-drm2 \
        intel-media-va-driver-non-free \
        mesa-va-drivers \
        vainfo \
        pulseaudio \
        pulseaudio-utils \
        python3 \
        python3-venv \
        python3-pip \
        python3-evdev \
        cifs-utils \
        sudo \
        procps \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# PS2 (PCSX2) - optional, off by default (INCLUDE_PS2=false): experimental
# (branch poc/ps2-support-headless-gl, see docs/ps2-poc.md), downloaded from a
# nightly build (no pinned stable version yet), and adds ~150MB of X11/Mesa
# dependencies that most installs (PS1/SNES) do not use. Enable with
# `docker build --build-arg INCLUDE_PS2=true`.
#
# pcsx2_libretro is RETRO_HW_RENDER: it needs a real OpenGL context
# (video_driver=null is not enough, unlike PS1/SNES). xvfb provides that
# context without a physical display. mesa-utils/x11-utils are diagnostics only
# (glxinfo). There is no Ubuntu 24.04 apt package, so the core is downloaded
# from the official libretro buildbot (same pattern as mediamtx below: download
# + verification, but no fixed SHA because the nightly changes).
ARG INCLUDE_PS2=false
ARG LIBRETRO_PS2_CORE_URL=https://buildbot.libretro.com/nightly/linux/x86_64/latest/pcsx2_libretro.so.zip
RUN if [ "$INCLUDE_PS2" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            xvfb \
            x11-utils \
            mesa-utils \
            libgl1-mesa-dri \
            libglx-mesa0 \
            unzip \
        && rm -rf /var/lib/apt/lists/* \
        && mkdir -p /app/cores \
        && curl -sL -o /tmp/pcsx2_core.zip "$LIBRETRO_PS2_CORE_URL" \
        && unzip -o /tmp/pcsx2_core.zip -d /app/cores \
        && rm /tmp/pcsx2_core.zip; \
    fi

# CIFS storage (app/services/storage.py) calls `sudo mount/umount/tee`. On the
# Pi, this is restricted by a narrowly scoped sudoers rule. In the container the
# process already runs as root, so `sudo` just forwards the command without a
# password. Installing sudo avoids FileNotFoundError without environment-specific
# branching in the code. `mount -t cifs` itself requires --privileged at runtime.

# This image is x86_64 (amd64) ONLY: mediamtx is downloaded as linux_amd64 and
# libretro cores come from apt for the build arch. Fail early with a clear
# message if someone builds on ARM (for example Mac M1+) instead of failing
# obscurely later. ARM support would be possible (download mediamtx by buildx
# TARGETARCH), but it has not been validated; see SECURITY.md / ARCHITECTURE.md.
RUN arch="$(uname -m)"; \
    if [ "$arch" != "x86_64" ]; then \
        echo "ERROR: RetroHost container is x86_64-only; detected architecture: $arch." >&2; \
        echo "Build on an amd64 machine/platform, or use --platform linux/amd64." >&2; \
        exit 1; \
    fi

# mediamtx (not distributed via apt) - official GitHub release, amd64 build.
# Same version used on the Pi (scripts/install_mediamtx.sh, with the armv7 asset).
ARG MEDIAMTX_VERSION=v1.19.2
# SHA-256 for the linux_amd64 asset for v1.19.2; update when changing MEDIAMTX_VERSION.
ARG MEDIAMTX_SHA256=f9c601cc303ceca8fad2883917b022882672c5bc56311e92dbceb16e5f20c60c
RUN mkdir -p /app/bin \
    && curl -sL -o /tmp/mediamtx.tar.gz \
        "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_amd64.tar.gz" \
    && echo "${MEDIAMTX_SHA256}  /tmp/mediamtx.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/mediamtx.tar.gz -C /app/bin mediamtx \
    && chmod +x /app/bin/mediamtx \
    && rm /tmp/mediamtx.tar.gz

WORKDIR /app

# Project code (backend, frontend, config, scripts, container). .dockerignore
# excludes .venv/, local bin/, *.db, roms, __pycache__, and .git.
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY config/ /app/config/
COPY scripts/ /app/scripts/
COPY container/ /app/container/

# venv with --system-site-packages so it can see python3-evdev from apt
# (matching what setup_input.sh does on the Pi).
RUN python3 -m venv --system-site-packages /app/backend/.venv \
    && /app/backend/.venv/bin/pip install --no-cache-dir -r /app/backend/requirements.txt

# Build the SDL input LD_PRELOAD (virtual joystick inside RetroArch; see
# container/sdl_input_preload.c). gcc is installed only for the build and removed.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libsdl2-dev \
    && gcc -shared -fPIC -o /app/container/sdl_input_preload.so \
        /app/container/sdl_input_preload.c -ldl -lpthread \
    && apt-get purge -y gcc libc6-dev libsdl2-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN chmod +x /app/scripts/docker-entrypoint.sh

# /data: persistent data (SQLite), separate from code so it can be a volume.
# /mnt/homegames-roms: mount point for CIFS storage.
# /etc/samba: where the CIFS credentials file lives (CIFS_CREDENTIALS_PATH).
# On the Pi, setup_network_storage.sh creates these; in the container, we do it here.
RUN mkdir -p /data /mnt/homegames-roms /etc/samba

# HOMEGAMES_ROOT: makes core/config.py resolve every path from /app.
# HOMEGAMES_ENCODER=auto: auto-detects the encoder (nvenc>qsv>vaapi>libx264);
# keeps the image GPU-agnostic.
# HOMEGAMES_DB_PATH / STORAGE_CONFIG / CIFS_CREDENTIALS: all in /data (volume),
# so the DB, storage config, and CIFS credentials survive restarts and the
# entrypoint can remount CIFS at boot.
ENV HOMEGAMES_ROOT=/app \
    HOMEGAMES_ENCODER=auto \
    HOMEGAMES_DB_PATH=/data/homegames.db \
    HOMEGAMES_STORAGE_CONFIG=/data/storage.json \
    HOMEGAMES_CIFS_CREDENTIALS=/data/homegames-credentials \
    HOMEGAMES_INPUT_PROVIDER=sdl \
    HOMEGAMES_SDL_PRELOAD=/app/container/sdl_input_preload.so \
    PATH=/app/backend/.venv/bin:$PATH

# 8000 FastAPI/WHEP signaling, 8889 WHEP, 8189/udp ICE (WebRTC), 8554 internal RTSP.
EXPOSE 8000 8889 8554 8189/udp

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
