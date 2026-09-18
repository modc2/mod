"""Test wiring.

Every test runs against a throwaway state dir, because the real one
(~/.mod/eth) holds **private keys** and a test that can overwrite those is a
test nobody should ever run. The env vars are set before anything under test is
imported — identity.py, wallet.py, chains.py and ledger.py all read them at
import time.

Chain-touching tests are skipped rather than mocked when no local node is
listening. A mocked RPC would prove that this module can talk to a mock; the
whole value of the `local` network in this module is that it is a real EVM.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent.parent.parent            # holds the `mod` package

STATE = Path(tempfile.mkdtemp(prefix='eth-test-'))
os.environ['ETH_DIR'] = str(STATE)
os.environ['ETH_DB'] = str(STATE / 'eth.db')
os.environ['ETH_NETWORK'] = 'local'
os.environ.pop('ETH_OPEN', None)            # auth must be tested as it ships
os.environ.pop('ETH_PRIVATE_KEY', None)     # no ambient signer in tests

# Repo root first so `import mod` is the protocol package; the module root is
# added after, so `import ops` / `import wallet` resolve too. Both are needed
# and the order is the whole trick (see protocol.py).
for path in (str(ROOT), str(REPO)):
    if path in sys.path:
        sys.path.remove(path)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(REPO))

PASSWORD = 'test-password-123'

# anvil's first default account — funded on any fresh anvil/hardhat node, and
# worthless anywhere else, which is exactly what a test should hold.
ANVIL_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80'
ANVIL_ADDRESS = '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266'


@pytest.fixture(scope='session')
def state_dir() -> Path:
    return STATE


@pytest.fixture(scope='session')
def core():
    """This module's mod.py, loaded by path (its name collides with `mod`)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('eth_core', ROOT / 'mod.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='session')
def _auth():
    """Auth bound to a deterministic test key — NOT the box's own key, so a
    test can never mint a token with real standing anywhere."""
    from protocol import auth
    return auth(key='test.eth')


@pytest.fixture(scope='session')
def token(_auth):
    """A real protocol token: signed envelope, verified the same way as a wallet's."""
    return _auth.token({'mod': 'eth-test'})


@pytest.fixture(scope='session')
def address(_auth):
    return _auth.key.address.lower()


@pytest.fixture(scope='session')
def client():
    from fastapi.testclient import TestClient
    from api.api import app
    return TestClient(app)


@pytest.fixture(scope='session')
def chain_up() -> bool:
    """Is a local EVM actually listening? Chain tests skip when not."""
    import chains
    return bool(chains.reachable('local').get('ok'))


@pytest.fixture(scope='session')
def funded(chain_up, address):
    """anvil's first account, imported into the test vault under the caller."""
    if not chain_up:
        pytest.skip('no local EVM on :8545')
    import wallet
    try:
        wallet.import_key(address, 'funded', PASSWORD, ANVIL_KEY)
    except wallet.WalletError:
        pass                                # a previous test in this session
    wallet.unlock(address, 'funded', PASSWORD, 900)
    return 'funded'
