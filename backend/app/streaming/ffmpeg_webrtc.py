"""Streaming via ffmpeg: reads the FIFO where RetroArchDriver writes raw
video/audio and publishes H.264 + Opus via RTSP to mediamtx, which exposes
the stream to the browser via WHEP/WebRTC.

The video encoder is selectable via settings.VIDEO_ENCODER (env
HOMEGAMES_ENCODER). Values:
  - "auto":        auto-detects the best available encoder (GPU-agnostic
                   container: nvenc > qsv > vaapi > libx264). See encoder_detect.py.
  - h264_nvenc:    NVIDIA GPU.
  - h264_qsv:      Intel QuickSync.
  - h264_vaapi:    Intel/AMD GPU via VAAPI.
  - libx264:       software (CPU, works on any machine).
  - h264_v4l2m2m:  Raspberry Pi hardware encoder (Pi default).
The args for each encoder live in encoder_profiles.py (EncoderProfile),
shared with the detector so the probe tests the real path.

Note: we tried migrating to WHIP (-f whip) to cut one remux layer,
but the ffmpeg 7.1.5 build on Raspbian does not include the WHIP muxer
('Requested output format whip is not known'). Stayed on RTSP.

-fps_mode passthrough: RetroArch records ~30fps of real content but
declares the stream as 60fps, causing ffmpeg to DUPLICATE ~41 frames/s and
run a rate conversion filter (seen as 'Clipping in rate conversion'
in -loglevel debug) — unnecessary buffering and CPU usage.
passthrough forwards frames as they come, without duplicating or converting.

Manually validated parameters on a real Raspberry Pi 3 —
see scripts/stream_pipeline.sh and the performance tuning history.
"""
import subprocess
from typing import Any

from app.core.config import settings
from app.streaming.base import StreamingProvider
from app.streaming.encoder_detect import detect_encoder
from app.streaming.encoder_profiles import EncoderProfile, get_profile


class FFmpegStreamingProvider(StreamingProvider):
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None

    def _profile(self) -> EncoderProfile:
        name = settings.VIDEO_ENCODER
        if name == "auto":
            name = detect_encoder()
        return get_profile(name, settings.RENDER_NODE)

    def start(self, game: Any) -> None:
        profile = self._profile()
        command = [
            "ffmpeg", "-y",
            "-fflags", "nobuffer", "-flags", "low_delay",
            *profile.pre_input,
            "-i", str(settings.STREAMING_FIFO_PATH),
            "-fps_mode", "passthrough",
            *profile.filter_args,
            *profile.codec_args,
            "-af", "aresample=async=1000:first_pts=0",
            "-c:a", "libopus", "-b:a", "96k",
            "-pkt_size", "1200",
            "-f", "rtsp", settings.MEDIAMTX_RTSP_URL,
        ]
        self._process = subprocess.Popen(command)

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._process = None

    def status(self) -> bool:
        if self._process is None:
            return False
        if self._process.poll() is None:
            return True
        self._process = None
        return False
