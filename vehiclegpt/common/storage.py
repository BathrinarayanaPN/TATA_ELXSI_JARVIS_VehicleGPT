"""SQLite storage: tiered retention, trips, rollups, events (+ FTS5 log search).

Tiers
  signals_1s    : 1 Hz decoded signals           -> kept RETENTION_SIGNALS_1S_DAYS (7 d)
  rollups_1min  : 1-minute aggregates           -> kept RETENTION_ROLLUPS_DAYS (12 weeks)
  trips         : per-trip energy decomposition -> kept with rollups
  events        : warnings, DTCs, ADAS, anomalies -> kept RETENTION_EVENTS_DAYS (6 months)
SQLite in WAL mode lets the collector write while the assistant reads, with a ~1 MB library footprint.
"""
import sqlite3
import time
from pathlib import Path

import config
from sim.vehicle_model import trip_energy_breakdown

SIGNAL_COLS = [
    "ts", "trip_id", "road_type", "drive_mode", "speed_kmh", "accel_mps2", "power_kw", "soc_pct",
    "batt_voltage", "batt_current_a", "batt_temp_c", "coolant_pump_ok", "ambient_c", "ac_kw",
    "tire_psi_fl", "adas_active", "est_range_km", "odometer_km", "power_derate",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals_1s(
  ts REAL PRIMARY KEY, trip_id INTEGER, road_type TEXT, drive_mode TEXT,
  speed_kmh REAL, accel_mps2 REAL, power_kw REAL, soc_pct REAL, batt_voltage REAL,
  batt_current_a REAL, batt_temp_c REAL, coolant_pump_ok INTEGER, ambient_c REAL, ac_kw REAL,
  tire_psi_fl REAL, adas_active INTEGER, est_range_km REAL, odometer_km REAL, power_derate INTEGER);
CREATE INDEX IF NOT EXISTS ix_sig_trip ON signals_1s(trip_id);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, trip_id INTEGER, event_type TEXT, severity TEXT,
  source TEXT, code TEXT, description TEXT, value REAL);
CREATE INDEX IF NOT EXISTS ix_ev_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_ev_type ON events(event_type, ts);

CREATE TABLE IF NOT EXISTS trips(
  trip_id INTEGER PRIMARY KEY, start_ts REAL, end_ts REAL, road_type TEXT, drive_mode TEXT,
  distance_km REAL, energy_kwh REAL, wh_per_km REAL, avg_speed_kmh REAL, max_batt_temp_c REAL,
  avg_ambient_c REAL, avg_tire_psi REAL, ac_kwh REAL, aero_kwh REAL, rolling_kwh REAL,
  aux_kwh REAL, dynamic_kwh REAL, aggressive_share REAL);
CREATE INDEX IF NOT EXISTS ix_trips_start ON trips(start_ts);

CREATE TABLE IF NOT EXISTS rollups_1min(
  minute REAL, trip_id INTEGER, road_type TEXT, avg_speed REAL, max_speed REAL, energy_kwh REAL,
  distance_km REAL, avg_batt_temp REAL, max_batt_temp REAL, avg_ac_kw REAL, avg_ambient REAL,
  min_soc REAL, avg_tire_psi REAL, avg_est_range REAL, PRIMARY KEY(minute, trip_id));
"""

FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
  event_type, code, description, content='events', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid, event_type, code, description)
  VALUES (new.id, new.event_type, new.code, new.description); END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
  INSERT INTO events_fts(events_fts, rowid, event_type, code, description)
  VALUES ('delete', old.id, old.event_type, old.code, old.description); END;
"""


def connect(path=None, readonly=False):
    path = path or config.DB_PATH
    if readonly:
        uri = Path(path).resolve().as_uri() + "?mode=ro"     # file:///C:/... on Windows, file:///home/... on Linux
        conn = sqlite3.connect(uri, uri=True, timeout=5, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    try:
        conn.executescript(FTS)
    except sqlite3.OperationalError:
        pass  # FTS5 not compiled in -> log search falls back to LIKE
    conn.commit()


def has_fts(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='events_fts'").fetchone() is not None


def insert_signals(conn, rows):
    ph = ",".join("?" * len(SIGNAL_COLS))
    conn.executemany(f"INSERT OR REPLACE INTO signals_1s({','.join(SIGNAL_COLS)}) VALUES({ph})",
                     [[r.get(c) for c in SIGNAL_COLS] for r in rows])


def insert_event(conn, ts, trip_id, event_type, severity, source, code, description, value=None):
    conn.execute("INSERT INTO events(ts,trip_id,event_type,severity,source,code,description,value)"
                 " VALUES(?,?,?,?,?,?,?,?)",
                 (ts, trip_id, event_type, severity, source, code, description, value))


def summarize_trip(conn, trip_id):
    rows = conn.execute(
        "SELECT ts, road_type, drive_mode, speed_kmh, power_kw, batt_temp_c, ambient_c, ac_kw, tire_psi_fl"
        " FROM signals_1s WHERE trip_id=? ORDER BY ts", (trip_id,)).fetchall()
    if len(rows) < 10:
        return
    b = trip_energy_breakdown(rows)
    road = max(("city", "highway"), key=lambda r: sum(1 for x in rows if x["road_type"] == r))
    agg = sum(1 for x in rows if x["drive_mode"] == "aggressive") / len(rows)
    conn.execute(
        """INSERT OR REPLACE INTO trips VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (trip_id, rows[0]["ts"], rows[-1]["ts"], road, "aggressive" if agg > 0.5 else "normal",
         b["distance_km"], b["energy_kwh"], b["wh_per_km"], b["avg_speed_kmh"],
         max(x["batt_temp_c"] for x in rows), sum(x["ambient_c"] for x in rows) / len(rows),
         sum(x["tire_psi_fl"] for x in rows) / len(rows), b["ac_kwh"], b["aero_kwh"],
         b["rolling_kwh"], b["aux_kwh"], b["dynamic_kwh"], agg))


def rollup(conn, start_ts, end_ts):
    conn.execute(
        """INSERT OR REPLACE INTO rollups_1min
        SELECT CAST(ts/60 AS INTEGER)*60.0 AS minute, trip_id,
               MAX(road_type), AVG(speed_kmh), MAX(speed_kmh), SUM(power_kw)/3600.0,
               SUM(speed_kmh)/3600.0, AVG(batt_temp_c), MAX(batt_temp_c), AVG(ac_kw),
               AVG(ambient_c), MIN(soc_pct), AVG(tire_psi_fl), AVG(est_range_km)
        FROM signals_1s WHERE ts >= ? AND ts < ? GROUP BY minute, trip_id""",
        (start_ts, end_ts))


def apply_retention(conn, now=None):
    now = now or time.time()
    day = 86400
    out = {}
    for table, col, days in (("signals_1s", "ts", config.RETENTION_SIGNALS_1S_DAYS),
                             ("rollups_1min", "minute", config.RETENTION_ROLLUPS_DAYS),
                             ("trips", "start_ts", config.RETENTION_ROLLUPS_DAYS),
                             ("events", "ts", config.RETENTION_EVENTS_DAYS)):
        out[table] = conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (now - days * day,)).rowcount
    conn.commit()
    return out
