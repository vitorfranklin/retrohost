#!/usr/bin/env bash
# Manual streaming pipeline: RetroArch (headless) -> FIFO -> ffmpeg
# (h264_v4l2m2m hardware encoder + opus audio) -> RTSP -> mediamtx (WHEP/WebRTC).
#
# Usage: ./stream_pipeline.sh <core.so> <launch_file>
# Example:
#   ./stream_pipeline.sh \
#     /home/YOUR_USER/retrohost/emulator/cores/pcsx_rearmed_libretro.so \
#     "/home/YOUR_USER/retrohost/emulator/roms/ps1/Dino Crisis (v1.1)/Dino Crisis (v1.1).cue"
#
# Prerequisites: scripts/setup_streaming.sh already run (and Pi rebooted),
# mediamtx already running (bin/mediamtx config/mediamtx.yml).
#
# Note on audio codec: RetroArch writes raw PCM (pcm_s16le) into a single
# FIFO in a Matroska container alongside raw video (see
# config/retroarch/record_raw_av.cfg). ffmpeg encodes that audio as Opus
# — not AAC: mediamtx drops AAC tracks when publishing via WHEP because
# browsers do not natively support AAC in WebRTC (only Opus/G.711).
#
# -g 15 (short GOP): recovers from packet loss faster (was 30, causing
# visible freezes). -pkt_size 1200: prevents mediamtx from having to
# reassemble RTP packets larger than the MTU (1460 > 1440 observed in logs).
set -euo pipefail

CORE="${1:?uso: stream_pipeline.sh <core.so> <launch_file>}"
LAUNCH_FILE="${2:?uso: stream_pipeline.sh <core.so> <launch_file>}"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIFO=/tmp/retroarch_av.fifo
RECORDCONFIG="$PROJECT_ROOT/config/retroarch/record_raw_av.cfg"

rm -f "$FIFO"
mkfifo "$FIFO"

cleanup() {
    echo "Shutting down pipeline..."
    kill "$RETROARCH_PID" "$FFMPEG_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    rm -f "$FIFO"
}
trap cleanup EXIT INT TERM

retroarch -f -r "$FIFO" --recordconfig="$RECORDCONFIG" -L "$CORE" "$LAUNCH_FILE" &
RETROARCH_PID=$!

sleep 2

ffmpeg -y -re -i "$FIFO" \
    -c:v h264_v4l2m2m -b:v 2M -g 15 \
    -c:a libopus -b:a 96k \
    -pkt_size 1200 \
    -f rtsp rtsp://127.0.0.1:8554/live &
FFMPEG_PID=$!

echo "RetroArch PID=$RETROARCH_PID, ffmpeg PID=$FFMPEG_PID"
echo "Stream available via WHEP at http://<pi>:8889/live/whep"
wait "$RETROARCH_PID" "$FFMPEG_PID"
