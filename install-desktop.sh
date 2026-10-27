#!/bin/sh
# Puts a Mesh Chat icon on the Raspberry Pi desktop and in the app menu,
# and starts Mesh Chat automatically when you log in after a power cycle.
set -eu
cd "$(dirname "$(readlink -f "$0")")"
ROOT=$(pwd)

if [ "$(uname -s)" != "Linux" ]; then
    echo "Copy this folder to the Pi, then run: bash install-desktop.sh" >&2
    exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user, not with sudo." >&2
    echo "The shortcut has to land on your desktop, not root's." >&2
    exit 1
fi

chmod +x "$ROOT/mesh-chat.sh"
sh "$ROOT/mesh-chat.sh" --setup
sh "$ROOT/mesh-chat.sh" --install-shortcuts

echo "Mesh Chat is on the desktop and will open on login."
