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
proof. `export_keys` prints the ZIP-32 extended spending key so a proving
wallet (Zashi, Ywallet, zcashd, zingo) can spend the same notes, and
`node_send` hands the job to a node when one is configured.
"""

import time

try:
    from . import bundles as _bundles
    from . import keys as _keys
    from . import sapling as _sapling
except ImportError:                     # loaded as loose modules
    import bundles as _bundles
    import keys as _keys
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


def derive_address(mnemonic: str, passphrase: str = "", account: int = 0,
                   index: int = 0, transparent: str = None) -> dict:
    """A shielded receive address, with the unified address that wraps it."""
    xsk = account_key(mnemonic, passphrase, account)
    addr, used = xsk.fvk().address_at(index)
    t_hash = None
    if transparent:
        info = _keys.decode_address(transparent)
        if info["type"] == "p2pkh":
            t_hash = info["hash160"]
    return {
        "account": account,
        "diversifier_index": used,
        "address": addr.encode(),
        "unified_address": addr.unified(t_hash),
        "unified_receivers": ["sapling"] + (["p2pkh"] if t_hash else []),
        "pool": "sapling",
    }


def export_keys(mnemonic: str, passphrase: str = "", account: int = 0) -> dict:
    """Everything another wallet needs to view -- or spend -- this account."""
    xsk = account_key(mnemonic, passphrase, account)
    fvk = xsk.fvk()
    return {
        "account": account,
        "path": f"m/32'/{_sapling.COIN_TYPE}'/{account}'",
        "extended_spending_key": xsk.encode(),
        "extended_full_viewing_key": xsk.encode_fvk(),
        "incoming_viewing_key": fvk.ivk.to_bytes(32, "little").hex(),
        "outgoing_viewing_key": fvk.ovk.hex(),
        "default_address": fvk.address(0).encode(),
        "warning": (
            "The extended spending key spends every note in this account. "
            "Import it into Zashi, Ywallet, zingo or zcashd to send shielded "
            "ZEC -- this module can build the note but cannot prove it."),
    }


def viewing_keys(mnemonic: str, passphrase: str = "", account: int = 0) -> dict:
    """(ivk, ovk, nk) for scanning -- no spend authority."""
    fvk = account_key(mnemonic, passphrase, account).fvk()
    return {"ivk": fvk.ivk, "ovk": fvk.ovk, "nk": fvk.nk, "fvk": fvk}


def keys_from_viewing_key(key: str) -> dict:
    """Accept a `zxviews...` extended full viewing key for watch-only scans."""
    fvk = _sapling.decode_extended_full_viewing_key(key)
    return {"ivk": fvk.ivk, "ovk": fvk.ovk, "nk": fvk.nk, "fvk": fvk}


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


def scan_raw_transaction(raw, fvk, txid: str = None, height: int = None,
                         position_base: int = None) -> dict:
    """Trial-decrypt every Sapling output of one serialized transaction."""
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
    return scan_output_list(parsed.sapling_outputs, fvk, txid, height,
                            position_base, parsed.sapling_spends,
                            parsed.to_dict())


def scan_explorer_row(row: dict, fvk) -> dict:
    """Scan one transaction as the public explorer serves it."""
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


def summarize(scans: list, spent_nullifiers=None, positions_known=False) -> dict:
    """Roll per-transaction scans into a balance.

    `positions_known` is a property of the scan, not of the notes it happened
    to find: a scan that found nothing still has to say whether it *could*
    have told a spent note from an unspent one.
    """
    spent_nullifiers = set(spent_nullifiers or [])
    notes, received, sent, unspent = [], 0, 0, 0
    for s in scans:
        for n in s.get("notes", []):
            notes.append(n)
            if n["direction"] == "incoming":
                received += n["value_zatoshi"]
                nf = n.get("nullifier")
                if nf is None:
                    n["spent"] = None
                elif nf in spent_nullifiers:
                    n["spent"] = True
                else:
                    n["spent"] = False
                    unspent += n["value_zatoshi"]
            else:
                sent += n["value_zatoshi"]
    out = {
        "notes": notes,
        "note_count": len(notes),
        "received_zatoshi": received,
        "received_zec": received / 1e8,
        "sent_zatoshi": sent,
        "sent_zec": sent / 1e8,
        "pools_scanned": ["sapling"],
        "pools_not_scanned": ["orchard"],
    }
    if positions_known:
        out["spend_detection"] = "nullifiers"
        out["unspent_zatoshi"] = unspent
        out["unspent_zec"] = unspent / 1e8
    else:
        out["spend_detection"] = "unavailable"
        out["unspent_zec"] = None
        out["note"] = (
            "Received value only. Telling a spent note from an unspent one "
            "needs its nullifier, which needs the note's position in the "
            "commitment tree -- set ZCASH_RPC_URL to a zcashd/zebrad node for "
            "that. If this seed has never been imported into a spending "
            "wallet, nothing here has been spent.")
    return out


# ── Chain-backed scans ──────────────────────────────────────────────────────

MAX_EXPLORER_BLOCKS = 4000    # ~4 000 requests-worth of chain, at 100 tx each


def scan_blocks(chain, fvk, from_height: int, to_height: int,
                progress=None) -> dict:
    """Scan a height range for this wallet's notes.

    With a node every transaction in every block is read locally and note
    positions -- and so nullifiers, and so spentness -- are known. Without
    one the scan runs against the public explorer, which serves a hundred
    transactions (Sapling ciphertexts included) per request but cannot say
    where a note sits in the commitment tree.
    """
    if to_height < from_height:
        raise ShieldedError("to_height is below from_height")
    span = to_height - from_height + 1
    started = time.time()
    if chain.has_node:
        return _scan_with_node(chain, fvk, from_height, to_height, progress, started)
    if span > MAX_EXPLORER_BLOCKS:
        raise ShieldedError(
            f"{span} blocks is past the {MAX_EXPLORER_BLOCKS}-block cap for a "
            f"public-explorer scan (roughly {span // 1150} days of chain). "
            f"Scan a narrower range, or set ZCASH_RPC_URL to a zcashd/zebrad "
            f"node, which has no such limit and can also tell spent notes "
            f"from unspent ones.")
    return _scan_with_explorer(chain, fvk, from_height, to_height, progress, started)


def _scan_with_explorer(chain, fvk, from_height, to_height, progress, started):
    scans, chunk = [], 500
    for start in range(from_height, to_height + 1, chunk):
        end = min(start + chunk - 1, to_height)
        for row in chain.shielded_transactions(start, end):
            try:
                scans.append(scan_explorer_row(row, fvk))
            except _bundles.UnknownLayout as e:
                scans.append({"txid": row.get("hash"), "height": row.get("block_id"),
                              "notes": [], "error": str(e)})
        if progress:
            progress(end, len(scans))
    out = summarize(scans)
    out.update({"scanned_blocks": to_height - from_height + 1,
                "shielded_transactions_seen": len(scans),
                "from_height": from_height, "to_height": to_height,
                "source": "explorer",
                "seconds": round(time.time() - started, 1)})
    _report_unreadable(out, scans)
    return out


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


def _scan_with_node(chain, fvk, from_height, to_height, progress, started):
    # The tree size before the first block gives every later note its position.
    try:
        position = sapling_tree_size(chain, from_height - 1)
    except Exception:
        position = None
    scans, blocks = [], 0
    for height in range(from_height, to_height + 1):
        for txid, raw in chain.block_raw_transactions(height):
            scan = _safe_scan(raw, fvk, txid, height, position)
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
                    positions_known=positions_known)
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


def _safe_scan(raw, fvk, txid, height, position_base):
    try:
        return scan_raw_transaction(raw, fvk, txid, height, position_base)
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
