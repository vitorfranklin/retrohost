#!/usr/bin/env bash
# RetroHost - PS2 headless POC smoke test (branch poc/ps2-support-headless-gl).
#
# Question this script answers: can pcsx2_libretro open a real OpenGL context
# inside the container via Xvfb and produce video frames, instead of using the
# video_driver=null path we use for PS1/SNES?
#
# This does NOT validate the full stack (FIFO -> ffmpeg -> mediamtx -> WebRTC)
# and does not touch PlayerService. That is left for a second POC if this first
# one proves headless rendering is viable. See docs/ps2-poc.md.
#
# BIOS/ISO files are not provided by this repository due to copyright. Mount
# your own through environment variables:
#   PS2_BIOS_DIR : directory containing PS2 BIOS file(s), e.g. SCPH-xxxxx.bin
#   PS2_ISO_PATH : path to a valid game/homebrew .iso/.chd image
#
# Without PS2_ISO_PATH, the script still validates that Xvfb + GL + the core
# load and that RetroArch does not crash when initializing the core without
# content. That is only a partial signal; it does not prove real game frames are
# produced.
set -euo pipefail

echo "=== RetroHost PS2 POC - headless rendering smoke test ==="

CORE_PATH="/app/cores/pcsx2_libretro.so"
if [[ ! -f "$CORE_PATH" ]]; then
    echo "ERROR: core not found at $CORE_PATH" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Xvfb: virtual X display that gives the core a real GLX context to render
#    into (instead of video_driver=null, which opens no context at all).
# ---------------------------------------------------------------------------
export DISPLAY=:99
echo "==> Starting Xvfb on $DISPLAY"
Xvfb "$DISPLAY" -screen 0 1280x720x24 -nolisten tcp &
XVFB_PID=$!
sleep 1

if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "ERROR: Xvfb did not start." >&2
    exit 1
fi

echo "==> Checking available GL (glxinfo)"
if glxinfo -display "$DISPLAY" > /tmp/glxinfo.log 2>&1; then
    renderer=$(grep -m1 "OpenGL renderer" /tmp/glxinfo.log || echo "unknown")
    echo "    $renderer"
else
    echo "WARNING: glxinfo failed; GL may not be available. See /tmp/glxinfo.log" >&2
fi

# ---------------------------------------------------------------------------
# 2. PulseAudio null-sink: same reason as the main container. Without an audio
#    clock, the core runs in fast-forward and audio_sync does not throttle.
# ---------------------------------------------------------------------------
export HOME=/tmp PULSE_RUNTIME_PATH=/tmp/pulse XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p /tmp/pulse /tmp/xdg
pulseaudio --exit-idle-time=-1 --disable-shm=true -n \
    -L "module-native-protocol-unix" \
    -L "module-null-sink sink_name=ps2poc sink_properties=device.description=PS2POC" \
    --daemonize=yes 2>/tmp/pulseaudio.log || echo "WARNING: PulseAudio did not start (see /tmp/pulseaudio.log)"
sleep 1

# ---------------------------------------------------------------------------
# 3. retroarch.cfg: video_driver=gl (NOT null), targeting Xvfb via DISPLAY.
#    This is the core of what we are testing: video_context_driver=x11 gives
#    the core a real GLX context, unlike today's PS1/SNES path.
# ---------------------------------------------------------------------------
RETROARCH_CFG="$HOME/.config/retroarch/retroarch.cfg"
RETROARCH_SYSTEM_DIR="$HOME/.config/retroarch/system"
mkdir -p "$(dirname "$RETROARCH_CFG")" "$RETROARCH_SYSTEM_DIR"

cat > "$RETROARCH_CFG" <<EOF
video_driver = "gl"
video_context_driver = "x11"
video_fullscreen = "true"
video_windowed_fullscreen = "false"
video_vsync = "false"
video_shader_enable = "false"
input_driver = "udev"
audio_driver = "pulse"
config_save_on_exit = "false"
system_directory = "$RETROARCH_SYSTEM_DIR"

# Critical for PS2: pcsx2_libretro is RETRO_HW_RENDER (GL), unlike Beetle PSX
# (PS1), which is pure software. Without video_gpu_record=true, RetroArch warns
# "Libretro core is hardware rendered. Must use post-shaded recording as well.."
# and never writes to the FIFO (--recordconfig alone is not enough). Empirically
# validated: 0 bytes without this line, real recording with it. This applies to
# any PS2 core; the bottleneck is GL, not the game.
video_gpu_record = "true"
EOF

# pcsx2_libretro, unlike Beetle PSX used for PS1, expects BIOS files under
# <system_directory>/pcsx2/bios/, not directly at the system_directory root.
# Empirically validated: without the correct subdirectory, the core reports
# "Could not find any valid PS2 BIOS File" even when the BIOS is present. We
# only copy .bin/.BIN files (the BIOS ROM itself); other files in a typical dump
# (.NVM/.MEC/.EROM/.ROM1/.ROM2) are auxiliary nvram/mcd files, not the BIOS.
PCSX2_BIOS_DIR="$RETROARCH_SYSTEM_DIR/pcsx2/bios"
mkdir -p "$PCSX2_BIOS_DIR"
if [[ -n "${PS2_BIOS_DIR:-}" && -d "$PS2_BIOS_DIR" ]]; then
    echo "==> Copying BIOS (.bin/.BIN) from $PS2_BIOS_DIR to $PCSX2_BIOS_DIR"
    find "$PS2_BIOS_DIR" -maxdepth 1 -type f \( -iname '*.bin' \) -exec cp -v {} "$PCSX2_BIOS_DIR/" \;
else
    echo "WARNING: PS2_BIOS_DIR was not provided or does not exist; the core may not initialize up to BIOS."
fi

# ---------------------------------------------------------------------------
# 4. Record raw frames into a FIFO (same mechanism as the real RetroArchDriver:
#    --recordconfig), for N seconds, and report how many bytes arrived.
#    Bytes > 0 means the core actually produced video, not merely opened
#    without crashing.
# ---------------------------------------------------------------------------
FIFO=/tmp/ps2poc.fifo
rm -f "$FIFO"
mkfifo "$FIFO"

# Same format used in production (config/retroarch/record_raw_av.cfg). The
# [Output]/size syntax we used before is not recognized by RetroArch and
# resulted in 0 bytes written to the FIFO, masking it as "core does not render".
RECORD_CFG=/tmp/record.cfg
cat > "$RECORD_CFG" <<EOF
vcodec = "rawvideo"
acodec = "pcm_s16le"
format = "matroska"
audio_enable = "true"
pix_fmt = "yuv420p"
EOF

RUN_SECONDS="${PS2_POC_RUN_SECONDS:-15}"

RA_ARGS=(-f --recordconfig "$RECORD_CFG" -r "$FIFO" -L "$CORE_PATH")
# -s (not just -f): compose always mounts something at PS2_ISO_PATH (a 0-byte
# placeholder when no real ISO is provided). Checking only for existence would
# make RetroArch try to load an empty ISO as content and fail early.
if [[ -n "${PS2_ISO_PATH:-}" && -s "${PS2_ISO_PATH:-}" ]]; then
    echo "==> Content: $PS2_ISO_PATH"
    RA_ARGS+=("$PS2_ISO_PATH")
else
    echo "WARNING: PS2_ISO_PATH was not provided or does not exist; starting the core WITHOUT content (--menu)."
    RA_ARGS+=(--menu)
fi

echo "==> Reading from FIFO in the background for ${RUN_SECONDS}s (dd counts received bytes)"
( timeout "$RUN_SECONDS" dd if="$FIFO" of=/dev/null bs=1M 2>/tmp/dd.log; echo "dd finished" ) &
DD_PID=$!

echo "==> Starting RetroArch: retroarch ${RA_ARGS[*]}"
set +e
timeout "$RUN_SECONDS" retroarch "${RA_ARGS[@]}" > /tmp/retroarch.log 2>&1
RA_EXIT=$?
set -e

wait "$DD_PID" 2>/dev/null || true

BYTES_RECEIVED=$(grep -oE '[0-9]+ bytes' /tmp/dd.log | tail -1 | grep -oE '[0-9]+' || echo 0)

echo ""
echo "=== RESULT ==="
echo "RetroArch exit code : $RA_EXIT (124 = expected timeout, the test time limit)"
echo "Bytes received by FIFO : ${BYTES_RECEIVED:-0}"
echo "glxinfo renderer : $(grep -m1 'OpenGL renderer' /tmp/glxinfo.log 2>/dev/null || echo 'N/A')"
echo ""
echo "--- last RetroArch log lines ---"
tail -n 40 /tmp/retroarch.log || true

kill "$XVFB_PID" 2>/dev/null || true
pulseaudio --kill 2>/dev/null || true

if [[ "${BYTES_RECEIVED:-0}" -gt 0 ]]; then
    echo ""
    echo "==> SUCCESS: the core produced video through GL/Xvfb into the FIFO."
    exit 0
else
    echo ""
    echo "==> FAILURE: no video bytes received. See /tmp/retroarch.log above."
    exit 1
fi
