# VehicleGPT: On-Device Conversational AI for Software-Defined Vehicles

[![Python Version](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Simulation](https://img.shields.io/badge/Simulation-CARLA%200.9.16-success.svg)](https://carla.org/)
[![Local LLM](https://img.shields.io/badge/Local%20LLM-Ollama%20%7C%20Qwen2.5--1.5B-orange.svg)](https://ollama.ai/)
[![Demo Video](https://img.shields.io/badge/Demo%20Video-Google%20Drive-red.svg)](https://drive.google.com/drive/folders/1B3jZHPINCD7oXFJ57fBMOBP6nsmdrmzn?usp=sharing)
[![Protocol](https://img.shields.io/badge/Protocols-CAN%20DBC%20%7C%20SOME%2FIP-informational.svg)]()
[![Edge Deployment](https://img.shields.io/badge/Edge%20Profile-2%20Core%20%2F%202%20GB%20RAM-purple.svg)]()
[![Privacy](https://img.shields.io/badge/Data%20Privacy-100%25%20On--Device%20%2F%20Air--Gapped-green.svg)]()

> **Automotive Industry Evaluation & Engineering Demonstration**  
> An edge-native, zero-cloud in-cabin conversational diagnostics assistant for Software-Defined Vehicles (SDVs). VehicleGPT listens to real-time CAN bus frames, SOME/IP services, and diagnostic logs, correlates weeks of telemetry history on-device, and provides natural-language root-cause explanations grounded in deterministic physics.

> [!TIP]
> **Live Engineering Demo Video**:  
> Watch the complete end-to-end CARLA simulation, fault injection, and multilingual Ollama in-cabin assistant demonstration:  
> [**Watch the Demonstration Video on Google Drive**](https://drive.google.com/drive/folders/1B3jZHPINCD7oXFJ57fBMOBP6nsmdrmzn?usp=sharing)

---

## Executive Summary

Modern Software-Defined Vehicles generate gigabytes of telematics, proprietary CAN signals, and diagnostic trouble codes (DTCs). When performance anomalies or subsystem warnings occur, drivers face cryptic dashboard lights, while service teams manually parse dense trace files.

**VehicleGPT** resolves this challenge with an edge-native AI architecture:
* **Zero-Hallucination Design**: The language model never guesses numbers or invents diagnostic causes. Exact numerical values and physical attributions are computed by deterministic SQL and physics tools; the on-device Small Language Model (SLM) is strictly used for natural phrasing and multilingual synthesis.
* **100% On-Device & Air-Gapped**: Powered by **Ollama** running `qwen2.5:1.5b` locally. Sensitive telemetry and driver locations never leave the vehicle.
* **Closed-Loop CARLA Digital Twin**: Integrated with CARLA 0.9.16 (Town04 highway loop and urban street network) with live CAN streaming, camera feeds, and automated safety controllers.
* **Autonomous Safety Supervisor**: Single subsystem faults trigger power derating or advisory warnings while maintaining vehicle cruising; total multi-system cascade failures trigger an immediate ISO 26262 Minimum Risk Condition (MRC) emergency safe stop to 0.0 km/h.
* **Multilingual In-Cabin Voice Assistant**: Instant acoustic alarms, simple driver safety glance summaries, detailed engineering root causes, and voice synthesis across four languages: **English**, **Hindi (हिन्दी)**, **Telugu (తెలుగు)**, and **Tamil (தமிழ்)**.

---

## System Architecture

VehicleGPT employs a strict hardware-isolated safety boundary between the safety-critical vehicle bus and the AI partition.

```
+---------------------------------------------------------------------------------------------------+
|                                  VEHICLE DOMAIN (Untouched / Safety-Critical)                     |
|                                                                                                   |
|   +------------------------------------+          +-------------------------------------------+   |
|   |   CARLA 0.9.16 Digital Twin        |          |   10 Hz Physics Vehicle Node              |   |
|   |   Town04 Autonomous Highway/City   |   OR     |   BMS, Powertrain, HVAC, TPMS             |   |
|   +------------------------------------+          +-------------------------------------------+   |
+---------------------------------------------------------------------------------------------------+
                  |                                        |                             |
      CAN / CAN-FD (DBC Encoded)            SOME/IP Notifications (0x4ADA)      QNX slog2 Diagnostic Logs
                  v                                        v                             v
+---------------------------------------------------------------------------------------------------+
|                                   AI & DIAGNOSTICS PARTITION (Read-Only)                          |
|                                                                                                   |
|   +-------------------------------------------------------------------------------------------+   |
|   |   Ingestion Tier: ReadOnlyBus (No send capability) + tail-follower log subscriber         |   |
|   +-------------------------------------------------------------------------------------------+   |
|                                                  |                                                |
|   +-----------------------------------+          v          +---------------------------------+   |
|   |   Learned-Baseline Anomaly Engine |                     |   Tiered SQLite Storage Engine  |   |
|   |   Lead-time detection before ECUs |                     |   1 Hz detail + 1-min rollups   |   |
|   +-----------------------------------+                     +---------------------------------+   |
|                                                  |                                                |
|   +-------------------------------------------------------------------------------------------+   |
|   |   Deterministic Query Engine: Rule Router (<1 ms) + Physics Decomposition Tools           |   |
|   +-------------------------------------------------------------------------------------------+   |
|                                                  |                                                |
|   +-------------------------------------------------------------------------------------------+   |
|   |   On-Device SLM: Ollama (Qwen2.5-1.5B Q4 via llama.cpp)                                   |   |
|   |   Role adaptation (Driver / Engineer / Fleet) + Multilingual synthesis (EN, HI, TE, TA)   |   |
|   +-------------------------------------------------------------------------------------------+   |
|                                                  |                                                |
|   +-------------------------------------------------------------------------------------------+   |
|   |   Interactive Cockpit UI: WebSocket Telemetry + MJPEG Video Stream + Acoustic Alarm Chime |   |
|   +-------------------------------------------------------------------------------------------+   |
+---------------------------------------------------------------------------------------------------+
```

---

## Query Execution Flow (< 2.0 s Latency Budget)

```
User Query (Text / Voice)
          |
          v
[1. Route (< 1 ms)] --------> Regex/Keyword Intent Router (Rules-first)
                               | Fallback: Ollama JSON classifier (~0.4 s)
          v
[2. Retrieve (< 15 ms)] ----> Indexed SQL execution over SQLite WAL storage tier
                               (1 Hz for 7 days; 1-min rollups for 12 weeks)
          v
[3. Reason (Deterministic)] -> Energy decomposition (Aerodynamic, Rolling, HVAC, Pattern)
                               Precursor timelines and anomaly lead-time calculation
          v
[4. Instant Answer (0 ms)] -> Deterministic truth verified (numbers, units, DTCs)
          v
[5. Explain (Streamed)] ----> Ollama translates verified facts into natural language
                               (English / Hindi / Telugu / Tamil + Driver / Engineer persona)
```

---

## Closed-Loop CARLA Digital Twin & Safety Response

The CARLA bridge interfaces VehicleGPT with CARLA 0.9.16 across highway loops and urban town streets.

| Fault Scenario | Trigger Command | Physical & CAN Impact | CARLA Autonomous Behavior | Cockpit & Chatbot Response |
| :--- | :--- | :--- | :--- | :--- |
| **Coolant Pump Failure** | `coolant_pump` | DTC `P0C73` set; coolant flow 0.0 L/min; thermal gradient climbs | Power derates (-50%); **vehicle continues cruising** safely at ~20 km/h | ⚠️ In-drive warning card; advisory audio chime; driver safety glance |
| **TPMS Tire Puncture** | `tpms_puncture` | DTC `C0035` set; FL tire drops to 18 PSI; rolling drag +45% | Increased tire drag; **vehicle continues cruising** along lane corridor | ⚠️ TPMS warning card; range penalty explanation; maintenance chip |
| **Inverter Overheat** | `inverter` | DTC `P0A0F` set; phase switching temp > 110 deg C | Phase current and torque limited; **vehicle continues cruising** | ⚠️ Inverter thermal warning; cooling recommendation chip |
| **ADAS Sensor Occlusion** | `adas_occlusion` | DTC `U0100` set; optical glare/blockage; SOME/IP disengaged | Autopilot maintains vehicle cruising; optical lane centering standby | ⚠️ ADAS takeover notice; camera radome inspection chip |
| **Extreme Heatwave** | `ambient_heat` | Ambient temp 42 deg C; HVAC compressor spikes to 3.2 kW | Normal cruising speed; auxiliary battery drain accelerates | ☀️ Thermal stress alert; range drop calculation chip |
| **Total System Cascade Failure** | `all_faults` | **All 5 subsystem faults active simultaneously** | **Full Emergency Safe Stop (0.0 km/h)**: Autopilot disengaged, mechanical brakes 1.0, handbrake locked | 🚨 Continuous emergency alarm loop; cascade failure alert; recovery chip |
| **Clear All Faults** | `clear` | All DTCs reset; nominal baselines restored | **Drive Resumed**: Brakes released, autopilot re-engaged, vehicle accelerates to cruising speed | ✅ Drive resumed dialogue; audio confirmation; green status indicators |

---

## Technical Specifications

### 1. In-Vehicle Telemetry Protocols
* **CAN 2.0B / CAN-FD**: Decoded via standard DBC file (`dbc/vehiclegpt_ev.dbc`) across 6 periodic message streams at 10 Hz:
  * `BMS_BatteryStatus` (SOC, pack voltage, current, internal resistance)
  * `BMS_ThermalStatus` (Pack temperature, coolant inlet/outlet temp, coolant pump RPM)
  * `Motor_DriveStatus` (Motor RPM, vehicle speed, drive torque, inverter temperature)
  * `Chassis_TPMS` (Individual 4-wheel pressures, rolling resistance estimates)
  * `HVAC_Status` (Cabin setpoint, ambient temperature, compressor electrical draw)
  * `VCU_EnergyStatus` (Regenerative braking energy, auxiliary power draw, odometer)
* **SOME/IP (Scalable service-Oriented MiddlewarE over IP)**:
  * Service ID: `0x4ADA`, Event ID: `0x8001`, UDP Port `30509`
  * AUTOSAR 4.x header layout transmitting real-time ADAS tracking and disengagement events.
* **Diagnostic Logging**:
  * QNX slog2-style structured diagnostic lines capturing active DTC sets, resets, and collision events.

### 2. Zero-Hallucination Deterministic Engine
* **Rule Router**: Instant keyword and semantic matcher executing in under 1 millisecond.
* **Physics Decomposition Tools**:
  * Aerodynamic drag: $P_{aero} = \frac{1}{2} \rho C_d A v^3$
  * Rolling resistance: $P_{roll} = C_{rr} m g v$ (dynamically penalized during low tire PSI)
  * HVAC compressor auxiliary load: Measured in real-time kW draw against ambient differentials
  * Driver pattern aggressiveness: Quantified by high-frequency throttle/brake jerk metrics

### 3. Edge-Native SLM (Ollama)
* **Model**: Quantized `qwen2.5:1.5b` (GGUF format, ~986 MB storage).
* **Role Adaptation**:
  * **Driver**: Plain language, non-technical guidance, practical range preservation advice.
  * **Service Engineer**: Specific DTC codes, ISO freeze frames, millivolt signal variations, and component schematics.
  * **Fleet Manager**: Operational uptime impacts, battery degradation metrics, and maintenance scheduling.
* **Languages**: Full tokenization and speech synthesis for English, Hindi, Telugu, and Tamil.

---

## Edge Resource Footprint & Benchmarks

All benchmarks measured on standard edge profiles (emulating a 2-Core ARM / 2 GB RAM automotive gateway):

| Metric | Measured Value | Automotive Target | Status |
| :--- | :--- | :--- | :--- |
| **SQL Fact Engine p50 Latency** | 1.8 ms | < 20 ms | PASSED |
| **SQL Fact Engine p95 Latency** | 13.6 ms | < 50 ms | PASSED |
| **Assistant Memory (RSS)** | 39 MB | < 100 MB | PASSED |
| **Collector Memory (RSS)** | 28 MB | < 60 MB | PASSED |
| **Database Storage Footprint** | 5.5 MB (4 weeks of trips + events) | < 50 MB / month | PASSED |
| **Total AI Runtime Footprint** | ~1.1 GB (including Ollama Q4 model) | < 2.0 GB RAM | PASSED |
| **Outbound Internet Traffic** | 0.00 KB (Zero cloud requests) | 0.00 KB | PASSED |

---

## Quick Start & Installation

### Prerequisites
* Windows 10/11, Ubuntu 22.04+, or macOS
* Python 3.10+ (Recommended: Python 3.12)
* [Ollama](https://ollama.ai/) installed on localhost
* CARLA 0.9.16 (Optional, for 3D visual simulation)

### 1. Clone & Set Up Environment
```bash
git clone https://github.com/your-org/vehiclegpt.git
cd vehiclegpt

# Create and activate virtual environment
python -m venv vgpt-env
# On Windows:
vgpt-env\Scripts\activate
# On Linux/macOS:
source vgpt-env/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Pull the On-Device SLM
```bash
ollama serve
ollama pull qwen2.5:1.5b
```

### 3. One-Click Demonstration Launchers (Windows)
* **Full Cockpit & Live Vehicle Simulator**:
  Double-click `run_cockpit.bat` or execute:
  ```bat
  run_cockpit.bat
  ```
  Opens the interactive web cockpit at **`http://localhost:8080`**.

* **CARLA 3D Autonomous Simulation**:
  Ensure CARLA 0.9.16 is launched, then execute:
  ```bat
  run_carla_simulation.bat
  ```

### 4. Manual Component Launch (Any Platform)
```bash
# Terminal 1: Background Collector & Anomaly Engine
python -m collector.collector

# Terminal 2: Live Vehicle Physics Node (or CARLA bridge)
python -m sim.live_simulator
# (Alternative for CARLA: python -m sim.carla_bridge --town Town04)

# Terminal 3: FastAPI Web Cockpit Server
python -m ui.server
```

---

## Evaluation Checklist for Company Judges

| # | Demonstration Milestone | How to Validate | Expected Result |
| :---: | :--- | :--- | :--- |
| **0** | **Complete Video Walkthrough** | [Google Drive Demo Folder](https://drive.google.com/drive/folders/1B3jZHPINCD7oXFJ57fBMOBP6nsmdrmzn?usp=sharing) | Full video demonstration of CARLA autonomy, Ollama multilingual AI, and safety supervisor. |
| **1** | **Live CAN & SOME/IP Streaming** | Open `http://localhost:8080` | Speed dials, battery SOC, and energy stream update in real time at 10-20 Hz. |
| **2** | **Single Fault Safety Decoupling** | Click `❄️ Coolant Pump Failure` | Vehicle slows by 40% but **keeps cruising**; cockpit displays in-drive warning card and audio chime. |
| **3** | **Zero-Hallucination Querying** | Click chip: *"Why is my range dropping?"* | Instant response decomposing exact kWh losses (HVAC, aerodynamic drag, rolling drag). |
| **4** | **Total Cascade Emergency Stop** | Click `💥 Trigger All Faults` | Emergency alarm sounds; CARLA car executes **immediate full stop (0.0 km/h)** with brakes locked. |
| **5** | **Autonomous Recovery** | Click `🟢 Clear All Faults` | Brakes release, autopilot re-engages, and vehicle resumes cruising automatically. |
| **6** | **Multilingual Voice Capabilities** | Select `हिन्दी`, `తెలుగు`, or `தமிழ்` | Chatbot provides native script explanations and audible speech synthesis. |
| **7** | **Air-Gapped Edge Verification** | Disconnect Wi-Fi/Internet | System continues running with zero degradation; all intelligence is 100% on-device. |

---

## Directory Structure

```
vehiclegpt/
├── config.py                 # Central configuration and edge mode switches
├── requirements.txt          # Python runtime dependencies
├── run_cockpit.bat           # Automated Windows launcher for Cockpit & Simulator
├── run_carla_simulation.bat  # Automated CARLA 0.9.16 bridge launcher
├── stop_all.bat              # Clean process termination utility
├── dbc/
│   └── vehiclegpt_ev.dbc     # Standardized CAN database specification
├── common/
│   ├── canbus.py             # Read-only CAN interface (SocketCAN, Virtual, UDP)
│   ├── dbc.py                # Pure-Python DBC bit-unpacking and clamping
│   ├── someip.py             # SOME/IP AUTOSAR 4.x packet serializer/deserializer
│   └── storage.py            # SQLite WAL tiered storage and retention manager
├── sim/
│   ├── vehicle_model.py      # Dual-mass EV physics simulation (BMS, motor, HVAC)
│   ├── driver_model.py       # Longitudinal driver simulation model
│   ├── live_simulator.py     # Standalone 10 Hz telemetry and fault injection node
│   ├── carla_bridge.py       # Closed-loop CARLA 0.9.16 bridge and safety controller
│   └── carla_hud.py          # Automotive vision HUD and lane trajectory overlay
├── collector/
│   ├── collector.py          # Read-only ingestion daemon for CAN, SOME/IP, and logs
│   └── anomaly.py            # Real-time learned-baseline anomaly detection engine
├── assistant/
│   ├── engine.py             # Hybrid query router and zero-hallucination fact pipeline
│   ├── intents.py            # Deterministic intent classification rules
│   ├── tools.py              # Physics cause attribution and SQL analytical tools
│   ├── llm.py                # Ollama SLM client and prompt orchestration
│   └── summaries.py          # Multilingual dual-layer summary generation
└── ui/
    ├── server.py             # FastAPI telemetry server, WebSocket, and MJPEG stream
    └── web/                  # Web Cockpit frontend
        ├── index.html        # Automotive HUD display layout
        ├── style.css         # Dark theme automotive UI styles
        └── app.js            # WebSocket client, audio alarms, and speech synthesis
```

---

## Safety, Privacy & Ethical Compliance

1. **Hardware-Isolated Bus Security**: The `ReadOnlyBus` implementation completely lacks a `send()` primitive. Even in the event of an arbitrary code execution vulnerability in the AI layer, no malicious frames can be injected into the physical CAN network.
2. **ASIL / ISO 26262 Alignment**: All autonomous vehicle state modifications (emergency halts, derates) are executed by deterministic supervisory controllers with hardcoded constraints, never by probabilistic neural networks.
3. **GDPR / DPDP Compliance**: Vehicle telemetry, diagnostic traces, and passenger speech queries are processed entirely inside volatile edge memory and local encrypted SQLite databases. No telemetry is exported to third-party cloud infrastructure.

---

## License & Attribution

Developed for automotive engineering evaluations, hackathons, and software-defined vehicle research demonstrations.  
Designed and maintained with a focus on real-time deterministic reliability and edge AI efficiency.
