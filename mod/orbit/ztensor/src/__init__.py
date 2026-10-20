"""ztensor's source package, loaded as `ztensor_src` (see mod.py: a bare `core`
or `mod` import would resolve to the fleet's, not ours).

    core/       what every chain shares: felt/IO helpers, the Cairo signer
                (core/prover/ + keys), stark.py (curve + Poseidon in Python)
    networks/   one adapter per chain, each with its contract, driver,
                tools and its own tests — see networks/README.md
"""
import os

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)            # the module dir: config.json, data/
DATA = os.path.join(ROOT, 'data')      # state (keys, deployments) — gitignored
