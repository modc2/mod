"""
Which chain, and how to reach it.

A network here is four things: a chain id, an RPC url, whether it is a
**testnet**, and where a human goes to look at a hash. The last two are not
decoration — the testnet flag is the switch every write path consults before it
will spend anything, and the explorer url is the only honest way to answer
"did that actually happen" from a console.

The defaults are public RPCs, which is what makes this module useful with no
configuration at all; they are also rate-limited and occasionally behind, so
every one of them can be replaced three ways, most specific first:

    ETH_RPC_<NETWORK>          env, e.g. ETH_RPC_MAINNET=https://…/v2/<key>
    ~/.mod/eth/networks.json   what the owner adds through the API, and any
                               `rpc` override for a built-in
    the built-in default      the table below

`local` is first in the list on purpose. An anvil/hardhat node on :8545 is the
right place to learn what a deploy does, and it is the only network here where
being wrong is free.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE = Path(os.path.expanduser(os.environ.get('ETH_DIR', '~/.mod/eth')))
NETWORKS_PATH = STATE / 'networks.json'

# name → chain id, rpc, testnet, explorer, native currency, symbol
BUILTIN: Dict[str, Dict[str, Any]] = {
    'local': {
        'chain_id': 31337, 'rpc': 'http://127.0.0.1:8545', 'testnet': True,
        'explorer': None, 'currency': 'ETH', 'label': 'anvil / hardhat (local)',
    },
    'mainnet': {
        'chain_id': 1, 'rpc': 'https://ethereum-rpc.publicnode.com',
        'testnet': False, 'explorer': 'https://etherscan.io',
        'currency': 'ETH', 'label': 'Ethereum',
    },
    'sepolia': {
        'chain_id': 11155111, 'rpc': 'https://ethereum-sepolia-rpc.publicnode.com',
        'testnet': True, 'explorer': 'https://sepolia.etherscan.io',
        'currency': 'ETH', 'label': 'Ethereum Sepolia',
    },
    'holesky': {
        'chain_id': 17000, 'rpc': 'https://ethereum-holesky-rpc.publicnode.com',
        'testnet': True, 'explorer': 'https://holesky.etherscan.io',
        'currency': 'ETH', 'label': 'Ethereum Holesky',
    },
    'base': {
        'chain_id': 8453, 'rpc': 'https://base-rpc.publicnode.com',
        'testnet': False, 'explorer': 'https://basescan.org',
        'currency': 'ETH', 'label': 'Base',
    },
    'base-sepolia': {
        'chain_id': 84532, 'rpc': 'https://base-sepolia-rpc.publicnode.com',
        'testnet': True, 'explorer': 'https://sepolia.basescan.org',
        'currency': 'ETH', 'label': 'Base Sepolia',
    },
    'optimism': {
        'chain_id': 10, 'rpc': 'https://optimism-rpc.publicnode.com',
        'testnet': False, 'explorer': 'https://optimistic.etherscan.io',
        'currency': 'ETH', 'label': 'OP Mainnet',
    },
    'op-sepolia': {
        'chain_id': 11155420, 'rpc': 'https://optimism-sepolia-rpc.publicnode.com',
        'testnet': True, 'explorer': 'https://sepolia-optimism.etherscan.io',
        'currency': 'ETH', 'label': 'OP Sepolia',
    },
    'arbitrum': {
        'chain_id': 42161, 'rpc': 'https://arbitrum-one-rpc.publicnode.com',
        'testnet': False, 'explorer': 'https://arbiscan.io',
        'currency': 'ETH', 'label': 'Arbitrum One',
    },
    'arbitrum-sepolia': {
        'chain_id': 421614, 'rpc': 'https://arbitrum-sepolia-rpc.publicnode.com',
        'testnet': True, 'explorer': 'https://sepolia.arbiscan.io',
        'currency': 'ETH', 'label': 'Arbitrum Sepolia',
    },
    'polygon': {
        'chain_id': 137, 'rpc': 'https://polygon-bor-rpc.publicnode.com',
        'testnet': False, 'explorer': 'https://polygonscan.com',
        'currency': 'POL', 'label': 'Polygon PoS', 'poa': True,
    },
    'polygon-amoy': {
        'chain_id': 80002, 'rpc': 'https://polygon-amoy-bor-rpc.publicnode.com',
        'testnet': True, 'explorer': 'https://amoy.polygonscan.com',
        'currency': 'POL', 'label': 'Polygon Amoy', 'poa': True,
    },
    'bsc': {
        'chain_id': 56, 'rpc': 'https://bsc-rpc.publicnode.com',
        'testnet': False, 'explorer': 'https://bscscan.com',
        'currency': 'BNB', 'label': 'BNB Smart Chain', 'poa': True,
    },
    'avalanche': {
        'chain_id': 43114, 'rpc': 'https://avalanche-c-chain-rpc.publicnode.com',
        'testnet': False, 'explorer': 'https://snowtrace.io',
        'currency': 'AVAX', 'label': 'Avalanche C-Chain',
    },
}

DEFAULT = os.environ.get('ETH_NETWORK', 'local')


class ChainError(Exception):
    """No such network, or it would not answer."""


# ── the registry ─────────────────────────────────────────────────────

def custom() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(NETWORKS_PATH.read_text()) or {}
    except Exception:
        return {}


def save_custom(table: Dict[str, Dict[str, Any]]) -> None:
    NETWORKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    NETWORKS_PATH.write_text(json.dumps(table, indent=2, sort_keys=True))


def env_rpc(name: str) -> Optional[str]:
    key = 'ETH_RPC_' + name.upper().replace('-', '_')
    return os.environ.get(key) or None


def table() -> Dict[str, Dict[str, Any]]:
    """Built-ins, then anything the owner added, then the environment."""
    out: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in BUILTIN.items()}
    for name, spec in custom().items():
        merged = dict(out.get(name, {}))
        merged.update({k: v for k, v in (spec or {}).items() if v is not None})
        merged['custom'] = True
        out[name] = merged
    for name, spec in out.items():
        override = env_rpc(name)
        if override:
            spec['rpc'] = override
            spec['rpc_source'] = 'env'
        elif spec.get('custom'):
            spec['rpc_source'] = 'owner'
        else:
            spec['rpc_source'] = 'default'
    return out


def resolve(network: Optional[str]) -> Dict[str, Any]:
    """A network name (or a bare chain id) → its spec, `name` included."""
    name = (network or DEFAULT or 'local').strip()
    known = table()
    if name in known:
        return {'name': name, **known[name]}
    if name.isdigit():                      # a chain id is a fine way to ask
        wanted = int(name)
        for key, spec in known.items():
            if spec.get('chain_id') == wanted:
                return {'name': key, **spec}
    if name.startswith('http://') or name.startswith('https://'):
        return {'name': name, 'rpc': name, 'chain_id': None, 'testnet': None,
                'explorer': None, 'currency': 'ETH', 'label': 'ad-hoc rpc',
                'rpc_source': 'request'}
    raise ChainError(f'unknown network {name!r} — known: {", ".join(sorted(known))}')


def add(name: str, rpc: str, chain_id: Optional[int] = None,
        testnet: bool = True, explorer: Optional[str] = None,
        currency: str = 'ETH', label: Optional[str] = None) -> Dict[str, Any]:
    name = name.strip().lower()
    if not name or ' ' in name:
        raise ChainError('a network name is one lowercase word')
    if not rpc.startswith('http'):
        raise ChainError('rpc must be an http(s) url')
    saved = custom()
    saved[name] = {'rpc': rpc, 'chain_id': chain_id, 'testnet': bool(testnet),
                   'explorer': explorer, 'currency': currency,
                   'label': label or name}
    save_custom(saved)
    return {'name': name, **saved[name], 'custom': True}


def remove(name: str) -> bool:
    saved = custom()
    if name not in saved:
        return False
    saved.pop(name)
    save_custom(saved)
    return True


# ── clients ──────────────────────────────────────────────────────────

_clients: Dict[str, Any] = {}


def client(network: Optional[str] = None):
    """A Web3 bound to that network, cached per rpc url.

    Cached because a fresh HTTPProvider per request means a fresh connection
    pool per request, and the public RPCs notice.
    """
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware

    spec = resolve(network)
    rpc = spec['rpc']
    w3 = _clients.get(rpc)
    if w3 is None:
        timeout = float(os.environ.get('ETH_RPC_TIMEOUT', 30))
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': timeout}))
        # Chains with >32-byte extraData (PoA and friends) break the default
        # block formatter. Injecting it where it is not needed is harmless.
        if spec.get('poa') or spec.get('chain_id') in (56, 137, 80002, 97):
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        _clients[rpc] = w3
    return w3


def reachable(network: Optional[str] = None) -> Dict[str, Any]:
    """Is it up, and is it the chain it claims to be."""
    spec = resolve(network)
    out = {'network': spec['name'], 'rpc': spec['rpc'],
           'chain_id': spec.get('chain_id'), 'testnet': spec.get('testnet'),
           'explorer': spec.get('explorer'), 'currency': spec.get('currency', 'ETH'),
           'label': spec.get('label'), 'rpc_source': spec.get('rpc_source'),
           'ok': False}
    try:
        w3 = client(network)
        actual = w3.eth.chain_id
        out['ok'] = True
        out['chain_id'] = actual
        out['block'] = w3.eth.block_number
        if spec.get('chain_id') and spec['chain_id'] != actual:
            # A wrong chain id behind a right-looking name is how a testnet
            # deploy ends up on something that costs money. Say so loudly.
            out['ok'] = False
            out['error'] = (f'rpc reports chain {actual}, but {spec["name"]} is '
                            f'{spec["chain_id"]} — check the rpc url')
    except Exception as e:
        out['error'] = f'{type(e).__name__}: {e}'
    return out


def explorer_link(spec: Dict[str, Any], kind: str, value: str) -> Optional[str]:
    base = (spec or {}).get('explorer')
    if not base or not value:
        return None
    path = {'tx': 'tx', 'address': 'address', 'block': 'block',
            'token': 'token'}.get(kind, kind)
    return f'{base.rstrip("/")}/{path}/{value}'


def is_mainnet(spec: Dict[str, Any]) -> bool:
    """Anything not explicitly a testnet is treated as real money."""
    return spec.get('testnet') is False


def summary() -> List[Dict[str, Any]]:
    out = []
    for name, spec in table().items():
        out.append({'name': name, **spec})
    out.sort(key=lambda s: (not s.get('testnet'), s['name']))
    return out
