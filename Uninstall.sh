#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/installer"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 was not found. Install Python 3.11+ from your distro's package manager, then run this again."
    exit 1
fi

python3 uninstall.py
