"""
View-key scanning: finding your own outputs in a chain that hides them.

Monero has no address balance to look up. Every output pays a one-time key
P = Hs(8aR || i)G + B that reveals nothing about its owner. The only way to
know an output is yours is to try the derivation with your view key on every
output in every block.

That is exactly what this does, locally: blocks come from the daemon, the view
key never leaves the host. Since HF15 each output also carries a one-byte view
tag, so 255 of every 256 outputs are dismissed with a single Keccak instead of
a scalar multiplication -- which is what makes Python fast enough to do this at
all.

What a scan can tell you and what it cannot:

  * found outputs and their amounts -- yes, that is what the view key is for;
  * whether you have since *spent* them -- no. That needs key images, which
    need the private spend key and the hash-to-point map. Use a
    monero-wallet-rpc wallet (walletrpc.py) for a true unspent balance.

So `received` here is a lower bound on what arrived, not a spendable balance,
and every result says so.
"""

import json
import time

try:
    from . import crypto
    from .daemon import DaemonError, xmr
except ImportError:  # loaded as a loose module by the mod runtime
    import crypto
    from daemon import DaemonError, xmr


class ScanError(Exception):
    pass


# ── Transaction parsing ─────────────────────────────────────────────────────

def parse_extra(extra) -> dict:
    """Pull the transaction public key(s) and payment id out of tx_extra.

    tx_extra is an untyped byte soup with a tag-length convention that is not
    strictly enforced by consensus, so a malformed field must not take the
    whole scan down -- it just ends the parse.
    """
    data = bytes(extra or [])
    out = {"tx_pubkey": None, "additional_pubkeys": [], "payment_id": None,
           "encrypted_payment_id": None}
    pos = 0
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == 0x00:                       # padding, runs to the end
            break
        if tag == 0x01:                       # tx public key
            if pos + 32 > len(data):
                break
            key = data[pos:pos + 32]
            pos += 32
            if out["tx_pubkey"] is None:
                out["tx_pubkey"] = key
        elif tag == 0x02:                     # extra nonce (payment id lives here)
            if pos >= len(data):
                break
            length = data[pos]
            pos += 1
            nonce = data[pos:pos + length]
            pos += length
            if nonce[:1] == b"\x00" and len(nonce) == 33:
                out["payment_id"] = nonce[1:].hex()
            elif nonce[:1] == b"\x01" and len(nonce) == 9:
                out["encrypted_payment_id"] = nonce[1:].hex()
        elif tag == 0x03:                     # merge mining tag
            try:
                _, pos = crypto.read_varint(data, pos)
            except crypto.CryptoError:
                break
            pos += 32
        elif tag == 0x04:                     # additional pubkeys (subaddresses)
            try:
                count, pos = crypto.read_varint(data, pos)
            except crypto.CryptoError:
                break
            for _ in range(count):
                if pos + 32 > len(data):
                    break
                out["additional_pubkeys"].append(data[pos:pos + 32])
                pos += 32
        else:
            break                              # unknown tag: stop, do not guess
    return out


def output_keys(body: dict) -> list:
    """[(one-time key, view tag or None, plaintext amount)] for each output."""
    rows = []
    for vout in body.get("vout") or []:
        target = vout.get("target") or {}
        tagged = target.get("tagged_key")
        if tagged:
            rows.append((tagged.get("key"), tagged.get("view_tag"), vout.get("amount", 0)))
        else:
            rows.append((target.get("key"), None, vout.get("amount", 0)))
    return rows


def _encrypted_amount(body: dict, index: int):
    ecdh = ((body.get("rct_signatures") or {}).get("ecdhInfo") or [])
    if index >= len(ecdh):
        return None
    return (ecdh[index] or {}).get("amount")


# ── Scanning ────────────────────────────────────────────────────────────────

def scan_transaction(body: dict, view_sec: bytes, spend_keys: dict) -> list:
    """Outputs in one transaction that belong to us.

    `spend_keys` maps a public spend key (hex) to a label, and holds the main
    address plus every subaddress the wallet knows. Recognition works by
    recovering the candidate spend key from the output rather than by
    predicting the output key, which is what makes one pass cover every
    subaddress at the same cost.
    """
    extra = parse_extra(body.get("extra"))
    if not extra["tx_pubkey"]:
        return []

    outs = output_keys(body)
    additional = extra["additional_pubkeys"]
    use_additional = len(additional) == len(outs) and len(outs) > 0

    try:
        main_derivation = crypto.generate_key_derivation(extra["tx_pubkey"], view_sec)
    except crypto.CryptoError:
        return []

    found = []
    for index, (key_hex, view_tag, plain_amount) in enumerate(outs):
        if not key_hex:
            continue
        for derivation in ([main_derivation] +
                           ([_safe_derivation(additional[index], view_sec)]
                            if use_additional else [])):
            if derivation is None:
                continue
            if view_tag:
                if crypto.derive_view_tag(derivation, index) != int(view_tag, 16):
                    continue
            try:
                candidate = _recover_spend_key(derivation, index, bytes.fromhex(key_hex))
            except (crypto.CryptoError, ValueError):
                continue
            label = spend_keys.get(candidate.hex())
            if label is None:
                continue

            amount = plain_amount or 0
            encrypted = _encrypted_amount(body, index)
            if encrypted:
                amount = crypto.decode_amount(derivation, index,
                                              bytes.fromhex(encrypted)[:8])
            found.append({
                "output_index": index, "one_time_key": key_hex,
                "amount": amount, "amount_xmr": xmr(amount),
                "to": label,
                "payment_id": extra["payment_id"] or extra["encrypted_payment_id"],
                "coinbase": bool(plain_amount) and not encrypted,
            })
            break
    return found


def _safe_derivation(pubkey: bytes, view_sec: bytes):
    try:
        return crypto.generate_key_derivation(pubkey, view_sec)
    except crypto.CryptoError:
        return None


def _recover_spend_key(derivation: bytes, index: int, one_time_key: bytes) -> bytes:
    """B = P - Hs(D||i)G."""
    scalar = crypto.derivation_to_scalar(derivation, index)
    shared_point = crypto.scalarmult_base(int.from_bytes(scalar, "little"))
    negated = (crypto.Q - shared_point[0] % crypto.Q, shared_point[1], shared_point[2],
               (crypto.Q - shared_point[3] % crypto.Q))
    return crypto.encode_point(crypto._add(crypto.decode_point(one_time_key), negated))


def spend_key_table(view_sec: bytes, spend_pub: bytes, network: str = "mainnet",
                    accounts: int = 1, subaddresses: int = 0) -> dict:
    """Public spend keys for the main address and a window of subaddresses.

    Subaddresses are matched by their own spend key, so every one we want to
    recognise has to be derived up front -- there is no way to test an
    unbounded range.
    """
    table = {spend_pub.hex(): "main"}
    for major in range(max(1, accounts)):
        for minor in range(subaddresses + 1):
            if major == 0 and minor == 0:
                continue
            m = crypto.hash_to_scalar(b"SubAddr\x00" + view_sec +
                                      crypto.varint(major) + crypto.varint(minor))
            point = crypto._add(crypto.decode_point(spend_pub),
                                crypto.scalarmult_base(int.from_bytes(m, "little")))
            table[crypto.encode_point(point).hex()] = f"subaddress {major}/{minor}"
    return table


def self_test() -> dict:
    """Play the sender, then check the scanner finds what was sent.

    A scanner that never matches looks identical to a wallet with no funds, so
    "it ran without error" proves nothing. Here an output is constructed the
    way a real sender constructs one -- R = rG, the shared secret from the
    sender's side, the masked amount, the view tag -- and the scanner has to
    recover it from the receiver's side, to the main address and to a
    subaddress, while ignoring an output built for somebody else.
    """
    seed = crypto.sc_reduce32(crypto.keccak256(b"monero-scan-self-test"))
    keys = crypto.keys_from_seed(seed)
    view_sec = bytes.fromhex(keys["view_secret_key"])
    spend_pub = bytes.fromhex(keys["spend_public_key"])
    table = spend_key_table(view_sec, spend_pub, subaddresses=2)

    def send_to(dest_spend, dest_view, r, index, amount, is_subaddress):
        big_r = (crypto.encode_point(crypto.scalarmult(
                    crypto.decode_point(dest_spend), int.from_bytes(r, "little")))
                 if is_subaddress else crypto.secret_to_public(r))
        derivation = crypto.encode_point(crypto.mul8(crypto.scalarmult(
            crypto.decode_point(dest_view), int.from_bytes(r, "little"))))
        one_time = crypto.derive_public_key(derivation, index, dest_spend)
        tag = crypto.derive_view_tag(derivation, index)
        mask = crypto.keccak256(
            b"amount" + crypto.derivation_to_scalar(derivation, index))[:8]
        encrypted = bytes(x ^ y for x, y in
                          zip(int.to_bytes(amount, 8, "little"), mask))
        return {
            "version": 2, "unlock_time": 0,
            "vin": [{"key": {"amount": 0, "key_offsets": [1, 2], "k_image": "00" * 32}}],
            "vout": [{"amount": 0, "target": {"tagged_key": {
                "key": one_time.hex(), "view_tag": "%02x" % tag}}}],
            "extra": list(bytes([1]) + big_r),
            "rct_signatures": {"type": 6, "txnFee": 30000000,
                               "ecdhInfo": [{"amount": encrypted.hex()}]},
        }

    r = crypto.sc_reduce32(crypto.keccak256(b"r"))
    amount = 250_000_000_000
    main_hits = scan_transaction(
        send_to(spend_pub, bytes.fromhex(keys["view_public_key"]), r, 0, amount, False),
        view_sec, table)

    m = crypto.hash_to_scalar(b"SubAddr\x00" + view_sec +
                              crypto.varint(0) + crypto.varint(2))
    point = crypto._add(crypto.decode_point(spend_pub),
                        crypto.scalarmult_base(int.from_bytes(m, "little")))
    sub_spend = crypto.encode_point(point)
    sub_view = crypto.encode_point(crypto.scalarmult(point, int.from_bytes(view_sec, "little")))
    sub_hits = scan_transaction(
        send_to(sub_spend, sub_view, r, 0, 7_500_000_000, True), view_sec, table)

    stranger = crypto.keys_from_seed(crypto.keccak256(b"somebody-else"))
    other_hits = scan_transaction(
        send_to(bytes.fromhex(stranger["spend_public_key"]),
                bytes.fromhex(stranger["view_public_key"]), r, 0, 1, False),
        view_sec, table)

    found_main = len(main_hits) == 1 and main_hits[0]["amount"] == amount
    found_sub = len(sub_hits) == 1 and sub_hits[0]["to"] == "subaddress 0/2"
    return {
        "ok": found_main and found_sub and not other_hits,
        "finds_main_address_output": found_main,
        "finds_subaddress_output": found_sub,
        "ignores_other_peoples_outputs": not other_hits,
        "decoded_amount_xmr": main_hits[0]["amount_xmr"] if main_hits else None,
    }


def scan_blocks(daemon, view_sec: bytes, spend_pub: bytes, start_height: int,
                blocks: int = 20, network: str = "mainnet", accounts: int = 1,
                subaddresses: int = 0, budget_seconds: float = None) -> dict:
    """Scan a range of blocks for outputs we own.

    Bounded on purpose. A full chain scan is 3.7 million blocks; at the rate
    this reports it would take weeks, so the caller picks a window and gets
    told exactly which one was covered.
    """
    if not daemon.is_own_node:
        note = ("Scanning through a public node: it learns which blocks you "
                "requested, though not what you found. Set MONERO_DAEMON_URL "
                "to your own monerod for a private scan.")
    else:
        note = None

    table = spend_key_table(view_sec, spend_pub, network, accounts, subaddresses)
    tip = daemon.tip_height()
    start = max(0, int(start_height))
    end = min(tip, start + max(1, int(blocks)))

    started = time.time()
    found, scanned, txs_seen, errors = [], 0, 0, []

    for height in range(start, end):
        if budget_seconds and time.time() - started > budget_seconds:
            break
        # One get_block carries the header, the miner transaction and the list
        # of tx hashes -- worth doing by hand rather than through daemon.block(),
        # which would cost a second round trip per block.
        try:
            raw_block = daemon.rpc("get_block", {"height": height})
            header = raw_block.get("block_header") or {}
            parsed = json.loads(raw_block["json"]) if raw_block.get("json") else {}
        except (DaemonError, ValueError, KeyError) as e:
            errors.append({"height": height, "error": str(e)})
            continue
        scanned += 1

        bodies = []
        if parsed.get("miner_tx"):
            bodies.append((header.get("miner_tx_hash"), parsed["miner_tx"]))

        block = {"timestamp": header.get("timestamp")}
        hashes = [h for h in (parsed.get("tx_hashes") or []) if h]
        if hashes:
            try:
                for tx in daemon.transactions(hashes):
                    if tx.get("as_json"):
                        bodies.append((tx.get("tx_hash"), json.loads(tx["as_json"])))
            except (DaemonError, ValueError) as e:
                errors.append({"height": height, "error": str(e)})

        for txid, body in bodies:
            txs_seen += 1
            for hit in scan_transaction(body, view_sec, table):
                found.append(dict(hit, height=height, tx_hash=txid,
                                  timestamp=block.get("timestamp")))

    elapsed = time.time() - started
    total = sum(f["amount"] for f in found)
    return {
        "from_height": start, "to_height": start + scanned - 1 if scanned else start,
        "tip_height": tip,
        "blocks_scanned": scanned, "transactions_scanned": txs_seen,
        "outputs_found": len(found), "outputs": found,
        "received": total, "received_xmr": xmr(total),
        "keys_watched": len(table),
        "seconds": round(elapsed, 2),
        "blocks_per_second": round(scanned / elapsed, 2) if elapsed else None,
        "eta_full_chain_hours": round(tip / (scanned / elapsed) / 3600, 1)
                                if elapsed and scanned else None,
        "errors": errors or None,
        "node_note": note,
        "caveat": "This is what arrived, not what is left: a view key cannot "
                  "tell whether an output has since been spent. For a "
                  "spendable balance use a monero-wallet-rpc wallet.",
    }
