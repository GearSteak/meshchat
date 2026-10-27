#!/bin/sh
# Launch Mesh Chat. Also used by install-desktop.sh as:
#   ./mesh-chat.sh --setup
#   ./mesh-chat.sh --install-shortcuts
set -eu
cd "$(dirname "$(readlink -f "$0")")"
ROOT=$(pwd)

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/meshchat"
mkdir -p "$DATA"
LOG="$DATA/startup.log"

install_shortcuts() {
    mkdir -p "$HOME/Desktop" "$HOME/.local/share/applications" "$HOME/.config/autostart"

    write_entry() {
        autostart=$1
        target=$2
        if [ "$autostart" = "1" ]; then
            # Wait for the desktop + USB before opening the window after boot.
            exec_line="sh -c \"sleep 8; exec \\\"$ROOT/mesh-chat.sh\\\"\""
            extra="X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=8
"
        else
            exec_line="\"$ROOT/mesh-chat.sh\""
            extra=""
        fi
        cat > "$target" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Mesh Chat
Comment=Text the T9 radios through the Heltec Tracker
Exec=$exec_line
Path=$ROOT
Icon=mail-message-new
Terminal=false
Categories=Network;
StartupNotify=true
$extra
EOF
        chmod +x "$target"
    }

    write_entry 0 "$HOME/Desktop/Mesh Chat.desktop"
    write_entry 0 "$HOME/.local/share/applications/mesh-chat.desktop"
    write_entry 1 "$HOME/.config/autostart/mesh-chat.desktop"
    if command -v gio >/dev/null 2>&1; then
        gio set "$HOME/Desktop/Mesh Chat.desktop" metadata::trusted true || true
    fi
}

if [ "${1:-}" = "--install-shortcuts" ]; then
    install_shortcuts
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is missing. Install it with: sudo apt install python3 python3-venv python3-tk" | tee "$LOG" >&2
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "The window toolkit is missing. Install it with: sudo apt install python3-tk" | tee "$LOG" >&2
    exit 1
fi

need_venv=0
if [ ! -x .venv/bin/python ]; then
    need_venv=1
elif ! .venv/bin/python -c "import sys" >/dev/null 2>&1; then
    need_venv=1
fi

if [ "$need_venv" -eq 1 ]; then
    # A copy from Windows has a .venv that Pi Python cannot run.
    rm -rf .venv
    python3 -m venv .venv || {
        echo "Could not create a virtualenv. Install it with: sudo apt install python3-venv" | tee "$LOG" >&2
        exit 1
    }
fi

if ! .venv/bin/python -c "import serial" >/dev/null 2>&1; then
    .venv/bin/python -m pip install -r requirements.txt
fi

if [ "${1:-}" = "--setup" ]; then
    exit 0
fi

# Keep the login autostart entry pointed at this checkout (Update moves files).
install_shortcuts

# Drop a stuck Python copy from a previous crash so the database is not locked.
# Do not match this shell script's own path.
pkill -f '/python(3)? -m meshchat' 2>/dev/null || true
pkill -f 'python(3)? -m meshchat' 2>/dev/null || true
sleep 0.3

set +e
# Force this checkout's package so an older pip copy cannot hide receive fixes.
export PYTHONPATH="$(pwd)${PYTHONPATH:+:$PYTHONPATH}"
.venv/bin/python -m meshchat "$@" >"$LOG" 2>&1
status=$?
set -e
if [ "$status" -ne 0 ]; then
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Mesh Chat" --text="Could not start. See $LOG" 2>/dev/null || true
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send "Mesh Chat" "Could not start. See $LOG" 2>/dev/null || true
    fi
fi
exit "$status"
