"""Tests for Tree — module tree discovery and search."""
import os
import pytest
from unittest.mock import MagicMock, patch
from mod.core.tree.tree import Tree


class TestTreeInit:
    def test_init_stores_mod(self):
        mock_mod = MagicMock()
        t = Tree(mock_mod)
        assert t.mod is mock_mod

    def test_class_defaults(self):
        assert 'core' in Tree.orbits
        assert 'orbit' in Tree.orbits


class TestFilterFn:
    def test_exact_match(self):
        assert Tree.filter_fn('store', 'store') is True

    def test_substring_match(self):
        assert Tree.filter_fn('store.local', 'store') is True

    def test_suffix_match(self):
        assert Tree.filter_fn('data.store', 'store') is True

    def test_prefix_match(self):
        assert Tree.filter_fn('store.local', 'store') is True

    def test_no_match(self):
        assert Tree.filter_fn('key', 'store') is False

    def test_case_insensitive(self):
        assert Tree.filter_fn('Store', 'store') is True


class TestTreeProcessPath:
    def test_process_path_strips_suffix(self):
        mock_mod = MagicMock()
        t = Tree(mock_mod)
        assert t.process_path('/mod/orbit/store/src') == '/mod/orbit/store'
        assert t.process_path('/mod/orbit/store/core') == '/mod/orbit/store'

    def test_process_path_no_strip(self):
        mock_mod = MagicMock()
        t = Tree(mock_mod)
        assert t.process_path('/mod/orbit/store') == '/mod/orbit/store'


class TestTreeOrbits:
    def test_orbit2depth_defaults(self):
        assert Tree.orbit2depth['orbit'] == 1
        assert Tree.orbit2depth['core'] == 10
        assert Tree.orbit2depth['portal'] == 2

    def test_tree_aggregates_orbits(self):
        mock_mod = MagicMock()
        mock_mod.paths = {'orbit': {'core': '/core', 'orbit': '/orbit', 'portal': '/orbit/portal'}}
        t = Tree(mock_mod)
        with patch.object(t, 'get_tree', return_value={'mod_a': '/path/a'}):
            result = t.tree()
        assert 'mod_a' in result
