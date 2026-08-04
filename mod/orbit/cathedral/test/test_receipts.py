"""Tests for offline receipt verification — real ed25519, no network.

Every signature here is produced by a key generated in the test, so a pass means
the verifier actually did the cryptography rather than believing a field.
"""
import base64
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('cathedral_receipts', ROOT / 'receipts.py')
rcpt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rcpt)

spec_m = importlib.util.spec_from_file_location('cathedral_mod_r', ROOT / 'mod.py')
cathedral = importlib.util.module_from_spec(spec_m)
spec_m.loader.exec_module(cathedral)

KID = 'cathedral-customer-receipt-2026-07-31-01'


@pytest.fixture
def signer():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    keys = {KID: {'algorithm': 'ed25519', 'public_key_base64': base64.b64encode(pub).decode(),
                  'status': 'active', 'valid_from': '2026-07-31T00:00:00.000000Z',
                  'valid_until': '2027-08-01T00:00:00.000000Z'}}
    return priv, keys


def body(**over):
    doc = {
        'schema': rcpt.RECEIPT_SCHEMA, 'receipt_id': 'rcpt_test', 'receipt_status': 'ready',
        'profile_id': 'gcp-g4-rtx-pro-6000-sev-v1', 'cpu_tee': 'amd_sev',
        'workload_digest': 'a' * 64, 'result_digest': 'b' * 64, 'environment_digest': 'c' * 64,
        'execution_status': 'PASS', 'teardown_status': 'PASS', 'charge_usd': 3.0,
    }
    doc.update(over)
    return doc


def sign(priv, doc, encoding='jcs'):
    sig = priv.sign(rcpt.signed_bytes(doc, encoding))
    return {**doc, 'signature': {'algorithm': 'ed25519', 'key_id': KID,
                                 'signature': base64.b64encode(sig).decode()}}


# ── the signature itself ─────────────────────────────────────────────────

def test_valid_signature_verifies(signer):
    priv, keys = signer
    out = rcpt.verify(sign(priv, body()), keys)
    assert out['verified'] is True
    assert out['signature'] == 'valid'
    assert out['canonicalization'] == 'jcs'
    assert out['signed_by'] == KID


@pytest.mark.parametrize('encoding', list(rcpt.CANONICAL))
def test_every_candidate_encoding_is_found(signer, encoding):
    """A receipt signed under any published encoding still verifies, and the
    verifier names which one it was rather than guessing silently."""
    priv, keys = signer
    out = rcpt.verify(sign(priv, body(), encoding), keys)
    assert out['verified'] is True
    # Distinct encodings can coincide on a doc with no unicode/ordering quirks;
    # what matters is that the bytes it names really do verify.
    assert rcpt.CANONICAL[out['canonicalization']]


def test_tampered_field_does_not_verify(signer):
    """The charge is signed content — editing it must not pass."""
    priv, keys = signer
    doc = sign(priv, body())
    doc['charge_usd'] = 0.01
    out = rcpt.verify(doc, keys)
    assert out['verified'] is False
    assert out['signature'] == 'unverified'


def test_added_field_does_not_verify(signer):
    priv, keys = signer
    doc = sign(priv, body())
    doc['refunded'] = True
    assert rcpt.verify(doc, keys)['verified'] is False


def test_unknown_key_id_is_refused(signer):
    priv, _ = signer
    out = rcpt.verify(sign(priv, body()), {})
    assert out['verified'] is False
    assert out['signature'] == 'untrusted_key'


def test_wrong_key_does_not_verify(signer):
    """A valid signature from a key that is not the trusted one is still a fail."""
    priv, keys = signer
    other = Ed25519PrivateKey.generate()
    out = rcpt.verify(sign(other, body()), keys)
    assert out['verified'] is False


def test_revoked_key_is_refused(signer):
    priv, keys = signer
    keys[KID]['status'] = 'revoked'
    out = rcpt.verify(sign(priv, body()), keys)
    assert out['verified'] is False
    assert out['signature'] == 'key_unusable'


def test_expired_key_is_refused(signer):
    priv, keys = signer
    keys[KID]['valid_until'] = '2026-07-31T00:00:01.000000Z'
    out = rcpt.verify(sign(priv, body()), keys)
    assert out['verified'] is False
    assert 'validity window' in out['error']


def test_missing_signature_object(signer):
    _, keys = signer
    out = rcpt.verify(body(), keys)
    assert out['verified'] is False
    assert out['signature'] == 'missing'


def test_malformed_signature_length(signer):
    _, keys = signer
    doc = {**body(), 'signature': {'algorithm': 'ed25519', 'key_id': KID,
                                   'signature': base64.b64encode(b'short').decode()}}
    out = rcpt.verify(doc, keys)
    assert out['signature'] == 'bad_signature'


def test_non_dict_receipt():
    assert rcpt.verify('not a receipt', {})['verified'] is False


# ── structure, independent of who signed ─────────────────────────────────

def test_structure_flags_wrong_schema():
    out = rcpt.structure(body(schema='something_else'))
    assert out['ok'] is False
    assert any(c['check'] == 'schema' and not c['ok'] for c in out['checks'])


def test_structure_flags_pending_receipt():
    out = rcpt.structure(body(receipt_status='pending'))
    assert any(c['check'] == 'receipt_status=ready' and not c['ok'] for c in out['checks'])


def test_structure_flags_bad_digest():
    out = rcpt.structure(body(result_digest='nope'))
    assert any(c['check'] == 'digests are 64-hex' and not c['ok'] for c in out['checks'])


def test_structure_flags_failed_binding_gate():
    out = rcpt.structure(body(teardown_status='NOT PROVEN'))
    assert any(c['check'] == 'binding/evidence gates PASS' and not c['ok'] for c in out['checks'])


def test_structure_notes_the_g4_nulls():
    assert any('AMD SEV' in n for n in rcpt.structure(body())['notes'])


def test_structure_notes_the_legacy_shape():
    notes = rcpt.structure(body(job_id='job_1'))['notes']
    assert any('legacy' in n for n in notes)


def test_signed_but_inconsistent_is_not_verified(signer):
    """A genuine signature over a document that fails its own gates must not
    come back `verified` — the signature is real, the claim is not clean."""
    priv, keys = signer
    out = rcpt.verify(sign(priv, body(receipt_status='pending')), keys)
    assert out['signature'] == 'valid'
    assert out['verified'] is False
    assert 'consistency' in out['error']


# ── pinning ──────────────────────────────────────────────────────────────

def test_parse_rejects_foreign_schema():
    with pytest.raises(ValueError):
        rcpt.parse_trusted_keys({'schema': 'something_else', 'keys': {'a': {}}})


def test_parse_rejects_empty_key_set():
    with pytest.raises(ValueError):
        rcpt.parse_trusted_keys({'schema': rcpt.TRUSTED_KEYS_SCHEMA, 'keys': {}})


def test_merge_adds_new_key_ids():
    merged = rcpt.merge_pinned({'a': {'public_key_base64': 'X'}},
                               {'a': {'public_key_base64': 'X'}, 'b': {'public_key_base64': 'Y'}})
    assert merged['added'] == ['b']
    assert not merged['conflicts']


def test_merge_keeps_pinned_key_on_conflict():
    """A changed public key for a known id is news, not an update."""
    merged = rcpt.merge_pinned({'a': {'public_key_base64': 'PINNED'}},
                               {'a': {'public_key_base64': 'SWAPPED'}})
    assert merged['conflicts'] == ['a']
    assert merged['keys']['a']['public_key_base64'] == 'PINNED'


def test_merge_takes_status_updates_for_same_key():
    merged = rcpt.merge_pinned({'a': {'public_key_base64': 'X', 'status': 'active'}},
                               {'a': {'public_key_base64': 'X', 'status': 'revoked'}})
    assert merged['keys']['a']['status'] == 'revoked'


# ── the CLI surface ──────────────────────────────────────────────────────

@pytest.fixture
def pinned(tmp_path, monkeypatch, signer):
    _, keys = signer
    path = tmp_path / 'trusted-keys.json'
    path.write_text(json.dumps({'schema': rcpt.TRUSTED_KEYS_SCHEMA, 'keys': keys}))
    monkeypatch.setattr(cathedral, 'TRUSTED_KEYS_PATH', path)
    monkeypatch.setattr(cathedral.requests, 'get',
                        lambda *a, **kw: pytest.fail('pinned keys must not refetch'))
    return path


def test_verify_fn_reads_a_saved_receipt(tmp_path, pinned, signer):
    priv, _ = signer
    doc = sign(priv, body())
    p = tmp_path / 'receipt.json'
    p.write_text(json.dumps(doc))
    out = cathedral.Mod().verify(path=str(p))
    assert out['verified'] is True
    assert out['source'] == str(p)


def test_verify_fn_takes_inline_json(pinned, signer):
    priv, _ = signer
    out = cathedral.Mod().verify(json.dumps(sign(priv, body())))
    assert out['verified'] is True


def test_verify_fn_strips_our_own_status_annotation(pinned, signer):
    """`_call` stamps a `status` onto responses. It is not signed content, so it
    must be stripped before verifying or every fetched receipt would fail."""
    priv, _ = signer
    doc = sign(priv, body())
    m = cathedral.Mod()
    m.receipt = lambda rid: {**doc, 'status': 200}
    out = m.verify('rcpt_test')
    assert out['verified'] is True


def test_verify_fn_needs_something_to_verify(pinned):
    assert 'error' in cathedral.Mod().verify()


def test_trusted_keys_uses_the_pin_without_network(pinned):
    out = cathedral.Mod().trusted_keys()
    assert out['fetched'] is False
    assert KID in out['keys']
    # Public keys are public, but the summary stays a summary.
    assert 'public_key_base64' not in out['keys'][KID]
