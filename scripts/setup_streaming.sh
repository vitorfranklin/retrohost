#!/usr/bin/env bash
# Configure the Raspberry Pi to run RetroArch headless (no physical TV/monitor)
# and expose raw video via FIFO for external capture by ffmpeg.
#
# Idempotent: can be run more than once without duplicating configuration.
# Requires a manual reboot after modifying /boot/firmware/config.txt the first time.
set -euo pipefail

BOOT_CONFIG="/boot/firmware/config.txt"
RETROARCH_CFG="$HOME/.config/retroarch/retroarch.cfg"

echo "==> Configuring $BOOT_CONFIG (forced HDMI hotplug for offscreen rendering)"
if ! grep -q "^hdmi_force_hotplug=1" "$BOOT_CONFIG"; then
    sudo cp "$BOOT_CONFIG" "${BOOT_CONFIG}.bak.$(date +%s)"
    sudo tee -a "$BOOT_CONFIG" > /dev/null <<'EOF'

# RetroHost: force display detection even without a physically connected TV/monitor
# (required for RetroArch to render offscreen for headless capture/streaming)
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=85
EOF
    echo "    Added. REBOOT REQUIRED (sudo reboot) before testing streaming."
else
    echo "    Already configured, nothing to do."
fi

echo "==> Configuring $RETROARCH_CFG"
if [[ ! -f "$RETROARCH_CFG" ]]; then
    echo "    File does not exist yet. Run 'retroarch' once (even if it fails) to generate it, then re-run this script."
    exit 1
fi

set_cfg_key() {
    local key="$1"
    local value="$2"
    if grep -qE "^${key} = " "$RETROARCH_CFG"; then
        sed -i "s/^${key} = .*/${key} = \"${value}\"/" "$RETROARCH_CFG"
    else
        echo "${key} = \"${value}\"" >> "$RETROARCH_CFG"
    fi
    echo "    ${key} = \"${value}\""
}

# video_driver=null: RetroArch does not need a physical display or compatible
#   GPU/shader — the game is captured via record/FIFO, not displayed locally.
# input_driver=udev: does not depend on X11/Wayland (the "x" driver requires a graphical session).
# audio_driver=alsa: PulseAudio does not run in this headless setup; ALSA accesses
#   /dev/snd directly. Without working audio, audio_sync cannot throttle the core
#   and the game runs in "fast forward" (observed and fixed bug).
set_cfg_key "video_driver" "null"
set_cfg_key "video_context_driver" ""
set_cfg_key "video_shader_enable" "false"
set_cfg_key "input_driver" "udev"
set_cfg_key "audio_driver" "alsa"

echo "==> Done."
