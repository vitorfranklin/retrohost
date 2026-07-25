"""ffmpeg video encoder profiles, GPU-agnostic.

Each profile describes the ffmpeg args for a specific H.264 encoder,
split into three positions because hardware encoders (VAAPI/QSV) need
to inject args BEFORE the input (-vaapi_device / -init_hw_device) and an
upload filter to the GPU (-vf), not just -c:v:

    ffmpeg <pre_input> -i <fifo> -fps_mode passthrough <filter> <codec> ...

The detector (encoder_detect.py) and FFmpegStreamingProvider use EXACTLY the
same args (via build_video_args), so the encode-probe tests the real path —
a simplified probe could pass while the real stream fails.

Auto-detection preference order: nvenc > qsv > vaapi > libx264.
libx264 (software) is the last resort, always works (no GPU required).
"""
from dataclasses import dataclass, field

# /dev/dri/renderD128 is the default render node (Intel/AMD). Configurable via
# env in config.py if the machine has multiple GPUs.
DEFAULT_RENDER_NODE = "/dev/dri/renderD128"


@dataclass(frozen=True)
class EncoderProfile:
    name: str
    # args before "-i <fifo>" (e.g. -vaapi_device, -init_hw_device)
    pre_input: list[str] = field(default_factory=list)
    # video filter (-vf ...), e.g. upload the frame to the GPU
    filter_args: list[str] = field(default_factory=list)
    # codec args (-c:v ... and tuning)
    codec_args: list[str] = field(default_factory=list)


def _vaapi_profile(render_node: str = DEFAULT_RENDER_NODE) -> EncoderProfile:
    # VAAPI: points to the device, uploads the frame (nv12) to the GPU and encodes.
    return EncoderProfile(
        name="h264_vaapi",
        pre_input=["-vaapi_device", render_node],
        filter_args=["-vf", "format=nv12,hwupload"],
        codec_args=["-c:v", "h264_vaapi", "-b:v", "4M", "-g", "30", "-bf", "0"],
    )


def _qsv_profile(render_node: str = DEFAULT_RENDER_NODE) -> EncoderProfile:
    # Intel QuickSync via QSV. Uses the same render node.
    return EncoderProfile(
        name="h264_qsv",
        pre_input=["-init_hw_device", f"qsv=hw,child_device={render_node}", "-filter_hw_device", "hw"],
        filter_args=["-vf", "format=nv12,hwupload=extra_hw_frames=8"],
        codec_args=["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", "4M", "-g", "30", "-bf", "0"],
    )


# Profiles without hardware-upload (pure codec). nvenc and v4l2m2m are accelerated but
# accept software frames directly; libx264 is software.
_SIMPLE_PROFILES: dict[str, list[str]] = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll",
                   "-b:v", "4M", "-g", "30", "-bf", "0"],
    # -bf 0: WebRTC does NOT accept H.264 with B-frames (the browser rejects the stream
    # with "doesn't support H264 streams with B-frames"). The -tune zerolatency from
    # x264 should zero B-frames, but it is not guaranteed in all builds — that is why
    # we force -bf 0 explicitly. Applies to any encoder.
    "libx264": ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-b:v", "4M", "-g", "30", "-bf", "0"],
    "h264_v4l2m2m": ["-c:v", "h264_v4l2m2m", "-b:v", "2M", "-g", "15"],
}


def get_profile(name: str, render_node: str = DEFAULT_RENDER_NODE) -> EncoderProfile:
    if name == "h264_vaapi":
        return _vaapi_profile(render_node)
    if name == "h264_qsv":
        return _qsv_profile(render_node)
    if name in _SIMPLE_PROFILES:
        return EncoderProfile(name=name, codec_args=_SIMPLE_PROFILES[name])
    raise ValueError(
        f"Unknown encoder '{name}'. Options: "
        f"{', '.join(['h264_nvenc', 'h264_qsv', 'h264_vaapi', 'libx264', 'h264_v4l2m2m'])}."
    )


# Auto-detection preference order (best latency/quality first;
# libx264 last, always works).
AUTO_DETECT_ORDER = ["h264_nvenc", "h264_qsv", "h264_vaapi", "libx264"]
