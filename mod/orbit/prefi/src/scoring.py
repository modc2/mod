"""Prediction scoring — a normalized dollar error in, a 0..1 score out.

Every model answers the same question: how far off was the prediction, in
dollars, relative to the price it was predicting?

    normalized_error = |predicted − actual| / actual

That ratio is the *only* input to a model, which is what makes the score
comparable across assets — being $50 off on BTC at $64,000 and $0.0003 off on
AERO at $0.41 are both a 0.08% miss and score identically. Models differ only
in how fast the score decays once the error passes `tolerance`.

A model is a **score function** from `curves.py`: an expression over the
error plus its parameters. The defaults (`l2`, `linear`, `exponential`,
`threshold`, …) are written in that language, and so is anything a user saves
or imports. Params are validated into a `fn` snapshot — the program itself —
so a prediction placed under a function that is later edited or deleted still
resolves exactly as it was sold.
"""

import math
from typing import Callable, Dict, Optional

try:
    import curves
except ImportError:                                  # imported as a package
    from . import curves


# ── Models ───────────────────────────────────────────────────────────
# The built-in curves as plain callables `(normalized_error, tolerance)` —
# the shape older code (and the tests) expect. Each one is the same program
# `curves.BUILTINS` holds, evaluated with `tol` set to the tolerance given.

class _BuiltinModels(dict):
    def __init__(self):
        super().__init__()
        for name, spec in curves.BUILTINS.items():
            self[name] = self._make(spec)

    @staticmethod
    def _make(spec) -> Callable[[float, float], float]:
        def model(nerr: float, tol: float) -> float:
            return curves.evaluate(spec, nerr, {'tol': tol} if 'tol' in spec['params'] else None)
        model.__doc__ = spec.get('description', '')
        model.__name__ = spec['name']
        return model


MODELS: Dict[str, Callable[[float, float], float]] = _BuiltinModels()


# ── Parameters ───────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    'model': 'l2',          # a function name: a default, or one in the library
    'tolerance': 0.02,      # normalized error scale (0.02 = a 2% miss) → `tol`
    'model_params': {},     # other parameter overrides, by name
    'fn': None,             # the resolved program {name, expr, params}
    'multiplier': 3.0,      # payout = burn × multiplier × score
    'horizon': 86400,       # default seconds until resolution (1 day)
    'min_burn': 1.0,        # smallest PREFI burn accepted
    'free_per_day': 3,      # free calls an address may place per 24h (0 = off)
    'free_payout': 1.0,     # PREFI a *perfect* free call mints; scaled by score
}

# The library non-default names resolve through when a caller passes none.
# `Mod` points it at the module store; standalone use sees the built-ins only.
LIBRARY: Optional[curves.Library] = None

MIN_HORIZON = 3600          # 1 hour
MAX_HORIZON = 2592000       # 30 days
FREE_WINDOW = 86400         # rolling window the free allowance is counted over


def describe_models(library: curves.Library = None) -> Dict[str, str]:
    """Model name → what its curve does. Drives the picker in the UI."""
    return curves.describe(library if library is not None else LIBRARY)


def validate(params: Dict, library: curves.Library = None) -> Dict:
    """Merge over the defaults and bounds-check. Raises ValueError on junk.

    Resolves `model` (+ `tolerance`, `model_params`) into `fn`, the snapshot
    that actually scores. A snapshot already present is trusted as the
    fallback when the name is gone from the library — that is how an old
    prediction keeps its rule.
    """
    merged = {**DEFAULT_PARAMS, **{k: v for k, v in (params or {}).items()
                                   if k in DEFAULT_PARAMS}}

    merged['tolerance'] = float(merged['tolerance'])
    if merged['tolerance'] <= 0:
        raise ValueError('tolerance must be > 0')

    merged['model'] = str(merged['model'] or '').strip().lower()
    if library is None:
        library = LIBRARY
    try:
        merged['fn'] = curves.resolve(merged['model'], library,
                                      tolerance=merged['tolerance'],
                                      params=merged.get('model_params') or {},
                                      fallback=merged.get('fn'))
    except curves.ExprError as exc:
        raise ValueError(str(exc)) from None
    merged['model_params'] = {k: v for k, v in merged['fn']['params'].items() if k != 'tol'}

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


def score(predicted: float, actual: float, params: Dict = None,
          library: curves.Library = None) -> Dict:
    """Score one prediction. Returns the error terms alongside the score so a
    caller can show its work rather than just a number."""
    p = validate(params or {}, library)
    nerr = normalized_error(predicted, actual)
    value = 0.0 if math.isinf(nerr) else curves.evaluate(p['fn'], nerr)

    return {
        'model': p['model'],
        'tolerance': p['tolerance'],
        'fn': p['fn'],
        'abs_error': round(abs(predicted - actual), 8),
        'normalized_error': None if math.isinf(nerr) else round(nerr, 8),
        'score': round(min(1.0, max(0.0, value)), 8),
    }


def payout(burn: float, score_value: float, params: Dict = None,
           library: curves.Library = None) -> float:
    """PREFI minted back for a scored prediction. A perfect call returns
    `multiplier`× the burn; a total miss returns nothing and the burn is gone."""
    p = validate(params or {}, library)
    return round(burn * p['multiplier'] * score_value, 6)


def free_mint(score_value: float, params: Dict = None,
              library: curves.Library = None) -> float:
    """PREFI minted for a *free* call — there is no burn to scale, so the whole
    payout is `free_payout` × score. It is the only way into the token for
    someone who holds none: risk nothing, and accuracy still pays."""
    p = validate(params or {}, library)
    return round(p['free_payout'] * score_value, 6)
