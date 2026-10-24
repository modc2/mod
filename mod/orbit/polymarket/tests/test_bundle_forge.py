"""The BUNDLER agent's engine (src/app/app/lib/bundleForge.ts).

Runs the tsx suite under pytest: selection is train-only (rewriting every
holdout block changes no bundle), the screen's rejects, complementary legs,
the overlap rule, grading, ranking without the holdout, and an end-to-end
pass through the real replay. Skips when node/npx or node_modules are absent.
"""

import json
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
APP = os.path.join(MODULE, 'src', 'app')

needs_node = pytest.mark.skipif(
    shutil.which('npx') is None or not os.path.isdir(os.path.join(APP, 'node_modules')),
    reason='node/npx or src/app/node_modules not installed',
)


@needs_node
def test_bundle_forge_suite():
    r = subprocess.run(['npx', '--no-install', 'tsx', 'app/lib/__test_bundle_forge__.ts'],
                       cwd=APP, capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-4000:]
    assert '[FAIL]' not in out and 'ALL PASS' in out, out[-4000:]


def test_bundler_agent_mod_contract():
    d = os.path.join(MODULE, 'src', 'agents', 'bundler')
    cfg = json.load(open(os.path.join(d, 'config.json')))
    assert cfg['name'] == 'bundler' and cfg['kind'] == 'background'
    assert cfg['route'] == '/_api/bundler' and cfg['query'] == 'run=1'
    assert os.path.isfile(os.path.join(MODULE, 'src', 'app', 'app', '%5Fapi', 'bundler', 'route.ts'))


def test_pm_bundles_gate():
    import sys
    sys.path.insert(0, os.path.join(MODULE, 'src'))
    import mcp
    assert mcp._gate_kind('pm_bundles', {'op': 'run'}) == 'spend'
    assert mcp._gate_kind('pm_bundles', {'op': 'on'}) == 'spend'
    for op in ('latest', 'status', 'get', 'off'):
        assert mcp._gate_kind('pm_bundles', {'op': op}) is None
    assert mcp._gate_kind('pm_bundles', {}) is None
