"""Persistent inference worker — JSON-lines over stdin/stdout.

The Rust API spawns ONE of these per (model, adapter) and keeps it warm, so the
model loads once and every chat turn is fast. Protocol:

  startup → stdout: {"ready": true, "load_s": 3.1, "model": "...", "adapter": false}
  request ← stdin:  {"id": 1, "prompt": "...", "max_new_tokens": 200, "temperature": 0.2}
  response → stdout:{"id": 1, "text": "...", "completion_tokens": 87, "latency_s": 4.2, ...}

One JSON object per line, both directions. Any per-request error comes back as
{"id": <id>, "error": "..."} so a bad turn never kills the worker.

    python3 -m trainer.infer --model Qwen/... [--adapter <dir>] [--threads 4]
"""
import argparse
import json
import sys

from trainer.engine import Engine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()

    try:
        eng = Engine(args.model, args.adapter, args.threads)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ready": False, "error": str(e)}), flush=True)
        raise SystemExit(1)

    print(json.dumps({
        "ready": True, "load_s": eng.load_s, "model": eng.model_id,
        "adapter": bool(eng.adapter),
    }), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        try:
            res = eng.generate(
                req["prompt"],
                int(req.get("max_new_tokens", 200)),
                float(req.get("temperature", 0.2)),
            )
            res["id"] = rid
            print(json.dumps(res), flush=True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"id": rid, "error": str(e)}), flush=True)


if __name__ == "__main__":
    main()
