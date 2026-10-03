"""The whole thing, start to finish, with keys made on the spot.

`m id/demo` runs this. It is not a mock: the wallets are real keys signing the
real statements, and every call goes through the same functions a browser wallet
drives. The two steps that *fail* are the point of it — an uninvited wallet is
refused, and a replayed nonce is refused — because a module that only shows its
happy path is showing you nothing.

Unless `keep=true`, it all happens in a temporary directory that is deleted
afterwards, so watching the demo never touches a real identity.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from . import identity, signers, store


def _step(steps: List[Dict[str, Any]], what: str, detail: Any,
          expect: str = 'ok') -> Any:
    steps.append({'n': len(steps) + 1, 'step': what, 'expect': expect,
                  'result': detail})
    return detail


def run(keep: bool = False) -> Dict[str, Any]:
    if keep:
        return _run()
    with store.sandbox():
        return _run()


def _run() -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = []
    started = time.time()

    metamask = signers.ethereum()
    phantom = signers.solana()
    hardware = signers.bitcoin('p2wpkh')
    keplr = signers.cosmos()
    stranger = signers.ethereum()
    elsewhere = signers.ethereum()

    _step(steps, 'four wallets, made here and thrown away afterwards',
          {w.chain: w.address for w in (metamask, phantom, hardware, keplr)})

    # 1 — the first wallet makes the identity
    ask = identity.challenge('ethereum', metamask.address)
    _step(steps, 'the text the Ethereum wallet is asked to sign',
          {'statement': ask['statement'], 'scheme': ask['scheme']})
    first = identity.submit(ask['nonce'], **metamask.proof(ask['statement']))
    id, session = first['id'], first['session']
    _step(steps, 'it signs, and an identity exists',
          {'id': id, 'op': first['op'], 'note': first['note']})

    # 2 — the rest join, each with the first wallet's consent
    for wallet, label in ((phantom, 'Solana'), (hardware, 'Bitcoin'), (keplr, 'Cosmos')):
        ask = identity.challenge(wallet.chain, wallet.address, id=id)
        joined = identity.submit(ask['nonce'], session=session,
                                 **wallet.proof(ask['statement']))
        _step(steps, f'the {label} wallet joins',
              {'account': joined['account'], 'authorized_by': joined['authorized_by'],
               'accounts_now': joined['identity']['count']})

    # 3 — the two refusals that matter
    ask = identity.challenge('ethereum', stranger.address, id=id)
    try:
        identity.submit(ask['nonce'], **stranger.proof(ask['statement']))
        _step(steps, 'a stranger attaches their wallet to this identity',
              'ACCEPTED — this is a bug', expect='refused')
    except identity.IdError as exc:
        _step(steps, 'a stranger, with a perfectly valid signature, tries to join',
              {'refused': str(exc)}, expect='refused')

    ask = identity.challenge(phantom.chain, phantom.address, id=id)
    signature = phantom.proof(ask['statement'])
    identity.submit(ask['nonce'], session=session, **signature)
    try:
        identity.submit(ask['nonce'], session=session, **signature)
        _step(steps, 'the same signature is submitted twice', 'ACCEPTED — this is a bug',
              expect='refused')
    except identity.IdError as exc:
        _step(steps, 'the same nonce and signature are replayed',
              {'refused': str(exc)}, expect='refused')

    # 4 — a name
    ask = identity.challenge('ethereum', metamask.address, op='name', name='demo')
    named = identity.submit(ask['nonce'], **metamask.proof(ask['statement']))
    _step(steps, 'the root wallet sets a display name', {'name': named['name']})

    # 5 — a second identity, and a merge
    ask = identity.challenge('ethereum', elsewhere.address)
    second = identity.submit(ask['nonce'], **elsewhere.proof(ask['statement']))
    _step(steps, 'a second identity exists somewhere else', {'id': second['id']})

    ask = identity.challenge('ethereum', metamask.address, op='merge',
                             id=id, other=second['id'])
    half = identity.submit(ask['nonce'], **metamask.proof(ask['statement']))
    _step(steps, 'one side signs the merge',
          {'stage': half['stage'], 'waiting_on': half['waiting_on']})

    ask = identity.challenge('ethereum', elsewhere.address, op='merge',
                             id=id, other=second['id'])
    done = identity.submit(ask['nonce'], **elsewhere.proof(ask['statement']))
    _step(steps, 'the other side signs, and they become one',
          {'survivor': done['id'], 'absorbed': done['absorbed'],
           'moved': done['moved'],
           'old_name_still_resolves': store.follow(second['id']) == done['id']})

    # 6 — leaving
    ask = identity.challenge(keplr.chain, keplr.address, op='unlink')
    left = identity.submit(ask['nonce'], **keplr.proof(ask['statement']))
    _step(steps, 'the Cosmos wallet removes itself — nobody else has to agree',
          {'removed': left['removed'], 'accounts_now': left['identity']['count']})

    # 7 — the audit, and what it catches
    checked = identity.audit(id)
    _step(steps, 'every signature in the log is re-checked, offline',
          {'ok': checked['ok'], 'events': checked['events'],
           'accounts': checked['accounts']})

    path = store.log_path(id)
    lines = path.read_text().splitlines()
    forged = json.loads(lines[1])
    forged['account'] = stranger.account
    lines[1] = json.dumps(forged)
    path.write_text('\n'.join(lines) + '\n')
    after = identity.audit(id)
    _step(steps, 'someone edits the log by hand to insert an account',
          {'ok': after['ok'],
           'caught': [row['problems'] for row in after['checked'] if row['problems']]},
          expect='ok=False')

    return {
        'ok': all(step['result'] != 'ACCEPTED — this is a bug' for step in steps),
        'id': id,
        'took_ms': round((time.time() - started) * 1000),
        'wallets': {w.chain: w.address for w in (metamask, phantom, hardware, keplr)},
        'steps': steps,
        'note': ('every signature above was made and verified by this module, with no '
                 'wallet software and no network. The keys are gone.'),
    }
