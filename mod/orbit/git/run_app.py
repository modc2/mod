"""pm2 entrypoint: run the git app in the foreground (see Mod.worker)."""
import os
import sys

# this dir's mod.py (the module anchor) shadows the `mod` package when the
# script dir leads sys.path — drop it and lead with the repo root instead
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path = [ROOT] + [p for p in sys.path if os.path.abspath(p or '.') != HERE]

import mod as m

if __name__ == '__main__':
    port = int(os.environ.get('GIT_APP_PORT', 50330))
    m.mod('git')().serve(port=port, background=False)
