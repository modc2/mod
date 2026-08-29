"""
Testing a contract by putting it on a real chain and pushing it.

There is a kind of contract testing that runs in a simulator and tells you the
code is fine, and a kind that deploys to a chain, sends transactions from a
funded key, reads the state back and tells you what the chain thought. This is
the second kind. It is slower and it is the one that catches the gas limit, the
revert string, the event that never fired and the constructor argument in the
wrong order.

The default network is a testnet — `ops.guard` already refuses a write on a
non-testnet chain without `confirm=true`, and a test suite is nothing but
writes, so "test it on the testnet" is not a convention here, it is what
happens unless you go out of your way.

A **suite** is JSON, because it has to survive a round trip through the store
and an MCP client:

    {
      "name": "erc20 basics",
      "contract": "Token",             # which contract in the source
      "args": ["Test", "TST", 1000],   # constructor arguments
      "value": "0",                    # sent to a payable constructor
      "cases": [
        {"name": "name is set",   "fn": "name",       "expect": "Test"},
        {"name": "minted to me",  "fn": "balanceOf",  "args": ["$deployer"],
                                  "expect_gt": 0},
        {"name": "transfer",      "fn": "transfer",   "args": ["$zero", 1],
                                  "expect_event": "Transfer"},
        {"name": "cannot overspend", "fn": "transfer",
                                  "args": ["$zero", "10**60"],
                                  "expect_revert": true}
      ]
    }

A case names one function. Whether that is a free `eth_call` or a signed
transaction is read off the ABI, not off the case — `view` and `pure` are
already the declaration of intent, and making the author repeat it is how a
suite ends up asserting on a call that never actually ran.

Placeholders, expanded in arguments and in expectations:

    $deployer / $sender   the address the suite is signed from
    $contract             the address this suite just deployed
    $zero                 the zero address
    $account:<name>       another of your keystore accounts, by name

Every run gets a report, and the report goes to the **store** like everything
else here — so "it passed" is a CID somebody else can fetch and read, rather
than a screenshot.
"""
import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

import chains
import ledger
import ops
import wallet
from projects import ProjectError
from store_link import LINK, StoreError

ZERO = '0x0000000000000000000000000000000000000000'
MAX_CASES = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS test_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  project TEXT,
  project_id INTEGER,
  contract TEXT,
  network TEXT NOT NULL,
  chain_id INTEGER,
  address TEXT,
  passed INTEGER NOT NULL,
  failed INTEGER NOT NULL,
  seconds REAL,
  cid TEXT,
  report TEXT NOT NULL,
  created INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_owner ON test_runs(owner, created DESC);
"""


class TestError(Exception):
    """The suite is malformed — the author's problem, not the chain's."""


def connect() -> sqlite3.Connection:
    conn = ledger.connect()
    conn.executescript(SCHEMA)
    return conn


# ── values ───────────────────────────────────────────────────────────

def _big(text: str) -> Optional[int]:
    """`"10**18"`, `"1_000"`, `"0x2a"`, `"42"` → an int, or None if it is not one.

    `10**18` is here because a token amount written out in full is 19 digits
    that nobody proof-reads, and a suite full of unreadable constants is a
    suite nobody edits.
    """
    text = str(text).strip().replace('_', '')
    if not text:
        return None
    try:
        if re.fullmatch(r'-?\d+\s*\*\*\s*\d+', text):
            base, _, exp = text.partition('**')
            return int(base) ** int(exp)
        if re.fullmatch(r'0[xX][0-9a-fA-F]+', text):
            return int(text, 16)
        if re.fullmatch(r'-?\d+', text):
            return int(text)
    except (ValueError, OverflowError):
        return None
    return None


def expand(value: Any, ctx: Dict[str, Any]) -> Any:
    """Placeholders → real values, everywhere in the case."""
    if isinstance(value, list):
        return [expand(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, ctx) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text in ('$deployer', '$sender'):
        return ctx['deployer']
    if text == '$contract':
        return ctx.get('address')
    if text == '$zero':
        return ZERO
    if text.startswith('$account:'):
        name = text.split(':', 1)[1].strip()
        try:
            return wallet.address_of(ctx['owner'], name)
        except Exception:
            raise TestError(f'{text} — you have no account called {name!r}')
    if text.startswith('$'):
        raise TestError(f'{text} is not a placeholder this runner knows')
    big = _big(text)
    return big if big is not None else value


def normalise(value: Any) -> Any:
    """One shape for comparison, so `1`, `"1"` and `0x1` are one value.

    Addresses fold to lowercase because a checksum is a display convention;
    an assertion that fails on capitalisation is testing the formatter.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        return [normalise(v) for v in value]
    if isinstance(value, bytes):
        return '0x' + value.hex()
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r'0[xX][0-9a-fA-F]{40}', text):
            return text.lower()
        big = _big(text)
        return big if big is not None else text
    return value


def _as_hex(value: Any) -> str:
    """One hex spelling, so `b"\x01\x02"` and `"0x0102"` are the same value."""
    if isinstance(value, (bytes, bytearray)):
        return '0x' + bytes(value).hex()
    return str(value).strip().lower()


def same(got: Any, want: Any) -> bool:
    """Did the chain return what the case asked for.

    Bytes get their own arm: `normalise` turns a `bytes32` into a hex string
    and a hex string into an int, so a byte string and the hex the author
    wrote for it would otherwise never compare equal — the exact case where
    the runner reports a false failure on a correct contract.
    """
    if isinstance(got, (list, tuple)) and isinstance(want, (list, tuple)):
        return len(got) == len(want) and all(same(a, b) for a, b in zip(got, want))
    if isinstance(got, (bytes, bytearray)) or isinstance(want, (bytes, bytearray)):
        return _as_hex(got) == _as_hex(want)
    return normalise(got) == normalise(want)


def _numeric(value: Any) -> Optional[int]:
    got = normalise(value)
    if isinstance(got, bool):
        return None
    return got if isinstance(got, int) else None


# ── the suite ────────────────────────────────────────────────────────

def _cases_of(suite: Dict[str, Any]) -> List[Dict[str, Any]]:
    cases = suite.get('cases') or suite.get('tests') or []
    if not isinstance(cases, list):
        raise TestError('`cases` must be a list')
    if len(cases) > MAX_CASES:
        raise TestError(f'{len(cases)} cases is more than one suite runs '
                        f'({MAX_CASES})')
    return cases


def _fn_of(case: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """The function a case exercises, and whether the author forced its mode."""
    for key, forced in (('fn', None), ('call', 'read'), ('send', 'write'),
                        ('function', None)):
        if case.get(key):
            return str(case[key]), forced
    raise TestError(f'a case needs a function: {json.dumps(case)[:120]}')


def _abi_entry(abi: List[dict], name: str) -> Optional[dict]:
    return next((e for e in abi if e.get('type') == 'function'
                 and e.get('name') == name), None)


def _is_view(entry: Optional[dict]) -> bool:
    if not entry:
        return False
    return entry.get('stateMutability') in ('view', 'pure') or entry.get('constant')


def generate(abi: List[dict], name: str = 'smoke') -> Dict[str, Any]:
    """A starter suite read off the ABI: call everything that is free.

    Deliberately assertion-free. A generated expectation would be a guess
    dressed as a requirement; what this gives you is "the constructor ran and
    every getter answers", which is exactly the test people write by hand first
    and the one they skip when it is tedious.
    """
    cases: List[Dict[str, Any]] = []
    for entry in abi or []:
        if entry.get('type') != 'function' or not _is_view(entry):
            continue
        if entry.get('inputs'):
            continue
        cases.append({'name': f'{entry["name"]}() answers', 'fn': entry['name'],
                      'args': []})
    ctor = next((e.get('inputs') or [] for e in abi or []
                 if e.get('type') == 'constructor'), [])
    note = ('read off the ABI — every free getter is called and has to return '
            'without reverting. Add expectations to make it a test.')
    out = {'name': name, 'generated': True, 'note': note,
           'cases': cases[:MAX_CASES]}
    if ctor:
        # `args: null` rather than a guessed value: a null falls through to
        # whatever the caller passes (the constructor row on the bench), and a
        # made-up "" or 0 would silently deploy the wrong contract.
        out['args'] = None
        out['constructor'] = [{'name': i.get('name'), 'type': i.get('type')}
                              for i in ctor]
        out['note'] = (note + ' The constructor takes '
                       + ', '.join(f'{i.get("type")} {i.get("name") or ""}'.strip()
                                   for i in ctor)
                       + ' — fill `args` here, or leave it null and use the '
                         'constructor row on the bench.')
    return out


# ── running one case ─────────────────────────────────────────────────

def _revert_text(error: Exception) -> str:
    return str(error)


def _looks_reverted(error: Exception) -> bool:
    text = str(error).lower()
    return ('revert' in text or 'invalid opcode' in text
            or 'out of gas' in text or 'execution reverted' in text)


def _check(case: Dict[str, Any], result: Any, receipt: Optional[dict],
           events: List[dict], ctx: Dict[str, Any]) -> Tuple[bool, str]:
    """Every expectation on one case. Returns (passed, what happened)."""
    checks = 0

    if 'expect' in case:
        checks += 1
        want = expand(case['expect'], ctx)
        if not same(result, want):
            return False, f'expected {json.dumps(normalise(want), default=str)}, ' \
                          f'got {json.dumps(normalise(result), default=str)}'

    for key, test, label in (
            ('expect_gt', lambda a, b: a > b, 'greater than'),
            ('expect_gte', lambda a, b: a >= b, 'at least'),
            ('expect_lt', lambda a, b: a < b, 'less than'),
            ('expect_lte', lambda a, b: a <= b, 'at most')):
        if key in case:
            checks += 1
            want = _numeric(expand(case[key], ctx))
            got = _numeric(result)
            if want is None:
                return False, f'{key} needs a number, not {case[key]!r}'
            if got is None:
                return False, f'{key} needs a number back, got ' \
                              f'{json.dumps(normalise(result), default=str)[:120]}'
            if not test(got, want):
                return False, f'expected {label} {want}, got {got}'

    if 'expect_contains' in case:
        checks += 1
        want = str(expand(case['expect_contains'], ctx))
        if want not in json.dumps(normalise(result), default=str):
            return False, f'{want!r} is not in ' \
                          f'{json.dumps(normalise(result), default=str)[:200]}'

    if case.get('expect_event'):
        checks += 1
        wanted = case['expect_event']
        name = wanted if isinstance(wanted, str) else wanted.get('name')
        fired = [e for e in events if e.get('event') == name]
        if not fired:
            seen = ', '.join(sorted({e.get('event') or '?' for e in events})) or 'none'
            return False, f'no {name} event — events fired: {seen}'
        if isinstance(wanted, dict) and wanted.get('args'):
            want_args = expand(wanted['args'], ctx)
            if not any(all(same((e.get('args') or {}).get(k), v)
                           for k, v in want_args.items()) for e in fired):
                return False, f'{name} fired but not with ' \
                              f'{json.dumps(normalise(want_args), default=str)[:200]}'

    if case.get('expect_status'):
        checks += 1
        want, got = case['expect_status'], (receipt or {}).get('status')
        if str(want) != str(got):
            return False, f'expected the transaction to be {want}, it was {got}'

    if checks == 0:
        return True, 'ran without reverting (no expectation on this case)'
    return True, 'ok'


def run_case(case: Dict[str, Any], abi: List[dict], ctx: Dict[str, Any]) -> Dict[str, Any]:
    started = time.time()
    fn, forced = _fn_of(case)
    entry = _abi_entry(abi, fn)
    if entry is None:
        return {'name': case.get('name') or fn, 'fn': fn, 'ok': False,
                'why': f'{fn!r} is not in the ABI — '
                       f'{", ".join(sorted(e["name"] for e in abi if e.get("type") == "function"))[:200]}',
                'seconds': 0}
    mode = forced or ('read' if _is_view(entry) else 'write')
    args = expand(case.get('args') or [], ctx)
    wants_revert = 'expect_revert' in case and case['expect_revert'] is not False

    out: Dict[str, Any] = {'name': case.get('name') or f'{fn}()', 'fn': fn,
                           'mode': mode, 'args': args}
    result: Any = None
    receipt: Optional[dict] = None
    events: List[dict] = []

    try:
        if mode == 'read':
            got = ops.read(ctx['address'], fn, args, network=ctx['network'],
                           abi=abi, owner=ctx['owner'])
            result = got.get('result')
        else:
            got = ops.write(ctx['owner'], ctx['account'], ctx['address'], fn,
                            args=args, network=ctx['network'], abi=abi,
                            value=expand(case.get('value', 0), ctx),
                            password=ctx.get('password'),
                            gas=case.get('gas'), confirm=ctx['confirm'])
            out['hash'] = got.get('hash')
            out['gas_used'] = got.get('gas_used')
            out['explorer'] = got.get('explorer')
            receipt = {'status': got.get('status')}
            if got.get('status') == 'reverted':
                raise ops.OpError('the transaction reverted on chain')
            events = _events(ctx, got.get('hash'), abi)
            # A transaction returns a receipt, not a value: whatever the
            # function's return type says, the only thing that crossed the
            # wire is gas, status and logs. Assert on those, or read the
            # state back in the next case.
            result = None
    except Exception as e:
        if wants_revert and _looks_reverted(e):
            want = case['expect_revert']
            text = _revert_text(e)
            if isinstance(want, str) and want.lower() not in text.lower():
                out.update({'ok': False, 'why': f'reverted, but not with {want!r}: '
                                                f'{text[:240]}'})
            else:
                out.update({'ok': True, 'why': 'reverted, as the case required',
                            'revert': text[:400]})
            out['seconds'] = round(time.time() - started, 3)
            return out
        out.update({'ok': False, 'why': _revert_text(e)[:400],
                    'seconds': round(time.time() - started, 3)})
        return out

    if wants_revert:
        out.update({'ok': False, 'why': 'expected a revert and the call went '
                                        'through', 'result': result,
                    'seconds': round(time.time() - started, 3)})
        return out

    out['result'] = result
    if events:
        out['events'] = [{'event': e.get('event'), 'args': e.get('args')}
                         for e in events]
    ok, why = _check(case, result, receipt, events, ctx)
    out.update({'ok': ok, 'why': why, 'seconds': round(time.time() - started, 3)})
    return out


def _events(ctx: Dict[str, Any], tx_hash: Optional[str],
            abi: List[dict]) -> List[dict]:
    """Decoded events from one transaction, filtered to this contract.

    A failure to decode is not a test failure: the assertion below will say
    "no Transfer event", which is true and readable, rather than exploding the
    run with a web3 traceback.
    """
    if not tx_hash:
        return []
    try:
        w3 = chains.client(ctx['network'])
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        entries = [log for log in receipt['logs']
                   if str(log['address']).lower() == str(ctx['address']).lower()]
        return ops._decode_logs(w3, ctx['address'], abi, entries)
    except Exception:
        return []


# ── running a suite ──────────────────────────────────────────────────

def run(owner: str, account: str, network: Optional[str] = None,
        files: Optional[Dict[str, str]] = None, source: Optional[str] = None,
        project: Optional[str] = None, contract: Optional[str] = None,
        suites: Optional[Any] = None, args: Optional[List[Any]] = None,
        value: Any = 0, password: Optional[str] = None,
        address: Optional[str] = None, abi: Optional[Any] = None,
        solc: Optional[str] = None, optimize: bool = True,
        confirm: bool = False, token: Optional[str] = None,
        store_report: bool = True) -> Dict[str, Any]:
    """Deploy, exercise, report.

    `address` runs the suite against a contract that is already out there —
    the same cases, no fresh deploy. Everything else compiles the project and
    puts a new instance on the chain first, which is what you want while the
    contract is still changing.
    """
    import projects

    started = time.time()
    spec = chains.resolve(network)
    ops.guard(spec, confirm)              # a testnet is free; a mainnet is not
    owner = (owner or '').lower()

    name, project_id = None, None
    if project:
        row = projects.get(owner, project)
        files = files or row['files']
        name, project_id = row['name'], row['id']
        suites = row.get('tests') if suites is None else suites
        contract = contract or (row.get('settings') or {}).get('contract')
    if not files and source:
        files = {'Contract.sol': source}
    if not files and not (address and abi):
        raise TestError('give me a project, some files, or an address with '
                        'an ABI to run against')

    suites = _normalise_suites(suites)

    # One compile for every suite in the run: the source does not change
    # between them, and solc is the slow part.
    compiled = None
    if files:
        import compiler
        compiled = compiler.compile_sources(files, version=solc, optimize=optimize)

    results = []
    for suite in suites:
        results.append(_run_one(owner, account, spec, suite, files, compiled,
                                contract, args, value, password, address, abi,
                                confirm, solc, optimize))

    passed = sum(r['passed'] for r in results)
    failed = sum(r['failed'] for r in results)
    report = {
        'kind': 'eth.test-report/1',
        'project': name or project,
        'network': spec['name'],
        'chain_id': spec.get('chain_id'),
        'testnet': bool(spec.get('testnet')),
        'explorer': spec.get('explorer'),
        'ran_by': owner,
        'account': account,
        'suites': results,
        'passed': passed,
        'failed': failed,
        'ok': failed == 0,
        'total': passed + failed,
        'seconds': round(time.time() - started, 3),
        'when': int(time.time()),
        'compiler': (compiled or {}).get('compiler'),
    }
    report.update(_record(owner, project, project_id, report, token,
                          store_report))
    return report


def _normalise_suites(suites: Any) -> List[Dict[str, Any]]:
    if suites is None:
        return [{'name': 'smoke', 'auto': True}]
    if isinstance(suites, str):
        try:
            suites = json.loads(suites)
        except json.JSONDecodeError:
            raise TestError('the suite is not JSON')
    if isinstance(suites, dict):
        suites = [suites]
    if not isinstance(suites, list) or not suites:
        return [{'name': 'smoke', 'auto': True}]
    for suite in suites:
        if not isinstance(suite, dict):
            raise TestError('each suite must be an object with a `cases` list')
    return suites


def _run_one(owner, account, spec, suite, files, compiled, contract, args,
             value, password, address, abi, confirm, solc,
             optimize) -> Dict[str, Any]:
    started = time.time()
    which = suite.get('contract') or contract
    out: Dict[str, Any] = {'name': suite.get('name') or 'suite',
                           'network': spec['name']}

    if address:
        target, used_abi, deployed = address, abi, None
        if used_abi is None and compiled:
            chosen = _choose(compiled, which)
            used_abi = chosen['abi']
        if used_abi is None:
            # abi_for hands back the whole index row, not the ABI itself.
            used_abi = (ledger.abi_for(address, spec.get('chain_id'), owner)
                        or {}).get('abi')
        if not used_abi:
            out.update({'passed': 0, 'failed': 1, 'cases': [
                {'name': 'abi', 'ok': False,
                 'why': f'no ABI known for {address} — attach one first'}]})
            return out
    else:
        if not compiled:
            out.update({'passed': 0, 'failed': 1, 'cases': [
                {'name': 'compile', 'ok': False,
                 'why': 'nothing to deploy: no source in this run'}]})
            return out
        chosen = _choose(compiled, which)
        ctor_args = suite.get('args') if suite.get('args') is not None else (args or [])
        try:
            deployed = ops.deploy(
                owner, account, network=spec['name'], sources=files,
                contract=chosen['name'], args=list(ctor_args),
                value=suite.get('value', value), password=password, solc=solc,
                optimize=optimize, confirm=confirm,
                note=f'test run: {out["name"]}')
        except Exception as e:
            out.update({'passed': 0, 'failed': 1, 'seconds': round(time.time() - started, 3),
                        'contract': chosen['name'], 'cases': [
                            {'name': 'deploy', 'ok': False, 'why': str(e)[:400]}]})
            return out
        target = deployed.get('address')
        used_abi = deployed.get('abi') or chosen['abi']
        if not target:
            out.update({'passed': 0, 'failed': 1, 'contract': chosen['name'],
                        'seconds': round(time.time() - started, 3), 'cases': [
                            {'name': 'deploy', 'ok': False,
                             'why': deployed.get('error') or 'the deployment '
                                    'never produced an address'}]})
            return out
        out['contract'] = chosen['name']
        out['deploy'] = {'hash': deployed.get('hash'),
                         'gas_used': deployed.get('gas_used'),
                         'explorer': deployed.get('explorer')}

    out['address'] = target
    out['explorer'] = chains.explorer_link(spec, 'address', target)

    ctx = {'owner': owner, 'account': account, 'address': target,
           'network': spec['name'], 'password': password, 'confirm': confirm,
           'deployer': _deployer(owner, account)}

    cases = generate(used_abi)['cases'] if suite.get('auto') else _cases_of(suite)
    if suite.get('auto'):
        out['generated'] = True
        out['note'] = ('no suite was given, so every free getter on the ABI was '
                       'called — this proves the deploy, not the behaviour')
    ran = [run_case(case, used_abi, ctx) for case in cases]
    if not ran:
        ran = [{'name': 'deployed', 'ok': True,
                'why': 'the contract deployed and has code on chain', 'mode': 'read'}]
    out['cases'] = ran
    out['passed'] = sum(1 for c in ran if c['ok'])
    out['failed'] = sum(1 for c in ran if not c['ok'])
    out['seconds'] = round(time.time() - started, 3)
    return out


def _choose(compiled: Dict[str, Any], which: Optional[str]) -> Dict[str, Any]:
    deployable = [c for c in compiled['contracts'] if c['deployable']]
    if not deployable:
        raise TestError('this source has nothing deployable in it')
    if which:
        chosen = next((c for c in deployable if c['name'] == which), None)
        if chosen is None:
            raise TestError(f'{which!r} is not in this source — found '
                            f'{", ".join(c["name"] for c in deployable)}')
        return chosen
    if len(deployable) == 1:
        return deployable[0]
    raise TestError('this source declares several deployable contracts '
                    f'({", ".join(c["name"] for c in deployable)}) — name one '
                    f'with contract=')


def _deployer(owner: str, account: str) -> Optional[str]:
    try:
        return wallet.address_of(owner, account)
    except Exception:
        return None


# ── reports ──────────────────────────────────────────────────────────

def _record(owner: str, project: Optional[str], project_id: Optional[int],
            report: Dict[str, Any], token: Optional[str],
            store_report: bool) -> Dict[str, Any]:
    """Keep the run, and put the report in the store when we can.

    A report that only exists in one box's sqlite proves nothing to anybody
    else. With a CID, "the suite passed on Base Sepolia" is a fetchable object
    with the transaction hashes in it.
    """
    out: Dict[str, Any] = {}
    cid = None
    if store_report and token:
        try:
            pushed = LINK.put_json(token, f'eth-test-{int(time.time())}.json',
                                   report, public=False)
            cid = pushed.get('cid')
            out['store'] = {'cid': cid, 'stored': True}
        except StoreError as e:
            out['store'] = {'cid': None, 'stored': False, 'reason': e.message}
    elif store_report:
        out['store'] = {'cid': None, 'stored': False,
                        'reason': 'not signed in, so the report stayed local'}

    first = (report.get('suites') or [{}])[0]
    with connect() as conn:
        cursor = conn.execute(
            'INSERT INTO test_runs (owner, project, project_id, contract, '
            'network, chain_id, address, passed, failed, seconds, cid, report, '
            'created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (owner, str(project) if project else None, project_id,
             first.get('contract'), report['network'], report.get('chain_id'),
             first.get('address'), report['passed'], report['failed'],
             report['seconds'], cid, json.dumps(report), int(time.time())))
        out['id'] = cursor.lastrowid
    out['cid'] = cid
    return out


def runs(owner: str, limit: int = 30, project: Optional[str] = None) -> List[Dict[str, Any]]:
    owner = (owner or '').lower()
    sql = ('SELECT id, project, contract, network, chain_id, address, passed, '
           'failed, seconds, cid, created FROM test_runs WHERE owner=?')
    params: List[Any] = [owner]
    if project:
        sql += ' AND project=?'
        params.append(str(project))
    sql += ' ORDER BY created DESC LIMIT ?'
    params.append(int(limit))
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def report(owner: str, run_id: int) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute('SELECT * FROM test_runs WHERE id=? AND owner=?',
                           (int(run_id), (owner or '').lower())).fetchone()
    if row is None:
        raise ProjectError(f'no test run {run_id} of yours')
    out = dict(row)
    try:
        out['report'] = json.loads(out['report'])
    except json.JSONDecodeError:
        pass
    return out
