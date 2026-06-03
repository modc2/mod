"""CopyWallets — mirror a fixed set of wallets."""

from __future__ import annotations

from typing import Any, List

from .base import Leader, Strat


class CopyWallets(Strat):
    """Mirror a fixed list of leader wallets. The simplest strat — useful
    when you already know who you want to copy and don't need scoring.

    Example:
        CopyWallets(['0xleader1', '0xleader2'], size_pct=20).start(hl, eoa)
    """

    name = "copy_wallets"
    description = "Mirror a fixed list of leader wallets."

    def __init__(self, addresses: List[str], **params: Any) -> None:
        super().__init__(**params)
        if not addresses:
            raise ValueError("CopyWallets needs at least one address")
        self._addresses = [a.lower() for a in addresses]
        self._params["addresses"] = self._addresses

    def pick_leaders(self, hl) -> List[Leader]:
        return [Leader(address=a, weight=1.0) for a in self._addresses]
