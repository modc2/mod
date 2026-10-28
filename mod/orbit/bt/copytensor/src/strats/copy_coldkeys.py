"""CopyColdkeys — mirror a fixed list of coldkeys."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Union

from .base import Leader, Strat


class CopyColdkeys(Strat):
    """Mirror exactly the coldkeys you name — the strat for "I already know
    who I trust". Pass plain ss58 strings for equal weight, or
    `{"ss58": ..., "weight": ...}` dicts to size them yourself.

    Example:
        CopyColdkeys(["5F3s...", "5DAA..."], capital=50, size_pct=10)
    """

    name = "copy_coldkeys"
    description = "Mirror a fixed list of coldkeys, equal or custom-weighted."

    def __init__(self, ss58s: Sequence[Union[str, Dict[str, Any]]],
                 **params: Any) -> None:
        super().__init__(**params)
        self.entries: List[Dict[str, Any]] = [
            {"ss58": e, "weight": 1.0} if isinstance(e, str) else dict(e)
            for e in ss58s
        ]
        self._params.update(ss58s=[e["ss58"] for e in self.entries])

    def pick_leaders(self, ct) -> List[Leader]:
        return [Leader(ss58=e["ss58"], weight=float(e.get("weight", 1.0)))
                for e in self.entries]
