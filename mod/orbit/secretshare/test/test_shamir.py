"""Roundtrip, failure modes, and Python <-> browser (node) interop."""
import json
import os
import shutil
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(MODULE_DIR))
import shamir  # noqa: E402

JS = MODULE_DIR / 'app' / 'shamir.js'


def test_every_k_subset_rebuilds():
    secret = os.urandom(64)
    shares = shamir.split(secret, n=6, k=3)
    for c in combinations(shares, 3):
        assert shamir.combine(list(c)) == secret
    assert shamir.combine(shares) == secret          # more than k is fine


def test_text_and_string_input():
    shares = shamir.split('correct horse battery staple', n=3, k=2)
    assert shamir.combine(f'{shares[2]}\n{shares[0]}') == b'correct horse battery staple'


def test_too_few_refused():
    shares = shamir.split('x' * 20, n=5, k=3)
    with pytest.raises(shamir.ShareError, match='need 3'):
        shamir.combine(shares[:2])
    with pytest.raises(shamir.ShareError, match='need 3'):
        shamir.combine([shares[0], shares[0], shares[1]])   # duplicates don't count


def test_mixed_sets_refused():
    a = shamir.split('alpha', n=3, k=2)
    b = shamir.split('alpha', n=3, k=2)
    with pytest.raises(shamir.ShareError, match='different splits'):
        shamir.combine([a[0], b[1]])


def test_tampered_share_fails_checksum():
    shares = shamir.split('hello world', n=3, k=2)
    p = shamir.parse(shares[0])
    y = bytearray(p['y'])
    y[0] ^= 1
    bad = f'ss1.{p["set"]}.{p["k"]}.{p["x"]}.{shamir._b64e(bytes(y))}'
    with pytest.raises(shamir.ShareError, match='checksum'):
        shamir.combine([bad, shares[1]])


@pytest.mark.parametrize('n,k', [(1, 1), (3, 4), (256, 2), (3, 1)])
def test_bad_params(n, k):
    with pytest.raises(shamir.ShareError):
        shamir.split('s', n=n, k=k)


def test_inspect_hides_secret():
    s = shamir.split('twelve bytes', n=4, k=2)[3]
    info = shamir.inspect(s)
    assert info['k'] == 2 and info['x'] == 4 and info['secret_bytes'] == 12


def _node(script: str) -> str:
    if not shutil.which('node'):
        pytest.skip('node not installed')
    r = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_python_split_js_combine():
    secret = 'seed: abandon abandon zoo — ünïcode'
    shares = shamir.split(secret, n=5, k=3)
    out = _node(f"""
      const S = require({json.dumps(str(JS))});
      S.combine({json.dumps(shares[1:4])}).then(b => process.stdout.write(Buffer.from(b).toString('utf8')));
    """)
    assert out == secret


def test_js_split_python_combine():
    out = _node(f"""
      const S = require({json.dumps(str(JS))});
      S.split('from the browser', 4, 2).then(s => process.stdout.write(JSON.stringify(s)));
    """)
    shares = json.loads(out)
    assert shamir.combine([shares[3], shares[1]]) == b'from the browser'
