"""
wasmland's own tests.

The interesting ones are not "does it run" but "does it refuse": a forged
result must come back disputed, a stranger must not be able to run a paid
listing, a runaway loop must be killed, and a compute type that isn't
implemented must say so rather than pretend.

Everything writes under a test prefix in the store mod and is swept afterwards,
so running these does not disturb a real marketplace on the same box.
"""
import base64
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import engines, market, receipts, sandbox, storage  # noqa: E402

WASM_PACK = ROOT.parent / 'arena' / 'src' / 'examples' / 'wasm'
SORT_JS = b'''function run(input, ctx) {
  const nums = (input || '').split(/[,\\s]+/).map(Number).filter(n => !isNaN(n));
  nums.sort((a, b) => a - b);
  ctx.log(`sorted ${nums.length}`);
  return JSON.stringify({ sorted: nums, sum: nums.reduce((a, b) => a + b, 0) });
}'''


@pytest.fixture(scope='module', autouse=True)
def sandbox_prefix():
    """Keep the tests out of the real marketplace's keys."""
    original = storage.PREFIX
    storage.PREFIX = 'wasmland-test'
    yield
    import shutil
    root = Path(str(getattr(storage.store(), 'path', ''))) / 'wasmland-test'
    if root.is_dir():
        shutil.rmtree(root)
    storage.PREFIX = original


def wasm(name: str) -> bytes:
    path = WASM_PACK / f'{name}.wasm'
    if not path.is_file():
        pytest.skip(f'{name}.wasm is not built — run arena/src/examples/build.sh')
    return path.read_bytes()


# ── reading the binary ───────────────────────────────────────────────

def test_role_is_read_from_the_bytes_not_the_uploader():
    assert engines.inspect('wasm', wasm('ttt'))['role'] == 'game'
    assert engines.inspect('wasm', wasm('hello'))['role'] == 'command'
    assert engines.inspect('wasm', wasm('bot_random'))['role'] == 'player'
    assert engines.inspect('js', SORT_JS)['role'] == 'function'


def test_a_non_wasm_upload_is_refused_clearly():
    with pytest.raises(ValueError, match='not a wasm module'):
        engines.inspect('wasm', b'this is not a wasm module at all')


def test_manifest_reports_imports_and_memory():
    manifest = engines.inspect('wasm', wasm('hello'))
    assert manifest['memory']['initial_pages'] > 0
    assert any(i['module'].startswith('wasi') for i in manifest['imports'])


def test_unknown_compute_type_names_what_exists():
    with pytest.raises(ValueError, match='unknown compute type'):
        engines.get('quantum')


def test_planned_compute_types_refuse_rather_than_pretend():
    for planned in ('python', 'container', 'tee', 'gpu'):
        engine = engines.get(planned)
        assert engine.status == 'planned'
        assert engine.verify in ('replay', 'consensus', 'attestation')
        with pytest.raises(NotImplementedError, match='declared but not implemented'):
            engine.execute(b'x')


# ── determinism ──────────────────────────────────────────────────────

def test_same_seed_same_bytes_including_clock_and_randomness():
    source = (b'function run(i, ctx) { return JSON.stringify('
              b'[ctx.random(), Date.now(), Math.random()]) }')
    first = engines.execute('js', source, seed=42)
    second = engines.execute('js', source, seed=42)
    assert first['output'] == second['output']


def test_different_seeds_do_not_share_a_stream():
    """Neighbouring seeds must be unrelated, not one step apart."""
    source = b'function run() { return JSON.stringify([Math.random(), Math.random()]) }'
    a = json.loads(engines.execute('js', source, seed=7)['output'])
    b = json.loads(engines.execute('js', source, seed=8)['output'])
    assert not set(a) & set(b)


def test_the_guest_cannot_reach_the_outside():
    source = (b'function run() { return [typeof fetch, typeof process, '
              b'typeof require, typeof WebAssembly].join(",") }')
    out = engines.execute('js', source, seed=1)
    assert out['output'] == 'undefined,undefined,undefined,undefined'


def test_a_wasi_command_finds_no_filesystem():
    out = engines.execute('wasm', wasm('hello'), input='abc', seed=1)
    assert 'hello, world' in out['output']
    assert 'sandboxed' in out['output']


# ── the sandbox ──────────────────────────────────────────────────────

def test_an_endless_loop_is_killed():
    with pytest.raises(sandbox.RunFailed):
        sandbox.run({'engine': 'js', 'artifact': 'function run(){ while(true){} }'},
                    limits={'timeout': 6, 'cpu_seconds': 3})


def test_endless_allocation_is_killed():
    with pytest.raises(sandbox.RunFailed):
        sandbox.run({'engine': 'js', 'artifact':
                     'function run(){ const a=[]; while(true) a.push(new Array(1e6).fill(7)); }'},
                    limits={'timeout': 40, 'cpu_seconds': 30, 'memory_mb': 256})


def test_capabilities_do_not_overclaim():
    caps = sandbox.capabilities()
    # Either it is isolated, or it says plainly that it isn't.
    assert caps['network_isolated'] or caps['note']


# ── storage ──────────────────────────────────────────────────────────

def test_artifacts_are_content_addressed_and_idempotent():
    data = wasm('rps')
    first = storage.put_artifact(data, 'wasm', engines.inspect('wasm', data), 'rps.wasm')
    second = storage.put_artifact(data, 'wasm', {}, 'other-name.wasm')
    assert first['id'] == second['id'] == storage.sha256(data)
    assert second['manifest']['role'] == 'game'      # the first record stands
    assert storage.get_artifact(first['id']) == data


def test_altered_bytes_are_never_served():
    data = b'function run(){ return "one" }'
    artifact = storage.put_artifact(data, 'js', engines.inspect('js', data), 'a.js')
    # Tamper with the blob behind the store's back.
    storage.store().put_json(storage.blob_key(artifact['id']),
                             {'b64': base64.b64encode(b'function run(){ return "two" }').decode()})
    with pytest.raises(ValueError, match='altered'):
        storage.get_artifact(artifact['id'])


# ── verification ─────────────────────────────────────────────────────

def stored_js():
    return storage.put_artifact(SORT_JS, 'js', engines.inspect('js', SORT_JS), 'sort.js')['id']


def test_one_run_is_a_claim_and_two_agreeing_runs_verify_it():
    artifact = stored_js()
    run = receipts.run_here(artifact, 'js', input='3,1,2', seed=1, runner='0xalice')
    assert run['verdict']['status'] == 'claimed'
    checked = receipts.verify(run['id'], verifier='0xbob')
    assert checked['verdict']['status'] == 'verified'
    assert checked['verdict']['independent'] == 2


def test_the_same_verifier_twice_is_still_one_opinion():
    artifact = stored_js()
    run = receipts.run_here(artifact, 'js', input='1', seed=1, runner='0xalice')
    receipts.verify(run['id'], verifier='0xalice')
    again = receipts.verify(run['id'], verifier='0xalice')
    assert again['verdict']['status'] == 'claimed'
    assert again['verdict']['independent'] == 1


def test_a_forged_result_is_disputed_on_replay():
    artifact = stored_js()
    lie = receipts.claim(artifact, 'js', {'output': 'whatever I felt like', 'effects': {}},
                         input='3,1,2', seed=1, runner='0xliar')
    assert lie['verdict']['status'] == 'claimed'
    checked = receipts.verify(lie['id'], verifier='server')
    assert checked['verdict']['status'] == 'disputed'
    assert checked['replay_output'] != lie['output']


def test_a_receipt_is_the_hash_of_what_must_match():
    artifact = stored_js()
    one = receipts.run_here(artifact, 'js', input='5,4', seed=3, runner='a')
    two = receipts.run_here(artifact, 'js', input='5,4', seed=3, runner='b')
    three = receipts.run_here(artifact, 'js', input='5,4', seed=4, runner='c')
    assert one['receipt'] == two['receipt']          # same computation
    assert one['receipt'] != three['receipt']        # different seed


def test_a_numeric_input_is_the_same_computation_as_its_text():
    """`m wasmland/run input=200000` arrives as an int. The guest must see the
    characters either way, or the CLI and the tab disagree about a run they
    both describe identically."""
    artifact = stored_js()
    typed = receipts.run_here(artifact, 'js', input='5,4', seed=1, runner='a')
    numeric = receipts.run_here(artifact, 'js', input=54, seed=1, runner='b')
    assert numeric['input'] == '54'
    assert receipts.run_here(artifact, 'js', input='54', seed=1,
                             runner='c')['receipt'] == numeric['receipt']
    assert numeric['receipt'] != typed['receipt']


def test_wall_clock_is_not_part_of_the_receipt():
    artifact = stored_js()
    one = receipts.run_here(artifact, 'js', input='9', seed=1, runner='a')
    two = receipts.run_here(artifact, 'js', input='9', seed=1, runner='b')
    assert one['receipt'] == two['receipt']
    assert 'ms' not in receipts.receipt(one)


# ── the market ───────────────────────────────────────────────────────

def test_a_buyer_without_credits_cannot_run_a_paid_listing():
    artifact = stored_js()
    listing = market.publish(artifact, '0xseller', title='Paid', price=5)
    with pytest.raises(market.MarketError, match='credits needed'):
        market.charge('0xpoor', listing['id'])
    assert not market.entitled('0xpoor', listing['id'])


def test_credits_move_from_buyer_to_seller_once():
    artifact = stored_js()
    listing = market.publish(artifact, '0xseller2', title='Paid twice', price=4)
    market.grant('0xbuyer2', 10)
    first = market.charge('0xbuyer2', listing['id'])
    second = market.charge('0xbuyer2', listing['id'])
    assert first['charged'] == 4
    assert second['charged'] == 0                    # entitlement, not a meter
    assert market.account('0xbuyer2')['credits'] == 6
    assert market.account('0xseller2')['earned'] == 4


def test_only_the_seller_can_delist():
    artifact = stored_js()
    listing = market.publish(artifact, '0xowner', title='Mine')
    with pytest.raises(market.MarketError, match='belongs to'):
        market.unpublish(listing['id'], '0xsomebodyelse')
    assert market.unpublish(listing['id'], '0xowner')['artifact_kept'] == artifact


def test_a_free_listing_is_open_to_everyone():
    artifact = stored_js()
    listing = market.publish(artifact, '0xseller3', title='Free', price=0)
    assert market.entitled('0xanyone', listing['id'])


# ── games ────────────────────────────────────────────────────────────

def test_a_game_is_recognised_and_a_command_is_not():
    from src import games as bridge
    game = storage.put_artifact(wasm('ttt'), 'wasm', engines.inspect('wasm', wasm('ttt')), 'ttt.wasm')
    command = storage.put_artifact(wasm('hello'), 'wasm', engines.inspect('wasm', wasm('hello')), 'hello.wasm')
    assert bridge.is_game(game)
    assert not bridge.is_game(command)
    with pytest.raises(ValueError, match='not a game'):
        bridge.send(command['id'])
