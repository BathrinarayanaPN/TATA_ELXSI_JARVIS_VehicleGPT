"""End-to-end latency + memory benchmark.

  python benchmark.py               # fact engine on all PDF queries (+ LLM explanation if Ollama is running)
  python benchmark.py --edge        # LLM forced to CPU-only, 2 threads (emulates a 2-core ARM ECU)
  docker run --cpus=2 --memory=2g ...  for a hard resource cap (see docker/README section in README.md)

Writes results to benchmark_results.md.
"""
import argparse
import os
import platform
import statistics
import time

import config
from assistant.engine import Engine

QUERIES = [
    "Why has my driving range reduced over the last two weeks?",
    "When did fuel efficiency show the largest drop?",
    "Show all ADAS disengagement events from yesterday.",
    "What happened before the battery overheating warning?",
    "Compare energy consumption between city and highway driving.",
    "What's happening right now?",
    "What was the highest battery temperature yesterday?",
]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def rss_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except ImportError:
        return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--llm-runs", type=int, default=3)
    ap.add_argument("--edge", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    e = Engine()
    e.llm.edge = args.edge or config.EDGE_MODE
    lines = [f"# VehicleGPT benchmark - {time.strftime('%Y-%m-%d %H:%M')}",
             f"Host: {platform.machine()} {platform.processor() or ''} · {os.cpu_count()} CPUs · Python {platform.python_version()}",
             f"Model: {e.llm.model} · edge mode (CPU only, 2 threads): {e.llm.edge}", "",
             "## Fact engine (router + SQL + cause attribution)", "",
             "| Query | tool | p50 ms | p95 ms |", "|---|---|---|---|"]
    for q in QUERIES:
        times = []
        for _ in range(args.runs):
            r = e.ask(q, use_llm_router=False)
            times.append(r["timings"]["total_ms"])
        lines.append(f"| {q} | {r['intent']} | {statistics.median(times):.1f} | {pct(times, 95):.1f} |")
        print(lines[-1])

    if not args.no_llm and e.llm.available():
        lines += ["", "## On-device LLM explanation (end to end: facts + streamed answer)", "",
                  "| Query | first words p50 ms | full answer p50 ms | answer under 2 s |", "|---|---|---|---|"]
        list(e.explain(e.ask(QUERIES[0])))  # warm-up / load model into memory
        for q in QUERIES:
            ttft, total = [], []
            for _ in range(args.llm_runs):
                r = e.ask(q, use_llm_router=False)
                list(e.explain(r))
                ttft.append(r["timings"]["total_ms"] + e.llm.last_ttft_ms)
                total.append(r["timings"]["total_ms"] + e.llm.last_total_ms)
            lines.append(f"| {q} | {statistics.median(ttft):.0f} | {statistics.median(total):.0f} | "
                         f"{'yes' if statistics.median(ttft) < 2000 else 'no'} (first words) |")
            print(lines[-1])
    else:
        lines += ["", "_LLM not running: only the fact engine was measured._"]

    db = sum(os.path.getsize(config.DB_PATH + s) for s in ("", "-wal") if os.path.exists(config.DB_PATH + s))
    lines += ["", "## Footprint", "", f"- Assistant process RSS: {rss_mb():.0f} MB",
              f"- Database ({config.RETENTION_SIGNALS_1S_DAYS} d at 1 Hz + {config.RETENTION_ROLLUPS_DAYS} d rollups): {db / 1e6:.1f} MB",
              "- LLM memory: see `ollama ps` (qwen2.5:1.5b Q4 is roughly 1-1.3 GB)"]
    open("benchmark_results.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[-4:]))
    print("\nSaved benchmark_results.md")


if __name__ == "__main__":
    main()
