"""
mod2mcp.bridge — stdio MCP for ONE mod, compiled on the fly. No gateway, no
deploy step: the manifest is built in-process and served over stdin/stdout.

    claude mcp add news -- python3 -m mod2mcp.bridge news            # cwd = orbit/mod2mcp
    python3 -m mod2mcp.bridge polymarket --readonly
    python3 -m mod2mcp.bridge bt --fns search,read,status

stdout belongs to JSON-RPC; everything the loaded mod prints is rerouted to
stderr so a chatty Mod.__init__ can't corrupt the stream.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('mod', help='mod to serve, e.g. "news"')
    ap.add_argument('--readonly', action='store_true', help='expose only view functions')
    ap.add_argument('--fns', default='', help='comma-separated explicit interface')
    ap.add_argument('--exclude', default='', help='comma-separated functions to drop')
    args = ap.parse_args()

    wire = sys.stdout
    sys.stdout = sys.stderr      # mods print; the MCP stream must stay clean

    from . import rpc, runtime   # import after the swap — core prints on boot
    manifest = runtime.contract(args.mod,
                                fns=[f for f in args.fns.split(',') if f] or None,
                                exclude=[f for f in args.exclude.split(',') if f] or None,
                                readonly=args.readonly)
    fn_by_tool = {t['name']: t['fn'] for t in manifest['tools']}

    def call(tool: str, a: dict):
        fn = fn_by_tool.get(tool)
        if fn is None:
            raise LookupError(f'unknown tool {tool!r}')
        return runtime.call(args.mod, fn, a, readonly=manifest['readonly'])

    server = rpc.McpServer(args.mod, '0', f"{args.mod} at {manifest['address']} via mod2mcp",
                           lambda: manifest['tools'], call)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = server.handle(json.loads(line))
        except json.JSONDecodeError:
            resp = {'jsonrpc': '2.0', 'id': None,
                    'error': {'code': -32700, 'message': 'parse error'}}
        except Exception:
            traceback.print_exc(file=sys.stderr)
            continue
        if resp is not None:
            wire.write(json.dumps(resp, default=str) + '\n')
            wire.flush()


if __name__ == '__main__':
    main()
