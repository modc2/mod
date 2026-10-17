"""What has to be true, in the order it has to be true in.

The primitives are checked against the reference implementations (`eth_hash`,
`pynacl`, `hashlib`) and against published vectors, so this suite is not just
this module agreeing with itself. Then the rules — a join needs consent, a nonce
burns, a merge needs both sides — each with the refusal tested, not only the
success.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from src import accounts, chains, identity, signers, statement, store  # noqa: E402
from src.crypto import base58, bech32, ed25519, keccak, ripemd160, secp256k1  # noqa: E402


@pytest.fixture(autouse=True)
def sandbox():
    """Every test gets an empty store that is deleted afterwards."""
    with store.sandbox() as home:
        yield home


# ── the primitives, against outside implementations ──────────────────

def test_keccak_is_not_sha3():
    assert keccak.keccak256(b'').hex() == (
        'c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470')
    assert keccak.keccak256(b'') != hashlib.sha3_256(b'').digest()


def test_keccak_matches_eth_hash():
    reference = pytest.importorskip('eth_hash.auto').keccak
    for size in (0, 1, 135, 136, 137, 4096):
        data = os.urandom(size)
        assert keccak.keccak256(data) == reference(data)


def test_ripemd160_vectors():
    for message, want in (
            (b'', '9c1185a5c5e9fc54612808977ee8f548b2258d31'),
            (b'abc', '8eb208f7e05d987a9b044a8e98c6b087f15a0bfc'),
            (b'message digest', '5d0689ef49d2fae572b881b123a85ffa21595f36'),
            (b'a' * 1000000, '52783243c1697bdbe16d37f97f68f08325dc1528')):
        assert ripemd160.ripemd160(message).hex() == want


def test_ed25519_matches_rfc8032_and_pynacl():
    seed = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
    assert ed25519.public_key(seed).hex() == (
        'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
    assert ed25519.verify(b'', ed25519.sign(b'', seed), ed25519.public_key(seed))

    nacl = pytest.importorskip('nacl.signing')
    key = nacl.SigningKey(os.urandom(32))
    message = b'the statement'
    assert ed25519.verify(message, key.sign(message).signature, bytes(key.verify_key))
    assert not ed25519.verify(message + b'!', key.sign(message).signature,
                              bytes(key.verify_key))


def test_secp256k1_matches_eth_keys():
    eth_keys = pytest.importorskip('eth_keys').keys
    secret = int.from_bytes(os.urandom(32), 'big') % secp256k1.N
    mine = secp256k1.uncompressed(secp256k1.public_key(secret))
    assert mine == eth_keys.PrivateKey(secret.to_bytes(32, 'big')).public_key.to_bytes()


def test_secp256k1_recovery_roundtrip():
    secret = 0x4646464646464646464646464646464646464646464646464646464646464646
    digest = hashlib.sha256(b'recover me').digest()
    r, s, recovery = secp256k1.sign(digest, secret)
    assert secp256k1.recover(digest, r, s, recovery) == secp256k1.public_key(secret)
    assert s <= secp256k1.N // 2, 'signatures must come out low-s'
    with pytest.raises(ValueError):
        secp256k1.recover(digest, r, secp256k1.N - s, recovery)   # high s refused


def test_bitcoin_addresses_match_published_vectors():
    key = secp256k1.compress(secp256k1.public_key(1))
    forms = chains._btc_addresses(key, chains._BITCOIN)
    assert forms['p2pkh'] == '1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH'
    assert forms['p2pkh-uncompressed'] == '1EHNa6Q4Jz2uvNExL497mE43ikXhwF6kZm'
    assert forms['p2wpkh'] == 'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4'   # BIP-173


def test_bech32_bip350_vectors():
    assert bech32.encode_segwit('bc', 1, bytes.fromhex(
        '79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798')) == (
        'bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0')
    assert bech32.decode_segwit('bc', 'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4')[0] == 0


def test_base58_roundtrip_keeps_leading_zeros():
    payload = b'\x00\x00' + os.urandom(20)
    assert base58.check_decode(base58.check_encode(payload)) == payload
    with pytest.raises(ValueError):
        base58.check_decode('1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMG')   # bad checksum


# ── the digest each chain hashes, spelled out ────────────────────────

def test_ethereum_prefix_is_eip191():
    text = 'hello'
    assert chains._eip191_digest(text) == keccak.keccak256(
        b'\x19Ethereum Signed Message:\n5hello')


def test_bitcoin_preimage_is_the_magic_string():
    spec = chains._BITCOIN
    assert chains.varint(len(spec.magic)) + spec.magic == b'\x18Bitcoin Signed Message:\n'


def test_cosmos_sign_document_is_amino_json():
    document = chains._adr036_document('cosmos1abc', 'hi')
    assert json.loads(document) == {
        'account_number': '0', 'chain_id': '', 'fee': {'amount': [], 'gas': '0'},
        'memo': '', 'sequence': '0',
        'msgs': [{'type': 'sign/MsgSignData',
                  'value': {'data': 'aGk=', 'signer': 'cosmos1abc'}}]}


def test_eth_signature_from_eth_account_verifies():
    account_module = pytest.importorskip('eth_account')
    messages = pytest.importorskip('eth_account.messages')
    wallet = account_module.Account.create()
    text = 'mod:id/v1 link\nid: id_0123456789abcdef'
    signature = wallet.sign_message(messages.encode_defunct(text=text)).signature.hex()
    result = chains.verify('eth', wallet.address, text, signature)
    assert result['address'] == wallet.address.lower()
    with pytest.raises(chains.ProofError):
        chains.verify('eth', wallet.address, text + ' ', signature)


@pytest.mark.parametrize('chain', sorted(signers.MAKERS))
def test_every_chain_verifies_its_own_wallet_and_rejects_a_forgery(chain):
    wallet = signers.make(chain)
    text = 'mod:id/v1 link\naccount: whatever'
    result = chains.verify(chain, wallet.address, text, **wallet.proof(text))
    assert result['ok'] and result['strength'] == 'key'

    other = signers.make(chain)
    with pytest.raises(chains.ProofError):
        chains.verify(chain, other.address, text, **wallet.proof(text))
    with pytest.raises(chains.ProofError):
        chains.verify(chain, wallet.address, text + 'x', **wallet.proof(text))


def test_bitcoin_verifies_every_address_form():
    for form in ('p2pkh', 'p2sh-p2wpkh', 'p2wpkh'):
        wallet = signers.bitcoin(form)
        text = 'sign me'
        assert chains.verify('btc', wallet.address, text, wallet.sign(text))['form'] == form


def test_taproot_is_refused_with_a_reason():
    with pytest.raises(chains.AddressError, match='taproot'):
        chains.parse('bitcoin', 'bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0')


def test_evm_networks_are_one_account_not_many():
    wallet = signers.ethereum()
    assert chains.get('base').name == chains.get('arbitrum').name == 'ethereum'
    assert chains.parse('polygon', wallet.address) == chains.parse('eth', wallet.address)


def test_base58_signature_is_not_misread_as_base64():
    """An 88-character base58 signature is also valid base64, and decodes to junk."""
    wallet = signers.solana()
    text = 'a statement long enough to be interesting'
    signature = wallet.sign(text)
    assert len(signature) in (87, 88)
    assert chains.unhex(signature, (64,)) == base58.decode(signature)
    assert chains.verify('solana', wallet.address, text, signature)['ok']


# ── the statement ────────────────────────────────────────────────────

def test_statement_is_reproducible_and_domain_separated():
    fields = statement.fields('link', 'id_1', 'ethereum:0xabc', id='id_1')
    text = statement.render(fields)
    assert text.startswith('mod:id/v1 link')
    assert statement.render(fields) == text
    assert 'moves no funds' in text
    assert fields['nonce'] in text and 'id_1' in text


def test_expired_challenge_is_refused():
    fields = statement.fields('link', 'id_1', 'ethereum:0xabc', ttl=1,
                              issued=statement.now() - 10)
    with pytest.raises(ValueError, match='expired'):
        statement.check_fresh(fields)


# ── the rules ────────────────────────────────────────────────────────

def link(wallet, id=None, session=None, op='link', **extra):
    ask = identity.challenge(wallet.chain, wallet.address, op=op, id=id, **extra)
    return identity.submit(ask['nonce'], session=session, **wallet.proof(ask['statement']))


def test_first_wallet_creates_an_identity_named_after_itself():
    wallet = signers.ethereum()
    made = link(wallet)
    assert made['op'] == 'genesis'
    assert made['id'] == identity.derive(wallet.account)
    assert identity.members(made['id']) == [wallet.account]


def test_a_second_wallet_needs_the_first_ones_consent():
    first, second = signers.ethereum(), signers.solana()
    made = link(first)

    with pytest.raises(identity.IdError, match='consent'):
        link(second, id=made['id'])

    joined = link(second, id=made['id'], session=made['session'])
    assert joined['authorized_by'] == first.account
    assert set(identity.members(made['id'])) == {first.account, second.account}


def test_a_session_only_works_on_its_own_identity():
    mine, theirs, newcomer = signers.ethereum(), signers.ethereum(), signers.solana()
    made = link(mine)
    other = link(theirs)
    with pytest.raises(identity.IdError, match='consent'):
        link(newcomer, id=other['id'], session=made['session'])


def test_a_nonce_burns_after_use():
    wallet = signers.ethereum()
    ask = identity.challenge('ethereum', wallet.address)
    signature = wallet.proof(ask['statement'])
    identity.submit(ask['nonce'], **signature)
    with pytest.raises(identity.IdError, match='not outstanding'):
        identity.submit(ask['nonce'], **signature)


def test_a_failed_attempt_does_not_burn_the_nonce():
    wallet = signers.ethereum()
    ask = identity.challenge('ethereum', wallet.address)
    with pytest.raises(chains.ProofError):
        identity.submit(ask['nonce'], signature='0x' + '00' * 65)
    assert identity.submit(ask['nonce'], **wallet.proof(ask['statement']))['ok']


def test_a_signature_for_one_identity_cannot_be_used_on_another():
    mine, theirs, newcomer = signers.ethereum(), signers.ethereum(), signers.solana()
    made, other = link(mine), link(theirs)
    ask = identity.challenge('solana', newcomer.address, id=other['id'])
    signed = newcomer.proof(ask['statement'])
    # the statement names other['id']; the store applies it there, never to `made`
    joined = identity.submit(ask['nonce'], session=other['session'], **signed)
    assert joined['id'] == other['id'] != made['id']


def test_an_account_cannot_be_in_two_identities():
    shared, first, second = signers.solana(), signers.ethereum(), signers.ethereum()
    a, b = link(first), link(second)
    link(shared, id=a['id'], session=a['session'])
    with pytest.raises(identity.IdError, match='merge'):
        link(shared, id=b['id'], session=b['session'])


def test_merge_needs_a_signature_from_each_side():
    left, right = signers.ethereum(), signers.ethereum()
    a, b = link(left), link(right)
    survivor, absorbed = identity.merge_order(a['id'], b['id'])

    half = link(left, id=survivor, op='merge', other=absorbed)
    assert half['stage'] == 'half-signed'
    assert identity.document(survivor)['count'] == 1, 'nothing moved on one signature'

    done = link(right, id=survivor, op='merge', other=absorbed)
    assert done['id'] == survivor and done['absorbed'] == absorbed
    assert set(identity.members(survivor)) == {left.account, right.account}
    assert store.follow(absorbed) == survivor, 'the old name still resolves'


def test_an_outsider_cannot_sign_a_merge():
    left, right, outsider = signers.ethereum(), signers.ethereum(), signers.ethereum()
    a, b = link(left), link(right)
    link(outsider)
    survivor, absorbed = identity.merge_order(a['id'], b['id'])
    link(left, id=survivor, op='merge', other=absorbed)
    with pytest.raises(identity.IdError, match='member of each side'):
        link(outsider, id=survivor, op='merge', other=absorbed)


def test_an_account_can_always_remove_itself():
    first, second = signers.ethereum(), signers.solana()
    made = link(first)
    link(second, id=made['id'], session=made['session'])
    gone = link(second, op='unlink')
    assert gone['removed'] == second.account
    assert identity.members(made['id']) == [first.account]


def test_only_root_can_remove_somebody_else():
    root, member, third = signers.ethereum(), signers.solana(), signers.bitcoin()
    made = link(root)
    link(member, id=made['id'], session=made['session'])
    link(third, id=made['id'], session=made['session'])

    with pytest.raises(identity.IdError, match='root'):
        link(member, op='unlink', target=third.account)
    assert link(root, op='unlink', target=third.account)['removed'] == third.account


def test_the_last_account_cannot_leave():
    wallet = signers.ethereum()
    link(wallet)
    with pytest.raises(identity.IdError, match='only account'):
        link(wallet, op='unlink')


def test_only_root_names_the_identity():
    root, member = signers.ethereum(), signers.solana()
    made = link(root)
    link(member, id=made['id'], session=made['session'])
    with pytest.raises(identity.IdError, match='root'):
        link(member, op='name', name='mine')
    assert link(root, op='name', name='mine')['name'] == 'mine'


def test_whois_finds_the_siblings():
    first, second, third = signers.ethereum(), signers.solana(), signers.cosmos()
    made = link(first)
    link(second, id=made['id'], session=made['session'])
    link(third, id=made['id'], session=made['session'])
    found = identity.whois('solana', second.address)
    assert found['id'] == made['id']
    assert set(found['siblings']) == {first.account, third.account}
    assert identity.whois('ethereum', signers.ethereum().address)['found'] is False


# ── the audit ────────────────────────────────────────────────────────

def build_one():
    first, second = signers.ethereum(), signers.solana()
    made = link(first)
    link(second, id=made['id'], session=made['session'])
    return made['id'], first, second


def test_a_clean_log_audits_clean():
    id, _, _ = build_one()
    report = identity.audit(id)
    assert report['ok'] and report['events'] == 2
    assert all(row['ok'] for row in report['checked'])


def test_audit_catches_a_swapped_account():
    id, _, _ = build_one()
    path = store.log_path(id)
    lines = path.read_text().splitlines()
    event = json.loads(lines[1])
    event['account'] = 'ethereum:0x' + '11' * 20
    lines[1] = json.dumps(event)
    path.write_text('\n'.join(lines) + '\n')
    report = identity.audit(id)
    assert not report['ok']
    assert any('signature underneath' in p for row in report['checked'] for p in row['problems'])


def test_audit_catches_a_forged_signature():
    id, _, _ = build_one()
    path = store.log_path(id)
    lines = path.read_text().splitlines()
    event = json.loads(lines[1])
    event['proofs'][0]['signature'] = base58.encode(b'\x02' * 64)
    lines[1] = json.dumps(event)
    path.write_text('\n'.join(lines) + '\n')
    assert not identity.audit(id)['ok']


def test_audit_catches_an_account_inserted_without_consent():
    id, _, _ = build_one()
    path = store.log_path(id)
    lines = path.read_text().splitlines()
    event = json.loads(lines[1])
    event.pop('authorized_by')
    lines[1] = json.dumps(event)
    path.write_text('\n'.join(lines) + '\n')
    report = identity.audit(id)
    assert not report['ok']
    assert any('consenting' in p for row in report['checked'] for p in row['problems'])


def test_audit_catches_a_reused_nonce():
    id, _, second = build_one()
    events = store.events(id)
    events[1]['proofs'][0]['nonce'] = events[0]['proofs'][0]['nonce']
    path = store.log_path(id)
    path.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
    report = identity.audit(id)
    assert any('nonce reused' in p for row in report['checked'] for p in row['problems'])


# ── portability ──────────────────────────────────────────────────────

def test_an_identity_survives_the_trip_to_another_host():
    id, first, second = build_one()
    document = identity.export(id)
    assert 'secret' not in json.dumps(document).lower()

    with store.sandbox():                       # a different machine
        assert store.resolve(first.account) is None
        loaded = identity.import_document(document)
        assert loaded['id'] == id
        assert set(identity.members(id)) == {first.account, second.account}
        assert identity.audit(id)['ok']
        assert identity.whois(account=second.account)['id'] == id


def test_a_document_with_a_broken_proof_is_refused_on_import():
    id, _, _ = build_one()
    document = identity.export(id)
    document['events_log'][1]['proofs'][0]['statement'] += ' '
    with store.sandbox():
        with pytest.raises(identity.IdError, match='do not verify'):
            identity.import_document(document)
        assert not store.exists(id)


def test_the_index_is_only_a_cache():
    id, first, second = build_one()
    store.INDEX.unlink()
    assert store.resolve(first.account) is None
    identity.rebuild()
    assert store.resolve(first.account) == id
    assert store.resolve(second.account) == id


def test_merged_identities_still_resolve_after_a_rebuild():
    left, right = signers.ethereum(), signers.ethereum()
    a, b = link(left), link(right)
    survivor, absorbed = identity.merge_order(a['id'], b['id'])
    link(left, id=survivor, op='merge', other=absorbed)
    link(right, id=survivor, op='merge', other=absorbed)
    store.INDEX.unlink()
    identity.rebuild()
    assert store.follow(absorbed) == survivor
    assert store.resolve(right.account) == survivor


# ── the accounts that publish instead of signing ─────────────────────

def test_publication_handles_are_parsed_not_trusted():
    assert accounts.parse('github', 'https://github.com/Octocat') == 'octocat'
    assert accounts.parse('x', '@Some_One') == 'some_one'
    assert accounts.parse('dns', 'https://Example.COM/path') == 'example.com'
    assert accounts.parse('web', 'example.com/proof') == 'https://example.com/proof'
    with pytest.raises(accounts.AccountError):
        accounts.parse('github', 'not a login!')


def test_a_publication_challenge_asks_for_a_token_not_a_signature():
    ask = identity.challenge('github', 'octocat')
    assert ask['strength'] == 'publication'
    assert ask['token'].startswith('mod:id/v1 link')
    assert ask['nonce'] in ask['token']
    assert 'gist' in ask['publish_to']


def test_publication_proofs_are_labelled_weaker_than_keys():
    assert identity.strength_of('github') == 'publication'
    assert identity.strength_of('ethereum') == 'key'
    assert all(service['strength'] == 'publication' for service in accounts.known())


# ── the demo is the flow, not a mock ─────────────────────────────────

def test_demo_runs_and_the_refusals_refuse():
    from src import demo
    result = demo.run()
    assert result['ok']
    refusals = [step for step in result['steps'] if step['expect'] == 'refused']
    assert len(refusals) == 2
    assert all('refused' in step['result'] for step in refusals)
    assert result['steps'][-1]['result']['ok'] is False, 'tampering must be caught'
