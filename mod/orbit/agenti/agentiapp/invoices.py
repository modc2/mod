"""Invoices: "pay me N USDC on <chain>". Like upstream's create_invoice /
check_payment, an invoice is settled by looking at the chain — here by the
wallet's USDC balance rising by at least the amount since the invoice was made
(no indexer needed). Two open invoices on the same wallet+chain can't be told
apart, so a new one shows the others it overlaps."""
import io
import json
import secrets
import threading
import time
import urllib.parse

from . import chains, wallets

_LOCK = threading.Lock()


def _path():
    wallets.HOME.mkdir(parents=True, exist_ok=True)
    return wallets.HOME / 'invoices.json'


def _load() -> list:
    p = _path()
    return json.loads(p.read_text()) if p.is_file() else []


def _save(rows):
    _path().write_text(json.dumps(rows, indent=2))


def pay_uri(inv: dict) -> str:
    raw = int(round(inv['amount'] * 1e6))
    c = chains.chains()[inv['chain']]
    if c['kind'] == 'evm':
        return f"ethereum:{c['usdc']}@{c['chain_id']}/transfer?address={inv['address']}&uint256={raw}"
    q = urllib.parse.urlencode({'amount': f"{inv['amount']:g}", 'spl-token': c['usdc'],
                                'label': 'agenti', 'message': inv.get('memo') or ''})
    return f"solana:{inv['address']}?{q}"


def qr_svg(text: str) -> str:
    import segno
    buf = io.BytesIO()
    segno.make(text, error='m').save(buf, kind='svg', scale=5, border=2, dark='#0b0d10', light='#ffffff',
                                     xmldecl=False, svgns=True)
    return buf.getvalue().decode()


def _view(inv: dict) -> dict:
    return {**inv, 'uri': pay_uri(inv), 'explorer': chains.explorer(inv['chain'], inv['address'])}


def create(amount: float, chain: str = 'base', wallet_id: str = None, memo: str = '') -> dict:
    amount = float(amount)
    if not 0 < amount <= 1_000_000:
        raise ValueError('amount must be above 0')
    cs = chains.chains()
    if chain not in cs:
        raise ValueError(f'unknown chain {chain}')
    w = wallets.get(wallet_id)
    address = w['evm'] if cs[chain]['kind'] == 'evm' else w['solana']
    if not address:
        raise ValueError(f'wallet {w["name"]} has no {cs[chain]["kind"]} address')
    b = chains.balance(chain, address, fresh=True)
    if not b.get('ok'):
        raise ValueError(f'could not read {chain} balance: {b.get("error")}')
    inv = {'id': secrets.token_hex(4), 'amount': round(amount, 6), 'chain': chain, 'address': address,
           'wallet': w['id'], 'wallet_name': w['name'], 'memo': (memo or '')[:140],
           'created': int(time.time()), 'start_raw': b['usdc_raw'], 'status': 'open', 'paid_at': None}
    with _LOCK:
        rows = _load()
        rows.append(inv)
        _save(rows)
    out = _view(inv)
    out['overlaps'] = [r['id'] for r in rows if r['status'] == 'open' and r['id'] != inv['id']
                       and r['address'] == address and r['chain'] == chain]
    return out


def check(invoice_id: str) -> dict:
    with _LOCK:
        rows = _load()
    inv = next((r for r in rows if r['id'] == invoice_id), None)
    if not inv:
        raise KeyError(invoice_id)
    if inv['status'] == 'open':
        b = chains.balance(inv['chain'], inv['address'], fresh=True)
        if b.get('ok'):
            got = (b['usdc_raw'] - inv['start_raw']) / 1e6
            inv['received'] = round(max(got, 0), 6)
            if got + 1e-9 >= inv['amount']:
                inv['status'], inv['paid_at'] = 'paid', int(time.time())
            with _LOCK:
                rows = _load()
                for r in rows:
                    if r['id'] == inv['id']:
                        r.update(inv)
                _save(rows)
    return _view(inv)


def cancel(invoice_id: str) -> dict:
    with _LOCK:
        rows = _load()
        for r in rows:
            if r['id'] == invoice_id:
                r['status'] = 'cancelled'
                _save(rows)
                return _view(r)
    raise KeyError(invoice_id)


def list_invoices() -> list:
    return [_view(r) for r in _load()[::-1]]


def get(invoice_id: str) -> dict:
    r = next((r for r in _load() if r['id'] == invoice_id), None)
    if not r:
        raise KeyError(invoice_id)
    return _view(r)
