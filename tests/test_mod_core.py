"""Tests for mod/core/mod.py — core Mod class (unit tests that don't require full init)."""
import os
import sys
import json
import tempfile
import shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mod.core.mod import Mod


# ─── Helpers ──────────────────────────────────────────────────────────

class MockMod:
    """A lightweight mock that tests Mod methods without __init__."""

    def __new__(cls):
        obj = object.__new__(Mod)
        # Set minimal attributes needed by individual methods
        obj.homepath = os.path.expanduser('~')
        obj.avoid_folders = set(Mod._avoid_folders)
        obj.file_types = list(Mod._default_file_types)
        obj.anchor_names = list(Mod._default_anchor_names)
        obj.name = 'mod'
        obj.storage_path = os.path.expanduser('~/.mod')
        obj._mod_cache = {}
        obj.obj_cache = {}
        obj.modscache = {}
        obj.fnscache = {}
        obj.tree_cache = {}
        obj.executor_cache = {}
        obj._config_cache = {}
        obj._executors = {}
        import mod as _mod_pkg
        _repo = os.path.dirname(os.path.dirname(os.path.abspath(_mod_pkg.__file__)))
        obj.paths = {
            'mod': _repo + '/mod',
            'lib': _repo,
            'orbit': {
                'orbit': '/tmp/mod_test_orbit',
                'core': _repo + '/mod/core',
                'local': os.getcwd(),
            },
            'home': os.path.expanduser('~'),
        }
        return obj


@pytest.fixture
def mod():
    return MockMod()


# ─── abspath ──────────────────────────────────────────────────────────

class TestAbspath:
    def test_tilde(self, mod):
        result = mod.abspath('~/foo')
        assert result == os.path.expanduser('~/foo')

    def test_relative(self, mod):
        result = mod.abspath('.')
        assert result == os.path.abspath('.')

    def test_absolute(self, mod):
        result = mod.abspath('/tmp/foo')
        assert result == '/tmp/foo'

    def test_empty(self, mod):
        result = mod.abspath('')
        assert result == os.path.abspath('')


# ─── filter_path ──────────────────────────────────────────────────────

class TestFilterPath:
    def test_hidden_excluded(self, mod):
        assert mod.filter_path('/foo/.hidden/bar.py') is False

    def test_hidden_included(self, mod):
        assert mod.filter_path('/foo/.hidden/bar.py', include_hidden=True) is True

    def test_pycache_excluded(self, mod):
        assert mod.filter_path('/foo/__pycache__/bar.pyc') is False

    def test_node_modules_excluded(self, mod):
        assert mod.filter_path('/foo/node_modules/bar.js') is False

    def test_search_match(self, mod):
        assert mod.filter_path('/foo/bar.py', search='bar') is True

    def test_search_no_match(self, mod):
        assert mod.filter_path('/foo/bar.py', search='baz') is False

    def test_normal_path(self, mod):
        assert mod.filter_path('/foo/bar/baz.py') is True


# ─── files ────────────────────────────────────────────────────────────

class TestFiles:
    def test_list_files(self, mod, nested_dir):
        result = mod.files(nested_dir)
        filenames = [os.path.basename(f) for f in result]
        assert 'a.py' in filenames
        assert 'b.py' in filenames
        assert 'c.py' in filenames

    def test_excludes_hidden(self, mod, nested_dir):
        result = mod.files(nested_dir)
        for f in result:
            assert '/.' not in f

    def test_excludes_pycache(self, mod, nested_dir):
        result = mod.files(nested_dir)
        for f in result:
            assert '__pycache__' not in f

    def test_search_filter(self, mod, nested_dir):
        result = mod.files(nested_dir, search='deep')
        assert len(result) == 1
        assert 'c.py' in result[0]

    def test_depth_zero(self, mod, nested_dir):
        result = mod.files(nested_dir, depth=0)
        assert result == []

    def test_depth_one(self, mod, nested_dir):
        result = mod.files(nested_dir, depth=1)
        filenames = [os.path.basename(f) for f in result]
        assert 'a.py' in filenames
        # b.py is in sub/ which is depth 2
        assert 'b.py' not in filenames or 'c.py' not in filenames

    def test_include_hidden(self, mod, nested_dir):
        result = mod.files(nested_dir, include_hidden=True)
        has_hidden = any('d.py' in f for f in result)
        assert has_hidden


# ─── folders ──────────────────────────────────────────────────────────

class TestFolders:
    def test_list_folders(self, mod, nested_dir):
        result = mod.folders(nested_dir, depth=3)
        dirnames = [os.path.basename(d) for d in result]
        assert 'sub' in dirnames
        assert 'deep' in dirnames

    def test_excludes_hidden(self, mod, nested_dir):
        result = mod.folders(nested_dir, depth=3)
        for f in result:
            assert '/.' not in f

    def test_depth_limit(self, mod, nested_dir):
        result = mod.folders(nested_dir, depth=1)
        dirnames = [os.path.basename(d) for d in result]
        assert 'sub' in dirnames
        # deep is at depth 2
        assert 'deep' not in dirnames


# ─── ls ───────────────────────────────────────────────────────────────

class TestLs:
    def test_list_dir(self, mod, nested_dir):
        result = mod.ls(nested_dir)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_full_paths(self, mod, nested_dir):
        result = mod.ls(nested_dir)
        for p in result:
            assert os.path.isabs(p)

    def test_search(self, mod, nested_dir):
        result = mod.ls(nested_dir, search='sub')
        assert all('sub' in p for p in result)

    def test_nonexistent_returns_empty(self, mod):
        result = mod.ls('/nonexistent_path_xyz_test')
        assert result == []


# ─── get_json / put_json ─────────────────────────────────────────────

class TestJson:
    def test_get_json(self, mod, tmp_json):
        data = mod.get_json(tmp_json)
        assert data['name'] == 'test'
        assert data['value'] == 42

    def test_get_json_default(self, mod):
        data = mod.get_json('/nonexistent/path', default={'fallback': True})
        assert data == {'fallback': True}

    def test_get_json_auto_extension(self, mod, tmp_json):
        # Remove .json from path
        base = tmp_json.replace('.json', '')
        data = mod.get_json(base)
        assert data['name'] == 'test'

    def test_get_json_invalid(self, mod, tmp_dir):
        path = os.path.join(tmp_dir, 'bad.json')
        with open(path, 'w') as f:
            f.write('not valid json{{{')
        data = mod.get_json(path, default='fallback')
        assert data == 'fallback'

    def test_put_json(self, mod, tmp_dir):
        path = os.path.join(tmp_dir, 'out')
        mod.put_json(path, {'key': 'value'})
        with open(path + '.json', 'r') as f:
            data = json.load(f)
        assert data == {'key': 'value'}


# ─── text / get_text ──────────────────────────────────────────────────

class TestText:
    def test_read_text(self, mod, tmp_text_file):
        result = mod.text(tmp_text_file)
        assert result == 'hello world'


# ─── put_text ─────────────────────────────────────────────────────────

class TestPutText:
    def test_write_text(self, mod, tmp_dir):
        path = os.path.join(tmp_dir, 'output.txt')
        result = mod.put_text(path, 'test content')
        assert result['success'] is True
        with open(path, 'r') as f:
            assert f.read() == 'test content'

    def test_creates_dirs(self, mod, tmp_dir):
        path = os.path.join(tmp_dir, 'nested', 'dir', 'file.txt')
        mod.put_text(path, 'deep')
        assert os.path.exists(path)


# ─── get_path ─────────────────────────────────────────────────────────

class TestGetPath:
    def test_none_returns_storage_dir(self, mod):
        result = mod.get_path(None)
        assert result == mod.storage_dir()

    def test_absolute_path(self, mod):
        result = mod.get_path('/tmp/test')
        assert result == '/tmp/test'

    def test_tilde_path(self, mod):
        result = mod.get_path('~/test')
        assert result == os.path.expanduser('~/test')

    def test_relative_path(self, mod):
        result = mod.get_path('./test')
        assert result == os.path.abspath('./test')

    def test_storage_key(self, mod):
        result = mod.get_path('mykey')
        assert mod.storage_dir() in result

    def test_extension(self, mod):
        result = mod.get_path('/tmp/test', extension='json')
        assert result.endswith('.json')


# ─── rm ───────────────────────────────────────────────────────────────

class TestRm:
    def test_remove_file(self, mod, tmp_dir):
        path = os.path.join(tmp_dir, 'to_delete.txt')
        with open(path, 'w') as f:
            f.write('delete me')
        result = mod.rm(path)
        assert result['success'] is True
        assert not os.path.exists(path)

    def test_remove_dir(self, mod, tmp_dir):
        dirpath = os.path.join(tmp_dir, 'to_delete_dir')
        os.makedirs(dirpath)
        with open(os.path.join(dirpath, 'file.txt'), 'w') as f:
            f.write('content')
        result = mod.rm(dirpath)
        assert result['success'] is True
        assert not os.path.exists(dirpath)

    def test_nonexistent(self, mod):
        result = mod.rm('/nonexistent_xyz_test_path')
        assert result['success'] is False

    def test_protected_path(self, mod):
        with pytest.raises(PermissionError):
            mod.rm('/')

    def test_extension_fallback(self, mod, tmp_dir):
        path = os.path.join(tmp_dir, 'data.json')
        with open(path, 'w') as f:
            json.dump({'a': 1}, f)
        base = path.replace('.json', '')
        result = mod.rm(base)
        assert result['success'] is True


# ─── glob ─────────────────────────────────────────────────────────────

class TestGlob:
    def test_glob_files(self, mod, nested_dir):
        result = mod.glob(nested_dir)
        assert len(result) > 0
        for f in result:
            assert os.path.isfile(f)

    def test_excludes_hidden(self, mod, nested_dir):
        result = mod.glob(nested_dir)
        for f in result:
            assert '/.' not in f


# ─── env ──────────────────────────────────────────────────────────────

class TestEnv:
    def test_get_all(self, mod):
        result = mod.env()
        assert isinstance(result, dict)
        assert 'PATH' in result

    def test_get_key(self, mod):
        result = mod.env('PATH')
        assert isinstance(result, str)

    def test_missing_key(self, mod):
        result = mod.env('NONEXISTENT_KEY_XYZ_TEST')
        assert result is None


# ─── time ─────────────────────────────────────────────────────────────

class TestTime:
    def test_float(self, mod):
        t = mod.time()
        assert isinstance(t, float)
        assert t > 0

    def test_int(self, mod):
        t = mod.time(mode='int')
        assert isinstance(t, int)
        assert t > 0

    def test_iso(self, mod):
        t = mod.time(mode='iso')
        assert isinstance(t, str)
        assert 'T' in t

    def test_date(self, mod):
        t = mod.time(mode='date')
        assert isinstance(t, str)


# ─── pwd ──────────────────────────────────────────────────────────────

class TestPwd:
    def test_returns_cwd(self, mod):
        assert mod.pwd() == os.getcwd()


# ─── is_home ──────────────────────────────────────────────────────────

class TestIsHome:
    def test_home(self, mod):
        assert mod.is_home(os.path.expanduser('~')) is True

    def test_not_home(self, mod):
        assert mod.is_home('/tmp') is False


# ─── storage_dir ──────────────────────────────────────────────────────

class TestStorageDir:
    def test_default(self, mod):
        result = mod.storage_dir()
        assert result.endswith('/.mod/mod')

    def test_custom(self, mod):
        result = mod.storage_dir('test')
        assert 'test' in result


# ─── relpath ──────────────────────────────────────────────────────────

class TestRelpath:
    def test_home(self, mod):
        result = mod.relpath('~')
        assert result == '~'

    def test_subpath(self, mod):
        home = os.path.expanduser('~')
        result = mod.relpath(home + '/projects')
        assert result == '~/projects'


# ─── mod_class ────────────────────────────────────────────────────────

class TestModClass:
    def test_self(self, mod):
        assert mod.mod_class() == 'Mod'

    def test_given_class(self, mod):
        assert mod.mod_class(int) == 'int'


# ─── iscid ────────────────────────────────────────────────────────────

class TestIsCid:
    def test_valid_cid(self, mod):
        assert mod.iscid('QmXUjBQRFa8DbY2GhD1Aq6a44EBYzgejmtwwnYYTfvnFW4') is True

    def test_invalid_cid(self, mod):
        assert mod.iscid('not_a_cid') is False
        assert mod.iscid('') is False
        assert mod.iscid(42) is False


# ─── kwargs2str ───────────────────────────────────────────────────────

class TestKwargs2Str:
    def test_basic(self, mod):
        result = mod.kwargs2str({'a': 1, 'b': 2})
        assert 'a=1' in result
        assert 'b=2' in result

    def test_strips_self(self, mod):
        result = mod.kwargs2str({'self': None, 'a': 1})
        assert 'self' not in result

    def test_empty(self, mod):
        result = mod.kwargs2str({})
        assert result == ''


# ─── get_name ─────────────────────────────────────────────────────────

class TestGetName:
    def test_default(self, mod):
        assert mod.get_name() == 'mod'

    def test_with_slashes(self, mod):
        result = mod.get_name('model/openrouter')
        assert '/' not in result
        assert '.' in result or result == 'model.openrouter'

    def test_strips_avoid_terms(self, mod):
        result = mod.get_name('src.mymod')
        assert 'src' not in result

    def test_lowercase(self, mod):
        result = mod.get_name('MyMod')
        assert result == result.lower()


# ─── path2classes ─────────────────────────────────────────────────────

class TestPath2Classes:
    def test_find_classes(self, mod, tmp_py_file):
        result = mod.path2classes(tmp_py_file)
        classes_found = []
        for k, v in result.items():
            classes_found.extend(v)
        class_names = [c.split('.')[-1] for c in classes_found]
        assert 'SampleClass' in class_names
        assert 'AnotherClass' in class_names

    def test_tolist(self, mod, tmp_py_file):
        result = mod.path2classes(tmp_py_file, tolist=True)
        assert isinstance(result, list)
        assert len(result) >= 2


# ─── path2fns ─────────────────────────────────────────────────────────

class TestPath2Fns:
    def test_find_functions(self, mod, tmp_py_file):
        result = mod.path2fns(tmp_py_file, tolist=True)
        fn_names = [f.split('.')[-1] for f in result]
        # path2fns only finds top-level def statements (not class methods)
        assert 'standalone_fn' in fn_names

    def test_returns_dict_by_default(self, mod, tmp_py_file):
        result = mod.path2fns(tmp_py_file, tolist=False)
        assert isinstance(result, dict)
        assert tmp_py_file in result


# ─── sleep ────────────────────────────────────────────────────────────

class TestSleep:
    def test_short_sleep(self, mod):
        import time
        t0 = time.time()
        mod.sleep(0.01)
        elapsed = time.time() - t0
        assert elapsed >= 0.01


# ─── confirm ──────────────────────────────────────────────────────────

class TestConfirm:
    def test_yes(self, mod, monkeypatch):
        monkeypatch.setattr('builtins.input', lambda _: 'y')
        assert mod.confirm('Test?') is True

    def test_no(self, mod, monkeypatch):
        monkeypatch.setattr('builtins.input', lambda _: 'n')
        with pytest.raises(KeyboardInterrupt):
            mod.confirm('Test?')


# ─── time2str ─────────────────────────────────────────────────────────

class TestTime2Str:
    def test_default(self, mod):
        result = mod.time2str()
        assert isinstance(result, str)
        assert '-' in result  # date format

    def test_custom_format(self, mod):
        result = mod.time2str(fmt='%Y')
        assert len(result) == 4
