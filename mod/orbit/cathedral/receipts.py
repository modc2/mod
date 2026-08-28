"""Offline verification of cathedral_customer_receipt_v1.

A receipt is the whole point of renting confidential compute: it is Cathedral's
signed statement binding the measured environment, the workload, the result, the
charge and the teardown. Fetching one and trusting it because it arrived over
TLS proves nothing — the signature is what makes it evidence, and checking that
signature is an *offline* act you can repeat later, on a different machine, long
after the worker is gone.

Cathedral publishes its ed25519 receipt-signing keys at
    https://cathedral.computer/customer-receipt-trusted-keys.json
and their docs are explicit that you should pin that file through a channel you
trust before relying on it. So `trusted_keys()` writes it to ~/.mod/cathedral
once and thereafter *refuses to silently accept a changed public key* for a
key_id it already knows: a rotated key is news, not a detail.

WHAT THIS PROVES, AND WHAT IT DOES NOT
    It proves that a locally trusted Cathedral key signed exactly these
    assertions and that the document is internally consistent. It does not
    replay Intel or NVIDIA evidence, inspect billing, contact the provider,
    confirm teardown, or prove AMD SEV host attestation — the same boundary
    Cathedral draws around its own verifier.

CANONICALIZATION
    Cathedral documents the algorithm (ed25519) and the covered content ("every
    top-level assertion except the signature object") but does not publish the
    byte encoding those assertions are serialized to before signing. Rather than
    guess one and report a false negative as tampering, `verify` tries the
    standard encodings in order and tells you which one matched. A receipt that
    matches none is reported as `signature: "unverified"` with the candidates
    that were tried — never as `verified`, and never as forged.
"""
import base64
import binascii
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

TRUSTED_KEYS_URL = 'https://cathedral.computer/customer-receipt-trusted-keys.json'
TRUSTED_KEYS_SCHEMA = 'cathedral_customer_receipt_trusted_keys_v1'
RECEIPT_SCHEMA = 'cathedral_customer_receipt_v1'

HEX64 = re.compile(r'^(sha256:)?[0-9a-f]{64}$')

# Where a signature blob hides, across the shapes upstream has used.
_SIG_FIELDS = ('signature', 'value', 'sig', 'signature_base64', 'signature_b64')
_KEY_ID_FIELDS = ('key_id', 'keyid', 'kid', 'key')


# ── canonicalization candidates ──────────────────────────────────────────

def _jcs(doc: dict) -> bytes:
    """RFC 8785-shaped: keys sorted, no whitespace, UTF-8."""
    return json.dumps(doc, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


CANONICAL = {
    # Most likely first: JCS is what a signed-JSON contract normally means.
    'jcs': _jcs,
    'jcs_ascii': lambda d: json.dumps(d, sort_keys=True, separators=(',', ':'),
                                      ensure_ascii=True).encode('utf-8'),
    # Some servers sign the exact bytes they emitted, in field order.
    'compact_asis': lambda d: json.dumps(d, separators=(',', ':'),
                                         ensure_ascii=False).encode('utf-8'),
    'compact_asis_ascii': lambda d: json.dumps(d, separators=(',', ':'),
                                               ensure_ascii=True).encode('utf-8'),
    'sorted_indent2': lambda d: json.dumps(d, sort_keys=True, indent=2,
                                           ensure_ascii=False).encode('utf-8'),
}


def signed_bytes(doc: dict, encoding: str = 'jcs') -> bytes:
    """The assertions a signature covers: the document minus the signature object."""
    return CANONICAL[encoding]({k: v for k, v in doc.items() if k != 'signature'})


# ── trusted keys ─────────────────────────────────────────────────────────

def parse_trusted_keys(payload: dict) -> dict:
    """{key_id: {algorithm, public_key_base64, status, valid_from, valid_until}}."""
    if not isinstance(payload, dict):
        raise ValueError('trusted-keys file is not a JSON object')
    schema = payload.get('schema')
    if schema and schema != TRUSTED_KEYS_SCHEMA:
        raise ValueError(f'unexpected trusted-keys schema {schema!r}')
    keys = payload.get('keys')
    if not isinstance(keys, dict) or not keys:
        raise ValueError('trusted-keys file carries no keys')
    return keys


def merge_pinned(pinned: dict, fetched: dict) -> dict:
    """Fold a freshly fetched key set into the pinned one.

    New key_ids are added — key rotation is routine. A key_id whose public key
    *changed* is reported as a conflict and the pinned value wins: that is
    either a rotation Cathedral should have published under a new id, or
    somebody swapping the key you verify against.
    """
    merged = dict(pinned)
    added, conflicts = [], []
    for kid, entry in (fetched or {}).items():
        have = pinned.get(kid)
        if have is None:
            merged[kid] = entry
            added.append(kid)
        elif have.get('public_key_base64') != entry.get('public_key_base64'):
            conflicts.append(kid)
        else:
            # Same key, possibly new status/validity window — take the update.
            merged[kid] = entry
    return {'keys': merged, 'added': added, 'conflicts': conflicts}


def _key_usable(entry: dict, at: Optional[datetime] = None) -> Optional[str]:
    """None if the key may be used, else why not."""
    if (entry.get('algorithm') or '').lower() != 'ed25519':
        return f"unsupported algorithm {entry.get('algorithm')!r}"
    if (entry.get('status') or 'active').lower() != 'active':
        return f"key status is {entry.get('status')!r}"
    now = at or datetime.now(timezone.utc)
    for field, cmp in (('valid_from', lambda t: now < t), ('valid_until', lambda t: now > t)):
        stamp = _ts(entry.get(field))
        if stamp and cmp(stamp):
            return f'outside the key validity window ({field}={entry.get(field)})'
    return None


def _ts(text) -> Optional[datetime]:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None


# ── structure ────────────────────────────────────────────────────────────

def _sig_block(doc: dict) -> dict:
    sig = doc.get('signature')
    return sig if isinstance(sig, dict) else {}


def _pick(d: dict, fields) -> Optional[str]:
    for f in fields:
        v = d.get(f)
        if isinstance(v, str) and v:
            return v
    return None


def _b64(text: str) -> Optional[bytes]:
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return decode(text + '=' * (-len(text) % 4))
        except (binascii.Error, ValueError):
            continue
    return None


def structure(doc: dict) -> dict:
    """Consistency checks that hold regardless of who signed it.

    Reported as pass/fail rows rather than one boolean, because a receipt can be
    genuinely signed and still say the run did not do what you wanted.
    """
    checks, notes = [], []

    def check(name, ok, detail=None):
        checks.append({'check': name, 'ok': bool(ok), **({'detail': detail} if detail else {})})

    schema = doc.get('schema') or doc.get('receipt_schema')
    check('schema', schema == RECEIPT_SCHEMA, schema)
    status = doc.get('receipt_status')
    check('receipt_status=ready', status == 'ready', status)

    sig = _sig_block(doc)
    check('signature.algorithm=ed25519', (sig.get('algorithm') or '').lower() == 'ed25519',
          sig.get('algorithm'))
    check('signature.key_id', bool(_pick(sig, _KEY_ID_FIELDS)), _pick(sig, _KEY_ID_FIELDS))
    check('signature present', bool(_pick(sig, _SIG_FIELDS)))

    # Cathedral's contract: three 64-hex digests must be present and well formed.
    digests = {k: v for k, v in doc.items()
               if isinstance(v, str) and ('digest' in k or k.endswith('_sha256'))}
    bad = {k: v for k, v in digests.items() if not HEX64.match(v)}
    check('digests are 64-hex', digests and not bad, bad or f'{len(digests)} digest field(s)')

    # Binding/evidence gates the receipt asserts about itself.
    gates = {k: v for k, v in doc.items()
             if isinstance(v, str) and k.endswith(('_status', '_evidence_status'))
             and k != 'receipt_status'}
    failed = {k: v for k, v in gates.items() if v not in ('PASS', 'ready', 'confirmed')}
    if gates:
        check('binding/evidence gates PASS', not failed, failed or f'{len(gates)} gate(s) PASS')

    # Documented, expected nulls on the G4 path — surfaced so nobody reads a
    # missing Intel field as a failed one.
    if doc.get('cpu_tee') == 'amd_sev':
        notes.append('G4/AMD SEV path: report_data_match and intel_verified are null by design, '
                     'and the receipt carries no verified AMD SEV host evidence')
    if doc.get('job_id') is not None or isinstance(doc.get('verification'), dict):
        notes.append('nested verification object / job_id present — this looks like the legacy '
                     'unsigned shape, which the public verifier does not accept')

    return {'ok': all(c['ok'] for c in checks), 'checks': checks, 'notes': notes}


# ── the verification itself ──────────────────────────────────────────────

def verify(doc: dict, keys: dict) -> dict:
    """Check a receipt against a pinned trusted-key set. Never trusts on failure."""
    if not isinstance(doc, dict):
        return {'verified': False, 'error': 'receipt is not a JSON object'}

    struct = structure(doc)
    out: dict[str, Any] = {
        'verified': False,
        'receipt_id': doc.get('receipt_id') or doc.get('id'),
        'schema': doc.get('schema') or doc.get('receipt_schema'),
        'structure': struct,
    }

    sig = _sig_block(doc)
    if not sig:
        out['signature'] = 'missing'
        out['error'] = 'receipt carries no signature object — nothing to verify'
        return out

    key_id = _pick(sig, _KEY_ID_FIELDS)
    raw_sig = _pick(sig, _SIG_FIELDS)
    out['key_id'] = key_id
    if not raw_sig:
        out['signature'] = 'missing'
        out['error'] = 'signature object carries no signature value'
        return out

    entry = (keys or {}).get(key_id) if key_id else None
    if entry is None:
        out['signature'] = 'untrusted_key'
        out['error'] = (f'signing key {key_id!r} is not in the pinned trusted-key set'
                        if key_id else 'signature names no key_id')
        out['trusted_key_ids'] = sorted(keys or {})
        return out

    why = _key_usable(entry)
    if why:
        out['signature'] = 'key_unusable'
        out['error'] = why
        return out

    pub = _b64(entry.get('public_key_base64') or '')
    sig_bytes = _b64(raw_sig)
    if not pub or len(pub) != 32:
        out['signature'] = 'bad_key'
        out['error'] = 'trusted key is not a 32-byte ed25519 public key'
        return out
    if not sig_bytes or len(sig_bytes) != 64:
        out['signature'] = 'bad_signature'
        out['error'] = f'signature is not 64 bytes (got {len(sig_bytes or b"")})'
        return out

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        out['signature'] = 'unavailable'
        out['error'] = 'ed25519 verification needs the `cryptography` package (pip install cryptography)'
        return out

    verifier = Ed25519PublicKey.from_public_bytes(pub)
    for name in CANONICAL:
        try:
            verifier.verify(sig_bytes, signed_bytes(doc, name))
        except InvalidSignature:
            continue
        out['verified'] = struct['ok']
        out['signature'] = 'valid'
        out['canonicalization'] = name
        out['signed_by'] = key_id
        if not struct['ok']:
            out['error'] = ('signature is valid but the document fails its own consistency '
                            'checks — see structure.checks')
        out['proves'] = ('a locally trusted Cathedral key signed exactly these assertions; '
                         'it does not replay Intel/NVIDIA evidence, inspect billing, contact '
                         'the provider, confirm teardown, or prove AMD SEV host attestation')
        return out

    out['signature'] = 'unverified'
    out['tried'] = list(CANONICAL)
    out['error'] = ('no published canonicalization reproduced the signed bytes. Cathedral does '
                    'not publish the byte encoding, so this is inconclusive rather than proof '
                    'of tampering — compare against `cathedral customer-receipt verify`')
    return out
