#!/bin/sh
# Launch Mesh Chat. Also used by install-desktop.sh as: ./mesh-chat.sh --setup
set -eu
cd "$(dirname "$(readlink -f "$0")")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is missing. Install it with: sudo apt install python3 python3-venv python3-tk" >&2
    exit 1
fi

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "The window toolkit is missing. Install it with: sudo apt install python3-tk" >&2
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
        echo "Could not create a virtualenv. Install it with: sudo apt install python3-venv" >&2
        exit 1
    }
fi

if ! .venv/bin/python -c "import serial" >/dev/null 2>&1; then
    .venv/bin/python -m pip install -r requirements.txt
fi

if [ "${1:-}" = "--setup" ]; then
    exit 0
fi

exec .venv/bin/python -m meshchat
