#!/usr/bin/env python3
"""grokbot mcp — Grok's API as MCP tools, over the same code the console uses.

Ten tools. The interesting part is not "call a model" — Grok has few enough
models that picking is easy — it is *whose* key and *which* bot. So every tool
takes an optional `token` (a mod-protocol token: who you are, which decides the
stored key and the saved bots) and an optional `key` (an xAI key for this call
only, storing nothing). Over HTTP the server passes both in from the request
headers, so an MCP client that authenticates once does not repeat itself.

Self-contained: JSON-RPC 2.0 hand-rolled on the stdlib, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50890 # Streamable HTTP — POST /mcp
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

import client as C          # noqa: E402
import identity             # noqa: E402
from client import Client, GrokError    # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Grok (xAI) behind one mod. grok_chat is the workhorse: it takes a prompt or '
    'a message list, an optional model (default ' + C.DEFAULT_MODEL + '), and '
    'search=auto to let Grok search X and the web before answering — the '
    'citations come back with the text. grok_models lists what a key can '
    'actually see (xAI requires a key even to list models). A "grokbot" is a '
    'saved name + model + system prompt: grok_bot_save creates one, grok_bots '
    'lists them, and grok_chat bot=<name> runs it. Bots and stored keys hang off '
    'a signed-in address, so anything that touches them needs `token` — a '
    'mod-protocol token (m.mod("auth")().token({}), or wallet sign-in in the '
    "console). Every call spends the CALLER'S own xAI credits: pass `key` for one "
    'call, or grok_set_key to store it off-tree at 0600.'
)


def _who(a, token):
    """The signed-in address for this call, if any. Never raises for reads."""
    return identity.whoami(a.pop('token', None) or token)


def _need_who(a, token):
    return identity.require(a.pop('token', None) or token)


def _client(a, token, key):
    """Per-call keys never leave this process and are never echoed back."""
    return Client(key=a.pop('key', None) or key, address=_who(a, token))


# ── tools ──

def _t_chat(a, token, key):
    return _client(a, token, key).chat(**a)


def _t_models(a, token, key):
    return _client(a, token, key).models(refresh=bool(a.get('refresh')))


def _t_model(a, token, key):
    id_ = a['id']
    return _client(a, token, key).model(id_)


def _t_key_info(a, token, key):
    return _client(a, token, key).key_info()


def _t_whoami(a, token, key):
    address = _who(a, token)
    return {'address': address, 'role': identity.role(address),
            'signed_in': bool(address),
            'key': Client(key=key, address=address).key_state(),
            'bots': [b['name'] for b in C.bots(address)] if address else [],
            'how_to_sign_in': identity.status()['accepts']}


def _t_set_key(a, token, key):
    address = _need_who(a, token)
    return C.set_user_key(address, a.get('key') or key,
                          persist=a.get('persist', True))


def _t_bots(a, token, key):
    address = _need_who(a, token)
    return {'bots': C.bots(address), 'address': address}


def _t_bot_save(a, token, key):
    address = _need_who(a, token)
    return C.save_bot(address, a['name'], system=a.get('system'),
                      model=a.get('model'), temperature=a.get('temperature'),
                      search=a.get('search'), description=a.get('description'))


def _t_bot_delete(a, token, key):
    return C.delete_bot(_need_who(a, token), a['name'])


def _t_raw(a, token, key):
    c = _client(a, token, key)
    return c.raw(a['path'], method=a.get('method', 'GET'), body=a.get('body'),
                 params=a.get('params'))


_TOKEN = {'type': 'string', 'description': 'mod-protocol token — who you are; '
          'decides the stored key and the saved bots'}
_KEY = {'type': 'string', 'description': 'xAI key for this call only (xai-…); '
        'nothing is stored'}

TOOLS = {
    'grok_chat': {
        'fn': _t_chat,
        'description': 'Ask Grok. prompt or messages; model defaults to '
                       + C.DEFAULT_MODEL + '; bot=<name> runs a saved bot; '
                       'search=auto lets Grok search X and the web first.',
        'inputSchema': {'type': 'object', 'properties': {
            'prompt': {'type': 'string'},
            'messages': {'type': 'array', 'items': {'type': 'object'},
                         'description': 'OpenAI-shaped [{role, content}, …]'},
            'system': {'type': 'string'},
            'model': {'type': 'string'},
            'bot': {'type': 'string', 'description': 'a saved bot (needs token)'},
            'temperature': {'type': 'number'},
            'max_tokens': {'type': 'integer'},
            'search': {'type': 'string', 'enum': ['auto', 'on', 'off'],
                       'description': 'live search over X and the web'},
            'token': _TOKEN, 'key': _KEY}},
    },
    'grok_models': {
        'fn': _t_models,
        'description': 'Every Grok model this key can see, with USD-per-million '
                       'prices. xAI requires a key even to list them.',
        'inputSchema': {'type': 'object', 'properties': {
            'refresh': {'type': 'boolean'}, 'token': _TOKEN, 'key': _KEY}},
    },
    'grok_model': {
        'fn': _t_model,
        'description': 'One model by id — modalities and price.',
        'inputSchema': {'type': 'object', 'required': ['id'], 'properties': {
            'id': {'type': 'string'}, 'token': _TOKEN, 'key': _KEY}},
    },
    'grok_key_info': {
        'fn': _t_key_info,
        'description': "What xAI says about the resolved key: name, blocked "
                       'state, permissions.',
        'inputSchema': {'type': 'object', 'properties': {
            'token': _TOKEN, 'key': _KEY}},
    },
    'grok_whoami': {
        'fn': _t_whoami,
        'description': 'Who this token is, whether a key resolved and from '
                       'where, and which bots the account has.',
        'inputSchema': {'type': 'object', 'properties': {
            'token': _TOKEN, 'key': _KEY}},
    },
    'grok_set_key': {
        'fn': _t_set_key,
        'description': 'Store your xAI key against your address, 0600 and '
                       'off-tree. Send key="" to forget it.',
        'inputSchema': {'type': 'object', 'required': ['key'], 'properties': {
            'key': {'type': 'string'}, 'persist': {'type': 'boolean'},
            'token': _TOKEN}},
    },
    'grok_bots': {
        'fn': _t_bots,
        'description': 'The bots saved against your address.',
        'inputSchema': {'type': 'object', 'properties': {'token': _TOKEN}},
    },
    'grok_bot_save': {
        'fn': _t_bot_save,
        'description': 'Create or update a bot: a name, a model, a system '
                       'prompt, and whether it searches.',
        'inputSchema': {'type': 'object', 'required': ['name'], 'properties': {
            'name': {'type': 'string'}, 'system': {'type': 'string'},
            'model': {'type': 'string'}, 'temperature': {'type': 'number'},
            'search': {'type': 'string', 'enum': ['auto', 'on', 'off']},
            'description': {'type': 'string'}, 'token': _TOKEN}},
    },
    'grok_bot_delete': {
        'fn': _t_bot_delete,
        'description': 'Delete one of your bots.',
        'inputSchema': {'type': 'object', 'required': ['name'], 'properties': {
            'name': {'type': 'string'}, 'token': _TOKEN}},
    },
    'grok_raw': {
        'fn': _t_raw,
        'description': 'Escape hatch: any xAI route (path like /chat/'
                       'completions) with the resolved key attached.',
        'inputSchema': {'type': 'object', 'required': ['path'], 'properties': {
            'path': {'type': 'string'}, 'method': {'type': 'string'},
            'body': {'type': 'object'}, 'params': {'type': 'object'},
            'token': _TOKEN, 'key': _KEY}},
    },
}


def call_tool(name, arguments=None, token=None, key=None):
    tool = TOOLS.get(name)
    if not tool:
        raise GrokError(f'no tool {name!r} — {", ".join(TOOLS)}', status=404)
    return tool['fn'](dict(arguments or {}), token, key)


# ── json-rpc ──

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def _fail(id_, payload):
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': True}
    if isinstance(payload, dict):
        out['structuredContent'] = payload
    return _result(id_, out)


def _call(id_, params, token=None, key=None):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = call_tool(name, args, token=token, key=key)
    except GrokError as e:
        # A tool failure is a *successful* JSON-RPC response carrying isError,
        # per the MCP spec, so the model reads the hint and retries.
        return _fail(id_, e.dict())
    except identity.AuthError as e:
        return _fail(id_, {'error': str(e), 'status': 401})
    except identity.Denied as e:
        return _fail(id_, {'error': str(e), 'status': 403})
    except KeyError as e:
        return _fail(id_, f'{name}: missing argument {e}')
    except TypeError as e:
        return _fail(id_, f'{name}: bad arguments — {e}')
    except Exception as e:                                   # noqa: BLE001
        return _fail(id_, f'{name} failed: {type(e).__name__}: {e}')
    text = result if isinstance(result, str) else json.dumps(result, indent=2,
                                                             default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body, token=None, key=None):
    if not isinstance(body, dict) or body.get('jsonrpc') != '2.0':
        return _error(None, -32600, 'expected a JSON-RPC 2.0 object')
    if not body.get('method'):
        return _error(body.get('id'), -32600, 'method is required')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'grokbot', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params, token=token, key=key)
    return _error(id_, -32601, f'method not found: {method}')


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def tool_list():
    return [{'name': n, 'description': t['description'],
             'inputSchema': t['inputSchema']} for n, t in TOOLS.items()]


# ── transports ──

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body, token=os.environ.get('GROKBOT_TOKEN'))
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50890)))
    else:
        serve_stdio()
