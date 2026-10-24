"""Shared test setup.

Every Mod(store=tmp) draws its own post-quantum key on first use, and an
OHX-KECCAK key costs two Merkle trees (~7s in Python). One key is built per
session here and copied into each fresh secrets dir instead — same code
paths, same files, no per-test keygen.
"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent


def _pq():
    name = 'openhouse_pq'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, MODULE_DIR / 'pq.py')
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


@pytest.fixture(scope='session')
def pq_template(tmp_path_factory):
    d = tmp_path_factory.mktemp('pq_template')
    s = _pq().Signer(d)
    s.public()
    s.sign('0x' + '00' * 32)          # warms the first bottom tree too
    data = json.loads((d / 'proof_pq.json').read_text())
    data['next'] = 0
    (d / 'proof_pq.json').write_text(json.dumps(data))
    return d


@pytest.fixture(autouse=True)
def _seed_pq_keys(pq_template, monkeypatch):
    Signer = _pq().Signer
    orig = Signer.__init__

    def init(self, secrets_dir):
        orig(self, secrets_dir)
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(pq_template / 'proof_pq.json', self.path)
            shutil.copytree(pq_template / 'proof_pq_trees', self.path.parent / 'proof_pq_trees',
                            dirs_exist_ok=True)

    monkeypatch.setattr(Signer, '__init__', init)
