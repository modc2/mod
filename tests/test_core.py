"""Tests for core Mod operations: new, fork, token identity, tx saving."""
import os
import sys
import json
import time
import shutil
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import mod as m


# ─── Helpers ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_test_mods():
    """Remove test mods after each test."""
    yield
    for name in ['_test_core_new', '_test_core_fork', '_test_core_overwrite']:
        path = os.path.join(m.paths['orbit']['orbit'], name)
        if os.path.exists(path):
            shutil.rmtree(path)


# ─── Module Creation (new) ───────────────────────────────────────────

class TestNew:
    def test_new_creates_mod(self):
        result = m.new('_test_core_new', base='base')
        assert result['name'] == '_test_core_new'
        assert os.path.isdir(result['path'])
        assert m.mod_exists('_test_core_new')

    def test_new_has_content(self):
        result = m.new('_test_core_new', base='base')
        files = m.files(result['path'])
        assert len(files) > 0, 'New mod should have files copied from base'

    def test_new_overwrites_existing(self):
        m.new('_test_core_overwrite', base='base')
        result = m.new('_test_core_overwrite', base='base')
        assert result['msg'] == 'Mod Created'
        assert os.path.isdir(result['path'])

    def test_new_returns_expected_keys(self):
        result = m.new('_test_core_new', base='base')
        assert 'name' in result
        assert 'path' in result
        assert 'cid' in result
        assert 'base' in result
        assert 'msg' in result


# ─── Forking ─────────────────────────────────────────────────────────

class TestFork:
    def test_fork_alias_works(self):
        """fork is an alias for new."""
        assert m.Mod.fork is m.Mod.new

    def test_create_alias_works(self):
        assert m.Mod.create is m.Mod.new


# ─── Token Identity ──────────────────────────────────────────────────

class TestTokenIdentity:
    @pytest.fixture
    def auth(self):
        from mod.core.server.auth.auth.auth import Auth
        return Auth(key='test.core_tests', max_age=1)

    def test_token_generation(self, auth):
        token = auth.token(data={'fn': 'test', 'params': {'a': 1}})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_has_fields(self, auth):
        token = auth.token(data={'fn': 'test'}, mod='dict')
        assert 'data' in token
        assert 'time' in token
        assert 'key' in token
        assert 'signature' in token

    def test_token_verify_valid(self, auth):
        """Token should verify within the 1s window."""
        token = auth.token(data={'fn': 'test'})
        result = auth.verify(token)
        assert result is not None
        assert 'data' in result
        assert 'key' in result

    def test_token_verify_expired(self, auth):
        """Token should fail verification after 1s (replay protection)."""
        token = auth.token(data={'fn': 'test'})
        time.sleep(1.1)
        with pytest.raises(AssertionError, match='stale'):
            auth.verify(token)

    def test_token_verify_tampered(self, auth):
        """Tampered token should fail verification."""
        import base64
        token = auth.token(data={'fn': 'test'})
        # Decode, modify data, re-encode
        padding = b'=' * (4 - (len(token) % 4))
        raw = json.loads(base64.urlsafe_b64decode(token.encode() + padding))
        raw['data'] = {'fn': 'hacked'}
        tampered = base64.urlsafe_b64encode(
            json.dumps(raw, separators=(',', ':')).encode()
        ).rstrip(b'=').decode()
        with pytest.raises(AssertionError):
            auth.verify(tampered)


# ─── TX Saving ───────────────────────────────────────────────────────

class TestTxSaving:
    @pytest.fixture
    def tx_store(self, tmp_path):
        from mod.core.tx import Tx
        return Tx(storage_path=str(tmp_path / 'txs'))

    def test_save_tx(self, tx_store):
        result = tx_store.save(
            fn='test_fn',
            params={'args': [], 'kwargs': {'x': 1}},
            result={'success': True},
            client='0xABC',
            token='tok123',
        )
        assert 'hash' in result
        assert 'path' in result
        assert os.path.exists(result['path'])

    def test_tx_has_all_fields(self, tx_store):
        tx_store.save(
            fn='test_fn',
            params={'args': [], 'kwargs': {}},
            result={'ok': True},
            client='0xDEF',
            token='tok456',
        )
        txs = tx_store.list()
        assert len(txs) == 1
        tx = txs[0]
        assert tx['fn'] == 'test_fn'
        assert tx['client'] == '0xDEF'
        assert tx['token'] == 'tok456'
        assert 'timestamp' in tx
        assert 'params' in tx
        assert 'result' in tx

    def test_tx_get_by_hash(self, tx_store):
        result = tx_store.save(
            fn='lookup_fn', params={}, result='ok', client='0x1', token='t',
        )
        tx = tx_store.get(result['hash'])
        assert tx is not None
        assert tx['fn'] == 'lookup_fn'

    def test_tx_list_search(self, tx_store):
        tx_store.save(fn='alpha_fn', params={}, result='a', client='c', token='t')
        tx_store.save(fn='beta_fn', params={}, result='b', client='c', token='t')
        txs = tx_store.list(search='alpha')
        assert len(txs) == 1
        assert txs[0]['fn'] == 'alpha_fn'

    def test_tx_nonexistent_hash(self, tx_store):
        assert tx_store.get('nonexistent') is None

    def test_tx_result_not_serializable(self, tx_store):
        """Non-serializable results should be str'd."""
        result = tx_store.save(
            fn='obj_fn', params={}, result=object(), client='c', token='t',
        )
        tx = tx_store.get(result['hash'])
        assert isinstance(tx['result'], str)


# ─── Store Config ───────────────────────────────────────────────────

class TestStoreConfig:
    """Verify registry, API, and router read store from config."""

    def test_config_has_store_field(self):
        config = m.config('api')
        assert 'store' in config, "api config should have a 'store' field"
        assert config['store'] == 'localfs'

    def test_registry_defaults_to_config_store(self):
        from mod.core.registry.registry import Registry
        reg = Registry()
        # store should be a localfs instance
        assert hasattr(reg.store, 'put')
        assert hasattr(reg.store, 'get')
        assert 'localfs' in reg.store.__class__.__module__

    def test_registry_accepts_store_override(self):
        from mod.core.registry.registry import Registry
        reg = Registry(store='localfs')
        assert 'localfs' in reg.store.__class__.__module__

    def test_localfs_put_get_roundtrip(self):
        store = m.mod('localfs')()
        data = {'test': 'store_config', 'ts': m.time()}
        cid = store.put(data)
        assert cid.startswith('Qm') or cid.startswith('bafy')
        retrieved = store.get(cid)
        assert retrieved['test'] == 'store_config'
        store.rm(cid)

    def test_localfs_content_map(self):
        """Content should be storable as a path->CID map."""
        store = m.mod('localfs')()
        file_a = store.put('print("hello")')
        file_b = store.put('{"name": "test"}')
        content_map = {'mod.py': file_a, 'config.json': file_b}
        map_cid = store.put(content_map)
        retrieved = store.get(map_cid)
        assert retrieved['mod.py'] == file_a
        assert retrieved['config.json'] == file_b
        # cleanup
        for cid in [file_a, file_b, map_cid]:
            store.rm(cid)


# ─── Mixin Structure ────────────────────────────────────────────────

class TestModStructure:
    """Verify Mod class has all expected methods (formerly from mixins)."""

    def test_mod_has_fs_methods(self):
        assert hasattr(m.Mod, 'abspath')
        assert hasattr(m.Mod, 'files')
        assert hasattr(m.Mod, 'dirpath')
        assert hasattr(m.Mod, 'filepath')

    def test_mod_has_resolve_methods(self):
        assert hasattr(m.Mod, 'mod')
        assert hasattr(m.Mod, 'fn')
        assert hasattr(m.Mod, 'dirpath')
        assert hasattr(m.Mod, 'filepath')

    def test_mod_has_introspect_methods(self):
        assert hasattr(m.Mod, 'code')
        assert hasattr(m.Mod, 'content')
        assert hasattr(m.Mod, 'schema')
        assert hasattr(m.Mod, 'fns')

    def test_mod_has_deploy_methods(self):
        assert hasattr(m.Mod, 'serve')
        assert hasattr(m.Mod, 'kill')

    def test_mod_has_factory_methods(self):
        assert hasattr(m.Mod, 'new')
        assert hasattr(m.Mod, 'fork')
        assert hasattr(m.Mod, 'create')
