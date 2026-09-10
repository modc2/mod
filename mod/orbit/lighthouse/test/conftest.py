"""Test wiring.

Every test runs against a throwaway state dir, because the real one
(~/.mod/lighthouse) holds a deployment's API key and owner claim and a test
that can overwrite those is a test nobody will run twice. The env vars are set
before anything under test is imported — identity.py and mod.py read them at
import time.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent.parent.parent            # holds the `mod` package

STATE = Path(tempfile.mkdtemp(prefix='lighthouse-test-'))
os.environ['LIGHTHOUSE_DIR'] = str(STATE)
os.environ.setdefault('LIGHTHOUSE_STORE_URL', 'http://127.0.0.1:50152')
# No test may knock on the real activator: a store call against a dead port
# would otherwise start the fleet's actual store module as a side effect. The
# wake tests pass an explicit stub activator instead.
os.environ['LIGHTHOUSE_ACTIVATOR_URL'] = ''

# Repo root first so `import mod` is the protocol package; the module root is
# added after, so `import identity` / `import store_link` resolve too. Both are
# needed and the order is the whole trick (see protocol.py).
for path in (str(ROOT), str(REPO)):
    if path in sys.path:
        sys.path.remove(path)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))


@pytest.fixture(scope='session')
def state_dir() -> Path:
    return STATE


@pytest.fixture(scope='session')
def core():
    """This module's mod.py, loaded by path (its name collides with `mod`)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('lighthouse_core', ROOT / 'mod.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='session')
def _auth():
    """Auth bound to a deterministic test key — NOT the box's own key, so a
    test can never mint something with real standing in the store."""
    from protocol import auth
    return auth(key='test.lighthouse')


@pytest.fixture(scope='session')
def token(_auth):
    """A real protocol token: signed envelope, verified the same way as a wallet's."""
    return _auth.token({'mod': 'lighthouse-test'})


@pytest.fixture(scope='session')
def address(_auth):
    return _auth.key.address.lower()


@pytest.fixture(scope='session')
def client():
    from fastapi.testclient import TestClient
    from api.api import app
    return TestClient(app)


@pytest.fixture(scope='session')
def store_up() -> bool:
    """Is a store module actually listening? Bridge tests skip when not."""
    import requests
    from store_link import store_url
    try:
        return requests.get(f'{store_url()}/health', timeout=3).status_code == 200
    except Exception:
        return False
