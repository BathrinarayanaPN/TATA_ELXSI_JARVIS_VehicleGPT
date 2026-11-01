"""Core tests.  python -m pytest tests -v   (needs a seeded DB: python -m sim.seed_history)"""
import struct

import config
from assistant import intents
from assistant.engine import Engine
from common import someip
from common.dbc import Database


def test_dbc_roundtrip():
    db = Database(config.DBC_PATH)
    fid, data = db.encode("BMS_Thermal", dict(BattTemp=-12.34, CoolantPumpOK=1, OverTempWarn=0, CoolantPumpDuty=55, AmbientTemp=31.2))
    name, vals = db.decode(fid, data)
    assert name == "BMS_Thermal" and abs(vals["BattTemp"] + 12.34) < 0.006 and vals["CoolantPumpOK"] == 1
    assert abs(vals["AmbientTemp"] - 31.2) < 0.06 and vals["CoolantPumpDuty"] == 55


def test_dbc_clamps_out_of_range():
    db = Database(config.DBC_PATH)
    fid, data = db.encode("VCU_Speed", dict(VehicleSpeed=9999, LongAccel=-3.2, Odometer=12345.678))
    _, vals = db.decode(fid, data)
    assert vals["VehicleSpeed"] <= 655.35 and abs(vals["LongAccel"] + 3.2) < 0.001


def test_someip_header_and_adas_payload():
    pkt = someip.pack_adas_event(False, "CAMERA_BLOCKED_GLARE", 101.5, session_id=7)
    sid, eid, length = struct.unpack_from(">HHI", pkt)
    assert (sid, eid) == (someip.ADAS_SERVICE_ID, someip.ADAS_STATE_EVENT_ID) and length == len(pkt) - 8
    msg = someip.unpack(pkt)
    assert msg["message_type"] == someip.MSG_NOTIFICATION and msg["session_id"] == 7
    ev = someip.unpack_adas_event(msg["payload"])
    assert ev["reason"] == "CAMERA_BLOCKED_GLARE" and not ev["engaged"] and abs(ev["speed_kmh"] - 101.5) < 0.01


def test_collector_bus_is_read_only():
    from common.canbus import ReadOnlyBus
    assert not hasattr(ReadOnlyBus, "send")


def test_router_pdf_queries():
    cases = {
        "Why has my driving range reduced over the last two weeks?": "range_drop",
        "When did fuel efficiency show the largest drop?": "efficiency_drop",
        "Show all ADAS disengagement events from yesterday.": "adas_events",
        "What happened before the battery overheating warning?": "events_before",
        "Compare energy consumption between city and highway driving.": "city_vs_highway",
        "मेरी रेंज क्यों कम हो गई?": "range_drop",
    }
    for q, want in cases.items():
        assert intents.route(q).name == want, q


def test_time_parser():
    assert intents.parse_time("from yesterday").label == "yesterday"
    assert intents.parse_time("over the last two weeks").label == "last 14 days"
    assert intents.parse_time("past 3 days").label == "last 3 days"


def test_answers_contain_injected_scenarios():
    e = Engine()
    r = e.ask("What happened before the battery overheating warning?", use_llm_router=False)
    assert "coolant pump" in r["text"] and r["facts"]["ai_anomaly_lead_min"] > 0
    r = e.ask("Why has my driving range reduced over the last two weeks?", use_llm_router=False)
    assert r["facts"]["range_now_km"] < r["facts"]["range_before_km"]
    assert any("tire" in c["cause"] for c in r["facts"]["causes"])
    r = e.ask("Show all ADAS disengagement events from yesterday.", use_llm_router=False)
    assert r["facts"]["unplanned"] >= 5


def test_latency_under_budget():
    e = Engine()
    for q in ["Why has my driving range reduced over the last two weeks?",
              "What happened before the battery overheating warning?"]:
        assert e.ask(q, use_llm_router=False)["timings"]["total_ms"] < 2000


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
