"""Virtual keyboard via Linux uinput, used to translate browser gamepad state
into keys that RetroArch reads via input_driver=udev (as if it were a physical
USB keyboard plugged into the Pi).

Requires: uinput module loaded, /dev/uinput accessible by the service user
(see scripts/setup_input.sh) and the system package python3-evdev
(NOT via pip — see backend/requirements.txt; recompiling evdev on the Pi 3
would have the same build cost that led us to abandon uvicorn[standard]).

In the container (WSL2) there is no udevd to create /dev/input/eventN when
uinput registers the device — without that node, evdev cannot re-read the
capabilities and RetroArch (input_driver=udev) cannot see the keyboard. With
HOMEGAMES_MKNOD_INPUT=1, we create the node manually via mknod by reading the
major:minor from /sys (see _ensure_event_node). On the Pi this is disabled
(the system udev already creates the node).
"""
import os
import stat

from app.core.config import settings
from app.input.base import InputProvider
from app.input.keymap import EVDEV_KEYCODES

try:
    import evdev
except ImportError:  # environment without python3-evdev (e.g. dev on Windows)
    evdev = None


class UInputNotAvailableError(RuntimeError):
    pass


class UInputKeyboardProvider(InputProvider):
    def __init__(self, device_name: str = "RetroHost Virtual Keyboard") -> None:
        self._device_name = device_name
        self._device = None
        self._pressed_keys: set[str] = set()

    def connect(self) -> None:
        if self._device is not None:
            return
        if evdev is None:
            raise UInputNotAvailableError(
                "python3-evdev is not available. Run scripts/setup_input.sh on the Pi."
            )
        capabilities = {evdev.ecodes.EV_KEY: list(EVDEV_KEYCODES.values())}
        self._device = evdev.UInput(capabilities, name=self._device_name)
        if settings.MKNOD_INPUT:
            self._ensure_event_node()

    def _ensure_event_node(self) -> None:
        """Create /dev/input/eventN for the newly registered device, if missing.

        In environments without udevd (container WSL2), uinput registers the device in
        /sys/devices/virtual/input/inputN/eventN but no node appears in
        /dev/input. We read the major:minor from sysfs and create the node via mknod.
        Best-effort: any failure is silently ignored (RetroArch will give a clear error).
        """
        # We scan sysfs looking for the input with our device_name and create
        # the corresponding /dev/input/eventN node.
        try:
            os.makedirs("/dev/input", exist_ok=True)
            base = "/sys/devices/virtual/input"
            for input_dir in os.listdir(base):
                name_file = os.path.join(base, input_dir, "name")
                if not os.path.exists(name_file):
                    continue
                with open(name_file) as fh:
                    if fh.read().strip() != self._device_name:
                        continue
                for entry in os.listdir(os.path.join(base, input_dir)):
                    if not entry.startswith("event"):
                        continue
                    node = f"/dev/input/{entry}"
                    if os.path.exists(node):
                        continue
                    with open(os.path.join(base, input_dir, entry, "dev")) as fh:
                        major, minor = (int(x) for x in fh.read().strip().split(":"))
                    os.mknod(node, stat.S_IFCHR | 0o660, os.makedev(major, minor))
        except OSError:
            pass

    def disconnect(self) -> None:
        if self._device is None:
            return
        self._device.close()
        self._device = None
        self._pressed_keys.clear()

    def press(self, key: str) -> None:
        if self._emit(key, 1):
            self._pressed_keys.add(key)

    def release(self, key: str) -> None:
        if self._emit(key, 0):
            self._pressed_keys.discard(key)

    def release_all(self) -> None:
        for key in list(self._pressed_keys):
            self.release(key)

    def _emit(self, key: str, value: int) -> bool:
        if self._device is None:
            return False
        code = EVDEV_KEYCODES.get(key)
        if code is None:
            return False
        self._device.write(evdev.ecodes.EV_KEY, code, value)
        self._device.syn()
        return True
