"""
starknet/chain.py — Starknet as plain functions, python stdlib only.

Talks JSON-RPC to Starknet full nodes with a failover pool of free public
endpoints. Your own node always comes first: set STARKNET_RPC and no third
party is ever contacted. The starknet_keccak entry-point hash is computed
in-tree with a pure-Python keccak-256, so there are zero dependencies.

Everything here is importable on its own — other modules can
`from chain import rpc, call, balance` without touching the HTTP layer.
"""

import json
import os
import urllib.request
import urllib.error

# ---------------------------------------------------------------- keccak-256

_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
_MASK = (1 << 64) - 1


def _rol(v, n):
    return ((v << n) | (v >> (64 - n))) & _MASK


def _keccak_f(a):
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        a = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) for y in range(5)]
             for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak256(data: bytes) -> bytes:
    """Original Keccak-256 (0x01 padding — NOT sha3_256's 0x06)."""
    rate = 136
    a = [[0] * 5 for _ in range(5)]
    padded = data + b'\x01' + b'\x00' * (rate - 1 - len(data) % rate)
    padded = padded[:len(padded) - 1] + bytes([padded[-1] | 0x80])
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[i * 8:i * 8 + 8], 'little')
            a[i % 5][i // 5] ^= lane
        a = _keccak_f(a)
    out = b''
    for i in range(4):
        out += a[i % 5][i // 5].to_bytes(8, 'little')
    return out


def selector(name: str) -> str:
    """starknet_keccak of an entry-point name: keccak256 masked to 250 bits."""
    h = int.from_bytes(keccak256(name.encode()), 'big') & ((1 << 250) - 1)
    return hex(h)


# ---------------------------------------------------------------- constants

NETWORKS = {
    'mainnet': [
        'https://starknet-rpc.publicnode.com',
        'https://api.zan.top/public/starknet-mainnet',
    ],
    'sepolia': [
        'https://starknet-sepolia-rpc.publicnode.com',
        'https://api.zan.top/public/starknet-sepolia',
    ],
}

# Canonical token contracts (ETH and STRK share addresses across networks).
TOKENS = {
    'eth':  {'address': '0x049d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7', 'decimals': 18},
    'strk': {'address': '0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d', 'decimals': 18},
    'usdc': {'address': '0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8', 'decimals': 6},
}

DEFAULT_NETWORK = os.environ.get('STARKNET_NETWORK', 'mainnet')
TIMEOUT = int(os.environ.get('STARKNET_TIMEOUT', '15'))

_last_good = {}  # network -> endpoint that answered most recently


def endpoints(network=None):
    """RPC pool for a network; STARKNET_RPC (your own node) always first."""
    network = network or DEFAULT_NETWORK
    pool = list(NETWORKS.get(network, []))
    own = os.environ.get('STARKNET_RPC')
    if own:
        pool.insert(0, own)
    good = _last_good.get(network)
    if good in pool:
        pool.remove(good)
        pool.insert(0, good)
    return pool


# ---------------------------------------------------------------- transport

def rpc(method, params=None, network=None):
    """JSON-RPC call with endpoint failover. Raises on total failure."""
    network = network or DEFAULT_NETWORK
    body = json.dumps({'jsonrpc': '2.0', 'id': 1,
                       'method': method, 'params': params or []}).encode()
    errors = []
    for url in endpoints(network):
        req = urllib.request.Request(url, data=body,
                                     headers={'Content-Type': 'application/json',
                                              'User-Agent': 'mod-starknet/0.1'})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                out = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors.append(f'{url}: {e}')
            continue
        if 'error' in out:
            # A real node answered with a protocol error — that is the answer.
            _last_good[network] = url
            raise RpcError(out['error'], url)
        _last_good[network] = url
        return out.get('result')
    raise ConnectionError(f'no {network} RPC endpoint reachable: {errors}')


class RpcError(Exception):
    def __init__(self, err, url):
        self.code = err.get('code')
        self.data = err.get('data')
        self.url = url
        super().__init__(err.get('message', str(err)))


# ---------------------------------------------------------------- felt utils

def felt(v) -> str:
    """Normalize an int / decimal string / hex string to a 0x felt."""
    if isinstance(v, int):
        return hex(v)
    s = str(v).strip()
    return hex(int(s, 16)) if s.lower().startswith('0x') else hex(int(s))


def uint256(low, high) -> int:
    return int(str(low), 16) + (int(str(high), 16) << 128)


def _block_id(block='latest'):
    if block in ('latest', 'pending'):
        return block
    s = str(block)
    if s.startswith('0x'):
        return {'block_hash': s}
    return {'block_number': int(s)}


# ---------------------------------------------------------------- reads

def chain_id(network=None):
    cid = rpc('starknet_chainId', network=network)
    return {'chain_id': cid,
            'name': bytes.fromhex(cid[2:].zfill(2)).decode('ascii', 'replace')}


def spec_version(network=None):
    return rpc('starknet_specVersion', network=network)


def block_number(network=None):
    return rpc('starknet_blockNumber', network=network)


def block(block_id='latest', full=False, network=None):
    method = 'starknet_getBlockWithTxs' if full else 'starknet_getBlockWithTxHashes'
    return rpc(method, [_block_id(block_id)], network=network)


def tx(hash, network=None):
    return rpc('starknet_getTransactionByHash', [felt(hash)], network=network)


def receipt(hash, network=None):
    return rpc('starknet_getTransactionReceipt', [felt(hash)], network=network)


def nonce(address, network=None):
    return rpc('starknet_getNonce', ['latest', felt(address)], network=network)


def class_hash(address, network=None):
    return rpc('starknet_getClassHashAt', ['latest', felt(address)], network=network)


def storage(address, key, network=None):
    return rpc('starknet_getStorageAt', [felt(address), felt(key), 'latest'],
               network=network)


def call(contract, entrypoint=None, calldata=None, entry_selector=None,
         block_id='latest', network=None):
    """starknet_call: read any contract. entrypoint is a name ('balanceOf')
    hashed in-tree, or pass an explicit entry_selector felt."""
    sel = felt(entry_selector) if entry_selector else selector(entrypoint)
    request = {'contract_address': felt(contract),
               'entry_point_selector': sel,
               'calldata': [felt(c) for c in (calldata or [])]}
    return rpc('starknet_call', [request, _block_id(block_id)], network=network)


def balance(address, token='eth', network=None):
    """ERC-20 balance. token is eth|strk|usdc or any contract address."""
    t = TOKENS.get(str(token).lower())
    contract = t['address'] if t else felt(token)
    decimals = t['decimals'] if t else None
    out = call(contract, 'balanceOf', [address], network=network)
    raw = uint256(out[0], out[1]) if len(out) >= 2 else int(str(out[0]), 16)
    if decimals is None:
        try:
            decimals = int(str(call(contract, 'decimals', network=network)[0]), 16)
        except Exception:
            decimals = 18
    return {'address': felt(address), 'token': str(token).lower(),
            'contract': contract, 'raw': str(raw),
            'amount': raw / 10 ** decimals, 'decimals': decimals}


def account(address, network=None):
    """One view of an account: token balances, nonce, deployed class."""
    out = {'address': felt(address), 'network': network or DEFAULT_NETWORK,
           'balances': {}}
    for name in ('eth', 'strk'):
        try:
            b = balance(address, name, network=network)
            out['balances'][name] = {'amount': b['amount'], 'raw': b['raw']}
        except Exception as e:
            out['balances'][name] = {'error': str(e)}
    try:
        out['nonce'] = int(str(nonce(address, network=network)), 16)
        out['class_hash'] = class_hash(address, network=network)
        out['deployed'] = True
    except RpcError as e:
        out['deployed'] = False
        out['note'] = str(e)
    return out


def status(network=None):
    network = network or DEFAULT_NETWORK
    cid = chain_id(network=network)
    return {'network': network,
            'chain_id': cid['chain_id'], 'chain': cid['name'],
            'block_number': block_number(network=network),
            'spec_version': spec_version(network=network),
            'rpc': _last_good.get(network),
            'endpoints': endpoints(network)}


# ---------------------------------------------------------------- self-test

def selftest():
    """Offline checks — keccak and selector against known vectors."""
    empty = keccak256(b'').hex()
    assert empty == 'c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470', empty
    bal = selector('balanceOf')
    assert bal == '0x2e4263afad30923c891518314c3c95dbe830a16874e8abc5777a9a20b54c76e', bal
    return {'keccak256': 'ok', 'selector': 'ok'}


if __name__ == '__main__':
    print(json.dumps(selftest()))
    print(json.dumps(status(), indent=2))
