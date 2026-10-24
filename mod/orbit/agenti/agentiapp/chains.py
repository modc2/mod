"""Balances over public RPCs (no keys): native coin + USDC on the chains
agenti pays on. Results are cached for 30 s per (chain, address)."""
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {'User-Agent': 'agenti-mod/0.2', 'Content-Type': 'application/json'}

EVM = {
    'base':         {'label': 'Base', 'chain_id': 8453, 'rpc': 'https://mainnet.base.org', 'native': 'ETH',
                     'usdc': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', 'explorer': 'https://basescan.org/address/'},
    'ethereum':     {'label': 'Ethereum', 'chain_id': 1, 'rpc': 'https://ethereum-rpc.publicnode.com', 'native': 'ETH',
                     'usdc': '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', 'explorer': 'https://etherscan.io/address/'},
    'arbitrum':     {'label': 'Arbitrum', 'chain_id': 42161, 'rpc': 'https://arb1.arbitrum.io/rpc', 'native': 'ETH',
                     'usdc': '0xaf88d065e77c8cC2239327C5EDb3A432268e5831', 'explorer': 'https://arbiscan.io/address/'},
    'polygon':      {'label': 'Polygon', 'chain_id': 137, 'rpc': 'https://polygon-bor-rpc.publicnode.com', 'native': 'POL',
                     'usdc': '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359', 'explorer': 'https://polygonscan.com/address/'},
    'base-sepolia': {'label': 'Base Sepolia (test)', 'chain_id': 84532, 'rpc': 'https://sepolia.base.org', 'native': 'ETH',
                     'usdc': '0x036CbD53842c5426634e7929541eC2318f3dCF7e', 'explorer': 'https://sepolia.basescan.org/address/',
                     'testnet': True},
}
SOL = {
    'solana':        {'label': 'Solana', 'rpc': 'https://api.mainnet-beta.solana.com', 'native': 'SOL',
                      'usdc': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 'explorer': 'https://solscan.io/account/'},
    'solana-devnet': {'label': 'Solana Devnet (test)', 'rpc': 'https://api.devnet.solana.com', 'native': 'SOL',
                      'usdc': '4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU', 'explorer': 'https://solscan.io/account/',
                      'suffix': '?cluster=devnet', 'testnet': True},
}
# x402 network names (v1 plain + v2 CAIP-2) -> our chain key
NETWORK_ALIASES = {
    'base': 'base', 'base-mainnet': 'base', 'eip155:8453': 'base',
    'base-sepolia': 'base-sepolia', 'eip155:84532': 'base-sepolia',
    'ethereum': 'ethereum', 'ethereum-mainnet': 'ethereum', 'eip155:1': 'ethereum',
    'arbitrum': 'arbitrum', 'arbitrum-mainnet': 'arbitrum', 'eip155:42161': 'arbitrum',
    'polygon': 'polygon', 'polygon-mainnet': 'polygon', 'eip155:137': 'polygon',
    'solana': 'solana', 'solana-mainnet': 'solana', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp': 'solana',
    'solana-devnet': 'solana-devnet', 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1': 'solana-devnet',
}

_CACHE = {}
_CLOCK = threading.Lock()
TTL = 30


def chains() -> dict:
    out = {}
    for k, c in {**EVM, **SOL}.items():
        out[k] = {'label': c['label'], 'kind': 'evm' if k in EVM else 'solana', 'native': c['native'],
                  'testnet': bool(c.get('testnet')), 'chain_id': c.get('chain_id'), 'usdc': c['usdc']}
    return out


def explorer(chain: str, address: str) -> str:
    c = EVM.get(chain) or SOL.get(chain)
    return c['explorer'] + address + c.get('suffix', '') if c else ''


def _rpc(url: str, method: str, params: list, timeout: float = 8):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = urllib.request.Request(url, data=body, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    if data.get('error'):
        raise RuntimeError(data['error'].get('message', str(data['error'])))
    return data['result']


def _evm(chain: str, address: str) -> dict:
    c = EVM[chain]
    wei = int(_rpc(c['rpc'], 'eth_getBalance', [address, 'latest']), 16)
    call = {'to': c['usdc'], 'data': '0x70a08231' + address.lower().removeprefix('0x').rjust(64, '0')}
    usdc = int(_rpc(c['rpc'], 'eth_call', [call, 'latest']) or '0x0', 16)
    return {'native': wei / 1e18, 'usdc': usdc / 1e6, 'usdc_raw': usdc}


def _sol(chain: str, address: str) -> dict:
    c = SOL[chain]
    lamports = _rpc(c['rpc'], 'getBalance', [address])['value']
    res = _rpc(c['rpc'], 'getTokenAccountsByOwner', [address, {'mint': c['usdc']}, {'encoding': 'jsonParsed'}])
    raw = sum(int(a['account']['data']['parsed']['info']['tokenAmount']['amount']) for a in res['value'])
    return {'native': lamports / 1e9, 'usdc': raw / 1e6, 'usdc_raw': raw}


def balance(chain: str, address: str, fresh: bool = False) -> dict:
    key = (chain, address)
    now = time.time()
    with _CLOCK:
        hit = _CACHE.get(key)
    if hit and not fresh and now - hit['ts'] < TTL:
        return hit
    c = EVM.get(chain) or SOL.get(chain)
    if not c:
        raise KeyError(f'unknown chain {chain!r}')
    out = {'chain': chain, 'label': c['label'], 'address': address, 'native_symbol': c['native'],
           'testnet': bool(c.get('testnet')), 'explorer': explorer(chain, address), 'ts': now}
    try:
        out.update(_evm(chain, address) if chain in EVM else _sol(chain, address))
        out['ok'] = True
    except Exception as e:  # noqa: BLE001 — surface any RPC failure as data
        if hit:
            return {**hit, 'stale': True, 'error': str(e)[:200]}
        out.update(ok=False, error=str(e)[:200])
    with _CLOCK:
        _CACHE[key] = out
    return out


def balances(evm: str = None, solana: str = None, testnets: bool = True, fresh: bool = False) -> dict:
    jobs = []
    if evm:
        jobs += [(k, evm) for k, c in EVM.items() if testnets or not c.get('testnet')]
    if solana:
        jobs += [(k, solana) for k, c in SOL.items() if testnets or not c.get('testnet')]
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(lambda j: balance(j[0], j[1], fresh), jobs))
    px = prices()
    total = 0.0
    for r in rows:
        if r.get('ok') and not r['testnet']:
            r['usd'] = r['usdc'] + r['native'] * px.get(r['native_symbol'], 0)
            total += r['usd']
    return {'rows': rows, 'usd_total': round(total, 2), 'prices': px}


_PX = {'ts': 0, 'v': {}}


def prices() -> dict:
    if time.time() - _PX['ts'] < 120 and _PX['v']:
        return _PX['v']
    try:
        url = 'https://api.coingecko.com/api/v3/simple/price?ids=ethereum,solana,polygon-ecosystem-token&vs_currencies=usd'
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=6) as r:
            d = json.loads(r.read())
        _PX['v'] = {'ETH': d['ethereum']['usd'], 'SOL': d['solana']['usd'],
                    'POL': d.get('polygon-ecosystem-token', {}).get('usd', 0)}
        _PX['ts'] = time.time()
    except Exception:  # noqa: BLE001
        pass
    return _PX['v']


def usdc_of(chain: str) -> str:
    c = EVM.get(chain) or SOL.get(chain)
    return c['usdc'] if c else None
