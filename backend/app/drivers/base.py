"""Emulator driver interface.

The API should never talk directly to RetroArch or any other emulator.
All interaction goes through a concrete EmulatorDriver implementation, which
allows swapping the emulation backend (RetroArch, another libretro frontend,
a standalone emulator, etc.) without changing the service/API layer.
"""
from abc import ABC, abstractmethod
from typing import Any


class EmulatorDriverError(Exception):
    """Generic emulator driver error."""


class CoreNotConfiguredError(EmulatorDriverError):
    """Raised when the required core for the console is not available."""


class EmulatorDriver(ABC):
    @abstractmethod
    def launch(self, game: Any) -> None:
        """Start running `game`. Does not block the calling thread."""

    @abstractmethod
    def stop(self) -> None:
        """Terminate the running process, if any."""

    @abstractmethod
    def status(self) -> bool:
        """Return True if a game is currently running."""

    def pause(self) -> None:
        raise NotImplementedError("pause() is not yet supported by this driver")

    def resume(self) -> None:
        raise NotImplementedError("resume() is not yet supported by this driver")

    def save_state(self) -> None:
        raise NotImplementedError("save_state() is not yet supported by this driver")

    def load_state(self) -> None:
        raise NotImplementedError("load_state() is not yet supported by this driver")

    def screenshot(self) -> None:
        raise NotImplementedError("screenshot() is not yet supported by this driver")
