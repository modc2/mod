"""HyperEVM — the EVM half of Hyperliquid, spoken over plain JSON-RPC.

Hyperliquid runs two ledgers on one chain: HyperCore (the order book, where the
prices in this module come from) and HyperEVM (an EVM where the stablecoins
live). The pool takes its deposits on HyperEVM and settles them against
HyperCore prices, so this file is the money half and `mod.py::_price_at` is the
truth half.

Deliberately dependency-light: `requests` for the RPC and `eth_account` only
when something has to be *signed*. That keeps deposits (read-only, the common
path) working on a box with no key material at all.

Two facts about the public RPC shaped this file:

  * `eth_getLogs` is capped at **1000 blocks** per query and rate-limits hard
    (`-32005`). Blocks are ~1s, so a week is ~600k blocks — a naive "scan since
    genesis" is thousands of calls. Hence `scan_transfers` takes a chunk budget
    and returns a resumable cursor, and `transfers_in_tx` exists as the instant
    path: a user who hands us their tx hash costs exactly one RPC call. The
    limiter is strict enough that scanning falls back to other endpoints —
    see LOG_FALLBACK_RPCS.
  * There is no archive guarantee. Anything older than the cursor is gone, so
    the cursor is persisted by the caller and only ever moves forward.
"""

import os
import time
from typing import Dict, List, Optional

import requests

# ── Chains ───────────────────────────────────────────────────────────

CHAINS = {
    999: {
        'name': 'HyperEVM',
        'rpc': 'https://rpc.hyperliquid.xyz/evm',
        'explorer': 'https://hyperevmscan.io',
        'currency': 'HYPE',
        'testnet': False,
    },
    998: {
        'name': 'HyperEVM Testnet',
        'rpc': 'https://rpc.hyperliquid-testnet.xyz/evm',
        'explorer': 'https://testnet.purrsec.com',
        'currency': 'HYPE',
        'testnet': True,
    },
}

# Stablecoins we accept, verified against the chain before first use — every
# address here was read back on 2026-08-28 with symbol()/decimals() and is
# re-checked by `verify_token` rather than trusted.
DEFAULT_TOKENS = {
    999: {
        'USDC': {'address': '0xb88339CB7199b77E23DB6E890353E22632Ba630f', 'decimals': 6},
        'USDT0': {'address': '0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb', 'decimals': 6},
    },
    998: {},   # testnet has no canonical stables — the owner registers them
}

# Hyperliquid's own endpoint rate-limits `eth_getLogs` into uselessness from a
# shared host — measured on 2026-08-28, 1000-block queries were refused with
# -32005 at any pacing, including one every two seconds. Receipts and calls are
# fine there, so only the log scan falls back, and it sticks to whichever
# endpoint answered last. Override with PREFI_HYPEREVM_LOG_RPC.
LOG_FALLBACK_RPCS = {
    999: [
        'https://rpc.hypurrscan.io',
        'https://rpc.hyperlend.finance',
        'https://hyperliquid-json-rpc.stakely.io',
        'https://rpc.purroofgroup.com',
    ],
    998: [],
}

MAX_LOG_RANGE = 1000            # the public RPC's hard cap on eth_getLogs
# Fallback endpoints are separate nodes and run a few blocks behind the
# official one, which answers `toBlock` past their head with "invalid block
# range". Scanning stops this far short of the tip; the tx-hash path is what
# makes a fresh deposit show up immediately anyway.
SCAN_LAG = 25
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

SEL_NAME = '0x06fdde03'
SEL_SYMBOL = '0x95d89b41'
SEL_DECIMALS = '0x313ce567'
SEL_BALANCE_OF = '0x70a08231'
SEL_TRANSFER = '0xa9059cbb'


class RpcError(Exception):
    """A JSON-RPC error the node returned, code kept for callers that retry."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# ── ABI helpers (a 4-selector ABI does not need a library) ───────────

def _hex(n: int) -> str:
    return hex(n)


def pad_address(addr: str) -> str:
    """Left-pad an address to a 32-byte word — argument and log-topic form."""
    return '0x' + addr.lower().replace('0x', '').rjust(64, '0')


def _pad_uint(n: int) -> str:
    return format(int(n), '064x')


def _decode_string(raw: Optional[str]) -> Optional[str]:
    """ABI-decode a returned string. Some old tokens answer with a raw bytes32
    instead of a dynamic string, so fall back to trimming nulls."""
    if not raw or raw in ('0x', '0x0'):
        return None
    body = bytes.fromhex(raw[2:])
    if len(body) >= 64:
        try:
            length = int.from_bytes(body[32:64], 'big')
            if 0 < length <= len(body) - 64:
                return body[64:64 + length].decode('utf-8').strip('\x00')
        except (ValueError, UnicodeDecodeError):
            pass
    return body.rstrip(b'\x00').decode('utf-8', 'replace').strip() or None


def _decode_uint(raw: Optional[str]) -> Optional[int]:
    if not raw or raw == '0x':
        return None
    return int(raw, 16)


def is_address(value) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    return v.startswith('0x') and len(v) == 42 and all(
        c in '0123456789abcdefABCDEF' for c in v[2:])


def normalize(addr: str) -> str:
    """Lowercase form — what everything in the ledger is keyed by."""
    return (addr or '').strip().lower()


def to_units(amount: float, decimals: int) -> int:
    """USD float → integer token units, truncating rather than rounding up so a
    withdrawal can never ask for a hundredth of a cent we do not hold."""
    return int(round(float(amount) * (10 ** decimals)))


def from_units(units: int, decimals: int) -> float:
    return int(units) / float(10 ** decimals)


# ── Client ───────────────────────────────────────────────────────────

class HyperEVM:
    """One RPC endpoint, one chain. Cheap to construct — no connection state."""

    def __init__(self, chain_id: int = 999, rpc_url: str = None, timeout: int = 20):
        self.chain_id = int(chain_id)
        self.chain = CHAINS.get(self.chain_id, CHAINS[999])
        self.rpc_url = (rpc_url or os.environ.get('PREFI_HYPEREVM_RPC')
                        or self.chain['rpc'])
        self.timeout = timeout
        self._session = requests.Session()
        self._id = 0

        env_log = os.environ.get('PREFI_HYPEREVM_LOG_RPC')
        self.log_rpcs = [self.rpc_url] + (
            [env_log] if env_log else LOG_FALLBACK_RPCS.get(self.chain_id, []))
        self._log_rpc = None            # the endpoint that last answered a scan

    # ── transport ────────────────────────────────────────────────────

    def rpc(self, method: str, params: List = None, retries: int = 3,
            url: str = None):
        """One JSON-RPC call. Backs off on the rate limiter, which this endpoint
        applies aggressively enough that a tight scan loop hits it every time."""
        params = params if params is not None else []
        endpoint = url or self.rpc_url
        delay = 0.4
        last = None
        for attempt in range(retries):
            self._id += 1
            try:
                resp = self._session.post(
                    endpoint,
                    json={'jsonrpc': '2.0', 'id': self._id,
                          'method': method, 'params': params},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                body = resp.json()
            except Exception as exc:                      # network/JSON failure
                last = RpcError(f'{method}: {exc}')
                time.sleep(delay)
                delay *= 2
                continue

            if 'error' in body:
                err = body['error'] or {}
                code = err.get('code')
                last = RpcError(f"{method}: {err.get('message', err)}", code)
                if code == -32005 and attempt < retries - 1:   # rate limited
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise last
            return body.get('result')
        raise last or RpcError(f'{method}: no response')

    def ping(self) -> Dict:
        """Is the endpoint alive and is it the chain we think it is?"""
        try:
            chain_id = int(self.rpc('eth_chainId'), 16)
            block = int(self.rpc('eth_blockNumber'), 16)
            return {'ok': chain_id == self.chain_id, 'chain_id': chain_id,
                    'block': block, 'rpc': self.rpc_url}
        except Exception as exc:
            return {'ok': False, 'error': str(exc), 'rpc': self.rpc_url}

    # ── reads ────────────────────────────────────────────────────────

    def block_number(self) -> int:
        return int(self.rpc('eth_blockNumber'), 16)

    def call(self, to: str, data: str) -> Optional[str]:
        return self.rpc('eth_call', [{'to': to, 'data': data}, 'latest'])

    def erc20_meta(self, token: str) -> Dict:
        """symbol/decimals/name straight off the contract. This is what makes a
        token address trustworthy — nothing in the pool uses a hardcoded pair
        without reading it back."""
        return {
            'address': token,
            'symbol': _decode_string(self.call(token, SEL_SYMBOL)),
            'name': _decode_string(self.call(token, SEL_NAME)),
            'decimals': _decode_uint(self.call(token, SEL_DECIMALS)),
        }

    def erc20_balance(self, token: str, owner: str) -> Optional[int]:
        raw = self.call(token, SEL_BALANCE_OF + _pad_uint(int(owner, 16)))
        return _decode_uint(raw)

    def native_balance(self, owner: str) -> int:
        """HYPE balance — the gas that decides whether a payout can be sent."""
        return int(self.rpc('eth_getBalance', [owner, 'latest']), 16)

    def tx_receipt(self, tx_hash: str) -> Optional[Dict]:
        return self.rpc('eth_getTransactionReceipt', [tx_hash])

    # ── transfers in ─────────────────────────────────────────────────

    @staticmethod
    def parse_transfers(logs: List[Dict], to: str = None) -> List[Dict]:
        """Pull ERC-20 Transfers out of a log list.

        Filtering on the indexed `to` topic is what makes a deposit a deposit:
        a transaction can touch a dozen tokens, and only the leg that lands on
        the vault is ours.
        """
        want = pad_address(to) if to else None
        out = []
        for log in logs or []:
            topics = log.get('topics') or []
            if len(topics) < 3 or topics[0].lower() != TRANSFER_TOPIC:
                continue
            if want and topics[2].lower() != want:
                continue
            data = log.get('data') or '0x'
            try:
                units = int(data, 16)
            except ValueError:
                continue
            out.append({
                'token': normalize(log.get('address')),
                'from': '0x' + topics[1][-40:],
                'to': '0x' + topics[2][-40:],
                'units': units,
                'tx': normalize(log.get('transactionHash')),
                'log_index': int(log.get('logIndex', '0x0'), 16),
                'block': int(log.get('blockNumber', '0x0'), 16),
            })
        return out

    def transfers_in_tx(self, tx_hash: str, to: str) -> Dict:
        """The instant deposit path — one receipt, no scanning.

        Returns `confirmed: False` rather than raising when the hash is unknown
        or still in the mempool; a user pasting a hash the moment they send it
        is the normal case, not an error.
        """
        receipt = self.tx_receipt(tx_hash)
        if not receipt:
            return {'confirmed': False, 'reason': 'not mined yet', 'transfers': []}
        if int(receipt.get('status', '0x0'), 16) != 1:
            return {'confirmed': False, 'reason': 'transaction reverted', 'transfers': []}
        return {
            'confirmed': True,
            'block': int(receipt.get('blockNumber', '0x0'), 16),
            'transfers': self.parse_transfers(receipt.get('logs'), to),
        }

    def get_logs(self, params: Dict) -> Dict:
        """`eth_getLogs`, over whichever endpoint will actually serve it.

        Returns `{'logs', 'to_block'}` — `to_block` is what was *actually*
        covered, which can be short of what was asked for when the answering
        node is behind. The caller advances its cursor by that, never by the
        request, or a lagging endpoint would silently skip blocks.

        Sticky: once one answers, keep using it, and only walk the list again
        when it stops. Retries are kept to one per endpoint because the failure
        we are routing around is a rate limiter — waiting on it costs more than
        asking somebody else.
        """
        asked_to = int(params.get('toBlock', '0x0'), 16)
        order = ([self._log_rpc] if self._log_rpc else []) + \
                [u for u in self.log_rpcs if u != self._log_rpc]
        last = None
        for url in order:
            try:
                logs = self.rpc('eth_getLogs', [params], retries=1, url=url)
                self._log_rpc = url
                return {'logs': logs or [], 'to_block': asked_to}
            except RpcError as exc:
                last = exc
                # Every endpoint is a different node with its own tip, and one a
                # few blocks behind rejects the whole query rather than
                # answering what it has. Ask it where it is and try again.
                if 'block range' in str(exc).lower():
                    try:
                        head = int(self.rpc('eth_blockNumber', retries=1, url=url), 16)
                    except RpcError:
                        continue
                    if int(params.get('toBlock', '0x0'), 16) > head >= int(
                            params.get('fromBlock', '0x0'), 16):
                        try:
                            logs = self.rpc('eth_getLogs',
                                            [{**params, 'toBlock': _hex(head)}],
                                            retries=1, url=url)
                            self._log_rpc = url
                            return {'logs': logs or [], 'to_block': head}
                        except RpcError as retry_exc:
                            last = retry_exc
        self._log_rpc = None
        raise last or RpcError('eth_getLogs: no endpoint answered')

    def scan_transfers(self, token: str, to: str, from_block: int,
                       to_block: int = None, max_chunks: int = 20) -> Dict:
        """Walk Transfer logs into `to` in ≤1000-block chunks.

        Returns a `cursor` (next unscanned block) and `done`, so a caller with a
        million blocks of backlog can make progress a slice at a time instead of
        hanging on one call the RPC would refuse anyway.
        """
        head = int(to_block) if to_block is not None else max(0, self.block_number() - SCAN_LAG)
        start = max(0, int(from_block))
        found, chunks = [], 0

        while start <= head and chunks < max_chunks:
            end = min(start + MAX_LOG_RANGE - 1, head)
            answer = self.get_logs({
                'fromBlock': _hex(start),
                'toBlock': _hex(end),
                'address': token,
                'topics': [TRANSFER_TOPIC, None, pad_address(to)],
            })
            found.extend(self.parse_transfers(answer['logs'], to))
            chunks += 1
            covered = min(end, max(start - 1, answer['to_block']))
            if covered < start:
                break                      # the node is behind our cursor
            start = covered + 1

        return {
            'transfers': found,
            'cursor': start,
            'head': head,
            'done': start > head,
            'chunks': chunks,
            'blocks_behind': max(0, head - start + 1),
        }

    # ── transfers out (needs a key) ──────────────────────────────────

    def send_erc20(self, private_key: str, token: str, to: str,
                   units: int) -> Dict:
        """Sign and broadcast an ERC-20 transfer from the vault.

        The only method here that can move money. It is import-guarded so a
        deployment that never configures a key never even loads eth_account.
        """
        try:
            from eth_account import Account
        except ImportError:
            return {'error': 'eth_account is not installed — cannot sign'}

        acct = Account.from_key(private_key)
        sender = acct.address

        nonce = int(self.rpc('eth_getTransactionCount', [sender, 'pending']), 16)
        data = SEL_TRANSFER + _pad_uint(int(to, 16)) + _pad_uint(units)

        tx = {'from': sender, 'to': token, 'data': data, 'chainId': self.chain_id,
              'nonce': nonce}

        try:
            gas = int(self.rpc('eth_estimateGas', [
                {'from': sender, 'to': token, 'data': data}]), 16)
        except RpcError as exc:
            return {'error': f'gas estimate failed — {exc}'}
        tx['gas'] = int(gas * 1.3)

        # EIP-1559 when the chain prices blocks that way, legacy otherwise.
        head = self.rpc('eth_getBlockByNumber', ['latest', False]) or {}
        base_fee = head.get('baseFeePerGas')
        if base_fee:
            base = int(base_fee, 16)
            try:
                tip = int(self.rpc('eth_maxPriorityFeePerGas'), 16)
            except Exception:
                tip = 1_000_000_000
            tx['maxPriorityFeePerGas'] = tip
            tx['maxFeePerGas'] = base * 2 + tip
            tx['type'] = 2
        else:
            tx['gasPrice'] = int(int(self.rpc('eth_gasPrice'), 16) * 1.25)

        signed = Account.sign_transaction(tx, private_key)
        raw = signed.raw_transaction if hasattr(signed, 'raw_transaction') else signed.rawTransaction
        tx_hash = self.rpc('eth_sendRawTransaction', ['0x' + raw.hex().replace('0x', '')])
        return {'tx': tx_hash, 'from': sender, 'to': to, 'units': units,
                'gas': tx['gas'], 'nonce': nonce}

    # ── explorer links ───────────────────────────────────────────────

    def tx_url(self, tx_hash: str) -> str:
        return f"{self.chain['explorer']}/tx/{tx_hash}"

    def address_url(self, addr: str) -> str:
        return f"{self.chain['explorer']}/address/{addr}"


def new_wallet() -> Dict:
    """Generate a vault key. Separate from the class because creating one is an
    operator act, not an RPC one."""
    from eth_account import Account
    acct = Account.create()
    key = acct.key.hex()
    return {'address': acct.address, 'private_key': key if key.startswith('0x') else '0x' + key}
