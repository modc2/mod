"""
Verified execution.

A run here is a claim: "this artifact, on this input, with this seed, produced
these bytes". The claim is worth nothing on its own — it may have come from a
browser tab on a machine nobody controls — and it becomes worth something the
same way it does anywhere else: somebody else did it again and got the same
answer.

    receipt      the run reduced to what must match. Its id is the SHA-256 of
                 that, so two independent runs of the same computation produce
                 the same receipt id or they do not agree, and there is no
                 third possibility.
    attestation  one party saying "I ran this and got that". Carries who,
                 where, and what they got.
    verdict      what the attestations add up to.

WHAT IS AND ISN'T HASHED
    In:  artifact id, engine, entry, input hash, seed, output hash, exit code,
         and the effect counts (how many times the module asked for the clock
         or the PRNG, and which imports had to be stubbed).
    Out: wall-clock time, venue, who ran it, when. A replay that took longer
         is still the same computation; a replay that used the clock a
         different number of times is not.

VERIFICATION IS PER COMPUTE TYPE
    Engines declare how they can be checked, and this module does what they
    declare rather than assuming everything is replayable:

    replay       run it again elsewhere and compare (wasm, js — both seeded)
    consensus    N independent runs, majority wins, because bitwise equality
                 is the wrong test (GPU kernels, containers)
    attestation  the hardware signs what it ran; nobody can replay it, which
                 is the point (TEEs)

    A verdict of 'verified' therefore means something slightly different per
    engine, and says which rule it was reached under.
"""
import time
import uuid
from typing import Any, Dict, List, Optional

from . import engines, sandbox, storage

# How many *independent* agreeing attestations make a replay-verified run.
# Two: the claim, plus somebody who wasn't the claimant. A browser run
# confirmed by this box is the ordinary case.
QUORUM = 2


def digest(value: Any) -> str:
    return storage.sha256(storage.canonical(value).encode())


def receipt(run: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a run that two honest parties must agree on, byte for byte."""
    body = {
        'artifact': run['artifact'],
        'engine': run['engine'],
        'entry': run.get('entry') or 'run',
        'input_hash': storage.sha256((run.get('input') or '').encode()),
        'seed': int(run.get('seed') or 0),
        'output_hash': storage.sha256((run.get('output') or '').encode()),
        'exit_code': run.get('exit_code'),
        'effects': run.get('effects') or {},
    }
    return {**body, 'receipt': digest(body)}


def attestation(result: Dict[str, Any], venue: str, verifier: str,
                run: Dict[str, Any]) -> Dict[str, Any]:
    """One party's account of running the job. `verifier` identifies who."""
    signed = receipt({**run, **{k: result.get(k) for k in
                                ('output', 'exit_code', 'effects')}})
    return {
        'venue': venue,
        'verifier': verifier,
        'receipt': signed['receipt'],
        'output_hash': signed['output_hash'],
        'ms': result.get('ms'),
        'ts': time.time(),
        'sandbox': sandbox.capabilities() if venue == 'server' else None,
    }


def verdict(run: Dict[str, Any]) -> Dict[str, Any]:
    """What this run's attestations add up to, under its engine's own rule."""
    engine = engines.REGISTRY.get(run.get('engine'))
    mode = engine.verify if engine else 'replay'
    seen = run.get('attestations') or []
    # One party attesting twice is one opinion, not two.
    parties = {a['verifier']: a for a in seen}
    receipts = {}
    for att in parties.values():
        receipts.setdefault(att['receipt'], []).append(att['verifier'])

    out = {'mode': mode, 'attestations': len(seen), 'independent': len(parties),
           'agree': None, 'status': 'unverified', 'receipt': run.get('receipt')}

    if not parties:
        out['why'] = 'nobody has attested to this run yet'
        return out
    if len(receipts) > 1:
        out.update(status='disputed', agree=False, receipts=receipts)
        out['why'] = ('independent runs of the same computation produced '
                      'different results — one of these runners is wrong, '
                      'or the artifact is not deterministic')
        return out

    agreed, who = next(iter(receipts.items()))
    out.update(agree=True, receipt=agreed, verifiers=who)

    if mode == 'attestation':
        # For a TEE the signature is the evidence; a second run proves nothing
        # because nobody else can produce one.
        signed = [a for a in parties.values() if a.get('evidence')]
        out['status'] = 'verified' if signed else 'claimed'
        out['why'] = ('hardware evidence accompanies this run' if signed else
                      'this engine verifies by hardware attestation, and no '
                      'signed evidence is attached yet')
        return out

    need = QUORUM if mode == 'replay' else 3
    if len(parties) >= need:
        out['status'] = 'verified'
        out['why'] = (f'{len(parties)} independent runs agree'
                      + ('' if mode == 'replay' else ' (majority of three)'))
    else:
        out['status'] = 'claimed'
        out['why'] = (f'{len(parties)} of {need} independent runs — '
                      'replay it somewhere else to verify')
    return out


# ── running and recording ────────────────────────────────────────────

def _text(value: Any) -> str:
    """An input is text. A caller who says `input=200000` means the six
    characters, not the number — and the guest must see the same thing here
    that it saw in the tab, or the receipts disagree for no reason."""
    return '' if value is None else value if isinstance(value, str) else str(value)


def record(artifact_id: str, engine: str, result: Dict[str, Any], *,
           entry: str = 'run', input: str = '', seed: int = 0,
           venue: str = 'server', runner: str = 'server',
           listing: Optional[str] = None) -> Dict[str, Any]:
    """Write a run and its first attestation to the store."""
    run_id = uuid.uuid4().hex[:16]
    input = _text(input)
    run = {
        'id': run_id,
        'artifact': artifact_id,
        'listing': listing,
        'engine': engine,
        'entry': entry or 'run',
        'input': input[:10000],
        'seed': int(seed or 0),
        'output': (result.get('output') or '')[:100000],
        'logs': (result.get('logs') or [])[:200],
        'stdout': (result.get('stdout') or '')[:10000],
        'stderr': (result.get('stderr') or '')[:10000],
        'exit_code': result.get('exit_code'),
        'effects': result.get('effects') or {},
        'ms': result.get('ms'),
        'venue': venue,
        'runner': runner,
        'created': time.time(),
    }
    run.update({k: v for k, v in receipt(run).items() if k in ('receipt', 'output_hash', 'input_hash')})
    run['attestations'] = [attestation(result, venue, runner, run)]
    run['verdict'] = verdict(run)
    storage.put_record('runs', run_id, run)
    return run


def run_here(artifact_id: str, engine: str, *, entry: str = 'run',
             input: str = '', seed: int = 0, limits: Optional[dict] = None,
             runner: str = 'server', listing: Optional[str] = None) -> Dict[str, Any]:
    """Execute on this box and record it. The server venue's front door."""
    data = storage.get_artifact(artifact_id)
    if data is None:
        raise ValueError(f'no artifact {artifact_id[:12]} in the store')
    input = _text(input)
    result = engines.execute(engine, data, entry=entry, input=input,
                             seed=seed, limits=limits)
    return record(artifact_id, engine, result, entry=entry, input=input,
                  seed=seed, venue='server', runner=runner, listing=listing)


def claim(artifact_id: str, engine: str, result: Dict[str, Any], *,
          entry: str = 'run', input: str = '', seed: int = 0,
          runner: str = 'browser') -> Dict[str, Any]:
    """Record a run somebody else performed — a browser tab, or another box.

    Deliberately trusting about the *bytes* and deliberately untrusting about
    the *claim*: it is stored as-is and starts at 'claimed'. Nothing spends
    anything on the strength of one of these until a replay agrees with it.
    """
    return record(artifact_id, engine, result, entry=entry, input=input,
                  seed=seed, venue=result.get('venue') or 'browser',
                  runner=runner)


def verify(run_id: str, verifier: str = 'server') -> Dict[str, Any]:
    """Replay a recorded run on this box and attest to what came out.

    This is the whole mechanism. The replay reads the run's own inputs from the
    store — artifact, entry, seed, input — so a claimant cannot smuggle a
    different job into its own verification.
    """
    run = storage.get_record('runs', run_id)
    if not run:
        raise ValueError(f'no run {run_id}')
    engine = engines.REGISTRY.get(run['engine'])
    if engine and engine.verify != 'replay':
        raise ValueError(
            f"the '{run['engine']}' compute type verifies by {engine.verify}, "
            'not by replay — re-running it would prove nothing')

    data = storage.get_artifact(run['artifact'])
    if data is None:
        raise ValueError(f"the artifact this run used is gone: {run['artifact'][:12]}")
    result = engines.execute(run['engine'], data, entry=run['entry'],
                             input=run['input'], seed=run['seed'])
    att = attestation(result, 'server', verifier, run)

    run.setdefault('attestations', [])
    # A verifier re-attesting replaces its own earlier word rather than
    # stacking a second vote onto the same opinion.
    run['attestations'] = [a for a in run['attestations']
                           if a['verifier'] != verifier] + [att]
    run['verdict'] = verdict(run)
    if att['receipt'] != run.get('receipt'):
        run['replay_output'] = (result.get('output') or '')[:100000]
    storage.put_record('runs', run_id, run)
    return run


def runs(limit: int = 50, artifact: str = None, listing: str = None,
         status: str = None) -> List[Dict[str, Any]]:
    out = storage.records('runs', limit=500)
    if artifact:
        out = [r for r in out if r.get('artifact') == artifact]
    if listing:
        out = [r for r in out if r.get('listing') == listing]
    if status:
        out = [r for r in out if (r.get('verdict') or {}).get('status') == status]
    return out[:limit]
