#!/usr/bin/env bash
# Configure the Raspberry Pi to mount a ROM storage via network
# (CIFS/Samba), used by the service backend/app/services/storage.py to
# switch the ROM source without restarting the backend.
#
# Idempotent: can be run more than once without duplicating configuration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SUDOERS_SRC="$PROJECT_ROOT/config/sudoers/homegames-mount"
SUDOERS_DEST="/etc/sudoers.d/homegames-mount"
MOUNT_POINT="/mnt/homegames-roms"

echo "==> Installing cifs-utils via apt"
sudo apt-get install -y cifs-utils

echo "==> Ensuring mount point $MOUNT_POINT exists"
sudo mkdir -p "$MOUNT_POINT"

echo "==> Ensuring /etc/samba directory exists (destination for CIFS credentials file)"
sudo mkdir -p /etc/samba

echo "==> Installing sudoers rule (mount/umount with minimal scope)"
if [[ ! -f "$SUDOERS_DEST" ]] || ! diff -q "$SUDOERS_SRC" "$SUDOERS_DEST" >/dev/null 2>&1; then
    TMP_SUDOERS="$(mktemp)"
    cp "$SUDOERS_SRC" "$TMP_SUDOERS"
    if sudo visudo -c -f "$TMP_SUDOERS" >/dev/null; then
        sudo cp "$TMP_SUDOERS" "$SUDOERS_DEST"
        sudo chmod 440 "$SUDOERS_DEST"
        echo "    Rule installed and validated."
    else
        echo "    ERROR: invalid sudoers rule, not applied. See $TMP_SUDOERS"
        rm -f "$TMP_SUDOERS"
        exit 1
    fi
    rm -f "$TMP_SUDOERS"
else
    echo "    Already installed, nothing to do."
fi

echo "==> Done. Test: sudo -n mount -t cifs --help > /dev/null && echo OK"
