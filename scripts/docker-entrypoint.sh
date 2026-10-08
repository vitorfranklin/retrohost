#!/usr/bin/env bash
# Entrypoint for the RetroHost container (see Dockerfile / ARCHITECTURE.md).
#
# Replaces the two Pi systemd units (mediamtx.service + homegames.service)
# with a lightweight supervisor: starts mediamtx in the background and uvicorn
# in the foreground, shutting down mediamtx when the container receives SIGTERM/SIGINT.
#
# Also generates, on first run, the headless retroarch.cfg and a default cores.json
# — things that on the Pi are done by setup scripts, but here must happen inside
# the container.
set -euo pipefail

APP_ROOT="${HOMEGAMES_ROOT:-/app}"
# Fixed writable HOME: RetroArch writes the cfg and (with config_save_on_exit) may
# overwrite it; /tmp is writable and is the same HOME used by PulseAudio below.
# Exported so that uvicorn and subprocesses (RetroArch) inherit the same HOME.
export HOME=/tmp
RETROARCH_CFG="$HOME/.config/retroarch/retroarch.cfg"
RETROARCH_SYSTEM_DIR="$HOME/.config/retroarch/system"
CORES_JSON="$APP_ROOT/config/cores.json"
LIBRETRO_DIR="/usr/lib/x86_64-linux-gnu/libretro"

# ---------------------------------------------------------------------------
# 1. retroarch.cfg headless
# ---------------------------------------------------------------------------
# Replicates what scripts/setup_streaming.sh does on the Pi, WITHOUT the
# /boot/firmware/config.txt part (which only exists on Raspberry Pi).
#
# AUDIO: on the Pi, audio_driver=alsa reads real /dev/snd and the audio clock
# is what throttles the core; without functional audio the game runs in "fast forward".
# In the container (especially WSL2, which does not expose /dev/snd) we use a
# PulseAudio with software null-sink as the clock — see step 3 below.
# audio_driver is overridable via env HOMEGAMES_AUDIO_DRIVER (e.g. =alsa on a
# native Linux machine that passes /dev/snd via --device).
AUDIO_DRIVER="${HOMEGAMES_AUDIO_DRIVER:-pulse}"

# Regenerated ALWAYS (not just if missing): RetroArch, when exiting/crashing with
# config_save_on_exit=true, overwrites the cfg with defaults (without video_driver=null),
# which breaks the next headless run. config_save_on_exit="false" below
# prevents this, but regenerating at each boot guarantees a clean state.
echo "==> Generating headless retroarch.cfg at $RETROARCH_CFG"
mkdir -p "$(dirname "$RETROARCH_CFG")" "$RETROARCH_SYSTEM_DIR"
# Input driver: "sdl2" in the container (virtual SDL joystick via LD_PRELOAD,
# does not depend on udevd which WSL2 lacks) or "udev" (Pi, uinput keyboard).
INPUT_PROVIDER="${HOMEGAMES_INPUT_PROVIDER:-uinput}"
if [[ "$INPUT_PROVIDER" == "sdl" ]]; then
    RA_INPUT_DRIVER="sdl2"
    RA_JOYPAD_DRIVER="sdl2"
else
    RA_INPUT_DRIVER="udev"
    RA_JOYPAD_DRIVER="udev"
fi

cat > "$RETROARCH_CFG" <<EOF
video_driver = "null"
video_context_driver = ""
video_shader_enable = "false"
input_driver = "$RA_INPUT_DRIVER"
input_joypad_driver = "$RA_JOYPAD_DRIVER"
audio_driver = "$AUDIO_DRIVER"
config_save_on_exit = "false"
system_directory = "$RETROARCH_SYSTEM_DIR"

# Player 1 keyboard binds (RetroPad), used in uinput mode (Pi). In SDL mode
# input comes from the virtual joystick (autoconfig), and these binds are harmless.
# These are RetroArch defaults, consistent with app/input/keymap.py.
input_player1_a = "x"
input_player1_b = "z"
input_player1_x = "s"
input_player1_y = "a"
input_player1_l = "q"
input_player1_r = "w"
input_player1_start = "enter"
input_player1_select = "rshift"
input_player1_up = "up"
input_player1_down = "down"
input_player1_left = "left"
input_player1_right = "right"
EOF

# The PS1 BIOS copy (required by the Beetle PSX core) happens in
# RetroArchDriver.launch() — by then storage (CIFS) is already mounted and the
# system directory (HOMEGAMES_SYSTEM_DIR, exported below) is accessible. See
# backend/app/drivers/retroarch.py.
export HOMEGAMES_SYSTEM_DIR="$RETROARCH_SYSTEM_DIR"

# ---------------------------------------------------------------------------
# 2. cores.json default (if none is mounted via volume)
# ---------------------------------------------------------------------------
# On the Pi, cores live in emulator/cores/ (absolute paths in the JSON). In the container
# cores come from apt in $LIBRETRO_DIR. If the user has not mounted their own cores.json,
# we generate one pointing to whatever installed cores exist.
if [[ ! -f "$CORES_JSON" ]]; then
    echo "==> Generating default cores.json from $LIBRETRO_DIR"
    PS1_CORE="$LIBRETRO_DIR/mednafen_psx_libretro.so"
    SNES_CORE="$LIBRETRO_DIR/snes9x_libretro.so"
    # PS2 core is not installed by default (INCLUDE_PS2 build arg, see Dockerfile) —
    # only picked up here if present, same "no-op unless the file exists" pattern
    # as the other cores.
    PS2_CORE="/app/cores/pcsx2_libretro.so"
    {
        echo "{"
        first=1
        add_core() {
            local key="$1" path="$2"
            if [[ -f "$path" ]]; then
                [[ $first -eq 0 ]] && echo ","
                printf '  "%s": "%s"' "$key" "$path"
                first=0
            fi
        }
        add_core "ps1" "$PS1_CORE"
        add_core "snes" "$SNES_CORE"
        add_core "ps2" "$PS2_CORE"
        echo ""
        echo "}"
    } > "$CORES_JSON"
    echo "    cores.json generated:"
    cat "$CORES_JSON"
else
    echo "==> cores.json already present (mounted or from a previous build), keeping."
fi

# ---------------------------------------------------------------------------
# 2b. Xvfb for PS2 (only if a ps2 core is configured)
# ---------------------------------------------------------------------------
# pcsx2_libretro is RETRO_HW_RENDER — it needs a real OpenGL context, unlike
# every other core here (video_driver=null suffices for them). RetroArchDriver
# overrides video_driver=gl/video_context_driver=x11 per-launch for ps2 (see
# _VIDEO_PROFILES in backend/app/drivers/retroarch.py) and expects a real X
# server to connect to at HOMEGAMES_X_DISPLAY (default :99) — see docs/ps2-poc.md.
# Only started when ps2 is actually configured, so hosts without PS2 in their
# library don't pay for an idle Xvfb process.
if grep -q '"ps2"' "$CORES_JSON" 2>/dev/null; then
    export HOMEGAMES_X_DISPLAY="${HOMEGAMES_X_DISPLAY:-:99}"
    echo "==> ps2 core configured — starting Xvfb on $HOMEGAMES_X_DISPLAY"
    Xvfb "$HOMEGAMES_X_DISPLAY" -screen 0 1280x720x24 -nolisten tcp &
    XVFB_PID=$!
else
    XVFB_PID=""
fi

# ---------------------------------------------------------------------------
# 3. PulseAudio with null-sink (software audio clock)
# ---------------------------------------------------------------------------
# Only when audio_driver=pulse. Starts a system PulseAudio daemon with a
# single virtual sink (module-null-sink) — does not touch any hardware, only
# provides the audio clock that RetroArch needs to avoid running in fast
# forward. Works on WSL2 / any container without /dev/snd.
PULSE_PID=""
if [[ "$AUDIO_DRIVER" == "pulse" ]]; then
    echo "==> Starting PulseAudio (null-sink) as the audio clock"
    # User mode (not --system, which has a restrictive ACL that gives 'Access denied'
    # in pactl). Writable HOME/PULSE_RUNTIME_PATH allow running as root.
    # -n ignores the default config and loads only the modules we need.
    export HOME=/tmp PULSE_RUNTIME_PATH=/tmp/pulse XDG_RUNTIME_DIR=/tmp/xdg
    mkdir -p /tmp/pulse /tmp/xdg
    pulseaudio --exit-idle-time=-1 --disable-shm=true -n \
        -L "module-native-protocol-unix" \
        -L "module-null-sink sink_name=homegames sink_properties=device.description=HomeGames" \
        --daemonize=yes 2>/tmp/pulseaudio.log \
        || echo "    WARNING: PulseAudio did not start (see /tmp/pulseaudio.log)"
    sleep 1
    pactl set-default-sink homegames 2>/dev/null || true
    echo "    sinks: $(pactl list sinks short 2>/dev/null | awk '{print $2}' | tr '\n' ' ')"
fi

# ---------------------------------------------------------------------------
# 4. WebRTC host (access from other devices on the LAN)
# ---------------------------------------------------------------------------
# By default mediamtx announces the container's interface IPs (e.g. 172.17.0.2),
# which are unreachable by a phone on the LAN — resulting in a black screen in
# WebRTC. If HOMEGAMES_WEBRTC_HOST is set (host IP on the LAN), we inject it
# into webrtcAdditionalHosts so mediamtx announces a routable IP.
MEDIAMTX_CONFIG="$APP_ROOT/config/mediamtx.yml"
if [[ -n "${HOMEGAMES_WEBRTC_HOST:-}" ]]; then
    echo "==> Configuring WebRTC to announce host $HOMEGAMES_WEBRTC_HOST"
    MEDIAMTX_CONFIG=/tmp/mediamtx.yml
    sed "s/^webrtcAdditionalHosts:.*/webrtcAdditionalHosts: [$HOMEGAMES_WEBRTC_HOST]/" \
        "$APP_ROOT/config/mediamtx.yml" > "$MEDIAMTX_CONFIG"
fi

# ---------------------------------------------------------------------------
# 5. Supervisor: mediamtx (background) + uvicorn (foreground)
# ---------------------------------------------------------------------------

MEDIAMTX_PID=""

cleanup() {
    echo "==> Shutting down (signal received)..."
    if [[ -n "$MEDIAMTX_PID" ]] && kill -0 "$MEDIAMTX_PID" 2>/dev/null; then
        kill "$MEDIAMTX_PID" 2>/dev/null || true
        wait "$MEDIAMTX_PID" 2>/dev/null || true
    fi
    [[ -n "$PULSE_PID" ]] && kill "$PULSE_PID" 2>/dev/null || true
    pulseaudio --kill 2>/dev/null || true
    if [[ -n "$XVFB_PID" ]] && kill -0 "$XVFB_PID" 2>/dev/null; then
        kill "$XVFB_PID" 2>/dev/null || true
        wait "$XVFB_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "==> Starting mediamtx"
"$APP_ROOT/bin/mediamtx" "$MEDIAMTX_CONFIG" &
MEDIAMTX_PID=$!

# Short wait for mediamtx to open its ports before the backend (which queries
# its API on port 9997 when starting a game). Not critical — the player's
# _wait_stream_ready() already polls with a timeout — but avoids noise in the initial logs.
sleep 1

cd "$APP_ROOT/backend"

# Remount CIFS if storage was configured for network (storage.json and
# credentials persist in /data; the mount itself does not survive a restart).
echo "==> Checking network storage (remount if mode=cifs)"
python -c "from app.services.storage import remount_if_configured; remount_if_configured()" \
    && echo "    storage verified" || echo "    WARNING: failed to remount storage (reconfigure via UI if needed)"

# Auto-detect the video encoder (only when HOMEGAMES_ENCODER=auto). Runs the
# encode-probes once and shows which hardware will be used — helps the user know
# whether the GPU was detected or fell back to CPU. The result is cached in the
# process; uvicorn (same module) reuses it.
if [[ "${HOMEGAMES_ENCODER:-}" == "auto" ]]; then
    echo "==> Detecting video encoder (nvenc > qsv > vaapi > libx264)..."
    python -c "from app.streaming.encoder_detect import detect_encoder, describe_encoder; print('    Encoder:', describe_encoder(detect_encoder()))"
fi

echo "==> Starting backend (uvicorn) in foreground"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
