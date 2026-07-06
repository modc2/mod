import os
from typing import Dict, List, Optional


class Tree:
    """Module tree discovery, caching, and search."""

    orbits = ['core', 'orbit', 'mods']
    orbit2depth = { 'orbit': 1, 'core': 10, 'mods': 2}
    ignore_suffixes = ['/src', '/core']
    tree_cache = {}

    def __init__(self, mod):
        self.mod = mod

    # ── Tree & Search ────────────────────────────────────────────────────

    def tree(self, search=None, depth=None, orbit='all', key=None, **kwargs):
        """Get the full tree of mods across all orbits."""
        tree = {}
        orbits = sorted(self.orbits, reverse=True) if orbit == 'all' else [orbit]
        for o in orbits:
            tree.update(self.orbit(o, search=search, depth=depth, key=key, **kwargs))
        return dict(sorted(tree.items(), key=lambda item: len(item[0])))

    def get_tree(self, path: Optional[str] = None, search: Optional[str] = None,
                 depth=1, update=False, key=None, local_cache=False, **kwargs) -> Dict[str, str]:
        """Get the tree of mods in a path."""
        if key is not None:
            key_address = self.mod.key_address(key)
            path = self.mod.paths['orbit']['orbit'] + '/' + key_address
        else:
            path = path or self.mod.paths.orbit.core

        relpath = self.mod.hash(self.mod.relpath(path))
        cache_path = self.mod.abspath(f'~/.mod/tree/{relpath}/depth_{depth}.json')

        if not update and cache_path in self.tree_cache:
            tree = self.tree_cache[cache_path]
        else:
            tree = self.mod.get(cache_path, {}, update=update)

        if not tree:
            paths = self.mod.folders(path, depth=depth)
            for p in paths:
                name = self.mod.get_name(p)
                pp = self.process_path(p)
                if name not in tree or len(pp) < len(tree[name]):
                    tree[name] = pp
            tree = dict(sorted(tree.items()))
            for k, v in self.mod.shortcuts.items():
                if v in tree:
                    tree[k] = tree[v]
            tree = {k: self.mod.relpath(v) for k, v in tree.items()}
            self.tree_cache[cache_path] = tree
            if not local_cache:
                self.mod.put(cache_path, tree)

        tree = {k: self.mod.abspath(v) for k, v in tree.items()}
        if search:
            tree = self.search(search=search, tree=tree, key=key, **kwargs)
        if key is not None:
            tree = {k.replace(key_address.lower() + '.', ''): v for k, v in tree.items()}
        return tree

    def core_tree(self, search=None, depth=4, **kwargs):
        return self.get_tree(self.mod.paths.orbit.core, search=search, depth=depth, **kwargs)

    def orbit(self, orbit='core', search=None, depth=None, **kwargs):
        if depth is None:
            depth = self.orbit2depth.get(orbit, 1)
        kwargs['depth'] = depth or kwargs.get('depth', self.orbit2depth.get(orbit, 1))
        return self.get_tree(self.mod.paths['orbit'][orbit], search=search, **kwargs)

    def operational_tree(self, **kwargs) -> Dict[str, str]:
        """Filter tree to only include operational modules."""
        tree = self.tree(**kwargs)
        result = {}
        for name, path in tree.items():
            if name.count('.') == 0 or self.mod.has_anchor(path):
                result[name] = path
        return result

    @staticmethod
    def filter_fn(k, search):
        k_lower = k.lower()
        return (k_lower == search or search in k_lower
                or k_lower.endswith('.' + search)
                or k_lower.startswith(search + '.'))

    def search(self, search=None, tree=None, depth=None, max_depth=4,
               key=None, orbit='all', **kwargs) -> Dict[str, str]:
        """Search the tree for a mod. Starts at orbit default depth, expands up to max_depth."""
        if orbit == 'all':
            result = {}
            for o in self.orbits + ['local']:
                _r = self.search(search=search, tree=None, depth=depth,
                                     max_depth=max_depth, key=key, orbit=o, **kwargs)
                if isinstance(_r, dict):
                    result.update(_r)
            # sort by shortest path
            result = dict(sorted(result.items(), key=lambda item: len(item[0])))
            return result

        if depth is None:
            depth = self.orbit2depth.get(orbit, 1)
        search = search.lower().replace('/', '.')
        tree = tree or self.tree(depth=depth, orbit=orbit, key=key, **kwargs)
        if search is None:
            return tree
        tree_options = [k for k in tree if self.filter_fn(k, search)]
        if tree_options:
            return {k: tree[k] for k in sorted(tree_options, key=lambda k: len(k))}
        elif depth < max_depth:
            return self.search(search=search, tree=None, depth=depth + 1,
                               max_depth=max_depth, orbit=orbit, **kwargs)
        else:
            core_path = self.mod.paths['orbit']['core']
            for folder in os.listdir(core_path):
                folder_path = os.path.join(core_path, folder)
                if (os.path.isdir(folder_path) and not folder.startswith('_')
                        and (folder.lower() == search or search in folder.lower())):
                    return {folder: folder_path}
            return {}

    def process_path(self, x) -> str:
        for k in self.ignore_suffixes:
            if x.endswith(k):
                x = x[:-len(k)]
        parts = x.split('/')
        if len(parts) >= 2:
            if len(parts) > 2 and parts[-1] == parts[-2]:
                parts = parts[:-1]
            if len(parts) >= 3 and parts[-1] in parts[-3]:
                parts = parts[:-2]
        return '/'.join(parts)
