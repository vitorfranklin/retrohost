#!/usr/bin/env bash
# RetroHost — complete setup for Raspberry Pi.
#
# Usage (from the project root after cloning):
#   bash scripts/setup.sh
#
# What this script does:
#   1. Verify system dependencies (retroarch, ffmpeg, python3, git)
#   2. Configure RetroArch for headless mode (no TV/monitor)
#   3. Download MediaMTX (WebRTC/WHEP server)
#   4. Install and configure remote input via uinput
#   5. Create and install the Python venv with backend dependencies
#   6. Compile the PS1 core (PCSX-ReARMed) from source, if not already present
#   7. Install systemd services (mediamtx + retrohost)
#   8. (Optional) Configure ROM storage via network (CIFS/Samba)
#   9. Start services and validate the installation
#
# Idempotent: can be run more than once without issues.
# Requires sudo for: apt-get, systemd, udev, sudoers, /boot/firmware/config.txt
set -euo pipefail

# ---------------------------------------------------------------------------
# Colors and helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${BOLD}==>${NC} $*"; }
ok()      { echo -e "    ${GREEN}✓${NC} $*"; }
warn()    { echo -e "    ${YELLOW}!${NC} $*"; }
error()   { echo -e "    ${RED}✗${NC} $*" >&2; }
ask()     { echo -e "${BOLD}?${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}RetroHost — Raspberry Pi Setup${NC}"
echo "Projeto em: $PROJECT_ROOT"
echo ""

# ---------------------------------------------------------------------------
# 1. Check platform
# ---------------------------------------------------------------------------
info "Checking platform..."

if [[ "$(uname -s)" != "Linux" ]]; then
    error "This script is for Linux (Raspberry Pi OS). Use Docker for other systems."
    exit 1
fi

ARCH="$(uname -m)"
if [[ "$ARCH" != "armv7l" && "$ARCH" != "aarch64" ]]; then
    warn "Architecture '$ARCH' is not ARM — this setup is for Raspberry Pi."
    warn "For x86_64, use Docker (see README.md)."
    read -r -p "    Continue anyway? [y/N] " CONFIRM
    [[ "${CONFIRM,,}" == "y" ]] || exit 1
fi

ok "Platform: $(uname -s) $ARCH"

# ---------------------------------------------------------------------------
# 2. Detect user and installation directory
# ---------------------------------------------------------------------------
info "User configuration..."

CURRENT_USER="${SUDO_USER:-$(whoami)}"
DEFAULT_INSTALL_DIR="/home/$CURRENT_USER/retrohost"

ask "System user [$CURRENT_USER]: "
read -r INPUT_USER
INSTALL_USER="${INPUT_USER:-$CURRENT_USER}"

ask "Installation directory [$DEFAULT_INSTALL_DIR]: "
read -r INPUT_DIR
INSTALL_DIR="${INPUT_DIR:-$DEFAULT_INSTALL_DIR}"

if [[ "$INSTALL_DIR" != "$PROJECT_ROOT" ]]; then
    warn "The project was cloned in '$PROJECT_ROOT' but the installation directory is '$INSTALL_DIR'."
    warn "The .service files will use '$INSTALL_DIR'. Make sure the project is there."
fi

ok "User: $INSTALL_USER"
ok "Directory: $INSTALL_DIR"

# ---------------------------------------------------------------------------
# 3. Check system dependencies
# ---------------------------------------------------------------------------
info "Checking system dependencies..."

MISSING=()
for cmd in retroarch ffmpeg python3 curl git; do
    if ! command -v "$cmd" &>/dev/null; then
        MISSING+=("$cmd")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    warn "Missing dependencies: ${MISSING[*]}"
    info "Installing dependencies via apt..."
    sudo apt-get update -qq
    PKG_MAP=( "retroarch:retroarch" "ffmpeg:ffmpeg" "python3:python3-venv" "curl:curl" "git:git" )
    PKGS_TO_INSTALL=()
    for entry in "${PKG_MAP[@]}"; do
        cmd="${entry%%:*}"; pkg="${entry##*:}"
        if [[ " ${MISSING[*]} " == *" $cmd "* ]]; then
            PKGS_TO_INSTALL+=("$pkg")
        fi
    done
    sudo apt-get install -y --no-install-recommends "${PKGS_TO_INSTALL[@]}"
fi

ok "retroarch: $(retroarch --version 2>&1 | head -1)"
ok "ffmpeg: $(ffmpeg -version 2>&1 | head -1 | cut -d' ' -f3)"
ok "python3: $(python3 --version)"

# ---------------------------------------------------------------------------
# 4. Configure RetroArch headless
# ---------------------------------------------------------------------------
info "Configuring RetroArch for headless mode..."

BOOT_CONFIG="/boot/firmware/config.txt"
RETROARCH_CFG="$HOME/.config/retroarch/retroarch.cfg"

# Generate retroarch.cfg if it doesn't exist
if [[ ! -f "$RETROARCH_CFG" ]]; then
    warn "retroarch.cfg not found. Generating..."
    mkdir -p "$(dirname "$RETROARCH_CFG")"
    retroarch --headless 2>/dev/null || true
fi

if [[ ! -f "$RETROARCH_CFG" ]]; then
    warn "Could not generate retroarch.cfg automatically."
    warn "Run 'retroarch' once manually and re-run this script."
    mkdir -p "$(dirname "$RETROARCH_CFG")"
    touch "$RETROARCH_CFG"
fi

set_cfg_key() {
    local key="$1" value="$2"
    if grep -qE "^${key} = " "$RETROARCH_CFG" 2>/dev/null; then
        sed -i "s|^${key} = .*|${key} = \"${value}\"|" "$RETROARCH_CFG"
    else
        echo "${key} = \"${value}\"" >> "$RETROARCH_CFG"
    fi
}

set_cfg_key "video_driver"         "null"
set_cfg_key "video_context_driver" ""
set_cfg_key "video_shader_enable"  "false"
set_cfg_key "input_driver"         "udev"
set_cfg_key "audio_driver"         "alsa"

ok "retroarch.cfg configured ($RETROARCH_CFG)"

# Configure /boot/firmware/config.txt (force headless display detection)
if [[ -f "$BOOT_CONFIG" ]]; then
    if ! grep -q "^hdmi_force_hotplug=1" "$BOOT_CONFIG"; then
        sudo cp "$BOOT_CONFIG" "${BOOT_CONFIG}.bak.$(date +%s)"
        sudo tee -a "$BOOT_CONFIG" > /dev/null <<'EOF'

# RetroHost: force display detection even without a connected TV (headless)
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=85
EOF
        warn "Added hdmi_force_hotplug to $BOOT_CONFIG."
        NEEDS_REBOOT=1
    else
        ok "$BOOT_CONFIG already configured"
    fi
fi

# ---------------------------------------------------------------------------
# 5. Download MediaMTX
# ---------------------------------------------------------------------------
info "Installing MediaMTX (WebRTC/WHEP server)..."

BIN_DIR="$PROJECT_ROOT/bin"
MEDIAMTX_BIN="$BIN_DIR/mediamtx"
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-v1.19.2}"

if [[ -f "$MEDIAMTX_BIN" ]]; then
    ok "MediaMTX já instalado ($("$MEDIAMTX_BIN" --version 2>&1 | head -1))"
else
    # Detect the correct asset for this architecture
    case "$ARCH" in
        armv7l)  ASSET="mediamtx_${MEDIAMTX_VERSION}_linux_armv7.tar.gz" ;;
        aarch64) ASSET="mediamtx_${MEDIAMTX_VERSION}_linux_arm64v8.tar.gz" ;;
        x86_64)  ASSET="mediamtx_${MEDIAMTX_VERSION}_linux_amd64.tar.gz" ;;
        *)       error "Architecture '$ARCH' not supported for MediaMTX."; exit 1 ;;
    esac
    URL="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/${ASSET}"

    mkdir -p "$BIN_DIR"
    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT

    info "  Downloading $ASSET..."
    curl -sL -o "$TMP_DIR/$ASSET" "$URL"
    tar -xzf "$TMP_DIR/$ASSET" -C "$TMP_DIR" mediamtx
    mv "$TMP_DIR/mediamtx" "$MEDIAMTX_BIN"
    chmod +x "$MEDIAMTX_BIN"

    ok "MediaMTX instalado ($("$MEDIAMTX_BIN" --version 2>&1 | head -1))"
fi

# ---------------------------------------------------------------------------
# 6. Configure remote input (uinput)
# ---------------------------------------------------------------------------
info "Configuring remote input via uinput..."

sudo apt-get install -y --no-install-recommends python3-evdev -qq

if ! lsmod | grep -q '^uinput'; then
    sudo modprobe uinput
fi
if [[ ! -f /etc/modules-load.d/uinput.conf ]]; then
    echo "uinput" | sudo tee /etc/modules-load.d/uinput.conf > /dev/null
fi

UDEV_RULE_SRC="$PROJECT_ROOT/config/udev/99-uinput.rules"
UDEV_RULE_DEST="/etc/udev/rules.d/99-uinput.rules"
if [[ ! -f "$UDEV_RULE_DEST" ]] || ! diff -q "$UDEV_RULE_SRC" "$UDEV_RULE_DEST" &>/dev/null; then
    sudo cp "$UDEV_RULE_SRC" "$UDEV_RULE_DEST"
    sudo udevadm control --reload-rules
    sudo udevadm trigger --name-match=uinput
fi

ok "uinput configurado ($(ls -l /dev/uinput | awk '{print $1, $3, $4}'))"

# ---------------------------------------------------------------------------
# 7. Create Python venv and install dependencies
# ---------------------------------------------------------------------------
info "Setting up backend Python environment..."

VENV_DIR="$PROJECT_ROOT/backend/.venv"

if [[ -f "$VENV_DIR/pyvenv.cfg" ]] && grep -q "include-system-site-packages = true" "$VENV_DIR/pyvenv.cfg"; then
    ok "venv already configured with system-site-packages"
else
    rm -rf "$VENV_DIR"
    python3 -m venv --system-site-packages "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --quiet -r "$PROJECT_ROOT/backend/requirements.txt"
ok "Dependencies installed (fastapi, uvicorn, sqlalchemy, pydantic...)"

# ---------------------------------------------------------------------------
# 8. Compile PCSX-ReARMed (PS1 core), if not already present
# ---------------------------------------------------------------------------
# PCSX-ReARMed has no apt package on Raspberry Pi OS (unlike Beetle PSX used
# in Docker/x86_64) and is much lighter for Pi hardware, so we compile
# it from source directly into emulator/cores/.
PS1_CORE_DEST="$PROJECT_ROOT/emulator/cores/pcsx_rearmed_libretro.so"

if [[ ! -f "$PS1_CORE_DEST" ]]; then
    info "Compiling PS1 core (PCSX-ReARMed) from source..."

    sudo apt-get install -y --no-install-recommends build-essential git -qq

    # Map Pi model + architecture to the 'platform=' target in the Makefile.
    # See pcsx_rearmed's Makefile.libretro for the list of platforms.
    PI_MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo '')"
    PCSX_PLATFORM=""
    case "$PI_MODEL" in
        *"Raspberry Pi 5"*)   PCSX_PLATFORM="rpi4_64" ;;   # no dedicated target; rpi4_64 is the closest (arm64)
        *"Raspberry Pi 4"*)   [[ "$ARCH" == "aarch64" ]] && PCSX_PLATFORM="rpi4_64" || PCSX_PLATFORM="rpi4" ;;
        *"Raspberry Pi 3"*)   [[ "$ARCH" == "aarch64" ]] && PCSX_PLATFORM="rpi3_64" || PCSX_PLATFORM="rpi3" ;;
        *"Raspberry Pi 2"*)   PCSX_PLATFORM="rpi2" ;;
        *"Raspberry Pi Model"*|*"Raspberry Pi Zero"*) PCSX_PLATFORM="rpi1" ;;
        *)
            warn "Pi model not recognized ('$PI_MODEL') — using generic target."
            [[ "$ARCH" == "aarch64" ]] && PCSX_PLATFORM="rpi3_64" || PCSX_PLATFORM="rpi2"
            ;;
    esac
    ok "Build target detected: platform=$PCSX_PLATFORM ($PI_MODEL, $ARCH)"

    PCSX_SRC_DIR="$PROJECT_ROOT/emulator/pcsx_rearmed_src"
    if [[ ! -d "$PCSX_SRC_DIR" ]]; then
        git clone --depth 1 --recursive \
            https://github.com/libretro/pcsx_rearmed.git "$PCSX_SRC_DIR"
    fi

    if ( cd "$PCSX_SRC_DIR" && make -f Makefile.libretro "platform=$PCSX_PLATFORM" -j"$(nproc)" ); then
        mkdir -p "$PROJECT_ROOT/emulator/cores"
        cp "$PCSX_SRC_DIR/pcsx_rearmed_libretro.so" "$PS1_CORE_DEST"
        ok "PS1 core compiled: $PS1_CORE_DEST"
    else
        error "Failed to compile PCSX-ReARMed. See $PCSX_SRC_DIR to debug."
        warn "cores.json will lack 'ps1' — configure manually later."
    fi
else
    ok "PS1 core already compiled ($PS1_CORE_DEST)"
fi

# ---------------------------------------------------------------------------
# 9. Configure cores.json
# ---------------------------------------------------------------------------
info "Configuring libretro core mapping..."

CORES_JSON="$PROJECT_ROOT/config/cores.json"
CORES_EXAMPLE="$PROJECT_ROOT/config/cores.json.example"

if [[ ! -f "$CORES_JSON" ]]; then
    # Auto-detect installed cores via apt
    LIBRETRO_DIRS=(
        "/usr/lib/arm-linux-gnueabihf/libretro"
        "/usr/lib/aarch64-linux-gnu/libretro"
        "/usr/lib/x86_64-linux-gnu/libretro"
    )
    LIBRETRO_DIR=""
    for d in "${LIBRETRO_DIRS[@]}"; do
        [[ -d "$d" ]] && LIBRETRO_DIR="$d" && break
    done

    # Search for known cores
    PS1_CORE=""
    SNES_CORE=""

    # PS1: first the one compiled above, then any PS1 core already installed via apt
    [[ -f "$PS1_CORE_DEST" ]] && PS1_CORE="$PS1_CORE_DEST"
    [[ -z "$PS1_CORE" && -n "$LIBRETRO_DIR" && -f "$LIBRETRO_DIR/pcsx_rearmed_libretro.so" ]] && \
        PS1_CORE="$LIBRETRO_DIR/pcsx_rearmed_libretro.so"

    # SNES: bsnes-mercury (apt) or snes9x
    for candidate in \
        "$PROJECT_ROOT/emulator/cores/snes9x_libretro.so" \
        "${LIBRETRO_DIR:-/dev/null}/bsnes_mercury_performance_libretro.so" \
        "${LIBRETRO_DIR:-/dev/null}/snes9x_libretro.so"
    do
        [[ -f "$candidate" ]] && SNES_CORE="$candidate" && break
    done

    {
        echo "{"
        FIRST=1
        write_entry() {
            local key="$1" path="$2"
            [[ -z "$path" ]] && return
            [[ $FIRST -eq 0 ]] && echo ","
            printf '  "%s": "%s"' "$key" "$path"
            FIRST=0
        }
        write_entry "ps1"  "$PS1_CORE"
        write_entry "snes" "$SNES_CORE"
        echo ""
        echo "}"
    } > "$CORES_JSON"

    if [[ -n "$PS1_CORE" || -n "$SNES_CORE" ]]; then
        ok "cores.json generated automatically:"
        [[ -n "$PS1_CORE"  ]] && ok "  ps1  → $PS1_CORE"
        [[ -n "$SNES_CORE" ]] && ok "  snes → $SNES_CORE"
    else
        warn "No cores detected automatically."
        warn "Edit $CORES_JSON manually with the paths to your .so cores"
        warn "Example at $CORES_EXAMPLE"
    fi
elif grep -q "YOUR_USER\|/home/USER/" "$CORES_JSON"; then
    # cores.json was manually copied from .example and still has the user
    # placeholder — replace it with the real user instead of leaving a broken path.
    sed -i \
        -e "s|YOUR_USER|$INSTALL_USER|g" \
        -e "s|/home/USER/|/home/$INSTALL_USER/|g" \
        "$CORES_JSON"
    ok "cores.json already existed with user placeholder — updated to '$INSTALL_USER'"
else
    ok "cores.json already exists — keeping"
fi

# ---------------------------------------------------------------------------
# 10. (Optional) ROM storage via network (CIFS/Samba)
# ---------------------------------------------------------------------------
echo ""
ask "Configure ROM storage via network (CIFS/Samba)? [y/N]: "
read -r SETUP_CIFS

if [[ "${SETUP_CIFS,,}" == "y" ]]; then
    info "Configuring CIFS storage..."

    sudo apt-get install -y --no-install-recommends cifs-utils -qq

    MOUNT_POINT="/mnt/homegames-roms"
    sudo mkdir -p "$MOUNT_POINT"

    SUDOERS_SRC="$PROJECT_ROOT/config/sudoers/homegames-mount"
    SUDOERS_DEST="/etc/sudoers.d/homegames-mount"

    # Replace YOUR_USER with the real user before installing
    TMP_SUDOERS="$(mktemp)"
    sed "s/YOUR_USER/$INSTALL_USER/g" "$SUDOERS_SRC" > "$TMP_SUDOERS"

    if sudo visudo -c -f "$TMP_SUDOERS" &>/dev/null; then
        sudo cp "$TMP_SUDOERS" "$SUDOERS_DEST"
        sudo chmod 440 "$SUDOERS_DEST"
        ok "Sudoers rule installed for $INSTALL_USER"
    else
        error "Invalid sudoers rule — not applied"
    fi
    rm -f "$TMP_SUDOERS"

    ok "CIFS configured. Use 'Configure storage' in the web interface to point to the share."
else
    ok "CIFS storage skipped — using local ROMs in $PROJECT_ROOT/emulator/roms/"
fi

# ---------------------------------------------------------------------------
# 11. Install systemd services
# ---------------------------------------------------------------------------
info "Installing systemd services..."

for SERVICE in mediamtx homegames; do
    SRC="$PROJECT_ROOT/scripts/${SERVICE}.service"
    DEST="/etc/systemd/system/${SERVICE}.service"

    # Replace YOUR_USER and /home/YOUR_USER/retrohost with the real values
    TMP_SERVICE="$(mktemp)"
    sed \
        -e "s|YOUR_USER|$INSTALL_USER|g" \
        -e "s|/home/YOUR_USER/retrohost|$INSTALL_DIR|g" \
        "$SRC" > "$TMP_SERVICE"

    sudo cp "$TMP_SERVICE" "$DEST"
    rm -f "$TMP_SERVICE"
    ok "$SERVICE.service installed"
done

sudo systemctl daemon-reload
sudo systemctl enable mediamtx homegames
ok "Services enabled to start at boot"

# ---------------------------------------------------------------------------
# 12. Start services
# ---------------------------------------------------------------------------
info "Starting services..."

sudo systemctl restart mediamtx
sleep 2
sudo systemctl restart homegames
sleep 2

MEDIAMTX_STATUS="$(systemctl is-active mediamtx 2>/dev/null || echo 'inactive')"
HOMEGAMES_STATUS="$(systemctl is-active homegames 2>/dev/null || echo 'inactive')"

if [[ "$MEDIAMTX_STATUS" == "active" ]]; then
    ok "mediamtx: active"
else
    error "mediamtx: $MEDIAMTX_STATUS (see: journalctl -u mediamtx -n 20)"
fi

if [[ "$HOMEGAMES_STATUS" == "active" ]]; then
    ok "homegames (API): active"
else
    error "homegames: $HOMEGAMES_STATUS (see: journalctl -u homegames -n 20)"
fi

# ---------------------------------------------------------------------------
# 13. Final validation
# ---------------------------------------------------------------------------
info "Validating installation..."

sleep 1
HTTP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo '000')"

if [[ "$HTTP_STATUS" == "200" ]]; then
    ok "API responding at http://localhost:8000/health"
else
    warn "API has not responded yet (HTTP $HTTP_STATUS) — may need a few more seconds"
    warn "Test: curl http://localhost:8000/health"
fi

# ---------------------------------------------------------------------------
# Resumo final
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}========================================${NC}"
echo -e "${BOLD}  RetroHost configured!${NC}"
echo -e "${BOLD}========================================${NC}"
echo ""
echo "  Acesse: http://$(hostname -I | awk '{print $1}'):8000"
echo ""
echo "  Next steps:"
echo "  1. Put ROMs in: $PROJECT_ROOT/emulator/roms/<console>/"
echo "  2. Click 'Scan library' in the web interface"
if [[ -z "${PS1_CORE:-}" && -z "${SNES_CORE:-}" ]]; then
    echo "  3. Configure cores in: $PROJECT_ROOT/config/cores.json"
fi
echo ""
echo "  Logs:"
echo "    journalctl -u homegames -f"
echo "    journalctl -u mediamtx -f"
echo ""

if [[ "${NEEDS_REBOOT:-0}" == "1" ]]; then
    echo -e "  ${YELLOW}⚠ REBOOT REQUIRED${NC} to apply headless HDMI settings:"
    echo "    sudo reboot"
    echo ""
fi
