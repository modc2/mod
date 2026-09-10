"""Test wiring.

Everything runs against a throwaway state dir AND a throwaway module tree.
Both matter: the real state dir holds this deployment's marks, and the real
tree is the fleet — a test that can write `logo` into somebody's config.json
is a test nobody will run twice.

The env vars are set before anything under test is imported, because
identity.py and marks.py read them at import time.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent.parent.parent            # holds the `mod` package

STATE = Path(tempfile.mkdtemp(prefix='logo-test-state-'))
TREE = Path(tempfile.mkdtemp(prefix='logo-test-tree-'))
MODSTATE = Path(tempfile.mkdtemp(prefix='logo-test-mod-'))

os.environ['LOGO_DIR'] = str(STATE)
os.environ['LOGO_TREE'] = str(TREE)
os.environ['MOD_STATE_DIR'] = str(MODSTATE)
os.environ.pop('LOGO_OPEN', None)           # the gate is what we are testing
os.environ.pop('LOGO_OWNER', None)

# Repo root first so `import mod` is the protocol package; the module root
# after, so `import identity` / `import marks` resolve too. Both are needed and
# the order is the whole trick (see protocol.py).
for path in (str(ROOT), str(REPO)):
    if path in sys.path:
        sys.path.remove(path)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))


PRISTINE = {}          # manifest path -> the bytes it was created with


def _module(group: str, name: str, **manifest):
    path = TREE / group / name
    path.mkdir(parents=True, exist_ok=True)
    config = path / 'config.json'
    config.write_text(json.dumps(
        {'name': name, 'version': '1.0.0', 'icon': 'o', **manifest}, indent=4))
    PRISTINE[config] = config.read_text()
    return path


@pytest.fixture(scope='session')
def keys():
    """Two deterministic identities — NOT the box's own key, so no test can
    mint something with real standing anywhere in the fleet."""
    from protocol import auth
    owner = auth(key='test.logo.owner')
    stranger = auth(key='test.logo.stranger')
    return {
        'owner': owner, 'stranger': stranger,
        'owner_address': owner.key.address.lower(),
        'stranger_address': stranger.key.address.lower(),
        'owner_token': owner.token({'scope': 'logo'}),
        'stranger_token': stranger.token({'scope': 'logo'}),
    }


@pytest.fixture(scope='session', autouse=True)
def tree(keys):
    """A tiny fake fleet:

        orbit/demo     owned by the test owner key
        orbit/shared   owned by nobody in config, one co-owner in ~/.mod
        orbit/orphan   declares no owner at all
        core/demo      same bare name as orbit/demo — the collision case
    """
    _module('orbit', 'demo', owner=keys['owner_address'])
    _module('orbit', 'shared')
    _module('orbit', 'orphan')
    _module('core', 'demo', owner=keys['stranger_address'])
    (MODSTATE / 'shared').mkdir(parents=True, exist_ok=True)
    (MODSTATE / 'shared' / 'owners.json').write_text(
        json.dumps({'addresses': [keys['owner_address']]}))
    return TREE


@pytest.fixture(scope='session')
def state_dir() -> Path:
    return STATE


@pytest.fixture(scope='session')
def tree_dir() -> Path:
    return TREE


@pytest.fixture()
def clean():
    """Back to a fleet where nobody has set a mark: no stored marks, and every
    fake manifest exactly as it was created. Writes touch both, so a test that
    inspects either has to start from a known state."""
    import shutil
    marks = STATE / 'marks'
    if marks.exists():
        shutil.rmtree(marks)
    for path, text in PRISTINE.items():
        if path.read_text() != text:
            path.write_text(text)
    yield


@pytest.fixture(scope='session')
def client():
    from fastapi.testclient import TestClient
    from api.api import app
    return TestClient(app)
