"""CAIRO-MSS-SHA256 — the hash-based key type whose verifier is a Cairo program.

Scheme and honesty notes live in pq/cairomss.py; the Cairo verifier in
pq/cairo/src/lib.cairo (pq_cairo runs a witness through it in the Cairo VM
and checks it agrees with the Python twin that the chain's hot path uses).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cairomss  # noqa: E402


def register(algos):
    algos.register(cairomss.make_algo(algos))
