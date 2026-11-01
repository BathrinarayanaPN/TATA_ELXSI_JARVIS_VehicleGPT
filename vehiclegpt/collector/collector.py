"""VehicleGPT collector - the only component that touches vehicle networks, and it is receive-only.

  CAN thread     : ReadOnlyBus (no send method) -> DBC decode -> latest-value cache, hard-brake detection
  SOME/IP thread : subscribe to ADAS service 0x4ADA notifications -> ADAS engage/disengage events
  slog2 thread   : tail QNX-style diagnostic log -> DTC set/clear, collision events
  writer (1 Hz)  : signals_1s row, edge-triggered warnings, AI anomaly detection, trips, rollups, retention

  python -m collector.collector
"""
import json
import os
import queue
import re
import socket
import threading
import time
from datetime import datetime

import config
from collector.anomaly import AnomalyDetector
from common import canbus, someip, storage
from common.dbc import Database

ROADS = {0: "city", 1: "highway"}
MODES = {0: "normal", 1: "aggressive"}
DTC_RE = re.compile(r"DTC (\w+) (SET|CLEARED) (.*)")
STATUS_FILE = config.DATA_DIR / "collector_status.json"


class Collector:
    def __init__(self):
        self.db = Database(config.DBC_PATH)
        self.conn = storage.connect()
        storage.init_db(self.conn)
        self.latest, self.latest_ts = {}, 0.0
        self.lock = threading.Lock()
        self.events = queue.Queue()
        self.counters = {"can_frames": 0, "someip_msgs": 0, "log_lines": 0}
        self.last_hard_brake = 0.0
        self.detector = AnomalyDetector()
        self.trip_map = {}
        self.trip_base = self.conn.execute("SELECT COALESCE(MAX(trip_id),0) FROM trips").fetchone()[0]
        self.trip_base = max(self.trip_base, self.conn.execute(
            "SELECT COALESCE(MAX(trip_id),0) FROM signals_1s").fetchone()[0])
        self.prev = {}
        self.tire_warned_trips = set()
        self.started = time.time()
        self._prime_detector()

    def _prime_detector(self):
        """Warm the anomaly baselines from stored history so detection works from the first minute."""
        rows = self.conn.execute("SELECT * FROM (SELECT * FROM signals_1s ORDER BY ts DESC LIMIT 20000) ORDER BY ts").fetchall()
        for r in rows:
            self.detector.update(dict(r))
        print(f"[collector] anomaly baselines primed from {len(rows):,} stored samples")

    # ---------------- receivers ----------------
    def can_loop(self):
        bus = canbus.open_rx_readonly()
        while True:
            f = bus.recv(1.0)
            if f is None:
                continue
            name, vals = self.db.decode(f.arbitration_id, f.data)
            if not name:
                continue
            with self.lock:
                self.latest.update(vals)
                self.latest_ts = time.time()
                self.counters["can_frames"] += 1
            if name == "VCU_Speed" and vals["LongAccel"] < -4.5 and time.time() - self.last_hard_brake > 3:
                self.last_hard_brake = time.time()
                self.events.put(("HARD_BRAKE", "info", "CAN", "HB01", "Hard braking detected (> 0.45 g)",
                                 round(vals["LongAccel"], 2)))

    def someip_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", config.SOMEIP_PORT))
        while True:
            pkt, _ = sock.recvfrom(1500)
            try:
                msg = someip.unpack(pkt)
            except Exception:
                continue
            if msg["service_id"] != someip.ADAS_SERVICE_ID or msg["event_id"] != someip.ADAS_STATE_EVENT_ID:
                continue
            e = someip.unpack_adas_event(msg["payload"])
            self.counters["someip_msgs"] += 1
            if e["engaged"]:
                self.events.put(("ADAS_ENGAGE", "info", "SOME/IP", "ENGAGED", "ACC + lane keeping engaged",
                                 round(e["speed_kmh"], 1)))
            else:
                sev = "info" if e["reason"] == "EXIT_ODD" else "warning"
                self.events.put(("ADAS_DISENGAGE", sev, "SOME/IP", e["reason"], f"ADAS disengaged: {e['reason']}",
                                 round(e["speed_kmh"], 1)))

    def log_loop(self):
        path = config.DIAG_LOG
        path.touch(exist_ok=True)
        with open(path, encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                self.counters["log_lines"] += 1
                m = DTC_RE.search(line)
                if m:
                    code, action, desc = m.groups()
                    self.events.put(("DTC" if action == "SET" else "DTC_CLEARED",
                                     "warning" if action == "SET" else "info", "DIAG/slog2", code,
                                     f"DTC {code} {action} {desc.strip()}", None))
                elif "Collision" in line:
                    self.events.put(("COLLISION", "critical", "DIAG/slog2", "CRASH", line.split("  ")[-1].strip(), None))

    # ---------------- writer ----------------
    def local_trip(self, sim_trip):
        if sim_trip not in self.trip_map:
            if self.trip_map:
                storage.summarize_trip(self.conn, list(self.trip_map.values())[-1])
            self.trip_base += 1
            self.trip_map[sim_trip] = self.trip_base
        return self.trip_map[sim_trip]

    def write_tick(self, now):
        with self.lock:
            fresh = now - self.latest_ts < 2.0
            v = dict(self.latest)
        trip = self.local_trip(int(v.get("TripId", 0))) if fresh else None
        ts = float(int(now))
        new_events = []
        if fresh and "VehicleSpeed" in v and "BattTemp" in v:
            row = dict(
                ts=ts, trip_id=trip, road_type=ROADS.get(int(v.get("RoadType", 0)), "city"),
                drive_mode=MODES.get(int(v.get("DriveMode", 0)), "normal"),
                speed_kmh=v["VehicleSpeed"], accel_mps2=v.get("LongAccel"), power_kw=v.get("PackPower"),
                soc_pct=v.get("SOC"), batt_voltage=v.get("PackVoltage"), batt_current_a=v.get("PackCurrent"),
                batt_temp_c=v["BattTemp"], coolant_pump_ok=int(v.get("CoolantPumpOK", 1)),
                ambient_c=v.get("AmbientTemp"), ac_kw=v.get("AcPower"), tire_psi_fl=v.get("TirePsiFL"),
                adas_active=int(v.get("AdasActive", 0)), est_range_km=v.get("EstRange"),
                odometer_km=v.get("Odometer"), power_derate=int(v.get("PowerDerate", 0)))
            storage.insert_signals(self.conn, [row])
            # edge-triggered warnings from CAN status bits
            if v.get("OverTempWarn") and not self.prev.get("OverTempWarn"):
                new_events.append(("BATT_OVERTEMP", "critical", "CAN", "BMS_OT1",
                                   f"Battery over-temperature warning at {v['BattTemp']:.1f} C", round(v["BattTemp"], 1)))
            if v.get("PowerDerate") and not self.prev.get("PowerDerate"):
                new_events.append(("POWER_DERATE", "warning", "CAN", "VCU_DR1", "Propulsion power limited to protect battery", None))
            if v.get("TireLowWarn") and trip not in self.tire_warned_trips:
                self.tire_warned_trips.add(trip)
                new_events.append(("TIRE_PRESSURE_LOW", "warning", "CAN", "TPMS_FL",
                                   f"Front-left tire pressure low: {v['TirePsiFL']:.1f} psi", round(v["TirePsiFL"], 1)))
            for a in self.detector.update(row):
                new_events.append((a["event_type"], a["severity"], a["source"], a["code"], a["description"], a["value"]))
            self.prev = v
        while not self.events.empty():
            new_events.append(self.events.get_nowait())
        for e in new_events:
            storage.insert_event(self.conn, now, trip, *e)
            print(f"[collector] EVENT {e[0]:<24} {e[4]}")
        self.conn.commit()
        return fresh

    def write_status(self, rate):
        try:
            import psutil
            rss = psutil.Process().memory_info().rss / 1e6
        except Exception:
            rss = None
        STATUS_FILE.write_text(json.dumps(dict(
            pid=os.getpid(), updated=time.time(), uptime_s=time.time() - self.started, rss_mb=rss,
            can_backend=config.CAN_BACKEND, frames_per_s=rate, **self.counters)))

    def run(self):
        for target in (self.can_loop, self.someip_loop, self.log_loop):
            threading.Thread(target=target, daemon=True).start()
        print(f"[collector] listen-only on CAN({config.CAN_BACKEND}) + SOME/IP udp/{config.SOMEIP_PORT} "
              f"+ slog2 {config.DIAG_LOG.name} -> {config.DB_PATH}")
        storage.apply_retention(self.conn)
        last_frames, last_min, last_hour = 0, int(time.time() // 60), time.time()
        next_t = time.time() + 1
        while True:
            time.sleep(max(0.0, next_t - time.time()))
            now = time.time()
            next_t += 1
            self.write_tick(now)
            rate = self.counters["can_frames"] - last_frames
            last_frames = self.counters["can_frames"]
            self.write_status(rate)
            if int(now // 60) != last_min:                       # every minute: rollup + trip summary
                last_min = int(now // 60)
                storage.rollup(self.conn, now - 180, now)
                if self.trip_map:
                    storage.summarize_trip(self.conn, list(self.trip_map.values())[-1])
                self.conn.commit()
            if now - last_hour > 3600:
                last_hour = now
                print(f"[collector] retention: {storage.apply_retention(self.conn)}")


if __name__ == "__main__":
    Collector().run()
