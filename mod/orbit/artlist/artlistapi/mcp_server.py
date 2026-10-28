"""MCP server for artlist — one dispatch shared by stdio and HTTP transports."""
import json
import sys

from . import api, client

PROTOCOL = '2025-06-18'

TOOLS = [
    {'name': 'artlist_music',
     'description': 'Search Artlist royalty-free music. Returns songs with artist, duration, tags and a playable preview_url.',
     'inputSchema': {'type': 'object', 'properties': {
         'q': {'type': 'string', 'description': 'search terms (mood, genre, use-case)'},
         'k': {'type': 'integer', 'default': 10}, 'page': {'type': 'integer', 'default': 1}},
         'required': ['q']}},
    {'name': 'artlist_song',
     'description': 'Fetch Artlist songs by id.',
     'inputSchema': {'type': 'object', 'properties': {
         'ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['ids']}},
    {'name': 'artlist_sfx',
     'description': 'Search Artlist sound effects. Returns items with a playable preview_url.',
     'inputSchema': {'type': 'object', 'properties': {
         'q': {'type': 'string'}, 'k': {'type': 'integer', 'default': 10},
         'sort': {'type': 'string', 'enum': ['NEWEST', 'TOP_DOWNLOADS', 'STAFF_PICKS']}},
         'required': ['q']}},
    {'name': 'artlist_footage',
     'description': 'Search Artlist/Artgrid stock footage clips (name, story, thumbnail, duration, tags).',
     'inputSchema': {'type': 'object', 'properties': {
         'q': {'type': 'string'}, 'k': {'type': 'integer', 'default': 10}}, 'required': ['q']}},
    {'name': 'artlist_templates',
     'description': 'Search Artlist video templates (After Effects / Premiere / FCP / Resolve) with HLS previews.',
     'inputSchema': {'type': 'object', 'properties': {
         'q': {'type': 'string'}, 'k': {'type': 'integer', 'default': 10}}, 'required': ['q']}},
    {'name': 'artlist_voices',
     'description': 'List Artlist AI voiceover voices; each accent has a playable preview_url.',
     'inputSchema': {'type': 'object', 'properties': {
         'page': {'type': 'integer', 'default': 1}, 'k': {'type': 'integer', 'default': 10}}}},
    {'name': 'artlist_gq',
     'description': 'Raw GraphQL against search-api.artlist.io (escape hatch for fields not yet wrapped).',
     'inputSchema': {'type': 'object', 'properties': {
         'query': {'type': 'string'}, 'variables': {'type': 'object'}}, 'required': ['query']}},
    {'name': 'artlist_status',
     'description': 'Module liveness and cache stats.',
     'inputSchema': {'type': 'object', 'properties': {}}},
]


def tool_list() -> list:
    return TOOLS


def call_tool(name: str, args: dict):
    if name == 'artlist_music':
        return api.music(args.get('q', ''), k=int(args.get('k', 10)), page=int(args.get('page', 1)))
    if name == 'artlist_song':
        return api.songs(args.get('ids') or [])
    if name == 'artlist_sfx':
        return api.sfx(args.get('q', ''), k=int(args.get('k', 10)), sort=args.get('sort', 'NEWEST'))
    if name == 'artlist_footage':
        return api.footage(args.get('q', ''), k=int(args.get('k', 10)))
    if name == 'artlist_templates':
        return api.templates(args.get('q', ''), k=int(args.get('k', 10)))
    if name == 'artlist_voices':
        return api.voices(page=int(args.get('page', 1)), k=int(args.get('k', 10)))
    if name == 'artlist_gq':
        return client.gql(args['query'], args.get('variables'))
    if name == 'artlist_status':
        return {'ok': True, 'cache': client.cache_stats()}
    raise ValueError(f'unknown tool: {name}')


def handle_message(msg: dict):
    """One JSON-RPC message in, one response dict out (None for notifications)."""
    mid, method = msg.get('id'), msg.get('method', '')
    if mid is None:
        return None
    try:
        if method == 'initialize':
            result = {'protocolVersion': PROTOCOL, 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'artlist', 'version': '0.1.0'}}
        elif method == 'tools/list':
            result = {'tools': tool_list()}
        elif method == 'tools/call':
            p = msg.get('params') or {}
            out = call_tool(p.get('name', ''), p.get('arguments') or {})
            result = {'content': [{'type': 'text', 'text': json.dumps(out, indent=1)}]}
        elif method == 'ping':
            result = {}
        else:
            return {'jsonrpc': '2.0', 'id': mid,
                    'error': {'code': -32601, 'message': f'method not found: {method}'}}
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}
    except Exception as e:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32000, 'message': str(e)[:500]}}


def main():  # stdio transport
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle_message(json.loads(line))
        except json.JSONDecodeError:
            continue
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
