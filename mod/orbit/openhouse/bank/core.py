"""
bank/core.py — one shape for every bank, and the reconciler that turns a
bank transfer into rent.

Banks speak dozens of dialects: Berlin Group PSD2 JSON, UK Open Banking
JSON, ISO 20022 camt.053 XML, SWIFT MT940 text, OFX, a CSV export with
whatever column names the bank felt like, and — increasingly — an MCP
server of their own. This file is the part that doesn't care which:

  Account   {id, name, currency, iban, balance}
  Txn       {id, account, date, amount, currency, counterparty,
             counterparty_iban, reference, status}
            amount is SIGNED from the account holder's side: + is money in.
  Payment   {id, account, to_name, to_iban, amount, currency, reference,
             status, created}

An adapter (sandbox.py, statements.py, openbanking.py, remote.py) turns its
dialect into those three shapes and nothing else. Everything above the
adapters — the connection store, renter links, reconciliation — only ever
sees the normalized shapes, so a new bank is one new adapter file.

Deliberately free of any openhouse import: the reconciler takes a `record`
callback and a `to_units` converter, so this package can be lifted into any
module that needs "did the money arrive, and whose was it".

Secrets: connection credentials live in `connections.json` under the
secrets directory the caller hands in (openhouse passes ~/.mod/openhouse/,
off the repo, chmod 600), and every read path returns them redacted.
"""
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ── normalized shapes ───────────────────────────────────────────


def _f(v, default=0.0) -> float:
    """Parse a bank amount: '1.234,56' / '1,234.56' / '-12' / 12.0."""
    if v is None or v == '':
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(' ', '').replace(' ', '')
    s = re.sub(r'[^\d,.\-+]', '', s)
    if ',' in s and '.' in s:
        # whichever comes last is the decimal mark
        if s.rfind(',') > s.rfind('.'):
            s = s.replace('.', '').replace(',', '.')
        else:
            s = s.replace(',', '')
    elif ',' in s:
        # a lone comma followed by exactly 1-2 digits is a decimal mark
        s = s.replace(',', '.') if re.search(r',\d{1,2}$', s) else s.replace(',', '')
    try:
        return float(s)
    except ValueError:
        return default


_DATE_FORMATS = ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f',
                 '%Y%m%d', '%Y%m%d%H%M%S', '%d/%m/%Y', '%m/%d/%Y', '%d.%m.%Y',
                 '%y%m%d', '%Y/%m/%d', '%d-%m-%Y')


def parse_date(v) -> int:
    """Any bank date → unix seconds (UTC). 0 when unparseable."""
    import calendar
    import datetime as dt
    if v is None or v == '':
        return 0
    if isinstance(v, (int, float)):
        return int(v / 1000) if v > 1e11 else int(v)
    s = str(v).strip()
    if s.isdigit() and len(s) >= 9 and len(s) not in (12, 14):
        n = int(s)
        return int(n / 1000) if n > 1e11 else n
    # OFX: 20260915120000[-5:EST] / 20260915120000.000
    s = re.sub(r'\[.*\]$', '', s)
    s = re.sub(r'(\d{14})\.\d+$', r'\1', s)
    s = re.sub(r'(Z|[+-]\d{2}:?\d{2})$', '', s)
    # YYMMDD (MT940) would otherwise parse as year 2609 under %Y%m%d
    fmts = ('%y%m%d',) if re.fullmatch(r'\d{6}', s) else _DATE_FORMATS
    for fmt in fmts:
        try:
            return calendar.timegm(dt.datetime.strptime(s, fmt).timetuple())
        except ValueError:
            continue
    return 0


def norm_iban(v) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(v or '').upper())


def txn(**kw) -> dict:
    """Build a normalized Txn; derives a stable id when the bank gave none."""
    t = {
        'id': str(kw.get('id') or ''),
        'account': str(kw.get('account') or ''),
        'date': parse_date(kw.get('date')),
        'amount': round(_f(kw.get('amount')), 2),
        'currency': str(kw.get('currency') or '').upper(),
        'counterparty': str(kw.get('counterparty') or '').strip(),
        'counterparty_iban': norm_iban(kw.get('counterparty_iban')),
        'reference': str(kw.get('reference') or '').strip(),
        'status': str(kw.get('status') or 'booked'),
    }
    if not t['id']:
        t['id'] = 'h' + hashlib.sha256(json.dumps(
            [t['account'], t['date'], t['amount'], t['counterparty'],
             t['reference'], kw.get('seq', 0)]).encode()).hexdigest()[:16]
    return t


def account(**kw) -> dict:
    return {
        'id': str(kw.get('id') or ''),
        'name': str(kw.get('name') or kw.get('id') or ''),
        'currency': str(kw.get('currency') or '').upper(),
        'iban': norm_iban(kw.get('iban')),
        'balance': None if kw.get('balance') is None else round(_f(kw.get('balance')), 2),
    }


# Every dialect names the same five things differently. One lookup table,
# used by the CSV reader and the remote-MCP adapter alike.
KEYS = {
    'id':       ('id', 'transactionid', 'transaction_id', 'fitid', 'txn_id', 'reference_id', 'entryreference'),
    'date':     ('date', 'bookingdate', 'booking_date', 'booked', 'posted', 'dtposted', 'value_date', 'valuedate', 'transaction_date', 'created', 'timestamp', 'bookingdatetime'),
    'amount':   ('amount', 'transactionamount', 'value', 'trnamt', 'sum'),
    'credit':   ('credit', 'paid_in', 'money_in', 'deposit', 'in'),
    'debit':    ('debit', 'paid_out', 'money_out', 'withdrawal', 'out'),
    'currency': ('currency', 'ccy', 'curr'),
    'counterparty': ('counterparty', 'payee', 'payer', 'name', 'debtorname', 'creditorname', 'counterparty_name', 'from', 'merchant'),
    'counterparty_iban': ('counterparty_iban', 'iban', 'debtoriban', 'creditoriban', 'counterparty_account', 'account_number'),
    'reference': ('reference', 'memo', 'description', 'details', 'remittanceinformationunstructured', 'remittance', 'narrative', 'transactioninformation', 'purpose', 'text'),
    'status':   ('status', 'bookingstatus', 'state'),
}


def pick(row: dict, field: str, default=None):
    """First present key for `field`, case/space/underscore-insensitive."""
    flat = {re.sub(r'[\s_\-]', '', str(k).lower()): v for k, v in row.items()}
    for k in KEYS[field]:
        v = flat.get(k.replace('_', ''))
        if v not in (None, ''):
            return v
    return default


def txn_from_any(row: dict, account_id: str = '', currency: str = '', seq: int = 0) -> dict:
    """Best-effort normalize one transaction from any JSON/CSV-ish dict."""
    amt = pick(row, 'amount')
    if isinstance(amt, dict):          # {"amount": "12.00", "currency": "EUR"}
        currency = currency or amt.get('currency') or amt.get('Currency') or ''
        amt = amt.get('amount', amt.get('Amount'))
    if amt in (None, ''):
        amt = _f(pick(row, 'credit')) - abs(_f(pick(row, 'debit')))
    else:
        amt = _f(amt)
        ind = str(row.get('CreditDebitIndicator') or row.get('credit_debit') or '').lower()
        if ind.startswith('debit') and amt > 0:
            amt = -amt
    iban = pick(row, 'counterparty_iban')
    if isinstance(iban, dict):
        iban = iban.get('iban') or iban.get('Identification') or ''
    return txn(id=pick(row, 'id'), account=account_id, date=pick(row, 'date'),
               amount=amt, currency=pick(row, 'currency') or currency,
               counterparty=pick(row, 'counterparty'), counterparty_iban=iban,
               reference=pick(row, 'reference'), status=pick(row, 'status') or 'booked',
               seq=seq)


# ── the adapter contract ────────────────────────────────────────


class BankError(Exception):
    """An adapter refused or the bank said no — reported, never a 500."""


class Adapter:
    """One bank dialect. Subclasses set the class attributes and implement
    the three verbs; `caps` says which verbs are real for this dialect."""

    kind = ''
    label = ''
    live = False            # True when it talks to a real bank (money can move)
    caps = ('accounts', 'transactions')
    # [{name, required, secret, help}] — drives connect() validation and the
    # redaction on every read path.
    fields: List[dict] = []

    def __init__(self, conn: dict, data_dir: Path):
        self.conn = conn
        self.data_dir = data_dir

    @property
    def cfg(self) -> dict:
        return self.conn.get('config', {})

    def accounts(self) -> List[dict]:
        raise BankError(f'{self.kind}: accounts not supported')

    def transactions(self, account: str = '', since: int = 0) -> List[dict]:
        raise BankError(f'{self.kind}: transactions not supported')

    def pay(self, account: str, to_name: str, to_iban: str, amount: float,
            currency: str, reference: str) -> dict:
        raise BankError(f'{self.kind}: outbound payments not supported — '
                        'connect a "statement" bank to get an ISO 20022 '
                        'pain.001 file to upload instead')

    @classmethod
    def describe(cls) -> dict:
        return {'kind': cls.kind, 'label': cls.label, 'live': cls.live,
                'caps': list(cls.caps), 'fields': cls.fields,
                'doc': (cls.__doc__ or '').strip()}


# ── the hub ─────────────────────────────────────────────────────

# One reconcile at a time per data directory: two overlapping runs would both
# see a transfer as unbooked and book it twice. (One process per node; the
# API's thread pool is the concurrency this guards against.)
_RECONCILE_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _reconcile_lock(data_dir) -> threading.Lock:
    with _LOCKS_GUARD:
        return _RECONCILE_LOCKS.setdefault(str(Path(data_dir).resolve()), threading.Lock())


def reference_code(address: str) -> str:
    """The code a renter types into the transfer memo: OH-7F3A2C.

    Derived from the address so it is stable, needs no store to look up, and
    is short enough to fit every bank's reference field (SEPA allows 140,
    ACH addenda 80, Faster Payments 18)."""
    return 'OH-' + hashlib.sha256((address or '').lower().encode()).hexdigest()[:6].upper()


def _squash(s: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


class Bank:
    """Connections + links + reconciliation over a directory of JSON.

    data_dir     non-secret state: links, matched transactions, sandbox books,
                 imported statements, the pain.001 outbox
    secrets_dir  connections.json (credentials), written 0600
    """

    def __init__(self, data_dir, secrets_dir, adapters: Dict[str, type]):
        self.data_dir = Path(data_dir)
        self.secrets_dir = Path(secrets_dir)
        self.adapters = adapters

    # storage ------------------------------------------------------

    def _read(self, path: Path, default):
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            return default

    def _write(self, path: Path, data, private=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(json.dumps(data, indent=2, default=str))
        if private:
            os.chmod(tmp, 0o600)
        tmp.replace(path)

    @property
    def _conns_path(self):
        return self.secrets_dir / 'connections.json'

    @property
    def _links_path(self):
        return self.data_dir / 'links.json'

    @property
    def _matched_path(self):
        return self.data_dir / 'matched.json'

    def _conns(self) -> dict:
        return self._read(self._conns_path, {})

    def _redact(self, conn: dict) -> dict:
        cls = self.adapters.get(conn.get('kind'))
        secret = {f['name'] for f in (cls.fields if cls else []) if f.get('secret')}
        cfg = {k: ('••••' + str(v)[-4:] if k in secret and v else v)
               for k, v in conn.get('config', {}).items()}
        return {**conn, 'config': cfg}

    # connections --------------------------------------------------

    def kinds(self) -> list:
        return [cls.describe() for cls in self.adapters.values()]

    def connect(self, kind: str, name: str = '', config: Optional[dict] = None) -> dict:
        cls = self.adapters.get(kind)
        if not cls:
            raise BankError(f'unknown bank kind {kind!r} — have {", ".join(self.adapters)}')
        config = {k: v for k, v in (config or {}).items() if v not in (None, '')}
        missing = [f['name'] for f in cls.fields if f.get('required') and not config.get(f['name'])]
        if missing:
            raise BankError(f'{kind} needs: {", ".join(missing)}')
        known = {f['name'] for f in cls.fields}
        extra = set(config) - known
        if extra:
            raise BankError(f'{kind} does not take: {", ".join(sorted(extra))}')
        conns = self._conns()
        base = re.sub(r'[^a-z0-9]+', '-', (name or kind).lower()).strip('-') or kind
        cid, n = base, 2
        while cid in conns:
            cid, n = f'{base}-{n}', n + 1
        conn = {'id': cid, 'kind': kind, 'name': name or cls.label,
                'live': cls.live, 'config': config, 'created': int(time.time())}
        conns[cid] = conn
        self._write(self._conns_path, conns, private=True)
        adapter = cls(conn, self.data_dir)
        if hasattr(adapter, 'on_connect'):
            adapter.on_connect()
        return self._redact(conn)

    def connections(self) -> list:
        return [self._redact(c) for c in self._conns().values()]

    def disconnect(self, conn_id: str) -> dict:
        conns = self._conns()
        if conn_id not in conns:
            raise BankError(f'no connection {conn_id!r}')
        gone = conns.pop(conn_id)
        self._write(self._conns_path, conns, private=True)
        return {'removed': conn_id, 'kind': gone['kind']}

    def adapter(self, conn_id: str = '') -> Adapter:
        conns = self._conns()
        if not conn_id:
            if len(conns) != 1:
                raise BankError('connection required — have: '
                                + (', '.join(conns) or 'none; connect one first'))
            conn_id = next(iter(conns))
        conn = conns.get(conn_id)
        if not conn:
            raise BankError(f'no connection {conn_id!r} — have: {", ".join(conns) or "none"}')
        return self.adapters[conn['kind']](conn, self.data_dir)

    # verbs --------------------------------------------------------

    def accounts(self, conn_id: str = '') -> list:
        return self.adapter(conn_id).accounts()

    def transactions(self, conn_id: str = '', account: str = '', since: int = 0,
                     limit: int = 200) -> list:
        rows = self.adapter(conn_id).transactions(account=account, since=int(since or 0))
        # newest first; same-second rows keep the bank's own order, reversed
        order = sorted(range(len(rows)), key=lambda i: (rows[i]['date'], i), reverse=True)
        return [rows[i] for i in order][:max(int(limit or 200), 1)]

    def pay(self, conn_id: str, account: str, to_name: str, to_iban: str,
            amount: float, currency: str = '', reference: str = '') -> dict:
        if float(amount) <= 0:
            raise BankError('amount must be positive')
        return self.adapter(conn_id).pay(account, to_name, norm_iban(to_iban),
                                         round(float(amount), 2),
                                         (currency or '').upper(), reference)

    # links: which bank payer is which renter ----------------------

    def links(self) -> list:
        return list(self._read(self._links_path, {}).values())

    def link(self, address: str, payer_iban: str = '', payer_name: str = '',
             kind: str = 'rent') -> dict:
        if not address:
            raise BankError('renter address required')
        if kind not in ('rent', 'option'):
            raise BankError("kind must be 'rent' or 'option'")
        links = self._read(self._links_path, {})
        entry = {'address': address, 'reference': reference_code(address),
                 'payer_iban': norm_iban(payer_iban), 'payer_name': payer_name.strip(),
                 'kind': kind, 'linked': int(time.time())}
        links[address.lower()] = entry
        self._write(self._links_path, links)
        return entry

    def unlink(self, address: str) -> dict:
        links = self._read(self._links_path, {})
        if not links.pop((address or '').lower(), None):
            raise BankError(f'no link for {address}')
        self._write(self._links_path, links)
        return {'removed': address}

    def match(self, t: dict, links: list) -> Optional[dict]:
        """Whose money is this? Reference code first (the renter typed it),
        then the payer's IBAN, then the exact payer name. Credits only."""
        if t['amount'] <= 0 or t['status'] != 'booked':
            return None
        ref = _squash(t['reference'])
        for l in links:
            if _squash(l['reference']) in ref:
                return {**l, 'by': 'reference'}
        for l in links:
            if l['payer_iban'] and l['payer_iban'] == t['counterparty_iban']:
                return {**l, 'by': 'iban'}
        for l in links:
            if l['payer_name'] and l['payer_name'].casefold() == t['counterparty'].casefold():
                return {**l, 'by': 'name'}
        return None

    def matched(self) -> dict:
        return self._read(self._matched_path, {})

    def reconcile(self, record: Callable[[str, float, str, dict], dict],
                  to_units: Callable[[float, str], dict], conn_id: str = '',
                  account: str = '', since: int = 0, dry_run: bool = False) -> dict:
        """Walk the bank's credits and book each linked one exactly once.

        record(renter, units, kind, source) -> the host's result dict (an
            'error' key means "not now" — the transfer stays unbooked and is
            retried on the next run).
        to_units(amount, currency) -> {'units': float, 'rate': float, ...}
            converts bank money into ledger units (openhouse: fiat → Ξ).
        """
        with _reconcile_lock(self.data_dir):
            return self._reconcile(record, to_units, conn_id, account, since, dry_run)

    def _reconcile(self, record, to_units, conn_id, account, since, dry_run) -> dict:
        a = self.adapter(conn_id)
        cid = a.conn['id']
        links = self.links()
        done = self.matched()
        booked, unmatched, held, skipped = [], [], [], 0
        rows = sorted(a.transactions(account=account, since=int(since or 0)),
                      key=lambda t: t['date'])
        for t in rows:
            key = f'{cid}:{t["id"]}'
            if key in done:
                skipped += 1
                continue
            hit = self.match(t, links)
            if not hit:
                if t['amount'] > 0 and t['status'] == 'booked':
                    unmatched.append(t)
                continue
            conv = to_units(t['amount'], t['currency'])
            if 'error' in conv:
                held.append({**t, 'renter': hit['address'], 'reason': conv['error']})
                continue
            source = {'bank': cid, 'txn': t['id'], 'fiat': t['amount'],
                      'currency': t['currency'], 'rate': conv['rate'],
                      'matched_by': hit['by'], 'booked_at_bank': t['date']}
            row = {**t, 'renter': hit['address'], 'kind': hit['kind'],
                   'units': conv['units'], 'rate': conv['rate'], 'matched_by': hit['by']}
            if dry_run:
                booked.append(row)
                continue
            res = record(hit['address'], conv['units'], hit['kind'], source)
            if isinstance(res, dict) and 'error' in res:
                held.append({**row, 'reason': res['error']})
                continue
            done[key] = {'renter': hit['address'], 'units': conv['units'],
                         'fiat': t['amount'], 'currency': t['currency'],
                         'rate': conv['rate'], 'at': int(time.time())}
            self._write(self._matched_path, done)
            booked.append(row)
        return {'connection': cid, 'dry_run': bool(dry_run),
                'booked': booked, 'held': held, 'unmatched': unmatched,
                'already_booked': skipped,
                'summary': f'{len(booked)} booked, {len(held)} held, '
                           f'{len(unmatched)} unmatched credit(s), {skipped} already on the ledger'}


# ── the one HTTP seam ───────────────────────────────────────────
# Every network adapter goes through here, so tests monkeypatch exactly one
# function and a deployment can audit exactly one place that leaves the box.

HTTP_TIMEOUT = 20

# In-process banks: inproc://<name>/… is answered by INPROC[name](method, url,
# headers, body) without a socket. The testnet examples mount the reference
# bank (mockbank.py) here, and a node can host a bank for itself the same way.
INPROC: Dict[str, Callable] = {}


def http_json(method: str, url: str, headers: Optional[dict] = None, body=None) -> dict:
    if url.startswith('inproc://'):
        name = url[len('inproc://'):].split('/', 1)[0]
        if name not in INPROC:
            raise BankError(f'no in-process bank {name!r} mounted')
        return INPROC[name](method, url, headers or {}, body)
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Accept': 'application/json',
        **({'Content-Type': 'application/json'} if data is not None else {}),
        **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode('utf-8', 'replace')
        raise BankError(f'bank said {e.code}: {detail}')
    except (urllib.error.URLError, OSError) as e:
        raise BankError(f'bank unreachable: {e}')
    try:
        return json.loads(raw or b'{}')
    except ValueError:
        raise BankError('bank returned non-JSON')
