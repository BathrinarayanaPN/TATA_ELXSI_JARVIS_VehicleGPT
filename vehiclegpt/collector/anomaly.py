"""Online anomaly detection with learned baselines (no labels, O(1) memory per signal).

1. Battery temperature rise rate vs the vehicle's own learned rate distribution.
2. Energy consumption (Wh/km over 5 min) vs a learned baseline per road type.
These catch behaviour nobody scripted, e.g. a failing coolant pump before the BMS warning fires.
"""
import math
from collections import deque


class _EW:
    def __init__(self, alpha, mean=0.0, var=0.0):
        self.alpha, self.mean, self.var, self.n = alpha, mean, var, 0

    def update(self, x):
        self.n += 1
        d = x - self.mean
        self.mean += self.alpha * d
        self.var = (1 - self.alpha) * (self.var + self.alpha * d * d)

    @property
    def std(self):
        return math.sqrt(max(self.var, 1e-9))


class AnomalyDetector:
    def __init__(self):
        self.temps = deque(maxlen=31)
        self.powers = deque(maxlen=31)
        self.rates = {b: _EW(0.002) for b in range(4)}   # learned per load bucket (0-15-30-45+ kW)
        self.rate_hits = 0
        self.rate_cooldown_until = 0.0
        self.window = deque()          # (ts, power_kw, speed_kmh, road)
        self.eff = {"city": _EW(0.05), "highway": _EW(0.05)}
        self.eff_next_eval = 0.0
        self.eff_cooldown_until = 0.0

    def update(self, r):
        events = []
        ts = r["ts"]
        # A new trip or a data gap invalidates the rate windows
        if r.get("trip_id") != getattr(self, "_trip", None) or ts - getattr(self, "_last_ts", ts) > 5:
            self.temps.clear()
            self.powers.clear()
            self.window.clear()
            self.rate_hits = 0
        self._trip, self._last_ts = r.get("trip_id"), ts
        # ---- 1. battery temperature rise rate ----
        self.temps.append(r["batt_temp_c"])
        self.powers.append(max(r["power_kw"], 0.0))
        if len(self.temps) == self.temps.maxlen and r["speed_kmh"] > 5:
            rate = (self.temps[-1] - self.temps[0]) / 30.0
            load = sum(self.powers) / len(self.powers)
            base = self.rates[min(3, int(load // 15))]
            threshold = max(0.015, base.mean + 5 * base.std)
            if base.n > 300 and rate > threshold:       # unusual -> never learn from it
                self.rate_hits += 1 if r["batt_temp_c"] > 33 else 0
                if self.rate_hits >= 15 and ts > self.rate_cooldown_until:
                    ratio = rate / max(abs(base.mean) + 2 * base.std, 0.002)
                    events.append(dict(
                        event_type="ANOMALY_BATT_TEMP_RISE", severity="warning", source="AI_ANOMALY",
                        code="AN_BT_RATE", value=round(rate * 60, 2),
                        description=(f"Battery temperature rising {rate * 60:.1f} C/min at {r['batt_temp_c']:.1f} C "
                                     f"under {load:.0f} kW load, {ratio:.0f}x the learned rate for that load")))
                    self.rate_cooldown_until = ts + 600
            else:
                self.rate_hits = 0
                base.update(rate)
        # ---- 2. consumption vs learned baseline per road type ----
        self.window.append((ts, r["power_kw"], r["speed_kmh"], r["road_type"]))
        while self.window and self.window[0][0] < ts - 300:
            self.window.popleft()
        if ts >= self.eff_next_eval and len(self.window) > 240:
            self.eff_next_eval = ts + 60
            dist = sum(w[2] for w in self.window) / 3600
            road = r["road_type"]
            if dist > 1.0 and road in self.eff:
                whpkm = sum(w[1] for w in self.window) / 3600 * 1000 / dist
                base = self.eff[road]
                if base.n > 60 and whpkm > base.mean * 1.5 and ts > self.eff_cooldown_until:
                    events.append(dict(
                        event_type="ANOMALY_HIGH_CONSUMPTION", severity="info", source="AI_ANOMALY",
                        code="AN_EFF", value=round(whpkm, 1),
                        description=(f"{road.title()} consumption {whpkm:.0f} Wh/km over last 5 min, "
                                     f"{(whpkm / base.mean - 1) * 100:.0f}% above learned baseline "
                                     f"{base.mean:.0f} Wh/km")))
                    self.eff_cooldown_until = ts + 900
                else:
                    if base.n == 0:
                        base.mean = whpkm
                    base.update(whpkm)
        return events
