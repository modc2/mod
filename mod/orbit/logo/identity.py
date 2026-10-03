"""
Who is calling, and whose mark they are allowed to touch.

One door in: the **mod-protocol token** — the signed `{data, time, key,
signature}` envelope that `m.mod('auth').token(...)` mints, that a browser
wallet can produce with a single `personal_sign`, and that every other module
in this fleet already speaks. It is verified by the auth mod itself rather than
reimplemented here, so a wallet, a CLI key and a peer module all arrive the
same way and end at one address.

And one rule out: **a mark belongs to the module it is drawn on.** The address
that signed must be the owner of the *target* module, read from that module's
own `config.json` — not from a list this module keeps, and not from a
credential this module holds. That is the whole security property, and it is
what makes it safe for another console (orbit/build) to render the editor:
build forwards the owner's signed token and cannot mint one, so proxying the
UI never grants it the power to change a mark.

Owner resolution, in order:

    1. `{module}/config.json` → `owner`      the module's own manifest
    2. `~/.mod/{module}/owners.json`         co-owners, off-chain by design —
                                             a bare array or {"addresses": []},
                                             the same file build's Rust side reads
    3. this deployment's owner               ONLY for a module that declares no
                                             owner at all. First signed caller
                                             claims (~/.mod/logo/owner.json).

Name resolution mirrors the protocol's: module names are path-derived, and
`core/` is applied after `orbit/`, so a bare name that exists in both resolves
to the CORE one — exactly what `m.mod(name)` gives you. Pass `orbit/store` or
`core/store` explicitly to address the other side of a collision.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from protocol import auth

TOKEN_MAX_AGE = int(os.environ.get('LOGO_TOKEN_MAX_AGE', 7 * 86_400))
STATE = Path(os.path.expanduser(os.environ.get('LOGO_DIR', '~/.mod/logo')))
OWNER_PATH = STATE / 'owner.json'

# The module tree. `mod/orbit/<name>` and `mod/core/<name>` — this file lives at
# mod/orbit/logo/identity.py, so the tree root is two levels up.
TREE = Path(os.environ.get('LOGO_TREE') or Path(__file__).resolve().parent.parent.parent)
GROUPS = ('core', 'orbit')          # search order = the protocol's: core wins

# Where a module keeps its own runtime state, and therefore its co-owner list.
MOD_STATE = Path(os.path.expanduser(os.environ.get('MOD_STATE_DIR', '~/.mod')))

NAME = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$', re.I)


class AuthError(Exception):
    """The caller is not who they say they are, or didn't say."""


class UnknownModule(Exception):
    """No module by that name in the tree."""


# ── the caller ───────────────────────────────────────────────────────

def open_mode() -> bool:
    """Local development only. `status()` always says which mode it is in, and
    the console prints it in red, because an open-mode deployment lets any
    caller repaint every module in the fleet."""
    return os.environ.get('LOGO_OPEN', '') not in ('', '0', 'false')


def strip(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    return token or None


def from_token(token: str) -> str:
    try:
        headers = auth(max_age=TOKEN_MAX_AGE).verify(token)
    except Exception as e:
        raise AuthError(f'token rejected: {e}')
    address = str(headers.get('key') or '')
    if not address:
        raise AuthError('token carries no signer address')
    return address.lower()


def whoami(token: Optional[str]) -> Optional[str]:
    token = strip(token)
    if not token:
        return None
    try:
        return from_token(token)
    except AuthError:
        return None


def require(token: Optional[str]) -> str:
    token = strip(token)
    if not token:
        if open_mode():
            return 'open-mode'
        raise AuthError('sign in first — send a mod-protocol token as '
                        '`Authorization: Bearer <token>`')
    return from_token(token)


# ── the module ───────────────────────────────────────────────────────

def resolve(module: str) -> Tuple[str, str, Path]:
    """`build` → ('orbit', 'build', /root/mod/mod/orbit/build).

    Accepts a qualified `orbit/x` / `core/x` to name one side of a collision.
    Raises UnknownModule rather than inventing a directory: a mark for a module
    that does not exist is a typo, and silently storing it means the owner
    never finds out why their logo did not appear.
    """
    key = (module or '').strip().strip('/')
    group: Optional[str] = None
    if '/' in key:
        group, _, key = key.partition('/')
        group = group.lower()
        if group not in GROUPS:
            raise UnknownModule(f'no such group {group!r} — expected one of {GROUPS}')
    if not NAME.match(key):
        raise UnknownModule(f'{module!r} is not a module name')
    key = key.lower()
    for candidate in ([group] if group else GROUPS):
        path = TREE / candidate / key
        if (path / 'config.json').is_file() or (path / 'src' / 'config.json').is_file():
            return candidate, key, path
    raise UnknownModule(f'no module named {module!r} under {TREE}')


def manifest(path: Path) -> Dict[str, Any]:
    """A module's config.json — at its root, or under src/ for the forks that
    keep it there. Never raises: an unreadable manifest means "no owner
    declared", which fails closed everywhere it matters."""
    for candidate in (path / 'config.json', path / 'src' / 'config.json'):
        try:
            parsed = json.loads(candidate.read_text())
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


def manifest_path(path: Path) -> Optional[Path]:
    for candidate in (path / 'config.json', path / 'src' / 'config.json'):
        if candidate.is_file():
            return candidate
    return None


def co_owners(name: str) -> List[str]:
    """`~/.mod/{module}/owners.json` — a bare array or {"addresses": [...]}.
    Same file, same shape the build module's Rust side reads; a co-owner added
    there can set that module's mark too. Off-chain on purpose: who may write
    is deployment state, not something to commit."""
    try:
        parsed = json.loads((MOD_STATE / name / 'owners.json').read_text())
    except Exception:
        return []
    arr = parsed if isinstance(parsed, list) else (parsed or {}).get('addresses')
    if not isinstance(arr, list):
        return []
    return [a.lower() for a in arr if isinstance(a, str)]


def owners(module: str) -> Dict[str, Any]:
    """Everyone who may write this module's mark, and where each came from."""
    group, name, path = resolve(module)
    declared = manifest(path).get('owner')
    declared = str(declared).lower() if isinstance(declared, str) and declared else None
    co = [a for a in co_owners(name) if a != declared]
    addresses = ([declared] if declared else []) + co
    fallback = None
    if not addresses:
        # Nobody is declared. Rather than let anyone paint it, fall back to
        # whoever runs THIS deployment — and say so out loud.
        fallback = deployment_owner()
        if fallback:
            addresses = [fallback]
    return {
        'module': f'{group}/{name}',
        'name': name,
        'group': group,
        'owner': declared,
        'co_owners': co,
        'addresses': addresses,
        'source': ('config.json' if declared else
                   'owners.json' if co else
                   'logo deployment owner (this module declares none)' if fallback else
                   'nobody — this module declares no owner and this deployment is unclaimed'),
    }


def may_write(address: Optional[str], module: str) -> bool:
    if address == 'open-mode':
        return True
    if not address:
        return False
    return address.lower() in owners(module)['addresses']


def require_owner(token: Optional[str], module: str) -> str:
    """The gate every write goes through."""
    address = require(token)
    resolve(module)                       # UnknownModule before anything else
    if address == 'open-mode':
        return address
    if not may_write(address, module):
        who = owners(module)
        expected = who['owner'] or (who['addresses'][0] if who['addresses'] else None)
        raise AuthError(
            f'{address} may not set {who["module"]}\'s mark — '
            + (f'that belongs to {expected} ({who["source"]})' if expected else
               'no owner is declared for it and this deployment is unclaimed'))
    return address


# ── this deployment ──────────────────────────────────────────────────

def deployment_owner() -> Optional[str]:
    try:
        return (json.loads(OWNER_PATH.read_text()) or {}).get('address')
    except Exception:
        env = os.environ.get('LOGO_OWNER')
        return env.lower() if env else None


def claim(address: str) -> Dict[str, Any]:
    """First signed caller claims the deployment; after that it is fixed. This
    only ever matters for modules that declare no owner of their own."""
    existing = deployment_owner()
    if existing and existing.lower() != (address or '').lower():
        raise AuthError(f'this deployment already belongs to {existing}')
    OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_PATH.write_text(json.dumps({'address': address.lower(),
                                      'claimed': int(time.time())}, indent=2))
    return {'owner': address.lower(), 'claimed': True}


def status() -> Dict[str, Any]:
    return {
        'open_mode': open_mode(),
        'deployment_owner': deployment_owner(),
        'accepts': ['mod-protocol token (m.mod("auth").token) — the same token '
                    'the store, dns and lighthouse modules verify'],
        'token_max_age': TOKEN_MAX_AGE,
        'tree': str(TREE),
        'state': str(STATE),
        'rule': ('a mark may only be set by the owner in the target module\'s own '
                 'config.json (or a co-owner in ~/.mod/{module}/owners.json)'),
        'note': ('LOGO_OPEN is set — every caller may repaint every module. '
                 'Development only.' if open_mode() else
                 'reads are public; every write is owner-signed'),
    }
