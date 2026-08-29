#!/usr/bin/env python3
"""wingman mcp — twelve tools for turning a folder of photos into a profile.

Ordered the way the work goes: get photos in, audit them, ask for a lineup,
render for an app, export. `wingman_lineup` is the one that matters; the rest
exist so its choices can be inspected, argued with and reproduced.

Self-contained JSON-RPC 2.0 on the standard library, no `mcp` package.

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50830 # Streamable HTTP — POST /mcp
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything that imports us.
    sys.path.append(HERE)

import engine as E                                          # noqa: E402
from engine import WingmanError                             # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Dating-profile photo preparation, local and measured. A set of photos '
    'goes in (wingman_add: base64, a path, a directory on this box, or a URL) '
    'and comes out as a ranked lineup cropped to the card ratio of Tinder, '
    'Hinge or Bumble, with every metadata field stripped. '
    'The path: wingman_add → wingman_audit (what each photo is: face count and '
    'size, sharpness, exposure, a score with the issues that cost it) → '
    'wingman_lineup (the best N in order, near-duplicates collapsed, one '
    'clear solo face leading, group shots late and at most one) → '
    'wingman_render or wingman_export (face-aware crop, gentle polish, zip). '
    'Read `verdict` on each photo and `gaps` on the lineup first — the gaps '
    'are the honest answer, usually "no full-body shot" or "no photo good '
    'enough to lead", and the fix for those is a camera, not an edit. '
    'What the auditor cannot see it does not score: expression, eye contact, '
    'outfit, setting. Say so when you relay a score. Faces are found by an '
    'ONNX detector (UltraFace) — when `detector` says skin-heuristic the boxes '
    'are guesses and the crops should be checked. Nothing is retouched: no '
    'skin smoothing, no background replacement, no reshaping. Nothing leaves '
    'the machine; a set is addressed by an unguessable id, which is the only '
    'thing protecting it, so do not paste ids where they will be read.'
)


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc, **extra):
    return {'type': 'number', 'description': desc, **extra}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_SET = _str('a set: its id, an id prefix, or its name')
_PHOTO = _str('a photo in the set: its id, an id prefix, or its file name')
_PRESET = _str('tinder | hinge | bumble | square | story (default tinder)')


def _t_health(a):
    return E.health()


def _t_guide(a):
    return {'guide': E.GUIDE, 'presets': E.PRESETS,
            'scoring': 'start at 100; each issue costs its `cost`; `bad` issues '
                       'disqualify a photo from leading; lead_ok needs a solo face '
                       '15-45% of the frame, no bad issues, score >= 60'}


def _t_sets(a):
    return E.sets(limit=int(a.get('limit') or 100))


def _t_new(a):
    return E.new_set(a.get('name'))


def _t_add(a):
    return E.add(set_ref=a.get('set'), data=a.get('data'), path=a.get('path'),
                 dir=a.get('dir'), url=a.get('url'), files=a.get('files'),
                 name=a.get('name'), set_name=a.get('set_name'))


def _t_audit(a):
    return E.audit(a['set'], photo=a.get('photo'), force=bool(a.get('force')))


def _t_faces(a):
    return E.faces(a['set'], a['photo'], threshold=a.get('threshold') or E.FACE_THRESHOLD)


def _t_lineup(a):
    return E.lineup(a['set'], n=a.get('n') or 6, allow_group=a.get('allow_group', True),
                    min_score=a.get('min_score') or 35, force=bool(a.get('force')))


def _t_render(a):
    return E.render(a['set'], photo=a.get('photo'), preset=a.get('preset'),
                    ratio=a.get('ratio'), size=a.get('size'), zoom=a.get('zoom') or 'auto',
                    polish_mode=a.get('polish') or 'auto', quality=a.get('quality') or 90,
                    force=bool(a.get('force')), only_lineup=bool(a.get('only_lineup')),
                    n=a.get('n') or 6)


def _t_export(a):
    return E.export(a['set'], preset=a.get('preset'), n=a.get('n') or 6,
                    zoom=a.get('zoom') or 'auto', polish_mode=a.get('polish') or 'auto',
                    quality=a.get('quality') or 90, force=bool(a.get('force')))


def _t_remove(a):
    return E.remove(a['set'], a['photo'])


def _t_delete(a):
    return E.delete_set(a['set'])


TOOLS = {
    'wingman_health': {
        'description': 'Runtime: Pillow, onnxruntime, which face detector is live, '
                       'HEIC support, where state lives, how many sets.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_health},
    'wingman_guide': {
        'description': 'What makes a dating-profile photo work, restricted to what a '
                       'program can check; the app card ratios; how the score is built. '
                       'Read this before relaying a score to a person.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_guide},
    'wingman_sets': {
        'description': 'Every photo set held, newest first, with audit and render progress.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('max sets to return (default 100)')}},
        'handler': _t_sets},
    'wingman_new': {
        'description': 'Start an empty set. Returns its unguessable id, which is the '
                       'only handle to its photos.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': _str('a label for the set')}},
        'handler': _t_new},
    'wingman_add': {
        'description': 'Photos in: one base64 blob, a list of {name, data}, a file '
                       'path, a whole directory, or a URL. Into an existing set, or a '
                       'new set when none is given. Exact duplicates are skipped.',
        'inputSchema': {'type': 'object', 'properties': {
            'set': _str('existing set to add to (omit to create one)'),
            'set_name': _str('label for the new set, when creating'),
            'data': _str('base64 image bytes (data: URLs accepted)'),
            'name': _str('file name for data='),
            'files': {'type': 'array', 'description': 'many at once: [{name, data}]',
                      'items': {'type': 'object'}},
            'path': _str('a file on this box'),
            'dir': _str('a directory on this box — every image in it'),
            'url': _str('an http(s) image URL')}},
        'handler': _t_add},
    'wingman_audit': {
        'description': 'Measure every photo (or one): faces found, face size and '
                       'position, role (headshot/portrait/full/far/scene/group), '
                       'sharpness, exposure, contrast, duplicates hash, EXIF GPS. Each '
                       'issue has a cost and a sentence; score = 100 minus costs; '
                       '`verdict` is the one-line answer.',
        'inputSchema': {'type': 'object', 'properties': {
            'set': _SET, 'photo': _PHOTO,
            'force': _bool('re-measure even if cached')}, 'required': ['set']},
        'handler': _t_audit},
    'wingman_faces': {
        'description': 'Face boxes for one photo, in source pixels, with detector '
                       'confidence. Lower threshold to find small or turned faces.',
        'inputSchema': {'type': 'object', 'properties': {
            'set': _SET, 'photo': _PHOTO,
            'threshold': _num('detector confidence cutoff (default 0.7)')},
            'required': ['set', 'photo']},
        'handler': _t_faces},
    'wingman_lineup': {
        'description': 'The best N photos in the order they should go up, with why '
                       'each earned its slot, why the rest were left out, and `gaps`: '
                       'what the set is missing that no edit can add. Lead = sharp solo '
                       'face 15-45% of the frame; near-duplicates collapse; group shots '
                       'after slot 3 and at most one.',
        'inputSchema': {'type': 'object', 'properties': {
            'set': _SET, 'n': _num('slots to fill, 1-9 (default 6)'),
            'min_score': _num('floor for a slot (default 35)'),
            'allow_group': _bool('permit one group shot late (default true)'),
            'force': _bool('re-audit first')}, 'required': ['set']},
        'handler': _t_lineup},
    'wingman_render': {
        'description': 'Crop and polish for an app. Face-aware crop to the preset '
                       'ratio (eyes ~40% down, headroom kept), resized to the card '
                       'size, gentle autocontrast/exposure/sharpen, saved as JPEG with '
                       'every metadata field stripped. Reports the crop box, what was '
                       'applied and whether the source had to be upscaled. One photo, '
                       'the lineup (only_lineup), or the whole set.',
        'inputSchema': {'type': 'object', 'properties': {
            'set': _SET, 'photo': _PHOTO, 'preset': _PRESET,
            'ratio': _str('override the ratio, e.g. "4:5"'),
            'size': _str('override the output size, e.g. "1080x1350"'),
            'zoom': _str('auto (frame the face) | none (largest crop) | a number like 1.3'),
            'polish': _str('auto | none'),
            'quality': _num('JPEG quality (default 90)'),
            'only_lineup': _bool('render just the lineup slots'),
            'n': _num('lineup size when only_lineup'),
            'force': _bool('re-render even if cached')}, 'required': ['set']},
        'handler': _t_render},
    'wingman_export': {
        'description': 'The whole thing: lineup → render for one preset → a zip in '
                       'slot order (01-…, 02-…) with report.json explaining every '
                       'choice. Returns the zip path; the console serves it at '
                       '/download/<set>/<preset>.zip.',
        'inputSchema': {'type': 'object', 'properties': {
            'set': _SET, 'preset': _PRESET, 'n': _num('slots (default 6)'),
            'zoom': _str('auto | none | number'), 'polish': _str('auto | none'),
            'quality': _num('JPEG quality (default 90)'),
            'force': _bool('re-render everything')}, 'required': ['set']},
        'handler': _t_export},
    'wingman_remove': {
        'description': 'Drop one photo from a set, with its renders and audit.',
        'inputSchema': {'type': 'object', 'properties': {'set': _SET, 'photo': _PHOTO},
                        'required': ['set', 'photo']},
        'handler': _t_remove},
    'wingman_delete': {
        'description': 'Delete a whole set — sources, renders, audits, zips.',
        'inputSchema': {'type': 'object', 'properties': {'set': _SET}, 'required': ['set']},
        'handler': _t_delete},
}


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args):
    """Run one tool by name. Shared with the REST layer, so a route and an MCP
    tools/call cannot drift apart."""
    tool = TOOLS.get(name)
    if not tool:
        raise WingmanError(f'no tool named {name!r} — {", ".join(TOOLS)}', status=404)
    args = {k: v for k, v in (args or {}).items() if v is not None}
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise WingmanError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(out, default=str, indent=2)}],
                             'structuredContent': out if isinstance(out, dict) else None,
                             'isError': False})
    except WingmanError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), default=str)}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'bad arguments for {name}: {e}'}],
                             'isError': True})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'wingman', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def tool_list():
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


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
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import api
        i = argv.index('--port') + 1 if '--port' in argv else -1
        api.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50830)))
    else:
        serve_stdio()
