"""USB serial link to the tracker bridge firmware."""

from __future__ import annotations

import queue
import threading
import time

import serial
from serial.tools import list_ports

TRACKER_VID = 0x303A
TRACKER_PID = 0x1001
# ESP32-S2 Mini used by the mouse jiggler. Never open that port.
JIGGLER_PID = 0x0002


def open_link(port: str) -> serial.Serial:
    """Open the Tracker without resetting it, then assert DTR for CDC output.

    Opening with DTR/RTS already high resets the ESP32. But TinyUSB CDC on
    this board discards every Serial.print until the host asserts DTR — that
    is why Mesh Chat could SEND (host→device) while the radio log saw 0 bytes
    back. Open with lines low, then raise DTR after the port is up.
    """
    link = serial.Serial()
    link.port = port
    link.baudrate = 115200
    link.timeout = 0.25
    link.write_timeout = 2
    link.dsrdtr = False
    link.rtscts = False
    link.xonxoff = False
    link.dtr = False
    link.rts = False
    link.open()
    # Keep RTS low. Raise DTR so USBSerial.connected becomes true and
    # firmware println() actually reaches the Pi.
    time.sleep(0.05)
    try:
        link.dtr = True
        link.rts = False
    except (serial.SerialException, OSError):
        pass
    try:
        link.reset_input_buffer()
        link.reset_output_buffer()
    except (serial.SerialException, OSError):
        pass
    time.sleep(0.15)
    return link


def tracker_port(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    for port in list_ports.comports():
        if port.vid == TRACKER_VID and port.pid == TRACKER_PID:
            return port.device
    return None


def _uint32_field(raw: str) -> int | None:
    """Accept decimal radio ids, including broadcast 4294967295."""
    if not raw or not raw.isascii() or not raw.isdigit():
        return None
    try:
        value = int(raw, 10)
    except ValueError:
        return None
    if value > 0xFFFFFFFF:
        return None
    return value


def parse_line(line: str) -> dict | None:
    # Keep the raw text so the UI can show every Tracker line even when a
    # field fails to parse. Sending can succeed while RX is silent if the
    # reader path is dropping lines.
    text = line.strip().strip("\x00")
    if not text:
        return None
    parts = text.split(" ")
    op = parts[0]
    if op == "PONG":
        return {"op": "pong", "raw": text}
    if op == "FW" and len(parts) >= 2:
        return {"op": "fw", "version": parts[1], "raw": text}
    if op == "ID" and len(parts) >= 2:
        phone = _uint32_field(parts[1])
        if phone is not None:
            return {"op": "id", "phone": phone, "raw": text}
    if op == "RADIO" and len(parts) >= 2:
        return {"op": "radio", "ok": parts[1] == "ok", "detail": " ".join(parts[1:]), "raw": text}
    if op == "ACK" and len(parts) >= 2:
        phone = _uint32_field(parts[1])
        if phone is not None:
            return {"op": "ack", "phone": phone, "raw": text}
    if op == "TX" and len(parts) >= 4:
        phone = _uint32_field(parts[1])
        msg_id = _uint32_field(parts[2])
        if phone is not None and msg_id is not None:
            return {
                "op": "tx",
                "phone": phone,
                "id": msg_id,
                "ok": parts[3] == "ok",
                "raw": text,
            }
    if op == "RX" and len(parts) >= 4:
        sender = _uint32_field(parts[1])
        target = _uint32_field(parts[2])
        msg_id = _uint32_field(parts[3])
        if sender is not None and target is not None and msg_id is not None:
            return {
                "op": "rx",
                "sender": sender,
                "target": target,
                "id": msg_id,
                "text": " ".join(parts[4:]),
                "raw": text,
            }
        return {"op": "log", "detail": text, "raw": text}
    if op == "HEARD":
        return {"op": "heard", "detail": " ".join(parts[1:]) or "packet", "raw": text}
    if op == "SCAN":
        return {"op": "scan", "detail": " ".join(parts[1:]), "raw": text}
    if op == "AIR":
        return {"op": "air", "detail": " ".join(parts[1:]), "raw": text}
    if op == "IRQ":
        return {"op": "irq", "detail": " ".join(parts[1:]), "raw": text}
    if op == "ERR":
        return {"op": "err", "detail": " ".join(parts[1:]) or "error", "raw": text}
    if op == "FREQ":
        return {"op": "freq", "detail": " ".join(parts[1:]), "raw": text}
    if op == "READY":
        return {"op": "log", "detail": text, "raw": text}
    if op == "GPS" and len(parts) >= 2 and parts[1] == "off":
        return {"op": "gps", "fix": 0, "sats": 0, "raw": text}
    if op == "GPS" and len(parts) >= 2 and parts[1] == "none":
        sats = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
        return {"op": "gps", "fix": 0, "sats": sats, "raw": text}
    if op == "GPS" and len(parts) >= 6:
        return {
            "op": "gps",
            "lat": float(parts[1]),
            "lon": float(parts[2]),
            "alt": float(parts[3]),
            "sats": int(parts[4]),
            "fix": int(parts[5]),
            "raw": text,
        }
    if op == "NMEA" and len(parts) >= 2 and parts[1].startswith("$"):
        return {"op": "nmea", "sentence": " ".join(parts[1:]), "raw": text}
    return {"op": "log", "detail": text, "raw": text}


class Tracker:
    """Background reader. Events arrive on `events` for the UI thread."""

    def __init__(self, phone: int | None = None, port: str | None = None) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self.wanted_phone = phone or 0
        self.firmware = ""
        self._explicit = port
        self._ser: serial.Serial | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.port_name = ""
        self.bytes_read = 0
        self.last_rx_at = 0.0
        self.connected = False

    def start(self) -> None:
        threading.Thread(target=self._run, name="tracker", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
        self.connected = False

    def set_phone(self, phone: int) -> None:
        self.wanted_phone = phone
        self.write(f"ID {phone}")

    def send(self, target: int, text: str) -> bool:
        body = " ".join(text.replace("\r", " ").replace("\n", " ").split())
        if not body:
            return False
        return self.write(f"SEND {target} {body}")

    def ping(self) -> bool:
        return self.write("PING")

    def write(self, line: str) -> bool:
        data = (line.rstrip("\r\n") + "\n").encode("utf-8")
        with self._lock:
            if self._ser is None or not self._ser.is_open:
                return False
            try:
                self._ser.write(data)
                self._ser.flush()
                # Local echo so the UI radio log moves even when USB reads are dead.
                self.events.put({"op": "log", "detail": f"> {line.rstrip()}", "raw": f"> {line.rstrip()}"})
                return True
            except TimeoutError:
                # A slow flush must not tear down the reader. Closing here made
                # Mesh Chat look like it could send while going deaf on receive.
                self.events.put(
                    {"op": "log", "detail": "write timeout", "raw": "WARN USB write timeout"}
                )
                return False
            except (serial.SerialException, OSError, TypeError, ValueError) as exc:
                # A Tracker reboot mid-write raises OSError on Linux, not only
                # SerialException. That used to escape and kill the window.
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
                self.connected = False
                self.events.put(
                    {"op": "log", "detail": str(exc), "raw": f"WARN USB write failed: {exc}"}
                )
                return False

    def _emit_line(self, decoded: str) -> None:
        try:
            event = parse_line(decoded)
        except (ValueError, UnicodeError, TypeError):
            event = {"op": "log", "detail": decoded, "raw": decoded}
        if event:
            self.events.put(event)

    def _run(self) -> None:
        announced_down = False
        while not self._stop.is_set():
            name = tracker_port(self._explicit)
            if not name:
                self.connected = False
                if not announced_down:
                    self.events.put(
                        {
                            "op": "down",
                            "detail": "Plug the Tracker V2 into USB",
                            "raw": "DOWN no Tracker port",
                        }
                    )
                    announced_down = True
                self._stop.wait(1.0)
                continue
            try:
                ser = open_link(name)
            except serial.SerialException as exc:
                self.connected = False
                if not announced_down:
                    self.events.put({"op": "down", "detail": str(exc), "raw": f"DOWN {exc}"})
                    announced_down = True
                self._stop.wait(1.5)
                continue
            announced_down = False
            with self._lock:
                self._ser = ser
            self.port_name = name
            self.connected = True
            self.events.put({"op": "up", "port": name, "raw": f"OPEN {name}"})
            buf = b""
            last_byte = time.time()
            last_ping = 0.0
            warned_silent = False
            # Handshake after buffers settled. Replies prove USB reads work.
            self.write("PING")
            if self.wanted_phone:
                self.write(f"ID {self.wanted_phone}")
            else:
                self.write("ID?")
            self.write("ECHO meshchat-usb-ok")
            last_ping = time.time()
            while not self._stop.is_set():
                try:
                    # Always block-read with timeout. Relying on in_waiting alone
                    # missed CDC data on some Pi USB stacks.
                    chunk = ser.read(1024)
                except (serial.SerialException, OSError) as exc:
                    self.connected = False
                    self.events.put({"op": "down", "detail": str(exc), "raw": f"DOWN {exc}"})
                    break
                now = time.time()
                if chunk:
                    last_byte = now
                    warned_silent = False
                    self.bytes_read += len(chunk)
                    self.last_rx_at = now
                    buf += chunk
                    while b"\n" in buf or b"\r" in buf:
                        if b"\n" in buf:
                            raw, buf = buf.split(b"\n", 1)
                        else:
                            raw, buf = buf.split(b"\r", 1)
                        raw = raw.rstrip(b"\r")
                        if not raw:
                            continue
                        self._emit_line(raw.decode("utf-8", "replace"))
                    continue
                # No bytes this slice — keep the Tracker awake and surface silence.
                if now - last_ping >= 4.0:
                    last_ping = now
                    self.write("PING")
                if now - last_byte >= 6.0 and not warned_silent:
                    warned_silent = True
                    self.events.put(
                        {
                            "op": "log",
                            "detail": "silent",
                            "raw": (
                                f"WARN Tracker on {name} accepts writes but sent "
                                f"0 USB bytes back — radio log cannot see RX/SCAN"
                            ),
                        }
                    )
            with self._lock:
                try:
                    ser.close()
                except serial.SerialException:
                    pass
                if self._ser is ser:
                    self._ser = None
            self.connected = False
            time.sleep(0.4)
