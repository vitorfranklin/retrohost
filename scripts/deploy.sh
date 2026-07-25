#!/usr/bin/env bash
# Syncs the local project to the Raspberry Pi via rsync over SSH.
#
# Usage:
#   RETROHOST_PI_USER=pi RETROHOST_PI_HOST=192.168.1.x ./scripts/deploy.sh
#   ./scripts/deploy.sh <ip>
#
# No hardcoded credentials: uses only the host/IP, with authentication via SSH key
# or the interactive password prompt from ssh/rsync.
set -euo pipefail

PI_USER="${RETROHOST_PI_USER:-pi}"
PI_HOST="${1:-${RETROHOST_PI_HOST:-}}"
PI_PATH="${RETROHOST_PI_PATH:-/home/${PI_USER}/retrohost/}"

if [[ -z "$PI_HOST" ]]; then
    echo "Error: provide the Raspberry Pi host." >&2
    echo "Usage: RETROHOST_PI_HOST=<ip> $0   or   $0 <ip>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rsync -avz --progress \
    --exclude ".git/" \
    --exclude ".venv/" \
    --exclude "__pycache__/" \
    --exclude "*.pyc" \
    --exclude "*.db" \
    --exclude "*-credentials" \
    --exclude "*-credenciais" \
    "$SCRIPT_DIR/" "${PI_USER}@${PI_HOST}:${PI_PATH}"

echo "Deploy complete to ${PI_USER}@${PI_HOST}:${PI_PATH}"
