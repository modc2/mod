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

    gen = ChallengeGenerator(llm_backend='openrouter')
    types_tested = []
    for ctype in [ChallengeType.OPEN, ChallengeType.VERTICAL, ChallengeType.CONTRARIAN]:
        c = gen.generate(epoch=1, challenge_type=ctype)
        assert c.prompt, f"Challenge {ctype} should have a prompt"
        assert c.id, f"Challenge {ctype} should have an id"
        types_tested.append(ctype.value)

    return _result('challenge_gen', True, f'types={types_tested}', time.time() - t0)


def test_backend_load(backend='chutes'):
    """Test that a backend module loads and has a forward() method."""
    import mod as m
    t0 = time.time()

    try:
        llm = m.mod(backend)()
        assert hasattr(llm, 'forward'), f"{backend} module has no forward()"
        return _result(f'backend_load:{backend}', True,
                       f'{backend} loaded, forward() available', time.time() - t0)
    except Exception as e:
        return _result(f'backend_load:{backend}', False, str(e), time.time() - t0)


def test_backend_forward(backend='chutes', model=None):
    """Test a live LLM call through a backend."""
    import mod as m
    t0 = time.time()

    try:
        llm = m.mod(backend)()
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


def test_epoch_local(backend='chutes', n_miners=2):
    """Test a full local epoch: miners generate, validator scores, weights computed."""
    from neurons.miner import BitevoMiner
    from neurons.validator import BitevoValidator
    t0 = time.time()

    try:
        val = BitevoValidator(backend=backend, local=True, tempo=0)
        for i in range(n_miners):
            miner = BitevoMiner(backend=backend, uid=i, local=True)
            val.add_miner(miner)

        result = val.epoch()
        assert result.epoch == 1
        assert len(result.responses) == n_miners
        assert len(result.scores) > 0
        assert len(result.weights) > 0

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

    try:
        val = BitevoValidator(backend='openrouter', local=True, tempo=0)
        backends = ['openrouter', 'venice', 'chutes']
        for i, b in enumerate(backends):
            try:
                miner = BitevoMiner(backend=b, uid=i, local=True)
                val.add_miner(miner)
            except Exception as e:
                print(f"  skipping {b}: {e}")

        if len(val._local_miners) < 2:
            return _result('multi_backend_epoch', False,
                           f'only {len(val._local_miners)} backends loaded')

        result = val.epoch()
        backend_models = [r.model_used for r in result.responses]
        return _result('multi_backend_epoch', True,
                       f'models={backend_models}, weights={result.weights}',
                       time.time() - t0)
    except Exception as e:
        return _result('multi_backend_epoch', False,
                       f'{e}\n{traceback.format_exc()}', time.time() - t0)


# ── Runner ────────────────────────────────────────────────────────

ALL_BACKENDS = ['openrouter', 'venice', 'chutes']

QUICK_TESTS = [test_schemas, test_scoring, test_challenge_gen]

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
