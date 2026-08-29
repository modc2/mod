"""
Attribution — the claim that travels with a name.

A name that resolves tells you where a module is answering. It does not tell
you *whose* module answered, or which code that is. The mod protocol already
knows both, and states them in two different registers:

  declared   `config.json` carries `owner` (an address) and `schema` (the
             module's IPFS CID), plus `version`, `anchor` and `protocol`. This
             is what the module says about itself, and it is what the on-chain
             Registry maps a module name to.

  attested   `m.info(<mod>)` is the protocol's module card — name, key, ports,
             functions, schema — signed by the key of the box that serves it,
             and checkable with `m.verify_info`. This is what the *host* says,
             under a signature, about the module it is running.

Neither travels with a DNS answer today, which is the gap this file closes.
Every derived module name gets a TXT record beside its address:

    _mod.eth.modc2.com.  TXT  "v=mod1 mod=eth key=0x7d7c… cid=Qmau24…
                               ver=1.0.0 orbit=orbit app=/eth api=/api/eth"

and the host itself gets one that names the deployment:

    _mod.modc2.com.      TXT  "v=mod1 host=modc2.com key=0x7d7c…
                               owner=0x… mods=52 app=/{mod} api=/api/{mod}"

The prefixed-underscore name is deliberate: `_mod` cannot collide with a module
called `mod`, because a leading underscore is not a legal label in a hostname,
which is exactly why SPF, DKIM and ACME all live under one.

Two honesty rules hold this together. The record only ever repeats what the
protocol already asserts — nothing here mints an owner, and a module with no
declared owner publishes none rather than inheriting the host's. And `key=` is
the box, not the author: it says *this deployment serves this CID*, which is a
claim a signature can back (`attest()` produces the signed card and checks it),
while `owner=` is the module's own declaration, which DNS can only repeat.
"""
import fleet
import settings

VERSION = 'mod1'
PREFIX = '_mod'

# Values go on the wire in a single space-separated TXT string, so a value that
# contains a space would silently split into two fields. Everything published
# here is an address, a CID, a version or a path — none of which may contain
# one — so a stray space means bad data, and dropping the field is better than
# publishing a record that parses back wrong.
SAFE = str.isprintable


def _pairs(items):
    out = []
    for k, v in items:
        if v is None or v == '':
            continue
        v = str(v)
        if ' ' in v or not SAFE(v):
            continue
        out.append(f'{k}={v}')
    return ' '.join(out)


def parse(text):
    """A TXT string back into fields. The inverse of what we publish."""
    out = {}
    for token in str(text or '').split():
        k, _, v = token.partition('=')
        if k and v:
            out.setdefault(k, v)
    return out


# ── the cards ────────────────────────────────────────────────────────────

def card(name, host=None):
    """What the protocol attributes one module by.

    Routed or not: a module that has not declared `route: true` still has an
    owner and a CID, it just has no derived name to hang them on. Saying so is
    more useful than a 404.
    """
    host = host or settings.host()
    n = (name or '').strip().lower()
    entry = fleet.module(n)
    cfg, path = fleet.config(n)
    if entry is None and cfg is None:
        return None
    cfg = cfg or {}
    out = {
        'mod': n,
        'routed': entry is not None,
        'owner': (str(cfg.get('owner')).lower() if cfg.get('owner') else None),
        'key': fleet.box_key(),
        'cid': cfg.get('schema') or cfg.get('cid') or None,
        'version': cfg.get('version') or None,
        'anchor': cfg.get('anchor') or None,
        'protocol': cfg.get('protocol') or None,
        'orbit': (entry or {}).get('root') or ('orbit' if path and '/orbit/' in path else None),
        'path': path or (entry or {}).get('dir'),
        'description': (cfg.get('description') or '')[:200],
        'app': f'/{n}', 'api': f'/api/{n}',
        'name': f'{PREFIX}.{n}.{host}' if entry else None,
        'urls': fleet.urls(n, host),
    }
    out['attributed_to'] = out['owner'] or (
        f'nobody in particular — {n} declares no owner, so the only claim on '
        f'record is that this box ({out["key"]}) serves it')
    out['txt'] = txt(out)
    return out


def txt(c):
    """One module card as the TXT string that goes on the wire."""
    return _pairs([
        ('v', VERSION), ('mod', c['mod']), ('owner', c.get('owner')),
        ('key', c.get('key')), ('cid', c.get('cid')), ('ver', c.get('version')),
        ('orbit', c.get('orbit')), ('app', c.get('app')), ('api', c.get('api')),
    ])


def deployment(host=None):
    """What this box is, as one record: the host, its key, its owner, its size."""
    import identity
    host = host or settings.host()
    ms = fleet.modules()
    owned = [m for m in ms if m.get('owner')]
    return {
        'host': host,
        'key': fleet.box_key(),
        'owner': identity.owner(),
        'modules': len(ms),
        'with_owner': len(owned),
        'with_cid': sum(1 for m in ms if m.get('schema')),
        'app': '/{mod}', 'api': '/api/{mod}',
        'name': f'{PREFIX}.{host}',
    }


def deployment_txt(d):
    return _pairs([
        ('v', VERSION), ('host', d['host']), ('key', d.get('key')),
        ('owner', d.get('owner')), ('mods', d.get('modules')),
        ('app', d.get('app')), ('api', d.get('api')),
    ])


# ── the records ──────────────────────────────────────────────────────────

def records(zone_name, derive_modules=True):
    """The attribution TXT records derived for a zone.

    Called from `zone.derived()` on every answer, so it does no I/O of its own
    beyond the fleet cache: the fields were read once when the module list was
    built.
    """
    zn = zone_name
    out = []

    def add(name, value, why):
        out.append({'name': name, 'type': 'TXT', 'value': value, 'why': why})

    d = deployment(zn)
    add(PREFIX, deployment_txt(d),
        'this deployment, as the protocol states it — the host, the key that '
        'signs its module cards, its owner, and how many modules it serves')
    if not derive_modules:
        return out
    for m in fleet.modules():
        line = txt({
            'mod': m['name'], 'owner': m.get('owner'), 'key': d['key'],
            'cid': m.get('schema'), 'version': m.get('version'),
            'orbit': m.get('root'), 'app': f'/{m["name"]}',
            'api': f'/api/{m["name"]}',
        })
        add(f'{PREFIX}.{m["name"]}', line,
            f'who {m["name"]} is attributed to, and which code it is — '
            f'{"owner " + m["owner"] if m.get("owner") else "no declared owner"}'
            f'{", cid " + m["schema"] if m.get("schema") else ""}')
    return out


# ── the attestation ──────────────────────────────────────────────────────

def attest(name):
    """The protocol's signed module card, and whether its signature holds.

    This is the part DNS cannot do. A TXT record is a statement anyone who
    holds the zone can write; `m.info()` is the same statement signed by the
    box's key, so a caller who fetched the record can ask the module for the
    card and check the two agree.
    """
    n = (name or '').strip().lower()
    try:
        from protocol import protocol
        m = protocol()
    except Exception as e:                                   # noqa: BLE001
        return {'mod': n, 'signed': False,
                'error': f'the protocol package is not importable here: {e}'}
    try:
        info = m.info(n)
    except Exception as e:                                   # noqa: BLE001
        return {'mod': n, 'signed': False,
                'error': f'no module card for {n!r}: {e}'}
    verified = None
    if info.get('signature'):
        try:
            m.verify_info(dict(info))
            verified = True
        except Exception:                                    # noqa: BLE001
            verified = False
    return {
        'mod': n, 'signed': bool(info.get('signature')), 'verified': verified,
        'key': info.get('key'), 'card': info,
        'means': ('the card is signed by this box\'s key and the signature '
                  'checks out — the host attests it serves this module'
                  if verified else
                  'the card carries no valid signature, so treat it as the '
                  'zone\'s word rather than the box\'s'),
    }


def report(name=None, host=None, verify=False):
    """Attribution for one module — or the whole fleet — in one object.

    The read behind `GET /attribution` and `dns_attribution`. With no name it
    answers for the deployment: every routed module, what it is attributed to,
    and the counts that say how much of the fleet actually declares anything.
    """
    host = host or settings.host()
    if name:
        c = card(name, host)
        if not c:
            return {'mod': (name or '').lower(), 'found': False,
                    'why': f'no module named {name!r} under {", ".join(str(r) for r in fleet.ROOTS)}'}
        out = {'found': True, 'host': host, 'card': c,
               'record': {'name': c['name'] or f'{PREFIX}.{c["mod"]}.{host}',
                          'type': 'TXT', 'value': c['txt'],
                          'published': bool(c['name'])},
               'means': c['attributed_to']}
        if not c['routed']:
            out['record']['why_unpublished'] = (
                f'{c["mod"]} does not set "route": true, so the zone derives no '
                f'name for it and nothing is published. The attribution is '
                f'still true — it just has nowhere to live in DNS yet.')
        if verify:
            out['attestation'] = attest(c['mod'])
        return out

    ms = fleet.modules()
    d = deployment(host)
    return {
        'host': host,
        'deployment': dict(d, txt=deployment_txt(d)),
        'counts': {'routed': len(ms), 'with_owner': d['with_owner'],
                   'with_cid': d['with_cid'],
                   'unattributed': len(ms) - d['with_owner']},
        'modules': [{'mod': m['name'], 'owner': m.get('owner'),
                     'cid': m.get('schema'), 'version': m.get('version'),
                     'orbit': m.get('root'),
                     'name': f'{PREFIX}.{m["name"]}.{host}'} for m in ms],
        'format': {'version': VERSION, 'prefix': PREFIX,
                   'fields': {'mod': 'module name',
                              'owner': 'the address the module declares as its '
                                       'owner in config.json — absent when it '
                                       'declares none',
                              'key': 'the address this box signs its module '
                                     'cards with',
                              'cid': 'the module schema CID',
                              'ver': 'config.json version',
                              'orbit': 'core or orbit — which root holds it',
                              'app': 'gateway path for the app',
                              'api': 'gateway path for the API'},
                   'read_it': f'dig +short TXT {PREFIX}.<mod>.{host}',
                   'check_it': 'GET /attribution?name=<mod>&verify=1 — the '
                               'signed module card behind the record'},
    }
