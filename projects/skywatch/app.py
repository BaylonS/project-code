#!/usr/bin/env python3
import json
import math
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "skywatch.sqlite3"

PI_AIRCRAFT_URL = os.environ.get(
    "SKYWATCH_AIRCRAFT_URL",
    "http://127.0.0.1/tar1090/data/aircraft.json",
)
RECEIVER_LAT = float(os.environ.get("SKYWATCH_RECEIVER_LAT", "36.7700"))
RECEIVER_LON = float(os.environ.get("SKYWATCH_RECEIVER_LON", "-114.1290"))
POLL_SECONDS = float(os.environ.get("SKYWATCH_POLL_SECONDS", "2"))
POSITION_LOG_SECONDS = float(os.environ.get("SKYWATCH_POSITION_LOG_SECONDS", "15"))
PORT = int(os.environ.get("SKYWATCH_PORT", "8088"))

state_lock = threading.Lock()
latest_state = {
    "ok": False,
    "error": "starting",
    "updated": 0,
    "now": 0,
    "messages": 0,
    "aircraft": [],
    "stats": {},
}
last_position_logged = {}


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS aircraft_seen (
                hex TEXT PRIMARY KEY,
                first_seen INTEGER NOT NULL,
                last_seen INTEGER NOT NULL,
                flight TEXT,
                aircraft_type TEXT,
                category TEXT,
                squawk TEXT,
                emergency TEXT,
                seen_count INTEGER NOT NULL DEFAULT 0,
                last_lat REAL,
                last_lon REAL,
                last_altitude INTEGER,
                last_speed REAL,
                last_track REAL,
                last_distance_nm REAL,
                last_rssi REAL,
                last_messages INTEGER
            );
            CREATE TABLE IF NOT EXISTS position_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                hex TEXT NOT NULL,
                flight TEXT,
                lat REAL,
                lon REAL,
                altitude INTEGER,
                speed REAL,
                track REAL,
                distance_nm REAL,
                rssi REAL,
                messages INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_position_log_hex_ts
                ON position_log(hex, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_position_log_ts
                ON position_log(ts DESC);
            """
        )


def aircraft_altitude(ac):
    for key in ("alt_baro", "alt_geom", "altitude"):
        value = ac.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def clean_flight(value):
    if not value:
        return ""
    return str(value).strip()


def distance_nm(lat, lon):
    lat_nm = (lat - RECEIVER_LAT) * 60.0
    lon_nm = (lon - RECEIVER_LON) * 60.0 * math.cos(math.radians(RECEIVER_LAT))
    return math.sqrt(lat_nm * lat_nm + lon_nm * lon_nm)


def normalize_aircraft(raw):
    out = []
    for ac in raw:
        hex_id = str(ac.get("hex") or "").strip().lower()
        if not hex_id:
            continue
        lat = ac.get("lat")
        lon = ac.get("lon")
        dist = None
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            dist = round(distance_nm(lat, lon), 2)
        out.append(
            {
                "hex": hex_id,
                "flight": clean_flight(ac.get("flight")),
                "type": ac.get("type") or "",
                "category": ac.get("category") or "",
                "squawk": ac.get("squawk") or "",
                "emergency": ac.get("emergency") or "",
                "lat": lat if isinstance(lat, (int, float)) else None,
                "lon": lon if isinstance(lon, (int, float)) else None,
                "altitude": aircraft_altitude(ac),
                "speed": ac.get("gs") if isinstance(ac.get("gs"), (int, float)) else None,
                "track": ac.get("track") if isinstance(ac.get("track"), (int, float)) else None,
                "seen": ac.get("seen"),
                "seen_pos": ac.get("seen_pos"),
                "distance_nm": dist,
                "rssi": ac.get("rssi") if isinstance(ac.get("rssi"), (int, float)) else None,
                "messages": ac.get("messages") if isinstance(ac.get("messages"), int) else None,
                "mlat": ac.get("mlat") or [],
                "tisb": ac.get("tisb") or [],
            }
        )
    out.sort(key=lambda x: (x["distance_nm"] is None, x["distance_nm"] or 999999))
    return out


def fetch_aircraft():
    req = urllib.request.Request(PI_AIRCRAFT_URL, headers={"User-Agent": "SkyWatch/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def store_aircraft(aircraft, ts):
    if not aircraft:
        return
    rows = []
    pos_rows = []
    for ac in aircraft:
        rows.append(
            (
                ac["hex"],
                ts,
                ts,
                ac["flight"],
                ac["type"],
                ac["category"],
                ac["squawk"],
                ac["emergency"],
                ac["lat"],
                ac["lon"],
                ac["altitude"],
                ac["speed"],
                ac["track"],
                ac["distance_nm"],
                ac["rssi"],
                ac["messages"],
            )
        )
        if ac["lat"] is not None and ac["lon"] is not None:
            last_ts = last_position_logged.get(ac["hex"], 0)
            if ts - last_ts >= POSITION_LOG_SECONDS:
                last_position_logged[ac["hex"]] = ts
                pos_rows.append(
                    (
                        ts,
                        ac["hex"],
                        ac["flight"],
                        ac["lat"],
                        ac["lon"],
                        ac["altitude"],
                        ac["speed"],
                        ac["track"],
                        ac["distance_nm"],
                        ac["rssi"],
                        ac["messages"],
                    )
                )
    with db() as con:
        con.executemany(
            """
            INSERT INTO aircraft_seen (
                hex, first_seen, last_seen, flight, aircraft_type, category, squawk,
                emergency, last_lat, last_lon, last_altitude, last_speed, last_track,
                last_distance_nm, last_rssi, last_messages, seen_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(hex) DO UPDATE SET
                last_seen=excluded.last_seen,
                flight=COALESCE(NULLIF(excluded.flight, ''), aircraft_seen.flight),
                aircraft_type=COALESCE(NULLIF(excluded.aircraft_type, ''), aircraft_seen.aircraft_type),
                category=COALESCE(NULLIF(excluded.category, ''), aircraft_seen.category),
                squawk=COALESCE(NULLIF(excluded.squawk, ''), aircraft_seen.squawk),
                emergency=COALESCE(NULLIF(excluded.emergency, ''), aircraft_seen.emergency),
                last_lat=COALESCE(excluded.last_lat, aircraft_seen.last_lat),
                last_lon=COALESCE(excluded.last_lon, aircraft_seen.last_lon),
                last_altitude=COALESCE(excluded.last_altitude, aircraft_seen.last_altitude),
                last_speed=COALESCE(excluded.last_speed, aircraft_seen.last_speed),
                last_track=COALESCE(excluded.last_track, aircraft_seen.last_track),
                last_distance_nm=COALESCE(excluded.last_distance_nm, aircraft_seen.last_distance_nm),
                last_rssi=COALESCE(excluded.last_rssi, aircraft_seen.last_rssi),
                last_messages=COALESCE(excluded.last_messages, aircraft_seen.last_messages),
                seen_count=aircraft_seen.seen_count + 1
            """,
            rows,
        )
        if pos_rows:
            con.executemany(
                """
                INSERT INTO position_log (
                    ts, hex, flight, lat, lon, altitude, speed, track,
                    distance_nm, rssi, messages
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                pos_rows,
            )


def build_stats(aircraft, payload):
    positioned = [a for a in aircraft if a["lat"] is not None and a["lon"] is not None]
    highest = max((a for a in aircraft if a["altitude"] is not None), key=lambda a: a["altitude"], default=None)
    farthest = max((a for a in aircraft if a["distance_nm"] is not None), key=lambda a: a["distance_nm"], default=None)
    emergency = [a for a in aircraft if a["emergency"] and a["emergency"] != "none"]
    return {
        "aircraft": len(aircraft),
        "positioned": len(positioned),
        "messages": payload.get("messages", 0),
        "highest": highest,
        "farthest": farthest,
        "emergency": emergency,
    }


def poll_loop():
    global latest_state
    while True:
        try:
            payload = fetch_aircraft()
            ts = int(time.time())
            aircraft = normalize_aircraft(payload.get("aircraft", []))
            store_aircraft(aircraft, ts)
            with state_lock:
                latest_state = {
                    "ok": True,
                    "error": "",
                    "updated": ts,
                    "now": payload.get("now", 0),
                    "messages": payload.get("messages", 0),
                    "aircraft": aircraft,
                    "stats": build_stats(aircraft, payload),
                    "receiver": {"lat": RECEIVER_LAT, "lon": RECEIVER_LON},
                    "source": PI_AIRCRAFT_URL,
                }
        except Exception as exc:
            with state_lock:
                latest_state = {**latest_state, "ok": False, "error": str(exc), "updated": int(time.time())}
        time.sleep(POLL_SECONDS)


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def send_json(self, obj, status=200):
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            with state_lock:
                self.send_json(latest_state)
            return
        if parsed.path == "/api/log":
            qs = parse_qs(parsed.query)
            limit = min(int(qs.get("limit", ["250"])[0]), 1000)
            q = (qs.get("q", [""])[0] or "").strip().lower()
            sql = """
                SELECT * FROM aircraft_seen
                WHERE (? = '' OR lower(hex) LIKE ? OR lower(COALESCE(flight,'')) LIKE ?)
                ORDER BY last_seen DESC
                LIMIT ?
            """
            like = f"%{q}%"
            with db() as con:
                self.send_json(rows_to_dicts(con.execute(sql, (q, like, like, limit)).fetchall()))
            return
        if parsed.path == "/api/history":
            qs = parse_qs(parsed.query)
            hex_id = (qs.get("hex", [""])[0] or "").strip().lower()
            limit = min(int(qs.get("limit", ["500"])[0]), 2000)
            with db() as con:
                rows = con.execute(
                    "SELECT * FROM position_log WHERE hex=? ORDER BY ts DESC LIMIT ?",
                    (hex_id, limit),
                ).fetchall()
            self.send_json(rows_to_dicts(rows))
            return
        if parsed.path == "/api/stats":
            with db() as con:
                totals = {
                    "unique_aircraft": con.execute("SELECT COUNT(*) FROM aircraft_seen").fetchone()[0],
                    "positions_logged": con.execute("SELECT COUNT(*) FROM position_log").fetchone()[0],
                    "first_seen": con.execute("SELECT MIN(first_seen) FROM aircraft_seen").fetchone()[0],
                    "last_seen": con.execute("SELECT MAX(last_seen) FROM aircraft_seen").fetchone()[0],
                }
            self.send_json(totals)
            return
        super().do_GET()


def main():
    init_db()
    threading.Thread(target=poll_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"SkyWatch listening on http://0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
