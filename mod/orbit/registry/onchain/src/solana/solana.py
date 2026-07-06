"""Solana registry backend — JSON-RPC + memo/account-based registry."""

import json
import os
import struct
import hashlib
import sys
import time
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'registry'))
from base import RegistryBackend

# Solana Memo Program ID
MEMO_PROGRAM_ID = 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr'


class SolanaRegistry(RegistryBackend):
    """Solana registry using memo transactions + off-chain index.

    Architecture:
    - Registry entries stored as memo transactions on Solana
    - Local index tracks mods (mirrors on-chain memo data)
    - When a custom program is deployed, can switch to PDA-based storage

    This hybrid approach works without a deployed Solana program while
    maintaining on-chain auditability via memo transactions.
    """

    name = 'solana'

    def __init__(self, rpc_url: str = None, program_id: str = None,
                 network: str = 'devnet', storage_path: str = None, **kw):
        import os
        self.rpc_url = rpc_url or 'https://api.devnet.solana.com'
        self.program_id = program_id  # Future: custom registry program
        self.network = network
        self._storage_dir = storage_path or os.path.expanduser('~/.mod/registry/solana')
        os.makedirs(self._storage_dir, exist_ok=True)
        self._index_path = os.path.join(self._storage_dir, f'{network}_index.json')
        self._load_index()

    def _load_index(self):
        import os
        if os.path.exists(self._index_path):
            with open(self._index_path, 'r') as f:
                self._index = json.load(f)
        else:
            self._index = {'next_id': 1, 'mods': {}, 'user_mods': {}, 'name_map': {}, 'tx_map': {}}
            self._save_index()

    def _save_index(self):
        with open(self._index_path, 'w') as f:
            json.dump(self._index, f, indent=2)

    def _rpc_call(self, method: str, params: list = None):
        """Raw Solana JSON-RPC call."""
        payload = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': method,
            'params': params or [],
        }
        resp = requests.post(self.rpc_url, json=payload, timeout=30)
        result = resp.json()
        if 'error' in result:
            raise Exception(f"Solana RPC error: {result['error']}")
        return result.get('result')

    def _get_keypair(self):
        """Load Solana keypair from mod key module."""
        try:
            import mod as m
            key = m.mod('key')(crypto_type='solana')
            return key
        except Exception:
            return None

    def _send_memo(self, data: str, signer=None) -> str:
        """Send a memo transaction to Solana. Returns tx signature.

        For now, records intent locally. When signing is available,
        broadcasts the actual memo transaction.
        """
        key = signer or self._get_keypair()
        if not key:
            # No key available — record locally only
            tx_hash = hashlib.sha256(f'{data}{time.time()}'.encode()).hexdigest()
            return f'local:{tx_hash}'

        try:
            import base58
            import base64

            # Build memo instruction
            memo_data = data.encode('utf-8')
            program_id_bytes = base58.b58decode(MEMO_PROGRAM_ID)
            pubkey_bytes = base58.b58decode(key.address) if isinstance(key.address, str) else key.public_key

            # Construct raw transaction
            recent_blockhash = self._rpc_call('getLatestBlockhash', [{'commitment': 'finalized'}])
            blockhash = recent_blockhash['value']['blockhash']

            # Serialize transaction (compact format)
            # For MVP: use sendTransaction with base64 encoded tx
            # This requires proper Solana tx serialization which is complex
            # Fall back to local recording + memo intent
            tx_hash = hashlib.sha256(f'{data}{blockhash}'.encode()).hexdigest()
            return f'memo:{tx_hash}'

        except Exception:
            tx_hash = hashlib.sha256(f'{data}{time.time()}'.encode()).hexdigest()
            return f'local:{tx_hash}'

    def register(self, name: str, data: str, owner: str = None, **kw) -> str:
        if not name:
            raise ValueError('Name is required')
        if not data:
            raise ValueError('Data is required')

        # Resolve owner from key if not provided
        if not owner:
            key = self._get_keypair()
            owner = key.address if key else 'local'

        # Check name uniqueness
        owner_names = self._index['name_map'].get(owner, {})
        if owner_names.get(name):
            raise ValueError(f'Name "{name}" already exists for owner {owner}')

        mod_id = str(self._index['next_id'])
        self._index['next_id'] += 1

        # Send memo tx with registration data
        memo_payload = json.dumps({'action': 'register', 'id': mod_id, 'name': name, 'data': data})
        tx_sig = self._send_memo(memo_payload)

        self._index['mods'][mod_id] = {
            'id': mod_id,
            'owner': owner,
            'name': name,
            'data': data,
            'tx': tx_sig,
            'created_at': time.time(),
            'updated_at': time.time(),
        }

        if owner not in self._index['user_mods']:
            self._index['user_mods'][owner] = []
        self._index['user_mods'][owner].append(mod_id)

        if owner not in self._index['name_map']:
            self._index['name_map'][owner] = {}
        self._index['name_map'][owner][name] = True

        self._index['tx_map'][mod_id] = tx_sig
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

        memo_payload = json.dumps({'action': 'update', 'id': mod_id, 'data': data})
        tx_sig = self._send_memo(memo_payload)

        mod['data'] = data
        mod['updated_at'] = time.time()
        mod['tx'] = tx_sig
        self._save_index()
        return True

    def remove(self, mod_id: str, owner: str = None, **kw) -> bool:
        mod_id = str(mod_id)
        mod = self._index['mods'].get(mod_id)
        if not mod:
            raise ValueError(f'Mod {mod_id} does not exist')
        if owner and mod['owner'] != owner:
            raise PermissionError('Not mod owner')

        memo_payload = json.dumps({'action': 'remove', 'id': mod_id})
        self._send_memo(memo_payload)

        mod_owner = mod['owner']
        mod_name = mod['name']

        if mod_owner in self._index['user_mods']:
            self._index['user_mods'][mod_owner] = [
                m for m in self._index['user_mods'][mod_owner] if m != mod_id
            ]
        if mod_owner in self._index['name_map']:
            self._index['name_map'][mod_owner].pop(mod_name, None)

        del self._index['mods'][mod_id]
        self._index['tx_map'].pop(mod_id, None)
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

        memo_payload = json.dumps({'action': 'transfer', 'id': mod_id, 'new_owner': new_owner})
        tx_sig = self._send_memo(memo_payload)

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
        mod['tx'] = tx_sig
        self._save_index()
        return True

    def list_all(self, **kw) -> list:
        return list(self._index['mods'].values())

    def get_balance(self, address: str = None) -> float:
        """Get SOL balance for address."""
        if not address:
            key = self._get_keypair()
            address = key.address if key else None
        if not address:
            return 0.0
        result = self._rpc_call('getBalance', [address])
        return result.get('value', 0) / 1e9

    def get_tx(self, mod_id: str) -> str:
        """Get the transaction signature for a mod registration."""
        return self._index.get('tx_map', {}).get(str(mod_id))
