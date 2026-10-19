"""
bank/statements.py — the adapter that works with every bank on earth.

Not every bank has an API, and the ones that do gate it behind licences and
aggregators. Every bank DOES let an account holder download a statement, in
one of four formats, and accept an uploaded payment file. So this adapter
needs no credentials and no network:

  IN   parse(content) reads, auto-detected:
         camt.053   ISO 20022 BankToCustomerStatement XML (EU/UK/CH/AU/…)
         MT940      SWIFT customer statement text (the legacy everywhere format)
         OFX / QFX  Open Financial Exchange, SGML or XML (US/CA)
         CSV        any header row — columns found by name, not position
  OUT  pay() writes an ISO 20022 pain.001.001.03 credit-transfer file to the
       outbox; the account holder uploads it to their bank's portal, which is
       how businesses have paid in bulk for twenty years.

Imports are idempotent: a transaction re-imported from an overlapping
statement keeps its id and is booked once.
"""
import csv
import io
import json
import re
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .core import Adapter, BankError, _f, account, norm_iban, txn, txn_from_any


# ── parsers: text in, (accounts, txns) out ──────────────────────

def detect(content: str) -> str:
    head = content.lstrip()[:4000]
    if 'camt.053' in head or '<BkToCstmrStmt' in head:
        return 'camt053'
    if 'OFXHEADER' in head or '<OFX>' in head.upper():
        return 'ofx'
    if re.search(r'^:20:', head, re.M) and re.search(r'^:61:', content, re.M):
        return 'mt940'
    return 'csv'


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    return root


def _text(el, path, default=''):
    if el is None:
        return default
    found = el.find(path)
    return (found.text or '').strip() if found is not None and found.text else default


def parse_camt053(content: str):
    root = _strip_ns(ET.fromstring(content.encode()))
    accounts, txns = [], []
    for stmt in root.iter('Stmt'):
        iban = _text(stmt, 'Acct/Id/IBAN') or _text(stmt, 'Acct/Id/Othr/Id')
        ccy = _text(stmt, 'Acct/Ccy')
        bal = None
        for b in stmt.findall('Bal'):
            if _text(b, 'Tp/CdOrPrtry/Cd') in ('CLBD', 'CLAV'):
                amt = b.find('Amt')
                bal = _f(amt.text if amt is not None else 0)
                if _text(b, 'CdtDbtInd') == 'DBIT':
                    bal = -bal
                ccy = ccy or (amt.get('Ccy') if amt is not None else '')
                break
        acct_id = norm_iban(iban) or 'camt'
        accounts.append(account(id=acct_id, name=_text(stmt, 'Acct/Nm') or acct_id,
                                currency=ccy, iban=iban, balance=bal))
        for i, e in enumerate(stmt.findall('Ntry')):
            amt_el = e.find('Amt')
            amt = _f(amt_el.text if amt_el is not None else 0)
            credit = _text(e, 'CdtDbtInd') == 'CRDT'
            tx = e.find('NtryDtls/TxDtls')
            party = 'Dbtr' if credit else 'Cdtr'
            name = (_text(tx, f'RltdPties/{party}/Nm') or _text(tx, f'RltdPties/{party}/Pty/Nm')) if tx is not None else ''
            cp_iban = _text(tx, f'RltdPties/{party}Acct/Id/IBAN') if tx is not None else ''
            ref = ' '.join(filter(None, [
                _text(tx, 'RmtInf/Ustrd') if tx is not None else '',
                _text(tx, 'RmtInf/Strd/CdtrRefInf/Ref') if tx is not None else '',
                _text(e, 'AddtlNtryInf')]))
            status = _text(e, 'Sts/Cd') or _text(e, 'Sts') or 'BOOK'
            txns.append(txn(
                id=_text(e, 'AcctSvcrRef') or _text(e, 'NtryRef') or
                   (_text(tx, 'Refs/EndToEndId') if tx is not None else ''),
                account=acct_id,
                date=_text(e, 'BookgDt/Dt') or _text(e, 'BookgDt/DtTm') or _text(e, 'ValDt/Dt'),
                amount=amt if credit else -amt,
                currency=(amt_el.get('Ccy') if amt_el is not None else '') or ccy,
                counterparty=name, counterparty_iban=cp_iban, reference=ref,
                status='booked' if status.upper().startswith('BOOK') else 'pending',
                seq=i))
    return accounts, txns


_MT61 = re.compile(r'^(\d{6})(\d{4})?(R?[CD])([A-Z])?([\d,]+)(\w{4})([^/\n]*)(?://(\S+))?')


def parse_mt940(content: str):
    accounts, txns = [], []
    acct_id, ccy, bal = 'mt940', '', None
    # join continuation lines onto their tag
    fields, cur = [], None
    for line in content.replace('\r', '').split('\n'):
        m = re.match(r'^:(\d{2}[A-Z]?):(.*)$', line)
        if m:
            cur = [m.group(1), m.group(2)]
            fields.append(cur)
        elif cur is not None and line.strip() not in ('', '-', '-}'):
            cur[1] += '\n' + line
    pending = None
    for i, (tag, val) in enumerate(fields):
        if tag == '25':
            acct_id = norm_iban(val.split('/')[-1]) or val.strip()
        elif tag in ('60F', '60M'):
            ccy = val[7:10]
        elif tag in ('62F', '62M'):
            sign = -1 if val[0] == 'D' else 1
            bal = sign * _f(val[10:])
            ccy = val[7:10] or ccy
        elif tag == '61':
            if pending:
                txns.append(txn(**pending))
            m = _MT61.match(val.split('\n')[0])
            if not m:
                pending = None
                continue
            ymd, _, dc, _, amt, _, ref, bank_ref = m.groups()
            amount = _f(amt.replace(',', '.'))
            pending = dict(id=bank_ref or '', account=acct_id, date=ymd,
                           amount=-amount if dc in ('D', 'RC') else amount,
                           currency=ccy, reference=ref.strip() if ref.strip() != 'NONREF' else '',
                           seq=i)
        elif tag == '86' and pending:
            info = val.replace('\n', '')
            # structured ?20-?29 remittance, ?32/?33 name, ?31 account (German MT940)
            sub = dict(re.findall(r'\?(\d{2})([^?]*)', info))
            if sub:
                pending['reference'] = ' '.join(filter(None, [pending['reference']] +
                                        [sub[k] for k in sorted(sub) if '20' <= k <= '29']))
                pending['counterparty'] = (sub.get('32', '') + sub.get('33', '')).strip()
                pending['counterparty_iban'] = sub.get('31', '')
            else:
                name = re.search(r'/NAME/([^/]+)', info)
                iban = re.search(r'/IBAN/([^/]+)', info)
                remi = re.search(r'/REMI/([^/]+(?:/[^/A-Z][^/]*)*)', info)
                if name or iban or remi:
                    pending['counterparty'] = name.group(1) if name else ''
                    pending['counterparty_iban'] = iban.group(1) if iban else ''
                    pending['reference'] = ' '.join(filter(None, [pending['reference'],
                                                                  remi.group(1) if remi else '']))
                else:
                    pending['reference'] = ' '.join(filter(None, [pending['reference'], info]))
    if pending:
        txns.append(txn(**pending))
    for t in txns:
        t['currency'] = t['currency'] or ccy
    accounts.append(account(id=acct_id, name=acct_id, currency=ccy, iban=acct_id, balance=bal))
    return accounts, txns


def _ofx(block: str, tag: str) -> str:
    m = re.search(rf'<{tag}>([^<\r\n]*)', block, re.I)
    return m.group(1).strip() if m else ''


def parse_ofx(content: str):
    acct_id = _ofx(content, 'ACCTID') or 'ofx'
    ccy = _ofx(content, 'CURDEF')
    lb = re.search(r'<LEDGERBAL>(.*?)(</LEDGERBAL>|<AVAILBAL>|</STMTRS>)', content, re.S | re.I)
    bal = _f(_ofx(lb.group(1), 'BALAMT')) if lb else None
    txns = []
    for i, block in enumerate(re.findall(r'<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>)',
                                         content, re.S | re.I)):
        txns.append(txn(id=_ofx(block, 'FITID'), account=acct_id,
                        date=_ofx(block, 'DTPOSTED'), amount=_ofx(block, 'TRNAMT'),
                        currency=ccy, counterparty=_ofx(block, 'NAME') or _ofx(block, 'PAYEE'),
                        reference=_ofx(block, 'MEMO'), seq=i))
    return [account(id=acct_id, name=acct_id, currency=ccy, balance=bal)], txns


def parse_csv(content: str, account_id: str = 'csv', currency: str = ''):
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(content.lstrip('﻿')), dialect=dialect))
    if rows and not any(k and re.search(r'amount|credit|debit|paid|value|sum|betrag|montant|importe',
                                        k, re.I) for k in rows[0]):
        raise BankError('CSV has no amount column — need one of amount / credit+debit')
    txns = [txn_from_any({k.strip(): (v or '').strip() for k, v in r.items() if k},
                         account_id=account_id, currency=currency, seq=i)
            for i, r in enumerate(rows)]
    txns = [t for t in txns if t['date'] or t['amount']]
    return [account(id=account_id, name=account_id, currency=currency)], txns


PARSERS = {'camt053': parse_camt053, 'mt940': parse_mt940, 'ofx': parse_ofx, 'csv': parse_csv}


def parse(content: str, fmt: str = 'auto'):
    fmt = detect(content) if fmt in ('', 'auto', None) else fmt
    if fmt not in PARSERS:
        raise BankError(f'unknown statement format {fmt!r} — have {", ".join(PARSERS)}')
    try:
        accts, txns = PARSERS[fmt](content)
    except BankError:
        raise
    except Exception as e:
        raise BankError(f'could not read {fmt} statement: {type(e).__name__}: {e}')
    return fmt, accts, txns


# ── pain.001: the payment file every bank portal accepts ────────

def pain001(msg_id: str, debtor_name: str, debtor_iban: str, payments: list,
            created: int = 0) -> str:
    """ISO 20022 CustomerCreditTransferInitiationV03, one payment per
    CdtTrfTxInf. `payments`: [{to_name, to_iban, amount, currency, reference, id}]."""
    ts = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(created or time.time()))
    day = ts[:10]
    total = sum(p['amount'] for p in payments)
    tx = ''.join(f"""
      <CdtTrfTxInf>
        <PmtId><EndToEndId>{escape(p['id'])}</EndToEndId></PmtId>
        <Amt><InstdAmt Ccy="{escape(p['currency'])}">{p['amount']:.2f}</InstdAmt></Amt>
        <Cdtr><Nm>{escape(p['to_name'][:70])}</Nm></Cdtr>
        <CdtrAcct><Id><IBAN>{escape(p['to_iban'])}</IBAN></Id></CdtrAcct>
        <RmtInf><Ustrd>{escape(p['reference'][:140])}</Ustrd></RmtInf>
      </CdtTrfTxInf>""" for p in payments)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03">
  <CstmrCdtTrfInitn>
    <GrpHdr>
      <MsgId>{escape(msg_id)}</MsgId>
      <CreDtTm>{ts}</CreDtTm>
      <NbOfTxs>{len(payments)}</NbOfTxs>
      <CtrlSum>{total:.2f}</CtrlSum>
      <InitgPty><Nm>{escape(debtor_name[:70])}</Nm></InitgPty>
    </GrpHdr>
    <PmtInf>
      <PmtInfId>{escape(msg_id)}</PmtInfId>
      <PmtMtd>TRF</PmtMtd>
      <NbOfTxs>{len(payments)}</NbOfTxs>
      <CtrlSum>{total:.2f}</CtrlSum>
      <ReqdExctnDt>{day}</ReqdExctnDt>
      <Dbtr><Nm>{escape(debtor_name[:70])}</Nm></Dbtr>
      <DbtrAcct><Id><IBAN>{escape(debtor_iban)}</IBAN></Id></DbtrAcct>
      <DbtrAgt><FinInstnId/></DbtrAgt>{tx}
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>
"""


class Statements(Adapter):
    """Any bank, no API: import the statement file you download from online
    banking (camt.053, MT940, OFX/QFX or CSV — auto-detected), and pay by
    ISO 20022 pain.001 file you upload back. No credentials stored."""

    kind = 'statement'
    label = 'Statement files (any bank)'
    live = False
    caps = ('accounts', 'transactions', 'pay', 'import')
    fields = [
        {'name': 'holder', 'required': False, 'secret': False,
         'help': 'account holder name printed on pain.001 files'},
        {'name': 'iban', 'required': False, 'secret': False,
         'help': 'your account IBAN — the debtor on pain.001 files'},
        {'name': 'currency', 'required': False, 'secret': False,
         'help': 'currency for CSV imports that do not say (default USD)'},
    ]

    @property
    def _path(self):
        return self.data_dir / 'statements' / f'{self.conn["id"]}.json'

    def _books(self):
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, ValueError):
            return {'accounts': {}, 'txns': {}, 'imports': [], 'payments': []}

    def _save(self, books):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(books, indent=2))

    def import_statement(self, content: str, fmt: str = 'auto') -> dict:
        if not (content or '').strip():
            raise BankError('statement is empty')
        fmt = detect(content) if fmt in ('', 'auto', None) else fmt
        if fmt == 'csv':
            accts, rows = parse_csv(content, currency=(self.cfg.get('currency') or 'USD').upper())
        else:
            fmt, accts, rows = parse(content, fmt)
        books = self._books()
        for a in accts:
            prev = books['accounts'].get(a['id'], {})
            books['accounts'][a['id']] = {**prev, **{k: v for k, v in a.items() if v not in (None, '')}}
        new = 0
        for t in rows:
            if t['id'] not in books['txns']:
                new += 1
            books['txns'][t['id']] = t
        books['imports'].append({'format': fmt, 'rows': len(rows), 'new': new,
                                 'at': int(time.time())})
        self._save(books)
        return {'format': fmt, 'accounts': [a['id'] for a in accts],
                'transactions': len(rows), 'new': new, 'duplicates': len(rows) - new}

    def accounts(self):
        return list(self._books()['accounts'].values())

    def transactions(self, account='', since=0):
        return [t for t in self._books()['txns'].values()
                if (not account or t['account'] == account) and t['date'] >= since]

    def pay(self, account, to_name, to_iban, amount, currency, reference):
        if not to_iban:
            raise BankError('to_iban required for a pain.001 transfer')
        books = self._books()
        debtor_iban = norm_iban(self.cfg.get('iban') or account)
        if not debtor_iban:
            raise BankError('set the connection iban (or pass account=<your IBAN>) '
                            'so the file names the paying account')
        n = len(books['payments'])
        pid = f'OH{int(time.time())}{n:03d}'
        p = {'id': pid, 'account': debtor_iban, 'to_name': to_name or to_iban,
             'to_iban': to_iban, 'amount': amount,
             'currency': currency or (self.cfg.get('currency') or 'EUR').upper(),
             'reference': reference, 'created': int(time.time())}
        xml = pain001(pid, self.cfg.get('holder') or 'OpenHouse', debtor_iban, [p], p['created'])
        out = self.data_dir / 'outbox' / f'{pid}.xml'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(xml)
        p.update(status='file_ready', file=str(out),
                 next_step='upload this pain.001 file in your bank\'s online banking '
                           '(bulk / file payments) and approve it there')
        books['payments'].append(p)
        self._save(books)
        return {**p, 'xml': xml}
