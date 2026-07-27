"""
credits - prepaid usage ledger for the agent's public API key.

Guests top up with USDT/USDC (Base or Ethereum) sent to the module's
deposit address, then spend those credits to run the agent on the
module's own provider key ("the public key") instead of bringing their
own. 1 credit = 1 USD; runs are billed per executed step.

Ledger state is private auth state — it lives OFF-tree under
~/.mod/agent/credits.json (same rule as the ACL), never in the repo.

Deposits are verified trustlessly: the caller submits a tx hash, we pull
the receipt from a public RPC, find ERC-20 Transfer logs of a supported
stablecoin into the deposit address, and credit the ON-CHAIN SENDER —
so nobody can claim someone else's deposit, and a hash can only be
credited once.
"""
import os
import json
import time
import threading
import urllib.request
from pathlib import Path
from typing import Optional

# keccak('Transfer(address,address,uint256)')
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

# supported stablecoins per network (all 6 decimals, 1:1 USD)
NETWORKS = {
    'base': {
        'rpc_env': 'AGENT_RPC_BASE',
        'rpc': 'https://mainnet.base.org',
        'tokens': {
            '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913': 'USDC',
            '0xfde4c96c8593536e31f229ea8f37b2ada2699bb2': 'USDT',
        },
    },
    'ethereum': {
        'rpc_env': 'AGENT_RPC_ETHEREUM',
        'rpc': 'https://eth.llamarpc.com',
        'tokens': {
            '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': 'USDC',
            '0xdac17f958d2ee523a2206206994597c13d831ec7': 'USDT',
        },
    },
}

TOKEN_DECIMALS = 6
DEFAULT_PRICE_PER_STEP = 0.01   # USD billed per executed agent step
MAX_HISTORY = 50                # ledger entries kept per account


def _now() -> float:
    return time.time()


class Credits:
    """Per-address prepaid credit ledger with on-chain USDT/USDC top-ups."""

    def __init__(self, state_dir, deposit_address: Optional[str] = None,
                 price_per_step: Optional[float] = None):
        self._path = Path(state_dir) / 'credits.json'
        self._lock = threading.Lock()
        self._state = self._load()
        cfg = self._state.setdefault('config', {})
        # config file wins, then env, then the module owner as deposit target
        self.deposit_address = (cfg.get('deposit_address')
                                or os.environ.get('AGENT_DEPOSIT_ADDRESS')
                                or deposit_address)
        if self.deposit_address:
            self.deposit_address = self.deposit_address.lower()
        self.price_per_step = float(
            cfg.get('price_per_step')
            or os.environ.get('AGENT_CREDIT_PRICE_PER_STEP')
            or (price_per_step if price_per_step is not None else DEFAULT_PRICE_PER_STEP))

    # ── persistence ──────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            if self._path.exists():
                with open(self._path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {'accounts': {}, 'txs': {}, 'config': {}}

    def _save(self):
        tmp = self._path.with_suffix('.json.tmp')
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, 'w') as f:
            json.dump(self._state, f, indent=2)
        os.replace(tmp, self._path)

    # ── accounts ─────────────────────────────────────────────────────

    def _account(self, address: str) -> dict:
        addr = (address or '').lower()
        return self._state['accounts'].setdefault(addr, {'balance': 0.0, 'history': []})

    def _record(self, acct: dict, kind: str, amount: float, note: str = '', tx: str = None):
        entry = {'time': _now(), 'type': kind, 'amount': round(amount, 6)}
        if note:
            entry['note'] = note[:120]
        if tx:
            entry['tx'] = tx
        acct['history'].append(entry)
        if len(acct['history']) > MAX_HISTORY:
            acct['history'] = acct['history'][-MAX_HISTORY:]

    def balance(self, address: Optional[str]) -> float:
        if not address:
            return 0.0
        acct = self._state['accounts'].get(address.lower())
        return round(float(acct['balance']), 6) if acct else 0.0

    def credit(self, address: str, amount: float, kind: str = 'deposit',
               note: str = '', tx: str = None) -> dict:
        """Add credits to an account (deposit verification or owner grant)."""
        if not address or not str(address).startswith('0x'):
            raise ValueError('a 0x address is required')
        amount = float(amount)
        with self._lock:
            acct = self._account(address)
            acct['balance'] = round(float(acct['balance']) + amount, 6)
            if acct['balance'] < 0:
                acct['balance'] = 0.0
            self._record(acct, kind, amount, note, tx)
            self._save()
            return {'address': address.lower(), 'balance': acct['balance'], 'credited': amount}

    def charge_steps(self, address: str, steps: int, note: str = '') -> dict:
        """Bill a finished run: steps × price, clamped to the balance."""
        if not address:
            return {'charged': 0.0, 'balance': 0.0}
        amount = round(max(0, int(steps)) * self.price_per_step, 6)
        with self._lock:
            acct = self._account(address)
            charged = round(min(amount, float(acct['balance'])), 6)
            if charged > 0:
                acct['balance'] = round(float(acct['balance']) - charged, 6)
                self._record(acct, 'spend', -charged, note)
                self._save()
            return {'charged': charged, 'steps': int(steps), 'balance': acct['balance']}

    # ── views ────────────────────────────────────────────────────────

    def info(self, address: Optional[str] = None, owner: bool = False) -> dict:
        """Public deposit/pricing info + the caller's own account view."""
        out = {
            'enabled': bool(self.deposit_address),
            'price_per_step': self.price_per_step,
            'deposit': {
                'address': self.deposit_address,
                'networks': {
                    net: {'tokens': sorted(set(info['tokens'].values()))}
                    for net, info in NETWORKS.items()
                },
            },
        }
        if address:
            acct = self._state['accounts'].get(address.lower())
            out['account'] = {
                'address': address.lower(),
                'balance': round(float(acct['balance']), 6) if acct else 0.0,
                'history': list(reversed(acct['history'])) if acct else [],
            }
        if owner:
            out['accounts'] = [
                {'address': a, 'balance': round(float(v['balance']), 6)}
                for a, v in sorted(self._state['accounts'].items())
            ]
        return out

    # ── on-chain deposit verification ────────────────────────────────

    def _rpc(self, network: str, method: str, params: list):
        info = NETWORKS[network]
        url = os.environ.get(info['rpc_env']) or info['rpc']
        body = json.dumps({'jsonrpc': '2.0', 'id': 1,
                           'method': method, 'params': params}).encode()
        req = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json',
            'User-Agent': 'mod-agent-credits/1.0',
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        if data.get('error'):
            raise RuntimeError(f"rpc error: {data['error'].get('message', data['error'])}")
        return data.get('result')

    def verify_deposit(self, tx_hash: str, network: str = 'base') -> dict:
        """Verify a USDT/USDC transfer into the deposit address and credit
        the on-chain sender. Each tx hash can only be credited once."""
        if not self.deposit_address:
            raise ValueError('deposits are disabled — no deposit address configured')
        network = (network or 'base').lower()
        if network not in NETWORKS:
            raise ValueError(f"unsupported network '{network}' — use one of {sorted(NETWORKS)}")
        tx_hash = (tx_hash or '').strip().lower()
        if not (tx_hash.startswith('0x') and len(tx_hash) == 66):
            raise ValueError('tx_hash must be a 0x… 66-char transaction hash')
        with self._lock:
            if tx_hash in self._state['txs']:
                raise ValueError('this transaction was already credited')

        receipt = self._rpc(network, 'eth_getTransactionReceipt', [tx_hash])
        if not receipt:
            raise ValueError('transaction not found (still pending? wrong network?)')
        if receipt.get('status') != '0x1':
            raise ValueError('transaction failed on-chain')

        tokens = NETWORKS[network]['tokens']
        want_to = self.deposit_address[2:].rjust(64, '0')
        total = 0.0
        sender = None
        token_seen = None
        for log in receipt.get('logs', []):
            topics = log.get('topics') or []
            if (len(topics) == 3
                    and topics[0].lower() == TRANSFER_TOPIC
                    and (log.get('address') or '').lower() in tokens
                    and topics[2].lower().endswith(want_to)):
                total += int(log.get('data', '0x0'), 16) / 10 ** TOKEN_DECIMALS
                sender = '0x' + topics[1][-40:].lower()
                token_seen = tokens[(log.get('address') or '').lower()]
        if total <= 0 or not sender:
            raise ValueError(
                f'no USDT/USDC transfer to {self.deposit_address} found in this transaction')

        with self._lock:
            if tx_hash in self._state['txs']:   # raced with a duplicate submit
                raise ValueError('this transaction was already credited')
            self._state['txs'][tx_hash] = {
                'network': network, 'token': token_seen, 'from': sender,
                'amount': round(total, 6), 'time': _now(),
            }
            acct = self._account(sender)
            acct['balance'] = round(float(acct['balance']) + total, 6)
            self._record(acct, 'deposit', total,
                         f'{token_seen} on {network}', tx_hash)
            self._save()
        return {'credited': round(total, 6), 'token': token_seen, 'network': network,
                'address': sender, 'balance': self.balance(sender)}
