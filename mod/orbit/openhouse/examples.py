"""
examples.py — testnet walkthroughs you can run, read and replay.

Each example is a short story told entirely in MCP tool calls: claim the
owner seat, set the deal, pay rent, pay it by bank transfer, import a real
bank statement, let a city freeze payments, bank through another bank's MCP
server. ``run(name, Mod)`` plays one in a THROWAWAY store (a temp directory,
its own bank key, its own sandbox bank) and returns the transcript:

    steps   [{n, tool, args, result | error, note}]   — every call, verbatim
    checks  [{claim, ok}]                              — what the story proves

Because every step is a real `openhouse_*` tool with its real arguments,
the transcript doubles as documentation and as a script: POST any step to
/mcp on the live testnet node and it does the same thing there.

Deterministic on purpose — fixed cast (demo.py's hash-derived addresses),
fixed fx rates, fixed amounts — so the tests assert on the same numbers the
site shows. Nothing here reaches a chain, a bank or the network.
"""
import importlib.util
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).parent
DAY = 24 * 3600


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
    return sys.modules[name]


def _tools():
    return _load('openhouse_mcp_tools', HERE / 'api' / 'mcp_server.py')


CAST = _load('openhouse_demo_cast', HERE / 'demo.py').CAST
SAMPLES = HERE / 'bank' / 'samples'
CITY_KEY = '0x' + 'c1' * 20        # a stand-in civic server key (testnet)
OWNER_IBAN = 'XT150000999988887777'
KEY_MASK = '‹operator bank key›'


class Run:
    """Plays tool calls against one Mod and writes down everything."""

    def __init__(self, oh):
        self.oh = oh
        self.mcp = _tools()
        self.steps, self.checks = [], []
        self.key = oh._bank_key()          # the throwaway store's own key

    def _shown(self, args):
        return {k: (KEY_MASK if k == 'key' else v) for k, v in args.items()}

    def call(self, tool, note='', **args):
        result = self.mcp.TOOLS[tool]['handler'](args, self.oh)
        self.steps.append({'n': len(self.steps) + 1, 'tool': tool,
                           'args': self._shown(args), 'result': result, 'note': note})
        return result

    def refused(self, tool, note='', **args):
        """A call the protocol is supposed to turn down — recorded with its reason."""
        try:
            self.mcp.TOOLS[tool]['handler'](args, self.oh)
        except self.mcp.ToolError as e:
            self.steps.append({'n': len(self.steps) + 1, 'tool': tool,
                               'args': self._shown(args), 'error': str(e), 'note': note})
            return str(e)
        raise AssertionError(f'{tool} was supposed to be refused and was not')

    def check(self, ok, claim):
        self.checks.append({'claim': claim, 'ok': bool(ok)})
        if not ok:
            raise AssertionError(claim)


EXAMPLES = {}


def example(name, title, shows, level='start'):
    def wrap(fn):
        EXAMPLES[name] = {'name': name, 'title': title, 'shows': shows,
                          'level': level, 'fn': fn}
        return fn
    return wrap


def _deal(r, **over):
    terms = dict(model='full_credit', fee_pct=2.5, home_price=120.0,
                 monthly_rent=0.62, owner=CAST['owner'], treasury=CAST['treasury'])
    terms.update(over)
    r.call('openhouse_claim_owner', 'The landlord takes the owner seat — first writer wins.',
           address=CAST['owner'])
    return r.call('openhouse_set_terms', 'The owner sets the deal. Only the owner can change it from now on.',
                  **terms)


# ── the stories ─────────────────────────────────────────────────

@example('first_rent', 'Your first rent payment',
         'Owner sets a full-credit deal; one month of rent becomes 97.5% equity.')
def first_rent(r):
    _deal(r)
    q = r.call('openhouse_quote', 'Preview what one month buys before paying.', amount=0.62)
    r.check(q['fee'] == 0.0155 and q['credit'] == 0.6045, '2.5% fee, 97.5% credited as equity')
    r.call('openhouse_pay_rent', 'Maya pays her first month.', renter=CAST['maya'], amount=0.62)
    eq = r.call('openhouse_equity', 'Her stake after one payment.', address=CAST['maya'])
    r.check(eq['principal'] == 0.6045, 'the principal on her account equals the quoted credit')
    r.check(abs(eq['equity_pct'] - 0.5037) < 1e-3, 'one month owns ~0.5% of a 120 Ξ home')
    st = r.call('openhouse_rent_stats', 'Where the money went across the whole property.')
    r.check(st['to_property_pct'] == 97.5, '97.5% of every Ξ stayed with the property')


@example('zero_fee', 'An owner who takes nothing',
         'The protocol fee can be 0%. It cannot be more than 5%.')
def zero_fee(r):
    _deal(r, model='hybrid', fee_pct=0)
    p = r.call('openhouse_pay_rent', 'Jonah pays 1 Ξ on a zero-fee 50/50 hybrid deal.',
               renter=CAST['jonah'], amount=1.0)
    r.check(p['fee'] == 0 and p['credit'] == 0.5 and p['owner_income'] == 0.5,
            'no fee; half equity, half owner income')
    err = r.refused('openhouse_set_terms', 'The owner tries a 7% fee — the contract band says no.',
                    fee_pct=7, owner=CAST['owner'])
    r.check('5' in err, 'fees above 5% are rejected')
    r.refused('openhouse_set_terms', 'A stranger tries to change the deal.',
              fee_pct=1, owner=CAST['ivy'])


@example('lease_option', 'Classic lease-option',
         'An upfront option fee is 100% equity; monthly rent credits 25%.')
def lease_option(r):
    _deal(r, model='classic', home_price=100.0, monthly_rent=0.5)
    o = r.call('openhouse_pay_rent', 'Maya pays the 3% option fee (kind=option: all equity).',
               renter=CAST['maya'], amount=3.0, kind='option')
    r.check(o['credit'] == round(3.0 * 0.975, 8), 'the whole net option fee is equity')
    m = r.call('openhouse_pay_rent', 'Then a normal month.', renter=CAST['maya'], amount=0.5)
    r.check(m['credit'] == round(0.5 * 0.975 * 0.25, 8), 'rent credits 25% of the net payment')
    r.call('openhouse_equity', 'Her stake: option fee plus one month.', address=CAST['maya'])


@example('payoff', 'Paying the house off',
         'Equity stops at the home price; the ledger refuses a payment past it.')
def payoff(r):
    _deal(r, fee_pct=0, home_price=2.0, monthly_rent=1.0)
    r.call('openhouse_pay_rent', 'Month one.', renter=CAST['maya'], amount=1.0)
    r.call('openhouse_pay_rent', 'Month two — the last one.', renter=CAST['maya'], amount=1.0)
    eq = r.call('openhouse_equity', 'She owns it.', address=CAST['maya'])
    r.check(eq['fully_owned'] and eq['remaining'] == 0, 'fully owned, nothing left to pay')
    err = r.refused('openhouse_pay_rent', 'A third payment has nothing left to buy.',
                    renter=CAST['maya'], amount=1.0)
    r.check('paid off' in err, 'the ledger refuses rent on a paid-off home')


@example('bank_transfer', 'Rent paid by bank transfer',
         'Sandbox bank: a renter wires rent with her reference code; the reconciler books it once.',
         level='bank')
def bank_transfer(r):
    _deal(r)
    r.call('openhouse_bank_connect', 'Open a sandbox bank account for the landlord (testnet, no key).',
           kind='sandbox', name='landlord', config={'currency': 'USD', 'holder': 'DEMO Landlord'})
    ref = r.call('openhouse_bank_reference', 'The code Maya puts in her transfer memo.',
                 address=CAST['maya'])['reference']
    r.call('openhouse_bank_link', 'Link Maya so her transfers are credited to her.',
           address=CAST['maya'], payer_name='Maya Ortiz')
    r.call('openhouse_bank_receive', 'Maya sends $1,550 from her bank with the code.',
           amount=1550, reference=f'Rent October {ref}', from_name='Maya Ortiz',
           from_iban='XT279876543210987654')
    r.call('openhouse_bank_receive', 'Someone else sends $300 with no code.',
           amount=300, reference='deposit', from_name='Unknown Sender')
    dry = r.call('openhouse_bank_reconcile', 'Preview first (1 Ξ = $2,500).', dry_run=True, rate=2500)
    r.check(len(dry['booked']) == 1 and dry['booked'][0]['units'] == 0.62,
            '$1,550 at $2,500/Ξ is 0.62 Ξ — one month')
    rec = r.call('openhouse_bank_reconcile', 'Book it.', rate=2500)
    r.check(len(rec['booked']) == 1 and len(rec['unmatched']) == 1,
            'Maya is booked; the stranger is left for the owner to look at')
    again = r.call('openhouse_bank_reconcile', 'Run it again — nothing is booked twice.', rate=2500)
    r.check(not again['booked'] and again['already_booked'] == 1, 'reconcile is idempotent')
    led = r.call('openhouse_rent_ledger', 'The ledger line knows which transfer paid it.',
                 renter=CAST['maya'])
    r.check(led['ledger'][0]['source']['fiat'] == 1550, 'the entry carries the bank transfer as its source')
    r.call('openhouse_bank_pay', 'The owner pays themself $500 of rent income.',
           amount=500, to_iban=OWNER_IBAN, to_name='DEMO Landlord', reference='owner draw')
    acc = r.call('openhouse_bank_accounts', 'What is left in the account.')
    r.check(acc['accounts'][0]['balance'] == 1350, '$1,550 + $300 − $500 = $1,350')


@example('bank_statement', 'Any bank, no API: a statement file',
         'Import a real-format ISO 20022 camt.053 statement, book two renters, pay a bill by pain.001.',
         level='bank')
def bank_statement(r):
    _deal(r)
    r.refused('openhouse_bank_connect', 'A statement is real bank data — without the operator key, no.',
              kind='statement', name='rent account')
    r.call('openhouse_bank_connect', 'With the operator key.', kind='statement', name='rent account',
           config={'holder': 'DEMO Landlord', 'iban': 'XT611234567890123456', 'currency': 'EUR'},
           key=r.key)
    for who, name in (('maya', 'Maya Ortiz'), ('jonah', 'Jonah Reyes')):
        r.call('openhouse_bank_link', f'Link {name}.', address=CAST[who], payer_name=name, key=r.key)
    camt = (SAMPLES / 'camt053.xml').read_text()
    imp = r.call('openhouse_bank_import', "Import September's camt.053, downloaded from online banking.",
                 content=camt, key=r.key)
    r.check(imp['format'] == 'camt053' and imp['new'] == 4, 'four entries read from the XML')
    dup = r.call('openhouse_bank_import', 'Import the same file again.', content=camt, key=r.key)
    r.check(dup['new'] == 0 and dup['duplicates'] == 4, 'overlapping statements never double up')
    rec = r.call('openhouse_bank_reconcile', 'Book the rent (1 Ξ = €2,300).', rate=2300, key=r.key)
    r.check(len(rec['booked']) == 2, 'both renters found by the code in their memo — even lower-cased')
    r.check(not rec['unmatched'], 'the plumber bill and the pending fee are not rent')
    pay = r.call('openhouse_bank_pay', 'Pay the plumber: an ISO 20022 pain.001 file to upload at the bank.',
                 amount=85, to_iban='XT720000111122223333', to_name='DEMO Plumbing Co',
                 currency='EUR', reference='Invoice 4411', key=r.key)
    r.check(pay['status'] == 'file_ready' and 'pain.001.001.03' in pay['xml'],
            'a standard credit-transfer file, ready for any bank portal')
    pay.pop('file', None)          # a temp path — meaningless outside this run


@example('city_pause', 'A city freezes payments',
         'A chartered housing authority pauses the property; bank rent is held, not lost, then booked.',
         level='civic')
def city_pause(r):
    _deal(r)
    r.call('openhouse_bank_connect', 'Sandbox bank for the landlord.', kind='sandbox', name='landlord')
    ref = r.call('openhouse_bank_reference', "Maya's code.", address=CAST['maya'])['reference']
    r.call('openhouse_bank_link', 'Link Maya.', address=CAST['maya'])
    r.call('openhouse_civic_charter', 'The owner charters the city into the civic seat.',
           key=CITY_KEY, name='DEMO City Housing Authority', region='US-OH', owner=CAST['owner'])
    r.call('openhouse_civic_override', 'The city pauses payments while it investigates a complaint.',
           action='pause', key=CITY_KEY, reason='habitability complaint #118')
    r.refused('openhouse_pay_rent', 'Direct payments are refused under the pause.',
              renter=CAST['maya'], amount=0.62)
    r.call('openhouse_bank_receive', 'Maya still pays by bank — the money arrives.',
           amount=1550, reference=ref, from_name='Maya Ortiz')
    held = r.call('openhouse_bank_reconcile', 'Reconcile: held, not booked, not lost.', rate=2500)
    r.check(len(held['held']) == 1 and not held['booked'], 'the transfer waits out the pause')
    r.call('openhouse_civic_override', 'Complaint resolved; the city lifts the pause.',
           action='unpause', key=CITY_KEY, reason='repairs verified')
    rec = r.call('openhouse_bank_reconcile', 'Reconcile again.', rate=2500)
    r.check(len(rec['booked']) == 1, 'the held transfer is booked once the pause lifts')
    r.call('openhouse_civic', 'Every override is on the public record.')


@example('bank_over_mcp', "A bank that speaks MCP",
         "Connect a bank through its own MCP server (the reference mockbank), read it, book rent, pay out through the bank's tool.",
         level='bank')
def bank_over_mcp(r):
    pkg = r.oh._bank_pkg()
    mockbank = importlib.import_module(pkg.__name__ + '.mockbank')
    core = importlib.import_module(pkg.__name__ + '.core')
    mount = 'mockbank-' + uuid.uuid4().hex[:8]
    bank = mockbank.MockBank(currency='USD', token='demo-bank-token')
    ref = pkg.reference_code(CAST['maya'])
    bank.credit(1550, 'Maya Ortiz', 'XT279876543210987654', f'rent {ref}')
    bank.credit(1550, 'Maya Ortiz', 'XT279876543210987654', 'rent (forgot the code)')
    core.INPROC[mount] = bank.inproc
    try:
        _deal(r)
        r.call('openhouse_bank_connect', "Connect the bank's MCP server: URL, its token, its tool names.",
               kind='mcp', name='mock bank', key=r.key,
               config={'url': f'inproc://{mount}/mcp', 'token': 'demo-bank-token',
                       'pay_tool': 'send_payment'})
        acc = r.call('openhouse_bank_accounts', "Read accounts through the bank's list_accounts tool.", key=r.key)
        r.check(acc['accounts'][0]['balance'] == 3100, 'balances come straight from the bank')
        r.call('openhouse_bank_link', 'Link Maya by code AND by her IBAN.', address=CAST['maya'],
               payer_iban='XT279876543210987654', key=r.key)
        rec = r.call('openhouse_bank_reconcile', 'Book what the bank reports (1 Ξ = $2,500).',
                     rate=2500, key=r.key)
        r.check(len(rec['booked']) == 2, 'one found by code, one by IBAN when she forgot it')
        r.check({b['matched_by'] for b in rec['booked']} == {'reference', 'iban'},
                'the match reason is on the record')
        pay = r.call('openhouse_bank_pay', "Pay the owner through the bank's own send_payment tool.",
                     amount=1000, to_iban=OWNER_IBAN, to_name='DEMO Landlord', account='chk-1',
                     reference='owner draw', key=r.key)
        r.check(pay['status'] == 'executed', 'the bank executed it')
        conns = r.call('openhouse_bank_status', 'Credentials never come back out.')
        r.check('demo-bank-token' not in str(conns), 'the bank token is not in any read')
    finally:
        core.INPROC.pop(mount, None)


# ── the runner ──────────────────────────────────────────────────

def catalog() -> list:
    return [{k: v for k, v in e.items() if k != 'fn'} for e in EXAMPLES.values()]


def run(name: str, Mod) -> dict:
    ex = EXAMPLES.get(name)
    if not ex:
        return {'error': f'no example {name!r} — have: {", ".join(EXAMPLES)}'}
    with tempfile.TemporaryDirectory(prefix='openhouse-example-') as d:
        r = Run(Mod(store=d))
        error = ''
        try:
            ex['fn'](r)
        except Exception as e:
            error = f'{type(e).__name__}: {e}'
    return {
        'name': name, 'title': ex['title'], 'shows': ex['shows'], 'level': ex['level'],
        'ok': not error, 'error': error,
        'steps': r.steps, 'checks': r.checks,
        'summary': f'{len(r.steps)} tool calls, {sum(c["ok"] for c in r.checks)}/'
                   f'{len(r.checks)} checks passed',
        'store': 'throwaway — ran in a temp directory; the live testnet store is untouched',
        'replay': 'each step is POST /mcp {"method":"tools/call","params":{"name":tool,'
                  '"arguments":args}} — the operator key is masked in this transcript',
    }
