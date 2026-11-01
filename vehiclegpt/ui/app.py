"""VehicleGPT cockpit UI.   streamlit run ui/app.py

Tabs: Live dashboard | Assistant (text + voice, multilingual, role-aware) | Scenarios (fault injection)
      | Analytics | System (memory, latency, storage tiers, safety boundary)
"""
import hashlib
import json
import os
import socket
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

import config
from assistant.engine import Engine
from common.someip import ADAS_REASONS

st.set_page_config(page_title="Tata Elxsi SDV · VehicleGPT", page_icon="🚗", layout="wide")

st.markdown("""
<style>
    /* Tata Elxsi SDV Dark Cyber Cockpit Theme */
    .stApp {
        background: radial-gradient(circle at 10% 20%, #080B10 0%, #030508 100%);
        color: #F0F4F8;
    }
    .stMetric {
        background: rgba(13, 17, 26, 0.85);
        border: 1px solid rgba(0, 242, 254, 0.25);
        border-radius: 10px;
        padding: 10px 14px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
    }
    .stMetric label {
        color: #8E99A8 !important;
        font-size: 0.75rem !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .stMetric [data-testid="stMetricValue"] {
        color: #00F2FE !important;
        font-family: monospace, sans-serif !important;
        font-weight: 700;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent;
        border-radius: 8px 8px 0 0;
        color: #8E99A8;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(0, 242, 254, 0.1) !important;
        color: #00F2FE !important;
        border-bottom: 2px solid #00F2FE !important;
    }
</style>
""", unsafe_allow_html=True)

SAMPLES = [
    "Why has my driving range reduced over the last two weeks?",
    "When did fuel efficiency show the largest drop?",
    "Show all ADAS disengagement events from yesterday.",
    "What happened before the battery overheating warning?",
    "Compare energy consumption between city and highway driving.",
    "What's happening right now?",
    "Any anomalies in the last month?",
    "मेरी रेंज क्यों कम हो गई?",
]
LANG_CODES = {"English": "en", "हिन्दी (Hindi)": "hi", "తెలుగు (Telugu)": "te", "தமிழ் (Tamil)": "ta"}
ROLE_CODES = {"Driver": "driver", "Service engineer": "service", "Fleet operator": "fleet"}


@st.cache_resource
def get_engine():
    return Engine()


engine = get_engine()
ss = st.session_state
ss.setdefault("messages", [])
ss.setdefault("latency", [])
ss.setdefault("pending", None)
ss.setdefault("last_audio", None)


def q(sql, args=()):
    try:
        return pd.read_sql_query(sql, engine.conn, params=args)
    except Exception:
        return pd.DataFrame()


def send_control(cmd):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(json.dumps(cmd).encode(), (config.SIM_HOST, config.CONTROL_PORT))
    sock.close()
    st.toast(f"Sent to vehicle simulator: {cmd}")


# ---------------- sidebar ----------------
with st.sidebar:
    st.title("🚗 VehicleGPT")
    st.caption("On-device vehicle assistant · no cloud")
    role = ROLE_CODES[st.radio("Who is asking?", list(ROLE_CODES), horizontal=False)]
    lang = LANG_CODES[st.selectbox("Answer language", list(LANG_CODES))]
    use_llm = st.toggle("Natural-language explanation (on-device LLM)", value=True)
    engine.llm.edge = st.toggle("Edge mode: CPU only, 2 threads", value=config.EDGE_MODE,
                                help="Emulates a 2-core ARM ECU for honest latency numbers")
    llm_ok = engine.llm.available()
    st.markdown(f"**LLM:** `{engine.llm.model}` {'🟢 ready' if llm_ok else '🔴 not running'}")
    if not llm_ok:
        st.caption(f"Start Ollama and run `ollama pull {engine.llm.model}`. Answers still work using the instant fact engine.")
    st.divider()
    st.caption("Safety: the AI side is read-only. The collector has no send path to CAN/SOME-IP; "
               "fault buttons talk only to the simulator test harness.")

tab_live, tab_chat, tab_scen, tab_an, tab_sys = st.tabs(
    ["🚗 Live", "💬 Assistant", "⚡ Scenarios", "📊 Analytics", "🖥 System"])


# ---------------- live ----------------
with tab_live:
    @st.fragment(run_every=1.0)
    def live_panel():
        df = q("SELECT * FROM signals_1s WHERE ts > (SELECT MAX(ts) FROM signals_1s) - 300 ORDER BY ts")
        if df.empty:
            st.info("No data yet. Run the history seeder, then start the collector and simulator.")
            return
        r = df.iloc[-1]
        age = time.time() - r.ts
        if age < 10:
            st.success(f"● LIVE · trip {int(r.trip_id)} · {r.road_type} · {r.drive_mode} driving")
        else:
            st.warning(f"Not streaming. Showing last stored sample from {time.strftime('%d %b %H:%M:%S', time.localtime(r.ts))}. "
                       "Start the collector and simulator (run_demo.bat).")
        prev = df[df.ts <= r.ts - 60]
        dtemp = (r.batt_temp_c - prev.iloc[-1].batt_temp_c) if not prev.empty else 0.0
        c = st.columns(8)
        c[0].metric("Speed", f"{r.speed_kmh:.0f} km/h")
        c[1].metric("Battery", f"{r.soc_pct:.1f} %")
        c[2].metric("Range", f"{r.est_range_km:.0f} km")
        c[3].metric("Pack temp", f"{r.batt_temp_c:.1f} °C", f"{dtemp:+.1f} °C/min", delta_color="inverse")
        c[4].metric("Power", f"{r.power_kw:.1f} kW")
        c[5].metric("Tire FL", f"{r.tire_psi_fl:.1f} psi")
        c[6].metric("AC", f"{r.ac_kw:.1f} kW")
        c[7].metric("ADAS", "ENGAGED" if r.adas_active else "off")
        if not r.coolant_pump_ok:
            st.error("Battery coolant pump NOT RUNNING")
        if r.power_derate:
            st.error("Propulsion power derated to protect the battery")

        # CARLA Stream and Cockpit link
        s_col1, s_col2 = st.columns([1.4, 1.0])
        with s_col1:
            st.caption("🏎️ CARLA 0.9.16 Live Camera HUD Stream (Port 50050)")
            st.markdown('<img src="http://localhost:50050/stream" style="width:100%; border-radius:8px; border:1px solid rgba(0,242,254,0.3);" onerror="this.style.display=\'none\';">', unsafe_allow_html=True)
        with s_col2:
            st.caption("🌐 Next-Generation Cockpit")
            st.info("The futuristic HTML5/Canvas Web Cockpit is active at **http://localhost:8080** with live telemetry, 3D dials, and full CAN sniffer.")
            st.link_button("🚀 Open Next-Gen Cockpit UI", "http://localhost:8080", use_container_width=True)
            if st.button("⚡ Toggle CARLA Autopilot", use_container_width=True):
                send_control({"cmd": "carla_autopilot", "value": not bool(r.adas_active)})
            if st.button("📷 Cycle CARLA Camera", use_container_width=True):
                send_control({"cmd": "carla_camera", "view": "cockpit"})

        df["time"] = pd.to_datetime(df.ts, unit="s")
        df = df.set_index("time")
        a, b, cc = st.columns(3)
        a.caption("Speed (km/h)")
        a.line_chart(df[["speed_kmh"]], height=200)
        b.caption("Battery temperature (°C)")
        b.line_chart(df[["batt_temp_c"]], height=200)
        cc.caption("Battery power (kW)")
        cc.line_chart(df[["power_kw"]], height=200)
        ev = q("""SELECT datetime(ts,'unixepoch','localtime') time, event_type, severity, source, description
                  FROM events WHERE ts > ? ORDER BY ts DESC LIMIT 12""", (r.ts - 1800,))
        st.caption("Events in the last 30 minutes")
        if ev.empty:
            st.caption("— none —")
        else:
            st.dataframe(ev, hide_index=True)

    live_panel()


# ---------------- assistant ----------------
def render_answer(res, explanation=None):
    st.markdown(f"**{res['title']}**")
    if explanation:
        st.markdown(explanation)
        with st.expander("Exact figures from the fact engine"):
            st.markdown(res["text"].replace("\n", "  \n"))
    else:
        st.markdown(res["text"].replace("\n", "  \n"))
    t = res["timings"]
    badge = f"⚡ facts in {t['total_ms']:.0f} ms · route: {res['route']} · tool: {res['intent']}"
    if res.get("llm_ttft_ms"):
        badge += f" · LLM first words {res['llm_ttft_ms']:.0f} ms, full {res['llm_total_ms']:.0f} ms"
    st.caption(badge)
    ch = res.get("chart")
    if ch and ch.get("data"):
        df = pd.DataFrame(ch["data"]).set_index(ch["x"])
        if ch["kind"] == "line" and len(ch["y"]) > 1:
            l, rgt = st.columns(2)
            l.line_chart(df[[ch["y"][0]]], height=220)
            rgt.line_chart(df[ch["y"][1:]], height=220)
        elif ch["kind"] == "line":
            st.line_chart(df[ch["y"]], height=220)
        else:
            st.bar_chart(df[ch["y"]], height=220)
    if res.get("table"):
        st.dataframe(pd.DataFrame(res["table"]), hide_index=True)
    if res.get("evidence"):
        with st.expander("Evidence"):
            for e in res["evidence"]:
                st.write("•", e)


@st.cache_resource
def get_whisper():
    from faster_whisper import WhisperModel
    return WhisperModel("tiny", device="cpu", compute_type="int8")


with tab_chat:
    st.caption("Ask about range, efficiency, ADAS, warnings, what happened before an event, live status… in English or Hindi.")
    cols = st.columns(4)
    for i, s in enumerate(SAMPLES):
        if cols[i % 4].button(s, key=f"chip{i}", use_container_width=True):
            ss.pending = s
    if hasattr(st, "audio_input"):
        audio = st.audio_input("🎤 Ask by voice (offline speech recognition)")
        if audio is not None:
            data = audio.getvalue()
            h = hashlib.md5(data).hexdigest()
            if h != ss.last_audio:
                ss.last_audio = h
                try:
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        f.write(data)
                    segments, _ = get_whisper().transcribe(f.name, beam_size=1)
                    text = " ".join(s.text for s in segments).strip()
                    os.unlink(f.name)
                    if text:
                        ss.pending = text
                except ImportError:
                    st.warning("Voice needs `pip install faster-whisper` (runs offline after the first model download).")
                except Exception as e:
                    st.warning(f"Speech recognition failed: {e}")

    for m in ss.messages:
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.write(m["content"])
            else:
                render_answer(m["result"], m.get("explanation"))

    typed = st.chat_input("Ask VehicleGPT…")
    question = typed or ss.pending
    ss.pending = None
    if question:
        ss.messages.append(dict(role="user", content=question))
        with st.chat_message("user"):
            st.write(question)
        with st.chat_message("assistant"):
            res = engine.ask(question)
            answer_lang = "hi" if res["lang"] == "hi" else lang
            explanation = None
            if use_llm and engine.llm.available():
                st.markdown(f"**{res['title']}**")
                try:
                    explanation = st.write_stream(engine.explain(res, role=role, lang=answer_lang))
                    res["llm_ttft_ms"], res["llm_total_ms"] = engine.llm.last_ttft_ms, engine.llm.last_total_ms
                except Exception as e:
                    st.warning(f"LLM explanation unavailable ({e}); showing fact-engine answer.")
                    explanation = None
                st.empty()
            elif answer_lang != "en":
                st.info("Answers in Hindi/Telugu/Tamil need the on-device LLM (Ollama). Showing English facts.")
            ss.latency.append(dict(facts_ms=res["timings"]["total_ms"], ttft_ms=res.get("llm_ttft_ms"),
                                   llm_total_ms=res.get("llm_total_ms"), route=res["route"]))
            ss.messages.append(dict(role="assistant", result=res, explanation=explanation))
            st.rerun()


# ---------------- scenarios ----------------
with tab_scen:
    st.subheader("Live fault injection")
    st.caption("Recreate driving scenarios and error sequences on the running vehicle, then ask the assistant about them. "
               "Commands go to the simulator / CARLA bridge test harness only.")
    st.info("Demo flow: inject the coolant pump failure → watch pack temperature climb on the Live tab → "
            "the AI anomaly alert fires before the BMS warning → ask “What happened before the battery overheating warning?”")
    a, b, c = st.columns(3)
    with a:
        st.markdown("**Faults**")
        if st.button("🔥 Coolant pump failure", use_container_width=True):
            send_control({"cmd": "fault", "name": "coolant_pump", "accel": 9.0})
        if st.button("🛞 Slow tire leak (front-left)", use_container_width=True):
            send_control({"cmd": "tire_leak"})
        if st.button("✅ Clear all faults", use_container_width=True):
            send_control({"cmd": "clear"})
    with b:
        st.markdown("**ADAS**")
        reason = st.selectbox("Disengagement reason", [r for r in ADAS_REASONS.values() if r != "ENGAGED"])
        if st.button("🚫 Force ADAS disengagement", use_container_width=True):
            send_control({"cmd": "adas_disengage", "reason": reason})
    with c:
        st.markdown("**Driving context**")
        road = st.radio("Road", ["auto", "city", "highway"], horizontal=True)
        style = st.radio("Driving style", ["normal", "aggressive"], horizontal=True)
        ac = st.slider("AC load (kW)", 0.0, 4.0, 2.0, 0.1)
        if st.button("Apply driving context", use_container_width=True):
            send_control({"cmd": "road", "value": road})
            send_control({"cmd": "style", "value": style})
            send_control({"cmd": "ac", "value": ac})


# ---------------- analytics ----------------
with tab_an:
    days = st.slider("Period (days)", 7, 84, 28)
    since = time.time() - days * 86400
    d = q("""SELECT date(start_ts,'unixepoch','localtime') day, SUM(distance_km) km,
             SUM(energy_kwh)*1000/SUM(distance_km) wh_per_km, AVG(avg_ambient_c) ambient_c, AVG(avg_tire_psi) tire_psi
             FROM trips WHERE start_ts > ? AND distance_km > 0 GROUP BY day ORDER BY day""", (since,))
    if not d.empty:
        d["full_charge_range_km"] = 58000 / d.wh_per_km
        d = d.set_index("day")
        l, r = st.columns(2)
        l.caption("Energy use per day (Wh/km)")
        l.bar_chart(d[["wh_per_km"]], height=250)
        r.caption("Estimated full-charge range (km)")
        r.line_chart(d[["full_charge_range_km"]], height=250)
        l.caption("Outside temperature (°C)")
        l.line_chart(d[["ambient_c"]], height=200)
        r.caption("Front-left tire pressure (psi)")
        r.line_chart(d[["tire_psi"]], height=200)
    cv = q("""SELECT road_type, SUM(aero_kwh)*1000/SUM(distance_km) aero, SUM(rolling_kwh)*1000/SUM(distance_km) rolling,
              SUM(ac_kwh)*1000/SUM(distance_km) ac, SUM(dynamic_kwh+aux_kwh)*1000/SUM(distance_km) accel_idle
              FROM trips WHERE start_ts > ? AND distance_km > 0 GROUP BY road_type""", (since,))
    if not cv.empty:
        st.caption("Where the energy goes: city vs highway (Wh/km)")
        st.bar_chart(cv.set_index("road_type"), height=250)
    ev = q("""SELECT date(ts,'unixepoch','localtime') day, event_type, COUNT(*) n FROM events
              WHERE ts > ? AND event_type != 'ADAS_ENGAGE' GROUP BY day, event_type""", (since,))
    if not ev.empty:
        st.caption("Events per day")
        st.bar_chart(ev.pivot(index="day", columns="event_type", values="n").fillna(0), height=250)
    st.caption("Recent trips")
    st.dataframe(q("""SELECT trip_id, datetime(start_ts,'unixepoch','localtime') start, road_type, drive_mode,
                      ROUND(distance_km,1) km, ROUND(wh_per_km) wh_per_km, ROUND(avg_speed_kmh) avg_kmh,
                      ROUND(max_batt_temp_c,1) max_batt_c FROM trips ORDER BY start_ts DESC LIMIT 20"""), hide_index=True)


# ---------------- system ----------------
with tab_sys:
    l, r = st.columns(2)
    with l:
        st.subheader("Storage tiers")
        tiers = []
        for t, col, keep in (("signals_1s", "ts", f"{config.RETENTION_SIGNALS_1S_DAYS} days"),
                             ("rollups_1min", "minute", f"{config.RETENTION_ROLLUPS_DAYS} days"),
                             ("trips", "start_ts", f"{config.RETENTION_ROLLUPS_DAYS} days"),
                             ("events", "ts", f"{config.RETENTION_EVENTS_DAYS} days")):
            row = q(f"SELECT COUNT(*) n, MIN({col}) oldest FROM {t}")
            if not row.empty:
                n, oldest = int(row.n[0]), row.oldest[0]
                tiers.append(dict(tier=t, rows=n, retention=keep,
                                  oldest=time.strftime("%d %b %H:%M", time.localtime(oldest)) if oldest else "-"))
        st.dataframe(pd.DataFrame(tiers), hide_index=True)
        size = sum(os.path.getsize(config.DB_PATH + s) for s in ("", "-wal") if os.path.exists(config.DB_PATH + s))
        st.metric("Database size", f"{size / 1e6:.1f} MB")
    with r:
        st.subheader("Processes")
        try:
            import psutil
            st.metric("UI + assistant RSS", f"{psutil.Process().memory_info().rss / 1e6:.0f} MB")
        except ImportError:
            pass
        status_file = config.DATA_DIR / "collector_status.json"
        if status_file.exists():
            s = json.loads(status_file.read_text())
            alive = time.time() - s["updated"] < 5
            st.metric("Collector", "running" if alive else "stopped",
                      f"{s['frames_per_s']} CAN frames/s · {s.get('rss_mb') or 0:.0f} MB RSS" if alive else None)
            st.caption(f"Backend {s['can_backend']} · CAN frames {s['can_frames']:,} · SOME/IP msgs {s['someip_msgs']} · "
                       f"log lines {s['log_lines']}")
        else:
            st.metric("Collector", "not started")
    st.subheader("Latency this session")
    if ss.latency:
        lat = pd.DataFrame(ss.latency)
        f = lat.facts_ms.dropna().tolist()
        c = st.columns(4)
        c[0].metric("Fact engine p50", f"{statistics.median(f):.0f} ms")
        c[1].metric("Fact engine max", f"{max(f):.0f} ms")
        tt = lat.ttft_ms.dropna().tolist()
        if tt:
            c[2].metric("LLM first words p50", f"{statistics.median(tt):.0f} ms")
            c[3].metric("LLM full answer p50", f"{statistics.median(lat.llm_total_ms.dropna()):.0f} ms")
        st.dataframe(lat, hide_index=True)
    else:
        st.caption("Ask some questions to collect latency numbers.")
    st.subheader("Safety boundary")
    st.markdown("- Collector receives through a `ReadOnlyBus` with **no send method**; SOME/IP is subscribe-only; logs are tailed read-only.\n"
                "- The assistant opens the database **read-only**; the LLM only rewrites facts and cannot call vehicle functions.\n"
                "- On a vehicle HPC the AI stack runs in its own VM/container partition (hypervisor), separate from ASIL safety ECUs.\n"
                "- All processing is local: no network calls except to the on-device LLM server on localhost.")
