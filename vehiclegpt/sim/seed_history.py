"""Seed several weeks of vehicle history so the assistant has data to search on day one.

Uses the same physics, driver and anomaly models as the live system and writes through the same
storage layer, then applies tier retention (1 Hz data older than 7 days is dropped, rollups kept).

Injected scenarios (days ago):
  last 14 : heatwave (more AC) + slow front-left tire leak -> range reduces
  9       : aggressive driving + max AC                    -> largest efficiency drop
  5       : coolant pump fault on a highway trip            -> battery overheat warning
  1       : several ADAS disengagements with reasons
  every   : mix of city and highway trips

  python -m sim.seed_history --days 28
"""
import argparse
import os
import time
from datetime import datetime, timedelta

import numpy as np

import config
from collector.anomaly import AnomalyDetector
from common import storage
from common.someip import ADAS_REASONS
from sim.driver_model import DriverModel
from sim.vehicle_model import NOMINAL_PSI, EVModel

REASONS = [r for r in ADAS_REASONS.values() if r not in ("ENGAGED", "EXIT_ODD")]


def plan_day(d, rng):
    heat = d <= 14
    ambient = float(rng.normal(31 if heat else 23, 1.5))
    psi = NOMINAL_PSI if d > 14 else NOMINAL_PSI - (14 - d) * 0.6
    ac = float(np.clip((ambient - 22) * 0.28, 0, 3.2))
    hours = sorted(rng.choice([7, 8, 12, 13, 17, 18, 20], int(rng.integers(2, 4)), replace=False))
    trips = []
    for h in hours:
        road = str(rng.choice(["city", "highway"], p=[0.6, 0.4]))
        dur = int(rng.integers(12, 35)) * 60 if road == "city" else int(rng.integers(20, 45)) * 60
        trips.append(dict(hour=int(h), minute=int(rng.integers(0, 50)), road=road, dur=dur,
                          style="normal", ambient=ambient, ac=ac, psi=psi))
    if d == 9:
        for t in trips:
            t.update(style="aggressive", ac=3.6, ambient=ambient + 3)
        trips[0].update(road="highway", dur=40 * 60)
    if d == 5:
        trips[-1].update(road="highway", dur=42 * 60, fault_at=600)
    if d == 1:
        trips[0].update(road="highway", dur=45 * 60, adas_plan=[400, 900, 1400, 1900, 2400])
        if len(trips) > 1:
            trips[1].update(road="highway", dur=30 * 60, adas_plan=[600])
    return trips


def run_trip(conn, trip_id, start, cfg, ev, rng, detector):
    drv = DriverModel(rng)
    ev.speed = 0.0
    ev.batt_temp = cfg["ambient"] + 2.0
    ev.faults.coolant_pump_failed = False
    ev.over_temp_warn = ev.derate = False
    adas, plan = False, list(cfg.get("adas_plan", []))
    prev_warn = prev_derate = 0
    rows = []

    def event(ts, *a):
        storage.insert_event(conn, ts, trip_id, *a)

    for t in range(cfg["dur"]):
        ts = start + t
        remaining = cfg["dur"] - t
        if cfg["road"] == "city" and drv.speed > 30 and rng.random() < 0.0004:
            speed = drv.hard_brake(1.0)
        else:
            speed = drv.step(1.0, cfg["road"], cfg["style"], ev.derate, stopping=remaining < 45)
        if cfg.get("fault_at") == t:
            ev.faults.coolant_pump_failed = True
            event(ts, "DTC", "warning", "DIAG/slog2", "P0C73",
                  "DTC P0C73 SET Battery coolant pump performance - pump not running (simulated code)")
        s = ev.step(speed, 1.0, cfg["ambient"], max(0.0, cfg["ac"] + rng.normal(0, 0.1)), cfg["psi"])

        if s["accel_mps2"] < -4.5:
            event(ts, "HARD_BRAKE", "info", "CAN", "HB01", "Hard braking detected (> 0.45 g)", round(s["accel_mps2"], 2))
        if s["over_temp_warn"] and not prev_warn:
            event(ts, "BATT_OVERTEMP", "critical", "CAN", "BMS_OT1",
                  f"Battery over-temperature warning at {s['batt_temp_c']:.1f} C", round(s["batt_temp_c"], 1))
        if s["power_derate"] and not prev_derate:
            event(ts, "POWER_DERATE", "warning", "CAN", "VCU_DR1", "Propulsion power limited to protect battery")
        prev_warn, prev_derate = s["over_temp_warn"], s["power_derate"]
        if t == 10 and cfg["psi"] < 30:
            event(ts, "TIRE_PRESSURE_LOW", "warning", "CAN", "TPMS_FL",
                  f"Front-left tire pressure low: {cfg['psi']:.1f} psi", round(cfg["psi"], 1))

        if cfg["road"] == "highway":
            if not adas and speed > 70 and remaining > 120 and rng.random() < 0.02:
                adas = True
                event(ts, "ADAS_ENGAGE", "info", "SOME/IP", "ENGAGED", "ACC + lane keeping engaged")
            if plan and t >= plan[0]:
                if adas:
                    adas = False
                    reason = str(rng.choice(REASONS))
                    event(ts, "ADAS_DISENGAGE", "warning", "SOME/IP", reason, f"ADAS disengaged: {reason}", round(speed, 1))
                    plan.pop(0)
                else:
                    plan[0] = t + 30
            elif adas and rng.random() < 0.0003:
                adas = False
                reason = str(rng.choice(REASONS))
                event(ts, "ADAS_DISENGAGE", "warning", "SOME/IP", reason, f"ADAS disengaged: {reason}", round(speed, 1))
            if adas and remaining < 60:
                adas = False
                event(ts, "ADAS_DISENGAGE", "info", "SOME/IP", "EXIT_ODD", "ADAS disengaged: EXIT_ODD", round(speed, 1))

        row = dict(ts=ts, trip_id=trip_id, road_type=cfg["road"], drive_mode=cfg["style"],
                   adas_active=int(adas), **{k: s[k] for k in storage.SIGNAL_COLS if k in s})
        rows.append(row)
        for a in detector.update(row):
            storage.insert_event(conn, ts, trip_id, a["event_type"], a["severity"], a["source"],
                                 a["code"], a["description"], a["value"])
    storage.insert_signals(conn, rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--db", default=config.DB_PATH)
    args = ap.parse_args()

    t0 = time.time()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(args.db + suffix):
            os.remove(args.db + suffix)
    conn = storage.connect(args.db)
    storage.init_db(conn)
    rng = np.random.default_rng(args.seed)
    ev = EVModel()
    detector = AnomalyDetector()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    trip_id = 0
    for d in range(args.days, 0, -1):
        day = today - timedelta(days=d)
        ev.soc = 90.0
        for cfg in plan_day(d, rng):
            trip_id += 1
            start = (day + timedelta(hours=cfg["hour"], minutes=cfg["minute"])).timestamp()
            run_trip(conn, trip_id, start, cfg, ev, rng, detector)
            storage.summarize_trip(conn, trip_id)
        conn.commit()
    storage.rollup(conn, 0, time.time())
    conn.commit()
    pruned = storage.apply_retention(conn)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("signals_1s", "rollups_1min", "trips", "events")}
    conn.execute("VACUUM")
    conn.close()
    print(f"Seeded {args.days} days in {time.time() - t0:.1f}s -> {args.db}")
    print(f"  rows: {counts}")
    print(f"  retention pruned (1 Hz older than {config.RETENTION_SIGNALS_1S_DAYS} d): {pruned['signals_1s']:,} rows")
    print(f"  size: {os.path.getsize(args.db) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
