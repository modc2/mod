"""The CONSISTENT score reaches the API from both agent surfaces.

pm_top_traders (src/mcp.py) and `m polymarket/active_traders` (src/mod.py)
must accept sort=consistent and forward min_consistency_score as the
server's `minConsistencyScore` query param — clamped to 0–100, omitted when
off. The score itself is pinned in routes.rs + __test__.ts; this only pins
the plumbing. No network: the HTTP getter is monkeypatched.
"""

import importlib.util
import os
import sys
import urllib.parse

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src')


@pytest.fixture()
def mcp(tmp_path, monkeypatch):
    monkeypatch.setenv('POLYMARKET_ACCESS_DIR', str(tmp_path))
    spec = importlib.util.spec_from_file_location('polymarket_mcp_cs', os.path.join(SRC, 'mcp.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules[spec.name]


def _capture(mcp, monkeypatch):
    seen = []

    def fake_get(url, timeout=None):
        seen.append(dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)))
        return {'traders': [], 'total': 0}

    monkeypatch.setattr(mcp, '_get', fake_get)
    return seen


def test_consistent_is_a_valid_sort(mcp):
    assert 'consistent' in mcp.TRADER_SORTS


def test_mcp_forwards_sort_and_floor(mcp, monkeypatch):
    seen = _capture(mcp, monkeypatch)
    out = mcp._t_top_traders({'sort': 'consistent', 'min_consistency_score': 60})
    assert seen[0]['sort'] == 'consistent'
    assert float(seen[0]['minConsistencyScore']) == 60
    assert out['minConsistencyScore'] == 60


def test_mcp_floor_off_and_clamped(mcp, monkeypatch):
    seen = _capture(mcp, monkeypatch)
    mcp._t_top_traders({'sort': 'consistent'})
    assert 'minConsistencyScore' not in seen[0]
    mcp._t_top_traders({'min_consistency_score': 500})
    assert float(seen[1]['minConsistencyScore']) == 100


def test_mcp_schema_documents_the_floor(mcp):
    props = mcp.TOOLS['pm_top_traders']['inputSchema']['properties']
    assert 'min_consistency_score' in props
    assert 'consistent' in props['sort']['description']


def test_mod_py_forwards_floor():
    """mod.py's active_traders — read the source rather than importing the
    whole Mod (it pulls the mod framework); the whitelist + param are what
    matter."""
    src = open(os.path.join(SRC, 'mod.py')).read()
    assert 'min_consistency_score: float = 0' in src
    assert '"consistent"' in src.split('def active_traders', 1)[1].split('def ', 1)[0]
    assert 'params["minConsistencyScore"]' in src
