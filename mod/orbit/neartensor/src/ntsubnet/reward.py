"""
Reward mechanism — pure functions, no I/O, unit-testable.

score = correctness * (W_FRESH * freshness + W_LAT * latency_credit)

correctness is binary and gating: a wrong or unsigned answer earns 0 no
matter how fast or fresh. freshness decays linearly with how far behind the
validator's final height the miner's anchored block is. latency_credit is 1
inside the target and decays with the square of the overshoot.
"""
W_FRESH = 0.7
W_LAT = 0.3


def correctness(truth, answer):
    """Byte-equal canonical answers → 1.0, anything else → 0.0."""
    if truth is None or answer is None:
        return 0.0
    return 1.0 if truth == answer else 0.0


def freshness(anchor_height, final_height, max_lag):
    lag = max(0, int(final_height) - int(anchor_height))
    if lag >= max_lag:
        return 0.0
    return 1.0 - lag / max_lag


def latency_credit(elapsed, target):
    if elapsed <= target:
        return 1.0
    over = elapsed / target
    return max(0.0, 1.0 / (over * over))


def score_response(*, truth, answer, anchor_height, final_height,
                   elapsed, signature_ok, max_lag=30, latency_target=2.0):
    """Score one (task, response) pair in [0, 1]."""
    if not signature_ok:
        return 0.0
    c = correctness(truth, answer)
    if c == 0.0:
        return 0.0
    return c * (W_FRESH * freshness(anchor_height, final_height, max_lag)
                + W_LAT * latency_credit(elapsed, latency_target))


def ema(prev, new, alpha):
    """Smooth scores across epochs so one bad epoch doesn't zero a miner."""
    if prev is None:
        return new
    return alpha * new + (1 - alpha) * prev


def normalize_weights(scores):
    """Map {uid: score} to {uid: weight} summing to 1.0 (empty if all zero)."""
    total = sum(v for v in scores.values() if v > 0)
    if total <= 0:
        return {}
    return {uid: v / total for uid, v in scores.items() if v > 0}
