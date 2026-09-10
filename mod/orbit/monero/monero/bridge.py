"""
Moving value between XMR and other chains.

Monero cannot be bridged the way most assets can, and it is worth being
precise about why. A bridge normally works because a contract on the origin
chain can watch a deposit and prove it happened. Monero has no contracts and
no public amounts, so there is nothing for a bridge contract to observe. The
two solver networks the rest of this fleet uses do not list XMR at all --
NEAR Intents covers 35 chains and Monero is not one of them, and Maya's
pool list is ADA/ARB/BTC/DASH/ETH/THOR/ZEC. Wrapped XMR on other chains
exists but requires trusting a custodian with the peg.

What is actually available, and what this module wires up, is an instant
swap provider: you send XMR to an address they control and they pay the
destination chain from their own float.

That means **custody**, not a trustless bridge, and the module says so in
every response. The provider here is Exolix, chosen because it quotes and
creates orders without an API key or an account, supports 630 assets, and
publishes per-network address rules that let a mistyped recipient be caught
locally before any funds move.

Nothing here can spend from your wallet on its own. `swap_start` reserves a
deposit address; paying it is a separate, explicit step.
"""

import re
import time

import requests

try:
    from . import crypto
except ImportError:  # loaded as a loose module by the mod runtime
    import crypto

EXOLIX = "https://exolix.com/api/v2"

_cache = {"networks": {}, "currencies": None, "at": 0}
_TTL = 600


class BridgeError(Exception):
    pass


def _get(path: str, params: dict = None):
    try:
        r = requests.get(f"{EXOLIX}/{path.lstrip('/')}", params=params, timeout=30)
    except requests.RequestException as e:
        raise BridgeError(f"swap provider unreachable: {e}")
    try:
        payload = r.json()
    except ValueError:
        raise BridgeError(f"swap provider returned non-JSON for {path}")
    if r.status_code >= 400:
        raise BridgeError(f"swap provider: {payload.get('error') or payload}")
    return payload


def _post(path: str, body: dict):
    try:
        r = requests.post(f"{EXOLIX}/{path.lstrip('/')}", json=body, timeout=45)
    except requests.RequestException as e:
        raise BridgeError(f"swap provider unreachable: {e}")
    try:
        payload = r.json()
    except ValueError:
        raise BridgeError(f"swap provider returned non-JSON for {path}")
    if r.status_code >= 400:
        detail = payload.get("error") or payload.get("message") or payload
        raise BridgeError(f"swap provider rejected the order: {detail}")
    return payload


# ── Asset catalog ───────────────────────────────────────────────────────────

def assets(search: str = None, limit: int = 100) -> dict:
    """Assets XMR can be swapped against."""
    payload = _get("currencies", {"search": search, "size": int(limit), "page": 1})
    return {"count": payload.get("count"),
            "assets": [{"code": c.get("code"), "name": c.get("name")}
                       for c in (payload.get("data") or [])]}


def networks(code: str) -> list:
    """The chains an asset lives on, with the address rules for each."""
    code = (code or "").upper()
    cached = _cache["networks"].get(code)
    if cached and time.time() - cached[0] < _TTL:
        return cached[1]
    payload = _get(f"currencies/{code}/networks")
    rows = [{"network": n.get("network"), "name": n.get("name"),
             "short_name": n.get("shortName"), "default": n.get("isDefault"),
             "native": n.get("isNative"), "precision": n.get("precision"),
             "memo_needed": n.get("memoNeeded"), "memo_name": n.get("memoName"),
             "address_regex": n.get("addressRegex"),
             "contract": n.get("contract")}
            for n in (payload or [])]
    if not rows:
        raise BridgeError(f"{code} is not supported by the swap provider")
    _cache["networks"][code] = (time.time(), rows)
    return rows


def _resolve(spec: str) -> dict:
    """'BTC', 'USDT:TRX', 'eth:USDC' -> one (asset, network) pair."""
    text = (spec or "").strip()
    if not text:
        raise BridgeError("no asset given")
    net, _, code = text.rpartition(":")
    code = code.upper()
    rows = networks(code)
    if net:
        wanted = net.upper()
        match = [n for n in rows
                 if wanted in (str(n["network"]).upper(),
                               str(n["short_name"]).upper(),
                               str(n["name"]).upper())]
        if not match:
            options = ", ".join(f"{n['network']}:{code}" for n in rows[:12])
            raise BridgeError(f"{code} is not on {net!r}; try one of {options}")
        chosen = match[0]
    else:
        chosen = next((n for n in rows if n["default"]), rows[0])
        if len(rows) > 1 and not chosen["default"]:
            options = ", ".join(f"{n['network']}:{code}" for n in rows[:12])
            raise BridgeError(f"{code} exists on several chains; qualify it: {options}")
    return {"code": code, **chosen}


def _check_address(asset: dict, address: str, what: str):
    """Reject a bad recipient here, where it costs nothing."""
    if not address:
        raise BridgeError(f"a {what} address is required")
    if asset["code"] == "XMR":
        if not crypto.is_valid_address(address):
            raise BridgeError(
                f"{what} address is not a valid Monero address "
                f"(checksum or length wrong): {address[:16]}...")
        return
    pattern = asset.get("address_regex")
    if pattern:
        try:
            if not re.match(pattern, address):
                raise BridgeError(
                    f"{what} address does not look like a valid "
                    f"{asset['name']} address")
        except re.error:
            pass


# ── Quote / order / track ───────────────────────────────────────────────────

def quote(from_asset: str, to_asset: str, amount, rate_type: str = "float") -> dict:
    """Price a swap. Reserves nothing and costs nothing."""
    src, dst = _resolve(from_asset), _resolve(to_asset)
    if (src["code"], src["network"]) == (dst["code"], dst["network"]):
        raise BridgeError("origin and destination are the same asset")
    payload = _get("rate", {
        "coinFrom": src["code"], "networkFrom": src["network"],
        "coinTo": dst["code"], "networkTo": dst["network"],
        "amount": amount, "rateType": rate_type})
    if payload.get("message"):
        raise BridgeError(f"swap provider: {payload['message']}")
    return {
        "route": "exolix",
        "from": f"{src['network']}:{src['code']}",
        "to": f"{dst['network']}:{dst['code']}",
        "amount_in": payload.get("fromAmount"),
        "amount_out": payload.get("toAmount"),
        "rate": payload.get("rate"),
        "min_amount": payload.get("minAmount"),
        "max_amount": payload.get("maxAmount"),
        "withdraw_min": payload.get("withdrawMin"),
        "rate_type": rate_type,
        "custodial": True,
        "note": "Quote only -- nothing is reserved. A float rate is settled "
                "when your deposit confirms, so the final amount can move.",
    }


def swap_start(from_asset: str, to_asset: str, amount, recipient: str,
               refund_to: str, rate_type: str = "float",
               recipient_memo: str = None, refund_memo: str = None) -> dict:
    """Reserve a real deposit address.

    Nothing moves until you fund the returned address yourself. The provider
    takes custody of what you send: if it disappears, this module cannot get
    it back. Amounts outside the quoted min/max are refunded, sometimes minus
    a network fee.
    """
    src, dst = _resolve(from_asset), _resolve(to_asset)
    _check_address(dst, recipient, "recipient")
    _check_address(src, refund_to, "refund")
    if dst.get("memo_needed") and not recipient_memo:
        raise BridgeError(
            f"{dst['name']} requires a {dst.get('memo_name') or 'memo'}; "
            "without it the exchange cannot credit the payment")

    body = {
        "coinFrom": src["code"], "networkFrom": src["network"],
        "coinTo": dst["code"], "networkTo": dst["network"],
        "amount": amount, "withdrawalAddress": recipient,
        "refundAddress": refund_to, "rateType": rate_type,
    }
    if recipient_memo:
        body["withdrawalExtraId"] = recipient_memo
    if refund_memo:
        body["refundExtraId"] = refund_memo

    payload = _post("transactions", body)
    deposit = payload.get("depositAddress")
    if not deposit:
        raise BridgeError("swap provider returned no deposit address")
    return {
        "route": "exolix", "order_id": payload.get("id"),
        "deposit_address": deposit,
        "deposit_memo": payload.get("depositExtraId"),
        "from": f"{src['network']}:{src['code']}",
        "to": f"{dst['network']}:{dst['code']}",
        "amount_in": payload.get("amount"),
        "amount_out": payload.get("amountTo"),
        "rate": payload.get("rate"), "rate_type": rate_type,
        "recipient": recipient, "refund_to": refund_to,
        "status": payload.get("status"),
        "custodial": True,
        "instructions": (
            f"Send {payload.get('amount')} {src['code']} on {src['network']} to "
            f"{deposit}. About {payload.get('amountTo')} {dst['code']} then goes "
            f"to {recipient}. Track it with swap_status order_id="
            f"{payload.get('id')}."),
        "warning": "The provider holds your funds between the deposit and the "
                   "payout. This is a custodial swap, not a trustless bridge.",
    }


def swap_status(order_id: str) -> dict:
    payload = _get(f"transactions/{order_id}")
    return {
        "route": "exolix", "order_id": payload.get("id"),
        "status": payload.get("status"),
        "deposit_address": payload.get("depositAddress"),
        "amount_in": payload.get("amount"), "amount_out": payload.get("amountTo"),
        "hash_in": payload.get("hashIn", {}).get("hash")
                   if isinstance(payload.get("hashIn"), dict) else payload.get("hashIn"),
        "hash_out": payload.get("hashOut", {}).get("hash")
                    if isinstance(payload.get("hashOut"), dict) else payload.get("hashOut"),
        "created_at": payload.get("createdAt"),
        "recipient": payload.get("withdrawalAddress"),
    }


def routes() -> dict:
    """What bridging XMR actually looks like, including what does not work."""
    try:
        xmr_networks = networks("XMR")
        available, error = True, None
    except BridgeError as e:
        xmr_networks, available, error = [], False, str(e)
    return {
        "available": available,
        # Only set when it is real: callers treat a truthy `error` as a failure.
        **({"error": error} if error else {}),
        "provider": {"name": "exolix", "type": "custodial instant exchange",
                     "api_key_required": False, "assets": 630},
        "xmr_networks": xmr_networks,
        "not_available": {
            "near-intents": "does not list XMR (35 chains, Monero is not one)",
            "maya": "ZEC and BTC pools, no XMR pool",
            "trustless_bridge": "impossible as such: Monero has no contracts and "
                                "no public amounts for a bridge to observe",
        },
        "note": "Every route out of Monero involves trusting somebody with "
                "custody for a few minutes. Size accordingly.",
    }
