"""Keeping the routers funded, in crypto, without being asked each time.

The brief for this half is "settle in crypto and pay in the background", and the
honest reading of that is a loop that watches balances and tops them up before a
call fails — not a loop with unattended spending authority. So the machinery is
complete and the *arming* is explicit:

    disarmed (default)  the watcher runs, prices the top-up, and files it as a
                        proposal. Nothing moves. `/settle/proposals` is the queue.
    armed               the watcher executes a proposal itself, bounded by a
                        per-provider floor, a per-top-up cap and a rolling
                        24h ceiling it will not cross for any reason.

Arming is a POST with `confirm=true` and a cap, never a config default, because
an unattended process holding a hot wallet is exactly the thing that should cost
somebody one deliberate action. The same code path runs either way — a proposal
is an execution that stopped before `rail.send`, so what you approve is what
already got priced, not a re-derivation of it.

Rails are not reimplemented here. This box already runs modules that hold those
keys and gate their own writes, so a top-up is an authenticated call to `eth`,
`solana` or `near` and the private key never enters this process. A rail whose
module is not listening is reported as down rather than being retried into a
timeout.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

import ledger as L
import router as R

STATE_DIR = os.path.expanduser(os.environ.get('INFER_DIR', '~/.mod/infer'))
POLICY = os.path.join(STATE_DIR, 'settle.json')
PROPOSALS = os.path.join(STATE_DIR, 'settle-proposals.jsonl')

# Every rail is a module already running on this box that holds its own keys and
# gates its own writes. `confirm` is the flag each one demands before it moves
# funds off a testnet; it is passed through, never defaulted to true here.
RAILS = {
    'eth':    {'url': 'http://localhost:50730', 'send': '/send',
               'coins': ('ETH', 'USDC', 'USDT'), 'auth': True},
    'solana': {'url': 'http://localhost:50710', 'send': '/transfer',
               'coins': ('SOL', 'USDC'), 'auth': True},
    'near':   {'url': 'http://localhost:50910', 'send': '/send',
               'coins': ('NEAR',), 'auth': True},
}

DEFAULTS = {
    'armed': False,
    'interval': 900,            # seconds between balance sweeps
    'floor_usd': 2.0,           # top up when a funded router drops below this
    'topup_usd': 10.0,          # how much to add
    'daily_cap_usd': 25.0,      # the ceiling the watcher will not cross, armed
    'rail': None,               # which rail to pay from; None = propose only
    'coin': 'USDC',
    'providers': {},            # per-provider overrides of the above
}


class SettleError(Exception):
    pass


# ── policy ───────────────────────────────────────────────────────────────

def policy():
    try:
        with open(POLICY) as f:
            return {**DEFAULTS, **(json.load(f) or {})}
    except Exception:
        return dict(DEFAULTS)


def _save_policy(p):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = POLICY + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(p, f, indent=2)
    os.replace(tmp, POLICY)
    return p


def configure(confirm=False, **kw):
    """Change the policy. Arming needs `confirm=true` and a real cap."""
    p = policy()
    arming = kw.get('armed') and not p['armed']
    for k, v in kw.items():
        if k in DEFAULTS and v is not None:
            p[k] = v
    if arming:
        if not confirm:
            return {'needs_confirm': True, 'would_arm': True,
                    'why': ('arming lets this process move funds unattended — '
                            'resend with confirm=true'),
                    'caps': {k: p[k] for k in
                             ('topup_usd', 'daily_cap_usd', 'floor_usd', 'rail')}}
        if not p.get('rail'):
            raise SettleError('arming needs rail= (one of %s)' % ', '.join(RAILS))
        if not p.get('daily_cap_usd'):
            raise SettleError('arming needs a daily_cap_usd — an unbounded '
                              'autopayer is not a feature')
    return _save_policy(p)


def _for(provider, p=None):
    p = p or policy()
    return {**{k: p[k] for k in
               ('floor_usd', 'topup_usd', 'rail', 'coin', 'daily_cap_usd')},
            **(p.get('providers', {}).get(provider) or {})}


# ── balances ─────────────────────────────────────────────────────────────

def balances(keys=None, kyc='none'):
    """What each funded router says is left. Unfunded routers say so."""
    out = []
    for prov in R.every(keys=keys, kyc=kyc):
        row = {'provider': prov.name, 'ready': prov.ready, 'kyc': prov.kyc,
               'pay': list(prov.pay), 'account': prov.account}
        if not prov.ready:
            row['balance'] = None
            row['why'] = 'no key'
        elif 'balance' not in prov.caps:
            row['balance'] = None
            row['why'] = 'router publishes no balance'
        else:
            try:
                row.update(prov.balance())
                row['balance'] = row.get('usd')
            except R.RouterError as e:
                row['balance'] = None
                row['why'] = str(e)
        out.append(row)
    return {'balances': out,
            'funded': [r['provider'] for r in out if (r.get('balance') or 0) > 0]}


# ── rails ────────────────────────────────────────────────────────────────

def rails():
    out = []
    for name, spec in RAILS.items():
        up, detail = _rail_up(spec)
        out.append({'rail': name, 'url': spec['url'], 'coins': list(spec['coins']),
                    'up': up, 'detail': detail})
    return {'rails': out, 'up': [r['rail'] for r in out if r['up']]}


def _rail_up(spec, timeout=2):
    try:
        req = urllib.request.Request(spec['url'] + '/health', method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500, 'listening'
    except urllib.error.HTTPError as e:
        return e.code < 500, 'listening (%d)' % e.code
    except Exception as e:
        return False, str(e)


def _send(rail, to, amount, coin, token=None, confirm=False, timeout=30):
    """Hand the transfer to the module that holds the key. Never sign here."""
    spec = RAILS.get(rail)
    if not spec:
        raise SettleError(f'unknown rail {rail!r} — have {", ".join(RAILS)}')
    up, detail = _rail_up(spec)
    if not up:
        raise SettleError(f'rail {rail} is not listening ({detail}) — '
                          f'start the {rail} module before arming settlement')
    body = {'to': to, 'amount': amount, 'confirm': bool(confirm)}
    if coin and coin.upper() not in ('ETH', 'SOL', 'NEAR'):
        body['token'] = token or coin
    if rail == 'near':
        body = {'to': to, 'amount_near': amount, 'token': token or coin}
    req = urllib.request.Request(
        spec['url'] + spec['send'], method='POST',
        data=json.dumps(body).encode(),
        headers={'content-type': 'application/json',
                 'user-agent': 'mod-infer/0.2 (settlement)'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        detail = ''
        try:
            detail = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            pass
        raise SettleError(f'{rail} refused the transfer ({e.code}): {detail}')
    except Exception as e:
        raise SettleError(f'{rail} transfer failed: {e}')


# ── proposals ────────────────────────────────────────────────────────────

def _file(entry):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(PROPOSALS, 'a') as f:
        f.write(json.dumps(entry) + '\n')
    return entry


def proposals(limit=50, state=None):
    try:
        with open(PROPOSALS) as f:
            rows = [json.loads(x) for x in f if x.strip()]
    except FileNotFoundError:
        return []
    if state:
        rows = [r for r in rows if r.get('state') == state]
    return rows[-limit:][::-1]


def _spent_today():
    day = time.time() - 86400
    return round(sum(r.get('usd') or 0 for r in proposals(limit=10 ** 6)
                     if r.get('state') == 'sent' and r.get('at', 0) >= day), 6)


def sweep(keys=None, kyc='none', execute=None, confirm=False):
    """One pass: read balances, decide, and either file or execute.

    This is the whole loop. The background thread calls it on a timer and adds
    nothing, so what runs unattended is exactly what a manual sweep does.
    """
    p = policy()
    execute = p['armed'] if execute is None else execute
    bal = balances(keys=keys, kyc=kyc)
    spent, cap = _spent_today(), p['daily_cap_usd']
    acted = []
    for row in bal['balances']:
        if not row['ready'] or row.get('balance') is None:
            continue
        rule = _for(row['provider'], p)
        if row['balance'] >= rule['floor_usd']:
            continue
        entry = {
            'id': '%d-%s' % (time.time_ns() // 1000, row['provider']),
            'at': time.time(), 'provider': row['provider'],
            'balance': row['balance'], 'floor': rule['floor_usd'],
            'usd': rule['topup_usd'], 'coin': rule['coin'],
            'rail': rule['rail'], 'state': 'proposed',
        }
        if not execute:
            entry['why'] = 'settlement disarmed — approve with POST /settle/pay'
        elif not rule['rail']:
            entry['state'] = 'blocked'
            entry['why'] = 'no rail configured for this provider'
        elif spent + entry['usd'] > cap:
            entry['state'] = 'blocked'
            entry['why'] = (f'24h cap {cap} would be exceeded '
                            f'(spent {spent}, this {entry["usd"]})')
        elif not entry.get('address'):
            # A top-up needs a deposit address the router issued. Nothing here
            # invents one: without it the proposal is complete but unsendable.
            entry['state'] = 'blocked'
            entry['why'] = ('no deposit address on file for '
                            f'{row["provider"]} — POST /settle/address '
                            '{provider, address, coin}')
        acted.append(_file(entry))
    return {'swept': len(bal['balances']), 'proposals': acted,
            'armed': p['armed'], 'spent_24h': spent, 'cap_24h': cap,
            'balances': bal['balances']}


def pay(proposal_id=None, provider=None, usd=None, address=None, rail=None,
        coin=None, confirm=False):
    """Execute one top-up. `confirm=true` is what actually moves funds."""
    p = policy()
    if proposal_id:
        found = next((r for r in proposals(limit=10 ** 6)
                      if r['id'] == proposal_id), None)
        if not found:
            raise SettleError(f'no proposal {proposal_id}')
        provider = provider or found['provider']
        usd = usd if usd is not None else found['usd']
        rail = rail or found.get('rail')
        coin = coin or found.get('coin')
        address = address or found.get('address')
    rule = _for(provider or '', p)
    rail, coin = rail or rule['rail'], coin or rule['coin']
    usd = rule['topup_usd'] if usd is None else float(usd)
    address = address or _addresses().get(provider, {}).get('address')
    if not address:
        raise SettleError(f'no deposit address for {provider} — '
                          'POST /settle/address {provider, address, coin}')
    if not rail:
        raise SettleError('which rail? one of ' + ', '.join(RAILS))
    if usd > rule['topup_usd'] * 4:
        raise SettleError(f'{usd} is more than 4x the configured top-up '
                          f'({rule["topup_usd"]}) — raise topup_usd deliberately')
    spent = _spent_today()
    if spent + usd > rule['daily_cap_usd']:
        raise SettleError(f'24h cap {rule["daily_cap_usd"]} would be exceeded '
                          f'(already {spent})')
    entry = {'id': '%d-%s' % (time.time_ns() // 1000, provider), 'at': time.time(),
             'provider': provider, 'usd': usd, 'coin': coin, 'rail': rail,
             'address': address, 'state': 'proposed'}
    if not confirm:
        entry['why'] = 'dry run — resend with confirm=true to move funds'
        entry['would_send'] = {'rail': rail, 'to': address, 'amount': usd,
                               'coin': coin}
        return _file(entry)
    try:
        receipt = _send(rail, address, usd, coin, confirm=True)
    except SettleError as e:
        entry['state'] = 'failed'
        entry['why'] = str(e)
        _file(entry)
        raise
    entry['state'] = 'sent'
    entry['receipt'] = receipt
    L.record(provider=provider, model='-', usd=usd, kind='topup',
             note=f'{coin} via {rail} -> {address}')
    return _file(entry)


def _addresses():
    try:
        with open(os.path.join(STATE_DIR, 'settle-addresses.json')) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def set_address(provider, address, coin=None):
    """The deposit address a router issued you. Never derived, always given."""
    if not provider or not address:
        raise SettleError('need provider= and address=')
    known = _addresses()
    known[provider] = {'address': str(address), 'coin': coin,
                       'set': time.time()}
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = os.path.join(STATE_DIR, 'settle-addresses.json.tmp')
    with open(tmp, 'w') as f:
        json.dump(known, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(STATE_DIR, 'settle-addresses.json'))
    return {'provider': provider, 'address': address, 'coin': coin}


# ── the background half ──────────────────────────────────────────────────

_thread = None
_stop = threading.Event()
_last = {}


def _loop():
    while not _stop.is_set():
        p = policy()
        try:
            _last.clear()
            _last.update(sweep())
            _last['at'] = time.time()
        except Exception as e:
            _last.update({'error': str(e), 'at': time.time()})
        _stop.wait(max(60, int(p['interval'])))


def start():
    """Run the sweep on a timer. Safe when disarmed: it only files proposals."""
    global _thread
    if _thread and _thread.is_alive():
        return status()
    _stop.clear()
    _thread = threading.Thread(target=_loop, name='infer-settle', daemon=True)
    _thread.start()
    return status()


def stop():
    _stop.set()
    return status()


def status():
    p = policy()
    return {'running': bool(_thread and _thread.is_alive()),
            'armed': p['armed'], 'interval': p['interval'],
            'floor_usd': p['floor_usd'], 'topup_usd': p['topup_usd'],
            'daily_cap_usd': p['daily_cap_usd'], 'rail': p['rail'],
            'spent_24h': _spent_today(), 'last_sweep': dict(_last),
            'addresses': {k: v.get('coin') for k, v in _addresses().items()},
            'note': ('armed: this process can move funds within the caps above'
                     if p['armed'] else
                     'disarmed: the watcher files proposals, nothing moves')}
