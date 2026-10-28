"""
The zones — what this server is authoritative for, and who owns each one.

A zone here has two layers, and keeping them apart is the whole design:

  derived   records the protocol computes from the fleet. The apex, the
            wildcard, `ns1`/`ns2`, and one name per routed module, all pointing
            at the zone's target box. Nobody edits these directly, because they
            are not user data — they are a function of which modules declared
            themselves routable. Add a module to the fleet and its name exists
            a moment later; take it out and the name goes.

  stored    records somebody wrote. A stored record for a (name, type) REPLACES
            the derived set for that pair, which is how the owner pins the apex
            to a CDN, or adds MX, TXT, CAA — anything the protocol would never
            guess. Nothing is lost: the derived record is still visible under
            `overridden` so you can see what you shadowed.

Ownership is per zone, not per server. The SYSTEM zone — the protocol host,
modc2.com unless the owner changed it — belongs to the deployment owner and
only they may write it. Every other zone belongs to the address that registered
it, and that address has the same authority inside it that the owner has inside
the system zone. That is the mechanism behind "anyone can point the protocol at
their own host": you bring a domain, you keep it.

Registration does not prove control of a domain — only the registrar's
delegation does. So a zone starts `verified: false`, and `verify()` looks for a
TXT challenge at `_mod-challenge.<zone>` through the public internet. Records
are still served either way (an undelegated zone is harmless: no resolver asks
us), but the state is never hidden.
"""
import json
import os
import re
import time
from pathlib import Path

import attrib
import fleet
import settings
import wire

STATE = Path(os.path.expanduser(os.environ.get('DNS_DIR', '~/.mod/dns')))
ZONES = STATE / 'zones'

LABEL = re.compile(r'^(?!-)[a-z0-9_-]{1,63}(?<!-)$')
EDITABLE_TYPES = ('A', 'AAAA', 'CNAME', 'TXT', 'MX', 'NS', 'SRV', 'CAA', 'SOA', 'PTR')

# Registering one of these would be a claim on everyone underneath it.
PUBLIC_SUFFIXES = {
    'co.uk', 'org.uk', 'ac.uk', 'com.au', 'net.au', 'co.nz', 'co.jp',
    'com.br', 'co.za', 'com.mx', 'github.io', 'pages.dev', 'workers.dev',
    'vercel.app', 'netlify.app', 'herokuapp.com', 'ngrok.io', 'onion',
}

CHALLENGE = '_mod-challenge'


class ZoneError(Exception):
    """The zone or record cannot be what you asked for."""


class Denied(Exception):
    """You are not the one who may change it."""


# ── names ────────────────────────────────────────────────────────────────

def valid_domain(name):
    n = wire.normalize(name)
    if not n or len(n) > 253:
        return False
    labels = n.split('.')
    if len(labels) < 2:
        return False
    if not all(LABEL.match(l) for l in labels):
        return False
    return not labels[-1].isdigit()


def fqdn(name, zone):
    """A record's owner name in full. '@' and '' mean the apex."""
    n = wire.normalize(name)
    if n in ('', '@'):
        return zone
    if n == zone or n.endswith('.' + zone):
        return n
    return f'{n}.{zone}'


def relative(name, zone):
    n = wire.normalize(name)
    if n == zone:
        return '@'
    if n.endswith('.' + zone):
        return n[:-(len(zone) + 1)]
    return n or '@'


# ── storage ──────────────────────────────────────────────────────────────

def _path(zone):
    return ZONES / f'{wire.normalize(zone)}.json'


def _read(zone):
    try:
        return json.loads(_path(zone).read_text())
    except (OSError, ValueError):
        return None


def _write(z):
    ZONES.mkdir(parents=True, exist_ok=True)
    z['serial'] = max(int(z.get('serial') or 0) + 1, int(time.time()))
    z['updated'] = int(time.time())
    p = _path(z['zone'])
    tmp = p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(z, indent=2))
    os.replace(tmp, p)
    return z


def system_zone():
    return settings.host()


def is_system(zone):
    z = wire.normalize(zone)
    return z == system_zone() or z in [wire.normalize(h) for h in settings.caddy_hosts()]


def stored_zones():
    if not ZONES.is_dir():
        return []
    out = []
    for p in sorted(ZONES.glob('*.json')):
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


def zone(name, create_system=False):
    """One zone's stored state. The system zone exists whether or not it has
    ever been written — it is defined by the settings, not by a file."""
    n = wire.normalize(name)
    z = _read(n)
    if z:
        z.setdefault('records', [])
        z['system'] = is_system(n)
        return z
    if is_system(n):
        z = {
            'zone': n, 'owner': None, 'system': True, 'verified': True,
            'created': int(time.time()), 'serial': int(time.time()),
            'target': None, 'target_v6': None, 'records': [],
            'derive': {'modules': settings.get('module_names', True),
                       'wildcard': settings.get('wildcard', True)},
            'note': 'the protocol host — derived from settings, owned by the '
                    'deployment owner',
        }
        if create_system:
            _write(z)
        return z
    return None


def zones():
    """Every zone served here, system first."""
    out, seen = [], set()
    sysz = zone(system_zone())
    out.append(sysz)
    seen.add(sysz['zone'])
    for z in stored_zones():
        if z['zone'] in seen:
            continue
        z['system'] = is_system(z['zone'])
        seen.add(z['zone'])
        out.append(z)
    for z in out:
        recs = records(z['zone'])
        z['record_count'] = len(recs['records'])
        z['derived_count'] = sum(1 for r in recs['records'] if r['source'] == 'derived')
    return out


def find_zone(name):
    """The deepest zone we are authoritative for that covers `name`."""
    n = wire.normalize(name)
    best = None
    for z in zones():
        zn = z['zone']
        if n == zn or n.endswith('.' + zn):
            if best is None or len(zn) > len(best['zone']):
                best = z
    return best


# ── derived records ──────────────────────────────────────────────────────

def target_of(z):
    """The address a zone answers with, and where that came from."""
    if z.get('target'):
        return z['target'], 'zone.target'
    if z.get('system'):
        ip = fleet.public_ip()
        return ip['ip'], ip['source']
    return None, 'unset'


def derived(z):
    """The records the protocol computes for this zone. No file holds these."""
    zn = z['zone']
    ttl = int(settings.get('ttl', 300))
    ip, source = target_of(z)
    v6 = z.get('target_v6') or (settings.get('target_v6') if z.get('system') else None)
    derive = z.get('derive') or {}
    out = []

    def add(name, rtype, value, why, ttl_=None):
        out.append({'name': name, 'type': rtype, 'value': value,
                    'ttl': ttl_ or ttl, 'source': 'derived', 'why': why,
                    'system': True, 'fqdn': fqdn(name, zn)})

    ns = [wire.normalize(n) for n in (settings.get('nameservers') or [])] or \
         [f'ns1.{zn}', f'ns2.{zn}']
    email = settings.get('soa_email') or f'hostmaster.{zn}'
    add('@', 'SOA',
        f'{ns[0]} {email} {int(z.get("serial") or 1)} 7200 3600 1209600 {ttl}',
        'the zone apex — serial rises on every change here', 3600)
    for n in ns:
        add('@', 'NS', n, 'the nameservers for this zone', 86400)

    if ip:
        add('@', 'A', ip, f'the box this zone points at ({source})')
        for n in ns:
            if n.endswith('.' + zn):
                add(relative(n, zn), 'A', ip, 'the nameserver itself', 86400)
        if derive.get('wildcard', True):
            add('*', 'A', ip,
                'every other name under this host resolves to the same box, so '
                'a new module needs no DNS change')
        add('www', 'A', ip, 'the conventional alias for the apex')
        if derive.get('modules', True):
            for mmod in fleet.modules():
                credit = (f'attributed to {mmod["owner"]}' if mmod.get('owner')
                          else 'no declared owner')
                add(mmod['name'], 'A', ip,
                    f'{mmod["name"]} — routed module, also at /{mmod["name"]} '
                    f'on this host; {credit}')
    if v6:
        add('@', 'AAAA', v6, f'IPv6 for this host')
        if derive.get('wildcard', True):
            add('*', 'AAAA', v6, 'IPv6 for every name under this host')

    # Who each of those names belongs to, published beside the address it
    # resolves to — see attrib.py. Derived like everything else here: change a
    # module's config.json owner and the record follows on the next answer.
    if settings.get('attribution', True):
        for r in attrib.records(zn, derive.get('modules', True)):
            add(r['name'], 'TXT', r['value'], r['why'])

    add(CHALLENGE, 'TXT', z.get('challenge') or _challenge_for(zn),
        'the token this zone is verified with — publish it at your registrar '
        'and call verify', 60)
    return out


def _challenge_for(zn):
    import hashlib
    seed = (STATE / 'challenge.seed')
    try:
        secret = seed.read_text().strip()
    except OSError:
        secret = os.urandom(16).hex()
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(secret)
        try:
            os.chmod(seed, 0o600)
        except OSError:
            pass
    return 'mod-dns=' + hashlib.sha256((secret + '|' + zn).encode()).hexdigest()[:32]


# ── the merged view ──────────────────────────────────────────────────────

def records(zone_name, name=None, rtype=None):
    """Stored records, plus every derived record they do not shadow."""
    z = zone(zone_name)
    if not z:
        raise ZoneError(f'no zone {wire.normalize(zone_name)} here')
    zn = z['zone']
    stored = []
    for r in z.get('records', []):
        r = dict(r)
        r['source'] = 'stored'
        r['fqdn'] = fqdn(r['name'], zn)
        stored.append(r)
    shadow = {(r['name'], r['type']) for r in stored}
    # A CNAME is exclusive: RFC 1034 forbids any other data at that name, so a
    # stored alias hides the whole derived set for it rather than just the
    # matching type. Without this a pinned `www CNAME` would be served next to
    # the derived `www A` and resolvers would disagree about which is real.
    aliased = {r['name'] for r in stored if r['type'] == 'CNAME'}
    overridden, merged = [], list(stored)
    for r in derived(z):
        if (r['name'], r['type']) in shadow or r['name'] in aliased:
            overridden.append(r)
        else:
            merged.append(r)

    def keep(r):
        if name is not None and wire.normalize(fqdn(name, zn)) != r['fqdn']:
            return False
        if rtype is not None and r['type'] != str(rtype).upper():
            return False
        return True

    merged = [r for r in merged if keep(r)]
    merged.sort(key=lambda r: (r['name'] != '@', r['name'], r['type']))
    return {
        'zone': zn, 'owner': z.get('owner'), 'system': z.get('system', False),
        'verified': z.get('verified', False), 'serial': z.get('serial'),
        'target': target_of(z)[0], 'target_source': target_of(z)[1],
        'records': merged, 'overridden': overridden,
        'counts': {'stored': sum(1 for r in merged if r['source'] == 'stored'),
                   'derived': sum(1 for r in merged if r['source'] == 'derived')},
    }


def all_records():
    """Every record in every zone — what the listener answers from."""
    out = {}
    for z in zones():
        try:
            out[z['zone']] = records(z['zone'])['records']
        except ZoneError:
            continue
    return out


# ── authorization ────────────────────────────────────────────────────────

def owner_of(zone_name):
    z = zone(zone_name)
    if not z:
        return None
    if z.get('system'):
        import identity
        return identity.owner()
    return z.get('owner')


def can_write(zone_name, address, deployment_owner=None):
    """Who may change this zone: its registrant, or the deployment owner."""
    if address is None:
        return False
    if deployment_owner and address.lower() == str(deployment_owner).lower():
        return True
    o = owner_of(zone_name)
    return bool(o and address.lower() == str(o).lower())


def require_write(zone_name, address, deployment_owner=None):
    if can_write(zone_name, address, deployment_owner):
        return True
    z = zone(zone_name)
    if z and z.get('system'):
        raise Denied(
            f'{z["zone"]} is the system zone — the protocol host itself. Only '
            f'the deployment owner changes it. To run the protocol on a host '
            f'you control, register your own zone: you own every record in it.')
    raise Denied(f'{wire.normalize(zone_name)} belongs to '
                 f'{owner_of(zone_name) or "nobody here"}')


# ── mutations ────────────────────────────────────────────────────────────

def register(zone_name, address, target=None, target_v6=None,
             modules=True, wildcard=True, note=None, deployment_owner=None):
    """Claim a domain and become the owner of its zone here."""
    zn = wire.normalize(zone_name)
    if not valid_domain(zn):
        raise ZoneError(f'{zn!r} is not a domain name this server can hold '
                        f'(need at least two labels of a-z0-9-)')
    if zn in PUBLIC_SUFFIXES:
        raise ZoneError(f'{zn} is a public suffix — registering it would claim '
                        f'every name underneath it')
    existing = _read(zn)
    if existing:
        if not can_write(zn, address, deployment_owner):
            raise Denied(f'{zn} is already registered to {existing.get("owner")}')
    else:
        for other in zones():
            on = other['zone']
            if on == zn:
                continue
            if (zn.endswith('.' + on) or on.endswith('.' + zn)) and \
                    not can_write(on, address, deployment_owner):
                raise Denied(
                    f'{zn} overlaps {on}, which belongs to '
                    f'{owner_of(on) or "the deployment owner"} — a zone inside '
                    f'somebody else\'s zone would silently take their names')
        if is_system(zn) and not (deployment_owner and
                                  str(address).lower() == str(deployment_owner).lower()):
            raise Denied(f'{zn} is the protocol host — only the deployment '
                         f'owner holds it')
    z = existing or {
        'zone': zn, 'created': int(time.time()), 'serial': int(time.time()),
        'records': [], 'verified': False,
    }
    # Re-registering a zone updates it; it does not transfer it. The deployment
    # owner may administer somebody else's zone, but taking it out from under
    # them would have to be deliberate, so it stays theirs.
    z['owner'] = z.get('owner') or str(address).lower()
    z['system'] = is_system(zn)
    if target is not None:
        z['target'] = target or None
    if target_v6 is not None:
        z['target_v6'] = target_v6 or None
    z['derive'] = {'modules': bool(modules), 'wildcard': bool(wildcard)}
    if note is not None:
        z['note'] = note
    z['challenge'] = z.get('challenge') or _challenge_for(zn)
    _write(z)
    return z


def set_target(zone_name, address, target=None, target_v6=None,
               deployment_owner=None):
    require_write(zone_name, address, deployment_owner)
    z = zone(zone_name)
    before = {'target': z.get('target'), 'target_v6': z.get('target_v6')}
    if target is not None:
        z['target'] = target or None
    if target_v6 is not None:
        z['target_v6'] = target_v6 or None
    _write(z)
    return {'zone': z['zone'], 'before': before,
            'after': {'target': z.get('target'), 'target_v6': z.get('target_v6')},
            'affects': 'the apex, the wildcard and every derived module name'}


def set_record(zone_name, name, rtype, value, ttl=None, address=None,
               deployment_owner=None, replace=True):
    """Write one record. Validated by encoding it the way the wire needs it."""
    require_write(zone_name, address, deployment_owner)
    z = zone(zone_name)
    zn = z['zone']
    t = str(rtype).upper()
    if t not in EDITABLE_TYPES:
        raise ZoneError(f'{t} is not a type this server writes. '
                        f'Known: {", ".join(EDITABLE_TYPES)}')
    rel = relative(name, zn)
    if rel not in ('@', '*') and not all(
            LABEL.match(l) or l == '*' for l in rel.split('.')):
        raise ZoneError(f'{name!r} is not a valid name under {zn}')
    try:
        wire.encode_rdata(t, value)
    except wire.WireError as e:
        raise ZoneError(f'{t} value rejected: {e}')
    ttl = int(ttl if ttl is not None else settings.get('ttl', 300))
    if not 0 <= ttl <= 604800:
        raise ZoneError('ttl must be between 0 and 604800 seconds')
    recs = [dict(r) for r in z.get('records', [])]
    at_name = [r for r in recs if r['name'] == rel]
    if t == 'CNAME':
        if rel == '@':
            raise ZoneError('a CNAME cannot live at the zone apex — the apex '
                            'must carry SOA and NS. Use an A or AAAA record.')
        clash = [r['type'] for r in at_name if r['type'] != 'CNAME']
        if clash:
            raise ZoneError(f'{rel}.{zn} already has {", ".join(sorted(set(clash)))} '
                            f'here; a CNAME must be the only record at a name')
    elif any(r['type'] == 'CNAME' for r in at_name):
        raise ZoneError(f'{rel}.{zn} is a CNAME — no other record type can '
                        f'exist beside it. Delete the CNAME first.')
    before = [r for r in recs if r['name'] == rel and r['type'] == t]
    if replace:
        recs = [r for r in recs if not (r['name'] == rel and r['type'] == t)]
    elif any(r['name'] == rel and r['type'] == t and r['value'] == value
             for r in recs):
        return {'zone': zn, 'unchanged': True,
                'record': {'name': rel, 'type': t, 'value': value, 'ttl': ttl}}
    rec = {'name': rel, 'type': t, 'value': value, 'ttl': ttl,
           'by': str(address).lower() if address else None, 'at': int(time.time())}
    recs.append(rec)
    z['records'] = recs
    _write(z)
    shadowed = [r for r in derived(z) if r['name'] == rel and r['type'] == t]
    return {'zone': zn, 'record': rec, 'before': before,
            'shadowed_derived': shadowed,
            'serial': z['serial']}


def delete_record(zone_name, name, rtype, value=None, address=None,
                  deployment_owner=None):
    require_write(zone_name, address, deployment_owner)
    z = zone(zone_name)
    zn = z['zone']
    t, rel = str(rtype).upper(), relative(name, zn)
    recs = z.get('records', [])
    gone = [r for r in recs
            if r['name'] == rel and r['type'] == t
            and (value is None or r['value'] == value)]
    if not gone:
        derived_hit = [r for r in derived(z) if r['name'] == rel and r['type'] == t]
        if derived_hit:
            raise ZoneError(
                f'{rel} {t} in {zn} is a DERIVED record — it is computed from '
                f'the fleet, not stored, so there is nothing to delete. Write a '
                f'record over it to shadow it, or turn the derivation off in '
                f'settings.')
        raise ZoneError(f'no stored {t} record for {rel} in {zn}')
    z['records'] = [r for r in recs if r not in gone]
    _write(z)
    return {'zone': zn, 'deleted': gone, 'serial': z['serial']}


def delete_zone(zone_name, address, deployment_owner=None):
    zn = wire.normalize(zone_name)
    require_write(zn, address, deployment_owner)
    if is_system(zn):
        raise Denied(f'{zn} is the protocol host — repoint it with host_set '
                     f'instead of deleting it')
    p = _path(zn)
    if not p.exists():
        raise ZoneError(f'no zone {zn} here')
    z = _read(zn)
    p.unlink()
    return {'zone': zn, 'deleted': True, 'records': len(z.get('records', []))}


def verify(zone_name, resolver='1.1.1.1'):
    """Look for the challenge TXT in the real DNS. Proof of delegation, not of
    ownership — but it is the same proof every CA uses."""
    z = zone(zone_name)
    if not z:
        raise ZoneError(f'no zone {wire.normalize(zone_name)} here')
    zn = z['zone']
    want = z.get('challenge') or _challenge_for(zn)
    name = f'{CHALLENGE}.{zn}'
    try:
        answer = wire.query(name, 'TXT', server=resolver)
    except Exception as e:                                   # noqa: BLE001
        return {'zone': zn, 'verified': z.get('verified', False),
                'error': str(e), 'expected': want, 'name': name}
    found = [v for v in answer.get('values', [])]
    ok = want in found
    if ok and not z.get('verified'):
        z['verified'] = True
        _write(z)
    return {'zone': zn, 'verified': ok or bool(z.get('verified')),
            'proved_now': ok, 'name': name, 'expected': want, 'found': found,
            'rcode': answer.get('rcode'),
            'how': f'publish a TXT record at {name} with the value above, then '
                   f'run this again'}
