"""
bank/near.py — a NEAR account as the rent account.

A renter sends NEAR from their own account to the owner's rent account; this
adapter reads those transfers off the chain and hands them to the same
reconcile loop every other bank feeds. A native NEAR transfer carries no memo,
so the renter is recognised by the *account they paid from*: link it once
(`bank_link(address, payer_name='alice.near')`) and every transfer from it is
that renter's rent.

Read-only: it indexes incoming TRANSFER actions through the public
nearblocks API and reads the balance through the NEAR RPC. Both go through
core.http_json, so tests (and the node's audit) see exactly one seam. Nothing
here holds a key — NEAR rent is paid by the renter's own wallet.
"""
from . import core
from .core import Adapter, BankError, account, txn

YOCTO = 10 ** 24

NETWORKS = {
    'testnet': {'indexer': 'https://api-testnet.nearblocks.io', 'rpc': 'https://rpc.testnet.near.org'},
    'mainnet': {'indexer': 'https://api.nearblocks.io', 'rpc': 'https://free.rpc.fastnear.com'},
}
PAGE = 25
# nearblocks sits behind Cloudflare, which refuses urllib's default agent (error 1010).
UA = {'User-Agent': 'openhouse-mod/2.5 (+https://github.com/mod-protocol)'}


class Near(Adapter):
    """A NEAR account that receives rent in native NEAR. Link each renter's
    NEAR account with their address; every transfer from it books as their
    rent, once, converted to Ξ at the live NEAR/ETH rate."""

    kind = 'near'
    label = 'NEAR account (native NEAR in)'
    live = False
    caps = ('accounts', 'transactions')
    fields = [
        {'name': 'account_id', 'required': True, 'secret': False,
         'help': 'the NEAR account that receives rent, e.g. rent.openhouse.near'},
        {'name': 'network', 'required': False, 'secret': False,
         'help': "'testnet' (default) or 'mainnet'"},
        {'name': 'indexer', 'required': False, 'secret': False,
         'help': 'indexer API root override (default: nearblocks for the network)'},
        {'name': 'rpc', 'required': False, 'secret': False,
         'help': 'NEAR RPC override (default: the public RPC for the network)'},
        {'name': 'max_pages', 'required': False, 'secret': False,
         'help': 'how many 25-row pages of history to read per sync (default 4)'},
    ]

    @property
    def network(self) -> str:
        n = (self.cfg.get('network') or 'testnet').lower()
        if n not in NETWORKS:
            raise BankError(f"network must be 'testnet' or 'mainnet', got {n!r}")
        return n

    @property
    def account_id(self) -> str:
        return str(self.cfg.get('account_id') or '').strip().lower()

    def _indexer(self) -> str:
        return (self.cfg.get('indexer') or NETWORKS[self.network]['indexer']).rstrip('/')

    def _rpc(self) -> str:
        return self.cfg.get('rpc') or NETWORKS[self.network]['rpc']

    def accounts(self):
        bal = None
        try:
            res = core.http_json('POST', self._rpc(), headers=UA, body={
                'jsonrpc': '2.0', 'id': 'oh', 'method': 'query',
                'params': {'request_type': 'view_account', 'finality': 'final',
                           'account_id': self.account_id}})
            amount = (res.get('result') or {}).get('amount')
            bal = None if amount is None else int(amount) / YOCTO
        except (BankError, ValueError):
            pass                     # an unreachable RPC must not hide the account
        a = account(id=self.account_id, name=f'{self.account_id} ({self.network})',
                    currency='NEAR', balance=bal)
        if bal is not None:
            a['balance'] = round(bal, 6)
        return [a]

    def transactions(self, account='', since=0):
        want = self.account_id
        pages = max(1, min(int(self.cfg.get('max_pages') or 4), 40))
        out, seen, cursor = [], set(), ''
        for _ in range(pages):
            url = f'{self._indexer()}/v1/account/{want}/txns?per_page={PAGE}&order=desc'
            if cursor:
                url += f'&cursor={cursor}'
            res = core.http_json('GET', url, headers=UA)
            rows = res.get('txns') or []
            for r in rows:
                t = self._incoming(r, want)
                if t and t['id'] not in seen:
                    seen.add(t['id'])
                    out.append(t)
            cursor = res.get('cursor') or ''
            if not rows or not cursor:
                break
            oldest = min((_secs(r) for r in rows), default=0)
            if since and oldest and oldest < since:
                break
        return [t for t in out if t['date'] >= int(since or 0)]

    @staticmethod
    def _incoming(r: dict, want: str):
        """One indexer row → a credit, or None. Only a successful receipt that
        landed on this account, from someone else, carrying a TRANSFER."""
        if str(r.get('receiver_account_id') or '').lower() != want:
            return None
        payer = str(r.get('predecessor_account_id') or '')
        if not payer or payer.lower() in (want, 'system'):
            return None
        if not (r.get('receipt_outcome') or {}).get('status'):
            return None
        deposit = sum(int(a.get('deposit') or 0) for a in (r.get('actions') or [])
                      if str(a.get('action')).upper() == 'TRANSFER')
        if deposit <= 0:
            return None
        t = txn(id=r.get('receipt_id') or r.get('transaction_hash'), account=want,
                date=_secs(r), amount=0, currency='NEAR', counterparty=payer,
                reference=f'tx {r.get("transaction_hash") or ""}'.strip())
        t['amount'] = round(deposit / YOCTO, 6)
        t['tx'] = r.get('transaction_hash') or ''
        return t

    def pay(self, account, to_name, to_iban, amount, currency, reference):
        raise BankError('near: this rail only receives — payouts are signed by the '
                        "owner's own wallet (orbit/near send), never by this node")


def _secs(r: dict) -> int:
    ns = (r.get('receipt_block') or {}).get('block_timestamp') or r.get('block_timestamp') or 0
    return int(int(ns) // 1_000_000_000)
