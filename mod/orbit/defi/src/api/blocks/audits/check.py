#!/usr/bin/env python3
"""Lint every audit against SCHEMA.md: keys, severities, counts, ids, and that
each audited block exists in the catalog. `python3 audits/check.py` from blocks/."""
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
cat = json.load(open(os.path.join(here, '..', 'catalog.json')))
ids = {b['id']: b for b in cat['blocks']}
SEV = ['critical', 'high', 'medium', 'low', 'info']
TOP = ['block', 'contract', 'file', 'audited_at', 'auditor', 'risk', 'summary', 'counts', 'findings', 'safe_use']
FND = ['id', 'severity', 'title', 'where', 'detail', 'exploit', 'recommendation']
bad = 0
rows = []
for name in sorted(os.listdir(here)):
    if not name.endswith('.json'):
        continue
    bid = name[:-5]
    try:
        a = json.load(open(os.path.join(here, name)))
    except Exception as e:
        print(f'{bid}: invalid JSON: {e}'); bad += 1; continue
    errs = []
    for k in TOP:
        if k not in a: errs.append(f'missing {k}')
    if a.get('block') != bid: errs.append(f'block={a.get("block")!r} != file {bid}')
    if bid != 'common':
        if bid not in ids: errs.append('not in catalog')
        else:
            if a.get('contract') != ids[bid]['contract']: errs.append(f'contract mismatch {a.get("contract")}')
            if a.get('file') != ids[bid]['file']: errs.append(f'file mismatch {a.get("file")}')
    if a.get('risk') not in SEV[:4]: errs.append(f'risk={a.get("risk")!r}')
    counts = {s: 0 for s in SEV}
    seen = set()
    for f in a.get('findings', []):
        for k in FND:
            if not f.get(k): errs.append(f'{f.get("id")}: missing {k}')
        if f.get('severity') not in SEV: errs.append(f'{f.get("id")}: severity={f.get("severity")!r}')
        else: counts[f['severity']] += 1
        if f.get('id') in seen: errs.append(f'duplicate id {f.get("id")}')
        seen.add(f.get('id'))
    for s in SEV:
        if int(a.get('counts', {}).get(s, 0)) != counts[s]:
            errs.append(f'counts.{s}={a.get("counts", {}).get(s)} but {counts[s]} findings')
    if errs:
        bad += 1
        print(f'{bid}: ' + '; '.join(errs))
    rows.append((bid, a.get('risk'), counts))
missing = sorted(set(ids) - {r[0] for r in rows})
order = {s: i for i, s in enumerate(SEV)}
for bid, risk, c in sorted(rows, key=lambda r: order.get(r[1], 9)):
    print(f'{bid:14s} {risk:9s} ' + ' '.join(f'{s[0]}={c[s]}' for s in SEV))
if missing:
    print('not yet audited:', ' '.join(missing))
print(f'{len(rows)} audits, {bad} with problems')
sys.exit(1 if bad else 0)
