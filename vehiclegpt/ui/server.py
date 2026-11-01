"""High-Performance FastAPI Web Server for Tata Elxsi SDV VehicleGPT.

Serves the Next-Generation Automotive Digital Cockpit, WebSocket telemetry stream,
CARLA video stream proxy, VehicleGPT AI engine with streaming LLM, and remote controls.
Run:
  python -m ui.server
"""
import asyncio
import json
import os
import socket
import time
from pathlib import Path

import pandas as pd
import requests
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from assistant.engine import Engine
from common import storage

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
WEB_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Tata Elxsi SDV VehicleGPT", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = Engine()


def get_db():
    try:
        return storage.connect(readonly=True)
    except Exception:
        return storage.connect()


def send_udp(host, port, payload):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(json.dumps(payload).encode(), (host, port))
    sock.close()


# ---------------- REST APIs ----------------

@app.get("/api/live")
def get_live():
    """Returns the latest CAN telemetry signals, trip status, and active warnings."""
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT * FROM signals_1s WHERE ts > (SELECT MAX(ts) FROM signals_1s) - 30 ORDER BY ts DESC LIMIT 1",
        conn
    )
    if df.empty:
        # Fallback nominal telemetry
        return {
            "online": False,
            "ts": time.time(),
            "speed_kmh": 0.0,
            "soc_pct": 88.0,
            "batt_temp_c": 28.5,
            "power_kw": 0.0,
            "est_range_km": 345.0,
            "tire_psi_fl": 36.0,
            "coolant_pump_ok": 1,
            "power_derate": 0,
            "adas_active": 0,
            "road_type": "highway",
            "drive_mode": "normal",
            "trip_id": 1,
            "ac_kw": 1.2
        }
    row = df.iloc[0].to_dict()
    row["online"] = (time.time() - row["ts"]) < 5.0
    return row


@app.get("/api/telemetry_history")
def get_telemetry_history(seconds: int = 120):
    """Returns the last N seconds of 1 Hz telemetry for live strip charts."""
    conn = get_db()
    df = pd.read_sql_query(
        f"SELECT ts, speed_kmh, soc_pct, batt_temp_c, power_kw, tire_psi_fl, adas_active "
        f"FROM signals_1s WHERE ts > (SELECT MAX(ts) FROM signals_1s) - {seconds} ORDER BY ts ASC",
        conn
    )
    return df.to_dict(orient="records")


@app.get("/api/can_frames")
def get_can_frames():
    """Simulates/returns real-time CAN bus frames (Vector CANoe style inspector)."""
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT * FROM signals_1s ORDER BY ts DESC LIMIT 1",
        conn
    )
    r = df.iloc[0] if not df.empty else {
        "speed_kmh": 65.0, "power_kw": 18.2, "soc_pct": 88.0, "batt_temp_c": 31.0,
        "coolant_pump_ok": 1, "tire_psi_fl": 36.0, "trip_id": 1, "road_type": "highway",
        "drive_mode": "normal", "power_derate": 0, "adas_active": 1, "est_range_km": 340.0, "ts": time.time()
    }

    # Generate DBC-aligned CAN frame representations
    now = time.time()
    frames = [
        {
            "id": "0x100", "name": "VCU_Speed", "dlc": 8, "rate_hz": 10,
            "signals": f"Speed={r['speed_kmh']:.1f} km/h, LongAccel=0.12 m/s2",
            "raw_hex": f"{(int(r['speed_kmh']*100) & 0xFFFF):04X} 007B 0000 3039",
            "delta_ms": 100.1, "node": "VCU"
        },
        {
            "id": "0x110", "name": "BMS_Pack", "dlc": 8, "rate_hz": 10,
            "signals": f"SOC={r['soc_pct']:.1f}%, Power={r['power_kw']:.1f} kW, Volt=390.4 V",
            "raw_hex": f"0F40 00A2 {(int(r['soc_pct']*100) & 0xFFFF):04X} 0472",
            "delta_ms": 99.8, "node": "BMS"
        },
        {
            "id": "0x111", "name": "BMS_Thermal", "dlc": 8, "rate_hz": 10,
            "signals": f"BattTemp={r['batt_temp_c']:.1f} °C, PumpOK={r['coolant_pump_ok']}, Warn={r.get('over_temp_warn', 0)}",
            "raw_hex": f"{(int(r['batt_temp_c']*100) & 0xFFFF):04X} 0100 4B00 00FA",
            "delta_ms": 100.2, "node": "BMS"
        },
        {
            "id": "0x120", "name": "HVAC_Status", "dlc": 4, "rate_hz": 5,
            "signals": "AcPower=1.40 kW, CabinSetTemp=22.0 °C",
            "raw_hex": "0578 2C00",
            "delta_ms": 200.0, "node": "HVAC"
        },
        {
            "id": "0x130", "name": "TPMS_Status", "dlc": 6, "rate_hz": 2,
            "signals": f"TirePsiFL={r['tire_psi_fl']:.1f} psi, TirePsiFR=36.0 psi, LowWarn={int(r['tire_psi_fl'] < 30)}",
            "raw_hex": f"{(int(r['tire_psi_fl']*100) & 0xFFFF):04X} 0E10 0000",
            "delta_ms": 500.1, "node": "TPMS"
        },
        {
            "id": "0x140", "name": "VCU_Status", "dlc": 8, "rate_hz": 10,
            "signals": f"Trip={r['trip_id']}, Road={r['road_type']}, Derate={r['power_derate']}, ADAS={r['adas_active']}",
            "raw_hex": f"{int(r['trip_id']):04X} 0100 0000 {(int(r['est_range_km']*10) & 0xFFFF):04X}",
            "delta_ms": 99.9, "node": "VCU"
        },
    ]
    return frames


@app.get("/api/events")
def get_events(limit: int = 15):
    """Returns recent events, DTCs, and SOME/IP logs."""
    conn = get_db()
    df = pd.read_sql_query(
        f"SELECT datetime(ts,'unixepoch','localtime') time, event_type, severity, source, description, value "
        f"FROM events ORDER BY ts DESC LIMIT {limit}",
        conn
    )
    return df.to_dict(orient="records")


class QueryRequest(BaseModel):
    question: str
    role: str = "driver"
    lang: str = "en"


@app.post("/api/ask")
def ask_assistant(req: QueryRequest):
    """Executes deterministic fact query + cause attribution via VehicleGPT Engine."""
    res = engine.ask(req.question)
    return res


@app.get("/api/explain_stream")
async def explain_stream(question: str, role: str = "driver", lang: str = "en", mode: str = "all"):
    """Streams token-by-token natural language explanation using Server-Sent Events (SSE)."""
    res = engine.ask(question)

    def event_generator():
        yield f"data: {json.dumps({'type': 'facts', 'data': res})}\n\n"
        if engine.llm.available():
            for chunk in engine.explain(res, role=role, lang=lang, mode=mode):
                yield f"data: {json.dumps({'type': 'token', 'token': chunk})}\n\n"
                time.sleep(0.01)
        else:
            # Fallback deterministic answer formatted nicely in selected language
            text_to_show = res.get("summaries", {}).get(mode, {}).get(lang) or res.get("simple_text") or res["text"]
            yield f"data: {json.dumps({'type': 'token', 'token': text_to_show})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


from typing import Any

class ControlCommand(BaseModel):
    cmd: str
    name: str = None
    value: Any = None
    accel: float = 9.0
    reason: str = "DRIVER_OVERRIDE_STEER"
    psi: float = None
    ambient: float = None
    ac: float = None

    class Config:
        extra = "allow"


@app.post("/api/control")
def send_vehicle_control(cmd: ControlCommand):
    """Sends fault injection / scenario command to vehicle simulator test harness."""
    payload = cmd.dict(exclude_none=True)
    send_udp(config.SIM_HOST, config.CONTROL_PORT, payload)
    return {"status": "sent", "command": payload}


class CarlaControl(BaseModel):
    autopilot: bool = None
    throttle: float = None
    steer: float = None
    brake: float = None
    reverse: bool = False
    hand_brake: bool = False
    weather: str = None
    camera: str = None
    town: str = None

    class Config:
        extra = "allow"


@app.post("/api/carla/control")
def send_carla_control(c: CarlaControl):
    """Sends direct control commands to CARLA Bridge."""
    if c.town:
        send_udp(config.SIM_HOST, config.CONTROL_PORT, {"cmd": "carla_town", "town": c.town})
    if c.autopilot is not None:
        send_udp(config.SIM_HOST, config.CONTROL_PORT, {"cmd": "carla_autopilot", "value": c.autopilot})
    if c.weather:
        send_udp(config.SIM_HOST, config.CONTROL_PORT, {"cmd": "carla_weather", "preset": c.weather})
    if c.camera:
        send_udp(config.SIM_HOST, config.CONTROL_PORT, {"cmd": "carla_camera", "view": c.camera})
    if c.throttle is not None or c.steer is not None or c.brake is not None:
        send_udp(config.SIM_HOST, config.CONTROL_PORT, {
            "cmd": "carla_control",
            "throttle": c.throttle or 0.0,
            "steer": c.steer or 0.0,
            "brake": c.brake or 0.0,
            "reverse": c.reverse,
            "hand_brake": c.hand_brake
        })
    return {"status": "ok"}


@app.get("/api/carla/stream")
def carla_stream():
    """Proxies the CARLA camera HUD stream from the CARLA bridge on port 50050."""
    try:
        r = requests.get("http://127.0.0.1:50050/stream", stream=True, timeout=2.0)
        return StreamingResponse(
            r.iter_content(chunk_size=1024),
            media_type="multipart/x-mixed-replace; boundary=FRAME"
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="CARLA stream not available. Ensure CARLA and sim.carla_bridge are running."
        )


@app.get("/api/analytics")
def get_analytics(days: int = 28):
    """Returns trip analytics, energy breakdowns, and efficiency trends."""
    conn = get_db()
    since = time.time() - days * 86400
    df_trips = pd.read_sql_query(
        """SELECT date(start_ts,'unixepoch','localtime') day, SUM(distance_km) km,
                  SUM(energy_kwh)*1000/SUM(distance_km) wh_per_km, AVG(avg_ambient_c) ambient_c,
                  AVG(avg_tire_psi) tire_psi
           FROM trips WHERE start_ts > ? AND distance_km > 0 GROUP BY day ORDER BY day""",
        conn, params=(since,)
    )
    if not df_trips.empty:
        df_trips["full_charge_range_km"] = 58000.0 / df_trips["wh_per_km"]

    df_energy = pd.read_sql_query(
        """SELECT road_type,
                  SUM(aero_kwh)*1000/SUM(distance_km) aero,
                  SUM(rolling_kwh)*1000/SUM(distance_km) rolling,
                  SUM(ac_kwh)*1000/SUM(distance_km) ac,
                  SUM(dynamic_kwh+aux_kwh)*1000/SUM(distance_km) dynamic
           FROM trips WHERE start_ts > ? AND distance_km > 0 GROUP BY road_type""",
        conn, params=(since,)
    )
    return {
        "daily": df_trips.to_dict(orient="records"),
        "energy_breakdown": df_energy.to_dict(orient="records")
    }


@app.get("/api/system")
def get_system():
    """Returns process RSS, database storage tier statistics, and collector health."""
    conn = get_db()
    tiers = []
    for t, col, keep in (("signals_1s", "ts", f"{config.RETENTION_SIGNALS_1S_DAYS} days"),
                         ("rollups_1min", "minute", f"{config.RETENTION_ROLLUPS_DAYS} days"),
                         ("trips", "start_ts", f"{config.RETENTION_ROLLUPS_DAYS} days"),
                         ("events", "ts", f"{config.RETENTION_EVENTS_DAYS} days")):
        row = conn.execute(f"SELECT COUNT(*) n, MIN({col}) oldest FROM {t}").fetchone()
        if row:
            n, oldest = row[0], row[1]
            tiers.append({
                "tier": t, "rows": n, "retention": keep,
                "oldest": time.strftime("%d %b %H:%M", time.localtime(oldest)) if oldest else "-"
            })
    db_size_mb = sum(
        os.path.getsize(config.DB_PATH + s) for s in ("", "-wal") if os.path.exists(config.DB_PATH + s)
    ) / 1e6

    collector_status = {}
    st_file = config.DATA_DIR / "collector_status.json"
    if st_file.exists():
        try:
            collector_status = json.loads(st_file.read_text())
            collector_status["alive"] = (time.time() - collector_status.get("updated", 0)) < 5.0
        except Exception:
            pass

    import psutil
    rss_mb = psutil.Process().memory_info().rss / 1e6

    return {
        "db_size_mb": round(db_size_mb, 2),
        "tiers": tiers,
        "rss_mb": round(rss_mb, 1),
        "collector": collector_status,
        "llm_ready": engine.llm.available(),
        "llm_model": engine.llm.model
    }


# ---------------- WebSocket Telemetry Stream (30-60 Hz) ----------------

@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    conn = get_db()
    try:
        while True:
            # Fetch latest 1 Hz sample from SQLite
            df = pd.read_sql_query(
                "SELECT * FROM signals_1s ORDER BY ts DESC LIMIT 1",
                conn
            )
            data = df.iloc[0].to_dict() if not df.empty else {
                "speed_kmh": 0.0, "soc_pct": 88.0, "batt_temp_c": 28.5,
                "power_kw": 0.0, "est_range_km": 345.0, "tire_psi_fl": 36.0,
                "coolant_pump_ok": 1, "power_derate": 0, "adas_active": 0,
                "road_type": "highway", "drive_mode": "normal", "trip_id": 1,
                "ts": time.time()
            }
            data["online"] = (time.time() - data.get("ts", 0)) < 5.0
            await websocket.send_text(json.dumps(data))
            await asyncio.sleep(0.1)  # 10 Hz broadcast
    except WebSocketDisconnect:
        pass


# Mount Web Directory
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>VehicleGPT Web Cockpit Loading...</h1>")


def run():
    port = int(os.getenv("PORT", "8080"))
    print(f"[*] Starting VehicleGPT Futuristic Web Cockpit at http://localhost:{port}")
    uvicorn.run("ui.server:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    run()
