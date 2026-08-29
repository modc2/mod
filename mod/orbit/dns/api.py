#!/usr/bin/env python3
"""dns api — REST, MCP and the console on one port, zero dependencies.

Every route is a thin call into `actions.py`, the same functions the MCP tools
and the CLI use, so a browser, a shell and an agent cannot get different
answers to the same question or different verdicts on the same permission.

The listener is started in-process by default: one pm2 entry gives you the HTTP
API, the console, the MCP endpoint and the authoritative name server on UDP and
TCP, which is what makes "the module owns the protocol's DNS" true rather than
aspirational.

    python3 api.py [--port 5380] [--no-listener]

Behind the gateway the path prefix is stripped (`/api/dns/...` → `/...`), and
the console is served at `/dns` on this same port, so `/dns/_api/...` is
accepted as a same-origin alias for the API.
"""
import json
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import actions          # noqa: E402
import identity         # noqa: E402
import mcp              # noqa: E402
import server as dnsd   # noqa: E402
import settings         # noqa: E402

BASE = os.environ.get('BASE_PATH', '/dns')
PORT = int(os.environ.get('PORT', 5380))


def info():
    s = settings.all()
    return {
        'name': 'dns',
        'version': mcp.version(),
        'what': "the DNS layer of the mod protocol — the zone is derived from "
                "the module fleet, served authoritatively on UDP and TCP, and "
                "resolvable back to every address a module answers on",
        'host': settings.host(),
        'owner': identity.owner(),
        'naming': {
            'app': f'https://{settings.host()}/{{mod}}',
            'api': f'https://{settings.host()}/api/{{mod}}',
            'mcp': f'https://{settings.host()}/api/{{mod}}/mcp',
            'name': f'{{mod}}.{settings.host()}',
        },
        'listener': {'port': s['dns_port'], 'bind': s['bind'],
                     'running': dnsd.state()['running'],
                     'transports': 'udp + tcp'},
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'attribution': {
            'record': '_mod.{mod}.' + settings.host(),
            'deployment': '_mod.' + settings.host(),
            'says': 'owner (declared in config.json), cid (module schema), '
                    'key (the address this box signs its module cards with)',
            'read': 'GET /attribution?name={mod}',
        },
        'auth': {'header': 'Authorization: Bearer <mod-protocol token>',
                 'reads': 'open to everyone',
                 'writes': 'attributed to a signed address',
                 'system': 'the protocol host, the system zone, the listener '
                           'and the router sync belong to the deployment owner',
                 'your_own_host': 'any signed caller can register their own '
                                  'zone and owns every record in it — see '
                                  'GET /plan?host=yourdomain.com'},
        'endpoints': {
            'GET /health': 'liveness',
            'GET /overview': 'the whole naming picture in one object',
            'GET /resolve': 'query= — module, hostname, path or URL → addresses',
            'GET /lookup': 'name=, type= — what this server would answer',
            'GET /check': 'name=, type= — held here vs what the internet returns',
            'GET /plan': 'host=, target= — how to run the protocol on your host',
            'GET /zones': 'every zone and who owns it',
            'POST /zones': '{zone, target, modules, wildcard} — register your own',
            'GET /zones/{zone}/records': 'merged records, stored + derived',
            'PUT /zones/{zone}/records': '{name, type, value, ttl}',
            'DELETE /zones/{zone}/records': 'name=, type=, value=',
            'POST /zones/{zone}/target': '{target, target_v6}',
            'POST /zones/{zone}/verify': 'prove the zone by TXT challenge',
            'DELETE /zones/{zone}': 'drop a zone you own',
            'GET /records': 'zone=, name=, type=',
            'GET /modules': 'the routed fleet as a name space',
            'GET /attribution': 'name=, verify= — who a module is attributed '
                                'to: owner, CID, and the signed module card',
            'GET /operations': 'the catalog — every operation and who may run it',
            'GET /ops': 'the change log — limit=, op=, zone=, actor=',
            'GET /queries': 'recent resolutions — limit=, name=, rcode=',
            'GET /stats': 'zones, records, listener, counters, settings',
            'GET /whoami': 'your standing and what you can run',
            'GET /guide': 'the beginner surface: checklist, glossary, prompts',
            'GET|POST /guide/ask': 'question= — plain words in, a grounded '
                                   'answer out; no model, and it says when it '
                                   'does not know',
            'GET /guide/term': 'word= — one piece of vocabulary, in full',
            'GET /guide/glossary': 'every word the guide defines',
            'POST /host': '{host} — OWNER: repoint the protocol host',
            'GET|POST /settings': 'OWNER: system settings',
            'POST /serve | POST /kill': 'OWNER: the listener',
            'POST /router_sync': 'OWNER: hand the host to caddy',
            'POST /ops/prune': 'OWNER: roll the change log',
            'GET /tools': 'the MCP tool registry',
            f'GET {BASE}/fonts/<face>.woff2': "the console's pixel faces",
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def _token(headers):
    return headers.get('authorization') or headers.get('x-mod-token')


class Handler(BaseHTTPRequestHandler):
    server_version = 'mod-dns/1.0'
    protocol_version = 'HTTP/1.1'

    # ── plumbing ──
    def log_message(self, fmt, *args):
        if os.environ.get('DNS_HTTP_LOG'):
            sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def _send(self, code, payload, ctype='application/json'):
        body = payload if isinstance(payload, bytes) else \
            json.dumps(payload, indent=2, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'content-type, authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return {}

    def _path(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        # Behind the gateway the prefix is stripped; served directly it is not.
        if path == BASE or path.startswith(BASE + '/'):
            rest = path[len(BASE):]
            if rest in ('', '/'):
                return '/__console__', {}
            for alias in ('/_api', '/api'):
                if rest == alias or rest.startswith(alias + '/'):
                    rest = rest[len(alias):] or '/'
                    break
            path = rest or '/'
        args = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        return path.rstrip('/') or '/', args

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def do_PUT(self):
        self._route('PUT')

    def do_DELETE(self):
        self._route('DELETE')

    # ── routing ──
    def _route(self, method):
        path, args = self._path()
        body = self._body() if method in ('POST', 'PUT', 'DELETE') else {}
        # The request body can only be read off the socket once. /mcp needs the
        # whole JSON-RPC envelope rather than the flattened arguments, so it is
        # kept here instead of being re-read — reading it twice blocks forever
        # waiting for bytes the client already sent.
        self._parsed_body = body
        args = {**args, **{k: v for k, v in body.items() if v is not None}}
        token = args.pop('token', None) or _token(self.headers)
        try:
            code, payload = self.dispatch(method, path, args, token)
        except actions.Refused as e:
            code, payload = e.status, {
                'error': e.message,
                'operations': '/operations lists what each change requires',
                'whoami': '/whoami says what you can run'}
        except (KeyError, TypeError) as e:
            code, payload = 400, {'error': f'missing or bad argument: {e}'}
        except Exception as e:                               # noqa: BLE001
            code, payload = 500, {'error': f'{type(e).__name__}: {e}'}
        if isinstance(payload, tuple):                 # (bytes, content-type)
            self._send(code, payload[0], payload[1])
        else:
            self._send(code, payload)

    def dispatch(self, method, path, a, token):
        parts = [p for p in path.split('/') if p]

        if path == '/__console__' or path == '/console':
            return 200, (self._console(), 'text/html; charset=utf-8')
        if len(parts) == 2 and parts[0] == 'fonts':
            return self._font(parts[1])
        if path == '/':
            return 200, info()
        if path == '/health':
            st = dnsd.state()
            return 200, {'ok': True, 'host': settings.host(),
                         'zones': len(st['zones']), 'listener': st['running'],
                         'tools': len(mcp.TOOLS)}
        if path == '/mcp' and method == 'POST':
            msg = self._mcp_body()
            out = mcp.handle(msg, token)
            return (202, b'') if out is None else (200, out)
        if path == '/tools':
            return 200, {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS)}

        # reads
        if path == '/overview':
            return 200, actions.overview(token)
        if path == '/resolve':
            return 200, actions.resolve(a.get('query') or a.get('name') or '',
                                        a.get('type', 'A'))
        if path == '/lookup':
            return 200, actions.lookup(a['name'], a.get('type', 'A'))
        if path == '/check':
            return 200, actions.check(a.get('name'), a.get('type', 'A'),
                                      a.get('resolver', '1.1.1.1'))
        if path == '/plan':
            return 200, actions.plan(a.get('host') or settings.host(), a.get('target'))
        if path == '/attribution':
            return 200, actions.attribution(
                a.get('name') or a.get('mod'),
                str(a.get('verify', '')).lower() in ('1', 'true', 'yes'))
        if path == '/modules':
            return 200, actions.modules()
        if path == '/operations':
            return 200, actions.operations(a.get('who'))
        if path == '/ops' and method == 'GET':
            return 200, actions.ops_log(int(a.get('limit', 100)), a.get('op'),
                                        a.get('zone'), a.get('actor'))
        if path == '/ops/prune' and method == 'POST':
            return 200, actions.ops_prune(token, int(a.get('keep', 200)))
        if path == '/queries':
            return 200, actions.queries(int(a.get('limit', 100)), a.get('name'),
                                        a.get('rcode'))
        if path == '/stats':
            return 200, actions.stats()
        if path == '/whoami':
            return 200, actions.whoami(token)

        # the guide — the part written for somebody who has never done this
        if path == '/guide':
            return 200, actions.guide(token)
        if path == '/guide/checklist':
            return 200, actions.guide(token)['checklist']
        if path == '/guide/glossary':
            return 200, actions.glossary(token)
        if path == '/guide/term':
            return 200, actions.explain(a.get('word') or a.get('term') or '', token)
        if path == '/guide/ask':
            return 200, actions.ask(a.get('question') or a.get('q') or '', token)
        if path == '/records' and method == 'GET':
            return 200, actions.records(a.get('zone'), a.get('name'), a.get('type'))

        # zones
        if path == '/zones' and method == 'GET':
            return 200, actions.zones()
        if path == '/zones' and method == 'POST':
            return 200, actions.zone_register(
                token, a['zone'], a.get('target'), a.get('target_v6'),
                a.get('modules', True), a.get('wildcard', True), a.get('note'))
        if parts and parts[0] == 'zones' and len(parts) >= 2:
            zone = urllib.parse.unquote(parts[1])
            tail = parts[2] if len(parts) > 2 else None
            if tail == 'records':
                if method == 'GET':
                    return 200, actions.records(zone, a.get('name'), a.get('type'))
                if method in ('PUT', 'POST'):
                    return 200, actions.record_set(
                        token, zone, a['name'], a.get('type', 'A'), a['value'],
                        a.get('ttl'), a.get('replace', True))
                if method == 'DELETE':
                    return 200, actions.record_delete(
                        token, zone, a['name'], a.get('type', 'A'), a.get('value'))
            if tail == 'target' and method == 'POST':
                return 200, actions.zone_target(token, zone, a.get('target'),
                                                a.get('target_v6'))
            if tail == 'verify' and method == 'POST':
                return 200, actions.zone_verify(token, zone,
                                                a.get('resolver', '1.1.1.1'))
            if tail is None:
                if method == 'GET':
                    return 200, actions.records(zone)
                if method == 'DELETE':
                    return 200, actions.zone_delete(token, zone)

        # records, flat form
        if path == '/records' and method in ('PUT', 'POST'):
            return 200, actions.record_set(token, a.get('zone'), a['name'],
                                           a.get('type', 'A'), a['value'],
                                           a.get('ttl'), a.get('replace', True))
        if path == '/records' and method == 'DELETE':
            return 200, actions.record_delete(token, a.get('zone'), a['name'],
                                              a.get('type', 'A'), a.get('value'))

        # owner-only system changes
        if path == '/host' and method == 'POST':
            return 200, actions.host_set(token, a['host'],
                                         a.get('sync_router', False))
        if path == '/settings':
            if method == 'GET':
                return 200, {'settings': settings.all(),
                             'known': sorted(settings.DEFAULTS),
                             'path': str(settings.PATH),
                             'who': 'only the deployment owner may change these'}
            return 200, actions.settings_set(token, **a)
        if path == '/serve' and method == 'POST':
            return 200, actions.serve_listener(token, a.get('port'), a.get('bind'))
        if path == '/kill' and method == 'POST':
            return 200, actions.kill_listener(token)
        if path == '/router_sync' and method == 'POST':
            return 200, actions.router_sync(token, a.get('apply', True))

        return 404, {'error': f'no route {method} {path}',
                     'endpoints': sorted(info()['endpoints'])}

    def _mcp_body(self):
        """The JSON-RPC envelope, as `_route` already parsed it."""
        return getattr(self, '_parsed_body', None) or {}

    def _font(self, name):
        """The console's two pixel faces, served from this module.

        Self-hosted rather than pulled from a font CDN: the console is the
        name layer's own front door, and it should not need a third party's
        name to resolve before it can draw itself.
        """
        if not re.fullmatch(r'[a-z0-9._-]+\.woff2', name or ''):
            return 404, {'error': 'no such font'}
        try:
            with open(os.path.join(HERE, 'fonts', name), 'rb') as f:
                blob = f.read()
        except OSError:
            return 404, {'error': f'no such font: {name}'}
        return 200, (blob, 'font/woff2')

    def _console(self):
        try:
            with open(os.path.join(HERE, 'console.html'), 'rb') as f:
                return f.read()
        except OSError:
            return b'<h1>dns</h1><p>console.html is missing</p>'


def serve(port=PORT, listener=True):
    if listener:
        state = dnsd.start()
        if state.get('error'):
            sys.stderr.write(f'[dns] name server not listening: {state["error"]}\n')
        else:
            sys.stderr.write(f'[dns] name server on {state["bind"]}:{state["port"]} '
                             f'(udp+tcp) for {", ".join(state["zones"])}\n')
    httpd = ThreadingHTTPServer(('0.0.0.0', int(port)), Handler)
    httpd.daemon_threads = True
    sys.stderr.write(f'[dns] api :{port}  console :{port}{BASE}  mcp :{port}/mcp\n')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dnsd.stop()
        httpd.server_close()


if __name__ == '__main__':
    argv = sys.argv[1:]
    port = int(argv[argv.index('--port') + 1]) if '--port' in argv else PORT
    serve(port, listener='--no-listener' not in argv)
