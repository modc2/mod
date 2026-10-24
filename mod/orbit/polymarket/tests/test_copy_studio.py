"""The COPY STUDIO (copy traders, allocate to each, backtest on highlight).

The studio's logic is TypeScript (src/app/app/lib/copyStudio.ts) and is
pinned by its own tsx suite; this runs that suite — and the basket engine
suite it is built on — under pytest so `pytest tests/` covers the module.
Skips cleanly when node/npx or the app's node_modules aren't installed.
"""

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), 'src', 'app')

needs_node = pytest.mark.skipif(
    shutil.which('npx') is None or not os.path.isdir(os.path.join(APP, 'node_modules')),
    reason='node/npx or src/app/node_modules not installed',
)


def _tsx(path):
    r = subprocess.run(
        ['npx', '--no-install', 'tsx', path],
        cwd=APP, capture_output=True, text=True, timeout=300,
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-4000:]
    assert '[FAIL]' not in out, out[-4000:]
    return out


@needs_node
def test_copy_studio_suite():
    out = _tsx('app/lib/__test_copy_studio__.ts')
    assert 'ALL PASS' in out


@needs_node
def test_studio_page_and_nav_wired():
    app = os.path.join(APP, 'app')
    assert os.path.isfile(os.path.join(app, 'copytrade', 'page.tsx'))
    with open(os.path.join(app, 'page.tsx')) as f:
        assert 'redirect("/copytrade")' in f.read(), 'front door should be the copy studio'
    with open(os.path.join(app, 'components', 'NavMenu.tsx')) as f:
        assert 'lib/navTabs' in f.read()


@needs_node
def test_basket_engine_suite():
    """The studio replays through lib/basketSim — its suite must stay green."""
    _tsx('app/lib/__test__.ts')
