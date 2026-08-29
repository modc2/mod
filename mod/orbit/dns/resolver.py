"""
The protocol resolver — a mod name, in; the addresses that serve it, out.

This is the piece the rest of the fleet actually wants. "Where is eth?" has,
in the mod protocol, four different right answers — an app URL, an API URL, an
MCP endpoint and an A record — and they are only consistent if one thing
computes all four from the same source. That thing is here.

`resolve()` accepts whatever the caller happens to be holding: a module name
(`eth`), a hostname (`eth.modc2.com`), a gateway path (`modc2.com/api/eth`) or
a whole URL. It answers with the module, the host it is served on, every
address for it, whether the upstream ports are actually listening, and the DNS
records that make the hostname resolve — each labelled with where it came from.

`check()` is the honesty function. This module holds records; the internet has
its own opinion, and when they differ nothing works while everything looks
fine. So `check()` asks a public resolver the same question, diffs the two, and
names the specific shape of the disagreement — delegated elsewhere, proxied by
a CDN, pointing at another box, or simply not published.

`plan()` answers the question a non-owner has: I want the protocol on MY
domain, what exactly do I do? It prints the records to publish, the router
command, and the one thing only the box's owner can do.
"""
import urllib.parse

import attrib
import fleet
import identity
import server
import settings
import wire
import zone as Z


def _split(query):
    """Whatever the caller pasted → (host, module, kind)."""
    q = (query or '').strip().strip('.')
    if not q:
        return None, None, 'empty'
    if '://' in q:
        parsed = urllib.parse.urlsplit(q)
        host, path = parsed.hostname or '', parsed.path
    elif '/' in q:
        host, _, path = q.partition('/')
        path = '/' + path
    else:
        host, path = q, ''
    parts = [p for p in path.split('/') if p]
    if parts and parts[0] == 'api':
        parts = parts[1:]
    module = parts[0].lower() if parts else None
    if not module and host and '.' not in host:
        return None, host.lower(), 'module'          # bare name: "eth"
    if not module and host:
        # eth.modc2.com — the leftmost label may be a module name
        labels = host.lower().split('.')
        zone_hit = Z.find_zone(host)
        if zone_hit and host.lower() != zone_hit['zone'] and len(labels) > 1:
            candidate = host.lower()[:-(len(zone_hit['zone']) + 1)]
            if fleet.module(candidate):
                return zone_hit['zone'], candidate, 'subdomain'
        return host.lower(), None, 'host'
    return (host.lower() or None), module, 'url'


def resolve(query, qtype='A'):
    """The one answer to "where is this?"."""
    host, module, kind = _split(query)
    host = host or settings.host()
    out = {'query': query, 'kind': kind, 'host': host, 'module': module}

    if module:
        entry = fleet.module(module)
        out['found'] = bool(entry)
        out['urls'] = fleet.urls(module, host)
        if entry:
            out['upstream'] = {
                'api_port': entry['api_port'], 'app_port': entry['app_port'],
                'api_live': entry.get('api_live'), 'app_live': entry.get('app_live'),
                'dir': entry['dir'],
            }
            out['description'] = entry['description']
            out['routed'] = True
            # The address is only half of "where is this?" — the other half is
            # whose it is, which the zone publishes beside it.
            out['attribution'] = {
                'owner': entry.get('owner'),
                'cid': entry.get('schema'),
                'version': entry.get('version'),
                'key': fleet.box_key(),
                'record': f'{attrib.PREFIX}.{module}.{host}',
                'txt': attrib.txt({
                    'mod': module, 'owner': entry.get('owner'),
                    'key': fleet.box_key(), 'cid': entry.get('schema'),
                    'version': entry.get('version'), 'orbit': entry.get('root'),
                    'app': f'/{module}', 'api': f'/api/{module}'}),
                'means': (f'{module} declares {entry["owner"]} as its owner'
                          if entry.get('owner') else
                          f'{module} declares no owner — the only claim on '
                          f'record is that this box serves this CID'),
            }
        else:
            out['routed'] = False
            out['why'] = (
                f'no module named {module!r} declares itself routable. A module '
                f'is routed when its config.json sets "route": true and a port; '
                f'until then {host}/{module} has nowhere to go, even though '
                f'{module}.{host} still resolves via the wildcard.')
        name = f'{module}.{host}'
    else:
        name = host

    dns = server.answer(name, qtype)
    out['name'] = name
    out['dns'] = {k: v for k, v in dns.items() if not k.startswith('_')}
    z = Z.find_zone(name)
    out['zone'] = z['zone'] if z else None
    out['authoritative_here'] = bool(z)
    out['addresses'] = [a['value'] for a in dns['answers'] if a['type'] in ('A', 'AAAA')]
    if not out.get('urls'):
        out['urls'] = {'app': f'https://{host}/', 'pattern': f'https://{host}/{{mod}}'}
    return out


def check(name=None, rtype='A', resolver_ip='1.1.1.1'):
    """What we say vs what the internet says, and why they differ."""
    name = wire.normalize(name or settings.host())
    mine = server.answer(name, rtype)
    mine_values = sorted({a['value'] for a in mine['answers']
                          if a['type'] == str(rtype).upper()})
    out = {'name': name, 'type': str(rtype).upper(),
           'here': {'rcode': mine['rcode'], 'values': mine_values,
                    'zone': mine.get('zone'), 'matched': mine.get('matched')},
           'resolver': resolver_ip}
    try:
        public = wire.query(name, rtype, server=resolver_ip)
    except Exception as e:                                   # noqa: BLE001
        out['public'] = {'error': str(e)}
        out['verdict'] = 'unknown'
        out['detail'] = 'could not reach a public resolver from this box'
        return out
    public_values = sorted(set(public.get('values') or []))
    out['public'] = {'rcode': public['rcode'], 'values': public_values}

    z = Z.find_zone(name)
    ns = []
    try:
        ns_answer = wire.query(z['zone'] if z else name, 'NS', server=resolver_ip)
        ns = sorted(set(ns_answer.get('values') or []))
    except Exception:                                        # noqa: BLE001
        pass
    out['delegation'] = {'nameservers': ns,
                         'ours': sorted({r['value'] for r in
                                         Z.records(z['zone'])['records']
                                         if r['type'] == 'NS'}) if z else []}
    delegated = bool(set(out['delegation']['ours']) & set(ns))
    out['delegation']['to_us'] = delegated

    if not mine_values and public_values:
        out['verdict'] = 'not held here'
        out['detail'] = (f'the internet resolves {name}, but this server holds '
                         f'no {out["type"]} for it — it is served by '
                         f'{", ".join(ns) or "somebody else"}')
    elif mine_values and not public_values:
        out['verdict'] = 'not published'
        out['detail'] = (f'this server would answer {", ".join(mine_values)}, '
                         f'but the public internet returns '
                         f'{public["rcode"]} — nothing has delegated {name} '
                         f'here yet.')
    elif set(mine_values) == set(public_values):
        out['verdict'] = 'match'
        out['detail'] = 'the record served here is the record the world sees'
    else:
        cf = any(v.startswith(('104.', '172.6', '188.114', '162.159'))
                 for v in public_values)
        out['verdict'] = 'proxied' if cf else 'mismatch'
        out['detail'] = (
            f'the world gets {", ".join(public_values)}; this server holds '
            f'{", ".join(mine_values) or "nothing"}'
            + (' — those look like CDN/proxy addresses, so the name is in front '
               'of a proxy rather than pointed straight at this box.' if cf else
               '. One of the two is stale.'))
    return out


def plan(host, target=None):
    """How to put the mod protocol on a host of your own.

    Written for the person who is NOT the owner of this box: everything here
    except the last section is theirs to do.
    """
    host = wire.normalize(host)
    ip = target or fleet.public_ip()['ip']
    z = Z.zone(host)
    ns = [f'ns1.{host}', f'ns2.{host}']
    modules = [m['name'] for m in fleet.modules()]
    return {
        'host': host,
        'target': ip,
        'registered_here': bool(z),
        'owner': z.get('owner') if z else None,
        'steps': [
            {'step': 1, 'do': 'register the zone',
             'how': f'POST /zones {{"zone": "{host}", "target": "{ip}"}} with '
                    f'your mod-protocol token — or `m dns/register '
                    f'{host} target={ip}`',
             'who': 'any signed caller — you do not need the owner',
             'gets': 'you become the owner of this zone: its records, its '
                     'target, its deletion'},
            {'step': 2, 'do': 'point the name at the box',
             'how': f'at your registrar, either publish A records '
                    f'({host} → {ip} and *.{host} → {ip}) or delegate the whole '
                    f'zone here with NS records {" and ".join(ns)} (and glue A '
                    f'records for those names → {ip})',
             'who': 'you, at your registrar — no server can do this for you',
             'gets': 'every name under your host resolves, including the '
                     f'{len(modules)} module names derived for you'},
            {'step': 3, 'do': 'prove it',
             'how': f'publish TXT {Z.CHALLENGE}.{host} = '
                    f'{(z or {}).get("challenge") or Z._challenge_for(host)} '
                    f'then call verify',
             'who': 'you',
             'gets': 'the zone shows as verified rather than merely claimed'},
            {'step': 4, 'do': 'route the HTTP side',
             'how': f'`m caddy/add_host {host}` on the box that runs the '
                    f'modules, so {host}/{{mod}} reaches them over TLS',
             'who': 'the OWNER of that box — this is the one step you cannot '
                    'do yourself, because it edits the live router',
             'gets': f'{host}/eth and {host}/api/eth serve the same modules'},
        ],
        'then': {
            'app': f'https://{host}/{{mod}}',
            'api': f'https://{host}/api/{{mod}}',
            'subdomain': f'https://{{mod}}.{host}',
            'modules': modules[:12],
            'module_count': len(modules),
        },
        'note': 'nothing in steps 1-3 touches the system zone, the protocol '
                'host, or anybody else\'s names. That is the point: a new host '
                'is a new zone with a new owner, not a change to this one.',
    }


def overview():
    """The protocol's naming, in one object — what the console opens on."""
    host = settings.host()
    ms = fleet.modules()
    live = [m for m in ms if m.get('live')]
    return {
        'host': host,
        'host_source': ('settings.json' if settings.get('host') != settings.DEFAULTS['host']
                        or settings.caddy_host() != host else 'caddy/settings.json'),
        'router_hosts': settings.caddy_hosts(),
        'target': Z.target_of(Z.zone(host))[0],
        'owner': identity.owner(),
        'zones': [{'zone': z['zone'], 'system': z.get('system'),
                   'owner': z.get('owner'), 'verified': z.get('verified'),
                   'records': z.get('record_count')} for z in Z.zones()],
        'modules': {'routed': len(ms), 'live': len(live),
                    'names': [m['name'] for m in ms],
                    'with_owner': sum(1 for m in ms if m.get('owner')),
                    'with_cid': sum(1 for m in ms if m.get('schema'))},
        'attribution': attrib.deployment(host),
        'naming': {
            'app': f'https://{host}/{{mod}}',
            'api': f'https://{host}/api/{{mod}}',
            'mcp': f'https://{host}/api/{{mod}}/mcp',
            'subdomain': f'https://{{mod}}.{host}',
            'rule': 'the path form is what the router serves; the subdomain '
                    'form is what this zone resolves. Both point at one box.',
        },
        'listener': server.state(),
    }
