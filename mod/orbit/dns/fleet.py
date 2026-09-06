"""
The fleet, as a name space.

The mod protocol's naming rule is short: `{host}/{mod}` is a module's app and
`{host}/api/{mod}` is its API. That rule only works because two separate things
agree — the router has a route for the module, and the host resolves to the box
the router runs on. This file is the half that knows the modules; `zone.py` is
the half that turns them into records.

A module is discovered from its own config.json under the orbit and core roots.
Two ports matter: `port` (the API) and `app_port` (the app). `route: true` is
the module's declared intent to be reachable from outside, and it is what makes
this module derive a name for it — the copy-pasted placeholder ports that many
dormant modules carry are exactly why intent, not a port number, is the
trigger.

The same read also carries the fields the protocol attributes a module by —
`owner`, `schema` (its IPFS CID), `version`, `anchor`, `protocol` — because a
name and the claim about who stands behind it come out of the same file, and
`attrib.py` turns them into the TXT records published beside the address.

Liveness is checked by connecting, not by trusting the config, because a config
that declares a port and a process that listens on it are different facts.
"""
import json
import os
import socket
import time
from pathlib import Path

import settings

ROOTS = [Path(p) for p in os.environ.get(
    'DNS_ROOTS', '/root/mod/mod/orbit:/root/mod/mod/core').split(':') if p]

_cache = {'at': 0, 'modules': None}
_ip_cache = {'at': 0, 'ip': None}
_key_cache = {'at': 0, 'key': None}
CACHE_TTL = 20


def port_live(port, host='127.0.0.1', timeout=0.2):
    if not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def modules(refresh=False, check_live=True):
    """Every module that has declared itself routable, with its ports."""
    now = time.time()
    if not refresh and _cache['modules'] is not None and now - _cache['at'] < CACHE_TTL:
        return _cache['modules']
    out = []
    for root in ROOTS:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            cfg_path = d / 'config.json'
            if not cfg_path.is_file():
                continue
            try:
                cfg = json.loads(cfg_path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(cfg, dict):
                continue
            name = str(cfg.get('name') or d.name).lower()
            api = cfg.get('port') or (cfg.get('ports') or {}).get('api')
            app = cfg.get('app_port') or (cfg.get('ports') or {}).get('app')
            routed = bool(cfg.get('route'))
            if not routed or not (api or app):
                continue
            entry = {
                'name': name,
                'dir': str(d),
                'root': root.name,
                'api_port': api,
                'app_port': app,
                'base_path': cfg.get('base_path') or f'/{name}',
                'description': (cfg.get('description') or '')[:160],
                # what the protocol attributes this module by
                'owner': (str(cfg.get('owner')).lower()
                          if cfg.get('owner') else None),
                'schema': cfg.get('schema') or cfg.get('cid') or None,
                'version': cfg.get('version') or None,
                'anchor': cfg.get('anchor') or None,
                'protocol': cfg.get('protocol') or None,
                'icon': cfg.get('icon') or None,
            }
            if check_live:
                entry['api_live'] = port_live(api)
                entry['app_live'] = port_live(app)
                entry['live'] = entry['api_live'] or entry['app_live']
            out.append(entry)
    # A name is path-derived and core wins over orbit on a collision.
    seen, deduped = set(), []
    for e in sorted(out, key=lambda e: (e['root'] != 'core', e['name'])):
        if e['name'] in seen:
            e['shadowed_by'] = 'core'
            continue
        seen.add(e['name'])
        deduped.append(e)
    deduped.sort(key=lambda e: e['name'])
    _cache.update(at=now, modules=deduped)
    return deduped


def module(name):
    name = (name or '').strip().lower()
    for e in modules():
        if e['name'] == name:
            return e
    return None


def urls(name, host=None, scheme='https'):
    """The protocol's addresses for one module on one host."""
    host = host or settings.host()
    return {
        'app': f'{scheme}://{host}/{name}',
        'api': f'{scheme}://{host}/api/{name}',
        'mcp': f'{scheme}://{host}/api/{name}/mcp',
        'subdomain': f'{scheme}://{name}.{host}',
    }


def public_ip(refresh=False, timeout=3.0):
    """This box's address as the internet sees it.

    Tried in order: an explicit setting, a public echo service, then the local
    address of a UDP socket 'connected' to a public address — which needs no
    network traffic and is right whenever the box is not behind NAT.
    """
    configured = settings.get('target')
    if configured:
        return {'ip': configured, 'source': 'settings.target'}
    now = time.time()
    if not refresh and _ip_cache['ip'] and now - _ip_cache['at'] < 300:
        return {'ip': _ip_cache['ip'], 'source': 'cached'}
    import urllib.request
    for url in ('https://api.ipify.org', 'https://ifconfig.me/ip'):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode().strip()
            if ip.count('.') == 3:
                _ip_cache.update(at=now, ip=ip)
                return {'ip': ip, 'source': url}
        except Exception:                                    # noqa: BLE001
            continue
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('1.1.1.1', 53))
            ip = s.getsockname()[0]
        _ip_cache.update(at=now, ip=ip)
        return {'ip': ip, 'source': 'local route'}
    except OSError:
        return {'ip': None, 'source': 'unknown',
                'note': 'no address detected — set one with settings target='}


def box_key():
    """The address this deployment signs its module cards with.

    `m.info(mod)` returns the protocol's module card with `key` set to this
    address and a signature over the card, so it is the identity behind every
    attribution served from this box.

    Finding it means importing the protocol package, which costs the better
    part of a second — far more than a DNS answer may take, and this is on the
    path of every derived TXT record. So a key that was found is cached for the
    life of the process (a box does not change its identity underneath itself)
    and only a failed lookup is retried, on a timer. `server.start()` primes it
    so the first query never pays.
    """
    now = time.time()
    if _key_cache['key']:
        return _key_cache['key']
    if _key_cache['at'] and now - _key_cache['at'] < 60:
        return None                     # looked and failed recently
    try:
        from protocol import protocol
        key = str(protocol().owner() or '') or None
    except Exception:                                        # noqa: BLE001
        key = None
    _key_cache.update(at=now, key=key)
    return key


def config(name):
    """One module's whole config.json, routed or not."""
    n = (name or '').strip().lower()
    hit = module(n)
    dirs = [Path(hit['dir'])] if hit else [r / n for r in ROOTS]
    for d in dirs:
        p = d / 'config.json'
        if p.is_file():
            try:
                cfg = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(cfg, dict):
                return cfg, str(d)
    return None, None
