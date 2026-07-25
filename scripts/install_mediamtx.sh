#!/usr/bin/env bash
# Downloads the mediamtx binary to bin/mediamtx and verifies SHA-256.
# mediamtx is not distributed via apt; we download the official GitHub release.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$PROJECT_ROOT/bin"
VERSION="${MEDIAMTX_VERSION:-v1.19.2}"

# Known SHA-256 hashes for v1.19.2 (calculated from the official release).
# Update these hashes when changing VERSION.
declare -A KNOWN_SHA256=(
    ["linux_armv7"]="de0afed5ba33df231a6c3321207b4a906f1da9be7ce8b3efac008928e982ca6d"
    ["linux_arm64v8"]="0019dfc4b32d63c1392aa264aed2253c1e0c2fb09216f8e2cc269bbfb8bb49b5"
    ["linux_amd64"]="f9c601cc303ceca8fad2883917b022882672c5bc56311e92dbceb16e5f20c60c"
)

ARCH="$(uname -m)"
case "$ARCH" in
    armv7l)  PLATFORM="linux_armv7" ;;
    aarch64) PLATFORM="linux_arm64v8" ;;
    x86_64)  PLATFORM="linux_amd64" ;;
    *) echo "Architecture '$ARCH' not supported." >&2; exit 1 ;;
esac

ASSET="mediamtx_${VERSION}_${PLATFORM}.tar.gz"
URL="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/${ASSET}"
EXPECTED_SHA="${KNOWN_SHA256[$PLATFORM]}"

mkdir -p "$BIN_DIR"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading ${URL}..."
curl -sL -o "$TMP_DIR/$ASSET" "$URL"

echo "Verifying integrity (SHA-256)..."
ACTUAL_SHA="$(sha256sum "$TMP_DIR/$ASSET" | awk '{print $1}')"
if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
    echo "ERROR: SHA-256 mismatch!" >&2
    echo "  Expected: $EXPECTED_SHA" >&2
    echo "  Got:      $ACTUAL_SHA" >&2
    exit 1
fi
echo "  OK: $ACTUAL_SHA"

tar -xzf "$TMP_DIR/$ASSET" -C "$TMP_DIR" mediamtx
mv "$TMP_DIR/mediamtx" "$BIN_DIR/mediamtx"
chmod +x "$BIN_DIR/mediamtx"

echo "Installed at $BIN_DIR/mediamtx ($("$BIN_DIR/mediamtx" --version))"
