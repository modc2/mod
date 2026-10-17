"""Generate a cross-implementation LSAG test vector with ring.py.

Run from the module root:  python3 api/tests/make_fixture.py
The Rust test `verifies_python_fixture` and the web cross-test both read it.
"""
import json
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODULE_DIR))
import ring  # noqa: E402

keys = [ring.keygen() for _ in range(4)]
rng = [pub for _, pub in keys]
topic = "fixture-epoch"
msg = "fixture-epoch|minerA"
sig = ring.sign(keys[1][0], rng, topic.encode(), msg.encode())
assert ring.verify(rng, topic.encode(), msg.encode(), sig)

out = {
    "ring": [format(y, "x") for y in rng],
    "topic": topic,
    "msg": msg,
    "sig": {"c0": str(sig["c0"]), "s": [str(x) for x in sig["s"]], "tag": str(sig["tag"])},
}
dest = Path(__file__).parent / "fixtures" / "lsag_python.json"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out))
print(f"wrote {dest}")
