"""NEAR registry backend — calls Registry contract on NEAR via near-cli-rs."""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'registry'))
from base import RegistryBackend

NEAR_CLI = "npx near-cli-rs"


def _run(cmd, cwd=None, timeout=120):
    """Run a shell command and return stdout."""
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result.stdout.strip()


class NearRegistry(RegistryBackend):
    """NEAR Protocol registry backend using near-cli-rs to call the contract."""

    name = 'near'

    def __init__(self, rpc_url: str = None, account: str = None,
                 network: str = 'testnet', signer: str = None,
                 storage_path: str = None, **kw):
        self.rpc_url = rpc_url or f'https://rpc.{network}.near.org'
        self.account = account  # Contract account (e.g. mod-registry.testnet)
        self.network = network
        self.signer = signer  # Account that signs txs
        self._storage_dir = storage_path or os.path.expanduser('~/.mod/registry/near')
        os.makedirs(self._storage_dir, exist_ok=True)
        self._index_path = os.path.join(self._storage_dir, f'{network}_index.json')
        self._load_index()

        # Try loading account from deployment.json if not provided
        if not self.account:
            deploy_file = os.path.join(
                os.path.dirname(__file__), 'deployment.json'
            )
            if os.path.exists(deploy_file):
                with open(deploy_file) as f:
                    info = json.load(f)
                self.account = info.get('account')

    def _load_index(self):
        """Local index mirrors on-chain state for fast queries."""
        if os.path.exists(self._index_path):
            with open(self._index_path, 'r') as f:
                self._index = json.load(f)
        else:
            self._index = {'next_id': 1, 'mods': {}, 'user_mods': {}, 'name_map': {}, 'tx_map': {}}
            self._save_index()

    def _save_index(self):
        with open(self._index_path, 'w') as f:
            json.dump(self._index, f, indent=2)

    def _call(self, method, args=None, gas="30 Tgas", deposit="0 NEAR"):
        """Call a state-changing contract method."""
        if not self.account:
            raise ValueError("No NEAR contract account configured")
        args_json = json.dumps(args or {})
        signer_part = f'sign-as "{self.signer}"' if self.signer else 'sign-with-keychain'
        cmd = (
            f'{NEAR_CLI} contract call-function as-transaction '
            f'"{self.account}" {method} json-args \'{args_json}\' '
            f"prepaid-gas '{gas}' attached-deposit '{deposit}' "
            f"{signer_part} network-config {self.network} send"
        )
        return _run(cmd)

    def _view(self, method, args=None):
        """Call a read-only view method."""
        if not self.account:
            raise ValueError("No NEAR contract account configured")
        args_json = json.dumps(args or {})
        cmd = (
            f'{NEAR_CLI} contract call-function as-read-only '
            f'"{self.account}" {method} json-args \'{args_json}\' '
            f"network-config {self.network} now"
        )
        return _run(cmd)

    def register(self, name: str, data: str, owner: str = None, **kw) -> str:
        if not name:
            raise ValueError('Name is required')
        if not data:
            raise ValueError('Data is required')
        owner = owner or self.signer or 'local'

        # Check local name uniqueness
        owner_names = self._index['name_map'].get(owner, {})
        if owner_names.get(name):
            raise ValueError(f'Name "{name}" already exists for owner {owner}')

        # Call contract if available
        tx_result = None
        if self.account:
            try:
                tx_result = self._call('register_mod', {'name': name, 'data': data})
            except Exception:
                pass

        mod_id = str(self._index['next_id'])
        self._index['next_id'] += 1

        self._index['mods'][mod_id] = {
            'id': mod_id,
            'owner': owner,
            'name': name,
            'data': data,
            'tx': tx_result,
            'created_at': time.time(),
            'updated_at': time.time(),
        }

        if owner not in self._index['user_mods']:
            self._index['user_mods'][owner] = []
        self._index['user_mods'][owner].append(mod_id)

        if owner not in self._index['name_map']:
            self._index['name_map'][owner] = {}
        self._index['name_map'][owner][name] = True

        self._save_index()
        return mod_id

    def update(self, mod_id: str, data: str, owner: str = None, **kw) -> bool:
        mod_id = str(mod_id)
        mod = self._index['mods'].get(mod_id)
        if not mod:
            raise ValueError(f'Mod {mod_id} does not exist')
        if owner and mod['owner'] != owner:
            raise PermissionError('Not mod owner')
        if not data:
            raise ValueError('Data is required')

        if self.account:
            try:
                self._call('update_mod', {'mod_id': int(mod_id), 'data': data})
            except Exception:
                pass

        mod['data'] = data
        mod['updated_at'] = time.time()
        self._save_index()
        return True

    def remove(self, mod_id: str, owner: str = None, **kw) -> bool:
        mod_id = str(mod_id)
        mod = self._index['mods'].get(mod_id)
        if not mod:
            raise ValueError(f'Mod {mod_id} does not exist')
        if owner and mod['owner'] != owner:
            raise PermissionError('Not mod owner')

        if self.account:
            try:
                self._call('remove_mod', {'mod_id': int(mod_id)})
            except Exception:
                pass

        mod_owner = mod['owner']
        mod_name = mod['name']

        if mod_owner in self._index['user_mods']:
            self._index['user_mods'][mod_owner] = [
                m for m in self._index['user_mods'][mod_owner] if m != mod_id
            ]
        if mod_owner in self._index['name_map']:
            self._index['name_map'][mod_owner].pop(mod_name, None)

        del self._index['mods'][mod_id]
        self._index.get('tx_map', {}).pop(mod_id, None)
        self._save_index()
        return True

    def get(self, mod_id: str, **kw) -> dict:
        mod_id = str(mod_id)
        return self._index['mods'].get(mod_id)

    def get_user_mods(self, owner: str, **kw) -> list:
        mod_ids = self._index['user_mods'].get(owner, [])
        return [self._index['mods'][mid] for mid in mod_ids if mid in self._index['mods']]

    def is_name_taken(self, owner: str, name: str, **kw) -> bool:
        return bool(self._index['name_map'].get(owner, {}).get(name))

    def transfer(self, mod_id: str, new_owner: str, owner: str = None, **kw) -> bool:
        mod_id = str(mod_id)
        mod = self._index['mods'].get(mod_id)
        if not mod:
            raise ValueError(f'Mod {mod_id} does not exist')
        if owner and mod['owner'] != owner:
            raise PermissionError('Not mod owner')
        if not new_owner:
            raise ValueError('New owner is required')

        if self.is_name_taken(new_owner, mod['name']):
            raise ValueError(f'Name "{mod["name"]}" already exists for new owner')

        if self.account:
            try:
                self._call('transfer_ownership', {
                    'mod_id': int(mod_id), 'new_owner': new_owner
                })
            except Exception:
                pass

        old_owner = mod['owner']
        mod_name = mod['name']

        if old_owner in self._index['user_mods']:
            self._index['user_mods'][old_owner] = [
                m for m in self._index['user_mods'][old_owner] if m != mod_id
            ]
        if old_owner in self._index['name_map']:
            self._index['name_map'][old_owner].pop(mod_name, None)

        if new_owner not in self._index['user_mods']:
            self._index['user_mods'][new_owner] = []
        self._index['user_mods'][new_owner].append(mod_id)

        if new_owner not in self._index['name_map']:
            self._index['name_map'][new_owner] = {}
        self._index['name_map'][new_owner][mod_name] = True

        mod['owner'] = new_owner
        mod['updated_at'] = time.time()
        self._save_index()
        return True

    def list_all(self, **kw) -> list:
        return list(self._index['mods'].values())
