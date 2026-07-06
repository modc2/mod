"""Integration tests for the unified Mod interface."""

import os
import sys
import shutil
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mod import Mod, parse_data_uri, make_data_uri


# --- Mock storage client ---

class MockStorageClient:
    """Fake IPFS/Lighthouse client that stores JSON in memory."""

    def __init__(self):
        self._store = {}
        self._counter = 0

    def put(self, data: dict) -> str:
        self._counter += 1
        cid = f'Qm{self._counter:06d}'
        import json
        self._store[cid] = json.dumps(data) if isinstance(data, dict) else data
        return cid

    def get(self, cid: str):
        import json
        raw = self._store.get(cid)
        if raw is None:
            raise ValueError(f'CID not found: {cid}')
        return json.loads(raw)


# --- Mock auth ---

class MockAuth:
    """Fake oath Auth that always verifies (or can be configured to fail)."""

    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.last_verified = None

    def verify(self, headers, data=None):
        self.last_verified = headers
        if self.should_fail:
            raise AssertionError('Mock auth failure')
        return True

    def forward(self, data, key=None, cost=0, ttl=None):
        return {
            'data': 'mock_hash',
            'time': '1234567890',
            'cost': str(cost),
            'key': 'mock_signer_address',
            'nonce': 'mock_nonce',
            'expiry': '9999999999',
            'signature': 'mock_sig',
        }


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix='registry_mod_test_')
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mock_storage():
    return MockStorageClient()


@pytest.fixture
def reg(tmp_dir, mock_storage):
    """Mod instance using offchain backend with temp storage and mock IPFS."""
    m = Mod(backend='offchain', storage='ipfs')
    from registry.offchain import OffchainRegistry
    m._backends['offchain'] = OffchainRegistry(storage_path=tmp_dir)
    # Inject mock storage for all providers
    m._storage_clients['ipfs'] = mock_storage
    m._storage_clients['lighthouse'] = mock_storage
    m._storage_clients['filecoin'] = mock_storage
    return m


@pytest.fixture
def reg_auth(tmp_dir, mock_storage):
    """Mod instance with require_auth=True and mock auth."""
    mock_auth = MockAuth()
    m = Mod(backend='offchain', storage='ipfs', require_auth=True, auth=mock_auth)
    from registry.offchain import OffchainRegistry
    m._backends['offchain'] = OffchainRegistry(storage_path=tmp_dir)
    m._storage_clients['ipfs'] = mock_storage
    m._storage_clients['lighthouse'] = mock_storage
    m._storage_clients['filecoin'] = mock_storage
    return m, mock_auth


class TestDataUri:
    def test_parse_ipfs(self):
        provider, cid = parse_data_uri('ipfs/QmABC123')
        assert provider == 'ipfs'
        assert cid == 'QmABC123'

    def test_parse_lighthouse(self):
        provider, cid = parse_data_uri('lighthouse/QmXYZ789')
        assert provider == 'lighthouse'
        assert cid == 'QmXYZ789'

    def test_parse_filecoin(self):
        provider, cid = parse_data_uri('filecoin/bafy123')
        assert provider == 'filecoin'
        assert cid == 'bafy123'

    def test_parse_unknown(self):
        provider, raw = parse_data_uri('some-random-string')
        assert provider is None
        assert raw == 'some-random-string'

    def test_make_data_uri(self):
        assert make_data_uri('ipfs', 'QmABC') == 'ipfs/QmABC'
        assert make_data_uri('lighthouse', 'QmXYZ') == 'lighthouse/QmXYZ'

    def test_make_data_uri_invalid_provider(self):
        with pytest.raises(ValueError, match='Unknown storage provider'):
            make_data_uri('s3', 'abc')


class TestModInterface:
    def test_register_with_dict(self, reg):
        mod_id = reg.register('testmod', {'version': '1.0'}, owner='alice')
        assert mod_id == '1'
        mod = reg.get(mod_id)
        assert mod['data'].startswith('ipfs/')

    def test_register_with_json_string(self, reg):
        mod_id = reg.register('testmod', '{"version": "2.0"}', owner='alice')
        assert mod_id == '1'
        mod = reg.get(mod_id)
        assert mod['data'].startswith('ipfs/')

    def test_register_with_pre_formatted_uri(self, reg):
        mod_id = reg.register('testmod', 'ipfs/QmExisting', owner='alice')
        assert mod_id == '1'
        mod = reg.get(mod_id)
        assert mod['data'] == 'ipfs/QmExisting'

    def test_register_invalid_data_fails(self, reg):
        with pytest.raises(ValueError):
            reg.register('testmod', 'not-json-and-not-uri', owner='alice')

    def test_forward_register(self, reg):
        mod_id = reg.forward(name='testmod', data={'v': 1}, owner='alice')
        assert mod_id == '1'

    def test_forward_no_args_lists(self, reg):
        reg.register('mod1', {'a': 1}, owner='alice')
        result = reg.forward()
        assert len(result) == 1

    def test_get(self, reg):
        mod_id = reg.register('testmod', {'desc': 'hello'}, owner='alice')
        mod = reg.get(mod_id)
        assert mod['name'] == 'testmod'
        assert mod['data'].startswith('ipfs/')

    def test_get_resolve(self, reg, mock_storage):
        mod_id = reg.register('testmod', {'desc': 'hello'}, owner='alice')
        mod = reg.get(mod_id, resolve=True)
        assert mod['resolved'] == {'desc': 'hello'}

    def test_update(self, reg):
        mod_id = reg.register('testmod', {'v': 1}, owner='alice')
        reg.update(mod_id, {'v': 2}, owner='alice')
        mod = reg.get(mod_id)
        assert mod['data'].startswith('ipfs/')

    def test_update_with_uri(self, reg):
        mod_id = reg.register('testmod', {'v': 1}, owner='alice')
        reg.update(mod_id, 'lighthouse/QmNew', owner='alice')
        mod = reg.get(mod_id)
        assert mod['data'] == 'lighthouse/QmNew'

    def test_remove(self, reg):
        mod_id = reg.register('testmod', {'v': 1}, owner='alice')
        reg.remove(mod_id, owner='alice')
        assert reg.get(mod_id) is None

    def test_list_owner(self, reg):
        reg.register('mod1', {'a': 1}, owner='alice')
        reg.register('mod2', {'b': 2}, owner='bob')
        assert len(reg.list(owner='alice')) == 1

    def test_list_all(self, reg):
        reg.register('mod1', {'a': 1}, owner='alice')
        reg.register('mod2', {'b': 2}, owner='bob')
        assert len(reg.list_all()) == 2

    def test_transfer(self, reg):
        mod_id = reg.register('testmod', {'v': 1}, owner='alice')
        reg.transfer(mod_id, 'bob', owner='alice')
        assert reg.get(mod_id)['owner'] == 'bob'

    def test_is_name_taken(self, reg):
        reg.register('testmod', {'v': 1}, owner='alice')
        assert reg.is_name_taken('alice', 'testmod')
        assert not reg.is_name_taken('bob', 'testmod')

    def test_backends_list(self, reg):
        backends = reg.backends()
        assert 'offchain' in backends
        assert 'evm' in backends
        assert 'solana' in backends
        assert 'near' in backends

    def test_storage_providers_list(self, reg):
        providers = reg.storage_providers()
        assert 'ipfs' in providers
        assert 'lighthouse' in providers
        assert 'filecoin' in providers


class TestStorageProviders:
    def test_register_with_lighthouse(self, reg):
        mod_id = reg.register('testmod', {'v': 1}, owner='alice', storage='lighthouse')
        mod = reg.get(mod_id)
        assert mod['data'].startswith('lighthouse/')

    def test_register_with_filecoin(self, reg):
        mod_id = reg.register('testmod', {'v': 1}, owner='alice', storage='filecoin')
        mod = reg.get(mod_id)
        assert mod['data'].startswith('filecoin/')

    def test_forward_with_storage(self, reg):
        mod_id = reg.forward(name='testmod', data={'v': 1}, owner='alice', storage='lighthouse')
        mod = reg.get(mod_id)
        assert mod['data'].startswith('lighthouse/')

    def test_resolve_ipfs(self, reg, mock_storage):
        mod_id = reg.register('testmod', {'hello': 'world'}, owner='alice', storage='ipfs')
        mod = reg.get(mod_id)
        resolved = reg.resolve(mod['data'])
        assert resolved == {'hello': 'world'}

    def test_resolve_lighthouse(self, reg, mock_storage):
        mod_id = reg.register('testmod', {'hello': 'lh'}, owner='alice', storage='lighthouse')
        mod = reg.get(mod_id)
        resolved = reg.resolve(mod['data'])
        assert resolved == {'hello': 'lh'}

    def test_resolve_invalid_uri(self, reg):
        with pytest.raises(ValueError, match='Invalid data URI'):
            reg.resolve('no-prefix-here')


class TestAuth:
    def test_register_requires_auth_headers(self, reg_auth):
        reg, mock_auth = reg_auth
        with pytest.raises(PermissionError, match='Auth headers required'):
            reg.register('testmod', {'v': 1})

    def test_register_with_valid_auth(self, reg_auth):
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({'name': 'testmod', 'action': 'register'})
        mod_id = reg.register('testmod', {'v': 1}, headers=headers)
        assert mod_id == '1'
        # Owner should be set from auth signer
        mod = reg.get(mod_id)
        assert mod['owner'] == 'mock_signer_address'

    def test_register_with_explicit_owner_overrides_auth(self, reg_auth):
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({})
        mod_id = reg.register('testmod', {'v': 1}, headers=headers, owner='alice')
        mod = reg.get(mod_id)
        assert mod['owner'] == 'alice'

    def test_register_with_failed_auth(self, tmp_dir, mock_storage):
        mock_auth = MockAuth(should_fail=True)
        reg = Mod(backend='offchain', storage='ipfs', require_auth=True, auth=mock_auth)
        from registry.offchain import OffchainRegistry
        reg._backends['offchain'] = OffchainRegistry(storage_path=tmp_dir)
        reg._storage_clients['ipfs'] = mock_storage

        headers = {'key': 'bad', 'signature': 'bad', 'time': '0', 'data': 'x'}
        with pytest.raises(PermissionError, match='Auth verification failed'):
            reg.register('testmod', {'v': 1}, headers=headers)

    def test_update_requires_auth(self, reg_auth):
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({})
        mod_id = reg.register('testmod', {'v': 1}, headers=headers)
        with pytest.raises(PermissionError, match='Auth headers required'):
            reg.update(mod_id, {'v': 2})

    def test_update_with_auth(self, reg_auth):
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({})
        mod_id = reg.register('testmod', {'v': 1}, headers=headers)
        reg.update(mod_id, {'v': 2}, headers=headers)
        mod = reg.get(mod_id, resolve=False)
        assert mod['data'].startswith('ipfs/')

    def test_remove_requires_auth(self, reg_auth):
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({})
        mod_id = reg.register('testmod', {'v': 1}, headers=headers)
        with pytest.raises(PermissionError, match='Auth headers required'):
            reg.remove(mod_id)

    def test_remove_with_auth(self, reg_auth):
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({})
        mod_id = reg.register('testmod', {'v': 1}, headers=headers)
        reg.remove(mod_id, headers=headers)
        assert reg.get(mod_id) is None

    def test_transfer_requires_auth(self, reg_auth):
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({})
        mod_id = reg.register('testmod', {'v': 1}, headers=headers)
        with pytest.raises(PermissionError, match='Auth headers required'):
            reg.transfer(mod_id, 'bob')

    def test_transfer_with_auth(self, reg_auth):
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({})
        mod_id = reg.register('testmod', {'v': 1}, headers=headers)
        reg.transfer(mod_id, 'bob', headers=headers)
        assert reg.get(mod_id)['owner'] == 'bob'

    def test_no_auth_when_not_required(self, reg):
        """When require_auth=False, no headers needed."""
        mod_id = reg.register('testmod', {'v': 1}, owner='alice')
        assert mod_id == '1'

    def test_list_does_not_require_auth(self, reg_auth):
        """List/get operations don't need auth."""
        reg, mock_auth = reg_auth
        headers = mock_auth.forward({})
        reg.register('testmod', {'v': 1}, headers=headers)
        # These should work without auth
        assert len(reg.list_all()) == 1
        assert reg.get('1') is not None


class TestSync:
    def test_sync_offchain_to_offchain(self, tmp_dir, mock_storage):
        """Test sync between two offchain backends (simulates cross-backend sync)."""
        m = Mod(backend='offchain', storage='ipfs')
        m._storage_clients['ipfs'] = mock_storage
        from registry.offchain import OffchainRegistry
        src = OffchainRegistry(storage_path=os.path.join(tmp_dir, 'src'))
        dst = OffchainRegistry(storage_path=os.path.join(tmp_dir, 'dst'))
        m._backends['offchain'] = src
        m._backends['offchain_dst'] = dst

        src.register('mod1', 'ipfs/Qm1', owner='alice')
        src.register('mod2', 'ipfs/Qm2', owner='alice')

        mods = src.list_all()
        synced = []
        for mod in mods:
            if not dst.is_name_taken(mod['owner'], mod['name']):
                new_id = dst.register(mod['name'], mod['data'], owner=mod['owner'])
                synced.append(new_id)

        assert len(synced) == 2
        assert len(dst.list_all()) == 2


class TestCRUDCycle:
    def test_full_lifecycle(self, reg, mock_storage):
        """Register -> get -> resolve -> update -> list -> transfer -> remove."""
        # Register
        mod_id = reg.register('lifecycle_mod', {'v': 1}, owner='alice')
        assert mod_id

        # Get
        mod = reg.get(mod_id)
        assert mod['data'].startswith('ipfs/')

        # Resolve
        resolved = reg.resolve(mod['data'])
        assert resolved == {'v': 1}

        # Update
        reg.update(mod_id, {'v': 2}, owner='alice')
        mod = reg.get(mod_id, resolve=True)
        assert mod['resolved'] == {'v': 2}

        # List
        assert len(reg.list(owner='alice')) == 1

        # Transfer
        reg.transfer(mod_id, 'bob', owner='alice')
        assert reg.get(mod_id)['owner'] == 'bob'
        assert len(reg.list(owner='alice')) == 0
        assert len(reg.list(owner='bob')) == 1

        # Remove
        reg.remove(mod_id, owner='bob')
        assert reg.get(mod_id) is None
        assert len(reg.list_all()) == 0

    def test_full_lifecycle_with_auth(self, reg_auth, mock_storage):
        """Full CRUD cycle with auth required."""
        reg, mock_auth = reg_auth
        reg._storage_clients['ipfs'] = mock_storage
        headers = mock_auth.forward({})

        # Register
        mod_id = reg.register('lifecycle_mod', {'v': 1}, headers=headers)
        assert mod_id

        # Get (no auth needed)
        mod = reg.get(mod_id)
        assert mod['data'].startswith('ipfs/')
        assert mod['owner'] == 'mock_signer_address'

        # Update
        reg.update(mod_id, {'v': 2}, headers=headers)

        # Transfer
        reg.transfer(mod_id, 'bob', headers=headers)
        assert reg.get(mod_id)['owner'] == 'bob'

        # Remove
        reg.remove(mod_id, headers=headers, owner='bob')
        assert reg.get(mod_id) is None
