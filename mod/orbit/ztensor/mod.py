"""
ztensor — anonymous voting & consensus for miner/validator networks.

Miners and validators register a PUBLIC key into an eligible set. To vote, a
member produces a Linkable Spontaneous Anonymous Group signature (see ring.py):
the tally learns that *a* member voted and what they chose, never *which*
member. A per-topic linkable tag stops anyone voting twice on a topic.

Design stance — PRIVATE BALLOTS, PUBLIC BOOKS:
  * votes are unlinkable (you cannot map a vote back to a validator);
  * rewards are the opposite — payout instructions are transparent, linkable
    and auditable, so anyone can check the pool was distributed honestly.
  * secret keys NEVER touch this service. It stores public keys, tags, and
    choices only. (keygen/sign live client-side; see `ztensor/demo`.)

CLI:
    m ztensor/demo                        # end-to-end anonymous vote, in-process
    m ztensor/register pub=<hex>          # add a public key to the eligible set
    m ztensor/set                         # show the current set
    m ztensor/tally topic=<t>             # public tally of a topic
    m ztensor/payout topic=<t> pool=1000  # transparent reward split by vote share
    m ztensor/test                        # self-check (sign/verify/double-vote)
    m ztensor/build                       # build Next.js console + Rust API
    m ztensor/serve                       # api + console on :51180, route /ztensor
    m ztensor/kill

Service stack: the Rust binary (api/, axum) serves both the JSON API and the
static Next.js console (app/ -> dist/); server.py is the pure-python fallback
used only when the binary has not been built. ring.py stays the reference
LSAG implementation — api/src/lsag.rs and app/lib/lsag.mjs are byte-for-byte
compatible ports (cross-checked by `cargo test` and app/scripts/crosstest.mjs).
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import mod as m

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

import ring  # noqa: E402

PM2_NAME = "ztensor"
DATA_DIR = Path(os.environ.get("ZTENSOR_DATA", MODULE_DIR / "state"))
STATE_FILE = DATA_DIR / "state.json"


def _load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"participants": [], "topics": {}}


def _save(st: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=2))


class Mod:
    description = (
        "Anonymous voting & consensus: members vote without revealing which "
        "member they are (LSAG); private ballots, public auditable payouts"
    )

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / "config.json").read_text())
        self.port = int(self.config.get("port", 51180))

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {
            "name": self.config["name"],
            "description": self.description,
            "port": self.port,
            "url": f"http://localhost:{self.port}/ztensor/",
            "scheme": "LSAG over RFC3526 MODP-2048 (order-q QR subgroup)",
            "guarantees": ["anonymity", "per-topic double-vote resistance", "public auditable payouts"],
            "fns": self.config["fns"],
        }

    # ── eligible set ─────────────────────────────────────────────────

    def register(self, pub: str) -> dict:
        """Add a PUBLIC key (hex int) to the eligible set. Private keys stay
        with their holder and never reach this service."""
        y = int(pub, 16) if isinstance(pub, str) else int(pub)
        st = _load()
        if y not in [int(p, 16) for p in st["participants"]]:
            st["participants"].append(format(y, "x"))
            _save(st)
        parts = [int(p, 16) for p in st["participants"]]
        return {"index": parts.index(y), "size": len(parts)}

    def set(self) -> dict:
        st = _load()
        return {"size": len(st["participants"]), "participants": st["participants"]}

    # ── voting ───────────────────────────────────────────────────────

    def _ring(self, st: dict):
        return [int(p, 16) for p in st["participants"]]

    def vote(self, topic: str, choice: str, sig: dict) -> dict:
        """Record an anonymous vote. `sig` is an LSAG signature (from ring.sign)
        over msg = topic|choice against the current eligible set. Rejected if
        the signature is invalid or the tag was already used for this topic."""
        if isinstance(sig, str):
            sig = json.loads(sig)
        st = _load()
        tp = st["topics"].setdefault(topic, {"ring": st["participants"][:], "votes": {}})
        rng = [int(p, 16) for p in tp["ring"]]
        msg = f"{topic}|{choice}".encode()
        if not ring.verify(rng, topic.encode(), msg, sig):
            return {"accepted": False, "reason": "invalid signature"}
        tag = format(int(sig["tag"]), "x")
        if tag in tp["votes"]:
            return {"accepted": False, "reason": "double vote (tag already used for topic)"}
        tp["votes"][tag] = choice
        _save(st)
        return {"accepted": True, "topic": topic}

    def tally(self, topic: str) -> dict:
        st = _load()
        tp = st["topics"].get(topic)
        if not tp:
            return {"topic": topic, "counts": {}, "total": 0}
        counts: dict = {}
        for choice in tp["votes"].values():
            counts[choice] = counts.get(choice, 0) + 1
        return {"topic": topic, "counts": counts, "total": len(tp["votes"])}

    def payout(self, topic: str, pool: float = 0.0) -> dict:
        """Transparent reward split. Each `choice` is treated as a recipient;
        the pool is divided proportionally to votes. This is LINKABLE and
        auditable ON PURPOSE — anyone can verify the books against the tally."""
        t = self.tally(topic)
        total = t["total"]
        instructions = []
        if total:
            for recipient, votes in sorted(t["counts"].items(), key=lambda kv: -kv[1]):
                share = votes / total
                instructions.append({
                    "recipient": recipient,
                    "votes": votes,
                    "share": round(share, 6),
                    "amount": round(pool * share, 8),
                })
        return {"topic": topic, "pool": pool, "total_votes": total, "instructions": instructions}

    # ── self-check ───────────────────────────────────────────────────

    def test(self) -> dict:
        """End-to-end: build a ring, cast anonymous votes, verify anonymity +
        double-vote rejection. Uses ephemeral keys; touches no stored state."""
        keys = [ring.keygen() for _ in range(5)]
        rng = [pub for _, pub in keys]
        topic = b"reward-epoch-1"
        # three distinct members vote
        sigs = []
        for i in (0, 2, 4):
            msg = b"reward-epoch-1|minerA"
            sigs.append(ring.sign(keys[i][0], rng, topic, msg))
        ok_all = all(ring.verify(rng, topic, b"reward-epoch-1|minerA", s) for s in sigs)
        tags = {format(s["tag"], "x") for s in sigs}
        # member 0 tries to vote twice -> same tag -> caught
        dup = ring.sign(keys[0][0], rng, topic, b"reward-epoch-1|minerB")
        dup_caught = format(dup["tag"], "x") in tags
        # a non-member cannot sign
        outsider, _ = ring.keygen()
        try:
            ring.sign(outsider, rng, topic, b"x")
            outsider_blocked = False
        except ValueError:
            outsider_blocked = True
        passed = ok_all and len(tags) == 3 and dup_caught and outsider_blocked
        return {
            "passed": passed,
            "signatures_verified": ok_all,
            "distinct_voters": len(tags),
            "double_vote_caught": dup_caught,
            "outsider_blocked": outsider_blocked,
        }

    def demo(self) -> dict:
        """Same as test but phrased as a walkthrough result."""
        r = self.test()
        r["explanation"] = (
            "5 members registered; 3 cast anonymous votes for the same miner. "
            "The tally sees 3 valid votes but cannot tell which members voted. "
            "A repeat vote by member 0 is rejected via its reused tag."
        )
        return r

    # ── service ──────────────────────────────────────────────────────

    def build(self) -> dict:
        """Build the Next.js console (atomic dist swap) and the Rust API."""
        r = subprocess.run(["bash", str(MODULE_DIR / "build.sh")], capture_output=True, text=True)
        return {"ok": r.returncode == 0, "tail": (r.stdout + r.stderr).splitlines()[-8:]}

    def serve(self, port: Optional[int] = None) -> dict:
        port = int(port or self.port)
        env = dict(os.environ, ZTENSOR_PORT=str(port), ZTENSOR_DIR=str(MODULE_DIR))
        binary = MODULE_DIR / "api" / "target" / "release" / "ztensor-api"
        # Rust binary serves API + Next console; python server.py is the fallback
        cmd = [str(binary)] if binary.exists() else [sys.executable, str(MODULE_DIR / "server.py")]
        subprocess.Popen(
            cmd, cwd=str(MODULE_DIR), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        return {"serving": True, "port": port, "stack": "rust" if cmd[0].endswith("ztensor-api") else "python",
                "url": f"http://localhost:{port}/ztensor/"}

    def kill(self) -> dict:
        # exact paths only — never a bare pattern that could match other mods
        subprocess.run(["pkill", "-f", str(MODULE_DIR / "api/target/release/ztensor-api")], check=False)
        subprocess.run(["pkill", "-f", str(MODULE_DIR / "server.py")], check=False)
        return {"killed": True}

    def status(self) -> dict:
        st = _load()
        return {"participants": len(st["participants"]), "topics": list(st["topics"].keys())}
