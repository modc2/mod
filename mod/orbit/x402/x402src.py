"""x402src — where x402 services come from, and how they are read.

Everything that knows about the outside world lives here; nothing here knows
about storage. Three kinds of source, all keyless, all plain HTTP:

    facilitator   any x402 facilitator exposing the spec's discovery API,
                  ``GET {base}/discovery/resources?limit&offset`` — Coinbase's
                  CDP Bazaar, PayAI, and whichever others answer it
    ecosystem     the partner registry in github.com/coinbase/x402 — every
                  listed project, and with it every facilitator's base URL,
                  which is how new facilitators get found without anyone
                  editing a list here
    probe         one URL, asked directly: an x402 resource answers an unpaid
                  request with 402 and its own payment requirements

``normalize`` turns any of the item shapes in the wild (x402 v1, v2, the
Bazaar extensions) into one flat row. Stdlib only — no SDK, no requests.
"""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = 'mod-x402-index/0.1 (+local-first aggregator)'
TIMEOUT = 30

# Facilitators known to list publicly. Seeds only — the ecosystem registry
# adds the rest, and a box owner can add their own with add_source.
SEEDS = [
    {'id': 'cdp', 'name': 'Coinbase CDP Bazaar',
     'base_url': 'https://api.cdp.coinbase.com/platform/v2/x402'},
    {'id': 'payai', 'name': 'PayAI',
     'base_url': 'https://facilitator.payai.network'},
]

ECOSYSTEM_REPO = 'coinbase/x402'
ECOSYSTEM_DIR = 'typescript/site/app/ecosystem/partners-data'

# CAIP-2 → the short names x402 v1 used. Unknown ids pass through untouched.
NETWORKS = {
    'eip155:1': 'ethereum', 'eip155:8453': 'base', 'eip155:84532': 'base-sepolia',
    'eip155:137': 'polygon', 'eip155:80002': 'polygon-amoy',
    'eip155:42161': 'arbitrum', 'eip155:10': 'optimism', 'eip155:56': 'bsc',
    'eip155:43114': 'avalanche', 'eip155:43113': 'avalanche-fuji',
    'eip155:1329': 'sei', 'eip155:1328': 'sei-testnet', 'eip155:196': 'xlayer',
    'eip155:4689': 'iotex', 'eip155:3338': 'peaq', 'eip155:480': 'worldchain',
    'eip155:42220': 'celo', 'eip155:143': 'monad', 'eip155:999': 'hyperevm',
    'eip155:2741': 'abstract', 'eip155:59144': 'linea', 'eip155:130': 'unichain',
    'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp': 'solana',
    'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1': 'solana-devnet',
    'solana:mainnet': 'solana', 'stellar:pubnet': 'stellar',
    'xrpl:0': 'xrpl', 'xrpl:mainnet': 'xrpl', 'eip155:146': 'sonic',
    'eip155:50': 'xdc', 'eip155:4326': 'megaeth', 'eip155:5042': 'arc',
    'eip155:5042002': 'arc-testnet', 'eip155:4663': 'tempo',
    'eip155:1187947933': 'skale-base', 'eip155:324705682': 'skale-base-sepolia',
}
# Families whose chain id varies by spelling (algorand's genesis hash is
# truncated by some facilitators) collapse to the family name.
NETWORK_FAMILIES = ('algorand:',)

# Dollar-pegged tokens. An amount is only turned into USD when the asset is
# one of these — a wrong price is worse than no price.
USD_NAMES = {'usd coin', 'usdc', 'usd₮0', 'usdt0', 'tether usd', 'usdt',
             'global dollar', 'usdg', 'world liberty financial usd', 'usd1',
             'united stables', 'paypal usd', 'pyusd'}
USD_ASSETS = {
    '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',   # USDC base
    '0x036cbd53842c5426634e7929541ec2318f3dcf7e',   # USDC base-sepolia
    '0x3c499c542cef5e3811e1192ce70d8cc03d5c3359',   # USDC polygon
    '0x41e94eb019c0762f9bfcf9fb1e58725bfb0e7582',   # USDC polygon-amoy
    '0xaf88d065e77c8cc2239327c5edb3a432268e5831',   # USDC arbitrum
    '0x0b2c639c533813f4aa9d7837caf62653d097ff85',   # USDC optimism
    '0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e',   # USDC avalanche
    '0x5425890298aed601595a70ab815c96711a31bc65',   # USDC fuji
    '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',   # USDC ethereum
    '0xe15fc38f6d8c56af07bbcbe3baf5708a2bf42392',   # USDC sei
    'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',  # USDC solana
    '4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU',  # USDC solana-devnet
    '31566704',                                      # USDC algorand (ASA)
}
USD_ASSETS = {a.lower() for a in USD_ASSETS}


class SourceError(Exception):
    pass


# ── transport ─────────────────────────────────────────────────────

def fetch(url, timeout=TIMEOUT, accept='application/json', method='GET'):
    """HTTP → (status, headers, body bytes). Never raises on an HTTP status.
    A non-GET carries an empty JSON body — enough to reach a paywall."""
    data = b'{}' if method not in ('GET', 'HEAD', 'DELETE') else None
    headers = {'User-Agent': UA, 'Accept': accept}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b''
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f'{url}: {getattr(e, "reason", e)}') from e


def fetch_json(url, timeout=TIMEOUT):
    status, _, body = fetch(url, timeout)
    if status != 200:
        raise SourceError(f'{url}: HTTP {status}')
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SourceError(f'{url}: not JSON') from e


# ── normalization ─────────────────────────────────────────────────

def network_name(net):
    net = net or ''
    if net in NETWORKS:
        return NETWORKS[net]
    for fam in NETWORK_FAMILIES:
        if net.startswith(fam):
            return fam.rstrip(':')
    return net


def _int(v):
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def offer(a):
    """One entry of `accepts` → a flat offer with a USD price when knowable."""
    extra = a.get('extra') or {}
    asset = a.get('asset') or a.get('currency') or ''
    amount = _int(a.get('amount') if a.get('amount') is not None
                  else a.get('maxAmountRequired'))
    aname = (extra.get('name') or '').strip()
    usd = asset.lower() in USD_ASSETS or aname.lower() in USD_NAMES
    decimals = _int(extra.get('decimals'))
    if decimals is None and usd:
        decimals = 6
    price = amount / 10 ** decimals if (usd and amount is not None) else None
    return {
        'network': network_name(a.get('network')),
        'scheme': a.get('scheme') or 'exact',
        'asset': asset,
        'asset_name': aname or ('USDC' if usd else ''),
        'amount': amount,
        'price_usd': price,
        'pay_to': a.get('payTo') or a.get('recipient') or '',
    }


def _method(item, accepts):
    info = ((item.get('extensions') or {}).get('bazaar') or {}).get('info') or {}
    m = (info.get('input') or {}).get('method') or item.get('method')
    for a in accepts:
        if m:
            break
        m = ((a.get('outputSchema') or {}).get('input') or {}).get('method')
    return (m or 'GET').upper()


def host_of(url):
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return ''


def canonical(url):
    """Lower-case scheme+host, no fragment, no trailing slash on the path."""
    p = urllib.parse.urlsplit((url or '').strip())
    path = p.path.rstrip('/') if p.path not in ('', '/') else ''
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ''))


def normalize(item):
    """Any discovery item / 402 body → one service row, or None if unusable."""
    url = item.get('resource') or item.get('resourceUrl') or item.get('url')
    if isinstance(url, dict):                     # v2 402 body: {url, description}
        url = url.get('url')
    accepts = [a for a in (item.get('accepts') or []) if isinstance(a, dict)]
    if not url and accepts:
        url = accepts[0].get('resource')
    if not url or not str(url).startswith(('http://', 'https://')):
        return None
    meta = item.get('metadata') or {}
    offers = [offer(a) for a in accepts]
    prices = [o['price_usd'] for o in offers if o['price_usd'] is not None]
    desc = (item.get('description') or meta.get('description')
            or next((a.get('description') for a in accepts if a.get('description')), '') or '')
    quality = item.get('quality') or {}
    tags = item.get('tags') or meta.get('tags') or []
    return {
        'url': canonical(url),
        'host': host_of(url),
        'name': item.get('serviceName') or meta.get('name') or '',
        'description': str(desc)[:2000],
        'method': _method(item, accepts),
        'type': item.get('type') or 'http',
        'tags': [str(t) for t in tags if t][:20] if isinstance(tags, list) else [],
        'icon': item.get('iconUrl') or '',
        'x402_version': _int(item.get('x402Version')) or 1,
        'networks': sorted({o['network'] for o in offers if o['network']}),
        'price_usd': min(prices) if prices else None,
        'offers': offers,
        # CDP reports a 30-day window; GoPlausible only a lifetime settle count.
        'calls_30d': _int(quality.get('l30DaysTotalCalls')) or _int(item.get('settleCount')) or 0,
        'payers_30d': _int(quality.get('l30DaysUniquePayers')) or 0,
        'last_updated': item.get('lastUpdated') or item.get('lastSeen') or '',
        'raw': item,
    }


# ── facilitators: the spec's discovery API ────────────────────────

def discovery_url(base, limit, offset):
    q = urllib.parse.urlencode({'limit': limit, 'offset': offset})
    return f'{base.rstrip("/")}/discovery/resources?{q}'


def lists(base, timeout=12):
    """Does this facilitator answer the discovery API? → total or None."""
    try:
        d = fetch_json(discovery_url(base, 1, 0), timeout)
    except SourceError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get('items'), list):
        return None
    return int((d.get('pagination') or {}).get('total') or len(d['items']))


def crawl(base, page=1000, max_items=200_000, pause=0.2, on_page=None):
    """Yield every normalized service a facilitator lists, page by page.

    Pages by the facilitator's own reported limit (some cap below what is
    asked) and stops on an empty or short page, or at the reported total.
    """
    offset, total, seen = 0, None, 0
    while offset < max_items:
        try:
            d = fetch_json(discovery_url(base, page, offset))
        except SourceError as e:
            # Some facilitators reject a large limit outright; step down once.
            if offset == 0 and page > 100 and 'HTTP 400' in str(e):
                page = 100
                continue
            raise
        items = d.get('items') or []
        pg = d.get('pagination') or {}
        total = _int(pg.get('total')) if pg.get('total') is not None else total
        if on_page:
            on_page(offset, total)
        for it in items:
            row = normalize(it) if isinstance(it, dict) else None
            if row:
                seen += 1
                yield row
        step = _int(pg.get('limit')) or len(items)
        if not items or step <= 0:
            break
        offset += len(items)
        if total is not None and offset >= total:
            break
        if pause:
            time.sleep(pause)


# ── ecosystem registry ────────────────────────────────────────────

def ecosystem(workers=16):
    """Every partner in coinbase/x402's ecosystem registry, metadata included."""
    listing = fetch_json(f'https://api.github.com/repos/{ECOSYSTEM_REPO}/contents/{ECOSYSTEM_DIR}')
    slugs = [x['name'] for x in listing if isinstance(x, dict) and x.get('type') == 'dir']
    raw = f'https://raw.githubusercontent.com/{ECOSYSTEM_REPO}/main/{ECOSYSTEM_DIR}'

    def one(slug):
        try:
            m = fetch_json(f'{raw}/{slug}/metadata.json', timeout=20)
        except SourceError:
            return None
        fac = m.get('facilitator') if isinstance(m.get('facilitator'), dict) else None
        return {
            'slug': slug,
            'name': m.get('name') or slug,
            'category': m.get('category') or '',
            'description': m.get('description') or '',
            'website': m.get('websiteUrl') or '',
            'logo': (f'https://raw.githubusercontent.com/{ECOSYSTEM_REPO}/main/typescript/site/public{m["logoUrl"]}'
                     if str(m.get('logoUrl') or '').startswith('/') else m.get('logoUrl') or ''),
            'facilitator': fac,
        }

    with ThreadPoolExecutor(workers) as ex:
        return [p for p in ex.map(one, slugs) if p]


# ── probe: ask one resource directly ──────────────────────────────

def probe(url, method='GET', timeout=15):
    """Call a URL unpaid. An x402 resource answers 402 with its requirements —
    in the body (v1) or base64 in the PAYMENT-REQUIRED header (v2)."""
    method = (method or 'GET').upper()
    status, headers, body = fetch(url, timeout, method=method)
    hdr = {k.lower(): v for k, v in headers.items()}
    if status != 402:
        return {'x402': False, 'status': status, 'url': url, 'method': method}
    req = None
    enc = hdr.get('payment-required')
    if enc:
        try:
            req = json.loads(base64.b64decode(enc + '=' * (-len(enc) % 4)))
        except (ValueError, json.JSONDecodeError):
            req = None
    if req is None:
        try:
            req = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            req = None
    if not isinstance(req, dict) or not req.get('accepts'):
        return {'x402': False, 'status': 402, 'url': url,
                'error': '402 without x402 payment requirements'}
    item = dict(req)
    res = req.get('resource')
    if isinstance(res, dict):                     # v2: resource is an object
        item.setdefault('description', res.get('description'))
        item['resource'] = res.get('url') or url
    item.setdefault('resource', url)
    row = normalize(item)
    if row and method != 'GET':
        row['method'] = method
    return {'x402': row is not None, 'status': 402, 'url': url, 'service': row}
