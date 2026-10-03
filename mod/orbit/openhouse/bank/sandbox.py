"""
bank/sandbox.py — the testnet bank.

A complete little bank that lives in one JSON file: accounts with balances,
a booked-transaction history, outbound payments that actually debit, and
`receive()` — the button that stands in for "a renter sent a bank transfer".
No network, no keys, no sign-up, deterministic when given dates.

It is the bank every testnet example and test runs against, and the shape a
real adapter has to match: if a flow works against the sandbox, swapping the
connection for a statement/openbanking/remote one changes where the numbers
come from, not what happens to them.

IBANs are minted under country code XT — an ISO 3166 user-assigned code no
real bank uses — with valid mod-97 check digits, so format validators pass
and nobody can mistake one for a real account.
"""
import hashlib
import json
import time

from .core import Adapter, BankError, account, norm_iban, txn


def _iban(seed: str) -> str:
    """XT + mod-97 check digits + 16-digit BBAN derived from seed."""
    bban = str(int(hashlib.sha256(seed.encode()).hexdigest(), 16))[:16]
    # ISO 13616: move country+00 to the end, letters → numbers, 98 - mod 97
    digits = ''.join(str(int(c, 36)) for c in bban + 'XT00')
    return f'XT{98 - int(digits) % 97:02d}{bban}'


class Sandbox(Adapter):
    """A local, fake bank for testnet — accounts, balances, transfers in and
    out, all in one JSON file. Nothing leaves the machine."""

    kind = 'sandbox'
    label = 'Sandbox bank (testnet)'
    live = False
    caps = ('accounts', 'transactions', 'pay', 'receive')
    fields = [
        {'name': 'holder', 'required': False, 'secret': False,
         'help': 'account holder name (default "OpenHouse Owner")'},
        {'name': 'currency', 'required': False, 'secret': False,
         'help': 'ISO 4217 currency of the opening account (default USD)'},
        {'name': 'opening_balance', 'required': False, 'secret': False,
         'help': 'starting balance of the operating account (default 0)'},
    ]

    @property
    def _path(self):
        return self.data_dir / 'sandbox' / f'{self.conn["id"]}.json'

    def _books(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, ValueError):
            return {'accounts': {}, 'txns': [], 'payments': []}

    def _save(self, books):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(books, indent=2))

    def on_connect(self):
        books = self._books()
        if not books['accounts']:
            self.open_account('operating', name=f'{self.cfg.get("holder") or "OpenHouse Owner"} — rent account',
                              currency=self.cfg.get('currency') or 'USD',
                              balance=float(self.cfg.get('opening_balance') or 0))

    def open_account(self, acct_id: str, name: str = '', currency: str = 'USD',
                     balance: float = 0.0) -> dict:
        books = self._books()
        if acct_id in books['accounts']:
            raise BankError(f'account {acct_id!r} exists')
        a = account(id=acct_id, name=name or acct_id, currency=currency or 'USD',
                    iban=_iban(f'{self.conn["id"]}:{acct_id}'), balance=balance)
        books['accounts'][acct_id] = a
        self._save(books)
        return a

    def _acct(self, books, acct_id):
        if not acct_id:
            if len(books['accounts']) != 1:
                raise BankError('account required — have: ' + ', '.join(books['accounts']))
            acct_id = next(iter(books['accounts']))
        a = books['accounts'].get(acct_id)
        if not a:
            raise BankError(f'no account {acct_id!r} — have: {", ".join(books["accounts"])}')
        return a

    # the three verbs ---------------------------------------------

    def accounts(self):
        return list(self._books()['accounts'].values())

    def transactions(self, account='', since=0):
        books = self._books()
        if account:
            self._acct(books, account)
        return [t for t in books['txns']
                if (not account or t['account'] == account) and t['date'] >= since]

    def pay(self, account, to_name, to_iban, amount, currency, reference):
        books = self._books()
        a = self._acct(books, account)
        if currency and currency != a['currency']:
            raise BankError(f'account is {a["currency"]}, payment is {currency}')
        if a['balance'] < amount:
            raise BankError(f'insufficient funds: {a["balance"]:.2f} {a["currency"]} '
                            f'available, {amount:.2f} requested')
        now = int(time.time())
        seq = len(books['txns'])
        t = txn(id=f'sbx-{seq:06d}', account=a['id'], date=now, amount=-amount,
                currency=a['currency'], counterparty=to_name, counterparty_iban=to_iban,
                reference=reference)
        a['balance'] = round(a['balance'] - amount, 2)
        books['txns'].append(t)
        p = {'id': f'pay-{len(books["payments"]):06d}', 'account': a['id'],
             'to_name': to_name, 'to_iban': norm_iban(to_iban), 'amount': amount,
             'currency': a['currency'], 'reference': reference,
             'status': 'executed', 'created': now, 'txn': t['id'],
             'balance_after': a['balance']}
        books['payments'].append(p)
        self._save(books)
        return p

    # the sandbox-only verb ----------------------------------------

    def receive(self, account='', from_name='', from_iban='', amount=0.0,
                reference='', date=None) -> dict:
        """Book an incoming transfer — "a renter paid by bank". `date` lets
        examples lay down history deterministically."""
        amount = round(float(amount), 2)
        if amount <= 0:
            raise BankError('amount must be positive')
        books = self._books()
        a = self._acct(books, account)
        seq = len(books['txns'])
        t = txn(id=f'sbx-{seq:06d}', account=a['id'], date=int(date or time.time()),
                amount=amount, currency=a['currency'], counterparty=from_name,
                counterparty_iban=from_iban, reference=reference)
        a['balance'] = round(a['balance'] + amount, 2)
        books['txns'].append(t)
        self._save(books)
        return {**t, 'balance_after': a['balance']}
