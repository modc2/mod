#!/usr/bin/env python3
"""dns mcp — the protocol's naming, as tools an agent can call.

Twenty-eight tools over one rule: reads are open, writes are attributed. The
catalog in `ops.py` says which standing each one needs, `dns_operations`
returns it, and `dns_whoami` says which of them the caller can actually run —
so an agent can discover its own authority instead of guessing and getting a
403.

Start at `dns_resolve`. In this protocol "where is eth?" has four right answers
(app URL, API URL, MCP endpoint, A record) and that tool returns all of them
from one source. `dns_check` is the one to reach for when something is
mysteriously unreachable: it diffs what this server holds against what the
public internet actually returns. And when the question is vaguer than a
name — "why is my domain not working", "how do I use my own domain" —
`dns_ask` takes it in plain words, resolves whatever name is in it, and answers
against what this box is doing right now.

Self-contained JSON-RPC 2.0 on the stdlib, no `mcp` package:

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 5380  # Streamable HTTP — POST /mcp

api.py mounts `handle()` at /mcp, so the tools, the REST routes and the console
run the same code and cannot drift.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

import actions                                              # noqa: E402
import ops                                                  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'
VERSION = '1.1.0'

INSTRUCTIONS = (
    'The DNS layer of the mod protocol. In this protocol a module is reachable '
    'at {host}/{mod} (app), {host}/api/{mod} (API) and {mod}.{host} (name), and '
    'this module is what makes all three agree: it derives a zone from the '
    'module fleet, serves it authoritatively over UDP and TCP, and resolves any '
    'of those forms back to addresses. Start with dns_resolve. Use dns_check '
    'when something is unreachable — it diffs the record held here against what '
    'a public resolver returns, which is where "it works locally" usually '
    'dies. Reads need no token. Writes take a mod-protocol token: any signed '
    'caller can register their OWN host with dns_zone_register and then owns '
    'every record in it, while the system zone (the protocol host itself), the '
    'listener and the router sync belong to the deployment owner alone. '
    'dns_operations lists every operation with the standing it needs; '
    'dns_whoami says which ones you can run.'
)


def _tok(a, token):
    return a.pop('token', None) or token


# ── tools ──

def _t_resolve(a, token):
    return actions.resolve(a['query'], a.get('type', 'A'))


def _t_lookup(a, token):
    return actions.lookup(a['name'], a.get('type', 'A'))


def _t_ask(a, token):
    return actions.ask(a.get('question') or a.get('q') or '', _tok(a, token))


def _t_guide(a, token):
    return actions.guide(_tok(a, token))


def _t_explain(a, token):
    return actions.explain(a.get('word') or '', _tok(a, token))


def _t_check(a, token):
    return actions.check(a.get('name'), a.get('type', 'A'),
                         a.get('resolver', '1.1.1.1'))


def _t_plan(a, token):
    return actions.plan(a['host'], a.get('target'))


def _t_overview(a, token):
    return actions.overview(_tok(a, token))


def _t_zones(a, token):
    return actions.zones()


def _t_records(a, token):
    return actions.records(a.get('zone'), a.get('name'), a.get('type'))


def _t_modules(a, token):
    return actions.modules()


def _t_operations(a, token):
    return actions.operations(a.get('who'))


def _t_ops(a, token):
    return actions.ops_log(a.get('limit', 50), a.get('op'), a.get('zone'),
                           a.get('actor'))


def _t_queries(a, token):
    return actions.queries(a.get('limit', 50), a.get('name'), a.get('rcode'))


def _t_attribution(a, token):
    return actions.attribution(a.get('name') or a.get('mod'),
                               bool(a.get('verify')))


def _t_stats(a, token):
    return actions.stats()


def _t_whoami(a, token):
    return actions.whoami(_tok(a, token))


def _t_zone_register(a, token):
    return actions.zone_register(_tok(a, token), a['zone'], a.get('target'),
                                 a.get('target_v6'), a.get('modules', True),
                                 a.get('wildcard', True), a.get('note'))


def _t_record_set(a, token):
    return actions.record_set(_tok(a, token), a.get('zone'), a['name'],
                              a.get('type', 'A'), a['value'], a.get('ttl'),
                              a.get('replace', True))


def _t_record_delete(a, token):
    return actions.record_delete(_tok(a, token), a.get('zone'), a['name'],
                                 a.get('type', 'A'), a.get('value'))


def _t_zone_target(a, token):
    return actions.zone_target(_tok(a, token), a.get('zone'), a.get('target'),
                               a.get('target_v6'))


def _t_zone_verify(a, token):
    return actions.zone_verify(_tok(a, token), a.get('zone'),
                               a.get('resolver', '1.1.1.1'))


def _t_zone_delete(a, token):
    return actions.zone_delete(_tok(a, token), a['zone'])


def _t_host_set(a, token):
    return actions.host_set(_tok(a, token), a['host'], a.get('sync_router', False))


def _t_settings(a, token):
    tok = _tok(a, token)
    return actions.settings_set(tok, **a)


def _t_serve(a, token):
    return actions.serve_listener(_tok(a, token), a.get('port'), a.get('bind'))


def _t_kill(a, token):
    return actions.kill_listener(_tok(a, token))


def _t_router_sync(a, token):
    return actions.router_sync(_tok(a, token), a.get('apply', True))


def _s(props, required=()):
    return {'type': 'object', 'properties': props, 'required': list(required)}


STR = {'type': 'string'}
INT = {'type': 'integer'}
BOOL = {'type': 'boolean'}
TOKEN = dict(STR, description='mod-protocol token; omitted when the transport '
                              'already carries an Authorization header')

TOOLS = [
    {'name': 'dns_ask', 'fn': _t_ask,
     'description': 'Ask about DNS or about this deployment in plain words and '
                    'get a plain answer, grounded in what the box is actually '
                    'doing right now — it resolves any name in the question '
                    'before answering. Written for somebody who has never set '
                    'up DNS: "how do I use my own domain", "why is my domain '
                    'not working", "how long until my change takes effect", '
                    '"do I need a wallet". No model behind it, so it says when '
                    'it does not know rather than inventing an answer.',
     'inputSchema': _s({'question': dict(STR, description='plain words; there '
                                                          'is no syntax'),
                        'token': TOKEN}, ['question'])},
    {'name': 'dns_guide', 'fn': _t_guide,
     'description': 'The beginner surface in one call: an ordered checklist for '
                    'putting the protocol on a domain of your own, already '
                    'marked done or not by looking at this box (listener up, '
                    'signed in, zone registered, zone delegated), plus the full '
                    'glossary and the questions worth asking from here.',
     'inputSchema': _s({'token': TOKEN})},
    {'name': 'dns_explain', 'fn': _t_explain,
     'description': 'One piece of DNS vocabulary — zone, apex, TTL, CNAME, '
                    'glue, NXDOMAIN, delegation, attribution — in a one-line '
                    'meaning, a paragraph that assumes nothing, and a line '
                    'saying what that word points at in this deployment.',
     'inputSchema': _s({'word': STR}, ['word'])},
    {'name': 'dns_resolve', 'fn': _t_resolve,
     'description': 'Where is this? Takes a module name (eth), a hostname '
                    '(eth.modc2.com), a gateway path (modc2.com/api/eth) or a '
                    'URL, and returns the app/API/MCP addresses, whether the '
                    'upstream ports are live, and the DNS answer for the name.',
     'inputSchema': _s({'query': dict(STR, description='module, hostname, path or URL'),
                        'type': dict(STR, description='record type, default A')},
                       ['query'])},
    {'name': 'dns_lookup', 'fn': _t_lookup,
     'description': 'Ask this name server a question the way a resolver would '
                    'and see the answer it would put on the wire, including '
                    'NXDOMAIN, NODATA, wildcard and CNAME chasing.',
     'inputSchema': _s({'name': STR, 'type': STR}, ['name'])},
    {'name': 'dns_check', 'fn': _t_check,
     'description': 'Diff the record held here against what a public resolver '
                    'returns for the same name, and say which way they differ: '
                    'match, proxied, mismatch, not published, not held here. '
                    'The first thing to run when a host is unreachable.',
     'inputSchema': _s({'name': STR, 'type': STR,
                        'resolver': dict(STR, description='public resolver IP, default 1.1.1.1')})},
    {'name': 'dns_plan', 'fn': _t_plan,
     'description': 'How to run the mod protocol on a host of your own: the '
                    'records to publish, the challenge to prove it, and the one '
                    'step only the box owner can do. No permission needed for '
                    'the rest.',
     'inputSchema': _s({'host': STR, 'target': dict(STR, description='IP the host should point at')},
                       ['host'])},
    {'name': 'dns_overview', 'fn': _t_overview,
     'description': 'The protocol naming picture in one call: host, target, '
                    'zones, module count, listener state, your own standing and '
                    'the last few changes.',
     'inputSchema': _s({'token': TOKEN})},
    {'name': 'dns_zones', 'fn': _t_zones,
     'description': 'Every zone served here, who owns each, which is the system '
                    'zone, and how many records each holds.',
     'inputSchema': _s({})},
    {'name': 'dns_records', 'fn': _t_records,
     'description': 'The merged record set of a zone — stored records plus the '
                    'records the protocol derives from the module fleet, each '
                    'labelled with where it came from and why.',
     'inputSchema': _s({'zone': STR, 'name': STR, 'type': STR})},
    {'name': 'dns_modules', 'fn': _t_modules,
     'description': 'The routed module fleet as a name space: every module that '
                    'declared itself routable, its ports, and its four addresses.',
     'inputSchema': _s({})},
    {'name': 'dns_attribution', 'fn': _t_attribution,
     'description': 'Who a module is attributed to in the mod protocol: the '
                    'owner address from its config.json, its schema CID, the '
                    'version, and the key this box signs its module card with '
                    '— plus the _mod.<mod>.<host> TXT record that publishes '
                    'them in DNS. With no name it answers for the whole '
                    'deployment and says how much of the fleet declares an '
                    'owner at all. verify=true also fetches the protocol\'s '
                    'signed module card and checks the signature, which is the '
                    'part a TXT record cannot prove by itself.',
     'inputSchema': _s({'name': dict(STR, description='module name — omit for the whole fleet'),
                        'verify': {'type': 'boolean',
                                   'description': "also check the signature on the module's card"}})},
    {'name': 'dns_operations', 'fn': _t_operations,
     'description': 'The catalog: every operation this module can perform and '
                    'the standing it requires (anyone / any signed caller / the '
                    "zone's owner / the deployment owner).",
     'inputSchema': _s({'who': dict(STR, description='filter: anon, holder, zone_owner, owner')})},
    {'name': 'dns_ops', 'fn': _t_ops,
     'description': 'The change log — every mutation ever made here, newest '
                    'first, with the address that made it and the before/after.',
     'inputSchema': _s({'limit': INT, 'op': STR, 'zone': STR, 'actor': STR})},
    {'name': 'dns_queries', 'fn': _t_queries,
     'description': 'Recent DNS questions this server was asked, with the rcode '
                    'and how each was matched (exact, wildcard, cname, nodata).',
     'inputSchema': _s({'limit': INT, 'name': STR, 'rcode': STR})},
    {'name': 'dns_stats', 'fn': _t_stats,
     'description': 'Zones, records, modules, listener state, query counters '
                    'and settings.',
     'inputSchema': _s({})},
    {'name': 'dns_whoami', 'fn': _t_whoami,
     'description': 'Who this token makes you, which zones you may change, and '
                    'exactly which operations you can run.',
     'inputSchema': _s({'token': TOKEN})},

    {'name': 'dns_zone_register', 'fn': _t_zone_register,
     'description': 'Claim a domain you control and become the owner of its '
                    'zone here — the way to run the protocol on a host other '
                    'than the system one. Any signed caller may do this and it '
                    'needs no permission from the deployment owner. Your zone '
                    'gets the same derived module names, pointed at your target.',
     'inputSchema': _s({'zone': STR, 'target': dict(STR, description='IPv4 this host points at'),
                        'target_v6': STR, 'modules': BOOL, 'wildcard': BOOL,
                        'note': STR, 'token': TOKEN}, ['zone'])},
    {'name': 'dns_record_set', 'fn': _t_record_set,
     'description': 'Add or update one record in a zone you own. Values are '
                    'validated by encoding them to wire format, so an '
                    'unservable record is refused now rather than at query '
                    'time. A stored record shadows the derived one for the same '
                    'name and type.',
     'inputSchema': _s({'zone': STR, 'name': dict(STR, description='label, or @ for the apex'),
                        'type': dict(STR, description='A, AAAA, CNAME, TXT, MX, NS, SRV, CAA, SOA, PTR'),
                        'value': STR, 'ttl': INT, 'replace': BOOL, 'token': TOKEN},
                       ['name', 'value'])},
    {'name': 'dns_record_delete', 'fn': _t_record_delete,
     'description': 'Delete a stored record from a zone you own. Derived '
                    'records cannot be deleted — shadow them instead.',
     'inputSchema': _s({'zone': STR, 'name': STR, 'type': STR, 'value': STR,
                        'token': TOKEN}, ['name'])},
    {'name': 'dns_zone_target', 'fn': _t_zone_target,
     'description': 'Point a zone you own at a different box. Rewrites the '
                    'apex, the wildcard and every derived module name at once.',
     'inputSchema': _s({'zone': STR, 'target': STR, 'target_v6': STR,
                        'token': TOKEN})},
    {'name': 'dns_zone_verify', 'fn': _t_zone_verify,
     'description': 'Prove a zone is really yours by looking for the challenge '
                    'TXT record in the public DNS.',
     'inputSchema': _s({'zone': STR, 'resolver': STR, 'token': TOKEN})},
    {'name': 'dns_zone_delete', 'fn': _t_zone_delete,
     'description': 'Drop a zone you own. The system zone cannot be deleted.',
     'inputSchema': _s({'zone': STR, 'token': TOKEN}, ['zone'])},

    {'name': 'dns_host_set', 'fn': _t_host_set,
     'description': 'OWNER ONLY. Repoint the protocol host — the host every '
                    'derived name, resolver answer and router route follows. '
                    'Anyone else who wants a different host registers their own '
                    'zone instead.',
     'inputSchema': _s({'host': STR, 'sync_router': BOOL, 'token': TOKEN}, ['host'])},
    {'name': 'dns_settings', 'fn': _t_settings,
     'description': 'OWNER ONLY. Read (no arguments) or change the system '
                    'settings: target, ttl, dns_port, bind, nameservers, '
                    'soa_email, and whether module names and the wildcard are '
                    'derived at all.',
     'inputSchema': _s({'target': STR, 'target_v6': STR, 'ttl': INT,
                        'dns_port': INT, 'bind': STR, 'module_names': BOOL,
                        'wildcard': BOOL, 'soa_email': STR,
                        'nameservers': {'type': 'array', 'items': STR},
                        'follow_caddy': BOOL, 'token': TOKEN})},
    {'name': 'dns_serve', 'fn': _t_serve,
     'description': 'OWNER ONLY. Bind the authoritative listener on UDP and TCP.',
     'inputSchema': _s({'port': INT, 'bind': STR, 'token': TOKEN})},
    {'name': 'dns_kill', 'fn': _t_kill,
     'description': 'OWNER ONLY. Release the listener.',
     'inputSchema': _s({'token': TOKEN})},
    {'name': 'dns_router_sync', 'fn': _t_router_sync,
     'description': 'OWNER ONLY. Hand the current host to the caddy module so '
                    'HTTP routing and DNS agree on what the protocol answers on.',
     'inputSchema': _s({'apply': BOOL, 'token': TOKEN})},
]

BY_NAME = {t['name']: t for t in TOOLS}


def version():
    return VERSION


def tool_list():
    return [{k: v for k, v in t.items() if k != 'fn'} for t in TOOLS]


class BadArguments(Exception):
    """The tool exists; the call does not satisfy its schema."""


def call(name, args, token=None):
    tool = BY_NAME.get(name)
    if not tool:
        raise KeyError(f'unknown tool: {name}')
    args = dict(args or {})
    # Say which argument is missing. Without this the handler's KeyError
    # surfaced as the bare key name ("'query'"), which reads like a server
    # fault rather than a call the caller can fix.
    missing = [k for k in tool['inputSchema'].get('required', []) if args.get(k) in (None, '')]
    if missing:
        need = ', '.join(missing)
        raise BadArguments(
            f'{name} needs {need} — '
            + '; '.join(f"{k}: {v.get('description', v.get('type', ''))}"
                        for k, v in tool['inputSchema']['properties'].items()
                        if k in missing))
    return tool['fn'](args, token)


# ── JSON-RPC ──

def _result(rid, payload):
    return {'jsonrpc': '2.0', 'id': rid, 'result': payload}


def _error(rid, code, message):
    return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}}


def handle(msg, token=None):
    """One JSON-RPC message in, one response (or None for a notification)."""
    if not isinstance(msg, dict):
        return _error(None, -32600, 'invalid request')
    method, rid, params = msg.get('method'), msg.get('id'), msg.get('params') or {}

    if method == 'initialize':
        asked = (params.get('protocolVersion') or DEFAULT_PROTOCOL_VERSION)
        version_out = asked if asked in SUPPORTED_PROTOCOL_VERSIONS \
            else DEFAULT_PROTOCOL_VERSION
        return _result(rid, {
            'protocolVersion': version_out,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'dns', 'version': VERSION},
            'instructions': INSTRUCTIONS,
        })
    if method in ('notifications/initialized', 'notifications/cancelled'):
        return None
    if method == 'ping':
        return _result(rid, {})
    if method == 'tools/list':
        return _result(rid, {'tools': tool_list()})
    if method == 'tools/call':
        name = params.get('name')
        args = params.get('arguments') or {}
        try:
            out = call(name, args, token)
            payload = json.dumps(out, indent=2, default=str)
            return _result(rid, {'content': [{'type': 'text', 'text': payload}],
                                 'isError': False})
        except KeyError as e:                       # unknown tool name
            return _error(rid, -32601, str(e).strip("'"))
        except BadArguments as e:
            return _error(rid, -32602, str(e))
        except actions.Refused as e:
            return _result(rid, {'content': [{'type': 'text', 'text': json.dumps(
                {'refused': e.message, 'status': e.status,
                 'hint': 'dns_whoami says what you can run; dns_operations says '
                         'what each operation needs'}, indent=2)}],
                'isError': True})
        except Exception as e:                               # noqa: BLE001
            return _result(rid, {'content': [{'type': 'text', 'text': json.dumps(
                {'error': f'{type(e).__name__}: {e}'}, indent=2)}],
                'isError': True})
    if method in ('resources/list', 'prompts/list'):
        return _result(rid, {method.split('/')[0]: []})
    return _error(rid, -32601, f'method not found: {method}')


def _stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            print(json.dumps(_error(None, -32700, 'parse error')), flush=True)
            continue
        out = handle(msg, os.environ.get('DNS_TOKEN'))
        if out is not None:
            print(json.dumps(out, default=str), flush=True)


if __name__ == '__main__':
    if '--http' in sys.argv:
        import api
        port = int(sys.argv[sys.argv.index('--port') + 1]
                   if '--port' in sys.argv else os.environ.get('PORT', 5380))
        api.serve(port)
    else:
        _stdio()
