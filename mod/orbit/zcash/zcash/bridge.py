"""
Cross-chain bridging for ZEC.

Zcash has no native smart-contract bridge, so moving value between ZEC and
other chains means an atomic-swap / intent network. Two are wired up here, both
reachable without an API key:

  NEAR Intents (1Click)   ZEC <-> 33 chains (Ethereum, Base, Arbitrum, Solana,
                          BTC, Tron, ...). Solver-based: you get a quote and a
                          deposit address, send to it, and the solver pays the
                          destination. This is the primary route.

  Maya Protocol          ZEC <-> BTC/ETH/ARB/USDC etc. via the native ZEC.ZEC
                          pool. A decentralised AMM; deposits carry a memo.

Both use the same shape: quote -> deposit address -> send -> track. When the
origin is ZEC the deposit address is an ordinary t-address, so the module's own
signer can fund it (see mod.bridge_send). When the origin is another chain, the
user funds the returned address from a wallet on that chain.
"""

import datetime
import time

import requests

try:
    from . import evm as _evm
    from . import keys as _k
except ImportError:  # loaded as a loose module
    import evm as _evm
    import keys as _k

ONECLICK = "https://1click.chaindefuser.com/v0"
MAYANODE = "https://mayanode.mayachain.info/mayachain"

ZEC_ASSET = "nep141:zec.omft.near"

# Chains whose addresses are EVM-style; used to validate recipients locally.
EVM_CHAINS = {"eth", "arb", "base", "op", "pol", "bsc", "avax", "gnosis", "scroll",
              "bera", "monad", "xlayer", "abs", "plasma", "adi"}

_token_cache = {"at": 0, "tokens": None}
_TOKEN_TTL = 300


class BridgeError(Exception):
    pass


# ── Token catalog ───────────────────────────────────────────────────────────

def tokens(force: bool = False) -> list:
    now = time.time()
    if not force and _token_cache["tokens"] and now - _token_cache["at"] < _TOKEN_TTL:
        return _token_cache["tokens"]
    try:
        r = requests.get(f"{ONECLICK}/tokens", timeout=25)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        if _token_cache["tokens"]:
            return _token_cache["tokens"]
        raise BridgeError(f"could not load bridge token list: {e}")
    _token_cache.update(at=now, tokens=data)
    return data


def chains() -> list:
    """Every chain reachable from ZEC, with its assets."""
    grouped = {}
    for t in tokens():
        grouped.setdefault(t["blockchain"], []).append(
            {"symbol": t["symbol"], "asset_id": t["assetId"],
             "decimals": t["decimals"], "price_usd": t.get("price")})
    return [{"chain": c, "assets": sorted(a, key=lambda x: x["symbol"])}
            for c, a in sorted(grouped.items())]


def resolve_asset(spec: str) -> dict:
    """Resolve 'ETH', 'eth:USDC', 'base:ETH' or a raw assetId to one token."""
    s = (spec or "").strip()
    if not s:
        raise BridgeError("no asset given")
    all_tokens = tokens()
    by_id = {t["assetId"]: t for t in all_tokens}
    if s in by_id:
        return by_id[s]

    chain, _, symbol = s.rpartition(":")
    symbol = symbol.upper()
    matches = [t for t in all_tokens if t["symbol"].upper() == symbol]
    if chain:
        matches = [t for t in matches if t["blockchain"].lower() == chain.lower()]
    if not matches:
        near = sorted({t["symbol"] for t in all_tokens
                       if t["symbol"].upper().startswith(symbol[:2])})[:8]
        raise BridgeError(
            f"unknown asset {spec!r}"
            + (f"; did you mean one of {', '.join(near)}?" if near else "")
            + " -- call bridge_chains() for the full list")
    if len(matches) > 1:
        # A bare symbol means its home chain when one exists: "ETH" is Ethereum,
        # "SOL" is Solana. Only genuinely ambiguous symbols (USDC) need a prefix.
        native = [t for t in matches if t["blockchain"].lower() == symbol.lower()]
        if len(native) == 1:
            return native[0]
        opts = ", ".join(f"{t['blockchain']}:{t['symbol']}" for t in matches[:10])
        raise BridgeError(
            f"{symbol} exists on several chains; qualify it, e.g. {opts}")
    return matches[0]


def _validate_recipient(token: dict, recipient: str):
    chain = token["blockchain"]
    if chain in EVM_CHAINS:
        if not _evm.is_valid_evm_address(recipient):
            raise BridgeError(
                f"{recipient!r} is not a valid {chain} address "
                "(20-byte hex; mixed case must satisfy the EIP-55 checksum)")
    elif chain == "zec":
        if not _k.is_valid_address(recipient):
            raise BridgeError(f"{recipient!r} is not a valid Zcash address")


def to_base_units(amount, decimals: int) -> str:
    """Decimal amount -> integer base units, without float rounding."""
    from decimal import Decimal, ROUND_DOWN
    q = (Decimal(str(amount)) * (10 ** decimals)).quantize(Decimal(1), rounding=ROUND_DOWN)
    if q <= 0:
        raise BridgeError(f"amount {amount} is too small for this asset")
    return str(int(q))


# ── NEAR Intents (1Click) ───────────────────────────────────────────────────

def quote(from_asset: str, to_asset: str, amount, recipient: str,
          refund_to: str, dry: bool = True, slippage_bps: int = 100,
          deadline_hours: int = 6) -> dict:
    """Quote a swap. With dry=False this reserves a real deposit address."""
    src = resolve_asset(from_asset)
    dst = resolve_asset(to_asset)
    if src["assetId"] == dst["assetId"]:
        raise BridgeError("origin and destination assets are the same")
    _validate_recipient(dst, recipient)
    _validate_recipient(src, refund_to)

    deadline = (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(hours=deadline_hours)
                ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {
        "dry": bool(dry), "swapType": "EXACT_INPUT",
        "slippageTolerance": int(slippage_bps),
        "originAsset": src["assetId"], "depositType": "ORIGIN_CHAIN",
        "destinationAsset": dst["assetId"], "recipientType": "DESTINATION_CHAIN",
        "amount": to_base_units(amount, src["decimals"]),
        "deadline": deadline,
        "recipient": recipient, "refundTo": refund_to, "refundType": "ORIGIN_CHAIN",
    }
    try:
        r = requests.post(f"{ONECLICK}/quote", json=body, timeout=30)
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        raise BridgeError(f"1Click quote failed: {e}")
    if r.status_code >= 400:
        raise BridgeError(f"1Click rejected the quote: {payload.get('message', payload)}")

    q = payload.get("quote") or {}
    out = {
        "route": "near-intents",
        "from": f"{src['blockchain']}:{src['symbol']}",
        "to": f"{dst['blockchain']}:{dst['symbol']}",
        "amount_in": q.get("amountInFormatted"),
        "amount_in_usd": q.get("amountInUsd"),
        "amount_out": q.get("amountOutFormatted"),
        "amount_out_usd": q.get("amountOutUsd"),
        "min_amount_out": q.get("minAmountOut"),
        "eta_seconds": q.get("timeEstimate"),
        "deposit_address": q.get("depositAddress"),
        "deadline": q.get("deadline"),
        "recipient": recipient,
        "refund_to": refund_to,
        "dry": bool(dry),
    }
    try:
        a_in = float(q.get("amountInUsd") or 0)
        a_out = float(q.get("amountOutUsd") or 0)
        if a_in:
            out["price_impact_pct"] = round((a_out - a_in) / a_in * 100, 3)
    except (TypeError, ValueError):
        pass
    if not dry and not out["deposit_address"]:
        raise BridgeError("1Click returned no deposit address")
    if not dry:
        out["instructions"] = (
            f"Send exactly {out['amount_in']} {src['symbol']} on "
            f"{src['blockchain']} to {out['deposit_address']} before "
            f"{out['deadline']}. Funds arrive at {recipient}; anything that "
            f"misses the deadline is refunded to {refund_to}.")
    return out


def status(deposit_address: str) -> dict:
    try:
        r = requests.get(f"{ONECLICK}/status",
                         params={"depositAddress": deposit_address}, timeout=25)
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        raise BridgeError(f"status lookup failed: {e}")
    if r.status_code == 404:
        raise BridgeError(f"no swap found for deposit address {deposit_address}")
    if r.status_code >= 400:
        raise BridgeError(f"status lookup failed: {payload.get('message', payload)}")
    d = payload.get("swapDetails") or {}
    return {
        "route": "near-intents",
        "deposit_address": deposit_address,
        "status": payload.get("status"),
        "updated_at": payload.get("updatedAt"),
        "deposited": d.get("depositedAmountFormatted"),
        "amount_out": d.get("amountOutFormatted"),
        "amount_out_usd": d.get("amountOutUsd"),
        "origin_tx": [t.get("hash") for t in (d.get("originChainTxHashes") or [])],
        "destination_tx": [t.get("hash") for t in (d.get("destinationChainTxHashes") or [])],
        "near_tx": d.get("nearTxHashes") or [],
    }


# ── Maya Protocol ───────────────────────────────────────────────────────────

def maya_status() -> dict:
    """Pool and inbound-address health for the ZEC.ZEC pool."""
    try:
        inbound = requests.get(f"{MAYANODE}/inbound_addresses", timeout=25).json()
    except (requests.RequestException, ValueError) as e:
        raise BridgeError(f"maya unreachable: {e}")
    zec = next((c for c in inbound if c.get("chain") == "ZEC"), None)
    halted_chains = [c["chain"] for c in inbound if c.get("halted")]
    return {
        "route": "maya",
        "zec_inbound_address": (zec or {}).get("address"),
        "zec_halted": bool((zec or {}).get("halted", True)),
        "gas_rate": (zec or {}).get("gas_rate"),
        "halted_chains": halted_chains,
        "available": bool(zec) and not zec.get("halted"),
    }


def maya_quote(to_asset: str, amount_zec, destination: str) -> dict:
    """Quote ZEC -> another Maya-supported asset (e.g. 'ETH.ETH', 'BTC.BTC')."""
    amount = int(round(float(amount_zec) * 1e8))
    params = {"from_asset": "ZEC.ZEC", "to_asset": to_asset,
              "amount": amount, "destination": destination}
    try:
        r = requests.get(f"{MAYANODE}/quote/swap", params=params, timeout=30)
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        raise BridgeError(f"maya quote failed: {e}")
    if payload.get("error"):
        health = maya_status()
        raise BridgeError(
            f"maya: {payload['error']}"
            + (f" (halted chains: {', '.join(health['halted_chains'])})"
               if health["halted_chains"] else ""))
    return {
        "route": "maya",
        "from": "ZEC.ZEC", "to": to_asset,
        "amount_in_zec": amount / 1e8,
        "expected_out": payload.get("expected_amount_out"),
        "deposit_address": payload.get("inbound_address"),
        "memo": payload.get("memo"),
        "eta_seconds": payload.get("total_swap_seconds"),
        "fees": payload.get("fees"),
        "expiry": payload.get("expiry"),
        "note": "Maya deposits must carry the memo exactly, or funds are lost.",
    }


# ── Aggregation ─────────────────────────────────────────────────────────────

def best_quote(to_asset: str, amount_zec, recipient: str, refund_to: str) -> dict:
    """Quote ZEC -> to_asset on every available route and rank by output value."""
    results, errors = [], {}
    try:
        results.append(quote(ZEC_ASSET, to_asset, amount_zec, recipient, refund_to, dry=True))
    except BridgeError as e:
        errors["near-intents"] = str(e)

    maya_asset = {"eth": "ETH.ETH", "btc": "BTC.BTC"}.get(str(to_asset).lower())
    if maya_asset:
        try:
            results.append(maya_quote(maya_asset, amount_zec, recipient))
        except BridgeError as e:
            errors["maya"] = str(e)

    def score(q):
        try:
            return float(q.get("amount_out_usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    results.sort(key=score, reverse=True)
    return {"best": results[0] if results else None,
            "quotes": results, "unavailable": errors}
