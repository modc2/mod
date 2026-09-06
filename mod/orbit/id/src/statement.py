"""The exact words a wallet is asked to sign, and why each line is in them.

A signature only means something if you know what was signed, so the statement
is plain text, fixed width, and reproducible byte for byte from the fields
stored alongside it. Every line does a job:

  line 1   `mod:id/v1 <op>` — a domain separator. A signature harvested from
           another site cannot be replayed here, and a signature made here
           cannot be replayed into a login prompt somewhere else, because the
           first thing hashed says which protocol and which operation this is.
  purpose  what the user is agreeing to, in one sentence, plus the sentence
           that matters most: this moves no funds.
  id       which identity the account is joining. Without it, a proof for one
           identity could be replayed to join a different one.
  account  the chain and address being proved. Without it, a signature from a
           key could be submitted as proof for someone else's address.
  issued   when it was made.
  expires  when it stops counting. A proof lying around forever is a proof
           somebody can steal later.
  nonce    32 random hex. Consumed once, never accepted twice.
  host     the machine that issued the challenge, so a user can see whether the
           prompt in front of them came from where they think it did.

Rendering is deliberately dumb — fixed labels, single spaces, `\\n` endings, no
alignment computed from the values — because anything clever is something a
verifier somewhere else has to reproduce exactly.
"""
from __future__ import annotations

import os
import secrets
import socket
import time
from typing import Any, Dict, List, Optional

PROTOCOL = 'mod:id/v1'
DEFAULT_TTL = 900  # 15 minutes

OPS: Dict[str, str] = {
    'link': 'I control this account, and I am linking it to the identity below.',
    'merge': 'I am joining these two identities into one. Every account of both '
             'becomes an account of the survivor.',
    'unlink': 'I am removing this account from the identity below.',
    'name': 'I am setting the display name of the identity below.',
    'claim': 'I am proving that I control this account.',
}

NO_FUNDS = ('Signing this proves control of a key. It moves no funds, approves no '
            'transaction, and grants no spending permission.')


def now() -> float:
    return time.time()


def stamp(when: Optional[float] = None) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(when if when is not None else now()))


def nonce() -> str:
    return secrets.token_hex(16)


def host() -> str:
    return os.environ.get('ID_HOST') or socket.gethostname() or '-'


def fields(op: str, identity: str, account: str, id: str = 'new',
           ttl: int = DEFAULT_TTL, extra: Optional[Dict[str, str]] = None,
           issued: Optional[float] = None) -> Dict[str, Any]:
    if op not in OPS:
        raise ValueError(f'unknown operation {op!r} — one of {", ".join(OPS)}')
    made = issued if issued is not None else now()
    return {
        'protocol': PROTOCOL,
        'op': op,
        'id': id or 'new',
        'account': account,
        'identity': identity,
        'issued': stamp(made),
        'issued_at': made,
        'expires': stamp(made + ttl),
        'expires_at': made + ttl,
        'nonce': nonce(),
        'host': host(),
        'extra': dict(extra or {}),
    }


def render(f: Dict[str, Any]) -> str:
    """The signed bytes. Reproduced exactly by anyone re-checking the proof."""
    lines: List[str] = [
        f'{f.get("protocol", PROTOCOL)} {f["op"]}',
        '',
        OPS[f['op']],
        NO_FUNDS,
        '',
        f'id:      {f["id"]}',
        f'account: {f["account"]}',
    ]
    for key in sorted(f.get('extra') or {}):
        lines.append(f'{(key + ":").ljust(8)} {f["extra"][key]}')
    lines += [
        f'issued:  {f["issued"]}',
        f'expires: {f["expires"]}',
        f'nonce:   {f["nonce"]}',
        f'host:    {f["host"]}',
    ]
    return '\n'.join(lines)


def token(f: Dict[str, Any]) -> str:
    """The short form, for places a paragraph will not fit — DNS TXT, a post.

    Carries the same three things that make the long statement unforgeable: the
    protocol, the identity being joined, and the nonce that is consumed once.
    """
    return f'{PROTOCOL} {f["op"]} {f["id"]} nonce={f["nonce"]}'


def check_fresh(f: Dict[str, Any], at: Optional[float] = None) -> None:
    when = at if at is not None else now()
    if when > float(f['expires_at']):
        raise ValueError(
            f'this challenge expired at {f["expires"]} — ask for a new one. '
            '(Proofs expire so that a signature copied off a screen months ago '
            'cannot be used today.)')
    if when + 300 < float(f['issued_at']):
        raise ValueError('challenge is issued in the future — check the clock')


def matches(f: Dict[str, Any], text: str) -> bool:
    """Did the signer sign what we think we asked for?"""
    return render(f) == text
