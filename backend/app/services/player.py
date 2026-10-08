"""Service that orchestrates play/stop/status over an EmulatorDriver + StreamingProvider
+ InputProvider.

Keeps in memory the currently running game (in the MVP, only one game at a time).
The API does not know RetroArch, ffmpeg, or uinput — it only calls this service,
which in turn delegates to the configured driver and providers.
"""
import json
import time
import urllib.error
import urllib.request

from app.core.config import settings
from app.drivers.base import EmulatorDriver
from app.input.base import InputProvider
from app.models.game import Game
from app.streaming.base import StreamingProvider

# Time for RetroArch to open the FIFO and start writing before ffmpeg
# connects as a reader — value validated manually (scripts/stream_pipeline.sh).
# For consoles in CONSOLES_WITH_UNSTABLE_FIFO (PS2), this only needs to cover
# process startup, not the full BIOS boot: FFmpegStreamingProvider does its
# own active polling for the settled recording file before connecting (see
# _source_for in streaming/ffmpeg_webrtc.py) rather than relying on a fixed
# sleep to guess when RetroArch is ready.
_FIFO_READY_DELAY_SECONDS = 2

# play() only returns after mediamtx confirms the stream is published, so the
# frontend does not try to connect via WHEP before there is anything to consume
# (the RTSP publication by ffmpeg takes longer than the FIFO opening time).
_STREAM_READY_TIMEOUT_SECONDS = 10
_STREAM_READY_POLL_INTERVAL_SECONDS = 0.5

# Consoles with a heavier boot path (BIOS load + shader compilation on first
# draw, e.g. PS2/pcsx2_libretro — validated in poc/ps2-support-headless-gl,
# see docs/ps2-poc.md) need more time before the stream is actually publishing
# than the default budget above, which was tuned for PS1/SNES: this must cover
# both FFmpegStreamingProvider's own polling wait (up to ~25s, see
# _SOURCE_SETTLE_TIMEOUT_SECONDS in ffmpeg_webrtc.py) and mediamtx picking up
# the RTSP publish afterwards.
_STREAM_READY_TIMEOUT_OVERRIDES_SECONDS: dict[str, int] = {
    "ps2": 50,
}


class StreamNotReadyError(RuntimeError):
    pass


class PlayerService:
    def __init__(self, driver: EmulatorDriver, streaming: StreamingProvider, input_provider: InputProvider) -> None:
        self._driver = driver
        self._streaming = streaming
        self._input = input_provider
        self._current_game: Game | None = None

    def play(self, game: Game) -> None:
        # Ensure network storage is mounted: the CIFS mount may have dropped during
        # use (Wi-Fi/timeout). Remounts on-demand and, if it fails, propagates a
        # clear error instead of the game failing with "Failed to load content" for no reason.
        from app.services.storage import ensure_storage_mounted

        ensure_storage_mounted()
        # input.connect() BEFORE driver.launch(): RetroArch scans input devices
        # at startup. Without udevd to signal hotplug (container), the virtual
        # keyboard must already exist when RetroArch starts. On the Pi the order
        # is also safe (the keyboard is created before the game).
        self._input.connect()
        self._driver.launch(game)
        time.sleep(_FIFO_READY_DELAY_SECONDS)
        self._streaming.start(game)
        self._wait_stream_ready(game.console)
        self._current_game = game

    def _wait_stream_ready(self, console: str) -> None:
        timeout = _STREAM_READY_TIMEOUT_OVERRIDES_SECONDS.get(console, _STREAM_READY_TIMEOUT_SECONDS)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_stream_ready():
                return
            time.sleep(_STREAM_READY_POLL_INTERVAL_SECONDS)

        self._streaming.stop()
        self._driver.stop()
        raise StreamNotReadyError(
            "The stream did not become available in mediamtx in time. Check that mediamtx is running."
        )

    @staticmethod
    def _is_stream_ready() -> bool:
        try:
            with urllib.request.urlopen(settings.MEDIAMTX_API_URL, timeout=1) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, TimeoutError, ValueError):
            return False
        return bool(data.get("ready"))

    def stop(self) -> None:
        self._input.disconnect()
        self._streaming.stop()
        self._driver.stop()
        self._current_game = None

    def status(self) -> tuple[bool, Game | None]:
        running = self._driver.status()
        if not running:
            self._current_game = None
        return running, self._current_game

    def handle_input(self, key: str, pressed: bool) -> None:
        if pressed:
            self._input.press(key)
        else:
            self._input.release(key)

    def release_all_inputs(self) -> None:
        self._input.release_all()


_player_service: PlayerService | None = None


def _build_input_provider() -> InputProvider:
    if settings.INPUT_PROVIDER == "sdl":
        from app.input.sdl_gamepad import SDLGamepadProvider

        return SDLGamepadProvider()
    from app.input.uinput_keyboard import UInputKeyboardProvider

    return UInputKeyboardProvider()


def get_player_service() -> PlayerService:
    global _player_service
    if _player_service is None:
        from app.drivers.retroarch import RetroArchDriver
        from app.streaming.ffmpeg_webrtc import FFmpegStreamingProvider

        _player_service = PlayerService(
            RetroArchDriver(), FFmpegStreamingProvider(), _build_input_provider()
        )
    return _player_service
