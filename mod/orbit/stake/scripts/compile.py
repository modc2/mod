#!/usr/bin/env python3
"""Compile the stake module's contracts through the chain hub's solc bridge.

core/chain's API (:8800) runs solc 0.8.26 standard-JSON with imports resolved
under core/chain/node_modules — no local toolchain needed here. Artifacts
(abi + bytecode) land in artifacts/<Name>.json.
"""
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CHAIN_API = "http://localhost:8800"


def compile_sources(sources: dict) -> dict:
    body = json.dumps({"sources": sources, "optimize": True, "runs": 200}).encode()
    req = urllib.request.Request(f"{CHAIN_API}/build/compile", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=150) as r:
        out = json.loads(r.read())
    if not out.get("ok"):
        for e in out.get("errors", []):
            print(e.get("message"), file=sys.stderr)
        raise SystemExit("compile failed")
    return out


def main():
    sources = {}
    for path in sorted(HERE.glob("contracts/**/*.sol")):
        sources[str(path.relative_to(HERE / "contracts"))] = path.read_text()
    out = compile_sources(sources)
    artifacts = HERE / "artifacts"
    artifacts.mkdir(exist_ok=True)
    for c in out["contracts"]:
        if c.get("abstract"):
            continue
        dest = artifacts / f"{c['name']}.json"
        dest.write_text(json.dumps({"contractName": c["name"], "abi": c["abi"],
                                    "bytecode": c["bytecode"], "solc": out.get("solc")}, indent=1))
        print(f"compiled {c['name']} ({c['size']} bytes) -> {dest.relative_to(HERE)}")


if __name__ == "__main__":
    main()
