"""x402mcp — the x402 index as an MCP server: find any paid API, then use it.

One JSON-RPC dispatch, two transports, so they cannot drift:

    stdio  python3 x402mcp.py                  (caller = this box, owner)
    HTTP   POST /mcp  (also /x402/mcp, /x402/api/mcp) on serve.py's port

    claude mcp add x402 -- python3 /root/mod/mod/orbit/x402/x402mcp.py
    claude mcp add --transport http x402 http://localhost:51110/mcp \\
        --header "Authorization: Bearer x402_…"

Every call is made *as someone* (x402trust.identify) and gated by that
caller's trust tier: reads are open, quoting and calling are earned, the
node's own money is spent only on trusted callers and only within budget.
Paying with your own wallet is the normal path and needs no trust in the
node at all: x402_quote hands back EIP-712 typed data, you sign it, and
x402_call replays the request with your signature.
"""

import json
import os
import secrets
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import x402db as store          # noqa: E402
import x402pay as pay           # noqa: E402
import x402src as src           # noqa: E402
import x402trust as trust       # noqa: E402

SERVER_INFO = {'name': 'x402', 'title': 'x402 · every paid API', 'version': '0.3.0'}
PROTOCOL_VERSION = '2025-06-18'
SUPPORTED = ('2025-06-18', '2025-03-26', '2024-11-05')
INSTRUCTIONS = (
    'Every x402 (HTTP 402 pay-per-request) API that exists, in one local index, and a way to '
    'call them. x402_search finds services (full-text, network, max price); x402_service opens '
    'one (its offers, and how calls through this node have gone). To use one: x402_call with '
    'pay="none" shows what it costs; x402_quote returns EIP-712 typed data — sign it with your '
    'own wallet (eth_signTypedData_v4) and pass quote_id + signature to x402_call. A node '
    'wallet ("house") may pay for trusted callers within a daily budget. Every caller has a '
    'trust score 0-100 (x402_whoami): it rises with tenure, active days and calls you paid for '
    'yourself, and falls with throttling and refused requests. Tiers: restricted <20 (read), '
    'basic 20+ (quote/call listed hosts), trusted 40+ (any public URL, probe, vouch, house '
    'budget), core 70+. No wallet? x402_register issues an API key (Authorization: Bearer).')


# ── quotes: typed data out, signature in ──────────────────────────

QUOTE_TTL = 600
_quotes = {}
_qlock = threading.Lock()


def _put_quote(q):
    qid = 'q_' + secrets.token_urlsafe(9)
    with _qlock:
        now = time.time()
        for k in [k for k, v in _quotes.items() if v['expires'] < now]:
            del _quotes[k]
        _quotes[qid] = q
    return qid


def _take_quote(qid, uid):
    with _qlock:
        q = _quotes.get(qid)
        if not q or q['expires'] < time.time():
            raise ValueError(f'quote {qid} is unknown or expired — quote again')
        if q['user'] != uid:
            raise ValueError('that quote belongs to another caller')
        return _quotes.pop(qid)


# ── helpers ───────────────────────────────────────────────────────

class Refused(Exception):
    """The caller asked for something its standing does not allow (no penalty)."""


class Denied(Exception):
    """The caller asked for something nobody may do — counts against trust."""


def _need(a, k):
    v = a.get(k)
    if v in (None, ''):
        raise ValueError(f'{k} is required')
    return v


def _num(v):
    return None if v in (None, '') else float(v)


def _can(st, cap):
    if cap not in st['can']:
        floor = next((f for f, n in reversed(trust.TIERS) if cap in trust.CAN[n]), None)
        raise Refused(f'{cap} needs tier {"owner" if floor is None else f"score >= {floor}"}; '
                      f'you are {st["tier"]} at {st["score"]} (see x402_whoami for how to rise)')


def _listed_host(url):
    return store.search(host=src.host_of(url), limit=1)['total'] > 0


def _target(a, st):
    """The URL to fetch, after the checks this caller's standing requires."""
    url = pay.with_query(str(_need(a, 'url')), a.get('query'))
    if 'local_urls' not in st['can']:
        try:
            pay.guard(url)
        except pay.PayError as e:
            raise Denied(str(e)) from e
    if 'call_any' not in st['can'] and not _listed_host(url):
        raise Refused(f'{src.host_of(url)} is not in the x402 index; calling unlisted hosts '
                      f'needs score >= 40 (you are {st["score"]})')
    return url


def _headers(a):
    h = a.get('headers') or {}
    if isinstance(h, str):
        h = json.loads(h)
    # The payment headers are ours to set.
    return {k: v for k, v in h.items()
            if k.lower() not in ('x-payment', 'payment-signature', 'host', 'content-length')}


def _body(a):
    b = a.get('body')
    if isinstance(b, str) and b.strip()[:1] in ('{', '['):
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            return b
    return b


def _price(req, a):
    try:
        return src.offer(a)['price_usd']
    except Exception:                                   # noqa: BLE001
        return None


def _finish(st, url, status, headers, body, paid_by, req=None, offer=None, max_chars=20000):
    """Shape the paid reply and write the outcome that moves trust."""
    out = pay.reply(status, headers, body, max_chars=max_chars)
    rc = pay.receipt(headers)
    if rc is not None:
        out['receipt'] = rc
    ok = status < 400
    if status == 402:            # paid and still 402: the facilitator said why
        again = src.requirements(status, headers, body) or {}
        out['payment_error'] = (again.get('error') or (out.get('json') or {}).get('error')
                                or 'payment not accepted')
    usd = _price(req, offer) if offer else None
    out['paid'] = {'by': paid_by, 'usd': usd, 'settled': bool(rc and rc.get('success'))}
    if paid_by == 'house':
        # Money left the node if the facilitator says so, or the resource served.
        spent = (usd or 0) if (ok or out['paid']['settled']) else 0
        trust.log(st['id'], 'call', 'house' if spent else 'paid_fail', url, usd=spent,
                  detail={'status': status})
    elif paid_by == 'you':
        # Only a facilitator-confirmed settlement to a host some facilitator
        # lists counts as `settled` — a caller cannot farm trust off its own
        # fake 402 server.
        real = out['paid']['settled'] and rc.get('transaction') and _listed_host(url)
        trust.log(st['id'], 'call', 'settled' if real else ('ok' if ok else 'paid_fail'), url,
                  detail={'status': status, 'tx': (rc or {}).get('transaction')})
    else:
        trust.log(st['id'], 'call', 'ok' if ok else 'error', url, detail={'status': status})
    return out


def _payment_required(req, url, st, note):
    return {'status': 402, 'payment_required': True, 'url': url,
            'x402_version': req.get('x402Version'), 'offers': pay.offers(req), 'note': note,
            'next': 'x402_quote url=… (sign the typed data yourself), or pass payment=<base64 '
                    'X-PAYMENT/PAYMENT-SIGNATURE value> to x402_call'}


# ── actions ───────────────────────────────────────────────────────

def do_quote(a, st):
    """Unpaid request → the offer to pay and the EIP-712 data to sign."""
    _can(st, 'quote')
    url, method = _target(a, st), str(a.get('method') or 'GET').upper()
    body, headers = _body(a), _headers(a)
    status, rh, rb = pay.send(url, method, body, headers)
    req = src.requirements(status, rh, rb)
    if req is None:
        trust.log(st['id'], 'quote', 'quoted', url, detail={'status': status})
        return {'payment_required': False, 'status': status, 'url': url,
                'note': 'no x402 payment asked for — call it directly with x402_call',
                'preview': pay.reply(status, rh, rb, max_chars=2000)}
    payer = a.get('payer') or (st['id'] if st['kind'] == 'wallet' else None)
    try:
        i, offer = pay.pick(req, index=a.get('offer'), network=a.get('network'),
                            max_usd=_num(a.get('max_price_usd')))
    except pay.PayError as e:
        trust.log(st['id'], 'quote', 'quoted', url)
        return {**_payment_required(req, url, st, str(e)), 'next': 'pay another way: '
                'pass payment=<base64 payload you built for one of these offers> to x402_call'}
    out = {'payment_required': True, 'url': url, 'method': method,
           'offers': pay.offers(req), 'chosen': i, 'price_usd': _price(req, offer)}
    if not payer:
        out['note'] = 'pass payer=<your 0x address> to get typed data to sign'
        trust.log(st['id'], 'quote', 'quoted', url)
        return out
    auth = pay.authorization(offer, payer)
    typed = pay.typed_data(offer, auth)
    qid = _put_quote({'user': st['id'], 'url': url, 'method': method, 'body': body,
                      'headers': headers, 'req': req, 'offer': offer, 'auth': auth,
                      'typed': typed, 'expires': time.time() + QUOTE_TTL})
    trust.log(st['id'], 'quote', 'quoted', url)
    return {**out, 'quote_id': qid, 'payer': payer, 'typed_data': typed,
            'expires_in': QUOTE_TTL,
            'next': f'sign typed_data with {payer} (eth_signTypedData_v4), then '
                    f'x402_call quote_id={qid} signature=0x…'}


def do_call(a, st):
    """Call a service; pay if asked to, in the way the caller chose."""
    _can(st, 'call')
    max_chars = int(a.get('max_chars') or 20000)

    if a.get('quote_id'):                                 # own wallet, typed data signed
        q = _take_quote(a['quote_id'], st['id'])
        sig = str(_need(a, 'signature'))
        try:
            from eth_account import Account
            from eth_account.messages import encode_typed_data
            signer = Account.recover_message(encode_typed_data(full_message=q['typed']),
                                             signature=sig)
        except Exception as e:                            # noqa: BLE001
            raise ValueError(f'bad signature: {e}') from e
        if signer.lower() != q['auth']['from'].lower():
            raise ValueError(f'signature is from {signer}, quote is for {q["auth"]["from"]}')
        name, value = pay.payload(q['req'], q['offer'], sig, q['auth'])
        status, rh, rb = pay.send(q['url'], q['method'], q['body'], {**q['headers'], name: value})
        return _finish(st, q['url'], status, rh, rb, 'you', q['req'], q['offer'], max_chars)

    url, method = _target(a, st), str(a.get('method') or 'GET').upper()
    body, headers = _body(a), _headers(a)

    if a.get('payment'):                                  # a finished payload, relayed
        value = str(a['payment'])
        name = a.get('payment_header')
        if not name:
            try:
                v = pay.decode(value).get('x402Version', 1)
            except Exception:                             # noqa: BLE001
                v = 1
            name = 'PAYMENT-SIGNATURE' if int(v) >= 2 else 'X-PAYMENT'
        status, rh, rb = pay.send(url, method, body, {**headers, name: value})
        return _finish(st, url, status, rh, rb, 'you', max_chars=max_chars)

    status, rh, rb = pay.send(url, method, body, headers)
    req = src.requirements(status, rh, rb)
    if req is None:
        return _finish(st, url, status, rh, rb, None, max_chars=max_chars)

    mode = str(a.get('pay') or 'auto').lower()
    if mode == 'none':
        trust.log(st['id'], 'call', 'quoted', url)
        return _payment_required(req, url, st, 'not paid (pay="none")')
    p = trust.policy()
    why = None
    if not p['house']:
        why = 'this node does not pay for callers (policy house=false)'
    elif not st['house']['enabled']:
        why = st['house'].get('why') or 'the node does not pay for you'
    elif not pay.wallet():
        why = 'this node has no wallet'
    if why:
        if mode == 'house':
            raise Refused(why)
        trust.log(st['id'], 'call', 'quoted', url)
        return _payment_required(req, url, st, why)
    budget = st['house']['daily_usd'] - trust.spent_today(st['id'])
    pool = p['global_daily_usd'] - trust.spent_today()
    cap = min(p['max_call_usd'], budget, pool,
              _num(a.get('max_price_usd')) if a.get('max_price_usd') not in (None, '') else 1e9)
    try:
        _, offer = pay.pick(req, network=a.get('network'), max_usd=max(cap, 0))
    except pay.PayError as e:
        trust.log(st['id'], 'call', 'quoted', url)
        return _payment_required(req, url, st, f'{e}; your house budget left today '
                                 f'${max(budget, 0):.4f}, node ${max(pool, 0):.4f}')
    auth, sig = pay.house_sign(offer)
    name, value = pay.payload(req, offer, sig, auth)
    status, rh, rb = pay.send(url, method, body, {**headers, name: value})
    return _finish(st, url, status, rh, rb, 'house', req, offer, max_chars)


def do_probe(a, st):
    _can(st, 'probe')
    url = _target({**a, 'query': None}, st)
    r = src.probe(url, method=a.get('method') or 'GET')
    if r.get('x402') and a.get('pin', True) is not False:
        store.pin(r['service'])
    if r.get('service'):
        r['service'] = {k: v for k, v in r['service'].items() if k != 'raw'}
    trust.log(st['id'], 'probe', 'ok', url, detail={'x402': r.get('x402')})
    return r


def do_register(a, st):
    _can(st, 'register')
    hour = time.time() - 3600
    mine = trust.db().execute("SELECT COUNT(*) FROM events WHERE user=? AND tool='register'"
                              " AND outcome='ok' AND at>=?", (st['id'], hour)).fetchone()[0]
    every = trust.db().execute("SELECT COUNT(*) FROM events WHERE tool='register'"
                               " AND outcome='ok' AND at>=?", (hour,)).fetchone()[0]
    if st['tier'] != 'owner' and (mine >= 1 or every >= 30):
        raise Refused('registration is limited (1 per caller and 30 per node per hour)')
    r = trust.register(a.get('label'), owner=st['id'])
    trust.log(st['id'], 'register', 'ok', detail={'issued': r['id']})
    r['standing'] = trust.standing(r['id'])
    return r


def do_vouch(a, st):
    _can(st, 'vouch')
    r = trust.vouch(st['id'], str(_need(a, 'user')), a.get('score', 0))
    trust.log(st['id'], 'vouch', 'ok', detail={'subject': a['user'], 'score': a.get('score')})
    return r


def do_whoami(a, st):
    s = trust.standing(st['id'])
    s['recent'] = trust.events(st['id'], limit=int(a.get('limit') or 10))
    s['how_to_rise'] = ('time (tenure), using services on different days (activity), paying '
                        'for calls with your own wallet (settled, the biggest lever), vouches '
                        'from trusted callers; throttling and refused requests cost points')
    return s


def do_service(a, st):
    url = src.canonical(str(_need(a, 'url')))
    s = store.get(url)
    if not s:
        raise ValueError(f'not indexed: {url} — x402_search, or x402_probe it (trusted)')
    if not a.get('raw'):
        s.pop('raw', None)
    s['observed'] = trust.observed(url)
    return s


def do_set_trust(a, st):
    _can(st, 'admin')
    return trust.set_trust(str(_need(a, 'user')), pin=a.get('pin'), adjust=a.get('adjust'),
                           ban=a.get('ban'), note=a.get('note'), clear=bool(a.get('clear')))


def do_policy(a, st):
    changes = {k: v for k, v in (a.get('set') or {}).items()}
    if changes:
        _can(st, 'admin')
        return trust.set_policy(**changes)
    p = trust.policy()
    w = pay.wallet()
    return {**p, 'wallet': w['address'] if w else None, 'spent_today': trust.spent_today(),
            'tiers': {n: {'from': f, 'can': sorted(trust.CAN[n])} for f, n in trust.TIERS}}


def do_wallet(a, st):
    _can(st, 'admin')
    w = pay.wallet(create=bool(a.get('create')))
    return {'wallet': w, 'spent_today': trust.spent_today(), 'policy_house': trust.policy()['house'],
            'note': None if w else 'no house wallet — create=true makes one (fund it with USDC)'}


def do_revoke(a, st):
    uid = str(a.get('user') or st['id'])
    if uid != st['id']:
        _can(st, 'admin')
    return trust.revoke(uid)


# serve.py and mod.py hand over their Mod: a sync shares its lock and the
# semantic tools share its one embedding index (x402sem).
SYNC = None
MOD = None


def _mod():
    if MOD is None:
        raise ValueError('semantic search runs on the module server (POST /x402/mcp)')
    return MOD


def do_sync(a, st):
    _can(st, 'admin')
    if SYNC is None:
        raise ValueError('sync runs on the module server (POST /x402/api/sync)')
    return SYNC(source=a.get('source'), background=True)


# ── the tool table ────────────────────────────────────────────────

def _s(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


_N = {'type': 'number'}
_I = {'type': 'integer'}
_REQ = {
    'url': _s('Service URL (from x402_search)'),
    'method': _s('HTTP method', default='GET'),
    'query': {'type': 'object', 'description': 'Query-string parameters'},
    'body': {'description': 'Request body: an object (sent as JSON) or a string'},
    'headers': {'type': 'object', 'description': 'Extra request headers'},
}

TOOLS = {
    'x402_search': {
        'fn': lambda a, st: store.search(
            q=a.get('query'), network=a.get('network'), host=a.get('host'), source=a.get('source'),
            max_price=_num(a.get('max_price_usd')), min_price=_num(a.get('min_price_usd')),
            priced=bool(a.get('priced')), sort=a.get('sort') or 'popular',
            limit=int(a.get('limit') or 20), offset=int(a.get('offset') or 0)),
        'cap': 'read', 'ro': True,
        'description': 'Search every known x402 paid API (full-text prefix words over url, host, '
                       'name, description, tags). Prices are USD per call.',
        'schema': {'type': 'object', 'properties': {
            'query': _s('e.g. "weather", "image generation", "token price"'),
            'network': _s('Payment network, e.g. base, solana, polygon'),
            'host': _s('Only this provider host'), 'source': _s('Only services this facilitator lists'),
            'max_price_usd': _N, 'min_price_usd': _N,
            'priced': {'type': 'boolean', 'description': 'Only services with a known USD price'},
            'sort': _s('Order', enum=list(store.SORTS)),
            'limit': {**_I, 'default': 20, 'maximum': 500}, 'offset': _I}},
    },
    'x402_find': {
        'fn': lambda a, st: _mod().find(q=_need(a, 'q'), kind='all', k=a.get('k') or 10,
                                        network=a.get('network'), max_price=a.get('max_price')),
        'cap': 'read', 'ro': True,
        'description': 'Search every x402 service by MEANING (local encoder + full-text fused): '
                       'say what you need in plain words. Returns url, method, price, networks, why.',
        'schema': {'type': 'object', 'required': ['q'], 'properties': {
            'q': _s('What you need, in plain words'), 'k': {**_I, 'default': 10},
            'network': _s('Only this payment network family, e.g. base, solana'),
            'max_price': {**_N, 'description': 'Max USD per call'}}},
    },
    'x402_mcp': {
        'fn': lambda a, st: _mod().mcp_servers(q=_need(a, 'q'), k=a.get('k') or 10,
                                               network=a.get('network'), max_price=a.get('max_price')),
        'cap': 'read', 'ro': True,
        'description': 'Find the right paid MCP server for a job, by meaning: servers ranked with '
                       'their matching tools, prices and a `claude mcp add` line.',
        'schema': {'type': 'object', 'required': ['q'], 'properties': {
            'q': _s('The job, in plain words'), 'k': {**_I, 'default': 10},
            'network': _s('Payment network family'), 'max_price': {**_N, 'description': 'Max USD per call'}}},
    },
    'x402_service': {
        'fn': do_service, 'cap': 'read', 'ro': True,
        'description': 'One service in full: offers (network, asset, price, payTo), which '
                       'facilitators list it, and how calls through this node have gone (observed).',
        'schema': {'type': 'object', 'required': ['url'], 'properties': {
            'url': _s('Service URL'), 'raw': {'type': 'boolean', 'description': 'Include the raw listing'}}},
    },
    'x402_providers': {
        'fn': lambda a, st: store.hosts(q=a.get('query'), limit=int(a.get('limit') or 50),
                                        offset=int(a.get('offset') or 0)),
        'cap': 'read', 'ro': True,
        'description': 'Providers: services grouped by host, busiest first.',
        'schema': {'type': 'object', 'properties': {'query': _s('Filter by host or name'),
                                                    'limit': _I, 'offset': _I}},
    },
    'x402_stats': {
        'fn': lambda a, st: {**store.stats(), 'last_sync': store.meta('last_sync')},
        'cap': 'read', 'ro': True,
        'description': 'Size of the index: services, hosts, networks, median price, sources.',
        'schema': {'type': 'object', 'properties': {}},
    },
    'x402_facilitators': {
        'fn': lambda a, st: store.get_sources(),
        'cap': 'read', 'ro': True,
        'description': 'Every facilitator this node reads, whether it lists, and how many services it gave.',
        'schema': {'type': 'object', 'properties': {}},
    },
    'x402_call': {
        'fn': do_call, 'cap': 'call', 'ro': False, 'spends': True,
        'description': 'Call an x402 service. Unpaid first; on 402: pay="none" returns the offers; '
                       'quote_id+signature (from x402_quote) or payment=<base64 payload> pays with '
                       'YOUR wallet; pay="auto"/"house" lets the node wallet pay if you are trusted '
                       'and within budget. Returns status, parsed body, payment receipt.',
        'schema': {'type': 'object', 'properties': {
            **_REQ,
            'pay': _s('auto | none | house', enum=['auto', 'none', 'house'], default='auto'),
            'quote_id': _s('From x402_quote — replays that exact request with your signature'),
            'signature': _s('Your EIP-712 signature over the quote typed_data'),
            'payment': _s('A finished base64 X-PAYMENT (v1) / PAYMENT-SIGNATURE (v2) value'),
            'payment_header': _s('Header name for payment, if not inferable'),
            'max_price_usd': {**_N, 'description': 'Never pay more than this per call'},
            'network': _s('Prefer an offer on this network'),
            'max_chars': {**_I, 'default': 20000}}},
    },
    'x402_quote': {
        'fn': do_quote, 'cap': 'quote', 'ro': True,
        'description': 'Ask a service its price and get EIP-712 typed data (EIP-3009 '
                       'transferWithAuthorization) to sign with your own wallet; returns quote_id. '
                       'payer defaults to your wallet if you signed in with one.',
        'schema': {'type': 'object', 'required': ['url'], 'properties': {
            **_REQ, 'payer': _s('Your 0x address (the `from` of the transfer)'),
            'offer': {**_I, 'description': 'Index into offers; default = cheapest signable'},
            'network': _s('Prefer this network'), 'max_price_usd': _N}},
    },
    'x402_probe': {
        'fn': do_probe, 'cap': 'probe', 'ro': False,
        'description': 'Ask any public URL for its 402; an x402 resource is added to the index '
                       'and pinned. Trusted callers (score >= 40).',
        'schema': {'type': 'object', 'required': ['url'], 'properties': {
            'url': _s('URL'), 'method': _s('HTTP method', default='GET'),
            'pin': {'type': 'boolean', 'default': True}}},
    },
    'x402_whoami': {
        'fn': do_whoami, 'cap': 'read', 'ro': True,
        'description': 'Who this node thinks you are: id, trust score 0-100 with its breakdown, '
                       'tier, what you can do, rate limit, house budget, recent activity.',
        'schema': {'type': 'object', 'properties': {'limit': _I}},
    },
    'x402_trust': {
        'fn': lambda a, st: trust.trust(str(_need(a, 'user'))), 'cap': 'read', 'ro': True,
        'description': 'Trust score and breakdown for any caller id (0x…, key:…, anon:…).',
        'schema': {'type': 'object', 'required': ['user'], 'properties': {'user': _s('Caller id')}},
    },
    'x402_users': {
        'fn': lambda a, st: trust.users(limit=int(a.get('limit') or 50), kind=a.get('kind')),
        'cap': 'read', 'ro': True,
        'description': 'Every MCP caller this node has seen, by trust: score, tier, calls, '
                       'settled payments, house spend.',
        'schema': {'type': 'object', 'properties': {
            'limit': _I, 'kind': _s('wallet | key | anon', enum=['wallet', 'key', 'anon'])}},
    },
    'x402_register': {
        'fn': do_register, 'cap': 'register', 'ro': False,
        'description': 'Get an API key (shown once) so you have a stable identity without a '
                       'wallet. Send it as "Authorization: Bearer x402_…". Starts at score 20.',
        'schema': {'type': 'object', 'properties': {'label': _s('A name for your agent')}},
    },
    'x402_vouch': {
        'fn': do_vouch, 'cap': 'vouch', 'ro': False,
        'description': 'Rate another caller 1..10 (0 withdraws). Weighted by your own trust; '
                       'moves their score by at most ±15 in total. Trusted callers only.',
        'schema': {'type': 'object', 'required': ['user', 'score'], 'properties': {
            'user': _s('Caller id'), 'score': {**_I, 'minimum': 0, 'maximum': 10}}},
    },
    'x402_revoke': {
        'fn': do_revoke, 'cap': 'read', 'ro': False,
        'description': 'Revoke API keys — your own, or (owner) anyone\'s.',
        'schema': {'type': 'object', 'properties': {'user': _s('Caller id; default yourself')}},
    },
    'x402_policy': {
        'fn': do_policy, 'cap': 'read', 'ro': True,
        'description': 'The node\'s spending and tier policy. Owner: set={...} changes it '
                       '(house, max_call_usd, global_daily_usd, house_daily_usd, rpm, owners).',
        'schema': {'type': 'object', 'properties': {'set': {'type': 'object'}}},
    },
    'x402_set_trust': {
        'fn': do_set_trust, 'cap': 'admin', 'ro': False,
        'description': 'Owner: pin a caller\'s score, adjust it (±), ban/unban, note, or clear.',
        'schema': {'type': 'object', 'required': ['user'], 'properties': {
            'user': _s('Caller id'), 'pin': {'type': ['integer', 'null']}, 'adjust': _I,
            'ban': {'type': 'boolean'}, 'note': _s('Why'), 'clear': {'type': 'boolean'}}},
    },
    'x402_wallet': {
        'fn': do_wallet, 'cap': 'admin', 'ro': False,
        'description': 'Owner: the node\'s house wallet address and today\'s spend; create=true makes one.',
        'schema': {'type': 'object', 'properties': {'create': {'type': 'boolean'}}},
    },
    'x402_sync': {
        'fn': do_sync, 'cap': 'admin', 'ro': False,
        'description': 'Owner: re-crawl every facilitator (or one: source=<id>) in the background.',
        'schema': {'type': 'object', 'properties': {'source': _s('Source id')}},
    },
}

# Tools whose failures are the caller's fault in a way that should cost trust
# are raised as Denied; everything else is just an error result.


def tool_list(st=None):
    out = []
    for n, t in TOOLS.items():
        floor = next((f for f, tn in reversed(trust.TIERS) if t['cap'] in trust.CAN[tn]), None)
        out.append({
            'name': n, 'description': t['description'], 'inputSchema': t['schema'],
            'annotations': {'readOnlyHint': t['ro'], 'openWorldHint': n in (
                'x402_call', 'x402_quote', 'x402_probe'),
                'destructiveHint': False, 'idempotentHint': t['ro']},
            '_meta': {'access': t['cap'], 'min_score': floor if floor is not None else 'owner',
                      'cost': 'spends' if t.get('spends') else 'free',
                      'allowed': (t['cap'] in st['can']) if st else None},
        })
    return out


def call_tool(name, args, who):
    """Run one tool as `who` ({id, kind}). Gate → run → log → result."""
    t = TOOLS.get(name)
    if not t:
        return _err(f'unknown tool {name!r}')
    st = trust.standing(who['id'])
    if not trust.allow(who['id'], st['rpm']):
        trust.log(who['id'], name, 'throttled')
        return _err(f'rate limit: {st["rpm"]} calls/min for tier {st["tier"]} — slow down '
                    '(each throttle costs 2 trust points)')
    try:
        out = t['fn'](args or {}, st)
        if t['cap'] == 'read' and name != 'x402_revoke':
            trust.log(who['id'], name, 'read')
    except Refused as e:
        trust.log(who['id'], name, 'refused', (args or {}).get('url'), detail=str(e))
        return _err(f'refused: {e}')
    except Denied as e:
        trust.log(who['id'], name, 'denied', (args or {}).get('url'), detail=str(e))
        return _err(f'denied: {e} (refused requests cost 5 trust points)')
    except Exception as e:                                # noqa: BLE001 — goes back to the model
        trust.log(who['id'], name, 'error', (args or {}).get('url'), detail=f'{type(e).__name__}: {e}')
        return _err(f'{type(e).__name__}: {e}')
    return {'content': [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False, default=str)}],
            'structuredContent': out if isinstance(out, dict) else {'result': out},
            'isError': False}


def _err(msg):
    return {'content': [{'type': 'text', 'text': msg}], 'isError': True}


def handle_message(msg, who):
    """One JSON-RPC message (or batch) → response, or None for notifications."""
    if isinstance(msg, list):
        out = [r for r in (handle_message(m, who) for m in msg) if r is not None]
        return out or None
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'invalid request'}}
    mid, method, p = msg.get('id'), msg.get('method', ''), msg.get('params') or {}
    if mid is None:
        return None
    try:
        if method == 'initialize':
            v = p.get('protocolVersion')
            res = {'protocolVersion': v if v in SUPPORTED else PROTOCOL_VERSION,
                   'capabilities': {'tools': {'listChanged': False}},
                   'serverInfo': SERVER_INFO, 'instructions': INSTRUCTIONS}
        elif method == 'ping':
            res = {}
        elif method == 'tools/list':
            res = {'tools': tool_list(trust.standing(who['id']))}
        elif method == 'tools/call':
            res = call_tool(p.get('name', ''), p.get('arguments') or {}, who)
        else:
            return {'jsonrpc': '2.0', 'id': mid,
                    'error': {'code': -32601, 'message': f'no method {method}'}}
    except Exception as e:                                # noqa: BLE001
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32603, 'message': str(e)}}
    return {'jsonrpc': '2.0', 'id': mid, 'result': res}


def stdio_caller():
    """stdio runs as this box (owner) unless told to act as someone else."""
    h = {}
    if os.environ.get('X402_MCP_KEY'):
        h['authorization'] = 'Bearer ' + os.environ['X402_MCP_KEY']
    if os.environ.get('X402_MCP_TOKEN'):
        h['token'] = os.environ['X402_MCP_TOKEN']
    return trust.identify(h, local=not h)


def main():
    who = stdio_caller()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle_message(json.loads(line), who)
        except json.JSONDecodeError:
            resp = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}
        except Exception:                                 # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            continue
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


def _attach():
    """Run standalone (stdio): load the anchor by path — `mod` is the
    protocol's name — so sync and semantic search work here too."""
    global SYNC, MOD
    import importlib.util
    spec = importlib.util.spec_from_file_location('x402_anchor', os.path.join(HERE, 'mod.py'))
    anchor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(anchor)
    MOD = anchor.Mod()
    SYNC = MOD.sync


if __name__ == '__main__':
    _attach()
    main()
