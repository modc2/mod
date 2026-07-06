import math
import json
import os
from typing import List, Dict, Optional
from core.schemas import ScoreBreakdown

CRITERIA_WEIGHTS = {
    "novelty": 0.25,
    "feasibility": 0.20,
    "market_size": 0.20,
    "defensibility": 0.15,
    "clarity": 0.10,
    "traction_signal": 0.10,
}


def composite_score(breakdown: ScoreBreakdown) -> float:
    total = 0.0
    for criterion, weight in CRITERIA_WEIGHTS.items():
        total += getattr(breakdown, criterion, 0.0) * weight
    return round(total, 4)


def normalize_scores(scores: List[float]) -> List[float]:
    if not scores:
        return []
    if len(scores) == 1:
        return [1.0]
    temperature = 2.0
    max_s = max(scores)
    exp_scores = [math.exp((s - max_s) / temperature) for s in scores]
    total = sum(exp_scores)
    if total == 0:
        return [1.0 / len(scores)] * len(scores)
    return [round(e / total, 6) for e in exp_scores]


class IncentiveMechanism:
    """EMA-based incentive: smooths scores, normalizes via softmax, tracks leaderboard."""

    def __init__(self, alpha: float = 0.3, min_score_threshold: float = 2.0,
                 max_history: int = 100):
        self.alpha = alpha
        self.min_score_threshold = min_score_threshold
        self.max_history = max_history
        self.score_history: Dict[int, List[float]] = {}
        self.ema_scores: Dict[int, float] = {}

    def update(self, uid: int, score: float) -> float:
        if uid not in self.ema_scores:
            self.ema_scores[uid] = score
        else:
            self.ema_scores[uid] = self.alpha * score + (1 - self.alpha) * self.ema_scores[uid]
        if uid not in self.score_history:
            self.score_history[uid] = []
        self.score_history[uid].append(score)
        if len(self.score_history[uid]) > self.max_history:
            self.score_history[uid] = self.score_history[uid][-self.max_history:]
        return self.ema_scores[uid]

    def compute_weights(self, epoch_scores: Dict[int, float]) -> Dict[str, float]:
        for uid, score in epoch_scores.items():
            self.update(uid, score)

        eligible_uids = []
        eligible_scores = []
        for uid, score in epoch_scores.items():
            ema = self.ema_scores.get(uid, 0.0)
            if ema >= self.min_score_threshold:
                eligible_uids.append(uid)
                eligible_scores.append(ema)

        if not eligible_uids:
            return {}

        normalized = normalize_scores(eligible_scores)
        return {str(uid): w for uid, w in zip(eligible_uids, normalized)}

    def get_leaderboard(self) -> List[Dict]:
        entries = []
        for uid, ema in sorted(self.ema_scores.items(), key=lambda x: x[1], reverse=True):
            history = self.score_history.get(uid, [])
            entries.append({
                "uid": uid,
                "ema_score": round(ema, 4),
                "last_score": round(history[-1], 4) if history else 0.0,
                "num_epochs": len(history),
                "trend": round(history[-1] - history[-2], 4) if len(history) >= 2 else 0.0,
            })
        return entries

    def state_dict(self) -> Dict:
        return {
            "ema_scores": {str(k): v for k, v in self.ema_scores.items()},
            "score_history": {str(k): v for k, v in self.score_history.items()},
            "alpha": self.alpha,
            "min_score_threshold": self.min_score_threshold,
        }

    @classmethod
    def from_state_dict(cls, state: Dict) -> 'IncentiveMechanism':
        im = cls(
            alpha=state.get("alpha", 0.3),
            min_score_threshold=state.get("min_score_threshold", 2.0),
        )
        im.ema_scores = {int(k): v for k, v in state.get("ema_scores", {}).items()}
        im.score_history = {int(k): v for k, v in state.get("score_history", {}).items()}
        return im

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(self.state_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> 'IncentiveMechanism':
        if os.path.exists(path):
            with open(path) as f:
                return cls.from_state_dict(json.load(f))
        return cls()
