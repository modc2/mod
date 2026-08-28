"""Shared types for the jcode iOS UX reward framework.

These are the only data structures scorers and the aggregator share. Keep this
module tiny and dependency-light so every scorer can import it freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CategoryScore:
    """One scorer's verdict for one matrix cell.

    value:     0..100, higher is better.
    evidence:  raw measurements behind the value, for debugging + regression
               diffs. Keep keys stable.
    available: False means "this scorer could not measure here" (e.g. perf with
               no Instruments). The aggregator drops unavailable scores and
               renormalizes weights so missing data never tanks the reward.
    """

    name: str
    category: str
    weight: float
    value: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    available: bool = True

    def clamped(self) -> float:
        return max(0.0, min(100.0, float(self.value)))


def make_unavailable(name: str, category: str, weight: float, reason: str) -> "CategoryScore":
    return CategoryScore(
        name=name, category=category, weight=weight,
        value=0.0, evidence={"reason": reason}, available=False,
    )
