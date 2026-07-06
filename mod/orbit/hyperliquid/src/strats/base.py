"""
Base abstractions for hyperliquid strategies.

A `Strat` is a Python object that knows which leaders to copy and how to
shape the live-engine config that mirrors their fills. The engine itself
lives in Rust (live_engine.rs); strats compose the config it consumes.

Method contract
---------------
    pick_leaders(hl)     REQUIRED. Returns a list[Leader] — who to mirror
                         and how to weight them. Allowed to hit the API
                         (e.g. `hl.top_traders(...)`).
    build_config(hl, e)  Default impl wraps `pick_leaders` into the engine
                         payload. Override only to inject coin filters,
                         vault routing, schedule-cancel, etc.
    start / stop / status   Delegate to the venue-side live engine via
                            the Hyperliquid mod instance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Leader:
    """One trader to mirror. `weight` multiplies on top of the engine's
    `size_pct`, so effective mirror size is
    `leader_fill_size × (size_pct / 100) × weight`."""
    address: str
    weight: float = 1.0
    enabled: bool = True

    def to_engine(self) -> Dict[str, Any]:
        return {"address": self.address.lower(), "weight": self.weight, "enabled": self.enabled}


@dataclass
class StratParams:
    """Risk + execution knobs every strat exposes. Mirrors the EngineConfig
    fields the Rust live engine consumes — see api/src/live_engine.rs."""
    size_pct: float = 10.0              # mirror leader.size × this %
    max_per_trade_usd: float = 0.0      # 0 = unlimited
    min_order_size_usd: float = 10.0    # skip dust mirrors
    max_slippage_bps: int = 100         # one-sided slippage padding
    interval_ms: int = 15_000           # engine cycle period
    coins_allow: List[str] = field(default_factory=list)
    coins_deny: List[str] = field(default_factory=list)
    vault_address: Optional[str] = None # route orders through a vault if set
    capital: float = 0.0                # informational; engine sizes %-based


class Strat(ABC):
    """Abstract base for hyperliquid copy strategies.

    Subclasses MUST set `name` + `description` and implement
    `pick_leaders`. Default `build_config` is sufficient for most copy
    patterns — override only if you need exotic filters/routing."""

    #: Identifier used by the registry / CLI.
    name: str = "abstract"

    #: One-line description shown by `list_strats()`.
    description: str = ""

    def __init__(self, **params: Any) -> None:
        # Split kwargs: risk knobs go to StratParams, the rest stay in
        # _params for the subclass (e.g. n, days, addresses).
        known = {f for f in StratParams.__dataclass_fields__}
        risk_kwargs = {k: params.pop(k) for k in list(params) if k in known}
        self.risk = StratParams(**risk_kwargs)
        self._params = params

    # ── Required ──

    @abstractmethod
    def pick_leaders(self, hl) -> List[Leader]:
        """Return the list of leaders the strat wants to mirror. `hl` is
        the Hyperliquid mod instance."""
        raise NotImplementedError

    # ── Optional override ──

    def build_config(self, hl, eoa: str) -> Dict[str, Any]:
        """Compose the live-engine config dict."""
        leaders = self.pick_leaders(hl)
        if not leaders:
            raise RuntimeError(f"strat {self.name!r} returned no leaders")
        return {
            "eoa": eoa.lower(),
            "strategy_id": self.name,
            "traders": [l.to_engine() for l in leaders],
            "interval_ms": self.risk.interval_ms,
            "size_pct": self.risk.size_pct,
            "max_per_trade_usd": self.risk.max_per_trade_usd,
            "min_order_size_usd": self.risk.min_order_size_usd,
            "max_slippage_bps": self.risk.max_slippage_bps,
            "coins_allow": self.risk.coins_allow,
            "coins_deny": self.risk.coins_deny,
            "vault_address": self.risk.vault_address,
            "capital": self.risk.capital,
        }

    # ── Lifecycle (delegates to the live engine) ──

    def start(self, hl, eoa: str) -> Dict[str, Any]:
        """Start the strat. Idempotent — replaces any existing session for
        `eoa`."""
        cfg = self.build_config(hl, eoa)
        return hl.live_start(**{k: v for k, v in cfg.items() if v is not None})

    def stop(self, hl, eoa: str) -> Dict[str, Any]:
        return hl.live_stop(eoa)

    def status(self, hl, eoa: str) -> Dict[str, Any]:
        return hl.live_status(eoa)

    # ── Introspection ──

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "params": dict(self._params),
            "risk": self.risk.__dict__,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self._params}>"
