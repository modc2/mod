"""
The settings that decide what the protocol's names mean.

All of it is deployment state, so none of it is in the committed config.json —
it lives in ~/.mod/dns/settings.json, which is the same place the caddy module
keeps the host it routes. `host` is the important one: modc2.com is a DEFAULT,
not a constant, and changing it here changes every derived name, every resolver
answer and (with router_sync) the HTTP routes as well.

Only the deployment owner writes this file. Anyone else who wants the protocol
on a different domain registers their own zone instead — that path does not go
through here.
"""
import json
import os
import threading
from pathlib import Path

STATE = Path(os.path.expanduser(os.environ.get('DNS_DIR', '~/.mod/dns')))
PATH = STATE / 'settings.json'
CADDY_SETTINGS = Path(os.path.expanduser(
    os.environ.get('MOD_CADDY_STATE', '~/.mod/caddy'))) / 'settings.json'

_lock = threading.Lock()

DEFAULTS = {
    'host': 'modc2.com',          # the protocol host — owner-settable
    'target': None,               # IPv4 the system zone answers with (None = detect)
    'target_v6': None,
    'ttl': 300,
    'dns_port': int(os.environ.get('DNS_PORT', 15353)),
    'bind': os.environ.get('DNS_BIND', '0.0.0.0'),
    'module_names': True,         # derive {mod}.{host} for every routed module
    'attribution': True,          # publish _mod TXT records: owner, CID, key
    'wildcard': True,             # derive *.{host}
    'nameservers': [],            # [] = derive ns1/ns2.{host}
    'soa_email': None,            # None = hostmaster@{host}
    'follow_caddy': True,         # adopt the router's host if it has one set
    'private_ips': True,          # mask addresses for everyone but the owner
}


def _read():
    try:
        return json.loads(PATH.read_text()) or {}
    except (OSError, ValueError):
        return {}


def all():
    """Effective settings: defaults, overlaid with what the owner has set."""
    s = dict(DEFAULTS)
    s.update({k: v for k, v in _read().items() if k in DEFAULTS})
    if s['follow_caddy'] and 'host' not in _read():
        s['host'] = caddy_host() or s['host']
    return s


def get(key, default=None):
    return all().get(key, default)


def host():
    return str(all()['host']).lower().rstrip('.')


def set(**kwargs):
    """Write settings. Unknown keys are refused rather than silently kept."""
    unknown = [k for k in kwargs if k not in DEFAULTS]
    if unknown:
        raise ValueError(f'unknown setting(s): {", ".join(sorted(unknown))}. '
                         f'Known: {", ".join(sorted(DEFAULTS))}')
    with _lock:
        current = _read()
        before = all()
        current.update(kwargs)
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(current, indent=2))
        os.replace(tmp, PATH)
    after = all()
    return {'before': {k: before[k] for k in kwargs},
            'after': {k: after[k] for k in kwargs},
            'settings': after, 'path': str(PATH)}


def caddy_host():
    """What the HTTP router thinks it serves. DNS and routing disagreeing is
    the most common way a fleet ends up half-reachable, so we read it."""
    try:
        return (json.loads(CADDY_SETTINGS.read_text()) or {}).get('host')
    except (OSError, ValueError):
        return None


def caddy_hosts():
    """Every domain the router answers on — primary plus add_host extras."""
    try:
        s = json.loads(CADDY_SETTINGS.read_text()) or {}
    except (OSError, ValueError):
        return []
    out = [s['host']] if s.get('host') else []
    out += sorted((s.get('hosts') or {}).keys())
    return out
