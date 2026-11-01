"""Physics-based EV model shared by the history seeder, the live simulator and the CARLA bridge.

Longitudinal dynamics (aero drag + rolling resistance + inertia) -> battery power,
a lumped battery thermal model with a coolant pump, SOC/voltage/current and range estimation.
The same equations are used by the assistant to decompose energy use into causes.
"""
from dataclasses import dataclass

BATTERY_KWH = 60.0
USABLE_KWH = 58.0
MASS = 1800.0
CDA = 0.72            # drag coefficient x frontal area (m^2)
RHO = 1.2
G = 9.81
CRR_BASE = 0.010
NOMINAL_PSI = 36.0
DRIVE_EFF = 0.90
REGEN_EFF = 0.60
AUX_KW = 0.3
TEMP_WARN_C = 55.0
TEMP_CLEAR_C = 45.0


def crr_for(psi):
    """Rolling resistance rises ~4% per psi of under-inflation."""
    return CRR_BASE * (1 + max(0.0, NOMINAL_PSI - psi) * 0.04)


@dataclass
class Faults:
    coolant_pump_failed: bool = False
    thermal_accel: float = 1.0      # >1 speeds up heating for live demos


class EVModel:
    def __init__(self, soc=90.0, odometer=12450.0, ambient=25.0):
        self.soc = soc
        self.odometer = odometer
        self.batt_temp = ambient + 2.0
        self.ema_whpkm = 160.0
        self.speed = 0.0
        self.faults = Faults()
        self.over_temp_warn = False
        self.derate = False

    def step(self, speed_kmh, dt, ambient, ac_kw, psi):
        v0, v1 = self.speed / 3.6, speed_kmh / 3.6
        accel = (v1 - v0) / dt
        force = 0.5 * RHO * CDA * v1 * v1 + (crr_for(psi) * MASS * G if v1 > 0.1 else 0.0) + MASS * accel
        p_mech = force * v1
        p_batt_kw = (p_mech / DRIVE_EFF if p_mech > 0 else p_mech * REGEN_EFF) / 1000
        power_kw = p_batt_kw + ac_kw + AUX_KW

        # Battery thermal (lumped mass, ~30 s power filter): Joule heating vs active or passive cooling.
        # With a failed coolant pump the stagnant loop also carries drive-unit heat into the pack.
        a = min(1.0, dt / 30.0)
        self.p_filt = (1 - a) * getattr(self, "p_filt", 0.0) + a * max(p_batt_kw, 0.0)
        heat = 3e-5 * self.p_filt ** 2
        if self.faults.coolant_pump_failed:
            heat = (heat + 0.0012 * self.p_filt) * self.faults.thermal_accel
            cool = 0.0005 * (self.batt_temp - ambient)
        elif self.batt_temp > 28:
            cool = 0.01 * (self.batt_temp - 28)
        else:
            cool = 0.002 * (self.batt_temp - ambient)
        self.batt_temp += (heat - cool) * dt

        if self.batt_temp >= TEMP_WARN_C and not self.over_temp_warn:
            self.over_temp_warn = self.derate = True
        elif self.batt_temp < TEMP_CLEAR_C and self.over_temp_warn:
            self.over_temp_warn = self.derate = False

        self.soc = max(0.0, self.soc - power_kw * dt / 3600 / BATTERY_KWH * 100)
        volt = 320 + 0.8 * self.soc - 0.04 * power_kw
        if speed_kmh > 5:
            inst = max(-200.0, min(800.0, power_kw * 1000 / speed_kmh))
            alpha = 0.001 * dt
            self.ema_whpkm = (1 - alpha) * self.ema_whpkm + alpha * inst
        self.odometer += speed_kmh * dt / 3600
        self.speed = speed_kmh
        pump_on = not self.faults.coolant_pump_failed
        return dict(
            speed_kmh=speed_kmh, accel_mps2=accel, power_kw=power_kw, soc_pct=self.soc,
            batt_voltage=volt, batt_current_a=power_kw * 1000 / volt, batt_temp_c=self.batt_temp,
            coolant_pump_ok=int(pump_on), pump_duty=(min(100, max(20, (self.batt_temp - 20) * 5)) if pump_on else 0),
            over_temp_warn=int(self.over_temp_warn), power_derate=int(self.derate),
            ambient_c=ambient, ac_kw=ac_kw, tire_psi_fl=psi,
            est_range_km=self.soc / 100 * BATTERY_KWH * 1000 / max(self.ema_whpkm, 50),
            odometer_km=self.odometer,
        )


def trip_energy_breakdown(rows):
    """Split battery energy of a 1 Hz trace into causes (kWh): aero, rolling, AC, aux, dynamic.

    dynamic = acceleration/regeneration losses (driving style) = total - modelled steady loads.
    """
    n = len(rows)
    dist = sum(r["speed_kmh"] for r in rows) / 3600
    energy = sum(r["power_kw"] for r in rows) / 3600
    aero = rolling = 0.0
    for r in rows:
        v = r["speed_kmh"] / 3.6
        aero += 0.5 * RHO * CDA * v ** 3 / DRIVE_EFF
        if v > 0.1:
            rolling += crr_for(r["tire_psi_fl"]) * MASS * G * v / DRIVE_EFF
    aero, rolling = aero / 3.6e6, rolling / 3.6e6
    ac = sum(r["ac_kw"] for r in rows) / 3600
    aux = AUX_KW * n / 3600
    return dict(distance_km=dist, energy_kwh=energy, wh_per_km=energy * 1000 / dist if dist > 0.05 else None,
                avg_speed_kmh=sum(r["speed_kmh"] for r in rows) / n, aero_kwh=aero, rolling_kwh=rolling,
                ac_kwh=ac, aux_kwh=aux, dynamic_kwh=energy - aero - rolling - ac - aux)
