#!/usr/bin/env bash
# Configure the Raspberry Pi to expose a virtual keyboard via Linux uinput,
# used by InputProvider (backend/app/input/uinput_keyboard.py) to
# translate gamepad button events received from the browser into keys that
# RetroArch reads via input_driver=udev.
#
# Idempotent: can be run more than once without duplicating configuration.
# No reboot required — modprobe + udevadm trigger apply the permission at runtime.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
UDEV_RULE_SRC="$PROJECT_ROOT/config/udev/99-uinput.rules"
UDEV_RULE_DEST="/etc/udev/rules.d/99-uinput.rules"

echo "==> Installing python3-evdev via apt"
sudo apt-get install -y python3-evdev

echo "==> Ensuring uinput module is loaded and persistent at boot"
if ! lsmod | grep -q '^uinput'; then
    sudo modprobe uinput
fi
if [[ ! -f /etc/modules-load.d/uinput.conf ]]; then
    echo "uinput" | sudo tee /etc/modules-load.d/uinput.conf > /dev/null
fi

echo "==> Installing udev rule for /dev/uinput"
if [[ ! -f "$UDEV_RULE_DEST" ]] || ! diff -q "$UDEV_RULE_SRC" "$UDEV_RULE_DEST" >/dev/null 2>&1; then
    sudo cp "$UDEV_RULE_SRC" "$UDEV_RULE_DEST"
    sudo udevadm control --reload-rules
    sudo udevadm trigger --name-match=uinput
    echo "    Rule installed and applied."
else
    echo "    Already installed, nothing to do."
fi

echo "==> Current permission of /dev/uinput:"
ls -l /dev/uinput

echo "==> Recreating backend venv with --system-site-packages (access to python3-evdev)"
cd "$PROJECT_ROOT/backend"
if [[ -f .venv/pyvenv.cfg ]] && grep -q "include-system-site-packages = true" .venv/pyvenv.cfg; then
    echo "    venv already configured, nothing to do."
else
    rm -rf .venv
    python3 -m venv --system-site-packages .venv
    .venv/bin/pip install -r requirements.txt
    echo "    venv recreated with access to system-site-packages."
fi

echo "==> Done. Test: .venv/bin/python -c 'import evdev; print(evdev.__version__)'"
