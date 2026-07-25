"""Auto-detection of the ffmpeg video encoder that works on this machine.

The container image is GPU-agnostic: the SAME image runs on NVIDIA, Intel,
AMD, or CPU only. Instead of trusting "the encoder is compiled" or "the device
exists" (fragile), we test each candidate with a REAL encode-probe — a short
ffmpeg run (testsrc -> encoder -> null). The first one returning 0 is used.

The probe uses exactly the same args as the real profile (encoder_profiles),
including hwupload filters for VAAPI/QSV, so it doesn't pass the probe
and fail the real stream.

Result is cached: detection runs once per process, not per game.
"""
import subprocess

from app.streaming.encoder_profiles import AUTO_DETECT_ORDER, EncoderProfile, get_profile

_PROBE_DURATION = "0.3"  # seconds of synthetic video per probe
_PROBE_SIZE = "256x240"  # small resolution, fast probe
_cached_encoder: str | None = None


def _probe(profile: EncoderProfile) -> bool:
    """True if ffmpeg can actually encode with this profile."""
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        *profile.pre_input,
        "-f", "lavfi", "-i", f"testsrc=size={_PROBE_SIZE}:rate=30",
        "-t", _PROBE_DURATION,
        *profile.filter_args,
        *profile.codec_args,
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def detect_encoder() -> str:
    """Return the name of the best working encoder (cached).

    Tests in order: nvenc > qsv > vaapi > libx264. libx264 is the last resort
    (software encode, always available) — if no hardware option works, falls back
    to it unconditionally.
    """
    global _cached_encoder
    if _cached_encoder is not None:
        return _cached_encoder
    for name in AUTO_DETECT_ORDER:
        if _probe(get_profile(name)):
            _cached_encoder = name
            return name
    # Theoretically unreachable (libx264 always passes), but explicit fail-safe.
    _cached_encoder = "libx264"
    return _cached_encoder


def describe_encoder(name: str) -> str:
    """Human-readable description for logging (which hardware is being used)."""
    return {
        "h264_nvenc": "h264_nvenc (NVIDIA GPU)",
        "h264_qsv": "h264_qsv (Intel QuickSync)",
        "h264_vaapi": "h264_vaapi (GPU via VAAPI - Intel/AMD)",
        "libx264": "libx264 (CPU/software - no GPU detected)",
        "h264_v4l2m2m": "h264_v4l2m2m (Raspberry Pi hardware encoder)",
    }.get(name, name)
