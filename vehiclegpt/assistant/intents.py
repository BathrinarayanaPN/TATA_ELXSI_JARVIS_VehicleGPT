"""Fast rule-based intent router + natural-language time parser (English + common Hindi words).

Common questions are routed here in well under a millisecond; only unusual phrasings go to the LLM router.
"""
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

NUM = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "couple of": 2,
       "a couple of": 2, "few": 3, "a few": 3}


@dataclass
class TimeRange:
    start: float
    end: float
    label: str
    days: float = field(init=False)

    def __post_init__(self):
        self.days = (self.end - self.start) / 86400


@dataclass
class Intent:
    name: str
    args: dict
    route: str = "rules"


def day_range(d: datetime, label):
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return TimeRange(start.timestamp(), (start + timedelta(days=1)).timestamp(), label)


def parse_time(q: str, now: float = None):
    now = now or time.time()
    dnow = datetime.fromtimestamp(now)
    ql = q.lower()
    if re.search(r"\byesterday\b|बीते कल|पिछले कल", ql):
        return day_range(dnow - timedelta(days=1), "yesterday")
    if re.search(r"\btoday\b|\bthis morning\b|आज", ql):
        return day_range(dnow, "today")
    m = re.search(r"(\d{4}-\d{2}-\d{2})", ql)
    if m:
        d = datetime.strptime(m[1], "%Y-%m-%d")
        return day_range(d, d.strftime("%d %b"))
    m = re.search(r"(?:last|past|previous|over the last|in the last|पिछले)\s+(\d+|a couple of|a few|couple of|few|"
                  r"one|two|three|four|five|six|seven)?\s*(day|week|month|दिन|हफ्ते|सप्ताह)", ql)
    if m:
        n = m[1]
        n = int(n) if n and n.isdigit() else NUM.get(n, 1)
        unit = {"day": 1, "दिन": 1, "week": 7, "हफ्ते": 7, "सप्ताह": 7, "month": 30}[m[2]]
        return TimeRange(now - n * unit * 86400, now, f"last {n * unit} days")
    if re.search(r"\bthis week\b", ql):
        start = (dnow - timedelta(days=dnow.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeRange(start.timestamp(), now, "this week")
    return None


EVENT_WORDS = [
    (r"overheat|over-?temp|battery temp|thermal|hot battery|ओवरहीट|गरम", "BATT_OVERTEMP"),
    (r"derate|power limit|limp|reduced power", "POWER_DERATE"),
    (r"tire|tyre|टायर", "TIRE_PRESSURE_LOW"),
    (r"hard brak|sudden brak|emergency brak", "HARD_BRAKE"),
    (r"collision|crash", "COLLISION"),
    (r"anomal", "ANOMALY_BATT_TEMP_RISE"),
    (r"adas|disengag", "ADAS_DISENGAGE"),
    (r"\bdtc\b|fault code|coolant|pump", "DTC"),
]

SIGNALS = [
    (r"battery temp|pack temp|batt temp|battery heat", "batt_temp_c"),
    (r"\bspeed\b|fast", "speed_kmh"),
    (r"\bsoc\b|state of charge|battery level|charge level", "soc_pct"),
    (r"\bpower\b|kw\b", "power_kw"),
    (r"tire|tyre", "tire_psi_fl"),
    (r"outside temp|ambient", "ambient_c"),
    (r"\bac\b|air ?con", "ac_kw"),
    (r"temperature", "batt_temp_c"),
]


def _event_type(ql):
    for pat, et in EVENT_WORDS:
        if re.search(pat, ql):
            return et
    return "ANY_WARNING"


def route(q: str):
    ql = q.lower().strip()
    if re.search(r"\b(before|prior to|leading up|lead(?:s|ing)? (?:up )?to|led to|preceded|precede|"
                 r"what caused|cause of|why did .*(warning|overheat|derate|fault))|से पहले|पहले क्या|ముందు|మునుపు|முன்|முன்பு", ql):
        return Intent("events_before", {"event_type": _event_type(ql)})
    if re.search(r"range|रेंज|రేంజ్|రేంజ్|ரேஞ்ச்", ql) and re.search(r"reduc|drop|decreas|less|lower|fell|shrink|down|worse|why|कम|घट|తగ్గి|తగ్గు|குறை", ql):
        return Intent("range_drop", {})
    if (re.search(r"city|urban|शहर|నగరం|நகரம்", ql) and re.search(r"highway|motorway|freeway|हाईवे|हाइवे|హైవే|நெடுஞ்சாலை", ql)) or \
            re.search(r"compare.*(energy|consumption).*(road|driving)", ql):
        return Intent("city_vs_highway", {})
    if re.search(r"efficien|consumption|mileage|economy|wh/km|kwh/100|खपत|माइलेज|సామర్థ్యం|செயல்திறன்", ql):
        return Intent("efficiency_drop", {})
    if re.search(r"adas|disengag|lane keep|adaptive cruise|\bacc\b|autopilot|एडास|డిస్‌ఎంగేజ్|துண்டிப்பு", ql):
        return Intent("adas_events", {})
    if re.search(r"anomal|unusual|abnormal|strange|weird|odd behavio|असामान्य|వింత|அசாதாரண", ql):
        return Intent("anomalies", {})
    m = re.match(r"^(?:search|find|grep|look up)\s+(?:the\s+)?(?:logs?\s+)?(?:for\s+)?(.+)$", ql)
    if m:
        return Intent("log_search", {"text": m[1].strip(" ?\"'")})
    if re.search(r"\bdtc|fault code|warnings?\b|errors?\b|diagnostic|faults?\b|alerts?\b|check engine|चेतावनी|खराबी", ql):
        return Intent("warnings", {})
    if re.search(r"how far|distance|trips?\b|km did|kilometers did|drove|driven|यात्रा", ql):
        return Intent("trips_summary", {})
    agg = re.search(r"\b(max(?:imum)?|highest|peak|hottest|min(?:imum)?|lowest|average|avg|mean)\b", ql)
    for pat, col in SIGNALS:
        if re.search(pat, ql) and agg:
            a = agg[1]
            a = "max" if a.startswith(("max", "high", "peak", "hot")) else "min" if a.startswith(("min", "low")) else "avg"
            return Intent("signal_stats", {"signal": col, "agg": a})
    if re.search(r"right now|currently|current|status|\bnow\b|live|how is my (car|vehicle)|what'?s happening|"
                 r"\brange\b|battery level|अभी|स्थिति|कैसी", ql):
        return Intent("current_status", {})
    return None
