"""VehicleGPT query engine.

  question -> (1) rule router <1 ms  | (2) LLM router (only if rules miss)
           -> (3) deterministic SQL/physics tool  (all numbers + causes)
           -> (4) instant template answer  ...and optionally  (5) streamed LLM explanation in any language/role

The template answer guarantees a correct response well under 2 s even on a Raspberry Pi with no LLM loaded;
the LLM adds natural language, multilingual output and role-specific tone on top.
"""
import re
import time

from assistant import intents, tools
from assistant.llm import LLM
from assistant.summaries import generate_summaries
from common import storage

DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class Engine:
    def __init__(self, db_path=None, llm=None):
        self.db_path = db_path
        self.llm = llm or LLM()
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            try:
                self._conn = storage.connect(self.db_path, readonly=True)   # the assistant can only read
                self._conn.execute("SELECT 1 FROM signals_1s LIMIT 1")
            except Exception:
                self._conn = storage.connect(self.db_path)
        return self._conn

    def _llm_route(self, question):
        j = self.llm.classify(question)
        if not j or j.get("tool") not in tools.TOOLS:
            return None
        args = {}
        if j.get("tool") == "events_before":
            args["event_type"] = j.get("event_type") or "ANY_WARNING"
        if j.get("tool") == "signal_stats":
            args.update(signal=j.get("signal") or "batt_temp_c", agg=j.get("agg") or "max")
        if j.get("tool") == "log_search":
            args["text"] = j.get("text") or question
        return intents.Intent(j["tool"], args, route="llm"), j

    def ask(self, question, use_llm_router=True):
        t0 = time.perf_counter()
        lang = "hi" if DEVANAGARI.search(question) else "en"
        intent = intents.route(question)
        llm_json = None
        if intent is None and use_llm_router:
            routed = self._llm_route(question)
            if routed:
                intent, llm_json = routed
        if intent is None:
            intent = intents.Intent("help", {}, route="fallback")
        tr = intents.parse_time(question)
        if tr is None and llm_json:
            if llm_json.get("when") in ("today", "yesterday"):
                tr = intents.parse_time(llm_json["when"])
            elif isinstance(llm_json.get("days"), (int, float)) and llm_json["days"] > 0:
                tr = intents.parse_time(f"last {int(llm_json['days'])} days")
        t_route = time.perf_counter()
        result = tools.TOOLS[intent.name](self.conn, tr=tr, **intent.args)
        t_end = time.perf_counter()
        sums = generate_summaries(intent.name, result.get("facts", {}), result.get("text", ""))
        result.update(question=question, route=intent.route, lang=lang, time_range=tr.label if tr else None,
                      summaries=sums, simple_text=sums["simple"].get(lang, sums["simple"]["en"]),
                      timings=dict(route_ms=(t_route - t0) * 1000, query_ms=(t_end - t_route) * 1000,
                                   total_ms=(t_end - t0) * 1000))
        return result

    def explain(self, result, role="driver", lang=None, mode="all"):
        """Generator of LLM text chunks. Yields nothing if the on-device model is unavailable."""
        if not self.llm.available():
            return
        yield from self.llm.stream(result["question"], result["text"], role=role, lang=lang or result.get("lang", "en"), mode=mode)


if __name__ == "__main__":
    import sys
    e = Engine()
    q = " ".join(sys.argv[1:]) or "Why has my driving range reduced over the last two weeks?"
    r = e.ask(q)
    print(f"[{r['intent']} via {r['route']}, {r['timings']['total_ms']:.1f} ms]\n{r['text']}")
    for chunk in e.explain(r):
        print(chunk, end="", flush=True)
    print()
