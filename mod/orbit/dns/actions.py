"""
The operations themselves — one implementation, three faces.

The REST API, the MCP tools and the CLI all call the functions in this file, so
a change made by an agent, a shell and a browser goes through exactly the same
permission check and lands in exactly the same log. Nothing in api.py or mcp.py
mutates state on its own; they only decide how to spell an argument.

Each mutating function does the same four things in the same order: identify
the caller, check the standing the operation requires (`ops.CATALOG` is the
declaration, `zone.require_write` is the enforcement), do the work, and record
it with before/after. A denial is an answer too — it comes back naming who may
do the thing and what the caller can do instead, because "403" is a useless
thing to tell somebody who just wanted their own domain to work.
"""
import attrib
import guide as G
import identity
import fleet
import ops
import resolver
import server
import settings
import wire
import zone as Z


class Refused(Exception):
    """Carries a status code and a message meant for a human."""

    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status
        self.message = message


def _caller(token, required=None, op=None):
    """(address, role) for this request, raising Refused with instructions."""
    try:
        if required == 'owner':
            address = identity.require_owner(
                token, f'run {op}' if op else 'change the system configuration')
        elif required in ('holder', 'zone_owner'):
            address = identity.require(token)
        else:
            address = identity.whoami(token)
    except identity.AuthError as e:
        raise Refused(str(e), 401)
    except identity.Denied as e:
        raise Refused(str(e), 403)
    return address, identity.role(address)


# ── reads ────────────────────────────────────────────────────────────────

def whoami(token=None):
    address = identity.whoami(token)
    role = identity.role(address)
    owned = [z['zone'] for z in Z.zones()
             if address and Z.can_write(z['zone'], address, identity.owner())]
    return {
        'address': address, 'role': role, 'owner': identity.owner(),
        'is_owner': identity.is_owner(address),
        'zones_you_can_change': owned,
        'can': [o['id'] for o in ops.OPERATIONS
                if o['who'] == 'anon'
                or (o['who'] in ('holder', 'zone_owner') and address)
                or (o['who'] == 'owner' and identity.is_owner(address))],
        'auth': identity.status(),
        'note': ('this deployment is unclaimed — the first signed caller to run '
                 'an owner operation becomes its owner'
                 if not identity.owner() else
                 'the owner holds the system zone and the protocol host; you '
                 'hold whatever zones you registered'),
    }


def overview(token=None):
    out = resolver.overview()
    out['you'] = whoami(token)
    out['recent_changes'] = ops.log(limit=5)['ops']
    return out


def resolve(query, type='A'):
    return resolver.resolve(query, type)


def lookup(name, type='A'):
    out = server.answer(name, type)
    return {k: v for k, v in out.items() if not k.startswith('_')}


def check(name=None, type='A', resolver_ip='1.1.1.1'):
    return resolver.check(name, type, resolver_ip)


def plan(host, target=None):
    return resolver.plan(host, target)


def zones():
    return {'zones': Z.zones(), 'system': Z.system_zone(),
            'owner': identity.owner(),
            'rule': 'the system zone belongs to the deployment owner; every '
                    'other zone belongs to whoever registered it'}


def records(zone=None, name=None, type=None):
    return Z.records(zone or Z.system_zone(), name, type)


def attribution(name=None, verify=False):
    """Who a name is attributed to, as the protocol states it."""
    return attrib.report(name, verify=verify)


def modules():
    ms = fleet.modules()
    host = settings.host()
    return {'host': host, 'count': len(ms),
            'modules': [dict(m, urls=fleet.urls(m['name'], host),
                             attribution=f'{attrib.PREFIX}.{m["name"]}.{host}')
                        for m in ms],
            'attributed': {'with_owner': sum(1 for m in ms if m.get('owner')),
                           'with_cid': sum(1 for m in ms if m.get('schema'))},
            'note': 'every one of these has a derived name in the system zone '
                    'because its config.json declares "route": true, and a '
                    '_mod TXT record beside it saying whose it is'}


def guide(token=None):
    """The whole beginner surface in one object: checklist, glossary, prompts."""
    return G.guide(token)


def ask(question, token=None):
    """A question in plain words, answered against this deployment."""
    return G.ask(question, token)


def explain(word, token=None):
    """One piece of vocabulary, in full, as it applies here."""
    return G.term(word, token)


def glossary(token=None):
    """Every word this guide defines, one line each."""
    return G.glossary(token)


def operations(who=None):
    return ops.catalog(who)


def ops_log(limit=100, op=None, zone=None, actor=None):
    out = ops.log(limit, op, zone, actor)
    out['summary'] = ops.summary()
    return out


def queries(limit=100, name=None, rcode=None):
    return server.recent(limit, name, rcode)


def stats():
    zs = Z.zones()
    return {
        'host': settings.host(),
        'zones': len(zs),
        'records': sum(z.get('record_count', 0) for z in zs),
        'modules': len(fleet.modules()),
        'listener': server.state(),
        'changes': ops.summary(),
        'settings': settings.all(),
        'identity': identity.status(),
    }


# ── writes: anyone signed, on their own host ─────────────────────────────

def zone_register(token=None, zone=None, target=None, target_v6=None,
                  modules=True, wildcard=True, note=None):
    address, role = _caller(token, 'holder', 'zone_register')
    try:
        z = Z.register(zone, address, target=target, target_v6=target_v6,
                       modules=modules, wildcard=wildcard, note=note,
                       deployment_owner=identity.owner())
    except Z.Denied as e:
        raise Refused(str(e), 403)
    except Z.ZoneError as e:
        raise Refused(str(e), 400)
    ops.record('zone_register', address, role, zone=z['zone'],
               after={'target': z.get('target'), 'derive': z.get('derive')})
    return {'zone': z, 'you_own_it': True,
            'records': Z.records(z['zone'])['records'],
            'next': resolver.plan(z['zone'], z.get('target'))['steps'][1:]}


def record_set(token=None, zone=None, name=None, type='A', value=None,
               ttl=None, replace=True):
    zone = zone or Z.system_zone()
    system = Z.is_system(zone)
    address, role = _caller(token, 'owner' if system else 'zone_owner',
                            'system_record' if system else 'record_set')
    try:
        out = Z.set_record(zone, name, type, value, ttl, address,
                           deployment_owner=identity.owner(), replace=replace)
    except Z.Denied as e:
        raise Refused(str(e), 403)
    except Z.ZoneError as e:
        raise Refused(str(e), 400)
    ops.record('system_record' if system else 'record_set', address, role,
               zone=out['zone'], target=f'{out["record"]["name"]} {type.upper()}',
               before=out.get('before'), after=out['record'],
               detail='shadows a derived record' if out.get('shadowed_derived') else None)
    return out


def record_delete(token=None, zone=None, name=None, type='A', value=None):
    zone = zone or Z.system_zone()
    system = Z.is_system(zone)
    address, role = _caller(token, 'owner' if system else 'zone_owner',
                            'system_record' if system else 'record_delete')
    try:
        out = Z.delete_record(zone, name, type, value, address,
                              deployment_owner=identity.owner())
    except Z.Denied as e:
        raise Refused(str(e), 403)
    except Z.ZoneError as e:
        raise Refused(str(e), 400)
    ops.record('record_delete', address, role, zone=out['zone'],
               target=f'{name} {str(type).upper()}', before=out['deleted'])
    return out


def zone_target(token=None, zone=None, target=None, target_v6=None):
    zone = zone or Z.system_zone()
    system = Z.is_system(zone)
    address, role = _caller(token, 'owner' if system else 'zone_owner',
                            'settings_set' if system else 'zone_target')
    try:
        out = Z.set_target(zone, address, target, target_v6,
                           deployment_owner=identity.owner())
    except Z.Denied as e:
        raise Refused(str(e), 403)
    except Z.ZoneError as e:
        raise Refused(str(e), 400)
    ops.record('zone_target', address, role, zone=out['zone'],
               before=out['before'], after=out['after'])
    return out


def zone_verify(token=None, zone=None, resolver_ip='1.1.1.1'):
    out = Z.verify(zone or Z.system_zone(), resolver_ip)
    if out.get('proved_now'):
        address = identity.whoami(token)
        ops.record('zone_verify', address, identity.role(address),
                   zone=out['zone'], after={'verified': True})
    return out


def zone_delete(token=None, zone=None):
    address, role = _caller(token, 'zone_owner', 'zone_delete')
    try:
        out = Z.delete_zone(zone, address, deployment_owner=identity.owner())
    except Z.Denied as e:
        raise Refused(str(e), 403)
    except Z.ZoneError as e:
        raise Refused(str(e), 400)
    ops.record('zone_delete', address, role, zone=out['zone'], before=out)
    return out


# ── writes: the owner only, on the system itself ─────────────────────────

def host_set(token=None, host=None, sync_router=False):
    """Repoint the protocol host. The single most consequential change here."""
    address, role = _caller(token, 'owner', 'host_set')
    new = wire.normalize(host)
    if not Z.valid_domain(new):
        raise Refused(f'{host!r} is not a hostname the protocol can use', 400)
    before = settings.host()
    result = settings.set(host=new)
    ops.record('host_set', address, role, zone=new,
               before={'host': before}, after={'host': new},
               detail='every derived name, resolver answer and router route '
                      'follows this')
    out = {'host': new, 'was': before, 'settings': result['settings'],
           'zone': Z.records(new)['counts'],
           'note': f'the system zone is now {new}. DNS alone does not move the '
                   f'HTTP routes — run router_sync (or `m caddy/host {new}`) '
                   f'so the router agrees.'}
    if sync_router:
        out['router'] = router_sync(token)
    return out


def settings_set(token=None, **kwargs):
    address, role = _caller(token, 'owner', 'settings_set')
    kwargs.pop('token', None)
    if not kwargs:
        return {'settings': settings.all(), 'known': sorted(settings.DEFAULTS),
                'path': str(settings.PATH)}
    if 'host' in kwargs:
        return host_set(token, kwargs['host'])
    try:
        result = settings.set(**kwargs)
    except ValueError as e:
        raise Refused(str(e), 400)
    ops.record('settings_set', address, role, before=result['before'],
               after=result['after'])
    return result


def serve_listener(token=None, port=None, bind=None):
    address, role = _caller(token, 'owner', 'serve')
    out = server.start(port, bind)
    ops.record('serve', address, role, after={'port': out['port'],
                                              'bind': out['bind']},
               ok=out.get('running', False), detail=out.get('error'))
    return out


def kill_listener(token=None):
    address, role = _caller(token, 'owner', 'kill')
    out = server.stop()
    ops.record('kill', address, role)
    return out


def router_sync(token=None, apply=True):
    """Hand the host to the caddy module so HTTP and DNS agree."""
    address, role = _caller(token, 'owner', 'router_sync')
    host = settings.host()
    try:
        from protocol import protocol
        caddy = protocol().mod('caddy')()
        out = caddy.host(host, apply=apply) if apply else caddy.settings(host=host)
    except Exception as e:                                   # noqa: BLE001
        raise Refused(f'could not reach the caddy module: {e}. The router is a '
                      f'separate module by design — DNS names a box, the router '
                      f'decides what answers on it.', 502)
    ops.record('router_sync', address, role, zone=host, after={'host': host},
               detail='caddy host + reload')
    return {'host': host, 'caddy': out,
            'now': {'app': f'https://{host}/{{mod}}',
                    'api': f'https://{host}/api/{{mod}}'}}


def ops_prune(token=None, keep=200):
    address, role = _caller(token, 'owner', 'ops_prune')
    return ops.prune(keep, actor=address)
