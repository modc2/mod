import os
import shutil
import threading
import time
from typing import Optional, Dict, Any, List, Union
import mod as m


class Registry:
    """
    Module registry — manages registration, lookup, versioning, and content
    for mod modules stored via CID-based content addressing.
    """

    folder_path = m.abspath('~/.mod/api')

    def __init__(self, key=None, store=None):
        store = store or m.config('api').get('store', 'localfs')
        self.store = m.mod(store)()
        self.key = m.key(key)
        self.registry_path = self.path('registry.json')

    def path(self, path: str) -> str:
        return self.folder_path + '/' + path

    # --- Key helpers ---

    def key_address(self, key=None):
        key = key or 'mod'
        if isinstance(key, str):
            if self.key.valid_ss58_address(key):
                return key.lower()
            else:
                return m.key(key).address.lower()
        else:
            return (key or m.key()).address.lower()

    def is_owner(self, address: str):
        return m.key() == address or self.key.address == address

    # --- Store wrappers ---

    def put(self, data):
        return self.store.put(data)

    def get(self, cid: str) -> Any:
        return self.store.get(cid)

    # --- Registry CRUD ---

    def registry(self, key='all', update=False) -> Dict[str, str]:
        """Get the mod registry."""
        registry = m.get(self.registry_path, {}, update=update)
        registry = {k.lower(): v for k, v in registry.items()}
        if key != 'all':
            registry = registry.get(self.key_address(key).lower(), {})
        return registry

    def cid(self, mod, key=None, default=None) -> str:
        return self.registry().get(self.key_address(key), {}).get(mod, default)

    def exists(self, mod='store', key=None) -> bool:
        """Check if a module exists in the store."""
        return bool(self.cid(mod=mod, key=key))

    def mod(self, mod='api', key=None, schema=False, expand=False, update=False, **kwargs) -> Dict[str, Any]:
        """Get a module's metadata from the store. Falls back to local filesystem."""
        cid = self.cid(mod=mod, key=key)
        if not cid:
            # Try local filesystem fallback
            return self._local_mod(mod, key=key, schema=schema, **kwargs)
        try:
            mod_info = self.get(cid)
        except (FileNotFoundError, Exception):
            return self._local_mod(mod, key=key, schema=schema, **kwargs)
        if mod_info['name'].startswith(mod_info['key'].lower() + '.'):
            mod_info['name'] = mod_info['name'][len(mod_info['key'].lower()) + 1:]
        mod_info['schema'] = self.get(mod_info['schema']) if schema else mod_info['schema']
        mod_info['content'] = self.content(mod_info['content'], expand=expand) if expand else mod_info['content']
        mod_info['cid'] = cid
        # Enrich with live URL data from config.json if not already present
        if not mod_info.get('url') or (isinstance(mod_info.get('url'), str)):
            live_url = self.get_url(mod_info['name'])
            if live_url:
                mod_info['url'] = live_url
        return mod_info

    def _local_mod(self, mod_name, key=None, schema=False, **kwargs) -> Dict[str, Any]:
        """Build module info from local filesystem when not in registry."""
        try:
            tree = m.tree(search=mod_name, orbit='orbit', depth=2)
            if not tree:
                tree = m.tree(search=mod_name, depth=4)
            if not tree:
                return {}
            mod_path = list(tree.values())[0]
            owner_key = self.key_address(key) if key else self.key_address()
            local_info = {
                'name': mod_name,
                'key': owner_key,
                'cid': None,
                'schema': None,
                'content': None,
                'created': None,
                'updated': None,
                'url': self.get_url(mod_name),
                'local': True,
                'path': mod_path,
            }
            if schema:
                try:
                    local_info['schema'] = m.schema(mod_name)
                except Exception:
                    pass
            return local_info
        except Exception:
            return {}

    def mods(self, search: str = None, key='all', n: int = None, page: int = None, page_size=10, **kwargs) -> List[Dict[str, Any]]:
        """List all registered mods + unregistered local mods from ~/mod."""
        registry = self.registry()
        # Build list of (mod_name, user_key) pairs first (cheap), dedup by lowered key
        entries = []
        seen = set()
        registered_names = set()
        if key != 'all':
            registry = {key.lower(): registry.get(key.lower(), {})}
        for user_key, user_mods in registry.items():
            for mod_name in user_mods.keys():
                dedup_key = (mod_name.lower(), user_key.lower())
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    entries.append((mod_name, user_key))
                    registered_names.add(mod_name.lower())

        # Discover local filesystem modules not yet registered
        local_entries = []
        try:
            local_tree = m.tree(orbit='orbit', depth=2)
            owner_key = self.key_address()
            for mod_name, mod_path in local_tree.items():
                # Skip stale entries: the tree cache can outlive a deleted module
                # (rm_mod's cache invalidation key differs from this read's), so a
                # just-removed module would otherwise still be listed. Verify the
                # path still exists on disk before surfacing it.
                if mod_path and not os.path.exists(mod_path):
                    continue
                # Include top-level modules always; include qualified (dotted) names only when searching
                if mod_name.lower() not in registered_names and ('.' not in mod_name or search is not None):
                    local_entries.append((mod_name, owner_key, mod_path))
                    registered_names.add(mod_name.lower())
        except Exception:
            pass

        # Filter by search BEFORE loading full mod info (expensive)
        if search is not None:
            search_lower = search.lower()
            entries = [(name, k) for name, k in entries if search_lower in name.lower()]
            local_entries = [(name, k, p) for name, k, p in local_entries if search_lower in name.lower()]
        # Paginate BEFORE loading full mod info
        total_registered = len(entries)
        total_local = len(local_entries)
        if page is not None and page_size is not None:
            start = page * page_size
            end = start + page_size
            # Registered entries come first, then local
            if start < total_registered:
                entries = entries[start:min(end, total_registered)]
                remaining = end - total_registered
                local_entries = local_entries[:max(0, remaining)] if remaining > 0 else []
            else:
                local_start = start - total_registered
                entries = []
                local_entries = local_entries[local_start:local_start + page_size]
        if n is not None:
            entries = entries[:n]
            local_entries = local_entries[:max(0, n - len(entries))]
        # Now load full mod info only for the slice we need
        mods = []
        seen_names = set()
        for mod_name, user_key in entries:
            info = self.mod(mod_name, key=user_key, **kwargs)
            if isinstance(info, dict) and 'name' in info:
                # Deduplicate by resolved name (mod() may strip key prefix)
                resolved = info['name'].lower()
                if resolved not in seen_names:
                    seen_names.add(resolved)
                    mods.append(info)
        # Build minimal info for unregistered local modules
        for mod_name, owner_key, mod_path in local_entries:
            if mod_name.lower() in seen_names:
                continue
            seen_names.add(mod_name.lower())
            local_info = {
                'name': mod_name,
                'key': owner_key,
                'cid': None,
                'schema': None,
                'content': None,
                'created': None,
                'updated': None,
                'url': self.get_url(mod_name),
                'local': True,
                'path': mod_path,
            }
            # Pull desc from config (schema is lazy — fetched on demand)
            try:
                cfg = m.config(mod_name)
                if isinstance(cfg, dict) and cfg.get('description'):
                    local_info['desc'] = cfg['description']
            except Exception:
                pass
            mods.append(local_info)
        return mods

    def n(self, *args, **kwargs):
        return len(self.mods(*args, **kwargs))

    # --- Content ---

    def content(self, mod, key=None, expand=False, depth=None, h=False) -> Dict[str, Any]:
        """Get the content of a module from the store."""
        try:
            if self.store.valid_cid(mod):
                data = self.get(mod)
                if isinstance(data, dict) and 'content' in data:
                    content = self.get(data['content'])['data']
                else:
                    content = self.get(mod)['data']
            else:
                mod_info = self.mod(mod, key=key)
                if not mod_info:
                    raise KeyError(f"Module '{mod}' not found in registry")
                content = self.get(mod_info['content'])['data']
            if expand:
                content = self.get(content)
            if h:
                return self.hc(content)
        except Exception as e:
            err = m.detailed_error(e)
            m.print(f"[content] Error: {err['error']}", color='red')
            m.print(f"  File: {err['file_name']}:{err['line_no']}", color='red')
            m.print(f"  Line: {err['line_text']}", color='red')
            raise
        return content

    def add_content(self, mod: str = 'store', comment=None) -> Dict[str, str]:
        file2cid = {}
        mod = mod.lower()
        content = m.content(mod)
        error_keys = {'error', 'file_name', 'line_no', 'line_text', 'success', 'traceback'}
        if isinstance(content, dict) and len(content) > 0:
            content_keys = set(content.keys())
            if len(content_keys & error_keys) >= 3:
                raise ValueError(f"Module '{mod}' content returned an error: {content.get('error', 'unknown')}")
        for file, file_content in content.items():
            cid = self.put(file_content)
            file2cid[file] = cid
        return self.put({'data': self.put(file2cid), 'comment': comment})

    def add_schema(self, mod: str = 'store', public=True) -> str:
        schema = m.schema(mod)
        return self.put(schema)

    def files(self, mod='store', search=None, **kwargs):
        files = list(self.content(mod, expand=True, **kwargs).keys())
        if search is not None:
            files = [f for f in files if search in f]
        return files

    # --- Hierarchical content helpers ---

    def hc(self, content: Dict[str, Any], flatten=False) -> Dict[str, Any]:
        """Get a human-readable version of the content dictionary."""
        new_dict = {}
        for file, cid in content.items():
            subfiles = file.split('/')
            self.dict_put(subfiles, cid, new_dict)
        return self.get_folder_cid(new_dict)

    def sort_recursive_dict(self, d: Dict[str, Any]) -> Dict[str, Any]:
        sorted_dict = {}
        for key in sorted(d.keys()):
            if isinstance(d[key], dict):
                sorted_dict[key] = self.sort_recursive_dict(d[key])
            else:
                sorted_dict[key] = d[key]
        return sorted_dict

    def get_folder_cid(self, folder_content: dict) -> str:
        is_single_depth_dict = lambda v: all(isinstance(v, str) for v in folder_content.values())
        new_folder_content = {}
        for file, content in folder_content.items():
            if isinstance(content, dict) and len(content) > 0:
                if is_single_depth_dict(content):
                    new_folder_content[file + '/'] = self.put(content)
                else:
                    new_folder_content[file + '/'] = self.put(self.get_folder_cid(content))
            else:
                new_folder_content[file] = content
        return new_folder_content

    def dict_put(self, k_list, v, d: Dict[str, Any]):
        if len(k_list) == 0:
            return v
        key = k_list[0]
        if key not in d:
            d[key] = {}
        d[key] = self.dict_put(k_list[1:], v, d[key])
        return d

    # --- Registration ---

    def reg_info(self, mod: dict):
        mod = self.get(mod) if isinstance(mod, str) else mod
        cid = mod['cid'] if 'cid' in mod else self.put(mod)
        registry = m.get(self.registry_path, {})
        key = mod['key'].lower()
        # Deduplicate: remove any differently-cased versions of this key
        for existing_key in list(registry.keys()):
            if existing_key.lower() == key and existing_key != key:
                registry[key] = {**registry.pop(existing_key), **registry.get(key, {})}
        registry[key] = registry.get(key, {})
        registry[key][mod['name']] = cid
        m.put(self.registry_path, registry)
        print('Registered({key}, {mod}) -> {cid}'.format(key=key, mod=mod['name'], cid=cid))
        return cid

    def get_info(self, mod='store', key=None, name=None, comment=None, public=False) -> Dict[str, Any]:
        """Build registration info for a module."""
        current_time = m.time()
        key = self.key_address(key)
        prev_info = self.mod(mod, key=key)
        return {
            'content': self.add_content(mod=mod, comment=comment),
            'schema': self.add_schema(mod=mod, public=public),
            'prev': prev_info.get('cid', None),
            'created': prev_info.get('created', current_time),
            'updated': current_time,
            'name': name or prev_info.get('name', mod),
            'key': prev_info.get('key', key),
            'url': self.get_url(mod),
        }

    def get_url(self, mod_name: str):
        """Get URL(s) for a module. Returns {api, app} dict or a string."""
        # Check the module's config.json for urls/url
        try:
            cfg = m.config(mod_name)
            if isinstance(cfg, dict):
                # config.json uses 'urls' key with {api, app}
                if 'urls' in cfg and isinstance(cfg['urls'], dict):
                    return cfg['urls']
                # or 'url' key with {api, app} or string
                if 'url' in cfg:
                    return cfg['url']
        except Exception:
            pass
        # Fall back to namespace server registry
        ns_url = m.namespace().get(mod_name, None)
        return ns_url

    def is_git_url(self, url: str) -> bool:
        return 'github.com' in url or 'gitlab.com' in url or ('/' in url and len(url.split('/')) == 2)

    def is_cid_url(self, url: str) -> bool:
        return self.store.iscid(url)

    def _init_mod_meta(self, modpath: str, branch: str = 'main'):
        """Initialize .mod/branch metadata for a module directory."""
        mod_meta = os.path.join(modpath, '.mod')
        os.makedirs(mod_meta, exist_ok=True)
        with open(os.path.join(mod_meta, 'branch'), 'w') as f:
            f.write(branch)

    def reg_git(self, url: str, name=None, key=None, comment=None, token=None) -> Dict[str, Any]:
        """Register a module from a git URL."""
        if token:
            verified_data = m.mod('auth')().verify(token)
            key = verified_data['key']
        else:
            key = self.key_address(key)

        print(f"Registering mod from URL: {url} with key: {key} and name: {name}")
        assert self.is_git_url(url), f'Unsupported URL for reg_git: {url}'
        # Normalize URL: strip trailing .git, extract repo name
        clean_url = url.rstrip('/')
        if clean_url.endswith('.git'):
            clean_url = clean_url[:-4]
        name = name or clean_url.split('/')[-1]
        name = name.lower()
        # Expand shorthand (user/repo) to full GitHub URL
        clone_url = url
        if '/' in url and len(url.split('/')) == 2 and 'github.com' not in url:
            clone_url = f'https://github.com/{url}'
        orbit = 'orbit' if self.is_owner(key) else 'mods'
        dirpath = m.paths['orbit'][orbit]
        modpath = os.path.join(dirpath, key, name)
        if os.path.exists(modpath):
            shutil.rmtree(modpath)
        os.makedirs(os.path.dirname(modpath), exist_ok=True)
        git_cmd = f'git clone --single-branch {clone_url} {modpath}'
        ret = os.system(git_cmd)
        assert ret == 0 and os.path.exists(modpath), f'git clone failed for {clone_url}'
        self._init_mod_meta(modpath)
        # Write config.json with git url for provenance
        config_path = os.path.join(modpath, 'config.json')
        if not os.path.exists(config_path):
            import json
            config = {'name': name, 'url': clone_url, 'key': key}
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
        info = self.get_info(mod=name, key=key, comment=comment)
        info['cid'] = self.reg_info(info)
        self.update()
        return info

    def reg_cid(self, cid: str, key=None, name=None, comment=None) -> Dict[str, Any]:
        """Register a module from a CID.

        The CID can point to:
        1. A config.json dict (must have 'name', may have 'url' for git source)
        2. A full mod_info dict (has 'content' and 'schema' keys — legacy format)
        """
        data = self.get(cid)
        assert isinstance(data, dict), f'CID {cid} does not resolve to a dict: {type(data)}'

        # Legacy format: full mod_info with content/schema already built
        if 'content' in data and 'schema' in data:
            mod_info = data
            mod_name = mod_info['name']
            content = self.get(mod_info['content'])
            owner_key = mod_info.get('key', self.key_address(key))
            orbit = 'orbit' if self.is_owner(owner_key) else 'mods'
            modpath = os.path.join(m.paths['orbit'][orbit], owner_key, mod_name)
            for file, file_cid in content['data'].items():
                file_content = self.get(file_cid)
                filepath = os.path.join(modpath, file)
                m.put_text(filepath, file_content)
            self._init_mod_meta(modpath)
            return self.reg_info(mod_info)

        # Config.json format: resolve name, clone if git url, then register
        config = data
        name = name or config.get('name')
        assert name, f'CID config has no "name" field and no name provided: {config}'
        name = name.lower()
        key = self.key_address(key)

        # Extra fields from config to carry into registration info
        config_fields = {k: v for k, v in config.items()
                         if k not in ('name', 'key') and v is not None}

        # If config has a git url, clone from it
        url = config.get('url') or config.get('urls', {}).get('git')
        if url and self.is_git_url(url):
            info = self.reg_git(url, name=name, key=key, comment=comment)
            info.update(config_fields)
            # Re-store with merged fields
            info['cid'] = self.put(info)
            self.reg_info(info)
            return info

        # Otherwise, write the config locally and register as a local mod
        orbit = 'orbit' if self.is_owner(key) else 'mods'
        modpath = os.path.join(m.paths['orbit'][orbit], key, name)
        os.makedirs(modpath, exist_ok=True)
        # Write the config.json
        import json
        config_path = os.path.join(modpath, 'config.json')
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        self._init_mod_meta(modpath)
        # Build info from the now-local module (anchor auto-generates if needed)
        info = self.get_info(mod=name, key=key, comment=comment)
        info.update(config_fields)
        info['cid'] = self.reg_info(info)
        self.update()
        return info

    def reg(self, mod: Union[str, dict] = 'store', key=None, comment=None, public=True, token=None, name=None) -> Dict[str, Any]:
        """Register or update a module in the store."""
        if token:
            key = key or m.mod('auth')().verify(token)['key']
        if isinstance(mod, str) and self.is_cid_url(mod):
            return self.reg_cid(mod, key=key, name=name, comment=comment)
        elif isinstance(mod, str) and self.is_git_url(mod):
            return self.reg_git(mod, key=key, comment=comment, name=name, token=token)
        info = self.get_info(mod=mod, key=key, comment=comment, public=public, name=name)
        info['cid'] = self.reg_info(info)
        self.update()
        return info

    def update(self):
        self.mods(update=1)

    def rm_mod(self, mod='store', key=None) -> bool:
        """Remove a mod from the store."""
        registry = self.registry()
        key = self.key_address(key)
        mod_info = self.mod(mod, key=key)
        if not mod_info:
            return False
        mod_cid = mod_info.get('cid')
        if mod_cid:
            content_info_cid = mod_info.get('content')
            if content_info_cid and isinstance(content_info_cid, str):
                content_data = self.get(content_info_cid)
                if isinstance(content_data, dict) and 'data' in content_data:
                    content_map = self.get(content_data['data'])
                    if isinstance(content_map, dict):
                        for file_key, file_cid in content_map.items():
                            self.store.rm(file_cid)
                self.store.rm(content_info_cid)
            schema_cid = mod_info.get('schema')
            if schema_cid and isinstance(schema_cid, str):
                self.store.rm(schema_cid)
            self.store.rm(mod_cid)
        if key in registry and mod in registry[key]:
            del registry[key][mod]
            m.put(self.registry_path, registry)
        # Remove local module directory if it exists
        try:
            mod_path = m.dp(mod)
        except (AssertionError, Exception):
            mod_path = None
        if mod_path and os.path.isdir(mod_path):
            import shutil
            shutil.rmtree(mod_path)
        # Rebuild tree cache so mods() doesn't rediscover the deleted module
        try:
            m._tree.orbit('orbit', update=True)
        except Exception:
            m._tree.tree_cache.clear()
        return True

    # --- Schema ---

    def schema(self, mod='store', key=None) -> Dict[str, Any]:
        """Get the schema of a mod."""
        fn = None
        if not isinstance(mod, dict):
            if '/' in mod:
                fn = mod.split('/')[-1]
                mod = mod.replace('/' + fn, '')
            mod_info = self.mod(mod, key=key, schema=False)
        else:
            assert 'schema' in mod, "Mod dictionary must contain 'schema' key"
            mod_info = mod
        schema = self.get(mod_info['schema'])
        if fn is not None:
            schema = schema.get(fn, {})
        return schema

    # --- Versioning ---

    def timestamp2utc(self, timestamp: int) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

    def versions(self, mod='app', key=None, df=False, n=1000, update=True, max_age=None) -> List[Dict[str, Any]]:
        if self.store.valid_cid(mod):
            mod_info = self.get(mod)
            mod = mod_info['name']
            key = mod_info['key']
        key_address = self.key_address(key)
        cache_path = self.path(f'versions/{key_address}/{mod}.json')
        result = m.get(cache_path, None, update=update, max_age=max_age)
        if result is None:
            cid = self.cid(mod=mod, key=key)
            result = []
            current_n = 0
            if cid is not None:
                while current_n < n:
                    info = self.get(cid)
                    content = self.get(info['content'])
                    prev_cid = info.get('prev', None)
                    result.append({
                        'cid': cid,
                        'data': content['data'],
                        'comment': content.get('comment', ''),
                        'updated': self.timestamp2utc(info['updated']),
                        'created': self.timestamp2utc(info.get('created', info['updated'])),
                    })
                    if prev_cid is None:
                        break
                    else:
                        cid = prev_cid
                    current_n += 1
            if len(result) > 0:
                result = m.df(result)
                result.sort_values('updated', ascending=False, inplace=True)
                result = result[:n]
                if not df:
                    result = result.to_dict(orient='records')
            m.put(cache_path, result)
        if n is not None:
            result = result[:n]
        return result

    v = versions

    def anchor_file(self, mod: str, key=None) -> Dict[str, Any]:
        mod_info = self.mod(mod, key=key)
        if not mod_info:
            return None
        content = mod_info.get('content', {})
        content = self.get(content)
        assert 'data' in content, f"Content for mod {mod} is missing 'data' field."
        file2content = self.get(content['data'])
        file_name_options = ['mod.py', 'server.py', mod_info['name'] + '.py']
        for file_path, file_content in file2content.items():
            if any(opt in file_path for opt in file_name_options):
                return file_path
        return None

    def setback(self, mod: str, cid: str, key=None, safety=True) -> Dict[str, Any]:
        """Setback a module to a previous CID."""
        mod_info = self.mod(mod, key=key)
        old_content = self.content(mod_info['cid'], expand=1)
        new_content = self.content(cid, expand=1)
        print(cid, new_content)
        dirpath = m.dp(mod_info['name'])
        add_dp_to_file = lambda f: os.path.join(dirpath, f)
        delete_files = [add_dp_to_file(f) for f in old_content.keys() if f not in new_content.keys()]
        new_content = {add_dp_to_file(k): v for k, v in new_content.items()}
        write_files = list(new_content.keys())
        print(f"Setback will overwrite the current mod at {mod} with content from CID {cid}.")
        m.print(f"old_content: {old_content}")
        m.print(f"new content: {new_content}")
        m.print(f"Files to be written: {write_files}")
        m.print(f"Files to be deleted: {delete_files}")
        if safety:
            input_prompt = input(f"Setback will overwrite the current mod at {mod}. Press y to continue...")
            if input_prompt != 'y':
                return {'status': 'setback aborted by user'}
            else:
                m.print("Proceeding with setback...", color="green")
        for k, v in new_content.items():
            m.put_text(k, self.get(v))
        for file_path in delete_files:
            m.rm(file_path)
        self.reg_info(cid)
        return {
            'old_cid': mod_info['cid'],
            'new_cid': cid,
            'mod': mod_info
        }

    # --- Users ---

    def user_keys(self, key=None) -> List[str]:
        """List all unique users who have registered mods."""
        return list(self.registry().keys())

    def users(self, search=None, update=False, **kwargs) -> List[Dict[str, Any]]:
        """List all users who have registered mods."""
        path = self.path('users')
        users = m.get(path, update=update)
        if users is None:
            user_keys = self.user_keys()
            users = []
            for user_key in user_keys:
                if search and search not in user_key:
                    continue
                users.append(self.user(user_key, update=update))
            m.put(path, users)
        return users

    def user(self, key: str = None, update=False, expand=False) -> Dict[str, Any]:
        """Get information about a specific user."""
        key = self.key_address(key)
        user_path = self.path(f'users/{key}_expand={expand}.json')
        user = m.get(user_path, None, update=update)
        if user is None:
            user = {
                'key': key,
                'balance': 0,
                'mods': [self.get(_cid) for _cid in self.registry().get(key, {}).values()]
            }
            user['mods'] = self.put(user['mods'])
            user['n'] = len(user['mods'])
            m.put(user_path, user)
        if expand:
            user['mods'] = self.get(user['mods'])
        return user

    # --- Bulk registration ---

    def regall(self, key=None, depth=1, comment=None, public=False, timeout=30) -> Dict[str, Any]:
        """Register all mods in the local environment."""
        results = []
        for mod_name in m.mods(depth=depth):
            try:
                result = self.reg(mod=mod_name, key=key, comment=comment, public=public)
                print(f"Registered mod: {result['name']} with CID: {result['cid']}")
                results.append(result)
            except Exception as e:
                print(f"Error registering mod: {str(e)}")
        return results

    # --- Schema sync & background worker ---

    def sync_schemas(self, search=None, depth=1) -> Dict[str, str]:
        """Compute schema for all modules, store as localfs CIDs, update config.json and registry."""
        results = {}
        for mod_name in m.mods(search=search, depth=depth):
            try:
                schema = m.schema(mod_name)
                if not schema:
                    continue
                cid = self.put(schema)
                # update config.json schema field to CID
                mod_path = m.dp(mod_name)
                if mod_path:
                    config_path = os.path.join(mod_path, 'config.json')
                    if os.path.exists(config_path):
                        cfg = m.config(mod_name)
                        if cfg.get('schema') != cid:
                            cfg['schema'] = cid
                            m.save_config(mod_name, cfg)
                # update registry entry if registered
                registry = m.get(self.registry_path, {})
                key = self.key_address()
                if key in registry and mod_name in registry[key]:
                    mod_cid = registry[key][mod_name]
                    try:
                        mod_info = self.get(mod_cid)
                        if mod_info.get('schema') != cid:
                            mod_info['schema'] = cid
                            mod_info['updated'] = m.time()
                            new_cid = self.put(mod_info)
                            registry[key][mod_name] = new_cid
                            m.put(self.registry_path, registry)
                    except Exception:
                        pass
                results[mod_name] = cid
            except Exception as e:
                results[mod_name] = f'error: {str(e)}'
        return results

    def start_worker(self, interval=300) -> dict:
        """Start background schema sync worker (runs every `interval` seconds)."""
        if hasattr(self, '_worker_thread') and self._worker_thread and self._worker_thread.is_alive():
            return {'status': 'already_running', 'interval': interval}
        self._worker_interval = interval
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        return {'status': 'started', 'interval': interval}

    def stop_worker(self) -> dict:
        """Stop the background schema sync worker."""
        self._worker_running = False
        return {'status': 'stopped'}

    def worker_status(self) -> dict:
        """Check if the background worker is running."""
        alive = hasattr(self, '_worker_thread') and self._worker_thread and self._worker_thread.is_alive()
        return {
            'running': alive,
            'interval': getattr(self, '_worker_interval', None),
        }

    def _worker_loop(self):
        while getattr(self, '_worker_running', False):
            try:
                self.sync_schemas()
            except Exception as e:
                print(f'[registry worker] sync error: {e}')
            time.sleep(self._worker_interval)

    def reg_payload(self, mod: str = 'store', key=None, comment=None) -> Dict[str, Any]:
        """Generate registration payload without executing registration."""
        info = self.get_info(mod=mod, key=key, comment=comment)
        return info

    def _clear(self) -> bool:
        m.put(self.registry_path, {})
        self.store.gc(aggressive=True)
        return {'status': 'registry cleared'}

    def cid_exists(self, cid: str) -> bool:
        """Check that a CID's block is actually resolvable in the store."""
        if not cid or not isinstance(cid, str):
            return False
        try:
            self.get(cid)
            return True
        except Exception:
            return False

    def snapshot(self, encrypt=True) -> str:
        """Take a fresh snapshot of the registry and update the root CID."""
        registry = self.registry()
        if encrypt:
            registry = self.key.encrypt(registry)
        root_cid = self.put(registry)
        # wrap in a dict: m.get swallows non-dict payloads (returns default)
        m.put(self.path('root_cid.json'), {'cid': root_cid, 'time': m.time()})
        return root_cid

    def root(self, encrypt=True, update=False, **kwargs) -> str:
        path = self.path('root_cid.json')
        root_cid = m.get(path, {}, update=update).get('cid')
        # a cached root CID can be stale (block gc'd/removed) — re-snapshot if it doesn't resolve
        if not self.cid_exists(root_cid):
            root_cid = self.snapshot(encrypt=encrypt)
        return root_cid

    def get_root(self, decrypt=True, **kwargs) -> Dict[str, Any]:
        root_cid = self.root(encrypt=decrypt)
        registry = self.get(root_cid)
        if decrypt:
            registry = self.key.decrypt(registry)
        return registry

    # --- Mods root hash (local filesystem integrity) ---

    def mods_root_hash(self, update=True) -> Dict[str, Any]:
        """Compute the root hash of all mods in registry/mods/.

        Walks the mods directory, hashes every file with SHA-256,
        then hashes the sorted file-hash map to produce a deterministic root.

        Returns:
            {'root': <sha256>, 'tree': {relpath: <sha256>}, 'n_files': int, 'n_mods': int}
        """
        import hashlib, json as _json
        mods_dir = m.paths['orbit']['mods']
        if not os.path.isdir(mods_dir):
            return {'root': None, 'tree': {}, 'n_files': 0, 'n_mods': 0}

        file_hashes = {}
        mod_set = set()
        for dirpath, dirnames, filenames in os.walk(mods_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for fname in sorted(filenames):
                if fname.startswith('.'):
                    continue
                fpath = os.path.join(dirpath, fname)
                relpath = os.path.relpath(fpath, mods_dir)
                with open(fpath, 'rb') as f:
                    file_hashes[relpath] = hashlib.sha256(f.read()).hexdigest()
                parts = relpath.split(os.sep)
                if len(parts) >= 2:
                    mod_set.add(f'{parts[0]}/{parts[1]}')

        root_data = _json.dumps(file_hashes, sort_keys=True)
        root = hashlib.sha256(root_data.encode()).hexdigest()
        return {'root': root, 'tree': file_hashes, 'n_files': len(file_hashes), 'n_mods': len(mod_set)}

    def commit_mods_root(self, comment=None, update=True) -> Dict[str, Any]:
        """Commit the current mods root hash to the store.

        Computes the filesystem hash, stores the tree as a CID,
        and saves the root entry for later verification.

        Returns:
            {'root': <sha256>, 'cid': <store_cid>, 'n_files': int, 'n_mods': int}
        """
        info = self.mods_root_hash()
        if not info['root']:
            return {'error': 'no mods found', **info}

        payload = {
            'root': info['root'],
            'tree': self.put(info['tree']),
            'n_files': info['n_files'],
            'n_mods': info['n_mods'],
            'comment': comment,
            'time': m.time(),
        }
        cid = self.put(payload)

        # store the latest root CID for quick lookup
        root_path = self.path('mods_root.json')
        prev = m.get(root_path, None)
        payload['prev'] = prev
        payload['cid'] = cid
        m.put(root_path, payload)

        return {'root': info['root'], 'cid': cid, 'n_files': info['n_files'], 'n_mods': info['n_mods']}

    def verify_mods_root(self) -> Dict[str, Any]:
        """Verify the current mods directory matches the last committed root hash.

        Returns:
            {'valid': bool, 'current': <hash>, 'committed': <hash>, 'changed_files': [...]}
        """
        current = self.mods_root_hash()
        root_path = self.path('mods_root.json')
        committed = m.get(root_path, None)
        if not committed:
            return {'valid': False, 'error': 'no committed root', 'current': current['root']}

        valid = current['root'] == committed['root']
        changed = []
        if not valid:
            try:
                committed_tree = self.get(committed['tree'])
                all_files = set(current['tree'].keys()) | set(committed_tree.keys())
                for f in sorted(all_files):
                    cur = current['tree'].get(f)
                    exp = committed_tree.get(f)
                    if cur != exp:
                        status = 'added' if exp is None else ('removed' if cur is None else 'modified')
                        changed.append({'file': f, 'status': status})
            except Exception:
                pass

        return {
            'valid': valid,
            'current': current['root'],
            'committed': committed['root'],
            'changed_files': changed,
        }

    # --- Source root hash (one hash over every module's source on this node) ---

    # Only source counts. Build output, vendored deps, binaries and logs are
    # skipped so the root moves when someone edits code, not when a server writes.
    SRC_SKIP_DIRS = {
        'node_modules', '.next', '__pycache__', 'target', 'target-docker',
        'vendor', 'dist', 'build', 'out', 'venv', '.venv', '.git', '.turbo',
        '.cache', '.mypy_cache', '.pytest_cache', 'coverage', 'logs',
    }
    SRC_SKIP_FILES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'Cargo.lock'}
    SRC_SKIP_EXTS = {
        '.log', '.db', '.sqlite', '.sqlite3', '.pyc', '.pyo', '.so', '.a', '.o',
        '.rlib', '.rmeta', '.dylib', '.dll', '.bin', '.pack', '.ptau', '.zkey',
        '.wasm', '.tsbuildinfo', '.lock', '.png', '.jpg', '.jpeg', '.gif',
        '.webp', '.mp4', '.mov', '.woff', '.woff2', '.ttf', '.ico', '.pdf',
        '.zip', '.tar', '.gz',
    }
    SRC_MAX_FILE_SIZE = 2 * 1024 * 1024

    def src_root_hash(self) -> Dict[str, Any]:
        """Hash the source of every module on this node into a single root.

        Per module: sha256 over its sorted {relpath: sha256(file)} map.
        Root: sha256 over the sorted {space/module: module_hash} map, so a
        changed file moves exactly one module hash and the root with it.

        Returns:
            {'root': <sha256>, 'mods': {name: <sha256>}, 'n_mods': int,
             'n_files': int, 'n_skipped': int, 'ms': int}
        """
        import hashlib, json as _json
        t0 = time.time()
        root_dir = m.paths['mod']
        mods, n_files, n_skipped = {}, 0, 0

        for space in sorted(os.listdir(root_dir)):
            space_dir = os.path.join(root_dir, space)
            if not os.path.isdir(space_dir) or space.startswith('.') or space in self.SRC_SKIP_DIRS:
                continue
            for name in sorted(os.listdir(space_dir)):
                mod_dir = os.path.join(space_dir, name)
                if not os.path.isdir(mod_dir) or name.startswith('.'):
                    continue
                files = {}
                for dirpath, dirnames, filenames in os.walk(mod_dir):
                    dirnames[:] = [d for d in dirnames
                                   if d not in self.SRC_SKIP_DIRS and not d.startswith('.')]
                    for fname in sorted(filenames):
                        if fname.startswith('.') or fname in self.SRC_SKIP_FILES:
                            continue
                        if os.path.splitext(fname)[1].lower() in self.SRC_SKIP_EXTS:
                            continue
                        fpath = os.path.join(dirpath, fname)
                        try:
                            if os.path.getsize(fpath) > self.SRC_MAX_FILE_SIZE:
                                n_skipped += 1
                                continue
                            with open(fpath, 'rb') as f:
                                data = f.read()
                        except OSError:
                            n_skipped += 1
                            continue
                        files[os.path.relpath(fpath, mod_dir)] = hashlib.sha256(data).hexdigest()
                        n_files += 1
                if files:
                    blob = _json.dumps(files, sort_keys=True).encode()
                    mods[f'{space}/{name}'] = hashlib.sha256(blob).hexdigest()

        root = hashlib.sha256(_json.dumps(mods, sort_keys=True).encode()).hexdigest() if mods else None
        return {
            'root': root,
            'mods': mods,
            'n_mods': len(mods),
            'n_files': n_files,
            'n_skipped': n_skipped,
            'ms': int((time.time() - t0) * 1000),
        }

    def _root_diff(self, current: dict, committed: dict) -> List[Dict[str, str]]:
        """Modules whose hash differs between two {name: hash} maps."""
        changed = []
        for name in sorted(set(current) | set(committed)):
            cur, exp = current.get(name), committed.get(name)
            if cur == exp:
                continue
            changed.append({'mod': name,
                            'status': 'added' if exp is None else ('removed' if cur is None else 'modified')})
        return changed

    # A forced rehash still can't be hammered: within this window it serves cache.
    ROOT_HASH_MIN_INTERVAL = 5

    def root_hash(self, update=False, max_age=3600) -> Dict[str, Any]:
        """Cached source root hash, compared against the last committed root.

        Recomputes when the cache is older than max_age seconds or update=True;
        otherwise serves the cached value so callers can poll cheaply.
        """
        path = self.path('root_hash.json')
        cached = m.get(path, None)
        have_cache = isinstance(cached, dict) and bool(cached.get('root'))
        age = (m.time() - cached.get('time', 0)) if have_cache else None
        if age is None or age >= max_age or (update and age >= self.ROOT_HASH_MIN_INTERVAL):
            cached = self.src_root_hash()
            cached['time'] = m.time()
            m.put(path, cached)
            age = 0

        committed = m.get(self.path('root_committed.json'), None) or {}
        result = {k: v for k, v in cached.items() if k != 'mods'}
        result['age'] = int(age)
        result['max_age'] = max_age
        result['committed'] = committed.get('root')
        result['committed_time'] = committed.get('time')
        result['committed_cid'] = committed.get('cid')
        result['valid'] = bool(committed.get('root')) and committed['root'] == cached['root']
        result['changed'] = self._root_diff(cached.get('mods', {}), committed.get('mods', {})) if committed else []
        return result

    def commit_root_hash(self, comment=None) -> Dict[str, Any]:
        """Recompute the source root hash and pin it as the committed root.

        Stores the {module: hash} map as a CID and chains it to the previous
        commit, so the node has a signed-off state to compare drift against.
        """
        info = self.src_root_hash()
        if not info.get('root'):
            return {'error': 'no modules found', **info}
        info['time'] = m.time()
        m.put(self.path('root_hash.json'), info)

        prev = m.get(self.path('root_committed.json'), None) or {}
        record = {**info, 'comment': comment, 'prev': prev.get('root'), 'prev_cid': prev.get('cid')}
        record['cid'] = self.put({k: record[k] for k in
                                  ('root', 'mods', 'n_mods', 'n_files', 'time', 'comment', 'prev')})
        m.put(self.path('root_committed.json'), record)
        return {
            'root': record['root'],
            'cid': record['cid'],
            'prev': record['prev'],
            'n_mods': record['n_mods'],
            'n_files': record['n_files'],
            'time': record['time'],
            'ms': record['ms'],
        }

    def dp(self, path: str, key=None) -> str:
        key = self.key_address(key)
        if key != self.key.address:
            path = key + '/' + path
        return m.dp(path)
