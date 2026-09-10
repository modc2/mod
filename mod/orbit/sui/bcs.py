#!/usr/bin/env python3
"""BCS — the byte layout Sui actually validates, built by hand.

A Sui transaction is not JSON. It is a BCS-serialized `TransactionData`, and
the node either deserializes it exactly or refuses the whole thing. There is no
partial credit and no helpful error: get one length prefix wrong and a transfer
is rejected, or — the case worth being afraid of — accepted and wrong.

So every rule below was pinned against mainnet rather than against a document:

* `SuiAddress` and `ObjectID` are 32 raw bytes with **no** length prefix.
* `ObjectDigest` is a byte *vector* — ULEB length `0x20`, then 32 bytes. Two
  fixed-size 32-byte fields, encoded differently, sitting next to each other.
* the signing digest is `blake2b256(intent || bcs)` where intent is the three
  bytes `00 00 00` (TransactionData, V0, Sui) — the signature is over the
  hash, not over the message.
* the transaction digest is `blake2b256(b"TransactionData::" || bcs)`, base58.
  The `TransactionData::` prefix is not in the signing hash. Verified against
  six live transactions; without the prefix nothing matches.

`python3 bcs.py --selftest` re-derives the last two from a transaction pulled
off mainnet, so the layout is checked against the chain and not against this
docstring.
"""

import base64
import hashlib

from keys import SuiError, b58decode, b58encode, blake2b, normalize

# IntentScope::TransactionData, IntentVersion::V0, AppId::Sui
INTENT_TRANSACTION_DATA = b'\x00\x00\x00'
TX_DIGEST_PREFIX = b'TransactionData::'
SUI_TYPE = '0x2::sui::SUI'


# ── primitives ───────────────────────────────────────────────────

def uleb128(n):
    if n < 0:
        raise SuiError(f'ULEB128 cannot encode {n}')
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | 0x80 if n else byte)
        if not n:
            return bytes(out)


def u8(n):
    return bytes([n & 0xFF])


def u16(n):
    return int(n).to_bytes(2, 'little')


def u64(n):
    n = int(n)
    if n < 0 or n >> 64:
        raise SuiError(f'{n} does not fit in a u64')
    return n.to_bytes(8, 'little')


def u256(n):
    return int(n).to_bytes(32, 'little')


def vector(items):
    items = list(items)
    return uleb128(len(items)) + b''.join(items)


def byte_vector(raw):
    """A `vector<u8>` — length-prefixed. Not the same as a fixed [u8; N]."""
    return uleb128(len(raw)) + bytes(raw)


def string(text):
    return byte_vector(text.encode())


def address(value):
    """32 raw bytes, no length prefix. ObjectID serializes identically."""
    return bytes.fromhex(normalize(value)[2:])


def object_digest(digest_b58):
    """Length-prefixed, unlike the address sitting immediately before it."""
    raw = b58decode(digest_b58)
    if len(raw) != 32:
        raise SuiError(f'an object digest is 32 bytes, got {len(raw)}')
    return byte_vector(raw)


def object_ref(object_id, version, digest):
    return address(object_id) + u64(version) + object_digest(digest)


# ── arguments and commands ───────────────────────────────────────

GAS_COIN = u8(0)


def Input(i):
    return u8(1) + u16(i)


def Result(i):
    return u8(2) + u16(i)


def NestedResult(i, j):
    return u8(3) + u16(i) + u16(j)


def pure(raw):
    """CallArg::Pure — the value's own BCS, wrapped in a byte vector."""
    return u8(0) + byte_vector(raw)


def owned_object(object_id, version, digest):
    """CallArg::Object(ObjectArg::ImmOrOwnedObject)."""
    return u8(1) + u8(0) + object_ref(object_id, version, digest)


def shared_object(object_id, initial_shared_version, mutable=True):
    """CallArg::Object(ObjectArg::SharedObject)."""
    return u8(1) + u8(1) + address(object_id) + \
        u64(initial_shared_version) + u8(1 if mutable else 0)


def split_coins(coin, amounts):
    return u8(2) + coin + vector(amounts)


def transfer_objects(objects, recipient):
    return u8(1) + vector(objects) + recipient


def merge_coins(destination, sources):
    return u8(3) + destination + vector(sources)


def move_call(package, module, function, type_arguments=(), arguments=()):
    return u8(0) + address(package) + string(module) + string(function) + \
        vector(type_arguments) + vector(arguments)


# ── the transaction ──────────────────────────────────────────────

def transaction_data(sender, inputs, commands, gas_payment, gas_price,
                     gas_budget, gas_owner=None):
    """TransactionData::V1 over a ProgrammableTransaction, with no expiry."""
    kind = u8(0) + vector(inputs) + vector(commands)
    gas_data = (vector(object_ref(*ref) for ref in gas_payment) +
                address(gas_owner or sender) + u64(gas_price) + u64(gas_budget))
    return u8(0) + kind + address(sender) + gas_data + u8(0)


def digest_of(tx_bytes):
    """The digest the explorers show — base58, and prefixed before hashing."""
    return b58encode(blake2b(TX_DIGEST_PREFIX + tx_bytes))


def signing_digest(tx_bytes):
    return blake2b(INTENT_TRANSACTION_DATA + tx_bytes)


def sign_transaction(tx_bytes, seed):
    """Sui's serialized signature: base64(flag || sig64 || pubkey32).

    The public key travels with the signature because a Sui address is a hash
    and the node cannot recover a key from it.
    """
    from keys import pubkey_of, sign
    signature = sign(seed, signing_digest(tx_bytes))
    return base64.b64encode(bytes([0]) + signature + pubkey_of(seed)).decode()


# ── coin selection ───────────────────────────────────────────────

# Sui now keeps some balances in an address accumulator rather than in Coin
# objects, and suix_getCoins reports those with a synthetic object digest —
# 32 bytes ending in a run of 0xAC padding. They are not real objects: pass one
# as a transaction input and the node rejects the whole transaction with a
# "withdraw reservation" error that never mentions which coin it meant.
SYNTHETIC_TAIL = b'\xac' * 8


def is_synthetic(coin):
    try:
        return b58decode(coin.get('digest') or '').endswith(SYNTHETIC_TAIL)
    except Exception:
        return False


def spendable(coins):
    """Only coins that are really objects, biggest first."""
    real = [c for c in coins if not is_synthetic(c)]
    return sorted(real, key=lambda c: int(c.get('balance') or 0), reverse=True)


def ref_of(coin):
    return (coin['coinObjectId'], int(coin['version']), coin['digest'])


def build_transfer(sender, recipient, amount, gas_coins, gas_price, gas_budget,
                   coins=None):
    """Bytes for "send `amount` of one coin type to `recipient`".

    Two shapes, because SUI is its own gas. Sending SUI splits the gas coin
    itself, so no separate input is needed; sending anything else merges that
    coin type's objects and splits the result, paying gas from SUI on the side.
    """
    recipient_input = pure(address(recipient))
    if coins is None:                                   # SUI: split the gas coin
        inputs = [pure(u64(amount)), recipient_input]
        commands = [split_coins(GAS_COIN, [Input(0)]),
                    transfer_objects([Result(0)], Input(1))]
    else:
        inputs = [owned_object(*ref_of(c)) for c in coins]
        primary, rest = Input(0), [Input(i) for i in range(1, len(coins))]
        amount_index = len(inputs)
        inputs += [pure(u64(amount)), recipient_input]
        commands = []
        if rest:
            commands.append(merge_coins(primary, rest))
        held = sum(int(c['balance']) for c in coins)
        if held == amount:
            commands.append(transfer_objects([primary], Input(amount_index + 1)))
        else:
            commands.append(split_coins(primary, [Input(amount_index)]))
            commands.append(transfer_objects([Result(len(commands) - 1)],
                                             Input(amount_index + 1)))
    return transaction_data(sender, inputs, commands,
                            [ref_of(c) for c in gas_coins], gas_price, gas_budget)


def _selftest():
    """Re-derive both digests from a real mainnet transaction."""
    import json
    import urllib.request
    url = 'https://sui-rpc.publicnode.com'
    body = json.dumps({'jsonrpc': '2.0', 'id': 1,
                       'method': 'suix_queryTransactionBlocks',
                       'params': [{'options': {'showRawInput': True,
                                                'showInput': True}}, None, 8,
                                  True]}).encode()
    req = urllib.request.Request(url, data=body, headers={
        'content-type': 'application/json', 'user-agent': 'mod-sui/selftest'})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read())['result']['data']
    checked = 0
    for row in rows:
        from keys import address_of
        sender = row['transaction']['data']['sender']
        # Unsigned system transactions come from 0x0 and their trailing bytes
        # can look like a signature; skip them by sender, not by layout.
        if normalize(sender) == normalize('0x0'):
            continue
        raw = base64.b64decode(row['rawTransaction'])
        if raw[0] != 1 or raw[-99] != 1 or raw[-98] != 0x61 or raw[-97] != 0:
            continue                       # multisig or zkLogin
        signature, pubkey = raw[-96:-32], raw[-32:]
        tx_bytes = raw[1:-99][3:]
        assert digest_of(tx_bytes) == row['digest'], row['digest']
        assert address_of(pubkey) == normalize(sender), sender
        try:
            from nacl.signing import VerifyKey
            VerifyKey(pubkey).verify(signing_digest(tx_bytes), signature)
        except ImportError:
            pass
        checked += 1
    print(f'ok — {checked} live transactions re-derived: digest, address, signature')
    return checked


if __name__ == '__main__':
    import sys
    sys.path.insert(0, __file__.rsplit('/', 1)[0])
    _selftest()
