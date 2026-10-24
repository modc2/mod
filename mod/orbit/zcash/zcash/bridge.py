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


class ShieldedRouteUnavailable(BridgeError):
    """The router will not pay a shielded Zcash address today.

    1Click quotes ZEC to a t-address and rejects a zs1 or u1 recipient as
    "recipient is not valid" -- solver support for shielded outputs is a
    property of the router, not of this module, and it has changed before.
    Raised as its own type so the caller can offer the two-leg route (bridge
    to your own t-address, then shield it) instead of passing an opaque
    third-party error up to someone who pasted a perfectly good z-address.
    """


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


# ── Shielded routes ─────────────────────────────────────────────────────────
#
# The solver networks speak ordinary chain addresses, and for Zcash that has
# historically meant a t-address: money crosses the bridge in the clear and the
# user is expected to shield it afterwards, in a second transaction, from a
# wallet that can prove.
#
# It does not have to be that way in the inbound direction, when the router
# cooperates. The 1Click router has accepted a ZIP-316 **unified address** as a
# ZEC recipient while rejecting a bare `zs1` (a UA quoted 201, a zs1 answered
# 400 "recipient is not valid"). A unified address whose only receiver is
# Sapling leaves the sender no transparent option -- so wrapping the user's
# shielded address in a shielded-only UA is what turns "bridge to Zcash" into
# "bridge into the shielded pool", with no second transaction and no
# transparent hop.
#
# That acceptance is the router's rule and it moves: since 2026-09-02 the same
# quote is refused for `u1` as well. `shielded_quote` raises
# ShieldedRouteUnavailable for exactly that answer so the caller can offer the
# public two-leg route rather than pass "recipient is not valid" back to
# someone whose address was fine.
#
# The catch, stated plainly because it decides whether funds are private: a
# unified address that ALSO publishes a transparent receiver is a transparent
# destination in practice, because ZIP-316 lets the sender pick any receiver it
# supports and a solver will pick the cheap one. The wallet's own u1 address is
# exactly that shape (Sapling + P2PKH). So `shielded_recipient` never passes a
# user's address through untouched -- it re-encodes it with the transparent
# receivers removed and says so.
#
# The outbound direction cannot be fixed this way. Spending a shielded note
# needs a Groth16 proof, and the deposit address the solver hands back is an
# ordinary t-address regardless. See `shielded_plan` for what that costs.

try:
    from . import sapling as _sapling
except ImportError:  # loaded as a loose module
    import sapling as _sapling

SHIELDED_TYPECODES = (_sapling.TYPECODE_SAPLING, _sapling.TYPECODE_ORCHARD)
_TRANSPARENT_TYPECODES = (_sapling.TYPECODE_P2PKH, _sapling.TYPECODE_P2SH)
_RECEIVER_NAMES = {0x00: "p2pkh", 0x01: "p2sh", 0x02: "sapling", 0x03: "orchard"}


POOL_NAMES = {_sapling.TYPECODE_SAPLING: "sapling",
              _sapling.TYPECODE_ORCHARD: "orchard"}


def shielded_recipient(address: str, readable: set = None) -> dict:
    """Normalise any Zcash address into one a bridge can pay *privately*.

    Returns the address to hand the router, plus what had to change to get
    there. Raises BridgeError for an address that cannot be paid shielded at
    all -- a bare t-address is not a shielded destination and pretending
    otherwise would quietly publish the payment.

    `readable` names the pools the caller can actually decrypt notes in
    ({"sapling"}, or {"sapling", "orchard"}). Receivers outside it are dropped:
    directing a bridge into a pool this module cannot scan would land the money
    somewhere real but invisible, which reads to the user as lost. Pass None to
    accept every shielded receiver the address offers -- correct for a caller
    that knows it is only building an address for some other wallet to use.
    """
    a = (address or "").strip()
    if not a:
        raise BridgeError("no Zcash address given")
    if readable is not None:
        readable = {str(p).lower() for p in readable}
        if not readable:
            raise BridgeError(
                "this module cannot read notes in any shielded pool, so it "
                "will not direct a bridge into one. Check capabilities().")

    if a.startswith(("t1", "t3")):
        raise BridgeError(
            f"{a} is a transparent address, so a bridge into it is a public "
            "payment. For a private one, pass this wallet's shielded address "
            "(zs1... or u1...) -- shielded_address(name) prints it.")

    if a.startswith("zs1"):
        if readable is not None and "sapling" not in readable:
            raise BridgeError(
                "this module cannot read Sapling notes, so it will not direct "
                "a bridge into a Sapling address. Check capabilities().")
        try:
            pa = _sapling.decode_payment_address(a)
        except ValueError as e:
            raise BridgeError(f"{a} is not a valid Sapling address: {e}")
        ua = _sapling.encode_unified_address([(_sapling.TYPECODE_SAPLING, pa.raw)])
        return {
            "recipient": ua,
            "given": a,
            "pool": "sapling",
            "pools": ["sapling"],
            "receivers": ["sapling"],
            "rewritten": True,
            "why": ("The router rejects a bare zs1 address, so this is the same "
                    "Sapling receiver re-encoded as a unified address. Same "
                    "keys, same notes -- only the envelope changed."),
        }

    if a.startswith("u1"):
        try:
            receivers = _sapling.decode_unified_address(a)
        except ValueError as e:
            raise BridgeError(f"{a} is not a valid unified address: {e}")
        kinds = [_RECEIVER_NAMES.get(tc, f"unknown({tc})") for tc, _ in receivers]
        shielded = [(tc, d) for tc, d in receivers if tc in SHIELDED_TYPECODES]
        if not shielded:
            raise BridgeError(
                f"{a} publishes only {', '.join(kinds)} receivers, so paying it "
                "is a transparent payment. A private bridge needs a Sapling or "
                "Orchard receiver.")

        dropped = [_RECEIVER_NAMES.get(tc, str(tc)) for tc, _ in receivers
                   if tc in _TRANSPARENT_TYPECODES]

        # A receiver we cannot decrypt is worse than no receiver: the money
        # arrives, the chain confirms it, and every balance we can show says
        # zero. ZIP-316 lets the sender pick any receiver it supports, so
        # leaving an unreadable one in the address is leaving that outcome on
        # the table.
        unreadable = []
        if readable is not None:
            keep = [(tc, d) for tc, d in shielded
                    if POOL_NAMES.get(tc, "") in readable]
            unreadable = [POOL_NAMES.get(tc, str(tc)) for tc, d in shielded
                          if (tc, d) not in keep]
            if not keep:
                offered = ", ".join(POOL_NAMES.get(tc, str(tc)) for tc, _ in shielded)
                raise BridgeError(
                    f"{a} offers only {offered} receivers, and this module "
                    f"cannot read notes in {offered}. Bridging into it would "
                    "land real funds that no balance here could ever show. "
                    f"Readable pools: {', '.join(sorted(readable))}.")
            shielded = keep

        left = [POOL_NAMES.get(tc, str(tc)) for tc, _ in shielded]
        if not dropped and not unreadable:
            return {"recipient": a, "given": a, "pool": left[0],
                    "pools": left, "receivers": left, "rewritten": False,
                    "why": "This unified address offers no transparent "
                           "receiver, so the payment can only land shielded."}

        stripped = _sapling.encode_unified_address(shielded)
        reasons = []
        if dropped:
            reasons.append(
                f"it also published a {', '.join(dropped)} receiver, and a "
                "sender is free to pick it -- which would put the funds in the "
                "clear")
        if unreadable:
            reasons.append(
                f"it offered a {', '.join(unreadable)} receiver this module "
                "cannot decrypt, so a payment there would be invisible to "
                "every balance shown here")
        out = {
            "recipient": stripped,
            "given": a,
            "pool": left[0],
            "pools": left,
            "receivers": left,
            "rewritten": True,
            "why": ("This is the address you gave with receivers removed, "
                    "because " + "; and ".join(reasons) + ". What is left can "
                    f"only be paid into the {' or '.join(left)} pool."),
        }
        if dropped:
            out["dropped_receivers"] = dropped
        if unreadable:
            out["unreadable_receivers"] = unreadable
        return out

    if a.startswith("zc"):
        raise BridgeError("Sprout addresses are deprecated and cannot receive funds")
    raise BridgeError(f"unrecognised Zcash address {a!r}")


def _is_recipient_rejection(e: Exception) -> bool:
    """Did the router turn the quote down over the recipient address?

    Matched on the message because 1Click answers every bad field with the
    same 400; the distinguishing detail is the text. Kept deliberately narrow
    -- a refundTo complaint is a different problem and must not be dressed up
    as a shielded-support outage.
    """
    msg = str(e).lower()
    return "recipient" in msg and ("not valid" in msg or "invalid" in msg)


def shielded_quote(from_asset: str, amount, z_recipient: str, refund_to: str,
                   dry: bool = True, slippage_bps: int = 100,
                   deadline_hours: int = 6, readable: set = None) -> dict:
    """Quote <asset> -> ZEC landing directly in the shielded pool."""
    target = shielded_recipient(z_recipient, readable=readable)
    try:
        q = quote(from_asset, ZEC_ASSET, amount, target["recipient"], refund_to,
                  dry=dry, slippage_bps=slippage_bps,
                  deadline_hours=deadline_hours)
    except BridgeError as e:
        if _is_recipient_rejection(e):
            raise ShieldedRouteUnavailable(
                f"the bridge router will not pay a shielded Zcash address "
                f"right now -- it rejected {target['recipient'][:16]}... as an "
                f"invalid recipient, while the same quote to a transparent "
                f"t-address is accepted. Nothing was reserved and nothing was "
                f"sent. Bridge to a t-address you own and shield it after "
                f"(shielded_shield), or wait for the router to take z-addresses "
                f"again. Router said: {e}")
        raise

    q["shielded"] = True
    q["destination_pool"] = target["pool"]
    q["destination_pools"] = target.get("pools", [target["pool"]])
    q["recipient_given"] = target["given"]
    q["recipient_rewritten"] = target["rewritten"]
    q["recipient_note"] = target["why"]
    if target.get("dropped_receivers"):
        q["dropped_receivers"] = target["dropped_receivers"]
    if target.get("unreadable_receivers"):
        q["unreadable_receivers"] = target["unreadable_receivers"]
    q["privacy"] = privacy("in", from_asset, target["pool"])
    return q


def shielded_out_quote(to_asset: str, amount_zec, recipient: str, refund_to: str,
                       dry: bool = True, slippage_bps: int = 100,
                       deadline_hours: int = 6) -> dict:
    """Quote shielded ZEC -> <asset>. The deposit address is transparent.

    `refund_to` must be transparent: a refund is a payment from the solver, and
    the solver cannot prove a shielded output any more than this module can.
    """
    if str(refund_to or "").startswith(("zs1", "u1")):
        raise BridgeError(
            "the refund address must be a transparent t-address: a refund is "
            "paid by the solver on the origin chain, and no solver can send "
            "into the Zcash shielded pool. Use a t-address from this wallet.")
    q = quote(ZEC_ASSET, to_asset, amount_zec, recipient, refund_to,
              dry=dry, slippage_bps=slippage_bps, deadline_hours=deadline_hours)
    q["funded_from"] = "shielded"
    q["privacy"] = privacy("out", to_asset, "sapling")
    return q


def privacy(direction: str, other_asset: str, pool: str = "sapling") -> dict:
    """What a shielded bridge in this direction does and does not hide."""
    if direction == "in":
        return {
            "direction": f"{other_asset} -> shielded ZEC",
            "grade": "good",
            "hidden": [
                "The ZEC amount, once it lands: a Sapling output is encrypted, "
                "so the chain shows a shielded output and nothing else.",
                "Your Zcash address: it never appears on the Zcash chain in the "
                "clear, and no transparent hop links it to anything.",
            ],
            "visible": [
                f"Everything you did on the {other_asset} side: the origin chain "
                "sees you fund the solver's deposit address in the open.",
                "The solver knows the origin address and the destination "
                "address, because you told it both.",
                "The amount and the timing of the swap, to anyone watching the "
                "origin chain -- the value entering the shielded pool is public "
                "even though its destination is not.",
            ],
            "better": [
                "Bridge amounts that are not memorable, and not the exact "
                "balance of the origin address.",
                "Let the funds sit before you spend them: value that enters "
                "and leaves the pool within minutes correlates by timing.",
            ],
        }
    return {
        "direction": f"shielded ZEC -> {other_asset}",
        "grade": "weak",
        "hidden": [
            "Which notes paid for it: the spend proves it owns *a* note "
            "without saying which one, so the payment does not link back to "
            "how the ZEC arrived.",
        ],
        "visible": [
            "The amount: unshielding is a public output, so the value leaving "
            "the pool is in the clear.",
            f"The link between that amount and your {other_asset} address, to "
            "the solver and to anyone comparing both chains at that timestamp.",
        ],
        "better": [
            "This direction cannot be made private by the bridge. The value has "
            "to become transparent to leave Zcash at all.",
            "Break the timing link: unshield to a fresh t-address first, wait, "
            "then bridge from it as an ordinary transparent bridge.",
        ],
        "note": "Leaving the shielded pool is the leaky direction. Entering it "
                "is the private one.",
    }


def shielded_plan(has_node: bool = False) -> dict:
    """Which shielded bridge directions work right now, and what each needs."""
    return {
        "in": {
            "supported": True,
            "route": "near-intents",
            "how": "When the router accepts a shielded-only unified address as "
                   "the ZEC recipient, the solver's payment lands as a Sapling "
                   "output: no transparent hop, no second transaction.",
            "needs": "nothing beyond a shielded address of your own",
            "depends_on_the_router": (
                "Whether a z-address is a valid recipient at all is the "
                "router's decision and it has changed before -- 1Click has "
                "answered 'recipient is not valid' to a zs1 and a u1 while "
                "quoting the same swap to a t-address. bridge_shielded_in "
                "detects that answer, reserves nothing, and returns the "
                "two-leg route instead."),
            "fallback": "transparent-then-shield: bridge to a t-address you "
                        "own, then shielded_shield moves it into the pool. Leg "
                        "one is public, and the response says so.",
            "verified": "Live, by tests/test_learn_bridge.py -- which quotes "
                        "for real and fails loudly the day the router stops "
                        "taking shielded recipients.",
        },
        "out": {
            "supported": bool(has_node),
            "route": "near-intents",
            "how": "The deposit address is an ordinary t-address, so leaving "
                   "the pool means spending a shielded note into it -- which "
                   "needs a Groth16 proof.",
            "needs": ("a proving backend: ZCASH_RPC_URL pointed at a "
                      "zcashd/zebrad node holding the spending key"),
            "without_it": "The bridge can still reserve the deposit address "
                          "and tell you exactly what to pay; you complete the "
                          "spend in a proving wallet (Zashi, Ywallet, zingo) "
                          "using the key from shielded_export.",
        },
        "maya": {
            "supported": False,
            "why": "Maya deposits carry a memo that identifies the swap, and "
                   "its ZEC inbound addresses are transparent. There is no "
                   "shielded recipient form in that protocol.",
        },
        "orchard": {
            "supported": True,
            "why": "Orchard notes are read here, so an Orchard receiver stays "
                   "in the address handed to the router. The rule is not "
                   "'Sapling only' but 'never advertise a pool we cannot "
                   "decrypt' -- shielded_recipient takes the readable set from "
                   "capabilities() and trims anything outside it.",
        },
    }


# ── EVM payment intents ─────────────────────────────────────────────────────
#
# Bridging *into* ZEC hands back a deposit address on the origin chain, and for
# most of those chains that chain is an EVM. This module cannot sign there --
# it holds Zcash keys, not Ethereum ones -- but a browser wallet can, so the
# missing piece is not a signer, it is an exact instruction: which chain, which
# contract, which calldata, to the zatoshi-equivalent unit.
#
# That is what payment() returns: a transaction object a wallet can be handed
# verbatim. Building it here rather than in the browser means the amount is
# converted with the same Decimal path that priced the quote, and the ERC-20
# calldata is checked against the same address validator as the recipient --
# a bridge deposit that is one wei short is a refund at best.

# chain code -> what a wallet needs to be pointed at it. Only chains whose id
# is certain are listed: a wrong chainId in wallet_addEthereumChain adds a fake
# network to someone's wallet, which is worse than saying "switch it yourself".
EVM_NETWORKS = {
    "eth":    (1,      "Ethereum",          "ETH",   "https://eth.llamarpc.com",              "https://etherscan.io"),
    "base":   (8453,   "Base",              "ETH",   "https://mainnet.base.org",              "https://basescan.org"),
    "arb":    (42161,  "Arbitrum One",      "ETH",   "https://arb1.arbitrum.io/rpc",          "https://arbiscan.io"),
    "op":     (10,     "OP Mainnet",        "ETH",   "https://mainnet.optimism.io",           "https://optimistic.etherscan.io"),
    "pol":    (137,    "Polygon",           "POL",   "https://polygon-rpc.com",               "https://polygonscan.com"),
    "bsc":    (56,     "BNB Smart Chain",   "BNB",   "https://bsc-dataseed.binance.org",      "https://bscscan.com"),
    "avax":   (43114,  "Avalanche C-Chain", "AVAX",  "https://api.avax.network/ext/bc/C/rpc", "https://snowtrace.io"),
    "gnosis": (100,    "Gnosis",            "XDAI",  "https://rpc.gnosischain.com",           "https://gnosisscan.io"),
    "scroll": (534352, "Scroll",            "ETH",   "https://rpc.scroll.io",                 "https://scrollscan.com"),
    "bera":   (80094,  "Berachain",         "BERA",  "https://rpc.berachain.com",             "https://berascan.com"),
}

# transfer(address,uint256)
ERC20_TRANSFER = "a9059cbb"


def erc20_transfer_data(to: str, base_units) -> str:
    """Calldata for an ERC-20 transfer, from a checksum-checked address."""
    if not _evm.is_valid_evm_address(to):
        raise BridgeError(f"{to!r} is not a valid EVM address")
    amount = int(base_units)
    if amount <= 0:
        raise BridgeError("transfer amount must be positive")
    return ("0x" + ERC20_TRANSFER
            + to.lower().removeprefix("0x").rjust(64, "0")
            + f"{amount:x}".rjust(64, "0"))


def payment(from_asset: str, amount, deposit_address: str) -> dict:
    """The exact EVM transaction that funds a bridge deposit address.

    `from_asset` is the origin asset of a quote ('eth:USDC', 'base:ETH'),
    `amount` its `amount_in`, `deposit_address` the address the router
    reserved. Returns a transaction a browser wallet can sign as-is.
    """
    src = resolve_asset(from_asset)
    chain = src["blockchain"]
    if chain not in EVM_CHAINS:
        raise BridgeError(
            f"{chain} is not an EVM chain, so an EVM wallet cannot pay this "
            f"deposit. Send {amount} {src['symbol']} from a {chain} wallet.")
    if chain not in EVM_NETWORKS:
        raise BridgeError(
            f"{chain} is EVM but this module does not carry a verified chain "
            "id for it. Point your wallet at that network yourself and send "
            f"{amount} {src['symbol']} to {deposit_address}.")
    if not _evm.is_valid_evm_address(deposit_address):
        raise BridgeError(
            f"{deposit_address!r} is not a valid {chain} address -- a bridge "
            "deposit address on an EVM chain always is")

    chain_id, name, native, rpc, explorer = EVM_NETWORKS[chain]
    units = to_base_units(amount, src["decimals"])
    contract = src.get("contractAddress")

    if contract:
        tx = {"to": _evm.to_checksum_address(contract), "value": "0x0",
              "data": erc20_transfer_data(deposit_address, units)}
        kind = "erc20"
    else:
        tx = {"to": _evm.to_checksum_address(deposit_address),
              "value": hex(int(units)), "data": "0x"}
        kind = "native"

    return {
        "asset": f"{chain}:{src['symbol']}",
        "symbol": src["symbol"],
        "decimals": src["decimals"],
        "kind": kind,
        "contract": _evm.to_checksum_address(contract) if contract else None,
        "chain": chain,
        "chain_name": name,
        "chain_id": chain_id,
        "chain_id_hex": hex(chain_id),
        "native_symbol": native,
        "amount": str(amount),
        "amount_base_units": units,
        "deposit_address": _evm.to_checksum_address(deposit_address),
        "tx": tx,
        "explorer_tx": f"{explorer}/tx/",
        "explorer_address": f"{explorer}/address/",
        # EIP-3085, for a wallet that has never seen this chain.
        "add_chain": {
            "chainId": hex(chain_id),
            "chainName": name,
            "nativeCurrency": {"name": native, "symbol": native, "decimals": 18},
            "rpcUrls": [rpc],
            "blockExplorerUrls": [explorer],
        },
        "note": (f"Send exactly {amount} {src['symbol']} on {name} to "
                 f"{deposit_address}. "
                 + ("This is an ERC-20 transfer: the transaction goes to the "
                    f"{src['symbol']} contract, not to the deposit address."
                    if contract else
                    "This is a plain native transfer.")),
    }
