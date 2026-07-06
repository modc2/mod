"""Tests for EVM registry backend (mocked web3)."""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)
sys.path.insert(0, os.path.join(_root, 'onchain', 'src'))
sys.path.insert(0, os.path.join(_root, 'registry'))

from evm.evm import EVMRegistry, REGISTRY_ABI


class TestEVMInit:
    def test_default_config(self):
        r = EVMRegistry(registry_address='0x' + '1' * 40)
        assert r.rpc_url == 'https://sepolia.base.org'
        assert r.chain_id == 84532
        assert r.name == 'evm'

    def test_custom_config(self):
        r = EVMRegistry(
            rpc_url='http://localhost:8545',
            chain_id=1337,
            registry_address='0x' + '2' * 40,
        )
        assert r.rpc_url == 'http://localhost:8545'
        assert r.chain_id == 1337

    def test_abi_loaded(self):
        r = EVMRegistry(registry_address='0x' + '1' * 40)
        # Should load ABI from artifact or use inline fallback
        assert isinstance(r._abi, list)
        assert len(r._abi) > 0
        # Should have registerMod function
        fn_names = [e.get('name') for e in r._abi if e.get('type') == 'function']
        assert 'registerMod' in fn_names


class TestEVMContractCalls:
    @pytest.fixture
    def mock_evm(self):
        """EVM registry with fully mocked web3."""
        r = EVMRegistry(
            rpc_url='http://localhost:8545',
            chain_id=1337,
            registry_address='0x' + 'a' * 40,
            private_key='0x' + 'b' * 64,
        )
        # Mock web3 internals
        r._w3 = MagicMock()
        r._w3.is_connected.return_value = True
        r._w3.eth.gas_price = 1000000000
        r._w3.eth.get_transaction_count.return_value = 0

        mock_contract = MagicMock()
        r._contract = mock_contract

        mock_account = MagicMock()
        mock_account.address = '0x' + 'c' * 40
        mock_account.sign_transaction.return_value = MagicMock(raw_transaction=b'\x00')
        r._account = mock_account

        r._w3.eth.send_raw_transaction.return_value = b'\x01' * 32
        r._w3.eth.wait_for_transaction_receipt.return_value = {'status': 1, 'logs': []}

        return r

    def test_get_calls_contract(self, mock_evm):
        mock_evm._contract.functions.getMod.return_value.call.return_value = (
            '0x' + 'c' * 40, 'testmod', 'ipfs://Qm123'
        )
        result = mock_evm.get('1')
        assert result['name'] == 'testmod'
        assert result['data'] == 'ipfs://Qm123'
        mock_evm._contract.functions.getMod.assert_called_with(1)

    def test_get_returns_none_for_zero_address(self, mock_evm):
        mock_evm._contract.functions.getMod.return_value.call.return_value = (
            '0x' + '0' * 40, '', ''
        )
        assert mock_evm.get('1') is None

    def test_get_user_mods(self, mock_evm):
        mock_evm._contract.functions.getUserMods.return_value.call.return_value = [1, 2]
        mock_evm._contract.functions.getMod.return_value.call.side_effect = [
            ('0x' + 'c' * 40, 'mod1', 'data1'),
            ('0x' + 'c' * 40, 'mod2', 'data2'),
        ]
        mods = mock_evm.get_user_mods('0x' + 'c' * 40)
        assert len(mods) == 2

    def test_is_name_taken(self, mock_evm):
        mock_evm._contract.functions.isNameTaken.return_value.call.return_value = True
        assert mock_evm.is_name_taken('0x' + 'c' * 40, 'testmod') is True

    def test_register_builds_tx(self, mock_evm):
        mock_fn = MagicMock()
        mock_evm._contract.functions.registerMod.return_value = mock_fn
        mock_fn.build_transaction.return_value = {'to': '0x', 'data': '0x'}
        mock_evm._contract.events.ModRegistered.return_value.process_receipt.return_value = [
            {'args': {'modId': 42}}
        ]
        result = mock_evm.register('testmod', 'data')
        assert result == '42'
        mock_evm._contract.functions.registerMod.assert_called_with('testmod', 'data')

    def test_update_builds_tx(self, mock_evm):
        mock_fn = MagicMock()
        mock_evm._contract.functions.updateMod.return_value = mock_fn
        mock_fn.build_transaction.return_value = {'to': '0x', 'data': '0x'}
        assert mock_evm.update('5', 'new_data') is True
        mock_evm._contract.functions.updateMod.assert_called_with(5, 'new_data')

    def test_remove_builds_tx(self, mock_evm):
        mock_fn = MagicMock()
        mock_evm._contract.functions.removeMod.return_value = mock_fn
        mock_fn.build_transaction.return_value = {'to': '0x', 'data': '0x'}
        assert mock_evm.remove('3') is True
        mock_evm._contract.functions.removeMod.assert_called_with(3)

    def test_list_all(self, mock_evm):
        mock_evm._contract.functions.nextModId.return_value.call.return_value = 3
        mock_evm._contract.functions.getMod.return_value.call.side_effect = [
            ('0x' + 'c' * 40, 'mod1', 'data1'),
            ('0x' + 'c' * 40, 'mod2', 'data2'),
        ]
        mods = mock_evm.list_all()
        assert len(mods) == 2
