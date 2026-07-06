"""
Bridge between Next.js API routes and the LocalFS Python module.

Reads a single JSON request from stdin: {"method": str, "args": dict}
Writes a single JSON response to stdout: {"ok": bool, "data": any} or
                                         {"ok": false, "error": str}

Binary inputs (for `put`) are base64-encoded under args.data_b64.
Binary outputs (for `cat`/`get_file`) are base64-encoded under data_b64.
"""

import base64
import json
import os
import sys
import traceback

# Make the localfs package importable regardless of cwd
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

from localfs import LocalFS  # noqa: E402


ALLOWED = {
    'put', 'get', 'cat', 'rm',
    'pin_add', 'pin_rm', 'pins', 'pinned',
    'stats', 'gc', 'add_file',
}


def _to_jsonable(value):
    """Coerce values that JSON can't serialize (bytes -> base64 string)."""
    if isinstance(value, bytes):
        return {'__bytes_b64__': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def main():
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
    except Exception as exc:
        json.dump({'ok': False, 'error': f'invalid json: {exc}'}, sys.stdout)
        return

    method = req.get('method')
    args = req.get('args') or {}

    if method not in ALLOWED:
        json.dump({'ok': False, 'error': f'method not allowed: {method}'}, sys.stdout)
        return

    try:
        lfs = LocalFS()

        if method == 'put':
            if 'data_b64' in args:
                data = base64.b64decode(args['data_b64'])
            else:
                data = args.get('data')
            pin = bool(args.get('pin', True))
            cid = lfs.put(data, pin=pin)
            result = {'cid': cid}

        elif method == 'get':
            cid = args['cid']
            value = lfs.get(cid)
            # value may be bytes/str/dict/list
            if isinstance(value, bytes):
                result = {'data_b64': base64.b64encode(value).decode('ascii'), 'kind': 'bytes'}
            elif isinstance(value, (dict, list)):
                result = {'data': value, 'kind': 'json'}
            else:
                result = {'data': value, 'kind': 'text'}

        elif method == 'cat':
            cid = args['cid']
            blob = lfs.get_file(cid)
            result = {'data_b64': base64.b64encode(blob).decode('ascii'), 'size': len(blob)}

        elif method == 'rm':
            result = lfs.rm(args['cid'])

        elif method == 'pin_add':
            result = lfs.pin_add(args['cid'])

        elif method == 'pin_rm':
            result = lfs.pin_rm(args['cid'])

        elif method == 'pins':
            cid = args.get('cid')
            result = lfs.pins(cid) if cid else lfs.pins()

        elif method == 'pinned':
            result = {'pinned': lfs.pinned(args['cid'])}

        elif method == 'stats':
            result = lfs.stats()

        elif method == 'gc':
            aggressive = bool(args.get('aggressive', False))
            result = lfs.gc(aggressive=aggressive)

        elif method == 'add_file':
            result = lfs.add_file(args['path'])

        else:  # pragma: no cover — guarded by ALLOWED
            json.dump({'ok': False, 'error': f'unhandled: {method}'}, sys.stdout)
            return

        json.dump({'ok': True, 'data': _to_jsonable(result)}, sys.stdout)

    except FileNotFoundError as exc:
        json.dump({'ok': False, 'error': f'not found: {exc}'}, sys.stdout)
    except Exception as exc:
        json.dump({
            'ok': False,
            'error': str(exc),
            'trace': traceback.format_exc(),
        }, sys.stdout)


if __name__ == '__main__':
    main()
