"""
The tests are mostly one question asked five ways: do the verifiers agree, and
do they still agree when the proof is broken?

A verifier suite that only checks valid proofs verify is worthless — anything
that returns True passes it. So every system here is tested in both directions,
and the tampering is done to the parts that matter: a public signal, a group
element, a Merkle sibling, a sigma protocol's context string.

The fixtures are real. `fixtures/build.sh` compiles the circuits with circom
and proves them with snarkjs; if they are missing, the snark tests skip rather
than pretend.

    python3 -m pytest tests -q          (or: m 0xprof/test)
"""
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import bounties, identity, market, proofs, storage, systems, verify  # noqa: E402
from src.methods import evm, native, node, snarkjs, solidity          # noqa: E402

FIXTURES = ROOT / 'fixtures'
CIRCUITS = [('threshold', 'g16', 'groth16'), ('threshold', 'plonk', 'plonk'),
            ('threshold', 'fflonk', 'fflonk'), ('multiplier', 'g16', 'groth16')]


def fixture(circuit, tag):
    files = [FIXTURES / f'{circuit}_{tag}_{part}.json'
             for part in ('proof', 'vkey', 'public')]
    if not all(f.exists() for f in files):
        pytest.skip(f'{circuit}/{tag} fixtures not built — run ./fixtures/build.sh')
    return [json.loads(f.read_text()) for f in files]


@contextmanager
def preserved(system, proof, vkey, signals):
    """Whatever is at this proof's id, put it back when the test is done.

    A proof id is the hash of its bytes, so the fixture a test publishes and the
    same fixture somebody listed for sale are not two records — they are one.
    A test that ends by dropping "its own" id therefore deletes a real listing,
    which is exactly how one went missing here once. So whatever was at that id
    is snapshotted first and restored afterwards, and the record is only really
    dropped when nothing was there before the test ran.
    """
    record_id = proofs.proof_id(systems.resolve(system), vkey or {}, proof,
                                list(signals or []))
    before = storage.get_record('proofs', record_id)
    try:
        yield record_id
    finally:
        if before:
            storage.put_record('proofs', record_id, before)
        else:
            storage.drop_record('proofs', record_id)


@contextmanager
def temporary(system, proof, vkey, signals, **kwargs):
    """Publish for the length of a test, restoring anything it displaced."""
    with preserved(system, proof, vkey, signals):
        yield proofs.publish(system, proof, vkey, signals, **kwargs)


def tamper(signals):
    """Change the statement, not the proof: the proof stays well-formed, so a
    verifier that rejects it is rejecting it on the maths and not on parsing."""
    return [str(int(signals[0]) + 1)] + list(signals[1:])


# ── the registry ─────────────────────────────────────────────────────

def test_every_system_can_be_checked_by_two_implementations():
    """The claim on the tin. A system with one verifier can never get past
    `claimed`, so a system listed with one is a bug in the module, not a fact
    about cryptography."""
    for name in systems.names():
        assert len(systems.get(name)['methods']) >= 2, name


def test_sniff_recognises_what_provers_actually_emit():
    for circuit, tag, expected in CIRCUITS:
        proof, vkey, _ = fixture(circuit, tag)
        assert systems.sniff({'proof': proof, 'vkey': vkey}) == expected


def test_unknown_system_is_a_refusal_not_a_guess():
    with pytest.raises(KeyError):
        systems.resolve('groth42')


# ── the methods, one at a time ───────────────────────────────────────

@pytest.mark.parametrize('circuit,tag,system', CIRCUITS)
def test_snarkjs_agrees_with_the_fixture_it_produced(circuit, tag, system):
    proof, vkey, signals = fixture(circuit, tag)
    assert snarkjs.verify(system, proof, vkey, signals)['ok'] is True
    assert snarkjs.verify(system, proof, vkey, tamper(signals))['ok'] is False


def test_native_groth16_matches_snarkjs_both_ways():
    proof, vkey, signals = fixture('threshold', 'g16')
    assert native.groth16(proof, vkey, signals)['ok'] is True
    assert native.groth16(proof, vkey, tamper(signals))['ok'] is False


def test_native_rejects_a_key_for_a_different_circuit():
    proof, _, signals = fixture('threshold', 'g16')
    _, other_key, _ = fixture('multiplier', 'g16')
    with pytest.raises(native.VerifyError):
        native.groth16(proof, other_key, signals)


def test_native_rejects_points_off_the_curve():
    proof, vkey, signals = fixture('threshold', 'g16')
    broken = json.loads(json.dumps(proof))
    broken['pi_a'][0] = str(int(broken['pi_a'][0]) + 1)
    with pytest.raises(native.VerifyError):
        native.groth16(broken, vkey, signals)


@pytest.mark.parametrize('system', ['schnorr', 'dleq'])
def test_sigma_proofs_verify_in_both_implementations(system):
    made = (native.prove_schnorr(31337, 'test') if system == 'schnorr'
            else native.prove_dleq(31337, None, 'test'))
    for method in (native, node):
        answer = method.verify(system, made['proof'], made['statement'],
                               made.get('public_signals'))
        assert answer['ok'] is True, method.__name__


@pytest.mark.parametrize('system', ['schnorr', 'dleq'])
def test_sigma_proofs_are_bound_to_their_context(system):
    """The replay bug this protocol family dies of: a proof of one statement
    accepted as a proof of another. The context string is in the challenge, so
    changing it must break the proof in every implementation."""
    made = (native.prove_schnorr(31337, 'for alice') if system == 'schnorr'
            else native.prove_dleq(31337, None, 'for alice'))
    elsewhere = {**made['statement'], 'context': 'for bob'}
    for method in (native, node):
        assert method.verify(system, made['proof'], elsewhere,
                             made.get('public_signals'))['ok'] is False


def test_a_pedersen_opening_verifies_and_only_opens_one_way():
    """Binding is the whole property: with H hashed to a point, nobody knows
    log_G(H), so no second (v', r') opens the same C. The test settles for what
    it can actually check — that both implementations accept the real opening
    and neither accepts a moved value or a moved blinding."""
    made = native.prove_pedersen(1000, blinding=12345, context='test')
    for method in (native, node):
        assert method.verify('pedersen', made['proof'], made['statement'],
                             made.get('public_signals'))['ok'] is True, method.__name__
    for tampered in ({**made['proof'], 'v': '1001'}, {**made['proof'], 'r': '12346'}):
        for method in (native, node):
            assert method.verify('pedersen', tampered, made['statement'],
                                 [])['ok'] is False, method.__name__


def test_pedersen_hides_the_value_until_it_is_opened():
    """Two commitments to the same value with different blindings must not look
    alike — a commitment that leaks equality is not hiding."""
    a = native.prove_pedersen(1000, context='test')
    b = native.prove_pedersen(1000, context='test')
    assert a['proof']['r'] != b['proof']['r']
    assert a['statement']['C'] != b['statement']['C']


def test_merkle_verifies_every_leaf_and_rejects_a_forged_one():
    leaves = [f'account-{i}'.encode() for i in range(9)]
    for index in range(len(leaves)):
        made = native.prove_merkle(leaves, index)
        for method in (native, node):
            assert method.verify('merkle', made['proof'], made['statement'], [])['ok']
    made = native.prove_merkle(leaves, 3)
    forged = {**made['proof'], 'leaf': '0x' + b'not-in-the-tree'.hex()}
    for method in (native, node):
        assert method.verify('merkle', forged, made['statement'], [])['ok'] is False


def test_the_two_merkle_implementations_agree_on_keccak_too():
    made = native.prove_merkle([b'a', b'b', b'c', b'd'], 1, 'keccak256')
    assert native.verify('merkle', made['proof'], made['statement'], [])['ok']
    assert node.verify('merkle', made['proof'], made['statement'], [])['ok']


# ── the two chain methods, which need network ────────────────────────

def rpc_or_skip():
    if not evm.available().get('available'):
        pytest.skip('no reachable RPC — the chain methods are optional by design')


def test_evm_precompiles_agree_with_the_local_pairing():
    rpc_or_skip()
    proof, vkey, signals = fixture('threshold', 'g16')
    assert evm.verify('groth16', proof, vkey, signals)['ok'] is True
    assert evm.verify('groth16', proof, vkey, tamper(signals))['ok'] is False


def test_evm_combines_public_inputs_the_same_way_on_chain_and_off():
    """The multi-scalar multiplication is the one part of a groth16 verifier
    that is easy to get subtly wrong; doing it both ways must land in the same
    place."""
    rpc_or_skip()
    proof, vkey, signals = fixture('threshold', 'g16')
    assert evm.verify('groth16', proof, vkey, signals, onchain_msm=True)['ok']
    assert evm.verify('groth16', proof, vkey, signals, onchain_msm=False)['ok']


@pytest.mark.parametrize('circuit,tag,system', CIRCUITS)
def test_the_real_solidity_verifier_agrees(circuit, tag, system):
    rpc_or_skip()
    if not solidity.available().get('available'):
        pytest.skip('solc or snarkjs missing')
    proof, vkey, signals = fixture(circuit, tag)
    assert solidity.verify(system, proof, vkey, signals)['ok'] is True
    assert solidity.verify(system, proof, vkey, tamper(signals))['ok'] is False


# ── consensus ────────────────────────────────────────────────────────

def verdicts(*pairs):
    return [{'method': method, 'status': status, 'at': i}
            for i, (method, status) in enumerate(pairs)]


def test_one_method_is_a_claim_and_two_are_a_verification():
    assert verify.consensus(verdicts(('native', 'valid')))['status'] == 'claimed'
    assert verify.consensus(verdicts(('native', 'valid'),
                                     ('snarkjs', 'valid')))['status'] == 'verified'


def test_disagreement_is_disputed_and_never_averaged():
    outcome = verify.consensus(verdicts(('native', 'valid'), ('snarkjs', 'invalid')))
    assert outcome['status'] == 'disputed'
    assert outcome['agree'] == ['native'] and outcome['disagree'] == ['snarkjs']


def test_an_error_is_not_a_rejection():
    """The failure mode that would make this module lie: a crashed verifier
    reported as a false proof."""
    outcome = verify.consensus(verdicts(('native', 'valid'), ('evm', 'error')))
    assert outcome['status'] == 'claimed'
    assert verify.consensus(verdicts(('evm', 'error')))['status'] == 'unverified'


def test_a_witness_cannot_promote_a_proof():
    outcome = verify.consensus(verdicts(('native', 'valid'), ('browser', 'valid')))
    assert outcome['status'] == 'claimed', 'a browser must not make a quorum'


def test_a_witness_can_contest_one():
    outcome = verify.consensus(verdicts(('native', 'valid'), ('snarkjs', 'valid'),
                                        ('browser', 'invalid')))
    assert outcome['status'] == 'verified' and outcome['contested'] is True


def test_rechecking_replaces_a_methods_verdict_rather_than_appending():
    existing = [{'method': 'evm', 'status': 'error', 'at': 1}]
    fresh = [{'method': 'evm', 'status': 'valid', 'at': 2}]
    merged = verify.merge(existing, fresh)
    assert len(merged) == 1 and merged[0]['status'] == 'valid'


def test_two_different_witnesses_both_count():
    merged = verify.merge(
        [{'method': 'browser', 'by': '0xaaa', 'status': 'valid', 'at': 1}],
        [{'method': 'browser', 'by': '0xbbb', 'status': 'invalid', 'at': 2}])
    assert len(merged) == 2


def test_two_people_rechecking_do_not_become_two_verdicts():
    """A method has one current answer about one proof, whoever pressed the
    button. Keying authoritative verdicts by person would let anybody double a
    method's vote by re-running it from a second address."""
    merged = verify.merge(
        [{'method': 'native', 'by': '0xaaa', 'status': 'valid', 'at': 1}],
        [{'method': 'native', 'by': '0xbbb', 'status': 'valid', 'at': 2}])
    assert len(merged) == 1
    assert merged[0]['by'] == '0xbbb', 'the latest run is the current one'


# ── who asked ────────────────────────────────────────────────────────

def signed_challenge(record_id, account=None):
    from eth_account import Account
    from eth_account.messages import encode_defunct
    account = account or Account.create()
    message = identity.action_challenge(record_id, account.address)['message']
    signature = Account.sign_message(encode_defunct(text=message),
                                     private_key=account.key).signature.hex()
    return account, message, signature


@contextmanager
def a_published_proof(author='0xchecks-test'):
    """A cheap real proof of this test's own, published and then taken away."""
    made = native.prove_schnorr(31337, f'check log test — {author}')
    with temporary('schnorr', made['proof'], made['statement'],
                   made.get('public_signals') or [], author=author) as record:
        yield record


def test_a_verdict_carries_who_made_it_run():
    with a_published_proof('0xasker') as record:
        assert {v.get('by') for v in record['verdicts']} == {'0xasker'}


def test_the_check_log_names_the_publisher_and_every_rechecker():
    with a_published_proof('0xpublisher-test') as record:
        account, message, signature = signed_challenge(record['id'])
        signed = identity.from_action(record['id'], account.address, message, signature)
        record = proofs.recheck(record['id'], by=signed['address'],
                                signature=signed['signature'])

        people = {row['address']: row for row in proofs.roster(record)}
        assert set(people) == {'0xpublisher-test', account.address}
        assert people['0xpublisher-test']['kinds'] == ['published']
        assert people[account.address]['signed'] == 1
        assert people[account.address]['signature'] == signature


def test_a_signature_is_bound_to_one_proof_and_one_action():
    from eth_account import Account
    from eth_account.messages import encode_defunct
    with a_published_proof('0xbinding-test') as record:
        account, message, signature = signed_challenge(record['id'])

        with pytest.raises(identity.AuthError):
            identity.from_action('another-proof-id', account.address, message, signature)

        sign_in = identity.challenge(account.address)['message']
        sign_in_sig = Account.sign_message(encode_defunct(text=sign_in),
                                           private_key=account.key).signature.hex()
        with pytest.raises(identity.AuthError):
            identity.from_action(record['id'], account.address, sign_in, sign_in_sig)

        stranger = Account.create()
        with pytest.raises(identity.AuthError):
            identity.from_action(record['id'], stranger.address, message, signature)


def test_a_signature_buys_exactly_one_run():
    """Ten minutes of validity times unlimited replays is a token, not a
    signature — and every replay would be published under the signer's name."""
    with a_published_proof('0xreplay-test') as record:
        account, message, signature = signed_challenge(record['id'])
        proofs.recheck(record['id'], by=account.address, signature=signature)
        with pytest.raises(proofs.ProofError):
            proofs.recheck(record['id'], by=account.address, signature=signature)


# ── the key the console makes in the tab ─────────────────────────────
#
# src/app/keys.js is a wallet written from scratch so a visitor without an
# extension can still hold an address. Everything downstream of a signature
# assumes eth_account can recover it, and a browser file is exactly the kind of
# thing nothing else in this suite would catch when it drifts — so it is run
# here, in node, against the same identity.py the API calls.

KEYS_JS = ROOT / 'src' / 'app' / 'keys.js'
BROWSER_PROBE = '''
import { webcrypto } from 'node:crypto';
import { keccak256, addressOf, personalSign, newKey, bytesToHex } from './keys.mjs';
// Every browser has crypto.getRandomValues; node 18 does not expose it to ES
// modules. That is the harness's problem to solve, not the console's.
globalThis.crypto ??= webcrypto;
const input = JSON.parse(process.argv[2]);
const key = input.key || newKey();
console.log(JSON.stringify({
  key,
  address: addressOf(key),
  keccak_empty: bytesToHex(keccak256('')),
  keccak_abc: bytesToHex(keccak256('abc')),
  known_address: addressOf('0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318'),
  signatures: (input.messages || []).map((m) => personalSign(m, key)),
}));
'''


def browser_key(tmp_path, key=None, messages=()):
    """Run the console's signer in node — .mjs because this package is not ESM."""
    import shutil
    import subprocess
    binary = shutil.which('node')
    if not binary:
        pytest.skip('node is not installed')
    (tmp_path / 'keys.mjs').write_text(KEYS_JS.read_text())
    (tmp_path / 'probe.mjs').write_text(BROWSER_PROBE)
    done = subprocess.run(
        [binary, str(tmp_path / 'probe.mjs'),
         json.dumps({'key': key, 'messages': list(messages)})],
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_browsers_own_key_hashes_and_signs_the_way_the_server_reads_it(tmp_path):
    """Signing in anonymously, end to end: a key made in a tab, a signature this
    box recovers, and no way for it to tell that from an extension's."""
    made = browser_key(tmp_path)

    # keccak-256, not SHA3 — one padding byte apart, and everything rests on it
    assert made['keccak_empty'] == ('c5d2460186f7233c927e7db2dcc703c0'
                                    'e500b653ca82273b7bfad8045d85a470')
    assert made['keccak_abc'] == ('4e03657aea45a94fc7d47ba826c8d667'
                                  'c0d1e6e33a64a036ec44f58fa12d6c45')
    assert made['known_address'] == '0x2c7536E3605D9C16a7a3D7b1898e529396a65c23'

    address, proof_id = made['address'], 'a' * 64
    challenge = identity.challenge(address)['message']
    action = identity.action_challenge(proof_id, address)['message']
    signed = browser_key(tmp_path, key=made['key'], messages=[challenge, action])
    assert signed['address'] == address
    sign_in_sig, action_sig = signed['signatures']

    # the sign-in the console does when there is no wallet in the browser
    assert identity._recover(challenge, sign_in_sig) == address
    assert identity.from_signature(address, challenge, sign_in_sig) == address
    assert identity.read_session(identity.mint_session(address)['token']) == address

    # and the signature that buys one re-run of one listing, from the same key
    assert identity.from_action(proof_id, address, action, action_sig)['address'] == address
    with pytest.raises(identity.AuthError):
        identity.from_action('b' * 64, address, action, action_sig)
    with pytest.raises(identity.AuthError):
        identity.from_action(proof_id, address, action, sign_in_sig)


def test_a_witness_who_disagrees_stays_on_their_own_row():
    with a_published_proof('0xwitness-test') as record:
        record = proofs.attest(record['id'], '0xdoubter', False, 'browser')
        doubter = [r for r in proofs.roster(record) if r['address'] == '0xdoubter'][0]
        assert doubter['contested'] is True and doubter['witnessed'] == 1
        assert record['contested'] is True
        assert record['status'] == 'verified', 'a witness contests, it never demotes'


def test_a_listing_carries_its_roster_without_being_opened():
    with a_published_proof('0xlisting-test') as record:
        listed = proofs.listing(record)
        assert listed['verifiers'][0]['address'] == '0xlisting-test'
        assert listed['checks'] == 1


def test_records_from_before_the_log_still_show_their_publisher():
    """The log was added after proofs existed. The first run certainly happened
    — the author and the timestamp are on the record — so it is reconstructed
    rather than shown as nobody."""
    with a_published_proof('0xlegacy-test') as record:
        legacy = {k: v for k, v in record.items() if k not in ('checks', 'verifiers')}
        log = proofs.checks_of(legacy)
        assert len(log) == 1 and log[0]['kind'] == 'published'
        assert log[0]['by'] == '0xlegacy-test' and log[0]['signature'] == ''


# ── end to end, through the market ───────────────────────────────────

def test_publish_verifies_with_everything_available_and_records_each_answer():
    proof, vkey, signals = fixture('threshold', 'g16')
    with temporary('groth16', proof, vkey, signals,
                   author='0xtest-author', title='test proof') as record:
        assert record['status'] == 'verified'
        assert len(record['agree']) >= 2
        methods_used = {v['method'] for v in record['verdicts']}
        assert 'native' in methods_used and 'snarkjs' in methods_used


def test_the_same_proof_published_twice_is_one_record():
    proof, vkey, signals = fixture('multiplier', 'g16')
    with temporary('groth16', proof, vkey, signals, author='0xa') as first:
        second = proofs.publish('groth16', proof, vkey, signals, author='0xb')
        assert first['id'] == second['id']


def test_a_priced_proof_hides_its_bytes_and_not_its_statement():
    proof, vkey, signals = fixture('multiplier', 'g16')
    with temporary('groth16', proof, vkey, signals, author='0xseller',
                   title='paid', price=5) as record:
        seen = proofs.view(record['id'], '0xstranger')
        assert seen['locked'] is True
        assert seen['proof']['locked'] is True
        assert seen['statement'] == vkey and seen['public_signals'] == signals
        assert seen['status'] == 'verified', 'the verdicts must be readable before paying'

        market.grant('0xbuyer-test', 10)
        proofs.buy(record['id'], '0xbuyer-test')
        assert proofs.view(record['id'], '0xbuyer-test')['locked'] is False


def test_an_invalid_proof_is_published_as_invalid_rather_than_refused():
    """A market that silently drops bad proofs teaches nobody anything. The
    record exists, and it says invalid."""
    proof, vkey, signals = fixture('multiplier', 'g16')
    with temporary('groth16', proof, vkey, tamper(signals),
                   author='0xliar', title='not a proof') as record:
        assert record['status'] == 'invalid'
        assert record['disagree']


def test_bounty_escrow_moves_only_when_the_spec_is_met():
    # Balances are asserted as deltas: the ledger lives in the store and
    # survives the test run, so an absolute number here would pass once and
    # then fail forever.
    proof, vkey, signals = fixture('threshold', 'g16')
    market.grant('0xposter-test', 50)
    poster_before = market.account('0xposter-test')['credits']
    prover_before = market.account('0xprover-test')['credits']
    bounty = bounties.create('0xposter-test', 'groth16', 20, vkey=vkey,
                             require=[{'index': 1, 'min': 999}],
                             title='test bounty')
    assert market.account('0xposter-test')['credits'] == poster_before - 20

    with preserved('groth16', proof, vkey, signals):
        outcome = bounties.submit(bounty['id'], '0xprover-test', proof, signals)
        assert outcome['submission']['accepted'] is True
        assert market.account('0xprover-test')['credits'] == prover_before + 20
    storage.drop_record('bounties', bounty['id'])


def test_a_bounty_rejects_a_valid_proof_of_the_wrong_statement():
    proof, vkey, signals = fixture('threshold', 'g16')
    market.grant('0xposter2-test', 50)
    prover_before = market.account('0xprover2-test')['credits']
    bounty = bounties.create('0xposter2-test', 'groth16', 5, vkey=vkey,
                             require=[{'index': 1, 'min': 10 ** 9}],
                             title='unreachable threshold')
    with preserved('groth16', proof, vkey, signals):
        outcome = bounties.submit(bounty['id'], '0xprover2-test', proof, signals)
        assert outcome['submission']['status'] == 'verified'
        assert outcome['submission']['accepted'] is False
        assert outcome['submission']['requirements']['failures']
        assert market.account('0xprover2-test')['credits'] == prover_before
    storage.drop_record('bounties', bounty['id'])


def test_cancelling_a_bounty_returns_the_escrow():
    market.grant('0xposter3-test', 30)
    before = market.account('0xposter3-test')['credits']
    bounty = bounties.create('0xposter3-test', 'schnorr', 30, title='cancel me')
    assert market.account('0xposter3-test')['credits'] == before - 30
    bounties.cancel(bounty['id'], '0xposter3-test')
    assert market.account('0xposter3-test')['credits'] == before
    storage.drop_record('bounties', bounty['id'])


def test_requirement_rules_compare_numbers_as_numbers():
    signals = ['0x3e8', '1000']
    assert bounties.check_requirements([{'index': 0, 'equals': '1000'}], signals)['ok']
    assert bounties.check_requirements([{'index': 1, 'min': 1000}], signals)['ok']
    assert not bounties.check_requirements([{'index': 1, 'min': 1001}], signals)['ok']
    assert not bounties.check_requirements([{'index': 5, 'min': 1}], signals)['ok']


def test_a_republisher_cannot_retitle_or_reprice_somebody_elses_listing():
    """Two people can hold the same bytes, and the id says they are the same
    proof. The listing still belongs to whoever published it first — otherwise
    anyone could reprice a proof out from under the people who bought it."""
    proof, vkey, signals = fixture('multiplier', 'g16')
    with temporary('groth16', proof, vkey, signals, author='0xfirst',
                   title='mine', price=9) as first:
        second = proofs.publish('groth16', proof, vkey, signals, author='0xsecond',
                                title='actually mine', price=0)
        assert second['id'] == first['id']
        assert second['title'] == 'mine' and second['price'] == 9
        assert second['author'] == '0xfirst'
        assert '0xsecond' in second['republished_by']
        assert [c['kind'] for c in second['checks']][-1] == 'republished'
