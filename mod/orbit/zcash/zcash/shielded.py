"""
The shielded pool as a wallet sees it: keys from a seed, notes off the chain.

`sapling.py` is the cryptography and `bundles.py` reads transactions; this is
the layer the module's functions call. It answers three questions:

  * what are this wallet's shielded addresses and viewing keys?
  * which notes in these transactions are ours, and what do they say?
  * how much do we hold -- and, where the data allows it, how much is unspent?

**Spend detection is not free.** A note is spent when its nullifier appears on
chain, and the nullifier depends on the note's position in the Sapling
commitment tree. That position can only be known by counting every Sapling
output from the pool's activation, which needs a node (`ZCASH_RPC_URL`, from
which `z_gettreestate` gives the tree size at a height). Without one, notes
are reported as received and `spent` stays None -- unknown, not false.

**Spending is not possible here.** Creating a shielded spend needs a Groth16
proof (Sapling) or a Halo 2 proof (Orchard). `export_keys` prints the ZIP-32
extended spending key and the mnemonic's unified viewing keys so a proving
wallet (Zashi, Ywallet, zcashd, zingo) can spend the same notes, and
`node_send` hands the job to a node when one is configured.

**Both shielded pools are here.** Sapling and Orchard keys come from the same
seed, and a unified address advertises both receivers. Orchard is the easier
of the two to watch: its nullifier needs no note position, so a scan that sees
the actions can say which of its own notes are spent with no node at all. What
it costs instead is reach -- the public explorer serializes Sapling bundles
into its transaction rows but not Orchard actions, so an Orchard scan has to
pull each candidate transaction's raw bytes (see `ORCHARD_EXPLORER_MAX_TXS`).
"""

import time

try:
    from . import bundles as _bundles
    from . import keys as _keys
    from . import orchard as _orchard
    from . import sapling as _sapling
except ImportError:                     # loaded as loose modules
    import bundles as _bundles
    import keys as _keys
    import orchard as _orchard
    import sapling as _sapling


class ShieldedError(Exception):
    pass


# ── Keys ────────────────────────────────────────────────────────────────────

def account_key(mnemonic: str, passphrase: str = "", account: int = 0):
    """The ZIP-32 Sapling account key for a BIP39 mnemonic.

    The seed is the same one the transparent addresses come from, so one
    mnemonic backs up both pools -- exactly as in every other Zcash wallet.
    """
    seed = _keys.mnemonic_to_seed(mnemonic, passphrase or "")
    return _sapling.ExtendedSpendingKey.from_seed(seed, account)


def orchard_account_key(mnemonic: str, passphrase: str = "", account: int = 0):
    """The ZIP-32 Orchard account key -- same seed, same account, same path.

    Orchard's ZIP-32 is hardened-only and its own BLAKE2b personalization, so
    the key is not the Sapling one; but m/32'/133'/account' is the same path
    every other wallet uses, which is what makes a mnemonic portable.
    """
    seed = _keys.mnemonic_to_seed(mnemonic, passphrase or "")
    return _orchard.ExtendedSpendingKey.from_seed(seed, account)


def derive_address(mnemonic: str, passphrase: str = "", account: int = 0,
                   index: int = 0, transparent: str = None,
                   orchard: bool = True) -> dict:
    """A shielded receive address, with the unified address that wraps it.

    The unified address carries an Orchard receiver as well as the Sapling one
    unless `orchard=False`. Both are derived from this seed and both can be
    read back by `shielded_scan`, so a payment to either is findable; a sender
    whose wallet knows only Sapling still has a receiver it understands.
    """
    xsk = account_key(mnemonic, passphrase, account)
    addr, used = xsk.fvk().address_at(index)
    t_hash = None
    if transparent:
        info = _keys.decode_address(transparent)
        if info["type"] == "p2pkh":
            t_hash = info["hash160"]
    receivers = ["sapling"] + (["p2pkh"] if t_hash else [])
    o_addr = None
    if orchard:
        o_addr = orchard_account_key(mnemonic, passphrase,
                                     account).fvk().address(used)
        receivers = ["orchard"] + receivers
        unified = o_addr.encode(sapling_raw=addr.raw, transparent_p2pkh=t_hash)
    else:
        unified = addr.unified(t_hash)
    return {
        "account": account,
        "diversifier_index": used,
        "address": addr.encode(),
        "unified_address": unified,
        "unified_receivers": receivers,
        "orchard_receiver": o_addr.raw.hex() if o_addr else None,
        "pool": "orchard+sapling" if orchard else "sapling",
    }


def export_keys(mnemonic: str, passphrase: str = "", account: int = 0) -> dict:
    """Everything another wallet needs to view -- or spend -- this account."""
    xsk = account_key(mnemonic, passphrase, account)
    fvk = xsk.fvk()
    oxsk = orchard_account_key(mnemonic, passphrase, account)
    ofvk = oxsk.fvk()
    return {
        "account": account,
        "path": f"m/32'/{_sapling.COIN_TYPE}'/{account}'",
        "extended_spending_key": xsk.encode(),
        "extended_full_viewing_key": xsk.encode_fvk(),
        "incoming_viewing_key": fvk.ivk.to_bytes(32, "little").hex(),
        "outgoing_viewing_key": fvk.ovk.hex(),
        "default_address": fvk.address(0).encode(),
        "unified_full_viewing_key": _orchard.encode_unified_fvk(
            ofvk, fvk.ak + fvk.nk + fvk.ovk + (fvk.dk or b"")),
        "unified_incoming_viewing_key": _orchard.encode_unified_ivk(
            ofvk.incoming(),
            (fvk.dk or b"") + fvk.ivk.to_bytes(32, "little")),
        "orchard": {
            "full_viewing_key": ofvk.to_bytes().hex(),
            "incoming_viewing_key": ofvk.ivk.to_bytes(32, "little").hex(),
            "outgoing_viewing_key": ofvk.ovk.hex(),
            "default_address": ofvk.address(0).encode(),
            "note": ("Orchard has no bech32 spending key of its own: a wallet "
                     "imports the mnemonic (or the unified spending key it "
                     "derives) to spend these notes."),
        },
        "warning": (
            "The extended spending key spends every Sapling note in this "
            "account, and the mnemonic spends the Orchard ones too. Import "
            "into Zashi, Ywallet, zingo or zcashd to send shielded ZEC -- "
            "this module can read the notes but cannot prove a spend."),
    }


def viewing_keys(mnemonic: str, passphrase: str = "", account: int = 0) -> dict:
    """(ivk, ovk, nk) for scanning -- no spend authority."""
    fvk = account_key(mnemonic, passphrase, account).fvk()
    ofvk = orchard_account_key(mnemonic, passphrase, account).fvk()
    return {"ivk": fvk.ivk, "ovk": fvk.ovk, "nk": fvk.nk, "fvk": fvk,
            "orchard_fvk": ofvk}


def keys_from_viewing_key(key: str) -> dict:
    """Accept a viewing key for watch-only scans.

    Either a Sapling `zxviews...` extended full viewing key, or a ZIP-316
    unified `uview1...` key -- which is how a wallet hands over both pools at
    once. A unified key without a Sapling item still scans Orchard.
    """
    k = (key or "").strip()
    if k.startswith(_orchard.HRP_UNIFIED_FVK + "1"):
        items = _orchard.decode_unified_fvk(k)
        sap = items["sapling"]
        fvk = None
        if sap:
            if len(sap) != 128:
                raise ShieldedError("unified key holds a malformed Sapling item")
            fvk = _sapling.FullViewingKey(sap[:32], sap[32:64], sap[64:96],
                                          sap[96:])
        if fvk is None and items["orchard"] is None:
            raise ShieldedError(
                "this unified full viewing key has neither a Sapling nor an "
                "Orchard item, so there is nothing to scan with")
        return {"ivk": fvk.ivk if fvk else None,
                "ovk": fvk.ovk if fvk else None,
                "nk": fvk.nk if fvk else None, "fvk": fvk,
                "orchard_fvk": items["orchard"]}
    fvk = _sapling.decode_extended_full_viewing_key(k)
    return {"ivk": fvk.ivk, "ovk": fvk.ovk, "nk": fvk.nk, "fvk": fvk,
            "orchard_fvk": None}


# ── Scanning ────────────────────────────────────────────────────────────────

def scan_output_list(outputs, fvk, txid=None, height=None, position_base=None,
                     spends=None, bundle=None) -> dict:
    """Trial-decrypt a list of Sapling outputs with one account's keys."""
    result = {"txid": txid, "height": height, "notes": [],
              "nullifiers_spent": list(spends or []),
              "sapling_outputs": len(outputs)}
    if bundle is not None:
        result["bundles"] = bundle
    holder = _bundles.Bundles(0, 0, "outputs")
    holder.sapling_outputs = list(outputs)
    for hit in _bundles.scan_outputs(holder, ivks=[fvk.ivk], ovks=[fvk.ovk]):
        note, out = hit["note"], hit["output"]
        entry = note.to_dict()
        entry.update({
            "direction": hit["direction"],
            "output_index": out.index,
            "txid": txid,
            "height": height,
            "commitment": out.cmu.hex(),
        })
        if position_base is not None:
            position = position_base + out.index
            entry["position"] = position
            entry["nullifier"] = note.nullifier(fvk.nk, position).hex()
        result["notes"].append(entry)
    return result


def scan_action_list(actions, ofvk, txid=None, height=None,
                     result: dict = None) -> dict:
    """Trial-decrypt a list of Orchard actions with one account's keys.

    Every note found gets its nullifier straight away: Orchard derives it from
    the note and the action's own rho, with no commitment tree in sight. That
    is why an Orchard balance can be honest about spentness where a Sapling
    one, without a node, cannot.
    """
    result = result if result is not None else {
        "txid": txid, "height": height, "notes": [], "nullifiers_spent": []}
    result.setdefault("notes", [])
    result["orchard_actions"] = len(actions)
    holder = _bundles.Bundles(0, 0, "actions")
    holder.orchard_actions = list(actions)
    result["orchard_nullifiers"] = holder.orchard_nullifiers
    for hit in _bundles.scan_actions(holder, ivks=[ofvk.ivk], ovks=[ofvk.ovk]):
        note, action = hit["note"], hit["action"]
        entry = note.to_dict()
        entry.update({
            "direction": hit["direction"],
            "output_index": action.index,
            "txid": txid,
            "height": height,
            "commitment": action.cmx.hex(),
            "nullifier": note.nullifier(ofvk.nk).hex(),
        })
        result["notes"].append(entry)
    return result


def scan_raw_transaction(raw, fvk, txid: str = None, height: int = None,
                         position_base: int = None, orchard_fvk=None) -> dict:
    """Trial-decrypt one serialized transaction, in both shielded pools."""
    parsed = _bundles.parse(raw)
    if parsed.layout == "unknown":
        return {
            "txid": txid, "height": height, "notes": [],
            "nullifiers_spent": [], "bundles": parsed.to_dict(),
            "warning": (
                f"transaction version {parsed.version} uses a shielded layout "
                f"this module does not know; its shielded outputs were not "
                f"examined"),
        }
    out = scan_output_list(parsed.sapling_outputs, fvk, txid, height,
                           position_base, parsed.sapling_spends,
                           parsed.to_dict()) if fvk is not None else {
        "txid": txid, "height": height, "notes": [],
        "nullifiers_spent": list(parsed.sapling_spends),
        "sapling_outputs": len(parsed.sapling_outputs),
        "bundles": parsed.to_dict()}
    if orchard_fvk is not None:
        scan_action_list(parsed.orchard_actions, orchard_fvk, txid, height, out)
    return out


def scan_explorer_row(row: dict, fvk, orchard_fvk=None, raw=None) -> dict:
    """Scan one transaction as the public explorer serves it.

    The explorer's row carries the Sapling bundle but never the Orchard one,
    so an Orchard scan of the same transaction needs its raw bytes -- pass
    them in as `raw` and the actions are read from there.
    """
    if raw is not None and orchard_fvk is not None:
        scan = scan_raw_transaction(raw, fvk, row.get("hash"),
                                    row.get("block_id"),
                                    orchard_fvk=orchard_fvk)
        return scan
    outputs = _bundles.outputs_from_explorer(row.get("shielded_output_raw"))
    spends = [_bundles.explorer_hash(s["nullifier"]).hex()
              for s in (row.get("shielded_input_raw") or []) if s.get("nullifier")]
    scan = scan_output_list(outputs, fvk, row.get("hash"), row.get("block_id"),
                            None, spends)
    scan["bundles"] = {
        "version": row.get("version"),
        "sapling_outputs": len(outputs),
        "sapling_spends": len(spends),
        "value_balance_zatoshi": row.get("shielded_value_delta"),
        "layout": "explorer",
    }
    return scan


def scan_transactions(items, fvk) -> list:
    """items: [(txid, raw, height)] -> per-transaction scan results."""
    out = []
    for txid, raw, height in items:
        try:
            out.append(scan_raw_transaction(raw, fvk, txid, height))
        except _bundles.UnknownLayout as e:
            out.append({"txid": txid, "height": height, "notes": [],
                        "error": str(e)})
    return out


def summarize(scans: list, spent_nullifiers=None, positions_known=False,
              pools=("sapling",), orchard_nullifiers=None) -> dict:
    """Roll per-transaction scans into a balance.

    `positions_known` is a property of the scan, not of the notes it happened
    to find: a scan that found nothing still has to say whether it *could*
    have told a spent note from an unspent one. It applies to Sapling only --
    an Orchard note carries everything its nullifier needs, so the question
    there is not "do we know the position" but "did this scan see the block
    that spent it", which is bounded by the scanned range.
    """
    spent_nullifiers = set(spent_nullifiers or [])
    orchard_spent = set(orchard_nullifiers or [])
    for s in scans:
        orchard_spent.update(s.get("orchard_nullifiers") or [])
    notes, received, sent, unspent = [], 0, 0, 0
    per_pool = {}
    for s in scans:
        for n in s.get("notes", []):
            notes.append(n)
            pool = n.get("pool", "sapling")
            bucket = per_pool.setdefault(
                pool, {"received_zatoshi": 0, "sent_zatoshi": 0,
                       "unspent_zatoshi": 0, "notes": 0})
            bucket["notes"] += 1
            if n["direction"] == "incoming":
                received += n["value_zatoshi"]
                bucket["received_zatoshi"] += n["value_zatoshi"]
                nf = n.get("nullifier")
                known = orchard_spent if pool == "orchard" else spent_nullifiers
                detectable = pool == "orchard" or positions_known
                if nf is None or not detectable:
                    n["spent"] = None
                elif nf in known:
                    n["spent"] = True
                else:
                    n["spent"] = False
                    bucket["unspent_zatoshi"] += n["value_zatoshi"]
                    if pool == "orchard" or positions_known:
                        unspent += n["value_zatoshi"]
            else:
                sent += n["value_zatoshi"]
                bucket["sent_zatoshi"] += n["value_zatoshi"]
    pools = list(pools)
    out = {
        "notes": notes,
        "note_count": len(notes),
        "received_zatoshi": received,
        "received_zec": received / 1e8,
        "sent_zatoshi": sent,
        "sent_zec": sent / 1e8,
        "pools_scanned": pools,
        "pools_not_scanned": [p for p in ("sapling", "orchard")
                              if p not in pools],
        "by_pool": {p: dict(v, received_zec=v["received_zatoshi"] / 1e8,
                            unspent_zec=v["unspent_zatoshi"] / 1e8)
                    for p, v in per_pool.items()},
    }
    sapling_scanned = "sapling" in pools
    if positions_known or not sapling_scanned:
        out["spend_detection"] = ("nullifiers" if sapling_scanned
                                  else "nullifiers (orchard needs no tree)")
        out["unspent_zatoshi"] = unspent
        out["unspent_zec"] = unspent / 1e8
    elif "orchard" in pools:
        out["spend_detection"] = "orchard only"
        out["unspent_zatoshi"] = unspent
        out["unspent_zec"] = unspent / 1e8
        out["note"] = (
            "Orchard notes here are marked spent or unspent from the "
            "nullifiers published in the scanned range -- Orchard needs no "
            "commitment tree for that. Sapling notes are received value only: "
            "their nullifiers need note positions, which need a node "
            "(ZCASH_RPC_URL). unspent_zec therefore counts Orchard only.")
    else:
        out["spend_detection"] = "unavailable"
        out["unspent_zec"] = None
        out["note"] = (
            "Received value only. Telling a spent Sapling note from an unspent "
            "one needs its nullifier, which needs the note's position in the "
            "commitment tree -- set ZCASH_RPC_URL to a zcashd/zebrad node for "
            "that. If this seed has never been imported into a spending "
            "wallet, nothing here has been spent.")
    return out


# ── Chain-backed scans ──────────────────────────────────────────────────────

MAX_EXPLORER_BLOCKS = 4000    # ~4 000 requests-worth of chain, at 100 tx each
ORCHARD_EXPLORER_MAX_BLOCKS = 1000   # Orchard costs one request per candidate tx
ORCHARD_EXPLORER_MAX_TXS = 400       # ... so the number of them is capped too


def scan_blocks(chain, fvk, from_height: int, to_height: int,
                progress=None, orchard_fvk=None) -> dict:
    """Scan a height range for this wallet's notes, in both shielded pools.

    With a node every transaction in every block is read locally and note
    positions -- and so Sapling nullifiers, and so spentness -- are known.
    Without one the scan runs against the public explorer, which serves a
    hundred transactions (Sapling ciphertexts included) per request but cannot
    say where a note sits in the commitment tree, and does not serve Orchard
    actions at all: those come from each candidate transaction's raw bytes,
    which is why an Orchard scan covers a shorter range.
    """
    if to_height < from_height:
        raise ShieldedError("to_height is below from_height")
    span = to_height - from_height + 1
    started = time.time()
    if chain.has_node:
        return _scan_with_node(chain, fvk, from_height, to_height, progress,
                               started, orchard_fvk)
    cap = (ORCHARD_EXPLORER_MAX_BLOCKS if orchard_fvk is not None
           else MAX_EXPLORER_BLOCKS)
    if span > cap:
        pool = "Orchard" if orchard_fvk is not None else "Sapling"
        raise ShieldedError(
            f"{span} blocks is past the {cap}-block cap for a public-explorer "
            f"{pool} scan (roughly {span // 1150} days of chain). Scan a "
            f"narrower range, or set ZCASH_RPC_URL to a zcashd/zebrad node, "
            f"which has no such limit and can also tell spent Sapling notes "
            f"from unspent ones.")
    return _scan_with_explorer(chain, fvk, from_height, to_height, progress,
                               started, orchard_fvk)


def _explorer_pools(orchard_fvk) -> tuple:
    return ("sapling", "orchard") if orchard_fvk is not None else ("sapling",)


def _scan_with_explorer(chain, fvk, from_height, to_height, progress, started,
                        orchard_fvk=None):
    scans, chunk, fetched, unreadable_v6 = [], 500, 0, 0
    for start in range(from_height, to_height + 1, chunk):
        end = min(start + chunk - 1, to_height)
        if orchard_fvk is None:
            rows = chain.shielded_transactions(start, end)
        else:
            rows = chain.transactions_in_range(start, end)
        for row in rows:
            sapling_row = bool(row.get("shielded_output_raw")
                               or row.get("shielded_input_raw"))
            raw = None
            if orchard_fvk is not None and _may_hold_orchard(row):
                if not _readable_layout(row):
                    # A version whose bundle layout this module cannot read
                    # could be hiding a payment to us. Count it; never let it
                    # pass as "nothing found here".
                    unreadable_v6 += 1
                    scans.append({
                        "txid": row.get("hash"), "height": row.get("block_id"),
                        "notes": [],
                        "error": (f"transaction version {row.get('version')} "
                                  f"(version group {row.get('version_group_id')}) "
                                  f"has a shielded layout this module cannot "
                                  f"read; its Orchard actions were not "
                                  f"examined")})
                    continue
                fetched += 1
                if fetched > ORCHARD_EXPLORER_MAX_TXS:
                    raise ShieldedError(
                        f"this range holds more than {ORCHARD_EXPLORER_MAX_TXS} "
                        f"transactions that could carry Orchard actions, and "
                        f"each one costs a request to the public explorer. "
                        f"Scan a narrower range, or set ZCASH_RPC_URL.")
                try:
                    raw = chain.raw_transaction(row["hash"])
                except Exception as e:
                    scans.append({"txid": row.get("hash"),
                                  "height": row.get("block_id"), "notes": [],
                                  "error": f"raw transaction unavailable: {e}"})
                    continue
            elif not sapling_row:
                continue
            try:
                scans.append(scan_explorer_row(row, fvk, orchard_fvk, raw))
            except _bundles.UnknownLayout as e:
                scans.append({"txid": row.get("hash"), "height": row.get("block_id"),
                              "notes": [], "error": str(e)})
        if progress:
            progress(end, len(scans))
    out = summarize(scans, pools=_explorer_pools(orchard_fvk))
    out.update({"scanned_blocks": to_height - from_height + 1,
                "shielded_transactions_seen": len(scans),
                "from_height": from_height, "to_height": to_height,
                "source": "explorer",
                "seconds": round(time.time() - started, 1)})
    if orchard_fvk is not None:
        out["raw_transactions_fetched"] = fetched
    _report_unreadable(out, scans)
    return out


def _may_hold_orchard(row: dict) -> bool:
    """Could this transaction carry an Orchard bundle at all?

    Orchard arrived with NU5 and the v5 format, so anything older cannot hold
    an action. A version group this module has never seen might, which is why
    it counts as a candidate here and is reported as unreadable rather than
    skipped in silence.
    """
    vgid = _bundles.version_group_of(row)
    if vgid in _bundles.ORCHARD_VERSION_GROUPS:
        return True
    if vgid in _bundles.READABLE_VERSION_GROUPS:
        return False
    try:
        return int(row.get("version") or 0) >= 5
    except (TypeError, ValueError):
        return True


def _readable_layout(row: dict) -> bool:
    return _bundles.version_group_of(row) in _bundles.READABLE_VERSION_GROUPS


def _report_unreadable(out: dict, scans: list):
    """A scan that could not read some transactions must say so.

    Silently returning "no notes found" for a transaction we failed to parse
    is the one answer a wallet must never give.
    """
    failed = [s for s in scans if s.get("error") or s.get("warning")]
    if not failed:
        return
    out["unreadable_transactions"] = len(failed)
    out["unreadable_sample"] = [
        {"txid": s.get("txid"), "reason": s.get("error") or s.get("warning")}
        for s in failed[:3]]
    out["warning"] = (
        f"{len(failed)} of {len(scans)} shielded transactions in this range "
        f"could not be read, so a note in one of them would have been missed.")


def _scan_with_node(chain, fvk, from_height, to_height, progress, started,
                    orchard_fvk=None):
    # The tree size before the first block gives every later note its position.
    try:
        position = sapling_tree_size(chain, from_height - 1)
    except Exception:
        position = None
    scans, blocks = [], 0
    for height in range(from_height, to_height + 1):
        for txid, raw in chain.block_raw_transactions(height):
            scan = _safe_scan(raw, fvk, txid, height, position, orchard_fvk)
            scans.append(scan)
            if position is None:
                continue
            # Positions are a running count, so one transaction we could not
            # read desynchronizes every note after it -- and a nullifier from a
            # wrong position matches nothing, which would report spent funds as
            # still ours. Stop counting instead.
            if scan.get("error") or scan.get("warning"):
                position = None
            else:
                position += scan.get("bundles", {}).get("sapling_outputs", 0)
        blocks += 1
        if progress:
            progress(height, len(scans))
    positions_known = position is not None
    spent = set()
    for s in scans:
        spent.update(s.get("nullifiers_spent") or [])
    out = summarize(scans, spent_nullifiers=spent,
                    positions_known=positions_known,
                    pools=_explorer_pools(orchard_fvk))
    out.update({"scanned_blocks": blocks, "from_height": from_height,
                "to_height": to_height, "source": "node",
                "positions_known": positions_known,
                "seconds": round(time.time() - started, 1)})
    if not positions_known:
        out["note"] = (
            "Note positions are unknown for this scan -- either the node did "
            "not answer z_gettreestate, or a transaction in the range could "
            "not be read and the running count of Sapling outputs broke. "
            "Values here are received, not unspent.")
    _report_unreadable(out, scans)
    return out


def _safe_scan(raw, fvk, txid, height, position_base, orchard_fvk=None):
    try:
        return scan_raw_transaction(raw, fvk, txid, height, position_base,
                                    orchard_fvk)
    except _bundles.UnknownLayout as e:
        return {"txid": txid, "height": height, "notes": [], "error": str(e)}


def sapling_tree_size(chain, height: int) -> int:
    """Number of Sapling note commitments in the tree at `height`.

    Read from the node's `z_gettreestate` frontier: a commitment tree's final
    state records one hash per filled subtree, so the sizes of those subtrees
    add up to the number of leaves.
    """
    if height < _sapling.SAPLING_ACTIVATION_HEIGHT:
        return 0
    state = chain.rpc("z_gettreestate", str(height))
    final = (((state or {}).get("sapling") or {}).get("commitments") or {}).get("finalState")
    if not final:
        raise ShieldedError("node did not return a Sapling tree state")
    return _frontier_size(bytes.fromhex(final))


def _frontier_size(blob: bytes) -> int:
    """Leaf count encoded by a serialized incremental Merkle tree frontier.

    The frontier is `left`, `right`, then a vector of `parents`, each an
    optional 32-byte hash written as a 0/1 discriminant followed by the hash
    itself. A filled parent at index i stands for a complete subtree of
    2^(i+1) leaves, so the leaf count is just those subtrees plus the one or
    two loose leaves at the bottom.

    Parsed strictly: a frontier we do not fully understand must raise, because
    a wrong size would give every note a wrong position, and a note whose
    nullifier is computed from the wrong position simply never matches a spend
    -- which would report spent funds as still yours.
    """
    off, size = 0, 0

    def opt_hash(o):
        if o >= len(blob):
            raise ShieldedError("truncated commitment tree frontier")
        present = blob[o]
        if present == 0:
            return None, o + 1
        if present != 1:
            raise ShieldedError("unexpected optional discriminant in frontier")
        if o + 33 > len(blob):
            raise ShieldedError("truncated hash in commitment tree frontier")
        return blob[o + 1:o + 33], o + 33

    left, off = opt_hash(off)
    right, off = opt_hash(off)
    size += (1 if left is not None else 0) + (1 if right is not None else 0)
    if off >= len(blob):
        raise ShieldedError("commitment tree frontier has no parents vector")
    count, off = blob[off], off + 1        # depth is 32, so never a long count
    if count > 32:
        raise ShieldedError(f"frontier claims {count} parent levels; max is 32")
    for level in range(count):
        parent, off = opt_hash(off)
        if parent is not None:
            size += 1 << (level + 1)
    if off != len(blob):
        raise ShieldedError(
            f"commitment tree frontier has {len(blob) - off} trailing bytes; "
            f"refusing to guess note positions from it")
    return size


# ── Sending through a node ──────────────────────────────────────────────────

def node_send(chain, from_address: str, to_address: str, amount_zec: float,
              memo: str = None, fee=None, broadcast: bool = False) -> dict:
    """Spend shielded ZEC by asking a configured node to prove and sign it.

    The node must already hold the spending key -- `node_import_key` puts it
    there. Nothing is sent unless broadcast=True.
    """
    if not chain.has_node:
        raise ShieldedError(
            "shielded sends need a proving backend. Set ZCASH_RPC_URL to a "
            "zcashd/zebrad node, or export the spending key with "
            "shielded_export and spend from Zashi/Ywallet/zingo.")
    recipient = {"address": to_address, "amount": round(float(amount_zec), 8)}
    if memo:
        recipient["memo"] = memo.encode().hex()
    if not broadcast:
        return {
            "mode": "DRY RUN", "broadcast": False,
            "from": from_address, "recipients": [recipient],
            "note": ("DRY RUN - nothing was sent. The node would be asked to "
                     "build, prove and broadcast this shielded spend. Re-run "
                     "with broadcast=True."),
        }
    params = [from_address, [recipient]]
    if fee is not None:
        params += [1, fee]
    opid = chain.rpc("z_sendmany", *params)
    return {"mode": "BROADCAST", "broadcast": True, "operation_id": opid,
            "from": from_address, "recipients": [recipient],
            "note": "Track it with shielded_operation(operation_id)."}


def node_operation(chain, operation_id: str) -> dict:
    if not chain.has_node:
        raise ShieldedError("no node configured (set ZCASH_RPC_URL)")
    result = chain.rpc("z_getoperationstatus", [operation_id])
    return {"operations": result}


def node_import_key(chain, extended_spending_key: str, rescan: str = "whenkeyisnew",
                    birthday: int = None) -> dict:
    """Give a configured node the spending key so it can prove spends."""
    if not chain.has_node:
        raise ShieldedError("no node configured (set ZCASH_RPC_URL)")
    params = [extended_spending_key, rescan]
    if birthday is not None:
        params.append(int(birthday))
    chain.rpc("z_importkey", *params)
    return {"imported": True, "rescan": rescan,
            "note": "The node now holds a spending key for this account."}
