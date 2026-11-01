"""Client for the on-device language model served by Ollama (llama.cpp under the hood, quantized GGUF).

The model has two narrow jobs:
  1. classify(): map an unusual question to one of the analytics tools (JSON output)
  2. stream():   turn the deterministic facts into a natural explanation (streamed, any language)
It never computes numbers or causes.
"""
import json
import time

import requests

import config

TOOL_SPEC = """Tools:
range_drop            - why driving range changed. args: days
efficiency_drop       - when efficiency/consumption was worst. args: days
adas_events           - ADAS disengagements. args: when (today|yesterday|null), days
events_before         - what happened before an event. args: event_type (BATT_OVERTEMP|POWER_DERATE|TIRE_PRESSURE_LOW|HARD_BRAKE|DTC|ADAS_DISENGAGE|ANY_WARNING)
city_vs_highway       - compare city vs highway energy. args: days
current_status        - live state right now
warnings              - warnings and fault codes. args: days
anomalies             - unusual behaviour found by the anomaly detector. args: days
log_search            - search logs. args: text
trips_summary         - trips and distance. args: days, when
signal_stats          - max/min/avg of a signal. args: signal (batt_temp_c|speed_kmh|soc_pct|power_kw|tire_psi_fl|ambient_c|ac_kw), agg (max|min|avg), days, when
help                  - anything else"""

CLASSIFY_SYSTEM = ("You route vehicle questions to a tool. Reply ONLY with JSON like "
                   '{"tool": "...", "days": null, "when": null, "event_type": null, "signal": null, "agg": null, "text": null}. '
                   "The question may be in English or Hindi.\n" + TOOL_SPEC)

ROLES = {
    "driver": "the car owner. Use plain, friendly language, avoid codes and jargon, give practical advice",
    "service": "a service engineer. Be precise: keep fault codes, timestamps, signal values and root cause",
    "fleet": "a fleet operator. Focus on impact, cost, downtime and what action to schedule",
}
LANGS = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "te": "Telugu (Telugu script)",
    "ta": "Tamil (Tamil script)"
}


class LLM:
    def __init__(self, url=None, model=None, edge=None):
        self.url = url or config.OLLAMA_URL
        self.model = model or config.LLM_MODEL
        self.edge = config.EDGE_MODE if edge is None else edge
        self._ok, self._checked = False, 0.0
        self.last_ttft_ms = self.last_total_ms = None

    def options(self, num_predict):
        o = {"temperature": 0.1, "num_predict": num_predict, "num_ctx": 2048}
        if self.edge:
            o.update(num_thread=2, num_gpu=0)    # emulate a 2-core ARM ECU: CPU only, 2 threads
        return o

    def available(self):
        if time.time() - self._checked < 15:
            return self._ok
        self._checked = time.time()
        try:
            tags = requests.get(f"{self.url}/api/tags", timeout=0.5).json()
            names = [m["name"] for m in tags.get("models", [])]
            self._ok = any(n == self.model or n.startswith(self.model + ":") or n.split(":")[0] == self.model for n in names)
        except Exception:
            self._ok = False
        return self._ok

    def classify(self, question):
        if not self.available():
            return None
        try:
            r = requests.post(f"{self.url}/api/chat", timeout=30, json=dict(
                model=self.model, stream=False, format="json", keep_alive="30m", options=self.options(80),
                messages=[{"role": "system", "content": CLASSIFY_SYSTEM}, {"role": "user", "content": question}]))
            return json.loads(r.json()["message"]["content"])
        except Exception:
            return None

    def stream(self, question, facts_text, role="driver", lang="en", num_predict=220, mode="all"):
        if mode == "simple":
            style_instruction = "Write a clear, concise 1-2 sentence plain-language summary for the driver. Give the core cause and 1 actionable recommendation."
        else:
            style_instruction = "Provide a thorough 3-4 sentence engineering explanation citing exact values and physical causes."

        system = (f"You are VehicleGPT, an offline in-vehicle assistant talking to {ROLES.get(role, ROLES['driver'])}. "
                  f"{style_instruction} Use ONLY numbers and causes that appear in FACTS; "
                  "never invent values. If FACTS say data is missing, say so. "
                  f"Answer strictly in {LANGS.get(lang, 'English')}.")
        user = f"Question: {question}\nFACTS:\n{facts_text}"
        t0 = time.time()
        self.last_ttft_ms = None
        with requests.post(f"{self.url}/api/chat", stream=True, timeout=120, json=dict(
                model=self.model, stream=True, keep_alive="30m", options=self.options(num_predict),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                text = chunk.get("message", {}).get("content", "")
                if text:
                    if self.last_ttft_ms is None:
                        self.last_ttft_ms = (time.time() - t0) * 1000
                    yield text
                if chunk.get("done"):
                    break
        self.last_total_ms = (time.time() - t0) * 1000
