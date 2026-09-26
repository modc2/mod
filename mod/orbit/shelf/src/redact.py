"""
Redaction — the reason this module is safe to point at ~/.mod.

Fleet state is not inert data. `~/.mod` holds `server.secret` HMAC keys, owner
claims, wallet material, GitHub PATs and BYOK vaults, sitting in the same tree
as the boring JSON somebody actually wants to look at. A state browser that
renders every value it finds is a credential exfiltrator with a nice console.
So redaction happens here, on the read path, before a value is ever returned to
a caller — not in the page, where it would be one `curl` away from irrelevant.

What comes back in place of a secret is a fingerprint — `sha256:1f3a9c02`, and
the length. That is deliberately more than a row of asterisks. It answers the
questions an operator actually has: do two modules share a secret, did this key
rotate since yesterday, is the field empty or just hidden? None of those need
the bytes, and the bytes never leave the box.

Matching is on names, never on values. A name is sensitive if any term in
SENSITIVE appears anywhere in it, case-insensitively, which over-redacts on
purpose: `pubkey` is hidden because it contains `key`. The failure modes are
not symmetric — a redacted public key is an inconvenience, a rendered private
one is an incident — so the tie goes to hiding it.

Some files are sensitive *as files*: `server.secret`, `*.pem`, `.env`. Those
are never opened at all. Redacting a value you have already read into memory is
a weaker promise than not reading it, and this is the cheaper of the two.
"""
import hashlib
import os
from typing import Any

# Substrings that make a key or filename sensitive. Lowercase, matched anywhere.
SENSITIVE = (
    'secret', 'password', 'passwd', 'token', 'apikey', 'api_key', 'privkey',
    'private', 'mnemonic', 'seed', 'credential', 'auth', 'signature', 'cookie',
    'session', 'bearer', 'salt', 'cipher', 'encrypted', 'key',
)

# Whole files that are secret by nature. Opened for `stat` only, never read.
SENSITIVE_FILES = (
    'server.secret', 'owner.json', '.env', 'credentials.json', 'keyfile',
)
SENSITIVE_EXTS = ('.pem', '.key', '.p12', '.pfx', '.jks', '.kdbx')

# Names that contain a sensitive term but are not secrets. Checked first, exact
# match on the final path/key segment, so `keys` (a listing) stays readable
# while `keys.json` under a vault does not.
ALLOW = ('keyword', 'keywords', 'monkey', 'donkey', 'authors', 'author')

REDACTED = '[redacted]'


def _lower(name: Any) -> str:
    return str(name).lower()


def sensitive_name(name: Any) -> bool:
    """Is this key or filename one whose value must not be rendered?"""
    low = _lower(name)
    if low in ALLOW:
        return False
    return any(term in low for term in SENSITIVE)


def sensitive_file(path: str) -> bool:
    """Is this whole file secret — never to be opened, only stat-ed?"""
    base = os.path.basename(path).lower()
    if base in SENSITIVE_FILES or base.endswith(SENSITIVE_EXTS):
        return True
    # A file inside a directory called `vault`/`keys` is secret whatever it is
    # named: modules put per-user key material in there under opaque ids.
    parts = {p.lower() for p in path.split(os.sep)}
    return bool(parts & {'vault', 'keys', 'wallets', '.ssh'})


def fingerprint(value: Any) -> str:
    """A stable, non-reversing label for a value.

    Same secret in two places gives the same fingerprint, which is the whole
    point — it is how you notice that two modules were handed one key, or that
    one did not rotate when it was told to.
    """
    if value is None:
        return 'null'
    raw = value if isinstance(value, bytes) else str(value).encode('utf-8', 'replace')
    if not raw:
        return 'empty'
    digest = hashlib.sha256(raw).hexdigest()[:8]
    return f'sha256:{digest} ({len(raw)}b)'


def value(name: Any, val: Any, _depth: int = 0) -> Any:
    """Redact `val`, recursing into containers.

    The name that decides is the one the value is filed under, so a secret
    nested three dicts deep is still caught by its own key rather than by the
    name of the file it happens to live in.
    """
    if _depth > 12:                       # cyclic or absurd; stop descending
        return '[deep]'
    if sensitive_name(name):
        return fingerprint(val) if not isinstance(val, (dict, list)) else REDACTED
    if isinstance(val, dict):
        return {k: value(k, v, _depth + 1) for k, v in val.items()}
    if isinstance(val, list):
        # Lists carry their parent's name, which has already been cleared.
        return [value(name, v, _depth + 1) for v in val]
    return val


def document(key: str, doc: Any) -> Any:
    """Redact a whole stored document, judged by its key and its own fields."""
    if sensitive_name(os.path.basename(str(key))):
        return REDACTED
    return value('', doc)


def scrub_text(text: str, limit: int = 4000) -> str:
    """Truncate free text and blank anything that looks like a long token.

    Used for previewing files that are not JSON. It is a coarser instrument
    than `value` — there are no names to judge — so it errs toward the shape of
    the thing: long unbroken runs of key-ish characters are what secrets look
    like, and prose is not.
    """
    import re
    out = text[:limit]
    out = re.sub(r'\b[A-Za-z0-9_\-]{40,}\b', '[long-token]', out)
    if len(text) > limit:
        out += f'\n... [{len(text) - limit} more bytes]'
    return out
