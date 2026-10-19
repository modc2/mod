"""
bank/remote.py — a bank that speaks MCP.

Banks, cores and aggregators are starting to ship Model Context Protocol
servers of their own. This adapter is the client side: point it at any MCP
server (Streamable HTTP, JSON-RPC 2.0), tell it which of that server's tools
lists accounts, lists transactions and sends a payment, and the bank becomes
one more connection — same reconciler, same renter links, same MCP tools on
our side. That closes the loop: OpenHouse is an MCP server agents drive, and
an MCP client of whatever bank sits behind it.

Tool results are read from `structuredContent` when the server sends it, or
parsed out of the first JSON text block when it doesn't, and normalized with
the same field-name table the CSV reader uses (core.KEYS) — so a bank that
calls it `bookingDate` and one that calls it `posted` both land as `date`.

Two OpenHouse nodes can even bank each other: point a remote connection at
another node's /mcp and map the tools to its `openhouse_bank_*` tools.
"""
import json
import uuid

from . import core
from .core import Adapter, BankError, account, txn_from_any


def _rows(payload, *keys):
    """Find the list inside a tool result — top level, or under a likely key."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys + ('data', 'items', 'results', 'rows'):
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = _rows(v, *keys)
                if inner:
                    return inner
    return []


class RemoteMCP(Adapter):
    """Any bank, core or aggregator that exposes an MCP server. Give the
    server URL, an optional bearer token, and the names of its accounts /
    transactions / payment tools; results are normalized by field name."""

    kind = 'mcp'
    label = 'Bank MCP server'
    live = True
    caps = ('accounts', 'transactions', 'pay')
    fields = [
        {'name': 'url', 'required': True, 'secret': False,
         'help': 'the bank\'s MCP endpoint (Streamable HTTP), e.g. https://bank.example/mcp'},
        {'name': 'token', 'required': False, 'secret': True,
         'help': 'bearer token the bank issued, if its server needs one'},
        {'name': 'accounts_tool', 'required': False, 'secret': False,
         'help': 'tool that lists accounts (default: list_accounts)'},
        {'name': 'transactions_tool', 'required': False, 'secret': False,
         'help': 'tool that lists transactions (default: list_transactions)'},
        {'name': 'pay_tool', 'required': False, 'secret': False,
         'help': 'tool that sends a payment (default: none — read-only)'},
        {'name': 'account_arg', 'required': False, 'secret': False,
         'help': 'argument name the transactions tool takes the account under (default: account)'},
    ]

    def _rpc(self, method, params=None):
        headers = {'Accept': 'application/json, text/event-stream'}
        if self.cfg.get('token'):
            headers['Authorization'] = f'Bearer {self.cfg["token"]}'
        body = {'jsonrpc': '2.0', 'id': str(uuid.uuid4()), 'method': method,
                'params': params or {}}
        res = core.http_json('POST', self.cfg['url'], headers, body)
        if 'error' in res:
            raise BankError(f'bank MCP {method}: {res["error"].get("message", res["error"])}')
        return res.get('result', {})

    def tools(self) -> list:
        return [t.get('name') for t in self._rpc('tools/list').get('tools', [])]

    def call(self, tool: str, args: dict):
        r = self._rpc('tools/call', {'name': tool, 'arguments': args})
        if r.get('isError'):
            text = next((c.get('text') for c in r.get('content', []) if c.get('type') == 'text'), '')
            raise BankError(f'bank tool {tool}: {text or "error"}')
        if 'structuredContent' in r:
            return r['structuredContent']
        for c in r.get('content', []):
            if c.get('type') == 'text':
                try:
                    return json.loads(c['text'])
                except ValueError:
                    return {'text': c['text']}
        return {}

    def accounts(self):
        out = []
        for a in _rows(self.call(self.cfg.get('accounts_tool') or 'list_accounts', {}), 'accounts'):
            bal = a.get('balance')
            if isinstance(bal, dict):
                bal = bal.get('amount')
            out.append(account(id=a.get('id') or a.get('resourceId') or a.get('account_id') or a.get('iban'),
                               name=a.get('name') or a.get('nickname'),
                               currency=a.get('currency'), iban=a.get('iban'), balance=bal))
        return out

    def transactions(self, account='', since=0):
        args = {}
        if account:
            args[self.cfg.get('account_arg') or 'account'] = account
        if since:
            args['since'] = since
        rows = _rows(self.call(self.cfg.get('transactions_tool') or 'list_transactions', args),
                     'transactions', 'ledger', 'booked')
        return [t for i, r in enumerate(rows)
                for t in [txn_from_any(r, account_id=account or str(r.get('account', '')), seq=i)]
                if t['date'] >= since]

    def pay(self, account, to_name, to_iban, amount, currency, reference):
        tool = self.cfg.get('pay_tool')
        if not tool:
            raise BankError('this bank connection is read-only — set pay_tool to the '
                            'bank\'s payment tool to send money through it')
        res = self.call(tool, {'account': account, 'to_name': to_name, 'to_iban': to_iban,
                               'amount': amount, 'currency': currency, 'reference': reference})
        return {'id': str(res.get('id') or res.get('paymentId') or ''), 'account': account,
                'to_name': to_name, 'to_iban': to_iban, 'amount': amount,
                'currency': currency, 'reference': reference,
                'status': str(res.get('status') or res.get('transactionStatus') or 'submitted'),
                'bank_response': res}
