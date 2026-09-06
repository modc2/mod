"""Play one arena round on ui/compress and keep what each agent wrote.

The board scores a match off the scratch dir and then deletes it — right for a
benchmark, useless when the artifact IS the answer. So rmtree is wrapped: the
dir is copied to /tmp/arena-ui/<agent>/ on its way out, and the match is scored
and rated exactly as it normally would be.

    cd /root/mod/mod/orbit/agent && python3 arena_ui_round.py builder debugger ...
"""
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.mod import Mod              # noqa: E402
from src.arena import mod as arena_mod  # noqa: E402

TASK = "ui/compress#0"
OUT = Path("/tmp/arena-ui")
OUT.mkdir(parents=True, exist_ok=True)

agents = sys.argv[1:] or ["builder", "debugger", "dev", "safety", "dev12"]

m = Mod()
arena = m.arena

_current = {"agent": ""}
_rmtree = arena_mod.shutil.rmtree


def keep(path, *a, **kw):
    try:
        dest = OUT / _current["agent"]
        if Path(path).exists():
            if dest.exists():
                _rmtree(dest, ignore_errors=True)
            shutil.copytree(path, dest)
    except Exception as e:
        print(f"  ! could not keep {path}: {e}", flush=True)
    return _rmtree(path, *a, **kw)


arena_mod.shutil.rmtree = keep

results = []
for a in agents:
    _current["agent"] = a
    print(f"── {a} ...", flush=True)
    t0 = time.time()
    try:
        match = arena.run_match(a, TASK, reason="ui/compress round")
    except Exception as e:
        print(f"  ! {a} blew up: {e}", flush=True)
        continue
    failed = [c["reason"] for c in match.get("checks", []) if not c["passed"]]
    print(f"  score={match['score']} passed={match['passed']} steps={match['steps']}/{match['budget']} "
          f"{match['seconds']}s ${match['cost']} void={match.get('void_reason')}", flush=True)
    for f in failed[:6]:
        print(f"    x {f}", flush=True)
    results.append({"agent": a, **{k: match.get(k) for k in
                                   ("score", "correct", "reliable", "efficient", "passed",
                                    "steps", "budget", "seconds", "cost", "tokens",
                                    "void", "void_reason")},
                    "failed": failed})

(OUT / "round.json").write_text(json.dumps(results, indent=2))
print("\n" + json.dumps(sorted(results, key=lambda r: -r["score"]), indent=2)[:2000], flush=True)
