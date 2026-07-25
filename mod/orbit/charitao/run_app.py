"""pm2 entrypoint for the charitao web app (background worker).

Runs the module's blocking server. PYTHONPATH-independent: it puts the repo root
(the dir containing the `mod` package) on sys.path so `import mod` resolves under
pm2 regardless of cwd.
"""
import os
import sys

_root = os.path.abspath(__file__)
for _ in range(4):                     # charitao -> orbit -> mod(pkg) -> repo root
    _root = os.path.dirname(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

import mod as m  # noqa: E402

port = int(os.environ.get('CHARITAO_PORT', '50270'))
m.mod('charitao')().serve(port=port, host='0.0.0.0', background=False)
