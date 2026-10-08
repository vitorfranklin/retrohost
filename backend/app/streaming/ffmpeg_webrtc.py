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

Source selection per console (_source_for): every console reads the FIFO at
settings.STREAMING_FIFO_PATH, except PS2. Found empirically (poc/ps2-support-headless-gl,
docs/ps2-poc.md): pcsx2_libretro re-emits SET_SYSTEM_AV_INFO 2-3 times during
BIOS boot, and each time RetroArch restarts its recording subsystem — but
after restarting it silently abandons the FIFO given via -r and falls back to
writing a timestamped .mkv in RetroArchDriver's CONSOLES_WITH_UNSTABLE_FIFO
dir instead. Reading the FIFO for PS2 means ffmpeg reads a truncated stream
from the FIRST (abandoned) recording and dies immediately with "Error opening
input: End of file". For those consoles we instead wait for RetroArch's
restarts to settle (no new file appearing for a settle window) and read the
resulting still-growing .mkv file.

Reading that .mkv is NOT as simple as pointing ffmpeg's -i at it. Unlike a
FIFO, a regular file's read() returns EOF as soon as the reader catches up to
the writer — it does not block waiting for more data — so ffmpeg treats
"caught up to what's been written so far" as the end of the stream and quits
("File ended prematurely" after processing only the backlog accumulated
during the settle wait, instead of continuing to receive frames as RetroArch
keeps writing). A separate `tail -f` process (blocks and keeps emitting new
bytes as the file grows, same semantics as a FIFO) piped into ffmpeg's stdin
fixes this — validated manually reading a full real-time session this way.
(A second, now-fixed pitfall: connecting too late lets the file accumulate
enough data for ffmpeg to estimate a fixed Duration from bitrate, at which
point it treats the input as a finished VOD instead of a stream — "Output
file is empty, nothing was encoded" after publishing a few seconds. The
settle window must be short enough to avoid that AND long enough to not
mistake a pause between two BIOS-boot restarts for real stabilization.)
"""
import subprocess
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.drivers.retroarch import CONSOLES_WITH_UNSTABLE_FIFO
from app.streaming.base import StreamingProvider
from app.streaming.encoder_detect import detect_encoder
from app.streaming.encoder_profiles import EncoderProfile, get_profile

# How long to wait for RetroArch's recording restarts (PS2 BIOS boot) to
# settle before giving up and using whatever file exists, if any.
_SOURCE_SETTLE_TIMEOUT_SECONDS = 25.0
_SOURCE_SETTLE_QUIET_SECONDS = 5.0
_SOURCE_POLL_INTERVAL_SECONDS = 0.5


class FFmpegStreamingProvider(StreamingProvider):
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._tail_process: subprocess.Popen | None = None

    def _profile(self) -> EncoderProfile:
        name = settings.VIDEO_ENCODER
        if name == "auto":
            name = detect_encoder()
        return get_profile(name, settings.RENDER_NODE)

    @staticmethod
    def _wait_for_settled_recording(records_dir: str) -> str | None:
        """Poll records_dir until the most recently written .mkv stops
        changing identity for _SOURCE_SETTLE_QUIET_SECONDS — see module
        docstring. Returns None if no file ever appeared."""
        path = Path(records_dir)
        deadline = time.monotonic() + _SOURCE_SETTLE_TIMEOUT_SECONDS
        last_newest: Path | None = None
        last_change = time.monotonic()

        while time.monotonic() < deadline:
            candidates = sorted(path.glob("*.mkv"), key=lambda p: p.stat().st_mtime)
            newest = candidates[-1] if candidates else None
            if newest != last_newest:
                last_newest = newest
                last_change = time.monotonic()
            if newest is not None and (time.monotonic() - last_change) >= _SOURCE_SETTLE_QUIET_SECONDS:
                return str(newest)
            time.sleep(_SOURCE_POLL_INTERVAL_SECONDS)
        return str(last_newest) if last_newest else None

    def start(self, game: Any) -> None:
        profile = self._profile()
        records_dir = CONSOLES_WITH_UNSTABLE_FIFO.get(game.console)

        if records_dir is None:
            ffmpeg_stdin = None
            source = str(settings.STREAMING_FIFO_PATH)
        else:
            settled_file = self._wait_for_settled_recording(records_dir)
            if settled_file is None:
                raise RuntimeError(
                    f"RetroArch never wrote a recording file in {records_dir} "
                    f"(waited {_SOURCE_SETTLE_TIMEOUT_SECONDS}s) — cannot start streaming."
                )
            # tail -f -c +0: start from byte 0 (the file was just created this
            # session — _clear_records_dir in RetroArchDriver guarantees no
            # leftovers) and keep blocking for new bytes like a FIFO would.
            # Piped into ffmpeg's stdin (-i pipe:0) rather than a second named
            # FIFO: one fewer temp file to create/clean up, and subprocess
            # already gives us a pipe fd for free via stdout=PIPE/stdin=PIPE.
            self._tail_process = subprocess.Popen(
                ["tail", "-f", "-c", "+0", settled_file],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            ffmpeg_stdin = self._tail_process.stdout
            source = "pipe:0"

        command = [
            "ffmpeg", "-y",
            "-fflags", "nobuffer", "-flags", "low_delay",
            *profile.pre_input,
            "-i", source,
            "-fps_mode", "passthrough",
            *profile.filter_args,
            *profile.codec_args,
            "-af", "aresample=async=1000:first_pts=0",
            "-c:a", "libopus", "-b:a", "96k",
            "-pkt_size", "1200",
            "-f", "rtsp", settings.MEDIAMTX_RTSP_URL,
        ]
        self._process = subprocess.Popen(command, stdin=ffmpeg_stdin)
        if ffmpeg_stdin is not None:
            # This process's own handle to the read end must be closed after
            # handing it to the child, or ffmpeg exiting won't be enough to
            # make tail's writes fail/SIGPIPE — the fd would stay open here too.
            ffmpeg_stdin.close()

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

        if self._tail_process is not None:
            if self._tail_process.poll() is None:
                self._tail_process.terminate()
                try:
                    self._tail_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._tail_process.kill()
                    self._tail_process.wait()
            self._tail_process = None

    def status(self) -> bool:
        if self._process is None:
            return False
        if self._process.poll() is None:
            return True
        self._process = None
        return False
