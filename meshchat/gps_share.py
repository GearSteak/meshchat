"""Share the Tracker GPS with map apps.

A browser map is served at http://127.0.0.1:8770/.
Raw NMEA sentences are served on TCP port 10110, which chart and map
programs already know how to open.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAP_PORT = 8770
NMEA_PORT = 10110
_PAGE = (Path(__file__).with_name("map.html")).read_text(encoding="utf-8")
_USER_AGENT = "MeshChat/0.1 (personal navigation on a Raspberry Pi)"
_OSRM = "https://router.project-osrm.org"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"


def travel_profile(mode: str) -> str:
    return "foot" if mode == "foot" else "driving"


def maneuver_text(step: dict) -> str:
    maneuver = step.get("maneuver") or {}
    kind = str(maneuver.get("type") or "continue")
    modifier = str(maneuver.get("modifier") or "").replace("-", " ")
    name = str(step.get("name") or "")
    onto = f" onto {name}" if name else ""
    if kind == "depart":
        head = f"Head {modifier}".strip()
        return f"{head}{onto}" if name else (head or "Start")
    if kind == "arrive":
        return "You have arrived" + (f" at {name}" if name else "")
    if kind in ("turn", "end of road", "fork", "merge"):
        verb = "Merge" if kind == "merge" else "Turn"
        turn = f"{verb} {modifier}".strip()
        return f"{turn}{onto}"
    if kind in ("roundabout", "rotary", "exit roundabout"):
        return "Take the roundabout" + onto
    if modifier:
        return f"Continue {modifier}{onto}"
    return "Continue" + (f" on {name}" if name else "")


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _on_earth(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def search_places(query: str) -> dict:
    text = " ".join(query.split())
    if len(text) < 2:
        return {"places": []}
    url = _NOMINATIM + "?" + urllib.parse.urlencode({"q": text, "format": "jsonv2", "limit": 5})
    try:
        rows = _fetch_json(url)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return {"error": "Place search needs a network connection.", "places": []}
    places = []
    if isinstance(rows, list):
        for row in rows[:5]:
            try:
                places.append(
                    {"name": row.get("display_name") or "Place", "lat": float(row["lat"]), "lon": float(row["lon"])}
                )
            except (KeyError, TypeError, ValueError):
                continue
    return {"places": places}


def driving_route(flat: float, flon: float, tlat: float, tlon: float, mode: str) -> dict:
    if not (_on_earth(flat, flon) and _on_earth(tlat, tlon)):
        return {"error": "That place is off the map."}
    profile = travel_profile(mode)
    url = (
        f"{_OSRM}/route/v1/{profile}/{flon:.6f},{flat:.6f};{tlon:.6f},{tlat:.6f}"
        "?overview=full&geometries=geojson&steps=true"
    )
    try:
        payload = _fetch_json(url)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return {"error": "Directions need a network connection."}
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not routes or payload.get("code") != "Ok":
        return {"error": "No route between those places."}
    route = routes[0]
    geometry = route.get("geometry") or {}
    line = []
    for pair in geometry.get("coordinates") or []:
        if isinstance(pair, list) and len(pair) >= 2:
            line.append([pair[1], pair[0]])
    steps = []
    for leg in route.get("legs") or []:
        for step in leg.get("steps") or []:
            location = (step.get("maneuver") or {}).get("location") or [None, None]
            steps.append(
                {
                    "text": maneuver_text(step),
                    "distance": step.get("distance") or 0,
                    "duration": step.get("duration") or 0,
                    "lon": location[0],
                    "lat": location[1],
                }
            )
    return {
        "distance": route.get("distance") or 0,
        "duration": route.get("duration") or 0,
        "line": line,
        "steps": steps,
    }


class GpsShare:
    def __init__(self) -> None:
        self.fix = 0
        self.lat = 0.0
        self.lon = 0.0
        self.alt = 0.0
        self.sats = 0
        self._sentences: list[str] = []
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.map_port = 0
        self.nmea_port = 0
        self._http: ThreadingHTTPServer | None = None
        self._nmea: socket.socket | None = None
        self._last_lookup = 0.0

    def start(self) -> None:
        self._start_map()
        self._start_nmea()

    def stop(self) -> None:
        self._stop.set()
        if self._http is not None:
            self._http.shutdown()
        if self._nmea is not None:
            self._nmea.close()
        with self._lock:
            for client in self._clients:
                client.close()
            self._clients.clear()

    def update_fix(self, fix: int, sats: int, lat: float = 0.0, lon: float = 0.0, alt: float = 0.0) -> None:
        with self._lock:
            self.fix = fix
            self.sats = sats
            if fix:
                self.lat = lat
                self.lon = lon
                self.alt = alt

    def send_nmea(self, sentence: str) -> None:
        line = sentence.strip()
        if not line.startswith("$"):
            return
        data = (line + "\r\n").encode("ascii", "replace")
        with self._lock:
            self._sentences.append(line)
            self._sentences = self._sentences[-2:]
            clients = list(self._clients)
        dead: list[socket.socket] = []
        for client in clients:
            try:
                client.sendall(data)
            except OSError:
                dead.append(client)
        if dead:
            with self._lock:
                self._clients = [client for client in self._clients if client not in dead]
            for client in dead:
                client.close()

    def search(self, query: str) -> dict:
        self._pace()
        return search_places(query)

    def route(self, flat: float, flon: float, tlat: float, tlon: float, mode: str) -> dict:
        self._pace()
        return driving_route(flat, flon, tlat, tlon, mode)

    def _pace(self) -> None:
        with self._lock:
            wait = 1.0 - (time.time() - self._last_lookup)
            if wait < 0:
                wait = 0.0
            self._last_lookup = time.time() + wait
        if wait:
            time.sleep(wait)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "fix": self.fix,
                "lat": self.lat,
                "lon": self.lon,
                "alt": self.alt,
                "sats": self.sats,
            }

    def _start_map(self) -> None:
        share = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path == "/pos":
                    body = json.dumps(share.snapshot()).encode("utf-8")
                    kind = "application/json"
                elif parsed.path == "/search":
                    body = json.dumps(share.search(query.get("q", [""])[0])).encode("utf-8")
                    kind = "application/json"
                elif parsed.path == "/route":
                    try:
                        result = share.route(
                            float(query["flat"][0]),
                            float(query["flon"][0]),
                            float(query["tlat"][0]),
                            float(query["tlon"][0]),
                            query.get("mode", ["driving"])[0],
                        )
                    except (KeyError, ValueError, IndexError):
                        result = {"error": "The route request was incomplete."}
                    body = json.dumps(result).encode("utf-8")
                    kind = "application/json"
                else:
                    body = _PAGE.encode("utf-8")
                    kind = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args) -> None:
                return

        try:
            self._http = ThreadingHTTPServer(("127.0.0.1", MAP_PORT), Handler)
        except OSError:
            return
        self.map_port = MAP_PORT
        threading.Thread(target=self._http.serve_forever, name="gps-map", daemon=True).start()

    def _start_nmea(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", NMEA_PORT))
        except OSError:
            sock.close()
            return
        sock.listen(4)
        sock.settimeout(0.5)
        self._nmea = sock
        self.nmea_port = NMEA_PORT
        threading.Thread(target=self._accept_nmea, name="gps-nmea", daemon=True).start()

    def _accept_nmea(self) -> None:
        assert self._nmea is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._nmea.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            client.settimeout(2)
            with self._lock:
                backlog = list(self._sentences)
                self._clients.append(client)
            for sentence in backlog:
                try:
                    client.sendall((sentence + "\r\n").encode("ascii", "replace"))
                except OSError:
                    break
