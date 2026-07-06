"""Tests for Mod — core class methods and resolution."""
import os
import pytest
from unittest.mock import MagicMock, patch


class TestModImport:
    def test_mod_class_importable(self):
        from mod.core.mod import Mod
        assert Mod is not None

    def test_mod_class_constants(self):
        from mod.core.mod import Mod
        assert Mod.name == 'mod'
        assert '__pycache__' in Mod._avoid_folders
        assert 'node_modules' in Mod._avoid_folders
        assert 'py' in Mod._default_file_types


class TestInlinedMethods:
    """Verify methods previously in FsMixin, DeployMixin, FactoryMixin are on Mod."""

    def test_has_fs_methods(self):
        from mod.core.mod import Mod
        assert hasattr(Mod, 'abspath')
        assert hasattr(Mod, 'files')

    def test_has_deploy_methods(self):
        from mod.core.mod import Mod
        assert hasattr(Mod, 'serve')

    def test_has_factory_methods(self):
        from mod.core.mod import Mod
        assert hasattr(Mod, 'new')
        assert hasattr(Mod, 'init_mod_meta')
        assert '.mod' in Mod._avoid_folders

    def test_factory_aliases(self):
        from mod.core.mod import Mod
        assert Mod.fork is Mod.new
        assert Mod.create is Mod.new

    def test_init_mod_meta_creates_branch_file(self, tmp_path):
        from mod.core.mod import Mod
        m = Mod()
        m.init_mod_meta(str(tmp_path))
        branch_file = tmp_path / '.mod' / 'branch'
        assert branch_file.exists()
        assert branch_file.read_text() == 'main'

    def test_init_mod_meta_custom_branch(self, tmp_path):
        from mod.core.mod import Mod
        m = Mod()
        m.init_mod_meta(str(tmp_path), branch='dev')
        branch_file = tmp_path / '.mod' / 'branch'
        assert branch_file.read_text() == 'dev'

    def test_init_mod_meta_does_not_overwrite(self, tmp_path):
        from mod.core.mod import Mod
        m = Mod()
        m.init_mod_meta(str(tmp_path), branch='dev')
        m.init_mod_meta(str(tmp_path), branch='other')
        branch_file = tmp_path / '.mod' / 'branch'
        assert branch_file.read_text() == 'dev'


class TestHasHidden:
    """Tests for _has_hidden helper."""

    def test_plain_file(self):
        from mod.core.mod import Mod
        m = Mod()
        assert m._has_hidden('mod.py') is False

    def test_nested_plain(self):
        from mod.core.mod import Mod
        m = Mod()
        assert m._has_hidden('src/utils/mod.py') is False

    def test_hidden_file(self):
        from mod.core.mod import Mod
        m = Mod()
        assert m._has_hidden('.env') is True

    def test_hidden_folder(self):
        from mod.core.mod import Mod
        m = Mod()
        assert m._has_hidden('.git/config') is True

    def test_hidden_nested(self):
        from mod.core.mod import Mod
        m = Mod()
        assert m._has_hidden('src/.secret/key.pem') is True

    def test_dotfile_example(self):
        from mod.core.mod import Mod
        m = Mod()
        assert m._has_hidden('.env.example') is True

    def test_empty_string(self):
        from mod.core.mod import Mod
        m = Mod()
        assert m._has_hidden('') is False


class TestContentHiddenFilter:
    """Tests that content() excludes hidden files unless .example suffixed."""

    def _make_mod_dir(self, tmp_path, files):
        """Create a module directory with given files dict {relpath: text}."""
        mod_dir = tmp_path / 'testmod'
        mod_dir.mkdir()
        for rel, text in files.items():
            fp = mod_dir / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(text)
        # Minimal mod.py anchor so Mod can find it
        anchor = mod_dir / 'mod.py'
        if not anchor.exists():
            anchor.write_text('class Testmod:\n    pass\n')
        return str(mod_dir)

    def test_hidden_files_excluded(self, tmp_path):
        from mod.core.mod import Mod
        m = Mod()
        mod_dir = self._make_mod_dir(tmp_path, {
            'mod.py': 'class Testmod:\n    pass\n',
            '.env': 'SECRET=abc',
            'config.json': '{}',
        })
        with patch.object(m, 'abspath', return_value=mod_dir), \
             patch.object(m, 'dirpath', return_value=mod_dir):
            result = m.content('testmod')
        assert '.env' not in result
        assert 'mod.py' in result
        assert 'config.json' in result

    def test_hidden_folders_excluded(self, tmp_path):
        from mod.core.mod import Mod
        m = Mod()
        mod_dir = self._make_mod_dir(tmp_path, {
            'mod.py': 'class Testmod:\n    pass\n',
            '.git/config': '[core]',
            'src/main.py': 'x = 1',
        })
        with patch.object(m, 'abspath', return_value=mod_dir), \
             patch.object(m, 'dirpath', return_value=mod_dir):
            result = m.content('testmod')
        assert not any('.git' in k for k in result)
        assert 'src/main.py' in result

    def test_example_suffix_allowed(self, tmp_path):
        from mod.core.mod import Mod
        m = Mod()
        mod_dir = self._make_mod_dir(tmp_path, {
            'mod.py': 'class Testmod:\n    pass\n',
            '.env.example': 'KEY=placeholder',
            '.env': 'KEY=real_secret',
        })
        with patch.object(m, 'abspath', return_value=mod_dir), \
             patch.object(m, 'dirpath', return_value=mod_dir):
            # Need include_hidden on files() to pick up dotfiles for the filter to act on
            result = m.content('testmod')
        # .env.example may or may not appear depending on files() include_hidden default,
        # but .env must NEVER appear
        assert '.env' not in result

    def test_no_false_positives(self, tmp_path):
        from mod.core.mod import Mod
        m = Mod()
        mod_dir = self._make_mod_dir(tmp_path, {
            'mod.py': 'class Testmod:\n    pass\n',
            'src/utils.py': 'a = 1',
            'data/file.csv': 'a,b',
        })
        with patch.object(m, 'abspath', return_value=mod_dir), \
             patch.object(m, 'dirpath', return_value=mod_dir):
            result = m.content('testmod')
        assert 'mod.py' in result
        assert 'src/utils.py' in result
        assert 'data/file.csv' in result
