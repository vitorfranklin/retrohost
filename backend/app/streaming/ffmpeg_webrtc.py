"""Streaming via ffmpeg: lê o FIFO onde o RetroArchDriver grava vídeo/áudio
raw e publica H.264 + Opus via RTSP para o mediamtx, que expõe o stream ao
navegador via WHEP/WebRTC.

O encoder de vídeo é selecionável por settings.VIDEO_ENCODER (env
HOMEGAMES_ENCODER). Valores:
  - "auto":        auto-detecta o melhor encoder disponível (container agnóstico
                   de GPU: nvenc > qsv > vaapi > libx264). Ver encoder_detect.py.
  - h264_nvenc:    GPU NVIDIA.
  - h264_qsv:      Intel QuickSync.
  - h264_vaapi:    GPU Intel/AMD via VAAPI.
  - libx264:       software (CPU, funciona em qualquer máquina).
  - h264_v4l2m2m:  encoder de hardware do Raspberry Pi (default no Pi).
Os args de cada encoder vivem em encoder_profiles.py (EncoderProfile),
compartilhados com o detector para o probe testar o caminho real.

Nota: tentamos migrar para WHIP (-f whip) para cortar uma camada de remux,
mas o build do ffmpeg 7.1.5 no Raspbian não inclui o muxer WHIP
('Requested output format whip is not known'). Ficou em RTSP.

-fps_mode passthrough: o RetroArch grava ~30fps de conteúdo real mas
declara o stream como 60fps, fazendo o ffmpeg DUPLICAR ~41 frames/s e
rodar um filtro de rate conversion (visto como 'Clipping in rate
conversion' no -loglevel debug) — buffering e CPU desnecessários.
passthrough repassa os frames como vêm, sem duplicar nem converter.

Parâmetros validados manualmente no Raspberry Pi 3 real —
ver scripts/stream_pipeline.sh e o histórico de tuning de performance.
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
	    "-c:a", "libopus",
            "-application", "lowdelay",
            "-frame_duration", "5",
	    "-b:a", "96k",
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
