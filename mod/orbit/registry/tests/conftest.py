"""Shared test fixtures for registry module."""

import os
import sys
import json
import shutil
import tempfile
import pytest

# Add parent for imports
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'onchain', 'src'))

from registry.offchain import OffchainRegistry
from solana.solana import SolanaRegistry
from near.near import NearRegistry


@pytest.fixture
def tmp_dir():
    """Temporary directory for test storage."""
    d = tempfile.mkdtemp(prefix='registry_test_')
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def offchain(tmp_dir):
    """Fresh off-chain registry instance."""
    return OffchainRegistry(storage_path=tmp_dir)


@pytest.fixture
def solana_backend(tmp_dir):
    """Solana registry with local-only mode (no RPC calls)."""
    return SolanaRegistry(
        rpc_url='https://api.devnet.solana.com',
        network='devnet',
        storage_path=tmp_dir,
    )


@pytest.fixture
def near_backend(tmp_dir):
    """NEAR registry with local-only mode (no contract calls)."""
    return NearRegistry(
        network='testnet',
        storage_path=tmp_dir,
    )
