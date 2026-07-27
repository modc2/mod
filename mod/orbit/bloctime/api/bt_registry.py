"""
BlocTime instance registry — the marketplace store.

Every deployed BlocTime (the official one plus anyone's fork) is one entry:
    { id, name, description, chainId, rpc, bloctime, nativeToken,
      owner, official, explorer, createdAt }

Entries live off-tree in ~/.mod/bloctime/registry.json; the official
instance is synthesized from the module's config.json so it is always
listed first and never duplicated. Registration is verified on-chain:
the address must actually respond like a BlocTime contract, and its
owner() is recorded as the authoritative instance owner.
"""

import json
import os
import re
import time
from pathlib import Path

MODULE_DIR = Path(__file__).parent.parent
STORE_DIR = Path(os.path.expanduser('~/.mod/bloctime'))
REGISTRY_PATH = STORE_DIR / 'registry.json'

EXPLORERS = {
    '1': 'https://etherscan.io',
    '8453': 'https://basescan.org',
    '84532': 'https://sepolia.basescan.org',
    '11155111': 'https://sepolia.etherscan.io',
}

# Minimal ABI: just what verification and live stats need.
PROBE_ABI = json.loads('''[
  {"inputs":[],"name":"totalBlocTime","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"totalSupply","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"nextStakeId","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"nativeToken","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"owner","outputs":[{"type":"address"}],"stateMutability":"view","type":"function"}
]''')


def explorer_for(chain_id, address):
    base = EXPLORERS.get(str(chain_id), '')
    return f"{base}/address/{address}" if base and address else ''


def slugify(name):
    slug = re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-')
    return slug or 'instance'


def _load():
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH) as f:
            return json.load(f)
    return []


def _save(entries):
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_PATH, 'w') as f:
        json.dump(entries, f, indent=2)


def official_entry():
    """The module's own deployment from config.json — always listed first."""
    cfg_path = MODULE_DIR / 'config.json'
    if not cfg_path.exists():
        return None
    with open(cfg_path) as f:
        cfg = json.load(f)
    network = cfg.get('network', 'testnet')
    contracts = cfg.get('contracts', {}).get(network, {})
    if not contracts.get('bloctime'):
        return None
    chain_id = contracts.get('chainId', '')
    return {
        'id': 'official',
        'name': 'BlocTime (official)',
        'description': 'The original BlocTime deployment run by this module.',
        'chainId': str(chain_id),
        'rpc': contracts.get('url', ''),
        'bloctime': contracts['bloctime'],
        'nativeToken': contracts.get('nativeToken', ''),
        'owner': '',
        'official': True,
        'explorer': explorer_for(chain_id, contracts['bloctime']),
        'createdAt': '',
    }


def list_instances():
    entries = []
    official = official_entry()
    if official:
        entries.append(official)
    entries.extend(_load())
    return entries


def get_instance(instance_id):
    for e in list_instances():
        if e['id'] == instance_id:
            return e
    return None


def verify_instance(rpc, bloctime, native_token=None):
    """Probe rpc/address on-chain; returns verified facts or raises ValueError."""
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
    if not w3.is_connected():
        raise ValueError(f"RPC unreachable: {rpc}")
    addr = Web3.to_checksum_address(bloctime)
    if w3.eth.get_code(addr) in (b'', b'\x00'):
        raise ValueError(f"No contract code at {addr}")
    c = w3.eth.contract(address=addr, abi=PROBE_ABI)
    try:
        total_bloctime = c.functions.totalBlocTime().call()
        chain_token = c.functions.nativeToken().call()
    except Exception as e:
        raise ValueError(f"Address does not behave like a BlocTime contract: {e}")
    if native_token and Web3.to_checksum_address(native_token) != Web3.to_checksum_address(chain_token):
        raise ValueError("nativeToken mismatch: on-chain token differs from the one provided")
    try:
        owner = c.functions.owner().call()
    except Exception:
        owner = ''
    return {
        'chainId': str(w3.eth.chain_id),
        'nativeToken': chain_token,
        'owner': owner,
        'totalBlocTime': str(total_bloctime),
    }


def add_instance(name, rpc, bloctime, native_token=None, description='', verify=True):
    """Verify on-chain and persist a new marketplace entry. Returns the entry."""
    if not name or not rpc or not bloctime:
        raise ValueError("name, rpc and bloctime address are required")
    facts = verify_instance(rpc, bloctime, native_token) if verify else {
        'chainId': '', 'nativeToken': native_token or '', 'owner': '', 'totalBlocTime': '0',
    }
    entries = _load()
    base = slugify(name)
    existing_ids = {e['id'] for e in entries} | {'official'}
    slug, n = base, 2
    while slug in existing_ids:
        slug, n = f"{base}-{n}", n + 1
    for e in list_instances():
        if (e['bloctime'].lower() == bloctime.lower()
                and str(e['chainId']) == facts['chainId']):
            raise ValueError(f"Already registered as '{e['id']}'")
    entry = {
        'id': slug,
        'name': str(name),
        'description': str(description or ''),
        'chainId': facts['chainId'],
        'rpc': rpc,
        'bloctime': bloctime,
        'nativeToken': facts['nativeToken'],
        'owner': facts['owner'],
        'official': False,
        'explorer': explorer_for(facts['chainId'], bloctime),
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    entries.append(entry)
    _save(entries)
    return entry


def remove_instance(instance_id):
    if instance_id == 'official':
        raise ValueError("Cannot remove the official instance")
    entries = _load()
    kept = [e for e in entries if e['id'] != instance_id]
    if len(kept) == len(entries):
        raise ValueError(f"Unknown instance '{instance_id}'")
    _save(kept)
    return {'removed': instance_id}


def instance_stats(entry):
    """Best-effort live stats for one instance (None on any failure)."""
    from web3 import Web3
    try:
        w3 = Web3(Web3.HTTPProvider(entry['rpc'], request_kwargs={'timeout': 6}))
        c = w3.eth.contract(
            address=Web3.to_checksum_address(entry['bloctime']), abi=PROBE_ABI,
        )
        return {
            'totalBlocTime': str(c.functions.totalBlocTime().call()),
            'totalSupply': str(c.functions.totalSupply().call()),
            'totalStakes': c.functions.nextStakeId().call(),
        }
    except Exception:
        return None
