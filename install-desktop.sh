#!/bin/sh
# Puts a Mesh Chat icon on the Raspberry Pi desktop and in the app menu.
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

mkdir -p "$HOME/Desktop" "$HOME/.local/share/applications"

write_entry() {
    cat > "$1" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Mesh Chat
Comment=Text the T9 radios through the Heltec Tracker
Exec="$ROOT/mesh-chat.sh"
Path=$ROOT
Icon=mail-message-new
Terminal=false
Categories=Network;
StartupNotify=true
EOF
}

write_entry "$HOME/Desktop/Mesh Chat.desktop"
write_entry "$HOME/.local/share/applications/mesh-chat.desktop"
chmod +x "$HOME/Desktop/Mesh Chat.desktop" "$HOME/.local/share/applications/mesh-chat.desktop"
if command -v gio >/dev/null 2>&1; then
    gio set "$HOME/Desktop/Mesh Chat.desktop" metadata::trusted true || true
fi
echo "Mesh Chat is on the desktop."
