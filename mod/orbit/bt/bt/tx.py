"""
bt.tx — let a browser wallet (SubWallet, Talisman, polkadot{.js}) sign real
Bittensor extrinsics without the console shipping a 2 MB chain library.

The split is the one every polkadot-js dapp uses, with the node doing the
heavy half:

  1. prepare  — the node composes the call against the live runtime, picks the
                nonce and a 64-block mortal era, and returns a standard
                SignerPayloadJSON (the exact shape `injector.signer.signPayload`
                takes) plus a human preview and a fee estimate.
  2. sign     — the extension shows its own confirm screen and signs. Keys never
                leave it; nothing here ever sees a secret.
  3. submit   — the node checks the signature against the bytes IT built (a
                payload the extension encoded differently is refused here
                instead of burning a nonce on chain), assembles the extrinsic,
                broadcasts it and waits for inclusion.

Bittensor's custom transaction extensions (SubtensorTransactionExtension,
DrandPriority, CheckShieldedTxValidity, SudoTransactionExtension) carry no
data, which is why an extension that has never heard of them still signs the
same bytes: polkadot-js treats unknown extensions as empty.

Every submission is written to ~/.mod/bt/tx.db — the console's own record of
what it signed, readable with no chain call.

Own websocket (BT_TX_ENDPOINT), own lock: a 20-second inclusion wait never
holds up the explorer's reads.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

ENDPOINT = os.environ.get('BT_TX_ENDPOINT', 'wss://entrypoint-finney.opentensor.ai:443')
ERA_PERIOD = 64                  # blocks a signed payload stays valid (~12.8 min)
RAO = 10 ** 9
DEFAULT_SLIPPAGE_PCT = 2.0       # stake/unstake are limit orders unless told otherwise
PENDING_TTL = ERA_PERIOD * 12    # seconds — past this the era has expired anyway
SS58_FORMAT = 42

KINDS = {
    'transfer': 'Send TAO to another address (Balances.transfer_keep_alive)',
    'stake': 'Buy subnet alpha — stake TAO to a validator hotkey on a subnet',
    'unstake': 'Sell subnet alpha back to TAO — unstake from a validator hotkey',
}

_lock = threading.Lock()          # one substrate websocket, not thread-safe
_substrate = None
_pending: Dict[str, Dict] = {}
_pending_lock = threading.Lock()


# ------------------------------------------------------------- addresses

def normalize(address: str) -> str:
    """Any ss58 (any network prefix) or 0x public key -> Bittensor's prefix 42.

    SubWallet hands out generic-substrate addresses, which already are 42,
    but a user can switch the display format; the chain only cares about
    the 32 bytes underneath.
    """
    from scalecodec.utils.ss58 import ss58_decode, ss58_encode
    a = (address or '').strip()
    if not a:
        raise ValueError('address required')
    if a.startswith('0x'):
        if len(a) != 66:
            raise ValueError(f'not a substrate account (EVM address?): {a}')
        return ss58_encode(a, SS58_FORMAT)
    try:
        return ss58_encode(ss58_decode(a), SS58_FORMAT)
    except Exception as e:
        raise ValueError(f'not a valid ss58 address: {a}') from e


# ------------------------------------------------------------- the payload

def hex_u(n: int, bytes_: int) -> str:
    """polkadot-js renders SignerPayloadJSON numbers as fixed-width hex."""
    return '0x' + format(int(n), f'0{bytes_ * 2}x')


def signer_payload(*, address: str, block_hash: str, block_number: int,
                   era_hex: str, genesis_hash: str, method_hex: str,
                   nonce: int, spec_version: int, tx_version: int,
                   signed_extensions: List[str], tip: int = 0) -> Dict:
    """The SignerPayloadJSON an injected signer's `signPayload` expects."""
    p = {
        'address': address,
        'blockHash': block_hash,
        'blockNumber': hex_u(block_number, 4),
        'era': era_hex,
        'genesisHash': genesis_hash,
        'method': method_hex,
        'nonce': hex_u(nonce, 4),
        'signedExtensions': list(signed_extensions),
        'specVersion': hex_u(spec_version, 4),
        'tip': hex_u(tip, 16),
        'transactionVersion': hex_u(tx_version, 4),
        'version': 4,
    }
    if 'CheckMetadataHash' in signed_extensions:
        p['mode'] = 0            # metadata hash check disabled — no hash to agree on
    return p


def split_signature(sig: str):
    """MultiSignature hex -> (crypto_type, raw bytes).

    Extensions return the signature with its type byte in front
    (0x00 ed25519, 0x01 sr25519, 0x02 ecdsa); a bare 64-byte one is sr25519.
    """
    s = (sig or '').strip()
    if s.startswith('0x'):
        s = s[2:]
    try:
        b = bytes.fromhex(s)
    except ValueError as e:
        raise ValueError('signature is not hex') from e
    if len(b) == 64:
        return 1, b
    if len(b) == 65 and b[0] in (0, 1):
        return b[0], b[1:]
    if len(b) == 66 and b[0] == 2:
        return 2, b[1:]
    raise ValueError(f'unexpected signature length {len(b)} bytes')


def limit_price_rao(price_tao: float, slippage_pct: float, buying: bool) -> int:
    """Worst acceptable alpha price for a limit stake/unstake, in rao per alpha."""
    f = 1 + slippage_pct / 100.0 if buying else 1 - slippage_pct / 100.0
    return max(1, int(price_tao * f * RAO))


# ------------------------------------------------------------- the chain

def _sub():
    global _substrate
    if _substrate is None:
        from async_substrate_interface.sync_substrate import SubstrateInterface
        _substrate = SubstrateInterface(ENDPOINT, ss58_format=SS58_FORMAT)
    return _substrate


def _reset():
    global _substrate
    try:
        if _substrate is not None:
            _substrate.close()
    except Exception:
        pass
    _substrate = None


def _price(netuid: int) -> Optional[float]:
    if netuid == 0:
        return 1.0
    from . import history
    for r in history.screener(sparks=False).get('rows', []):
        if r.get('netuid') == netuid:
            return r.get('price')
    return None


def _compose(sub, kind: str, address: str, args: Dict) -> Dict:
    """kind + human args -> (pallet, function, params) and a plain-words preview."""
    if kind == 'transfer':
        dest = normalize(str(args.get('dest') or ''))
        amt = float(args.get('amount_tao') or 0)
        if amt <= 0:
            raise ValueError('amount_tao must be > 0')
        if dest == address:
            raise ValueError('sending to yourself')
        return {'module': 'Balances', 'function': 'transfer_keep_alive',
                'params': {'dest': dest, 'value': int(round(amt * RAO))},
                'preview': {'action': 'Send TAO', 'to': dest, 'amount_tao': amt}}

    netuid = int(args.get('netuid'))
    hotkey = normalize(str(args.get('hotkey') or ''))
    slip = args.get('slippage_pct', DEFAULT_SLIPPAGE_PCT)
    slip = None if slip in (None, '', 0, '0') else float(slip)
    price = _price(netuid)
    if kind == 'stake':
        amt = float(args.get('amount_tao') or 0)
        if amt <= 0:
            raise ValueError('amount_tao must be > 0')
        params = {'hotkey': hotkey, 'netuid': netuid,
                  'amount_staked': int(round(amt * RAO))}
        preview = {'action': 'Buy alpha (stake)', 'netuid': netuid,
                   'hotkey': hotkey, 'amount_tao': amt, 'price': price,
                   'est_alpha': (amt / price) if price else None}
        fn = 'add_stake'
    elif kind == 'unstake':
        amt = float(args.get('amount_alpha') or 0)
        if amt <= 0:
            raise ValueError('amount_alpha must be > 0')
        params = {'hotkey': hotkey, 'netuid': netuid,
                  'amount_unstaked': int(round(amt * RAO))}
        preview = {'action': 'Sell alpha (unstake)', 'netuid': netuid,
                   'hotkey': hotkey, 'amount_alpha': amt, 'price': price,
                   'est_tao': (amt * price) if price else None}
        fn = 'remove_stake'
    else:
        raise ValueError(f'unknown kind: {kind} (one of {", ".join(KINDS)})')
    if slip and netuid != 0:
        if not price:
            raise ValueError(f'no price for subnet {netuid} yet — retry, or pass slippage_pct=0')
        params['limit_price'] = limit_price_rao(price, slip, kind == 'stake')
        params['allow_partial'] = False
        fn += '_limit'
        preview['slippage_pct'] = slip
        preview['limit_price'] = params['limit_price'] / RAO
    return {'module': 'SubtensorModule', 'function': fn, 'params': params,
            'preview': preview}


def _gc() -> None:
    cutoff = time.time() - PENDING_TTL
    with _pending_lock:
        for k in [k for k, v in _pending.items() if v['created'] < cutoff]:
            _pending.pop(k, None)


def prepare(kind: str, address: str, **args: Any) -> Dict:
    """Compose a call for `address` to sign. Returns {id, payload, preview, fee_tao}."""
    _gc()
    signer = (address or '').strip()      # exactly what the extension knows it as
    addr = normalize(signer)
    for attempt in (0, 1):
        try:
            with _lock:
                sub = _sub()
                sub.init_runtime()
                c = _compose(sub, kind, addr, args)
                call = sub.compose_call(c['module'], c['function'], c['params'])
                nonce = sub.get_account_next_index(addr)
                head = sub.get_chain_finalised_head()
                number = sub.get_block_number(head)
                era = {'period': ERA_PERIOD, 'current': number}
                era_obj = sub.runtime_config.create_scale_object('Era')
                era_hex = str(era_obj.encode(era))
                block_hash = sub.get_block_hash(era_obj.birth(number))
                genesis = sub.get_block_hash(0)
                exts = list(sub.runtime.metadata.get_signed_extensions().keys())
                spec, txv = sub.runtime.runtime_version, sub.runtime.transaction_version
                sign_bytes = bytes(sub.generate_signature_payload(
                    call=call, era=era, nonce=nonce).data)
                fee = None
                try:
                    from bittensor_wallet import Keypair
                    info = sub.get_payment_info(call, Keypair(ss58_address=addr),
                                                era=dict(era), nonce=nonce)
                    fee = (info.get('partial_fee') or info.get('partialFee') or 0) / RAO
                except Exception:
                    pass
                balance = None
                try:
                    acct = sub.query('System', 'Account', [addr])
                    balance = acct.value['data']['free'] / RAO
                except Exception:
                    pass
            break
        except ValueError:
            raise
        except Exception:
            _reset()                      # stale socket — one reconnect
            if attempt:
                raise
    payload = signer_payload(
        address=signer, block_hash=block_hash, block_number=number,
        era_hex=era_hex, genesis_hash=genesis, method_hex=str(call.data),
        nonce=nonce, spec_version=spec, tx_version=txv, signed_extensions=exts)
    tid = secrets.token_hex(8)
    with _pending_lock:
        _pending[tid] = {'kind': kind, 'address': addr, 'signer': signer,
                         'call': call, 'era': era, 'nonce': nonce,
                         'sign_bytes': sign_bytes, 'preview': c['preview'],
                         'args': args, 'created': time.time(),
                         'call_name': f"{c['module']}.{c['function']}"}
    return {'id': tid, 'kind': kind, 'address': addr, 'payload': payload,
            'call': f"{c['module']}.{c['function']}", 'preview': c['preview'],
            'fee_tao': fee, 'free_tao': balance, 'nonce': nonce,
            'valid_for_blocks': ERA_PERIOD, 'block': number}


def verify(address: str, sign_bytes: bytes, signature: str) -> bool:
    """Does `signature` sign exactly the payload this node built?"""
    from bittensor_wallet import Keypair
    ctype, raw = split_signature(signature)
    if ctype == 2:
        raise ValueError('ecdsa substrate accounts are not supported — use an sr25519 or ed25519 account')
    kp = Keypair(ss58_address=address, crypto_type=ctype)
    return bool(kp.verify(sign_bytes, raw))


def submit(id: str, signature: str, wait: bool = True) -> Dict:
    """Assemble + broadcast a prepared extrinsic with the wallet's signature."""
    with _pending_lock:
        p = _pending.get(id)
    if p is None:
        raise ValueError('unknown or expired transaction — prepare it again')
    if not verify(p['address'], p['sign_bytes'], signature):
        raise ValueError('signature does not match the prepared payload — '
                         'the wallet signed different bytes; nothing was sent')
    with _pending_lock:
        _pending.pop(id, None)            # single use: a nonce can only land once
    from bittensor_wallet import Keypair
    ctype, raw = split_signature(signature)
    rec_id = _log_start(id, p)
    try:
        with _lock:
            sub = _sub()
            ext = sub.create_signed_extrinsic(
                call=p['call'], keypair=Keypair(ss58_address=p['address'], crypto_type=ctype),
                era=dict(p['era']), nonce=p['nonce'],
                signature='0x' + bytes([ctype]).hex() + raw.hex())
            receipt = sub.submit_extrinsic(ext, wait_for_inclusion=wait)
            out = {'ok': True, 'id': id, 'extrinsic_hash': receipt.extrinsic_hash,
                   'block_hash': receipt.block_hash, 'included': bool(wait)}
            if wait:
                ok = receipt.is_success
                out['ok'] = bool(ok)
                out['block'] = receipt.block_number
                if not ok:
                    err = receipt.error_message or {}
                    out['error'] = f"{err.get('name', 'failed')}: {err.get('docs', '')}".strip(': ')
    except Exception as e:
        _reset()
        _log_end(rec_id, {'ok': False, 'error': f'{type(e).__name__}: {e}'})
        raise
    _log_end(rec_id, out)
    if out['ok']:
        _after(p['address'])
    out['preview'] = p['preview']
    out['call'] = p['call_name']
    return out


def _after(address: str) -> None:
    """A landed trade should show up at once: re-snapshot the wallet if tracked."""
    def go():
        try:
            from . import traders, tools
            if any(t['ss58'] == address for t in traders.watchlist()):
                with tools._call_lock:        # shared chain socket
                    traders.snapshot(address)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True, name='bt-tx-after').start()


# ------------------------------------------------------------- the record

def _db() -> sqlite3.Connection:
    from .history import data_dir
    conn = sqlite3.connect(os.path.join(data_dir(), 'tx.db'), timeout=30)
    conn.execute('''CREATE TABLE IF NOT EXISTS txs (
        id TEXT PRIMARY KEY, ts INTEGER, address TEXT, kind TEXT, call TEXT,
        args TEXT, preview TEXT, status TEXT, extrinsic_hash TEXT,
        block_hash TEXT, block INTEGER, error TEXT, done_ts INTEGER)''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_txs_addr ON txs(address, ts)')
    return conn


def _log_start(tid: str, p: Dict) -> str:
    conn = _db()
    try:
        conn.execute('INSERT OR REPLACE INTO txs (id, ts, address, kind, call, args, preview, status) '
                     'VALUES (?,?,?,?,?,?,?,?)',
                     (tid, int(time.time()), p['address'], p['kind'], p['call_name'],
                      json.dumps(p['args'], default=str), json.dumps(p['preview'], default=str),
                      'submitted'))
        conn.commit()
    finally:
        conn.close()
    return tid


def _log_end(tid: str, out: Dict) -> None:
    conn = _db()
    try:
        conn.execute('UPDATE txs SET status=?, extrinsic_hash=?, block_hash=?, block=?, error=?, done_ts=? WHERE id=?',
                     ('ok' if out.get('ok') else 'failed', out.get('extrinsic_hash'),
                      out.get('block_hash'), out.get('block'), out.get('error'),
                      int(time.time()), tid))
        conn.commit()
    finally:
        conn.close()


def history(address: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """What this console has signed and sent, newest first."""
    conn = _db()
    try:
        q = 'SELECT id, ts, address, kind, call, args, preview, status, extrinsic_hash, block_hash, block, error FROM txs'
        a: tuple = ()
        if address:
            q += ' WHERE address = ?'
            a = (normalize(address),)
        rows = conn.execute(q + ' ORDER BY ts DESC LIMIT ?', a + (int(limit),)).fetchall()
    finally:
        conn.close()
    cols = ('id', 'ts', 'address', 'kind', 'call', 'args', 'preview', 'status',
            'extrinsic_hash', 'block_hash', 'block', 'error')
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        for k in ('args', 'preview'):
            try:
                d[k] = json.loads(d[k] or 'null')
            except Exception:
                pass
        out.append(d)
    return out
