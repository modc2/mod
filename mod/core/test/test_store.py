"""Tests for mod/core/store/src/mod.py — Store class (filesystem operations only)."""
import os
import sys
import json
import tempfile
import shutil
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mod.core.store.src.mod import Store


# ─── Helpers ──────────────────────────────────────────────────────────

class MockStore:
    """A lightweight Store mock that avoids key/encryption init."""

    def __new__(cls, path):
        obj = object.__new__(Store)
        obj.path = os.path.abspath(path)
        obj.filetype = 'json'
        obj.private = False
        os.makedirs(obj.path, exist_ok=True)
        return obj


@pytest.fixture
def store(tmp_dir):
    return MockStore(os.path.join(tmp_dir, 'store'))


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix='mod_store_test_')
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ─── set_filetype ────────────────────────────────────────────────────

class TestSetFiletype:
    def test_json(self, store):
        assert store.set_filetype('json') == 'json'

    def test_yaml(self, store):
        assert store.set_filetype('yaml') == 'yaml'

    def test_invalid(self, store):
        with pytest.raises(AssertionError):
            store.set_filetype('txt')


# ─── get_path ────────────────────────────────────────────────────────

class TestStorGetPath:
    def test_relative_key(self, store):
        result = store.get_path('mykey', filetype='json')
        assert result.startswith(store.path)
        assert result.endswith('.json')

    def test_absolute_path(self, store):
        result = store.get_path('/tmp/test', filetype='json')
        assert result == '/tmp/test.json'

    def test_no_filetype(self, store):
        result = store.get_path('mykey')
        assert result == os.path.join(store.path, 'mykey')

    def test_already_has_extension(self, store):
        result = store.get_path('mykey.json', filetype='json')
        assert result.endswith('.json')
        assert not result.endswith('.json.json')

    def test_tilde_path(self, store):
        result = store.get_path('~/test', filetype='json')
        assert result.startswith('/')
        assert 'test.json' in result


# ─── abspath ─────────────────────────────────────────────────────────

class TestStoreAbspath:
    def test_tilde(self, store):
        result = store.abspath('~/foo')
        assert result == os.path.expanduser('~/foo')

    def test_relative(self, store):
        result = store.abspath('./foo')
        assert os.path.isabs(result)


# ─── ensure_path ─────────────────────────────────────────────────────

class TestStoreEnsurePath:
    def test_creates_dirs(self, store, tmp_dir):
        path = os.path.join(tmp_dir, 'new', 'nested', 'file.json')
        store.ensure_path(path)
        assert os.path.isdir(os.path.join(tmp_dir, 'new', 'nested'))

    def test_existing_dir(self, store, tmp_dir):
        path = os.path.join(tmp_dir, 'file.json')
        result = store.ensure_path(path)
        assert 'path' in result


# ─── put_json / get_json ─────────────────────────────────────────────

class TestStorePutGetJson:
    def test_roundtrip(self, store):
        data = {'name': 'test', 'value': 42, 'nested': {'a': [1, 2]}}
        store.put_json('testkey', data)
        result = store.get_json('testkey')
        assert result == data

    def test_overwrite(self, store):
        store.put_json('overwrite', {'first': True})
        store.put_json('overwrite', {'second': True})
        result = store.get_json('overwrite')
        assert result == {'second': True}

    def test_nested_key(self, store):
        store.put_json('sub/nested/key', {'deep': True})
        result = store.get_json('sub/nested/key')
        assert result == {'deep': True}


# ─── put / get ───────────────────────────────────────────────────────

class TestStorePutGet:
    def test_basic_put_get(self, store):
        store.put('item1', {'hello': 'world'})
        result = store.get('item1')
        assert result == {'hello': 'world'}

    def test_default_on_missing(self, store):
        result = store.get('nonexistent', default='fallback')
        assert result == 'fallback'

    def test_max_age(self, store):
        store.put('aged', {'data': 'old'})
        # With max_age=0, should return default because file is already > 0 seconds old
        time.sleep(0.05)
        result = store.get('aged', default='expired', max_age=0)
        assert result == 'expired'


# ─── validate_data ───────────────────────────────────────────────────

class TestValidateData:
    def test_unwraps_data_time(self, store):
        wrapped = {'data': {'actual': True}, 'time': 123}
        result = store.validate_data(wrapped)
        assert result == {'actual': True}

    def test_unwraps_data_timestamp(self, store):
        wrapped = {'data': [1, 2, 3], 'timestamp': 123}
        result = store.validate_data(wrapped)
        assert result == [1, 2, 3]

    def test_passthrough_normal_dict(self, store):
        data = {'a': 1, 'b': 2}
        result = store.validate_data(data)
        assert result == data

    def test_passthrough_list(self, store):
        data = [1, 2, 3]
        result = store.validate_data(data)
        assert result == data


# ─── rm ──────────────────────────────────────────────────────────────

class TestStoreRm:
    def test_remove_item(self, store):
        store.put_json('to_delete', {'delete': True})
        path = store.rm('to_delete')
        assert not os.path.exists(path)

    def test_nonexistent_raises(self, store):
        with pytest.raises(AssertionError):
            store.rm('nonexistent_item_xyz')

    def test_outside_path_raises(self, store):
        # Create a file outside the store path
        with pytest.raises(AssertionError):
            store.rm('/tmp/outside_store.json')


# ─── keys ────────────────────────────────────────────────────────────

class TestStoreKeys:
    def test_list_keys(self, store):
        store.put_json('key1', {'a': 1})
        store.put_json('key2', {'b': 2})
        keys = store.keys()
        assert 'key1' in keys
        assert 'key2' in keys

    def test_empty(self, store):
        keys = store.keys()
        assert keys == []

    def test_search(self, store):
        store.put_json('alpha_item', {'a': 1})
        store.put_json('beta_item', {'b': 2})
        keys = store.keys(search='alpha')
        assert len(keys) == 1
        assert 'alpha' in keys[0]


# ─── paths ───────────────────────────────────────────────────────────

class TestStorePaths:
    def test_lists_files(self, store):
        store.put_json('p1', {'x': 1})
        store.put_json('p2', {'y': 2})
        paths = store.paths()
        assert len(paths) >= 2
        for p in paths:
            assert os.path.isfile(p)

    def test_search_filter(self, store):
        store.put_json('match_this', {'a': 1})
        store.put_json('not_this', {'b': 2})
        paths = store.paths(search='match')
        assert all('match' in p for p in paths)

    def test_avoid_filter(self, store):
        store.put_json('good_item', {'a': 1})
        store.put_json('bad_item', {'b': 2})
        paths = store.paths(avoid='bad')
        assert all('bad' not in p for p in paths)


# ─── exists ──────────────────────────────────────────────────────────

class TestStoreExists:
    def test_exists(self, store):
        store.put_json('exists_test', {'a': 1})
        assert store.exists('exists_test') is True

    def test_not_exists(self, store):
        assert store.exists('nonexistent_xyz') is False


# ─── in_path ─────────────────────────────────────────────────────────

class TestStoreInPath:
    def test_inside(self, store):
        assert store.in_path(store.path + '/something') is True

    def test_outside(self, store):
        assert store.in_path('/tmp/outside') is False


# ─── shorten_item_path ───────────────────────────────────────────────

class TestShortenItemPath:
    def test_basic(self, store):
        full = store.path + '/mykey.json'
        result = store.shorten_item_path(full)
        assert result == 'mykey'

    def test_nested(self, store):
        full = store.path + '/sub/nested/key.json'
        result = store.shorten_item_path(full)
        assert result == 'sub/nested/key'


# ─── ls ──────────────────────────────────────────────────────────────

class TestStoreLs:
    def test_list_contents(self, store):
        store.put_json('ls_test', {'a': 1})
        result = store.ls()
        assert len(result) >= 1

    def test_empty_dir(self, store, tmp_dir):
        empty = os.path.join(tmp_dir, 'empty_store')
        os.makedirs(empty)
        mock = MockStore(empty)
        result = mock.ls()
        assert result == []


# ─── get_age ─────────────────────────────────────────────────────────

class TestStoreGetAge:
    def test_recent_file(self, store):
        store.put_json('age_test', {'a': 1})
        age = store.get_age('age_test')
        assert isinstance(age, float)
        assert age < 5  # should be very recent

    def test_nonexistent(self, store):
        result = store.get_age('nonexistent_xyz', default=-1)
        assert result == -1


# ─── items ───────────────────────────────────────────────────────────

class TestStoreItems:
    def test_returns_dict(self, store):
        store.put_json('item_a', {'val': 1})
        store.put_json('item_b', {'val': 2})
        items = store.items()
        assert isinstance(items, dict)
        assert len(items) >= 2

    def test_search(self, store):
        store.put_json('alpha', {'v': 1})
        store.put_json('beta', {'v': 2})
        items = store.items(search='alpha')
        assert len(items) == 1


# ─── path2name ───────────────────────────────────────────────────────

class TestPath2Name:
    def test_within_store(self, store):
        result = store.path2name(store.path + '/mykey.json')
        assert result == 'mykey'

    def test_outside_store(self, store):
        result = store.path2name('/tmp/other.json')
        assert result == '/tmp/other'


# ─── get_text / put_text ─────────────────────────────────────────────

class TestStoreTextOps:
    def test_roundtrip(self, store, tmp_dir):
        path = os.path.join(tmp_dir, 'text_test.txt')
        store.put_text(path, 'hello store')
        result = store.get_text(path)
        assert result == 'hello store'


# ─── values ──────────────────────────────────────────────────────────

class TestStoreValues:
    def test_returns_list(self, store):
        store.put_json('v1', {'x': 10})
        store.put_json('v2', {'y': 20})
        vals = store.values()
        assert isinstance(vals, list)
        assert len(vals) >= 2


# ─── item2age ────────────────────────────────────────────────────────

class TestStoreItem2Age:
    def test_returns_dict(self, store):
        store.put_json('age1', {'a': 1})
        result = store.item2age()
        assert isinstance(result, dict)
        for path, age in result.items():
            assert isinstance(age, float)
            assert age >= 0


# ─── path2text ───────────────────────────────────────────────────────

class TestStorePath2Text:
    def test_basic(self, store, tmp_dir):
        folder = os.path.join(tmp_dir, 'textfolder')
        os.makedirs(folder)
        with open(os.path.join(folder, 'a.txt'), 'w') as f:
            f.write('content_a')
        with open(os.path.join(folder, 'b.txt'), 'w') as f:
            f.write('content_b')
        result = store.path2text(folder)
        assert result['a.txt'] == 'content_a'
        assert result['b.txt'] == 'content_b'

    def test_nonexistent_folder(self, store):
        with pytest.raises(AssertionError):
            store.path2text('/nonexistent_xyz')

    def test_not_a_folder(self, store, tmp_dir):
        path = os.path.join(tmp_dir, 'file.txt')
        with open(path, 'w') as f:
            f.write('not a folder')
        with pytest.raises(AssertionError):
            store.path2text(path)
