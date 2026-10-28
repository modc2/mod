"""
bank/openbanking.py — banks with a regulated API.

Two dialects cover most of the banks that have one:

  berlin   Berlin Group NextGenPSD2 — the PSD2 standard ~75% of EU banks
           (and the aggregators in front of them) implement:
             GET  /v1/accounts
             GET  /v1/accounts/{id}/balances
             GET  /v1/accounts/{id}/transactions?bookingStatus=booked
             POST /v1/payments/sepa-credit-transfers
  obie     UK Open Banking (OBIE) v3.1 AISP read side:
             GET  /open-banking/v3.1/aisp/accounts[/{id}/balances|/transactions]
           (UK payment initiation needs a per-payment consent redirect,
           which is a human in a browser — use a statement connection's
           pain.001 or the bank's own portal for outbound.)

Credentials are what the bank (or the licensed aggregator you use in front
of it) issued to you: an OAuth access token and, for Berlin Group, the
consent id. They live in connections.json off the repo; this module never
logs them. Consent expiry, SCA redirects and token refresh belong to the
bank's own flow — when the token lapses, reconnect with a fresh one.
"""
import uuid

from . import core
from .core import Adapter, BankError, _f, account, txn_from_any


class OpenBanking(Adapter):
    """A bank with a PSD2 / Open Banking API (Berlin Group NextGenPSD2 or UK
    OBIE v3.1). Needs the base URL plus the access token (and consent id)
    your bank or aggregator issued. Reads accounts, balances and booked
    transactions; Berlin Group banks can also initiate SEPA transfers."""

    kind = 'openbanking'
    label = 'Open Banking API (PSD2 / OBIE)'
    live = True
    caps = ('accounts', 'transactions', 'pay')
    fields = [
        {'name': 'base_url', 'required': True, 'secret': False,
         'help': 'API root, e.g. https://api.bank.example (no trailing /v1)'},
        {'name': 'access_token', 'required': True, 'secret': True,
         'help': 'OAuth bearer token from your bank or aggregator'},
        {'name': 'dialect', 'required': False, 'secret': False,
         'help': "'berlin' (default, EU PSD2) or 'obie' (UK Open Banking)"},
        {'name': 'consent_id', 'required': False, 'secret': True,
         'help': 'Berlin Group Consent-ID for account access'},
        {'name': 'psu_ip', 'required': False, 'secret': False,
         'help': 'PSU-IP-Address header some banks require'},
    ]

    @property
    def dialect(self):
        d = (self.cfg.get('dialect') or 'berlin').lower()
        if d not in ('berlin', 'obie'):
            raise BankError(f"dialect must be 'berlin' or 'obie', got {d!r}")
        return d

    def _get(self, path, body=None, method='GET'):
        h = {'Authorization': f'Bearer {self.cfg["access_token"]}',
             'X-Request-ID': str(uuid.uuid4())}
        if self.cfg.get('consent_id'):
            h['Consent-ID'] = self.cfg['consent_id']
        if self.cfg.get('psu_ip'):
            h['PSU-IP-Address'] = self.cfg['psu_ip']
        return core.http_json(method, self.cfg['base_url'].rstrip('/') + path, h, body)

    # ── Berlin Group ──

    def _berlin_accounts(self):
        out = []
        for a in self._get('/v1/accounts').get('accounts', []):
            bal = None
            for b in self._get(f'/v1/accounts/{a["resourceId"]}/balances').get('balances', []):
                if b.get('balanceType') in ('closingBooked', 'interimAvailable', 'expected', 'interimBooked'):
                    bal = _f(b.get('balanceAmount', {}).get('amount'))
                    break
            out.append(account(id=a['resourceId'], name=a.get('name') or a.get('product') or a.get('iban'),
                               currency=a.get('currency'), iban=a.get('iban'), balance=bal))
        return out

    def _berlin_txns(self, acct, since):
        import time as _t
        q = '?bookingStatus=booked'
        if since:
            q += '&dateFrom=' + _t.strftime('%Y-%m-%d', _t.gmtime(since))
        res = self._get(f'/v1/accounts/{acct}/transactions{q}').get('transactions', {})
        rows = []
        for i, r in enumerate(res.get('booked', [])):
            amt = _f(r.get('transactionAmount', {}).get('amount'))
            # money in → the other side is the debtor; money out → the creditor
            side = 'debtor' if amt > 0 else 'creditor'
            cp_acct = r.get(f'{side}Account')
            rows.append(txn_from_any({
                'id': r.get('transactionId') or r.get('entryReference'),
                'date': r.get('bookingDate') or r.get('valueDate'),
                'amount': amt,
                'currency': r.get('transactionAmount', {}).get('currency'),
                'counterparty': r.get(f'{side}Name'),
                'counterparty_iban': cp_acct.get('iban', '') if isinstance(cp_acct, dict) else '',
                'reference': r.get('remittanceInformationUnstructured') or
                             ' '.join(r.get('remittanceInformationUnstructuredArray') or []),
            }, account_id=acct, seq=i))
        return rows

    # ── UK OBIE ──

    def _obie_accounts(self):
        base = '/open-banking/v3.1/aisp/accounts'
        out = []
        for a in self._get(base).get('Data', {}).get('Account', []):
            ident = next((x.get('Identification') for x in a.get('Account', [])), '')
            bal = None
            for b in self._get(f'{base}/{a["AccountId"]}/balances').get('Data', {}).get('Balance', []):
                v = _f(b.get('Amount', {}).get('Amount'))
                bal = -v if b.get('CreditDebitIndicator') == 'Debit' else v
                break
            out.append(account(id=a['AccountId'], name=a.get('Nickname') or ident,
                               currency=a.get('Currency'), iban=ident, balance=bal))
        return out

    def _obie_txns(self, acct, since):
        rows = self._get(f'/open-banking/v3.1/aisp/accounts/{acct}/transactions') \
            .get('Data', {}).get('Transaction', [])
        out = []
        for i, r in enumerate(rows):
            if r.get('Status', 'Booked') != 'Booked':
                continue
            cp = r.get('DebtorAccount') or r.get('CreditorAccount') or {}
            out.append(txn_from_any({
                'id': r.get('TransactionId') or r.get('TransactionReference'),
                'date': r.get('BookingDateTime'),
                'amount': r.get('Amount', {}),
                'CreditDebitIndicator': r.get('CreditDebitIndicator'),
                'counterparty': cp.get('Name') or (r.get('MerchantDetails') or {}).get('MerchantName'),
                'counterparty_iban': cp.get('Identification', ''),
                'reference': r.get('TransactionReference') or r.get('TransactionInformation'),
            }, account_id=acct, seq=i))
        return [t for t in out if t['date'] >= since]

    # ── the verbs ──

    def accounts(self):
        return self._berlin_accounts() if self.dialect == 'berlin' else self._obie_accounts()

    def transactions(self, account='', since=0):
        accts = [account] if account else [a['id'] for a in self.accounts()]
        fn = self._berlin_txns if self.dialect == 'berlin' else self._obie_txns
        return [t for a in accts for t in fn(a, since) if t['date'] >= since]

    def pay(self, account, to_name, to_iban, amount, currency, reference):
        if self.dialect != 'berlin':
            return super().pay(account, to_name, to_iban, amount, currency, reference)
        debtor = next((a for a in self.accounts() if a['id'] == account or a['iban'] == account), None)
        if not debtor:
            raise BankError(f'no account {account!r} on this connection')
        res = self._get('/v1/payments/sepa-credit-transfers', method='POST', body={
            'instructedAmount': {'currency': currency or debtor['currency'] or 'EUR',
                                 'amount': f'{amount:.2f}'},
            'debtorAccount': {'iban': debtor['iban']},
            'creditorAccount': {'iban': to_iban},
            'creditorName': to_name or to_iban,
            'remittanceInformationUnstructured': reference[:140],
        })
        links = res.get('_links', {})
        sca = links.get('scaRedirect') or links.get('scaOAuth') or {}
        return {'id': res.get('paymentId', ''), 'account': debtor['id'],
                'to_name': to_name, 'to_iban': to_iban, 'amount': amount,
                'currency': currency or debtor['currency'], 'reference': reference,
                'status': res.get('transactionStatus', 'RCVD'),
                'approve_at': sca.get('href', '') if isinstance(sca, dict) else '',
                'next_step': 'approve the payment in your bank (SCA) — the bank '
                             'executes it only after you do'}
