"""Flash the Heltec Wireless Tracker V2 that Mesh Chat talks to.

Only a Tracker USB port is eligible. The mouse jiggler and the Heltec LoRa
phone use other USB ids and are left alone.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

from meshchat.link import JIGGLER_PID, TRACKER_PID, TRACKER_VID

LAYOUT = (
    ("0x0", "bootloader.bin"),
    ("0x8000", "partitions.bin"),
    ("0xe000", "boot_app0.bin"),
    ("0x10000", "firmware.bin"),
)


def firmware_dir(root: Path) -> Path:
    return root / "firmware" / "dist"


def bundled_version(root: Path) -> str:
    path = firmware_dir(root) / "version.txt"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def firmware_needs_update(device: str, bundled: str, plugged_in: bool) -> bool:
    if not plugged_in or not bundled:
        return False
    return device != bundled


def tracker_port_names(ports=None) -> list[str]:
    found: list[str] = []
    for port in list_ports.comports() if ports is None else ports:
        vid = getattr(port, "vid", None)
        pid = getattr(port, "pid", None)
        if pid == JIGGLER_PID:
            continue
        if vid == TRACKER_VID and pid == TRACKER_PID:
            name = getattr(port, "device", "") or ""
            if name:
                found.append(name)
    return found


def flash_argv(port: str, dist: Path) -> list[str]:
    missing = [name for _offset, name in LAYOUT if not (dist / name).is_file()]
    if missing:
        raise RuntimeError("Firmware files are missing: " + ", ".join(missing))
    command = [
        sys.executable,
        "-m",
        "esptool",
        "--chip",
        "esp32s3",
        "--port",
        port,
        "--baud",
        "460800",
        "--before",
        "no_reset",
        "--after",
        "hard_reset",
        "write_flash",
        "--flash_mode",
        "dio",
        "--flash_freq",
        "80m",
        "--flash_size",
        "8MB",
    ]
    for offset, name in LAYOUT:
        command.append(offset)
        command.append(str(dist / name))
    return command


def update_tracker(root: Path) -> str:
    _ensure_esptool()
    dist = firmware_dir(root)
    ports = tracker_port_names()
    if not ports:
        return "The Tracker is not plugged in, so its firmware was not updated."
    _enter_bootloader(ports[0])
    boot_port = _wait_for_bootloader()
    _run(flash_argv(boot_port, dist))
    return "Tracker firmware updated."


def _enter_bootloader(port: str) -> None:
    opened = serial.Serial()
    opened.port = port
    opened.baudrate = 1200
    opened.timeout = 0.4
    opened.dtr = False
    opened.rts = False
    try:
        opened.open()
        time.sleep(0.2)
        opened.close()
    except (serial.SerialException, OSError):
        return


def _ensure_esptool() -> None:
    probe = subprocess.run(
        [sys.executable, "-m", "esptool", "version"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=20,
        check=False,
    )
    if probe.returncode == 0:
        return
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "esptool==4.8.1"],
        stdin=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )


def _wait_for_bootloader() -> str:
    deadline = time.time() + 25
    while time.time() < deadline:
        for port in tracker_port_names():
            if _bootloader_answers(port):
                return port
        time.sleep(0.4)
    raise RuntimeError(
        "The Tracker did not enter firmware update mode. Unplug it, plug it back in, and press Update again."
    )


def _bootloader_answers(port: str) -> bool:
    try:
        result = _run(
        [
            sys.executable,
            "-m",
            "esptool",
            "--chip",
            "esp32s3",
            "--port",
            port,
            "--baud",
            "115200",
            "--before",
            "no_reset",
            "--after",
            "no_reset",
            "--connect-attempts",
            "1",
            "chip_id",
        ],
        check=False,
        timeout=8,
    )
    except RuntimeError:
        return False
    return result.returncode == 0


def _run(
    command: list[str],
    check: bool = True,
    timeout: int = 90,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Flashing the Tracker took too long.") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "esptool failed").strip()
        raise RuntimeError(detail.splitlines()[-1] if detail else "esptool failed")
    return result
