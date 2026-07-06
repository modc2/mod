"""Off-chain registry backend using local JSON storage."""

import json
import os
import time

from ..base import RegistryBackend


class OffchainRegistry(RegistryBackend):
    """Local file-based registry. Zero external dependencies."""

    name: str = 'offchain'

    def __init__(self, storage_path=None, **kw):
        self.storage_path = os.path.expanduser(storage_path or '~/.mod/registry')
        os.makedirs(self.storage_path, exist_ok=True)
        self.index_path = os.path.join(self.storage_path, 'index.json')
        self.index = self._load_index()

    def _load_index(self):
        if os.path.exists(self.index_path):
            with open(self.index_path, 'r') as f:
                return json.load(f)
        return {'next_id': 1, 'mods': {}, 'user_mods': {}, 'name_map': {}}

    def _save_index(self):
        with open(self.index_path, 'w') as f:
            json.dump(self.index, f)

    def register(self, name: str, data: dict = None, owner: str = 'local', **kw) -> int:
        if not name:
            raise ValueError('Name is required')
        if not data:
            raise ValueError('Data is required')

        owner_names = self.index.get('name_map', {}).get(owner, {})
        if name in owner_names:
            raise ValueError(f'Name "{name}" already exists for owner {owner}')

        mod_id = self.index['next_id']
        self.index['next_id'] = mod_id + 1
        self.index['mods'][str(mod_id)] = {
            'id': mod_id,
            'owner': owner,
            'name': name,
            'data': data,
            'created_at': time.time(),
            'updated_at': time.time(),
        }

        if owner not in self.index['user_mods']:
            self.index['user_mods'][owner] = []
        self.index['user_mods'][owner].append(mod_id)

        if owner not in self.index['name_map']:
            self.index['name_map'][owner] = {}
        self.index['name_map'][owner][name] = mod_id

        self._save_index()
        return True

    def update(self, mod_id: int, data: dict = None, owner: str = 'local', **kw) -> bool:
        mod = self.index['mods'].get(str(mod_id))
        if not mod:
            raise ValueError(f'Mod {mod_id} does not exist')
        if mod['owner'] != owner:
            raise ValueError('Not mod owner')
        if not data:
            raise ValueError('Data is required')

        mod['data'] = data
        mod['updated_at'] = time.time()
        self._save_index()
        return True

    def remove(self, mod_id: int, owner: str = 'local', **kw) -> bool:
        mod = self.index['mods'].get(str(mod_id))
        if not mod:
            raise ValueError(f'Mod {mod_id} does not exist')
        mod_owner = mod['owner']
        if mod_owner != owner:
            raise ValueError('Not mod owner')

        mod_name = mod['name']
        del self.index['mods'][str(mod_id)]

        if mod_owner in self.index['user_mods']:
            self.index['user_mods'][mod_owner] = [
                m for m in self.index['user_mods'][mod_owner] if m != mod_id
            ]

        if mod_owner in self.index['name_map'] and mod_name in self.index['name_map'][mod_owner]:
            del self.index['name_map'][mod_owner][mod_name]

        self._save_index()
        return True

    def get(self, mod_id: int, **kw) -> dict:
        return self.index['mods'].get(str(mod_id))

    def get_user_mods(self, owner: str, **kw) -> list:
        mod_ids = self.index['user_mods'].get(owner, [])
        return [self.index['mods'][str(mid)] for mid in mod_ids if str(mid) in self.index['mods']]

    def is_name_taken(self, owner: str, name: str, **kw) -> bool:
        return name in self.index.get('name_map', {}).get(owner, {})

    def transfer(self, mod_id: int, new_owner: str, owner: str = 'local', **kw) -> bool:
        mod = self.index['mods'].get(str(mod_id))
        if not mod:
            raise ValueError(f'Mod {mod_id} does not exist')
        old_owner = mod['owner']
        if old_owner != owner:
            raise ValueError('Not mod owner')
        if not new_owner:
            raise ValueError('New owner is required')

        mod_name = mod['name']

        # Check name conflict on new owner
        if mod_name in self.index.get('name_map', {}).get(new_owner, {}):
            raise ValueError(f'Name "{mod_name}" already exists for new owner')

        # Remove from old owner
        if old_owner in self.index['user_mods']:
            self.index['user_mods'][old_owner] = [
                m for m in self.index['user_mods'][old_owner] if m != mod_id
            ]
        if old_owner in self.index['name_map'] and mod_name in self.index['name_map'][old_owner]:
            del self.index['name_map'][old_owner][mod_name]

        # Add to new owner
        if new_owner not in self.index['user_mods']:
            self.index['user_mods'][new_owner] = []
        self.index['user_mods'][new_owner].append(mod_id)

        if new_owner not in self.index['name_map']:
            self.index['name_map'][new_owner] = {}
        self.index['name_map'][new_owner][mod_name] = mod_id

        mod['owner'] = new_owner
        mod['updated_at'] = time.time()

        self._save_index()
        return True

    def list_all(self, **kw) -> list:
        return list(self.index['mods'].values())
