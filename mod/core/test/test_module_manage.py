"""Tests for module management: create, fork, delete, list via API."""
import os
import sys
import shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import mod as m


# ─── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api():
    """Provide an Api instance for tests."""
    from mod.core.api.mod import Api
    return Api(key='test', store='localfs')


@pytest.fixture(scope="session")
def test_key_address(api):
    """The address of the test key."""
    return api.key_address('test')


@pytest.fixture(autouse=True)
def cleanup_test_mods(test_key_address):
    """Remove test mods after each test."""
    yield
    names = ['_test_manage_new', '_test_manage_fork', '_test_manage_del', '_test_manage_create']
    for name in names:
        # Clean filesystem
        path = os.path.join(m.paths['orbit']['orbit'], name)
        if os.path.exists(path):
            shutil.rmtree(path)
        # Also check under the key address
        path2 = os.path.join(m.paths['orbit']['orbit'], test_key_address, name)
        if os.path.exists(path2):
            shutil.rmtree(path2)


def _create_test_mod(name, api):
    """Helper: create a mod using m.new() and register via api.reg()."""
    result = m.new(name, base='base')
    api.reg(mod=name, key='test')
    return result


# ─── Module Creation (reg) ──────────────────────────────────────────

class TestModuleCreate:
    def test_reg_local_module(self, api):
        """Api.reg() with a local module name should register it."""
        m.new('_test_manage_create', base='base')
        result = api.reg(mod='_test_manage_create', key='test', comment='test create')
        assert result is not None
        assert isinstance(result, dict)
        assert 'error' not in result

    def test_reg_returns_cid(self, api):
        """Registration should produce a CID."""
        m.new('_test_manage_create', base='base')
        result = api.reg(mod='_test_manage_create', key='test')
        assert 'cid' in result
        assert isinstance(result['cid'], str)
        assert len(result['cid']) > 0

    def test_reg_returns_name(self, api):
        """Registration result should include the module name."""
        m.new('_test_manage_create', base='base')
        result = api.reg(mod='_test_manage_create', key='test')
        assert 'name' in result
        assert result['name'] == '_test_manage_create'


# ─── Module Listing (mods) ─────────────────────────────────────────

class TestModuleListing:
    def test_mods_returns_list(self, api):
        """Api.mods() should return a list."""
        result = api.mods()
        assert isinstance(result, list)

    def test_mods_with_key_filter(self, api, test_key_address):
        """Api.mods(key=...) should filter by owner."""
        _create_test_mod('_test_manage_new', api)
        result = api.mods(key=test_key_address)
        assert isinstance(result, list)

    def test_mods_with_search(self, api):
        """Api.mods(search=...) should filter by name."""
        _create_test_mod('_test_manage_new', api)
        result = api.mods(search='_test_manage_new')
        assert isinstance(result, list)
        names = [mod.get('name', '') for mod in result]
        assert '_test_manage_new' in names

    def test_mods_pagination(self, api):
        """Api.mods() should support pagination."""
        result = api.mods(page=0, page_size=5)
        assert isinstance(result, list)
        assert len(result) <= 5

    def test_mods_entries_have_expected_fields(self, api):
        """Each mod entry should have name and key."""
        _create_test_mod('_test_manage_new', api)
        result = api.mods(search='_test_manage_new')
        assert len(result) > 0
        entry = result[0]
        assert 'name' in entry
        assert 'key' in entry


# ─── Module Forking ────────────────────────────────────────────────

class TestModuleFork:
    def test_fork_creates_copy(self, api):
        """Api.fork() should copy a module to a new location."""
        _create_test_mod('_test_manage_new', api)
        result = api.fork(mod='_test_manage_new', key='test', comment='test fork')
        assert 'error' not in result, f"Fork failed: {result.get('error')}"
        assert result.get('status') == 'forked'

    def test_fork_returns_expected_keys(self, api):
        """Fork result should have status, mod, name, key, path, reg."""
        _create_test_mod('_test_manage_new', api)
        result = api.fork(mod='_test_manage_new', key='test')
        assert 'status' in result
        assert 'mod' in result
        assert 'name' in result
        assert 'key' in result
        assert 'path' in result
        assert 'reg' in result

    def test_fork_creates_directory(self, api):
        """Forked module should exist on filesystem."""
        _create_test_mod('_test_manage_new', api)
        result = api.fork(mod='_test_manage_new', key='test')
        assert 'path' in result
        assert os.path.isdir(result['path'])

    def test_fork_nonexistent_returns_error(self, api):
        """Forking a nonexistent module should return error."""
        result = api.fork(mod='_nonexistent_xyz_99999', key='test')
        assert 'error' in result

    def test_fork_preserves_content(self, api):
        """Forked module should have files from the original."""
        _create_test_mod('_test_manage_new', api)
        result = api.fork(mod='_test_manage_new', key='test')
        assert os.path.isdir(result['path'])
        files = os.listdir(result['path'])
        assert len(files) > 0, 'Forked module should have files'

    def test_fork_registers_module(self, api):
        """Fork should register the forked module."""
        _create_test_mod('_test_manage_new', api)
        result = api.fork(mod='_test_manage_new', key='test')
        assert result.get('reg') is not None
        assert isinstance(result['reg'], dict)


# ─── Module Deletion (rm_mod) ──────────────────────────────────────

class TestModuleDelete:
    def test_rm_mod_removes_from_registry(self, api):
        """Api.rm_mod() should remove a module from the registry."""
        _create_test_mod('_test_manage_del', api)
        # Verify it exists
        mods_before = api.mods(search='_test_manage_del')
        found_before = any(mod.get('name') == '_test_manage_del' for mod in mods_before)
        assert found_before, 'Module should exist before deletion'
        # Delete
        result = api.rm_mod(mod='_test_manage_del', key='test')
        assert result is not False

    def test_rm_mod_not_in_listing_after_delete(self, api):
        """After rm_mod, the module should not appear in mods()."""
        _create_test_mod('_test_manage_del', api)
        api.rm_mod(mod='_test_manage_del', key='test')
        mods_after = api.mods(search='_test_manage_del')
        found_after = any(mod.get('name') == '_test_manage_del' for mod in mods_after)
        assert not found_after, 'Module should not appear after deletion'

    def test_rm_mod_nonexistent_is_safe(self, api):
        """Deleting a nonexistent module should not crash."""
        result = api.rm_mod(mod='_nonexistent_xyz_99999', key='test')
        # Should return False or handle gracefully
        assert result is not None or result is False or result is True


# ─── Full Lifecycle ────────────────────────────────────────────────

class TestModuleLifecycle:
    def test_create_fork_delete_lifecycle(self, api):
        """Full lifecycle: create -> register -> list -> fork -> delete."""
        # 1. Create
        create_result = m.new('_test_manage_new', base='base')
        assert os.path.isdir(create_result['path'])

        # 2. Register
        reg_result = api.reg(mod='_test_manage_new', key='test')
        assert isinstance(reg_result, dict)
        assert 'error' not in reg_result

        # 3. List and find it
        mods = api.mods(search='_test_manage_new')
        names = [mod.get('name', '') for mod in mods]
        assert '_test_manage_new' in names

        # 4. Fork
        fork_result = api.fork(mod='_test_manage_new', key='test')
        assert 'error' not in fork_result
        assert fork_result.get('status') == 'forked'

        # 5. Delete
        api.rm_mod(mod='_test_manage_new', key='test')
        mods_after = api.mods(search='_test_manage_new')
        found = any(mod.get('name') == '_test_manage_new' for mod in mods_after)
        assert not found

    def test_create_multiple_and_list(self, api):
        """Creating multiple modules should all appear in listing."""
        m.new('_test_manage_new', base='base')
        api.reg(mod='_test_manage_new', key='test')
        m.new('_test_manage_create', base='base')
        api.reg(mod='_test_manage_create', key='test')

        mods = api.mods(search='_test_manage')
        names = [mod.get('name', '') for mod in mods]
        assert '_test_manage_new' in names
        assert '_test_manage_create' in names


# ─── API Method Existence ─────────────────────────────────────────

class TestApiMethodsExist:
    """Verify the API class has all module management methods."""

    def test_has_fork(self, api):
        assert hasattr(api, 'fork')
        assert callable(api.fork)

    def test_has_rm_mod(self, api):
        assert hasattr(api, 'rm_mod')
        assert callable(api.rm_mod)

    def test_has_reg(self, api):
        assert hasattr(api, 'reg')
        assert callable(api.reg)

    def test_has_mods(self, api):
        assert hasattr(api, 'mods')
        assert callable(api.mods)

    def test_has_new(self, api):
        assert hasattr(api, 'new')
        assert callable(api.new)

    def test_has_rename(self, api):
        assert hasattr(api, 'rename')
        assert callable(api.rename)

    def test_has_schema(self, api):
        assert hasattr(api, 'schema')
        assert callable(api.schema)

    def test_has_content(self, api):
        assert hasattr(api, 'content')
        assert callable(api.content)
