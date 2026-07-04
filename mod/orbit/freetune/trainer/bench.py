"""Benchmark a model (optionally + a freetuned adapter) over example coding tasks.

Measures the per-task usage the efficiency dashboard shows: tokens generated,
latency, tokens/sec, peak RSS. Writes <out> as JSON and also prints it.

    python3 -m trainer.bench --model Qwen/... [--adapter <dir>] --out bench.json
    python3 -m trainer.bench --model Qwen/... --tasks tasks.json --out bench.json

Default tasks are generic code prompts; pass --tasks (a JSON list of strings) to
benchmark prompts relevant to the trained codebase.
"""
import argparse
import json
import time

from trainer.engine import Engine, rss_mb
from trainer.common import write_json

DEFAULT_TASKS = [
    "Write a Python function that reverses a linked list.",
    "Explain what a mutex is in two sentences.",
    "Write a Rust function that returns the nth Fibonacci number.",
    "Given a list of integers, write code to find the two numbers that sum to a target.",
    "What does this do: `arr.sort(key=lambda x: -x)` ?",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--tasks", default=None, help="JSON file: list of prompt strings")
    ap.add_argument("--max-new-tokens", type=int, default=160)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    tasks = DEFAULT_TASKS
    if args.tasks:
        with open(args.tasks) as f:
            tasks = json.load(f)

    base_rss = rss_mb()
    eng = Engine(args.model, args.adapter, args.threads)
    loaded_rss = rss_mb()

    results = []
    t0 = time.time()
    for i, prompt in enumerate(tasks):
        r = eng.generate(prompt, args.max_new_tokens)
        results.append({
            "task": prompt,
            "completion_tokens": r["completion_tokens"],
            "latency_s": r["latency_s"],
            "tokens_per_s": r["tokens_per_s"],
            "peak_rss_mb": r["peak_rss_mb"],
            "preview": r["text"][:280],
        })
    wall = time.time() - t0

    n = len(results) or 1
    summary = {
        "model": args.model,
        "adapter": bool(args.adapter),
        "tasks": len(results),
        "load_s": eng.load_s,
        "model_rss_mb": round(loaded_rss - base_rss, 1),
        "peak_rss_mb": max((r["peak_rss_mb"] for r in results), default=loaded_rss),
        "total_completion_tokens": sum(r["completion_tokens"] for r in results),
        "wall_s": round(wall, 2),
        "avg_latency_s": round(sum(r["latency_s"] for r in results) / n, 3),
        "avg_tokens_per_s": round(sum(r["tokens_per_s"] for r in results) / n, 2),
        "results": results,
        "ran_at": time.time(),
    }
    if args.out:
        write_json(args.out, summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
