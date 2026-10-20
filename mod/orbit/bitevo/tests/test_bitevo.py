"""
Bitevo network tests — verify backends, miner forward, validator scoring, epochs.

Usage (Python):
    from tests.test_bitevo import run_tests
    run_tests()                                    # quick smoke tests
    run_tests(backends=['chutes'])                 # test specific backend
    run_tests(full=True)                           # full test including live LLM calls

Usage (CLI):
    m bitevo/test
    m bitevo/test backends='["chutes"]'
    m bitevo/test full=true
"""

import sys
import time
import traceback
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
if str(DIR) not in sys.path:
    sys.path.insert(0, str(DIR))


def _result(name, passed, detail=None, elapsed=None):
    return {
        'test': name,
        'passed': passed,
        'detail': detail or ('ok' if passed else 'FAIL'),
        'elapsed': round(elapsed, 2) if elapsed is not None else None,
    }


def test_schemas():
    """Test that all Pydantic schemas instantiate and serialize correctly."""
    from core.schemas import (
        Challenge, ChallengeType, StartupPitch, MinerResponse,
        ValidatorScore, ScoreBreakdown, EpochResult,
    )
    t0 = time.time()

    c = Challenge(prompt="Test challenge", type=ChallengeType.OPEN, epoch=1)
    assert c.id, "Challenge should auto-generate id"
    assert c.model_dump()

    p = StartupPitch(company_name="TestCo", one_liner="We test things")
    assert p.model_dump()

    bd = ScoreBreakdown(novelty=4.0, feasibility=3.5, market_size=4.0,
                        clarity=3.0, defensibility=3.5, traction_signal=2.0)
    assert bd.model_dump()

    mr = MinerResponse(miner_uid=0, challenge_id=c.id, pitch=p)
    assert mr.model_dump()

    vs = ValidatorScore(miner_uid=0, challenge_id=c.id, breakdown=bd,
                        composite_score=3.5, feedback="Good idea")
    assert vs.model_dump()

    er = EpochResult(epoch=1, challenge=c, responses=[mr], scores=[vs],
                     weights={"0": 1.0})
    assert er.model_dump()

    return _result('schemas', True, f'{6} schemas validated', time.time() - t0)


def test_scoring():
    """Test composite scoring and incentive mechanism."""
    from core.schemas import ScoreBreakdown
    from core.scoring import composite_score, normalize_scores, IncentiveMechanism
    t0 = time.time()

    bd = ScoreBreakdown(novelty=5.0, feasibility=4.0, market_size=4.0,
                        clarity=3.0, defensibility=4.0, traction_signal=3.0)
    score = composite_score(bd)
    assert 0 < score <= 5.0, f"composite_score out of range: {score}"

    normed = normalize_scores([3.0, 4.0, 5.0])
    assert len(normed) == 3
    assert abs(sum(normed) - 1.0) < 0.01, f"normalized scores should sum to ~1: {normed}"

    im = IncentiveMechanism()
    im.update(0, 4.0)
    im.update(1, 3.0)
    weights = im.compute_weights({0: 4.0, 1: 3.0})
    assert len(weights) > 0, "should produce weights"

    lb = im.get_leaderboard()
    assert len(lb) == 2

    return _result('scoring', True, f'composite={score:.2f}, weights={weights}', time.time() - t0)


def test_challenge_gen():
    """Test challenge generator produces valid challenges."""
    from core.challenge import ChallengeGenerator
    from core.schemas import ChallengeType
    t0 = time.time()

    gen = ChallengeGenerator(llm_backend='claude')
    types_tested = []
    for ctype in [ChallengeType.OPEN, ChallengeType.VERTICAL, ChallengeType.CONTRARIAN]:
        c = gen.generate(epoch=1, challenge_type=ctype)
        assert c.prompt, f"Challenge {ctype} should have a prompt"
        assert c.id, f"Challenge {ctype} should have an id"
        types_tested.append(ctype.value)

    return _result('challenge_gen', True, f'types={types_tested}', time.time() - t0)


def test_evolution():
    """Evolution engine: fitness improves on the held-out future window."""
    from core.evolve import EvolutionEngine, make_dataset, DATASETS
    t0 = time.time()

    for name in DATASETS:
        s = make_dataset(name, n=120, seed=1)
        lengths = {len(v) for v in s.values()}
        assert len(lengths) == 1 and lengths.pop() == 120, f"{name} series misaligned"

    eng = EvolutionEngine(make_dataset('harmonics', 300, seed=3), dataset='harmonics',
                          population=24, horizon=8, seed=42)
    eng.run(10)
    first, last = eng.history[0], eng.history[-1]
    assert last['best'] >= first['best'], \
        f"elitism should make best fitness monotone: {first['best']} -> {last['best']}"
    assert last['best'] > first['best'], "10 generations should improve best fitness"
    lb = eng.leaderboard(top=5)
    assert lb[0]['fitness'] is not None and lb[0]['weight'] > 0
    assert abs(sum(e['weight'] for e in lb) - 1.0) < 0.01, "weights should sum to ~1"
    return _result('evolution', True,
                   f"best {first['best']} -> {last['best']} over {eng.generation} gens",
                   time.time() - t0)


def test_evolution_forecast():
    """Best genome produces a full multivariate forecast of the future."""
    from core.evolve import EvolutionEngine, make_dataset
    t0 = time.time()

    eng = EvolutionEngine(make_dataset('coupled', 240, seed=5), dataset='coupled',
                          population=16, horizon=6, seed=7)
    eng.run(5)
    fc = eng.forecast(horizon=9, tail=30)
    assert set(fc['variables']) == {'driver', 'follower', 'stress'}
    for k, v in fc['variables'].items():
        assert len(v['forecast']) == 9, f"{k} forecast wrong length"
        assert len(v['history']) == 30
        assert all(isinstance(x, float) for x in v['forecast'])
    return _result('evolution_forecast', True,
                   f"genome {fc['genome']} lag={fc['lag']} h={fc['horizon']}",
                   time.time() - t0)


def test_evolution_persistence():
    """Save/load round-trip resumes the same population and generation count."""
    import tempfile, os
    from core.evolve import EvolutionEngine, make_dataset
    t0 = time.time()

    eng = EvolutionEngine(make_dataset('regimes', 200, seed=2), dataset='regimes',
                          population=12, horizon=5, seed=11)
    eng.run(3)
    path = os.path.join(tempfile.mkdtemp(), 'evo.json')
    eng.save(path)
    eng2 = EvolutionEngine.load(path)
    assert eng2.generation == eng.generation
    assert len(eng2.population) == len(eng.population)
    assert eng2.leaderboard(top=1)[0]['id'] == eng.leaderboard(top=1)[0]['id']
    eng2.run(2)
    assert eng2.generation == eng.generation + 2, "resumed engine should continue counting"
    return _result('evolution_persistence', True,
                   f"gen {eng.generation} saved, resumed to {eng2.generation}",
                   time.time() - t0)


def test_backend_load(backend='chutes'):
    """Test that a backend module loads and has a forward() method."""
    from core.llm import get_llm
    t0 = time.time()

    try:
        llm = get_llm(backend)
        assert hasattr(llm, 'forward'), f"{backend} module has no forward()"
        return _result(f'backend_load:{backend}', True,
                       f'{backend} loaded, forward() available', time.time() - t0)
    except Exception as e:
        return _result(f'backend_load:{backend}', False, str(e), time.time() - t0)


def test_backend_forward(backend='chutes', model=None):
    """Test a live LLM call through a backend."""
    from core.llm import get_llm
    t0 = time.time()

    try:
        llm = get_llm(backend)
        kwargs = {'stream': False}
        if model:
            kwargs['model'] = model

        resp = llm.forward(
            "Say 'bitevo test ok' and nothing else.",
            system_prompt="You are a test assistant. Reply with exactly what is asked.",
            **kwargs,
        )
        assert resp and len(str(resp)) > 0, "Empty response"
        return _result(f'backend_forward:{backend}', True,
                       f'response={str(resp)[:80]}', time.time() - t0)
    except Exception as e:
        return _result(f'backend_forward:{backend}', False, str(e), time.time() - t0)


def test_miner_forward(backend='chutes'):
    """Test miner pitch generation with a given backend."""
    from neurons.miner import BitevoMiner
    from core.schemas import Challenge
    t0 = time.time()

    try:
        miner = BitevoMiner(backend=backend, uid=0, local=True)
        challenge = Challenge(
            prompt="Pitch a startup that uses decentralized GPU compute for AI inference.",
            epoch=1,
        )
        resp = miner.forward(challenge)
        assert resp.pitch.company_name, "Miner should generate a company name"
        assert resp.pitch.problem, "Miner should describe a problem"
        return _result(f'miner_forward:{backend}', True,
                       f'company={resp.pitch.company_name}, model={resp.model_used}',
                       time.time() - t0)
    except Exception as e:
        return _result(f'miner_forward:{backend}', False,
                       f'{e}\n{traceback.format_exc()}', time.time() - t0)


def test_validator_score(backend='chutes'):
    """Test validator scoring of a mock pitch."""
    from neurons.validator import BitevoValidator
    from core.schemas import Challenge, StartupPitch, MinerResponse
    t0 = time.time()

    try:
        val = BitevoValidator(backend=backend, local=True)
        challenge = Challenge(prompt="Pitch a startup in decentralized compute.", epoch=1)
        pitch = StartupPitch(
            company_name="ChutesNet",
            one_liner="Decentralized GPU marketplace for AI inference",
            problem="GPU compute is expensive and centralized",
            solution="P2P GPU sharing network with crypto incentives",
            market="$50B cloud compute market",
            traction="500 GPUs onboarded in beta",
            business_model="Take rate on compute transactions",
            team="Ex-Bittensor core devs",
            defensibility="Network effects + GPU supply lock-in",
            ask="$2M seed to scale GPU onboarding",
        )
        resp = MinerResponse(miner_uid=0, challenge_id=challenge.id, pitch=pitch)
        score = val._score_response(challenge, resp)
        assert 0 < score.composite_score <= 5.0, f"Score out of range: {score.composite_score}"
        assert score.feedback, "Should have feedback"
        return _result(f'validator_score:{backend}', True,
                       f'score={score.composite_score:.2f}, feedback={score.feedback[:60]}',
                       time.time() - t0)
    except Exception as e:
        return _result(f'validator_score:{backend}', False,
                       f'{e}\n{traceback.format_exc()}', time.time() - t0)


def test_epoch_local(backend='chutes', n_miners=3):
    """Test a full local epoch: miners generate, validator scores, weights computed."""
    import tempfile
    from neurons.miner import BitevoMiner
    from neurons.validator import BitevoValidator
    t0 = time.time()

    try:
        val = BitevoValidator(backend=backend, local=True, tempo=0,
                              storage_path=tempfile.mkdtemp(prefix='bitevo-test-'))
        for i in range(n_miners):
            miner = BitevoMiner(backend=backend, uid=i, local=True)
            val.add_miner(miner)

        result = val.epoch()
        assert result.epoch == 1
        assert len(result.responses) == n_miners
        assert len(result.scores) > 0
        assert len(result.weights) > 0
        assert result.zuma['audit']['valid'], result.zuma['audit']

        lb = val.leaderboard()
        return _result(f'epoch_local:{backend}', True,
                       f'miners={n_miners}, scores={len(result.scores)}, '
                       f'weights={result.weights}, leaderboard={lb}',
                       time.time() - t0)
    except Exception as e:
        return _result(f'epoch_local:{backend}', False,
                       f'{e}\n{traceback.format_exc()}', time.time() - t0)


def test_multi_backend_epoch():
    """Test a mixed-backend epoch with miners on different backends."""
    from neurons.miner import BitevoMiner
    from neurons.validator import BitevoValidator
    t0 = time.time()

    import tempfile
    try:
        val = BitevoValidator(backend='claude', local=True, tempo=0,
                              storage_path=tempfile.mkdtemp(prefix='bitevo-test-'))
        backends = ['openrouter', 'venice', 'chutes']
        for i, b in enumerate(backends):
            try:
                miner = BitevoMiner(backend=b, uid=i, local=True)
                val.add_miner(miner)
            except Exception as e:
                print(f"  skipping {b}: {e}")

        if len(val._local_miners) < 3:
            return _result('multi_backend_epoch', False,
                           f'only {len(val._local_miners)} backends loaded')

        result = val.epoch()
        return _result('multi_backend_epoch', True,
                       f'tags={len(result.responses)}, weights={result.weights}',
                       time.time() - t0)
    except Exception as e:
        return _result('multi_backend_epoch', False,
                       f'{e}\n{traceback.format_exc()}', time.time() - t0)


# ── zuma protocol (anonymous miners/validators) ───────────────────

_PITCH = {"company_name": "Co{n}", "one_liner": "does {n}", "problem": "p",
          "solution": "s", "market": "m", "traction": "t",
          "business_model": "b", "team": "t", "defensibility": "d", "ask": "a"}


class _MockLLM:
    def __init__(self, reply):
        self.reply = reply
        self.n = 0

    def forward(self, *a, **k):
        self.n += 1
        return self.reply(self.n) if callable(self.reply) else self.reply


def test_zuma_sign_verify():
    """LSAG sign/verify, tag linkability within a scope, rejection of tampering."""
    from core import zuma as Z
    t0 = time.time()
    xs = [11, 22, 33, 44]
    ring = [Z.public_key(x) for x in xs]
    sc = Z.mine_scope(Z.felt('bitevo-test'), 1)
    a = Z.sign(ring, 33, sc, 7)
    b = Z.sign(ring, 33, sc, 8)
    assert Z.verify(sc, 7, a) and Z.verify(sc, 8, b)
    assert a['tag'] == b['tag'], 'same key + scope must give the same tag'
    assert not Z.verify(sc, 8, a), 'message swap must fail'
    assert not Z.verify(Z.mine_scope(Z.felt('bitevo-test'), 2), 7, a), 'scope swap must fail'
    other = Z.sign(ring, 33, Z.mine_scope(Z.felt('bitevo-test'), 2), 7)
    assert other['tag'] != a['tag'], 'tags must be unlinkable across epochs'
    try:
        Z.sign(ring, 55, sc, 7)
        raise AssertionError('non-member signed')
    except ValueError:
        pass
    return _result('zuma_sign_verify', True, f'ring 4, tag {a["tag"][:14]}…',
                   time.time() - t0)


def test_zuma_epoch():
    """Full anonymous epoch with a mock LLM: submit, score, close, claim, audit,
    and the contract's rejections (double submit, double score, non-member)."""
    import json as _json
    import tempfile
    from core import zuma as Z
    from neurons.miner import BitevoMiner
    from neurons.validator import BitevoValidator
    t0 = time.time()
    val = BitevoValidator(backend='claude', local=True, tempo=0,
                          storage_path=tempfile.mkdtemp(prefix='bitevo-zuma-'))
    scores = [{"novelty": 5, "feasibility": 4, "market_size": 4, "clarity": 3,
               "defensibility": 4, "traction_signal": 3, "feedback": "ok"},
              {"novelty": 2, "feasibility": 2, "market_size": 2, "clarity": 2,
               "defensibility": 2, "traction_signal": 2, "feedback": "meh"},
              {"novelty": 3, "feasibility": 3, "market_size": 3, "clarity": 3,
               "defensibility": 3, "traction_signal": 3, "feedback": "fine"}]
    val._llm = _MockLLM(lambda n: _json.dumps(scores[(n - 1) % 3]))
    miners = []
    for i in range(3):
        m = BitevoMiner(backend='claude', uid=i, local=True)
        m._llm = _MockLLM(_json.dumps({k: v.format(n=i) for k, v in _PITCH.items()}))
        val.add_miner(m)
        miners.append(m)
    r = val.epoch()
    z = r.zuma
    assert z['submissions'] == 3 and not z['rejected'], z
    assert all(resp.miner_uid == -1 and not resp.miner_hotkey for resp in r.responses)
    assert len(r.scores) == 3 and all(s.nullifier for s in r.scores)
    assert len(z['claims']) == 3, z['claims']
    assert sum(c['amount'] for c in z['claims']) <= val.zuma.emission
    assert z['audit']['valid'] and z['audit']['signatures'] == 9, z['audit']
    assert set(r.weights) == {resp.tag for resp in r.responses}
    lb = {e['uid'] for e in val.leaderboard()}
    assert lb == {'miner-0', 'miner-1', 'miner-2'}, lb

    sub = val.zuma
    ep = r.epoch
    # double claim
    try:
        miners[0].zuma_claim(sub, ep)
        raise AssertionError('double claim accepted')
    except ValueError as e:
        assert 'already claimed' in str(e)
    # a fresh epoch: double submit + double score are rejected
    ep2 = ep + 1
    resp = miners[1].zuma_submit(sub, ep2, r.responses[0])
    sub.submit(ep2, int(resp.work, 16), resp.zuma_sig)
    try:
        sub.submit(ep2, int(resp.work, 16), resp.zuma_sig)
        raise AssertionError('double submit accepted')
    except ValueError as e:
        assert 'already submitted' in str(e)
    sig = Z.sign(sub.ring('validator'), sub.secret(val.zuma_key),
                 Z.score_scope(sub.subnet, ep2, int(resp.tag, 16)), 50)
    sub.score(ep2, resp.tag, 50, sig)
    try:
        sub.score(ep2, resp.tag, 50, sig)
        raise AssertionError('double score accepted')
    except ValueError as e:
        assert 'already scored' in str(e)
    # a validator cannot sign as a miner (wrong role ring)
    try:
        sub.submit(ep2, 1, Z.sign(sub.ring('validator'), sub.secret(val.zuma_key),
                                  Z.mine_scope(sub.subnet, ep2), 1))
        raise AssertionError('validator ring accepted as miner ring')
    except ValueError as e:
        assert 'not a registered miner' in str(e)
    return _result('zuma_epoch', True,
                   f"3 tags, 9 sigs audited, claims={[c['amount'] for c in z['claims']]}",
                   time.time() - t0)


def test_zuma_wire():
    """A bitevo signature verifies in zuma's own Cairo verifier (same scheme,
    same scopes, same keys). Skipped when orbit/ztensor's toolchain is absent."""
    import importlib.util
    import os
    from core import zuma as Z
    t0 = time.time()
    zmod = Z.ZUMA_DIR / 'src' / 'mod.py'
    if not zmod.exists() or not os.path.exists(os.path.expanduser('~/.local/bin/scarb')):
        return _result('zuma_wire', True, 'skipped (no orbit/ztensor toolchain)')
    spec = importlib.util.spec_from_file_location('zuma_mod_wire', zmod)
    zm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(zm)
    z = zm.Mod()
    xs = [101, 202, 303]
    ring = [Z.public_key(x) for x in xs]
    subnet = Z.felt('bitevo')
    sc = Z.mine_scope(subnet, 5)
    sig = Z.sign(ring, 202, sc, 4242)
    assert z.scope('mine', subnet, 5) == sc, 'mine scope differs'
    assert z.scope('score', subnet, 5, int(sig['tag'], 16)) == \
        Z.score_scope(subnet, 5, int(sig['tag'], 16)), 'score scope differs'
    assert z.verify(sc, 4242, sig)['valid'], 'zuma Cairo verifier rejected it'
    assert not z.verify(sc, 4243, sig)['valid']
    csig = zm.SIGNER.prove([4, 303, sc])
    assert hex(csig[2]) == hex(Z.key_id(Z.key_image(303, sc))), 'tag differs'
    return _result('zuma_wire', True, 'Cairo lsag::verify accepts bitevo sigs',
                   time.time() - t0)


# ── Runner ────────────────────────────────────────────────────────

ALL_BACKENDS = ['claude', 'openrouter', 'venice', 'chutes']

QUICK_TESTS = [test_schemas, test_scoring, test_challenge_gen,
               test_evolution, test_evolution_forecast, test_evolution_persistence,
               test_zuma_sign_verify, test_zuma_epoch, test_zuma_wire]

def run_tests(backends=None, full=False):
    """Run bitevo test suite.

    Args:
        backends: List of backends to test (default: all).
                  Pass ['chutes'] to only test chutes.
        full: If True, run live LLM tests (miner forward, validator score, epoch).

    Returns:
        Dict with results list and summary.
    """
    if isinstance(backends, str):
        import json as _json
        try:
            backends = _json.loads(backends)
        except Exception:
            backends = [backends]
    backends = backends or ALL_BACKENDS

    results = []
    print("=" * 60)
    print("  BITEVO TEST SUITE")
    print("=" * 60)

    # Quick tests (no LLM calls)
    print("\n-- Unit tests --")
    for fn in QUICK_TESTS:
        try:
            r = fn()
        except Exception as e:
            r = _result(fn.__name__, False, str(e))
        results.append(r)
        status = 'PASS' if r['passed'] else 'FAIL'
        print(f"  [{status}] {r['test']}: {r['detail']}")

    # Backend load tests
    print("\n-- Backend load --")
    for b in backends:
        r = test_backend_load(b)
        results.append(r)
        status = 'PASS' if r['passed'] else 'FAIL'
        print(f"  [{status}] {r['test']}: {r['detail']}")

    if full:
        # Live backend forward tests
        print("\n-- Backend forward (live LLM) --")
        for b in backends:
            r = test_backend_forward(b)
            results.append(r)
            status = 'PASS' if r['passed'] else 'FAIL'
            print(f"  [{status}] {r['test']}: {r['detail']}")

        # Miner forward tests
        print("\n-- Miner forward --")
        for b in backends:
            r = test_miner_forward(b)
            results.append(r)
            status = 'PASS' if r['passed'] else 'FAIL'
            print(f"  [{status}] {r['test']}: {r['detail']}")

        # Validator score tests
        print("\n-- Validator score --")
        for b in backends:
            r = test_validator_score(b)
            results.append(r)
            status = 'PASS' if r['passed'] else 'FAIL'
            print(f"  [{status}] {r['test']}: {r['detail']}")

        # Epoch tests
        print("\n-- Full epoch --")
        for b in backends:
            r = test_epoch_local(b, n_miners=2)
            results.append(r)
            status = 'PASS' if r['passed'] else 'FAIL'
            print(f"  [{status}] {r['test']}: {r['detail']}")

        # Multi-backend epoch
        print("\n-- Multi-backend epoch --")
        r = test_multi_backend_epoch()
        results.append(r)
        status = 'PASS' if r['passed'] else 'FAIL'
        print(f"  [{status}] {r['test']}: {r['detail']}")

    passed = sum(1 for r in results if r['passed'])
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  {passed}/{total} passed")
    print(f"{'=' * 60}")

    return {
        'passed': passed,
        'total': total,
        'all_passed': passed == total,
        'results': results,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Bitevo test suite')
    parser.add_argument('--backends', nargs='*', default=None)
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    run_tests(backends=args.backends, full=args.full)
