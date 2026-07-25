"""Remote input interface (browser gamepad -> input device on the Pi).

Concrete implementation: UInputKeyboardProvider (app/input/uinput_keyboard.py),
which exposes a virtual keyboard via /dev/uinput, read by RetroArch
(input_driver=udev) as if it were a physical USB keyboard.
"""
from abc import ABC, abstractmethod


class InputProvider(ABC):
    @abstractmethod
    def connect(self) -> None:
        """Create/register the input device. Idempotent if already connected."""

    @abstractmethod
    def disconnect(self) -> None:
        """Remove the input device, if it exists. Idempotent."""

    @abstractmethod
    def press(self, key: str) -> None:
        """Signal key pressed. `key` is a logical name (see keymap.py)."""

    @abstractmethod
    def release(self, key: str) -> None:
        """Signal key released."""

    @abstractmethod
    def release_all(self) -> None:
        """Release all currently pressed keys (e.g. on abrupt disconnect)."""
