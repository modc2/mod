"""pm2 entrypoint: the hourly root-repo push loop (see Mod.root_auto).

Temporary: it exists so the whole mod repo lands on GitHub on a timer instead
of by hand. `m github/root_auto on=0` stops it.
"""
import os
import sys

# this dir's mod.py (the module anchor) shadows the `mod` package when the
# script dir leads sys.path — drop it and lead with the repo root instead
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path = [ROOT] + [p for p in sys.path if os.path.abspath(p or '.') != HERE]

import mod as m

if __name__ == '__main__':
    tick = int(os.environ.get('GITHUB_ROOT_TICK', 60))
    m.mod('github')().root_loop(tick=tick)
