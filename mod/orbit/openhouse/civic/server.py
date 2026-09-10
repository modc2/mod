"""
OpenHouse Civic Server — the server a government runs.

This is the off-chain half of the civic seat. A city, county, or state runs
this on its own infrastructure — its box, its key, its .gov domain — and gets
two powers over the OpenHouse properties in its jurisdiction:

  VERIFY   Pull a property's terms and full rent ledger and recompute every
           split from scratch: fee, renter equity, owner income, the clamp
           that stops equity running past the home price, and the 0-5% fee
           band. The property node's arithmetic is not trusted — it is
           re-derived and diffed, entry by entry. A ledger that doesn't add
           up is a finding, on the record.

  OVERRIDE Issue a civic pause (freeze payments) or a civic hold (block a
           foreclosure) from this server. On mainnet these are signed
           transactions to OpenHouseTrust.civicSetPaused / setCivicHold from
           the key registered in CivicRegistry.sol; on this testnet
           deployment they are pushed to the property node's API and
           recorded as bookkeeping on both sides.

A city-owned rent-to-own program needs nothing extra: the city takes the bank
seat in the trust as well (its housing fund is the lender), points this same
server at its own deployments, and supervises itself in public.

Identity is proven off-chain, deliberately: publish this server's key on a
domain only the government controls (the `uri` here), so anyone can close the
loop between the registry, the trust's authority seat, and the .gov site.

Run it:  python civic/server.py           (port from CIVIC_PORT, default 50133)
State:   ~/.openhouse-civic/              (its own store — this is a different
                                           institution than the property node,
                                           even when demoed on the same box)

Testnet: the key generated on first boot is a placeholder address, not a real
signing key. A mainnet deployment holds a real key off this repo's disk
(~/.mod/openhouse/, hardware, or a Safe) and signs transactions with it.
"""
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DEFAULT_UPSTREAM = os.getenv("CIVIC_UPSTREAM", "http://localhost:50132")
FEE_BAND_MAX_PCT = 5.0     # MAX_FEE_BPS in the contract — the band the city audits
TOLERANCE = 1e-4           # ledger entries are rounded to 8dp; drift beyond this is a finding


# ── Storage ─────────────────────────────────────────────────────
# Paths are resolved per call, not at import, so a test (or a su'd operator)
# that moves HOME gets the store it expects.

def _store() -> Path:
    d = Path(os.path.expanduser("~/.openhouse-civic"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(name: str, default):
    p = _store() / name
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return default


def _save(name: str, data):
    with open(_store() / name, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _city() -> dict:
    c = _load("city.json", None)
    if c is None:
        # A placeholder key so the server has an address from first boot.
        c = {
            "name": "Unnamed Authority",
            "region": "",
            "uri": "",
            "key": "0x" + secrets.token_hex(20),
            "created": int(time.time()),
        }
        _save("city.json", c)
    return c


# ── Upstream I/O ────────────────────────────────────────────────
# One tiny seam for all HTTP, so tests can stand in a fake property node.

def _fetch(url: str, payload: Optional[dict] = None, timeout: float = 10.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ── The audit ───────────────────────────────────────────────────

def recompute_ledger(terms: dict, ledger: list) -> list:
    """Re-derive every split in a chronological ledger and diff it.

    Each entry is recomputed from its own recorded percentages — terms change
    over time, so the current terms only supply the home price for the equity
    clamp — and the percentages themselves are audited against the protocol
    band. The running principal advances by the RECORDED credit, so one bad
    entry is one finding, not a cascade.
    """
    price = float(terms.get("home_price", 0) or 0)
    running = 0.0
    findings = []
    for i, r in enumerate(ledger):
        amount = float(r.get("amount", 0))
        fee_pct = float(r.get("fee_pct", 0))
        credit_pct = 100.0 if r.get("kind") == "option" else float(r.get("credit_pct", 0))

        if fee_pct < 0 or fee_pct > FEE_BAND_MAX_PCT:
            findings.append({"entry": i, "field": "fee_pct", "recorded": fee_pct,
                             "expected": f"0-{FEE_BAND_MAX_PCT}",
                             "what": "protocol take outside the contract's band"})

        fee = amount * fee_pct / 100.0
        net = amount - fee
        credit = net * credit_pct / 100.0
        if price > 0:
            credit = min(credit, max(price - running, 0.0))
        income = net - credit

        for field, want in (("fee", fee), ("credit", credit), ("owner_income", income)):
            got = float(r.get(field, 0))
            if abs(got - want) > TOLERANCE:
                findings.append({"entry": i, "field": field, "recorded": round(got, 8),
                                 "expected": round(want, 8),
                                 "what": "recorded split does not follow from the entry's own terms"})

        running += float(r.get("credit", 0))

    if price > 0 and running - price > TOLERANCE:
        findings.append({"entry": None, "field": "principal", "recorded": round(running, 8),
                         "expected": f"<= {price}",
                         "what": "credited principal ran past the home price"})
    return findings


def verify_upstream(upstream: str) -> dict:
    """The full audit of one property node, from this server's point of view."""
    terms = _fetch(f"{upstream}/terms")
    ledger = _fetch(f"{upstream}/rent_ledger")     # newest first, per the node
    stats = _fetch(f"{upstream}/rent_stats")
    chronological = list(reversed(ledger))

    findings = recompute_ledger(terms, chronological)

    # The node's own totals must match the ledger it published.
    sums = {
        "gross_rent": sum(float(r.get("amount", 0)) for r in chronological),
        "protocol_fees": sum(float(r.get("fee", 0)) for r in chronological),
        "renter_equity": sum(float(r.get("credit", 0)) for r in chronological),
        "owner_income": sum(float(r.get("owner_income", 0)) for r in chronological),
    }
    for field, want in sums.items():
        got = float(stats.get(field, 0))
        if abs(got - want) > TOLERANCE:
            findings.append({"entry": None, "field": field, "recorded": round(got, 8),
                             "expected": round(want, 8),
                             "what": "published stats disagree with the published ledger"})

    report = {
        "at": int(time.time()),
        "upstream": upstream,
        "payments": len(chronological),
        "home_price": float(terms.get("home_price", 0) or 0),
        "ok": not findings,
        "findings": findings,
    }
    history = _load("verifications.json", [])
    history.append(report)
    _save("verifications.json", history)
    return report


# ── The server ──────────────────────────────────────────────────

app = FastAPI(
    title="OpenHouse Civic Server",
    description="A government's verification and override server for OpenHouse "
                "rent-to-own properties in its jurisdiction.",
)


class CityIdentity(BaseModel):
    name: str
    region: str = ""
    uri: str = ""


class WatchRequest(BaseModel):
    upstream: str = DEFAULT_UPSTREAM
    note: str = ""


class OverrideRequest(BaseModel):
    action: str                      # pause | unpause | hold | release
    reason: str = ""
    upstream: str = DEFAULT_UPSTREAM


@app.get("/")
@app.get("/city")
def city():
    c = _city()
    return {
        **c,
        "watches": len(_load("watches.json", [])),
        "verifications": len(_load("verifications.json", [])),
        "overrides": len(_load("overrides.json", [])),
        "role": "civic authority — verifies off-chain, overrides on-chain",
    }


@app.post("/city")
def set_city(req: CityIdentity):
    c = _city()
    c.update({"name": req.name, "region": req.region, "uri": req.uri,
              "updated": int(time.time())})
    _save("city.json", c)
    return c


@app.get("/watches")
def watches():
    return _load("watches.json", [])


@app.post("/watch")
def watch(req: WatchRequest):
    """Put a property node under this government's watch."""
    all_ = _load("watches.json", [])
    if any(w["upstream"] == req.upstream for w in all_):
        return {"already": True, "upstream": req.upstream}
    all_.append({"upstream": req.upstream, "note": req.note, "since": int(time.time())})
    _save("watches.json", all_)
    return {"watching": req.upstream, "watches": len(all_)}


@app.get("/verify")
def verify(upstream: str = DEFAULT_UPSTREAM):
    """Audit one property node: recompute its whole ledger and diff it."""
    try:
        return verify_upstream(upstream)
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"upstream unreachable: {e}")


@app.get("/verifications")
def verifications():
    return list(reversed(_load("verifications.json", [])))[:100]


@app.post("/override")
def override(req: OverrideRequest):
    """Exercise the civic seat from this server.

    pause/unpause drive the trust's civicPaused; hold/release drive civicHold.
    Testnet: pushed to the property node's /civic/override and booked on both
    sides. Mainnet: a signed transaction from this server's registered key.
    """
    if req.action not in ("pause", "unpause", "hold", "release"):
        raise HTTPException(status_code=400, detail=f"unknown action: {req.action}")
    c = _city()
    entry = {
        "timestamp": int(time.time()),
        "action": req.action,
        "reason": req.reason,
        "upstream": req.upstream,
        "key": c["key"],
        "authority": c["name"],
    }
    try:
        pushed = _fetch(f"{req.upstream}/civic/override",
                        {"action": req.action, "key": c["key"], "reason": req.reason})
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"upstream refused or unreachable: {e}")

    all_ = _load("overrides.json", [])
    all_.append(entry)
    _save("overrides.json", all_)
    return {"success": True, **entry, "upstream_response": pushed}


@app.get("/overrides")
def overrides():
    return list(reversed(_load("overrides.json", [])))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("CIVIC_PORT", "50133")))
