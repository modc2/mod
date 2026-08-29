"""Prediction scoring — a normalized dollar error in, a 0..1 score out.

Every model answers the same question: how far off was the prediction, in
dollars, relative to the price it was predicting?

    normalized_error = |predicted − actual| / actual

That ratio is the *only* input to a model, which is what makes the score
comparable across assets — being $50 off on BTC at $64,000 and $0.0003 off on
AERO at $0.41 are both a 0.08% miss and score identically. Models differ only
in how fast the score decays once the error passes `tolerance`.

Adding a model is one function plus one registry entry — nothing else in the
protocol knows the names.
"""

import math
from typing import Callable, Dict


# ── Models ───────────────────────────────────────────────────────────
# Each takes (normalized_error, tolerance) and returns a score in [0, 1].
# tolerance is the error scale: the point where the curve has decayed to a
# characteristic fraction (1/2 for l2, 0 for linear, 1/e for exponential).

def _l2(nerr: float, tol: float) -> float:
    """Inverse-square decay — 1/(1+(err/tol)²). Never quite reaches zero, so a
    wild miss still scores something. `tolerance=1` reproduces ScoreL2.sol."""
    return 1.0 / (1.0 + (nerr / tol) ** 2)


def _linear(nerr: float, tol: float) -> float:
    """Straight ramp to zero at `tolerance`. Miss by more than tol → 0."""
    return max(0.0, 1.0 - nerr / tol)


def _exponential(nerr: float, tol: float) -> float:
    """Exponential decay, 1/e at `tolerance`. Punishes the tail harder than l2."""
    return math.exp(-nerr / tol)


def _threshold(nerr: float, tol: float) -> float:
    """All or nothing — inside `tolerance` pays full, outside pays zero."""
    return 1.0 if nerr <= tol else 0.0


MODELS: Dict[str, Callable[[float, float], float]] = {
    'l2': _l2,
    'linear': _linear,
    'exponential': _exponential,
    'threshold': _threshold,
}


# ── Parameters ───────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    'model': 'l2',          # which curve in MODELS
    'tolerance': 0.02,      # normalized error scale (0.02 = a 2% miss)
    'multiplier': 3.0,      # payout = burn × multiplier × score
    'horizon': 86400,       # default seconds until resolution (1 day)
    'min_burn': 1.0,        # smallest PREFI burn accepted
    'free_per_day': 3,      # free calls an address may place per 24h (0 = off)
    'free_payout': 1.0,     # PREFI a *perfect* free call mints; scaled by score
}

MIN_HORIZON = 3600          # 1 hour
MAX_HORIZON = 2592000       # 30 days
FREE_WINDOW = 86400         # rolling window the free allowance is counted over


def describe_models() -> Dict[str, str]:
    """Model name → what its curve does. Drives the picker in the UI."""
    return {name: (fn.__doc__ or '').strip() for name, fn in MODELS.items()}


def validate(params: Dict) -> Dict:
    """Merge over the defaults and bounds-check. Raises ValueError on junk."""
    merged = {**DEFAULT_PARAMS, **{k: v for k, v in (params or {}).items()
                                   if k in DEFAULT_PARAMS}}

    if merged['model'] not in MODELS:
        raise ValueError(f"unknown model '{merged['model']}' — have {sorted(MODELS)}")

    merged['tolerance'] = float(merged['tolerance'])
    if merged['tolerance'] <= 0:
        raise ValueError('tolerance must be > 0')

    merged['multiplier'] = float(merged['multiplier'])
    if merged['multiplier'] < 0:
        raise ValueError('multiplier must be >= 0')

    merged['horizon'] = int(merged['horizon'])
    if not MIN_HORIZON <= merged['horizon'] <= MAX_HORIZON:
        raise ValueError(f'horizon must be {MIN_HORIZON}..{MAX_HORIZON} seconds')

    merged['min_burn'] = float(merged['min_burn'])
    if merged['min_burn'] < 0:
        raise ValueError('min_burn must be >= 0')

    merged['free_per_day'] = int(merged['free_per_day'])
    if merged['free_per_day'] < 0:
        raise ValueError('free_per_day must be >= 0')

    merged['free_payout'] = float(merged['free_payout'])
    if merged['free_payout'] < 0:
        raise ValueError('free_payout must be >= 0')

    return merged


# ── Scoring ──────────────────────────────────────────────────────────

def normalized_error(predicted: float, actual: float) -> float:
    """Dollar miss as a fraction of the actual price. 0.01 == 1% off."""
    if actual <= 0:
        return float('inf')
    return abs(predicted - actual) / actual


def score(predicted: float, actual: float, params: Dict = None) -> Dict:
    """Score one prediction. Returns the error terms alongside the score so a
    caller can show its work rather than just a number."""
    p = validate(params or {})
    nerr = normalized_error(predicted, actual)
    value = 0.0 if math.isinf(nerr) else MODELS[p['model']](nerr, p['tolerance'])

    return {
        'model': p['model'],
        'tolerance': p['tolerance'],
        'abs_error': round(abs(predicted - actual), 8),
        'normalized_error': None if math.isinf(nerr) else round(nerr, 8),
        'score': round(min(1.0, max(0.0, value)), 8),
    }


def payout(burn: float, score_value: float, params: Dict = None) -> float:
    """PREFI minted back for a scored prediction. A perfect call returns
    `multiplier`× the burn; a total miss returns nothing and the burn is gone."""
    p = validate(params or {})
    return round(burn * p['multiplier'] * score_value, 6)


def free_mint(score_value: float, params: Dict = None) -> float:
    """PREFI minted for a *free* call — there is no burn to scale, so the whole
    payout is `free_payout` × score. It is the only way into the token for
    someone who holds none: risk nothing, and accuracy still pays."""
    p = validate(params or {})
    return round(p['free_payout'] * score_value, 6)
