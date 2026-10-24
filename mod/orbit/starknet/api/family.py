"""starknet/api/family.py — the sibling Starknet mods, as the old /mods shape.

The family now lives in the Apps registry (api/apps.py): each sibling mod —
cairo, starktime, zquorum, zuma, zkwasm — is a submod at mods/<name> with
kind "mod" and its live probe in mods/<name>/mod.py facts(). This file keeps
GET /mods, the starknet_mods MCP tool and the guide's 'mods' topic working.

    mods()      the sibling mods, live
"""

import apps


def mods(fresh=False):
    rows = [r for r in apps.apps(fresh)['apps'] if r.get('kind') == 'mod']
    return {'mods': rows, 'up': sum(1 for r in rows if r.get('up')), 'count': len(rows),
            'at': apps.apps()['at']}


if __name__ == '__main__':
    import json
    print(json.dumps(mods(), indent=2))
