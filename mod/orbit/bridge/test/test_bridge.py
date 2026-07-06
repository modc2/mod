"""
Tests for Bridge module — snapshot, claims, commitments, health, status.

Uses a tmp dir for claims/commitments storage so tests don't affect real data.
Patches out mod dependencies (localfs, key modules, web3) to run offline.
"""

import json
import os
import sys
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock

# Insert the mod framework root so `import mod` resolves to the framework, not local mod.py
_mod_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_mod_root))

# Pre-patch: insert a fake `mod` module before importing bridge mod.py
_mock_m = MagicMock()
_mock_lfs = MagicMock()
_mock_lfs.valid_cid.return_value = False
_mock_m.mod.return_value = lambda: _mock_lfs
sys.modules.setdefault('_bridge_mock_m', _mock_m)

# Now load the Mod class from bridge/mod.py directly
import importlib.util
_bridge_mod_path = Path(__file__).resolve().parent.parent / 'mod.py'
_spec = importlib.util.spec_from_file_location('bridge_mod', _bridge_mod_path)
_bridge_module = importlib.util.module_from_spec(_spec)
_bridge_module.m = _mock_m  # inject mock before exec
_spec.loader.exec_module(_bridge_module)
BridgeMod = _bridge_module.Mod


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def bridge(tmp_path):
    """Create a Bridge Mod instance with tmp storage and mocked deps."""
    snapshot_dir = tmp_path / 'snapshot'
    snapshot_dir.mkdir()

    balances = {
        '5HgA2JHaR4NXVYBN8WV7hU1eFGTJFQQ8PpQzMgEoAPXJNQzb': 9998,
        '5Eneu6qKT7dcrUntNoEQ3qQfHQBjiovrx1wE8J3uU1TMwkX4': 235128862,
        '5F9Ap1DX2sikreph5Ly2PrGUiCMBM7LFdpJt5x3vrc4jBu64': 170312245,
    }
    with open(snapshot_dir / 'total_balances.json', 'w') as f:
        json.dump(balances, f)

    config = {
        'owner': '5HgA2JHaR4NXVYBN8WV7hU1eFGTJFQQ8PpQzMgEoAPXJNQzb',
        'network': 'testnet',
        'port': 18840,
        'app_port': 18841,
        'contracts': {
            'testnet': {
                'chainId': '84532',
                'url': 'https://sepolia.base.org',
                'contracts': {
                    'BridgeableToken': {
                        'address': '0x0472a18bcB061B0bd047Db60f5717C8215dC7EeD',
                    }
                }
            }
        }
    }

    mock_lfs = MagicMock()
    mock_lfs.valid_cid.return_value = False

    # Build instance without calling __init__ (which has external deps)
    instance = object.__new__(BridgeMod)
    instance.module_dir = Path(__file__).resolve().parent.parent
    instance.config = config
    instance.store_dir = tmp_path / 'store'
    instance.store_dir.mkdir(parents=True, exist_ok=True)
    instance.claims_path = instance.store_dir / 'claims.json'
    instance.commitments_path = instance.store_dir / 'commitments.json'
    instance.snapshot_dir = snapshot_dir
    instance.owner_address = config['owner']
    instance.network = 'testnet'
    instance.signer_key = ''
    instance.port = 18840
    instance.app_port = 18841
    net_cfg = config['contracts']['testnet']
    instance.rpc_url = net_cfg['url']
    instance.contract_address = net_cfg['contracts']['BridgeableToken']['address']
    instance.lfs = mock_lfs
    instance._abi_cid = ''

    instance.used_sigs_path = instance.store_dir / 'used_signatures.json'

    # Load snapshot (uses instance._load_snapshot which is a normal method)
    raw = instance._load_snapshot('total_balances.json')
    instance._total_balances = {addr: float(val) / 1e9 for addr, val in raw.items()}

    # Set admin key for tests
    os.environ['BRIDGE_ADMIN_KEY'] = 'test_admin_key'

    return instance


ADDR1 = '5HgA2JHaR4NXVYBN8WV7hU1eFGTJFQQ8PpQzMgEoAPXJNQzb'
ADDR2 = '5Eneu6qKT7dcrUntNoEQ3qQfHQBjiovrx1wE8J3uU1TMwkX4'
ADDR3 = '5F9Ap1DX2sikreph5Ly2PrGUiCMBM7LFdpJt5x3vrc4jBu64'
ADDR_NONE = '5AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
EVM_ADDR = '0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18'
ADMIN_KEY = 'test_admin_key'


def _add_commitment(bridge, source_address, evm_address='0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18', source_type='substrate'):
    """Helper to insert a commitment directly (bypassing signature verification)."""
    commitments = bridge._load_commitments()
    commitments[source_address] = {
        'source_address': source_address,
        'evm_address': evm_address,
        'source_type': source_type,
        'timestamp': time.time(),
    }
    bridge._save_commitments(commitments)


# ── Health & Status ─────────────────────────────────────────────

class TestHealth:
    def test_health(self, bridge):
        result = bridge.health()
        assert result['status'] == 'ok'
        assert result['module'] == 'bridge'
        assert result['snapshot_addresses'] == 3
        assert result['claims'] == 0

    def test_status(self, bridge):
        result = bridge.status()
        assert result['total_addresses'] == 3
        assert result['total_owed'] > 0
        assert result['total_claimed'] == 0
        assert result['total_unclaimed'] == result['total_owed']
        assert result['claim_count'] == 0

    def test_owner(self, bridge):
        assert bridge.owner() == ADDR1


# ── Snapshot ────────────────────────────────────────────────────

class TestSnapshot:
    def test_in_snapshot_found(self, bridge):
        result = bridge.in_snapshot(ADDR1)
        assert result['in_snapshot'] is True
        assert result['balance'] > 0

    def test_in_snapshot_not_found(self, bridge):
        result = bridge.in_snapshot(ADDR_NONE)
        assert result['in_snapshot'] is False
        assert result['balance'] == 0

    def test_get_total_balances(self, bridge):
        balances = bridge.get_total_balances()
        assert len(balances) == 3
        assert ADDR1 in balances
        assert ADDR2 in balances

    def test_balances_normalized(self, bridge):
        """Balances should be divided by 1e9 from raw snapshot values."""
        balances = bridge.get_total_balances()
        assert balances[ADDR1] == pytest.approx(9998 / 1e9)


# ── Claims ──────────────────────────────────────────────────────

class TestClaims:
    def _claim(self, bridge, address, evm=EVM_ADDR, source_type='substrate'):
        """Helper: add commitment + claim with mocked signature verification."""
        _add_commitment(bridge, address, evm, source_type)
        bridge._verify_claim_signature = MagicMock(return_value=True)
        ts = int(time.time())
        return bridge.claim(address=address, signature='deadbeef', timestamp=ts)

    def test_claim_success(self, bridge):
        result = self._claim(bridge, ADDR2)
        assert result['success'] is True
        assert result['recipient'] == EVM_ADDR
        assert result['from'] == ADDR2
        assert result['amount'] > 0

    def test_claim_no_address(self, bridge):
        result = bridge.claim(address='', signature='sig', timestamp=int(time.time()))
        assert 'error' in result

    def test_claim_no_signature(self, bridge):
        _add_commitment(bridge, ADDR2)
        result = bridge.claim(address=ADDR2, signature=None, timestamp=int(time.time()))
        assert 'error' in result
        assert 'Signature required' in result['error']

    def test_claim_no_commitment(self, bridge):
        bridge._verify_claim_signature = MagicMock(return_value=True)
        result = bridge.claim(address=ADDR2, signature='sig', timestamp=int(time.time()))
        assert 'error' in result
        assert 'No verified commitment' in result['error']

    def test_claim_not_in_snapshot(self, bridge):
        _add_commitment(bridge, ADDR_NONE)
        bridge._verify_claim_signature = MagicMock(return_value=True)
        result = bridge.claim(address=ADDR_NONE, signature='sig', timestamp=int(time.time()))
        assert 'error' in result
        assert 'No allocation' in result['error']

    def test_claim_duplicate(self, bridge):
        self._claim(bridge, ADDR2)
        bridge._verify_claim_signature = MagicMock(return_value=True)
        result = bridge.claim(address=ADDR2, signature='deadbeef2', timestamp=int(time.time()))
        assert 'error' in result
        assert 'Already claimed' in result['error']

    def test_has_claimed(self, bridge):
        assert bridge.has_claimed(ADDR2)['claimed'] is False
        self._claim(bridge, ADDR2)
        assert bridge.has_claimed(ADDR2)['claimed'] is True

    def test_unclaimed(self, bridge):
        total = bridge.in_snapshot(ADDR2)['balance']
        assert bridge.unclaimed(ADDR2) == total
        self._claim(bridge, ADDR2)
        assert bridge.unclaimed(ADDR2) == 0

    def test_claims_array(self, bridge):
        self._claim(bridge, ADDR2)
        arr = bridge.claims_array()
        assert len(arr) == 1
        assert arr[0]['address'] == ADDR2

    def test_get_claims(self, bridge):
        self._claim(bridge, ADDR2)
        claims = bridge.get_claims()
        assert ADDR2 in claims

    def test_delete_claim(self, bridge):
        self._claim(bridge, ADDR2)
        result = bridge.delete_claim(ADDR2, auth_token=ADMIN_KEY)
        assert result['success'] is True
        assert bridge.has_claimed(ADDR2)['claimed'] is False

    def test_delete_claim_bad_token(self, bridge):
        self._claim(bridge, ADDR2)
        result = bridge.delete_claim(ADDR2, auth_token='wrong_key')
        assert 'error' in result

    def test_delete_claim_not_found(self, bridge):
        result = bridge.delete_claim(ADDR_NONE, auth_token=ADMIN_KEY)
        assert 'error' in result

    def test_status_after_claim(self, bridge):
        self._claim(bridge, ADDR2)
        status = bridge.status()
        assert status['claim_count'] == 1
        assert status['total_claimed'] > 0
        assert status['total_unclaimed'] < status['total_owed']


# ── Commitments ─────────────────────────────────────────────────

class TestCommitments:
    def test_commit_missing_fields(self, bridge):
        result = bridge.commit('', EVM_ADDR, 'sig', 'substrate')
        assert 'error' in result

        result = bridge.commit(ADDR2, '', 'sig', 'substrate')
        assert 'error' in result

        result = bridge.commit(ADDR2, EVM_ADDR, '', 'substrate')
        assert 'error' in result

    def test_commit_bad_source_type(self, bridge):
        result = bridge.commit(ADDR2, EVM_ADDR, 'sig', 'ethereum')
        assert 'error' in result
        assert 'source_type' in result['error']

    def test_commit_not_in_snapshot(self, bridge):
        result = bridge.commit(ADDR_NONE, EVM_ADDR, 'sig', 'substrate')
        assert 'error' in result
        assert 'not in snapshot' in result['error']

    def test_get_commitments_empty(self, bridge):
        assert bridge.get_commitments() == {}

    def test_get_commitment_not_found(self, bridge):
        result = bridge.get_commitment(ADDR_NONE)
        assert 'error' in result

    def test_update_commitment_no_existing(self, bridge):
        result = bridge.update_commitment(ADDR2, EVM_ADDR, 'sig', 'substrate')
        assert 'error' in result
        assert 'No existing' in result['error']

    def test_update_commitment_bad_type(self, bridge):
        result = bridge.update_commitment(ADDR2, EVM_ADDR, 'sig', 'bitcoin')
        assert 'error' in result

    def test_update_commitment_missing_fields(self, bridge):
        result = bridge.update_commitment('', EVM_ADDR, 'sig', 'substrate')
        assert 'error' in result

    def test_update_commitment_after_claim(self, bridge):
        """Cannot update commitment after claiming."""
        _add_commitment(bridge, ADDR2)
        bridge._verify_claim_signature = MagicMock(return_value=True)
        bridge.claim(address=ADDR2, signature='sig', timestamp=int(time.time()))
        result = bridge.update_commitment(ADDR2, '0x1111111111111111111111111111111111111111', 'sig', 'substrate')
        assert 'error' in result
        assert 'after claim' in result['error']

    def test_update_commitment_same_address(self, bridge):
        """Cannot update to same EVM address."""
        _add_commitment(bridge, ADDR2, EVM_ADDR)
        result = bridge.update_commitment(ADDR2, EVM_ADDR, 'sig', 'substrate')
        assert 'error' in result
        assert 'same' in result['error']

    def test_update_commitment_type_mismatch(self, bridge):
        """source_type must match original."""
        _add_commitment(bridge, ADDR2, EVM_ADDR, 'substrate')
        result = bridge.update_commitment(ADDR2, '0x1111111111111111111111111111111111111111', 'sig', 'solana')
        assert 'error' in result
        assert 'source_type' in result['error']


# ── Contract Info ───────────────────────────────────────────────

class TestContractInfo:
    def test_contract_info(self, bridge):
        info = bridge.contract_info()
        assert info['network'] == 'testnet'
        assert info['chain_id'] == '84532'
        assert info['contract_address'] == '0x0472a18bcB061B0bd047Db60f5717C8215dC7EeD'
        assert info['abi_stored'] is False

    def test_contract_info_abi_cid(self, bridge):
        bridge._abi_cid = 'bafytest123'
        bridge.lfs.valid_cid.return_value = True
        info = bridge.contract_info()
        assert info['abi_stored'] is True


# ── Reset ──────────────────────────────────────────────────────

class TestReset:
    def test_reset_clears_data(self, bridge):
        # Add some data
        _add_commitment(bridge, ADDR2)
        bridge._verify_claim_signature = MagicMock(return_value=True)
        bridge.claim(address=ADDR2, signature='sig', timestamp=int(time.time()))

        assert len(bridge.get_claims()) == 1
        assert len(bridge.get_commitments()) == 1

        result = bridge.reset(auth_token=ADMIN_KEY)
        assert result['success'] is True

        assert bridge.get_claims() == {}
        assert bridge.get_commitments() == {}

    def test_reset_requires_auth(self, bridge):
        result = bridge.reset(auth_token='wrong')
        assert 'error' in result

    def test_reset_no_token(self, bridge):
        result = bridge.reset()
        assert 'error' in result
