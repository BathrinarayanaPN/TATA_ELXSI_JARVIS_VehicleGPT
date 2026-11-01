"""Central configuration. Every value can be overridden with an environment variable."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("VGPT_DATA", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = os.getenv("VGPT_DB", str(DATA_DIR / "vehicle.db"))
DIAG_LOG = Path(os.getenv("VGPT_DIAG_LOG", LOG_DIR / "slog2_diag.log"))
DBC_PATH = ROOT / "dbc" / "vehiclegpt_ev.dbc"

# ---- Vehicle bus transport ----
# builtin-udp : CAN frames over localhost UDP (works on Windows/Mac/Linux, no drivers)
# socketcan   : real Linux SocketCAN (vcan0 / can0) via python-can   -> Raspberry Pi, vehicle HPC
# udp_multicast / virtual / pcan / kvaser ... : any python-can interface
CAN_BACKEND = os.getenv("VGPT_CAN", "builtin-udp")
CAN_CHANNEL = os.getenv("VGPT_CAN_CHANNEL", "vcan0")
CAN_UDP_PORT = int(os.getenv("VGPT_CAN_PORT", "50100"))

COLLECTOR_HOST = os.getenv("VGPT_COLLECTOR_HOST", "127.0.0.1")  # where sim sends CAN/SOME-IP
SIM_HOST = os.getenv("VGPT_SIM_HOST", "127.0.0.1")              # where UI sends fault commands
SOMEIP_PORT = int(os.getenv("VGPT_SOMEIP_PORT", "30509"))
CONTROL_PORT = int(os.getenv("VGPT_CONTROL_PORT", "50020"))

# ---- On-device language model (Ollama) ----
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("VGPT_MODEL", "qwen2.5:1.5b")
# Edge mode forces CPU-only inference with 2 threads to mimic a 2-core ARM ECU.
EDGE_MODE = os.getenv("VGPT_EDGE", "0") == "1"

# ---- Storage tiers (days) ----
RETENTION_SIGNALS_1S_DAYS = int(os.getenv("VGPT_RET_SIGNALS", "7"))
RETENTION_ROLLUPS_DAYS = int(os.getenv("VGPT_RET_ROLLUPS", "84"))     # 12 weeks
RETENTION_EVENTS_DAYS = int(os.getenv("VGPT_RET_EVENTS", "180"))      # 6 months
