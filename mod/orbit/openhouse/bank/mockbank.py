"""
bank/mockbank.py — a reference bank that speaks MCP.

This is the other side of remote.py: the smallest honest bank MCP server,
three tools, JSON-RPC 2.0 over Streamable HTTP. It exists for two reasons:

  1. Testnet. The `bank_over_mcp` example mounts it in-process
     (core.INPROC['mockbank']) and drives OpenHouse's `mcp` adapter against
     it — no socket, no keys, same code path as a real bank.
  2. A template. A bank, credit union, core-banking vendor or aggregator
     that wants OpenHouse (or any agent) to read its ledger implements these
     three tools with these field names and it is connected — no custom
     adapter, no SDK:

       list_accounts      → {"accounts": [{id, name, currency, iban, balance}]}
       list_transactions  (account?, since?)
                          → {"transactions": [{id, date, amount (signed, + in),
                              currency, counterparty, counterparty_iban,
                              reference, status}]}
       send_payment       (account, to_name, to_iban, amount, currency, reference)
                          → {"id", "status"}

Run standalone:  python bank/mockbank.py        (MOCKBANK_PORT, default 50134)
Then connect:    bank_connect kind=mcp config={"url": "http://localhost:50134/mcp",
                                              "pay_tool": "send_payment"}

State is in memory: restart it and the bank is new again. It is fake money.
"""
import json
import time

TOOLS = [
    {'name': 'list_accounts', 'description': 'Accounts with balances.',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'list_transactions', 'description': 'Booked transactions, signed amounts.',
     'inputSchema': {'type': 'object', 'properties': {
         'account': {'type': 'string'}, 'since': {'type': 'integer'}}}},
    {'name': 'send_payment', 'description': 'Debit an account and pay an IBAN.',
     'inputSchema': {'type': 'object', 'properties': {
         'account': {'type': 'string'}, 'to_name': {'type': 'string'},
         'to_iban': {'type': 'string'}, 'amount': {'type': 'number'},
         'currency': {'type': 'string'}, 'reference': {'type': 'string'}},
         'required': ['account', 'to_iban', 'amount']}},
]


class MockBank:
    """A bank in a dict. `credit()` is how a test/example lays down history."""

    def __init__(self, currency='USD', token=''):
        self.token = token
        self.accounts = {'chk-1': {'id': 'chk-1', 'name': 'Mock Bank checking',
                                   'currency': currency, 'iban': 'XT89MOCK0000000001',
                                   'balance': 0.0}}
        self.txns = []

    def credit(self, amount, name='', iban='', reference='', date=None, account='chk-1'):
        a = self.accounts[account]
        a['balance'] = round(a['balance'] + amount, 2)
        t = {'id': f'mb-{len(self.txns) + 1:05d}', 'account': account,
             'date': int(date or time.time()), 'amount': round(amount, 2),
             'currency': a['currency'], 'counterparty': name,
             'counterparty_iban': iban, 'reference': reference, 'status': 'booked'}
        self.txns.append(t)
        return t

    # tools --------------------------------------------------------

    def _call(self, name, args):
        if name == 'list_accounts':
            return {'accounts': list(self.accounts.values())}
        if name == 'list_transactions':
            acct, since = args.get('account'), int(args.get('since') or 0)
            return {'transactions': [t for t in self.txns
                                     if (not acct or t['account'] == acct) and t['date'] >= since]}
        if name == 'send_payment':
            a = self.accounts.get(args.get('account') or 'chk-1')
            amt = float(args.get('amount') or 0)
            if not a:
                raise ValueError('no such account')
            if amt <= 0 or amt > a['balance']:
                raise ValueError(f'insufficient funds or bad amount ({a["balance"]:.2f} available)')
            t = self.credit(-amt, args.get('to_name', ''), args.get('to_iban', ''),
                            args.get('reference', ''), account=a['id'])
            return {'id': t['id'], 'status': 'executed', 'balance_after': a['balance']}
        raise KeyError(name)

    def rpc(self, body: dict, headers: dict = None) -> dict:
        """One JSON-RPC message in, one out — the whole server."""
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        id_ = body.get('id')
        if self.token and headers.get('authorization') != f'Bearer {self.token}':
            return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32001, 'message': 'unauthorized'}}
        m, p = body.get('method'), body.get('params') or {}
        if m == 'initialize':
            res = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                   'serverInfo': {'name': 'mockbank', 'version': '1.0.0'}}
        elif m == 'tools/list':
            res = {'tools': TOOLS}
        elif m == 'tools/call':
            try:
                out = self._call(p.get('name'), p.get('arguments') or {})
                res = {'content': [{'type': 'text', 'text': json.dumps(out)}],
                       'structuredContent': out, 'isError': False}
            except KeyError as e:
                return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32602, 'message': f'unknown tool {e}'}}
            except ValueError as e:
                res = {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
        else:
            return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': -32601, 'message': f'no method {m}'}}
        return {'jsonrpc': '2.0', 'id': id_, 'result': res}

    def inproc(self, method, url, headers, body):
        """Signature of core.INPROC handlers."""
        return self.rpc(body or {}, headers)


def app(bank: MockBank = None):
    from fastapi import FastAPI, Request
    bank = bank or MockBank()
    api = FastAPI(title='mockbank — reference bank MCP server')

    @api.post('/mcp')
    async def mcp(request: Request):
        return bank.rpc(json.loads(await request.body() or b'{}'), dict(request.headers))

    @api.post('/credit')
    def credit(amount: float, name: str = '', iban: str = '', reference: str = ''):
        """Fake an incoming transfer (testnet only)."""
        return bank.credit(amount, name, iban, reference)

    return api


if __name__ == '__main__':
    import os
    import uvicorn
    uvicorn.run(app(), host='127.0.0.1', port=int(os.getenv('MOCKBANK_PORT', '50134')))
