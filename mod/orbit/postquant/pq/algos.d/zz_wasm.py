"""The wasm binding — runs last, wraps every key type that ships a verifier.

This plugin registers no algorithm. It is the enforcement pass: after every
other plugin has had its say, each SigAlgo whose key type has a wasm blob
(builtins from pq/wasm/manifest.json, plugins via their own algo.wasm stamp)
gets its verify() replaced by a dispatch into that blob, hash-checked. From
here on, a transaction witness is valid only if the wasm that IS its
algorithm says so — see pq/wasmvm.py for the whole argument.

Named zz_ so the loader's basename sort runs it after every registrar,
in-tree and user-dir alike.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wasmvm  # noqa: E402


def register(algos):
    wasmvm.bind(algos)
