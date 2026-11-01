"""Deterministic analytics tools. Every number and cause in an answer comes from here, never from the LLM.

Each tool returns a dict:
  intent, title, text (complete deterministic answer), facts (key numbers for the LLM),
  chart (optional {"kind","data","x","y"}), table (optional rows), evidence (SQL / ids used)
"""
import statistics
import time
from datetime import datetime

from assistant.intents import TimeRange, day_range
from sim.vehicle_model import NOMINAL_PSI, USABLE_KWH

DAY = 86400


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _t(ts, with_day=True):
    d = datetime.fromtimestamp(ts)
    return d.strftime("%a %d %b %H:%M:%S") if with_day else d.strftime("%H:%M:%S")


def _range_or(tr, days, label=None):
    now = time.time()
    return tr or TimeRange(now - days * DAY, now, label or f"last {days} days")


def _result(intent, title, text, facts=None, chart=None, table=None, evidence=None):
    return dict(intent=intent, title=title, text=text, facts=facts or {}, chart=chart, table=table,
                evidence=evidence or [])


TRIP_AGG = """SELECT COUNT(*) n, SUM(distance_km) km, SUM(energy_kwh) e, SUM(ac_kwh) ac, SUM(aero_kwh) aero,
  SUM(rolling_kwh) roll, SUM(aux_kwh) aux, SUM(dynamic_kwh) dyn, SUM(end_ts-start_ts)/3600.0 hours,
  SUM(avg_ambient_c*distance_km)/SUM(distance_km) ambient, SUM(avg_tire_psi*distance_km)/SUM(distance_km) psi,
  SUM(aggressive_share*distance_km)/SUM(distance_km) aggressive
  FROM trips WHERE start_ts >= ? AND start_ts < ? AND distance_km > 0"""


def _per_km(a):
    km = a["km"]
    return dict(total=a["e"] * 1000 / km, ac=a["ac"] * 1000 / km, aero=a["aero"] * 1000 / km,
                rolling=a["roll"] * 1000 / km, pattern=(a["dyn"] + a["aux"]) * 1000 / km,
                speed=km / a["hours"] if a["hours"] else 0)


def range_drop(conn, tr=None):
    now = time.time()
    days = round(tr.days) if tr and tr.days >= 2 else 14
    recent = _rows(conn, TRIP_AGG, (now - days * DAY, now))[0]
    base = _rows(conn, TRIP_AGG, (now - 2 * days * DAY, now - days * DAY))[0]
    if not recent["km"] or not base["km"]:
        return _result("range_drop", "Range trend", "Not enough driving history to compare range yet.")
    r, b = _per_km(recent), _per_km(base)
    rng_r, rng_b = USABLE_KWH * 1000 / r["total"], USABLE_KWH * 1000 / b["total"]
    causes = [
        ("Air-conditioning load", r["ac"] - b["ac"], f"outside temperature {base['ambient']:.0f} -> {recent['ambient']:.0f} C"),
        ("Low front-left tire pressure", r["rolling"] - b["rolling"], f"{base['psi']:.1f} -> {recent['psi']:.1f} psi (nominal {NOMINAL_PSI:.0f})"),
        ("Higher speeds / more highway", r["aero"] - b["aero"], f"average speed {b['speed']:.0f} -> {r['speed']:.0f} km/h"),
        ("Driving pattern (acceleration, idling)", r["pattern"] - b["pattern"],
         f"aggressive driving share {base['aggressive'] * 100:.0f}% -> {recent['aggressive'] * 100:.0f}%"),
    ]
    pos = sum(c[1] for c in causes if c[1] > 0) or 1
    causes.sort(key=lambda c: -c[1])
    delta_pct = (rng_r / rng_b - 1) * 100
    direction = "dropped" if delta_pct < 0 else "improved"
    lines = [f"Your estimated full-charge range {direction} from {rng_b:.0f} km to {rng_r:.0f} km "
             f"({delta_pct:+.0f}%) over the last {days} days compared with the {days} days before. "
             f"Energy use changed from {b['total']:.0f} to {r['total']:.0f} Wh/km."]
    if delta_pct < 0:
        lines.append("Main causes:")
        for i, (name, d, detail) in enumerate([c for c in causes if c[1] > 0.5], 1):
            lines.append(f"{i}. {name}: +{d:.1f} Wh/km ({d / pos * 100:.0f}% of the increase) - {detail}")
        tips = []
        roll_d = r["rolling"] - b["rolling"]
        if roll_d > 1:
            gain = USABLE_KWH * 1000 / (r["total"] - roll_d) - rng_r
            tips.append(f"inflating the front-left tire to {NOMINAL_PSI:.0f} psi should recover about {gain:.0f} km")
        if r["ac"] - b["ac"] > 1:
            tips.append("pre-cooling the cabin while plugged in reduces AC draw from the battery")
        if tips:
            lines.append("Recommendation: " + "; ".join(tips) + ".")
    daily = _rows(conn, """SELECT date(start_ts,'unixepoch','localtime') day, SUM(energy_kwh)*1000/SUM(distance_km) whpkm
                           FROM trips WHERE start_ts >= ? AND distance_km > 0 GROUP BY day ORDER BY day""",
                  (now - 2 * days * DAY,))
    for d in daily:
        d["full_charge_range_km"] = round(USABLE_KWH * 1000 / d.pop("whpkm"), 1)
    facts = dict(range_before_km=round(rng_b), range_now_km=round(rng_r), change_pct=round(delta_pct, 1),
                 whpkm_before=round(b["total"]), whpkm_now=round(r["total"]),
                 causes=[dict(cause=c[0], wh_per_km=round(c[1], 1), detail=c[2]) for c in causes if c[1] > 0.5])
    return _result("range_drop", "Why range changed", "\n".join(lines), facts,
                   chart=dict(kind="line", data=daily, x="day", y=["full_charge_range_km"]),
                   evidence=[f"trips: {recent['n']} recent vs {base['n']} baseline",
                             "energy decomposition per trip (aero, rolling, AC, pattern) from 1 Hz signals"])


def efficiency_drop(conn, tr=None):
    tr = _range_or(tr if tr and tr.days >= 2 else None, 28)
    daily = _rows(conn, """SELECT date(start_ts,'unixepoch','localtime') day, SUM(distance_km) km,
        SUM(energy_kwh)*1000/SUM(distance_km) whpkm, SUM(ac_kwh)*1000/SUM(distance_km) ac,
        SUM(aero_kwh)*1000/SUM(distance_km) aero, SUM(rolling_kwh)*1000/SUM(distance_km) roll,
        SUM(dynamic_kwh+aux_kwh)*1000/SUM(distance_km) pattern, AVG(avg_ambient_c) ambient,
        SUM(aggressive_share*distance_km)/SUM(distance_km) aggressive, MIN(start_ts) first_ts
        FROM trips WHERE start_ts >= ? AND start_ts < ? AND distance_km > 0 GROUP BY day HAVING km > 5 ORDER BY day""",
                  (tr.start, tr.end))
    if len(daily) < 3:
        return _result("efficiency_drop", "Efficiency", "Not enough days of driving to find an efficiency drop.")
    med = statistics.median(d["whpkm"] for d in daily)
    worst = max(daily, key=lambda d: d["whpkm"])
    others = [d for d in daily if d is not worst]
    avg = lambda k: sum(d[k] for d in others) / len(others)
    factors = sorted([("AC load", worst["ac"] - avg("ac")), ("speed / aerodynamic drag", worst["aero"] - avg("aero")),
                      ("tire rolling resistance", worst["roll"] - avg("roll")),
                      ("aggressive acceleration & braking", worst["pattern"] - avg("pattern"))], key=lambda f: -f[1])
    trip = _rows(conn, """SELECT trip_id, start_ts, road_type, wh_per_km, avg_speed_kmh FROM trips
                          WHERE date(start_ts,'unixepoch','localtime')=? ORDER BY wh_per_km DESC LIMIT 1""", (worst["day"],))[0]
    wd = datetime.strptime(worst["day"], "%Y-%m-%d").strftime("%a %d %b")
    text = (f"The largest efficiency drop was on {wd}: {worst['whpkm']:.0f} Wh/km "
            f"({worst['whpkm'] / med * 100 - 100:.0f}% worse than your typical {med:.0f} Wh/km, "
            f"i.e. {1000 / worst['whpkm']:.1f} vs {1000 / med:.1f} km/kWh). "
            f"Biggest contributors that day: " + ", ".join(f"{n} (+{v:.0f} Wh/km)" for n, v in factors if v > 1) + ". "
            f"Aggressive driving made up {worst['aggressive'] * 100:.0f}% of the distance and outside temperature averaged "
            f"{worst['ambient']:.0f} C. Worst single trip: #{trip['trip_id']} at {_t(trip['start_ts'])}, "
            f"{trip['road_type']}, {trip['wh_per_km']:.0f} Wh/km.")
    facts = dict(worst_day=wd, worst_whpkm=round(worst["whpkm"]), typical_whpkm=round(med),
                 pct_worse=round(worst["whpkm"] / med * 100 - 100), contributors={n: round(v, 1) for n, v in factors if v > 1},
                 aggressive_share_pct=round(worst["aggressive"] * 100))
    return _result("efficiency_drop", "Largest efficiency drop", text, facts,
                   chart=dict(kind="bar", data=[dict(day=d["day"][5:], wh_per_km=round(d["whpkm"], 1)) for d in daily],
                              x="day", y=["wh_per_km"]),
                   evidence=[f"{len(daily)} days of trips, {tr.label}", f"worst trip id {trip['trip_id']}"])


def adas_events(conn, tr=None):
    tr = tr or day_range(datetime.fromtimestamp(time.time() - DAY), "yesterday")
    ev = _rows(conn, """SELECT id, ts, trip_id, code reason, value speed_kmh, severity FROM events
                        WHERE event_type='ADAS_DISENGAGE' AND ts >= ? AND ts < ? ORDER BY ts""", (tr.start, tr.end))
    engaged = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='ADAS_ENGAGE' AND ts >= ? AND ts < ?",
                           (tr.start, tr.end)).fetchone()[0]
    unplanned = [e for e in ev if e["reason"] != "EXIT_ODD"]
    if not ev:
        return _result("adas_events", "ADAS disengagements", f"No ADAS disengagements {tr.label} "
                       f"(ADAS was engaged {engaged} times).", dict(count=0, engaged=engaged))
    counts = {}
    for e in unplanned:
        counts[e["reason"]] = counts.get(e["reason"], 0) + 1
    text = (f"{len(unplanned)} unplanned ADAS disengagements {tr.label} out of {engaged} engagements"
            + (f" (plus {len(ev) - len(unplanned)} normal exits at the end of the highway)" if len(ev) > len(unplanned) else "")
            + ". By reason: " + ", ".join(f"{v}x {k.replace('_', ' ').lower()}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
            + ". Times: " + "; ".join(f"{_t(e['ts'], False)} {e['reason']} at {e['speed_kmh'] or 0:.0f} km/h" for e in unplanned) + ".")
    table = [dict(time=_t(e["ts"]), trip=e["trip_id"], reason=e["reason"], speed_kmh=e["speed_kmh"]) for e in ev]
    return _result("adas_events", "ADAS disengagements", text,
                   dict(period=tr.label, unplanned=len(unplanned), engagements=engaged, by_reason=counts),
                   chart=dict(kind="bar", data=[dict(reason=k, count=v) for k, v in counts.items()], x="reason", y=["count"]),
                   table=table, evidence=[f"events ids {', '.join(str(e['id']) for e in ev)} (source SOME/IP 0x4ADA)"])


EVENT_LABELS = {"BATT_OVERTEMP": "battery over-temperature warning", "POWER_DERATE": "power derate",
                "TIRE_PRESSURE_LOW": "low tire pressure warning", "HARD_BRAKE": "hard braking event",
                "COLLISION": "collision", "ANOMALY_BATT_TEMP_RISE": "battery heating anomaly",
                "ADAS_DISENGAGE": "ADAS disengagement", "DTC": "diagnostic trouble code"}


def events_before(conn, event_type="BATT_OVERTEMP", tr=None, window_min=30):
    where, args = ("event_type=?", [event_type]) if event_type != "ANY_WARNING" else ("severity IN ('warning','critical')", [])
    if tr:
        where += " AND ts >= ? AND ts < ?"
        args += [tr.start, tr.end]
    target = _rows(conn, f"SELECT * FROM events WHERE {where} ORDER BY ts DESC LIMIT 1", args)
    label = EVENT_LABELS.get(event_type, "warning")
    if not target:
        return _result("events_before", "What happened before", f"I found no {label} in the stored history.")
    t = target[0]
    t0 = t["ts"] - window_min * 60
    prior = _rows(conn, """SELECT id, ts, event_type, code, description FROM events
                           WHERE ts >= ? AND ts <= ? AND id != ? AND event_type NOT IN ('ADAS_ENGAGE') ORDER BY ts""",
                  (t0, t["ts"], t["id"]))
    sig = _rows(conn, "SELECT * FROM signals_1s WHERE ts >= ? AND ts <= ? ORDER BY ts", (t0, t["ts"]))
    steps = [f"{_t(p['ts'], False)} - {p['description']}" for p in prior]
    facts = dict(event=t["description"], event_time=_t(t["ts"]), window_min=window_min)
    chart, evidence = None, [f"event id {t['id']}", f"{len(prior)} prior events"]
    if sig:
        first, last = sig[0], sig[-1]
        moving = [s for s in sig if s["speed_kmh"] > 5] or sig
        pump_off = next((s for s in sig if s["coolant_pump_ok"] == 0), None)
        avg_speed = sum(s["speed_kmh"] for s in moving) / len(moving)
        avg_power = sum(s["power_kw"] for s in moving) / len(moving)
        facts.update(batt_temp_start=round(first["batt_temp_c"], 1), batt_temp_at_event=round(last["batt_temp_c"], 1),
                     avg_speed_kmh=round(avg_speed), avg_power_kw=round(avg_power, 1),
                     max_power_kw=round(max(s["power_kw"] for s in sig), 1), ambient_c=round(last["ambient_c"], 1),
                     ac_kw=round(last["ac_kw"], 1), road=last["road_type"])
        context = (f"Over the {window_min} minutes before it, the car was mostly on the {last['road_type']} at "
                   f"{avg_speed:.0f} km/h average with {avg_power:.0f} kW average battery load "
                   f"(peak {facts['max_power_kw']:.0f} kW), outside temperature {last['ambient_c']:.0f} C. "
                   f"Battery temperature went from {first['batt_temp_c']:.1f} C to {last['batt_temp_c']:.1f} C.")
        cause = ""
        if pump_off:
            mins = (t["ts"] - pump_off["ts"]) / 60
            rate = (last["batt_temp_c"] - pump_off["batt_temp_c"]) / max(mins, 0.1)
            facts.update(pump_failed_at=_t(pump_off["ts"], False), minutes_pump_off_to_event=round(mins, 1),
                         heating_rate_c_per_min=round(rate, 1))
            cause = (f" Root cause: the battery coolant pump stopped at {_t(pump_off['ts'], False)}; from then the pack heated "
                     f"at {rate:.1f} C/min for {mins:.1f} min with no active cooling until the warning.")
            anomaly = next((p for p in prior if p["event_type"].startswith("ANOMALY")), None)
            if anomaly:
                lead = (t["ts"] - anomaly["ts"]) / 60
                facts["ai_anomaly_lead_min"] = round(lead, 1)
                cause += f" VehicleGPT's anomaly detector flagged the abnormal heating {lead:.1f} min before the BMS warning."
        step = max(1, len(sig) // 120)
        chart = dict(kind="line", x="time", y=["batt_temp_c", "speed_kmh", "power_kw"],
                     data=[dict(time=_t(s["ts"], False), batt_temp_c=round(s["batt_temp_c"], 1),
                                speed_kmh=round(s["speed_kmh"]), power_kw=round(s["power_kw"], 1)) for s in sig[::step]])
        evidence.append(f"{len(sig)} x 1 Hz samples from signals_1s")
    else:
        roll = _rows(conn, "SELECT * FROM rollups_1min WHERE minute >= ? AND minute <= ? ORDER BY minute", (t0, t["ts"]))
        context = "1-second data for this period has aged out of the 7-day tier; "
        if roll:
            context += (f"1-minute rollups show average speed {sum(r['avg_speed'] for r in roll) / len(roll):.0f} km/h and "
                        f"battery temperature up to {max(r['max_batt_temp'] for r in roll):.1f} C.")
            evidence.append(f"{len(roll)} rollups_1min rows")
        cause = ""
    text = (f"The {label} happened on {_t(t['ts'])}: {t['description']}. " + context + cause
            + ("\nTimeline before it:\n" + "\n".join(steps) if steps else ""))
    facts["timeline"] = steps
    return _result("events_before", f"Before the {label}", text, facts, chart=chart, evidence=evidence)


def city_vs_highway(conn, tr=None):
    tr = _range_or(tr if tr and tr.days >= 2 else None, 28)
    rows = _rows(conn, """SELECT road_type, SUM(distance_km) km, SUM(energy_kwh) e, SUM(end_ts-start_ts)/3600.0 hours,
        SUM(aero_kwh) aero, SUM(ac_kwh) ac, SUM(rolling_kwh) roll, SUM(aux_kwh) aux, SUM(dynamic_kwh) dyn, COUNT(*) n
        FROM trips WHERE start_ts >= ? AND start_ts < ? AND distance_km > 0 GROUP BY road_type""", (tr.start, tr.end))
    by = {r["road_type"]: r for r in rows}
    if "city" not in by or "highway" not in by:
        return _result("city_vs_highway", "City vs highway", "Need both city and highway trips to compare.")
    c, h = _per_km(dict(by["city"])), _per_km(dict(by["highway"]))
    diff = (h["total"] / c["total"] - 1) * 100
    text = (f"Over the {tr.label}: city driving used {c['total']:.0f} Wh/km ({by['city']['km']:.0f} km at "
            f"{c['speed']:.0f} km/h average) and highway used {h['total']:.0f} Wh/km ({by['highway']['km']:.0f} km at "
            f"{h['speed']:.0f} km/h). Highway costs {abs(diff):.0f}% {'more' if diff > 0 else 'less'} energy per km. "
            f"Why: aerodynamic drag is {h['aero']:.0f} Wh/km on the highway vs {c['aero']:.0f} in the city (drag grows with "
            f"speed squared), while in the city stop-and-go and idling add {c['pattern']:.0f} Wh/km "
            f"and AC/cabin load counts more per km at low speed ({c['ac']:.0f} vs {h['ac']:.0f} Wh/km); "
            f"regenerative braking recovers part of the city losses.")
    table = [dict(road=k, wh_per_km=round(v["total"], 1), aero=round(v["aero"], 1), rolling=round(v["rolling"], 1),
                  ac=round(v["ac"], 1), accel_idle=round(v["pattern"], 1), avg_speed=round(v["speed"]))
             for k, v in (("city", c), ("highway", h))]
    return _result("city_vs_highway", "City vs highway energy", text,
                   dict(city_whpkm=round(c["total"]), highway_whpkm=round(h["total"]), highway_vs_city_pct=round(diff)),
                   chart=dict(kind="bar", data=table, x="road", y=["aero", "rolling", "ac", "accel_idle"]),
                   table=table, evidence=[f"{by['city']['n']} city + {by['highway']['n']} highway trips"])


def current_status(conn, tr=None):
    r = _rows(conn, "SELECT * FROM signals_1s ORDER BY ts DESC LIMIT 1")
    if not r:
        return _result("current_status", "Vehicle status", "No vehicle data yet.")
    r = r[0]
    age = time.time() - r["ts"]
    issues = _rows(conn, """SELECT ts, description FROM events WHERE ts > ? AND severity IN ('warning','critical')
                            ORDER BY ts DESC LIMIT 5""", (time.time() - 900,))
    live = "Live" if age < 10 else f"Last data {_t(r['ts'])} (not streaming)"
    text = (f"{live}: {r['speed_kmh']:.0f} km/h on {r['road_type']}, battery {r['soc_pct']:.0f}% with about "
            f"{r['est_range_km']:.0f} km estimated range, pack temperature {r['batt_temp_c']:.1f} C "
            f"(coolant pump {'OK' if r['coolant_pump_ok'] else 'NOT RUNNING'}), {r['power_kw']:.1f} kW, "
            f"front-left tire {r['tire_psi_fl']:.1f} psi, ADAS {'engaged' if r['adas_active'] else 'off'}"
            + (", power derated" if r["power_derate"] else "") + ". "
            + ("Active issues in the last 15 min: " + "; ".join(f"{_t(i['ts'], False)} {i['description']}" for i in issues)
               if issues else "No warnings in the last 15 minutes."))
    return _result("current_status", "Vehicle status", text, {k: r[k] for k in r if k != "ts"} | {"issues": len(issues)})


def warnings(conn, tr=None):
    tr = _range_or(tr, 7)
    ev = _rows(conn, """SELECT id, ts, event_type, code, severity, description FROM events
                        WHERE ts >= ? AND ts < ? AND severity IN ('warning','critical') ORDER BY ts DESC""", (tr.start, tr.end))
    if not ev:
        return _result("warnings", "Warnings & DTCs", f"No warnings or fault codes {tr.label}.")
    counts = {}
    for e in ev:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
    crit = [e for e in ev if e["severity"] == "critical"]
    text = (f"{len(ev)} warnings/fault events {tr.label}: " + ", ".join(f"{v}x {k}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
            + "." + (" Critical: " + "; ".join(f"{_t(e['ts'])} {e['description']}" for e in crit) + "." if crit else ""))
    return _result("warnings", "Warnings & DTCs", text, dict(total=len(ev), by_type=counts, critical=len(crit)),
                   table=[dict(time=_t(e["ts"]), type=e["event_type"], code=e["code"], severity=e["severity"],
                               description=e["description"]) for e in ev[:25]])


def anomalies(conn, tr=None):
    tr = _range_or(tr, 28)
    ev = _rows(conn, "SELECT ts, code, description FROM events WHERE event_type LIKE 'ANOMALY%' AND ts >= ? AND ts < ? ORDER BY ts DESC",
               (tr.start, tr.end))
    if not ev:
        return _result("anomalies", "Anomalies", f"No anomalies detected {tr.label}.")
    text = f"{len(ev)} anomalies detected by the on-device learned baselines {tr.label}: " + \
           "; ".join(f"{_t(e['ts'])} - {e['description']}" for e in ev[:6]) + "."
    return _result("anomalies", "Detected anomalies", text, dict(count=len(ev)),
                   table=[dict(time=_t(e["ts"]), code=e["code"], description=e["description"]) for e in ev])


def log_search(conn, text, tr=None):
    from common.storage import has_fts
    tr = _range_or(tr, 180)
    if has_fts(conn):
        q = " OR ".join(f'"{w}"' for w in text.replace('"', "").split() if w)
        ev = _rows(conn, """SELECT e.ts, e.event_type, e.code, e.description FROM events_fts f JOIN events e ON e.id=f.rowid
                            WHERE events_fts MATCH ? AND e.ts >= ? ORDER BY e.ts DESC LIMIT 50""", (q, tr.start))
    else:
        ev = _rows(conn, "SELECT ts, event_type, code, description FROM events WHERE description LIKE ? AND ts >= ? "
                         "ORDER BY ts DESC LIMIT 50", (f"%{text}%", tr.start))
    body = (f"{len(ev)} log entries match '{text}'. Most recent: " +
            "; ".join(f"{_t(e['ts'])} {e['description']}" for e in ev[:5])) if ev else f"No log entries match '{text}'."
    return _result("log_search", f"Log search: {text}", body, dict(matches=len(ev)),
                   table=[dict(time=_t(e["ts"]), type=e["event_type"], code=e["code"], description=e["description"]) for e in ev])


def trips_summary(conn, tr=None):
    tr = _range_or(tr, 7)
    a = _rows(conn, TRIP_AGG, (tr.start, tr.end))[0]
    if not a["n"]:
        return _result("trips_summary", "Trips", f"No trips {tr.label}.")
    longest = _rows(conn, "SELECT * FROM trips WHERE start_ts >= ? AND start_ts < ? ORDER BY distance_km DESC LIMIT 1",
                    (tr.start, tr.end))[0]
    text = (f"{a['n']} trips {tr.label}: {a['km']:.0f} km driven, {a['e']:.1f} kWh used "
            f"({a['e'] * 1000 / a['km']:.0f} Wh/km), {a['hours']:.1f} h behind the wheel. Longest: "
            f"{longest['distance_km']:.0f} km on {_t(longest['start_ts'])} ({longest['road_type']}).")
    return _result("trips_summary", "Trips", text, dict(trips=a["n"], km=round(a["km"]), kwh=round(a["e"], 1)))


ROLLUP_COL = {("batt_temp_c", "max"): "max_batt_temp", ("batt_temp_c", "avg"): "avg_batt_temp",
              ("batt_temp_c", "min"): "avg_batt_temp", ("speed_kmh", "max"): "max_speed", ("speed_kmh", "avg"): "avg_speed",
              ("soc_pct", "min"): "min_soc", ("ambient_c", "avg"): "avg_ambient", ("ambient_c", "max"): "avg_ambient",
              ("ac_kw", "avg"): "avg_ac_kw", ("tire_psi_fl", "min"): "avg_tire_psi", ("tire_psi_fl", "avg"): "avg_tire_psi"}
SIGNAL_NAMES = {"batt_temp_c": ("battery temperature", "C"), "speed_kmh": ("speed", "km/h"), "soc_pct": ("state of charge", "%"),
                "power_kw": ("battery power", "kW"), "tire_psi_fl": ("front-left tire pressure", "psi"),
                "ambient_c": ("outside temperature", "C"), "ac_kw": ("AC power", "kW"), "est_range_km": ("estimated range", "km")}


def signal_stats(conn, signal="batt_temp_c", agg="max", tr=None):
    tr = _range_or(tr, 7)
    name, unit = SIGNAL_NAMES.get(signal, (signal, ""))
    fn = {"max": "MAX", "min": "MIN", "avg": "AVG"}[agg]
    oldest = conn.execute("SELECT MIN(ts) FROM signals_1s").fetchone()[0] or time.time()
    if tr.start >= oldest - 60:
        row = _rows(conn, f"SELECT {fn}({signal}) v FROM signals_1s WHERE ts >= ? AND ts < ? AND speed_kmh > 0", (tr.start, tr.end))[0]
        src = "signals_1s (1 Hz)"
        when = None
        if agg != "avg" and row["v"] is not None:
            w = _rows(conn, f"SELECT ts FROM signals_1s WHERE ts >= ? AND ts < ? AND {signal} = ? LIMIT 1", (tr.start, tr.end, row["v"]))
            when = _t(w[0]["ts"]) if w else None
    else:
        col = ROLLUP_COL.get((signal, agg), ROLLUP_COL.get((signal, "avg")))
        if not col:
            return _result("signal_stats", name, f"{name} is only kept at 1 Hz for {7} days; ask about a shorter period.")
        row = _rows(conn, f"SELECT {fn}({col}) v FROM rollups_1min WHERE minute >= ? AND minute < ?", (tr.start, tr.end))[0]
        src, when = "rollups_1min (1-minute tier)", None
    if row["v"] is None:
        return _result("signal_stats", name, f"No {name} data {tr.label}.")
    word = {"max": "Highest", "min": "Lowest", "avg": "Average"}[agg]
    text = f"{word} {name} {tr.label}: {row['v']:.1f} {unit}" + (f" (at {when})" if when else "") + "."
    return _result("signal_stats", f"{word} {name}", text, dict(value=round(row["v"], 2), unit=unit, period=tr.label),
                   evidence=[f"source table: {src}"])


HELP = ("I can answer questions about your vehicle's history and live state, for example:\n"
        "- Why has my driving range reduced over the last two weeks?\n- When did efficiency show the largest drop?\n"
        "- Show all ADAS disengagement events from yesterday.\n- What happened before the battery overheating warning?\n"
        "- Compare energy consumption between city and highway driving.\n- What's happening right now?\n"
        "- Any warnings or fault codes this week? / Any anomalies? / Search logs for coolant\n"
        "- What was the highest battery temperature yesterday?")


def help_tool(conn, tr=None):
    return _result("help", "What I can do", HELP)


TOOLS = dict(range_drop=range_drop, efficiency_drop=efficiency_drop, adas_events=adas_events, events_before=events_before,
             city_vs_highway=city_vs_highway, current_status=current_status, warnings=warnings, anomalies=anomalies,
             log_search=log_search, trips_summary=trips_summary, signal_stats=signal_stats, help=help_tool)
