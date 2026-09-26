#!/usr/bin/env python3
"""Parity: the wasm verifiers against the pure-python references.

Signs with the python implementation, verifies through the wasm via the node
host, and checks that both engines agree on valid signatures, tampered
signatures, tampered messages, wrong contexts and wrong keys. This is the
gate a verifier blob must pass before its hash goes into the manifest.

    python3 parity_test.py [mldsa] [slhdsa]
"""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PQ = os.path.dirname(HERE)
sys.path.insert(0, PQ)

import mldsa  # noqa: E402


class Host:
    def __init__(self):
        self.proc = subprocess.Popen(
            ["node", os.path.join(HERE, "host.mjs")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.n = 0

    def verify(self, wasm, pk, msg, sig, ctx=b""):
        self.n += 1
        req = {"id": self.n, "wasm": wasm,
               "sha3": hashlib.sha3_256(open(wasm, "rb").read()).hexdigest(),
               "pk": pk.hex(), "msg": msg.hex(), "sig": sig.hex(),
               "ctx": ctx.hex()}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        resp = json.loads(self.proc.stdout.readline())
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error"))
        return resp["valid"]


def check(label, got, want):
    status = "ok" if got == want else "FAIL"
    print(f"  {status:4} {label}: wasm={got} want={want}")
    return got == want


def run(name, wasm, keygen, sign, pyverify, rounds=4):
    host = Host()
    good = True
    t0 = time.time()
    for i in range(rounds):
        seed = secrets.token_bytes(32)
        pk, sk = keygen(seed)
        msg = secrets.token_bytes(1 + i * 700)
        ctx = b"postquant/tx/v1" if i % 2 else b""
        sig = sign(sk, msg, ctx)
        assert pyverify(pk, msg, sig, ctx), "python reference rejects its own sig"
        good &= check(f"{name} r{i} valid", host.verify(wasm, pk, msg, sig, ctx), True)
        bad = bytearray(sig)
        bad[len(bad) // 2] ^= 1
        good &= check(f"{name} r{i} tampered sig",
                      host.verify(wasm, pk, msg, bytes(bad), ctx), False)
        good &= check(f"{name} r{i} tampered msg",
                      host.verify(wasm, pk, msg + b"x", sig, ctx), False)
        good &= check(f"{name} r{i} wrong ctx",
                      host.verify(wasm, pk, msg, sig, ctx + b"?"), False)
        pk2, _ = keygen(secrets.token_bytes(32))
        good &= check(f"{name} r{i} wrong key",
                      host.verify(wasm, pk2, msg, sig, ctx), False)
    n = host.n
    print(f"  {name}: {n} wasm verifications in {time.time() - t0:.2f}s")
    host.proc.stdin.close()
    return good


def main():
    which = sys.argv[1:] or ["mldsa", "slhdsa"]
    ok = True
    if "mldsa" in which:
        for name, blob in (("ML-DSA-44", "mldsa44.wasm"),
                           ("ML-DSA-65", "mldsa65.wasm"),
                           ("ML-DSA-87", "mldsa87.wasm")):
            ok &= run(
                name, os.path.join(PQ, "wasm", blob),
                lambda seed, n=name: mldsa.keygen_internal(seed, n),
                lambda sk, msg, ctx, n=name: mldsa.sign(sk, msg, n, ctx=ctx),
                lambda pk, msg, sig, ctx, n=name:
                    mldsa.verify(pk, msg, sig, n, ctx=ctx))
    if "slhdsa" in which:
        import slhdsa
        ok &= run(
            "SLH-DSA-SHAKE-128f", os.path.join(PQ, "wasm", "slhdsa128f.wasm"),
            lambda seed: slhdsa.keygen_internal(seed, "SLH-DSA-SHAKE-128f"),
            lambda sk, msg, ctx: slhdsa.sign(sk, msg, ctx=ctx),
            lambda pk, msg, sig, ctx: slhdsa.verify(pk, msg, sig, ctx=ctx),
            rounds=3)
    print("PARITY " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
