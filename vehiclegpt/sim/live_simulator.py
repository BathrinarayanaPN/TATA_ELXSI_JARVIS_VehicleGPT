"""Live vehicle node: runs the EV model at 10 Hz and publishes on real automotive transports.

  CAN       : 6 DBC-encoded frames per cycle (VCU, BMS, HVAC, TPMS)  -> CAN bus (builtin-udp / SocketCAN)
  SOME/IP   : ADAS state-change notifications (service 0x4ADA, event 0x8001) over UDP
  slog2 log : QNX-style diagnostic log lines (DTC set/clear, system messages)

Scenario control (fault injection) arrives as JSON on UDP CONTROL_PORT, e.g.
  {"cmd": "fault", "name": "coolant_pump"} | {"cmd": "tire_leak"} | {"cmd": "adas_disengage", "reason": "CAMERA_BLOCKED_GLARE"}
  {"cmd": "road", "value": "highway"} | {"cmd": "style", "value": "aggressive"} | {"cmd": "ac", "value": 3.5} | {"cmd": "clear"}

  python -m sim.live_simulator            # internal driver model
  python -m sim.carla_bridge              # speed comes from CARLA instead (same node)
"""
import argparse
import json
import socket
import time
from datetime import datetime

import numpy as np

import config
from common import canbus, someip
from common.dbc import Database
from sim.driver_model import DriverModel
from sim.vehicle_model import NOMINAL_PSI, EVModel

ROAD_CODES = {"city": 0, "highway": 1}
STYLE_CODES = {"normal": 0, "aggressive": 1}


class Slog2Writer:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, buffer, severity, msg):
        now = datetime.now()
        self.f.write(f"{now:%b %d %H:%M:%S}.{now.microsecond // 1000:03d}    {buffer:<14} 0  {severity}  {msg}\n")


class VehicleNode:
    def __init__(self, seed=None, ambient=29.0, use_driver=True):
        self.rng = np.random.default_rng(seed)
        self.db = Database(config.DBC_PATH)
        self.can = canbus.open_tx()
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ctrl.bind(("0.0.0.0", config.CONTROL_PORT))
        self.ctrl.setblocking(False)
        self.log = Slog2Writer(config.DIAG_LOG)
        self.ev = EVModel(soc=88.0, ambient=ambient)
        self.driver = DriverModel(self.rng) if use_driver else None
        self.ambient = ambient
        self.psi = NOMINAL_PSI
        self.tire_leak = False
        self.road_pin, self.style = None, "normal"
        self.ac_override = None
        self.road, self.road_until = "city", time.time() + 240
        self.trip_id, self.trip_started = 1, time.time()
        self.adas, self.someip_session = False, 1
        self.pending_disengage = None
        self.inverter_fault = False
        self.adas_fault = False
        self.heatwave_fault = False
        self.all_faults_active = False
        self.fault_stop_override = False
        self.log.write("vgpt_sim.1001", 5, "Vehicle node started, publishing CAN 10 Hz + SOME/IP ADAS service 0x4ADA")

    # ---------------- scenario control ----------------
    def poll_control(self):
        while True:
            try:
                pkt, _ = self.ctrl.recvfrom(2048)
            except (BlockingIOError, OSError):
                return
            try:
                self.apply(json.loads(pkt.decode()))
            except Exception as e:  # never crash the vehicle node on a bad command
                self.log.write("vgpt_sim.1001", 3, f"Bad control command ignored: {e}")

    def apply(self, c):
        cmd = c.get("cmd")
        name = c.get("name", "")
        if cmd in ("all_faults", "fault_all") or (cmd == "fault" and name in ("all", "all_faults")):
            self.ev.faults.coolant_pump_failed = True
            self.ev.faults.thermal_accel = 9.0
            self.tire_leak = True
            self.psi = 18.0
            self.ambient = 42.0
            self.ac_override = 3.2
            self.inverter_fault = True
            self.adas_fault = True
            self.heatwave_fault = True
            self.all_faults_active = True
            self.fault_stop_override = True
            self.road_pin = "highway"
            self.log.write("bms_diag.4242", 2, "DTC P0C73 SET Battery coolant pump performance - pump stalled")
            self.log.write("tpms_diag.4311", 2, "DTC C0035 SET Front-left TPMS pressure dropped to 18.0 psi")
            self.log.write("pcu_diag.2014", 2, "DTC P0A0F SET Motor inverter phase temperature exceeded 110C")
            self.log.write("adas_diag.5120", 3, "DTC U0100 SET Camera/Radar vision blocked -> Takeover requested")
            self.log.write("hvac_diag.3101", 3, f"High ambient heatwave 42.0C detected. AC compressor load 3.2 kW")
            self.log.write("vgpt_safety.01", 1, "ALL VEHICLE FAULTS ACTIVE: Minimum Risk Condition Emergency Safe Stop initiated")
            print("\n>>> [ALL FAULTS APPLIED] Total System Cascade Failure Triggered! Emergency Stop Active! <<<")
        elif cmd == "fault" and (name == "coolant_pump" or name == "batt_overtemp"):
            self.ev.faults.coolant_pump_failed = True
            self.ev.faults.thermal_accel = float(c.get("accel", 9.0))
            self.fault_stop_override = False
            self.road_pin = "highway"
            self.log.write("bms_diag.4242", 2, "DTC P0C73 SET Battery coolant pump performance - pump stalled, thermal runaway risk")
            print("\n>>> [FAULT APPLIED] Battery Coolant Pump Stalled -> DTC P0C73 -> Battery Temperature Rising! <<<")
        elif cmd in ("tire_leak", "tpms_puncture") or (cmd == "fault" and name == "tire_puncture"):
            self.tire_leak = True
            self.psi = float(c.get("psi", 18.0))
            self.fault_stop_override = False
            self.log.write("tpms_diag.4311", 2, "DTC C0035 SET Front-left TPMS pressure dropped to 18.0 psi (puncture detected)")
            print("\n>>> [FAULT APPLIED] Front-Left Tire Puncture -> DTC C0035 (18 psi) -> Rolling Resistance +45%! <<<")
        elif cmd in ("ambient_heat", "heatwave") or (cmd == "fault" and name == "ambient_heat"):
            self.ambient = float(c.get("ambient", 42.0))
            self.ac_override = float(c.get("ac", 3.2))
            self.heatwave_fault = True
            self.fault_stop_override = False
            self.log.write("hvac_diag.3101", 3, f"High ambient heatwave {self.ambient:.1f}C detected. AC compressor load elevated to {self.ac_override:.1f} kW")
            print(f"\n>>> [FAULT APPLIED] Extreme Ambient Heat {self.ambient}C -> HVAC Compressor Load 3.2 kW -> Range Impact! <<<")
        elif cmd == "fault" and name == "inverter":
            self.inverter_fault = True
            self.fault_stop_override = False
            self.log.write("pcu_diag.2014", 2, "DTC P0A0F SET Motor inverter phase temperature exceeded 110C -> Torque limited")
            print("\n>>> [FAULT APPLIED] Motor Inverter Overheating -> DTC P0A0F -> Inverter Efficiency Reduced! <<<")
        elif cmd in ("adas_disengage", "adas_occlusion") or (cmd == "fault" and name == "adas_occlusion"):
            self.pending_disengage = c.get("reason", "RADAR_CAMERA_OCCLUSION")
            self.adas_fault = True
            self.fault_stop_override = False
            self.log.write("adas_diag.5120", 3, f"DTC U0100 SET Camera/Radar vision blocked ({self.pending_disengage}) -> Takeover requested")
            print(f"\n>>> [FAULT APPLIED] ADAS Sensor Occlusion -> DTC U0100 -> Instant Takeover Request Issued! <<<")
        elif cmd == "emergency_stop":
            self.fault_stop_override = bool(c.get("active", True))
            self.log.write("vgpt_safety.01", 1, f"Emergency Stop override set to {self.fault_stop_override}")
            print(f"\n>>> [SAFETY COMMAND] Emergency Safe Stop override: {self.fault_stop_override} <<<")
        elif cmd == "road":
            self.road_pin = None if c.get("value") == "auto" else c.get("value")
        elif cmd == "style":
            self.style = c.get("value", "normal")
        elif cmd == "ac":
            self.ac_override = c.get("value")
        elif cmd == "ambient":
            self.ambient = float(c.get("value", c.get("ambient", 29)))
            if self.ambient > 35 or "ac" in c:
                self.ac_override = float(c.get("ac", 3.2))
                self.heatwave_fault = True
            else:
                self.heatwave_fault = False
            self.fault_stop_override = False
        elif cmd == "clear":
            if self.ev.faults.coolant_pump_failed:
                self.log.write("bms_diag.4242", 4, "DTC P0C73 CLEARED Battery coolant pump nominal")
            self.ev.faults.coolant_pump_failed = False
            self.ev.faults.thermal_accel = 1.0
            self.tire_leak, self.psi = False, NOMINAL_PSI
            self.ambient = 25.0
            self.inverter_fault = False
            self.adas_fault = False
            self.heatwave_fault = False
            self.all_faults_active = False
            self.fault_stop_override = False
            self.pending_disengage = None
            self.road_pin, self.style, self.ac_override = None, "normal", None
            print("\n>>> [ALL FAULTS CLEARED] Nominal vehicle operation restored. All DTCs cleared. <<<")
        self.log.write("vgpt_sim.1001", 5, f"Scenario command applied: {json.dumps(c)}")

    # ---------------- publishing ----------------
    def send_adas(self, engaged, reason, speed):
        self.udp.sendto(someip.pack_adas_event(engaged, reason, speed, self.someip_session),
                        (config.COLLECTOR_HOST, config.SOMEIP_PORT))
        self.someip_session = self.someip_session % 0xFFFF + 1

    def publish(self, s):
        frames = [
            ("VCU_Speed", dict(VehicleSpeed=s["speed_kmh"], LongAccel=s["accel_mps2"], Odometer=s["odometer_km"])),
            ("BMS_Pack", dict(PackVoltage=s["batt_voltage"], PackCurrent=s["batt_current_a"], SOC=s["soc_pct"],
                              PackPower=s["power_kw"])),
            ("BMS_Thermal", dict(BattTemp=s["batt_temp_c"], CoolantPumpOK=s["coolant_pump_ok"],
                                 OverTempWarn=s["over_temp_warn"], CoolantPumpDuty=s["pump_duty"],
                                 AmbientTemp=s["ambient_c"])),
            ("HVAC_Status", dict(AcPower=s["ac_kw"], CabinSetTemp=22)),
            ("TPMS_Status", dict(TirePsiFL=s["tire_psi_fl"], TirePsiFR=NOMINAL_PSI, TireLowWarn=int(s["tire_psi_fl"] < 30))),
            ("VCU_Status", dict(TripId=self.trip_id, RoadType=ROAD_CODES[self.road], DriveMode=STYLE_CODES[self.style],
                                PowerDerate=s["power_derate"], AdasActive=int(self.adas), EstRange=s["est_range_km"])),
        ]
        for name, values in frames:
            self.can.send(*self.db.encode(name, values))

    # ---------------- one 100 ms cycle ----------------
    def tick(self, dt=0.1, speed_override=None):
        now = time.time()
        self.poll_control()
        if self.road_pin:
            self.road = self.road_pin
        elif now > self.road_until:
            self.road = "highway" if self.road == "city" else "city"
            self.road_until = now + (360 if self.road == "highway" else 240)
        if now - self.trip_started > 1200 and self.ev.speed < 1:      # new trip every ~20 min at a stop
            self.trip_id += 1
            self.trip_started = now
        stopping = now - self.trip_started > 1170
        if self.tire_leak and self.psi > 27.0:
            self.psi = max(27.0, self.psi - 0.05 * dt)
        speed = speed_override if speed_override is not None else \
            self.driver.step(dt, self.road, self.style, self.ev.derate, stopping)
        ac = self.ac_override if self.ac_override is not None else max(0.0, (self.ambient - 22) * 0.28)
        s = self.ev.step(speed, dt, self.ambient, ac, self.psi)

        # ADAS (published over SOME/IP)
        if self.road == "highway" and not self.adas and speed > 70 and self.rng.random() < 0.02 * dt * 10:
            self.adas = True
            self.send_adas(True, "ENGAGED", speed)
        if self.adas and (self.pending_disengage or self.road != "highway" or speed < 50):
            reason = self.pending_disengage or ("EXIT_ODD" if self.road != "highway" else "DRIVER_BRAKE_OVERRIDE")
            self.adas, self.pending_disengage = False, None
            self.send_adas(False, reason, speed)
        self.publish(s)
        return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--ambient", type=float, default=29.0)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    node = VehicleNode(seed=args.seed, ambient=args.ambient)
    dt = 1.0 / args.hz
    print(f"[sim] publishing CAN ({config.CAN_BACKEND}) + SOME/IP to {config.COLLECTOR_HOST}; "
          f"control on udp/{config.CONTROL_PORT}. Ctrl+C to stop.")
    next_t, last_print = time.time(), 0
    try:
        while True:
            s = node.tick(dt)
            if time.time() - last_print > 0.5:
                last_print = time.time()
                derate_str = "ACTIVE (-50%)" if s.get('power_derate') else "OFF"
                pump_str = "OK" if s.get('coolant_pump_ok', 1) else "FAILED (P0C73)"
                print(f"\r[TATA ELXSI SDV] {node.road.upper():<7} | SPEED: {s['speed_kmh']:5.1f} km/h | "
                      f"SOC: {s['soc_pct']:4.1f}% | TEMP: {s['batt_temp_c']:4.1f}C | PWR: {s['power_kw']:+5.1f}kW | "
                      f"PUMP: {pump_str:<14} | DERATE: {derate_str:<12} | "
                      f"ADAS: {'ON' if node.adas else 'OFF'}", end="", flush=True)
            next_t += dt
            time.sleep(max(0.0, next_t - time.time()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
