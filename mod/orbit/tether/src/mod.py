"""
tether — USDT stablecoin orbit module (stub).

Minimal scaffold so the dev console picks it up. Replace these methods with
real chain calls (eth_call to USDT contract, etc.) when wiring it up.
"""
import json
import os
from pathlib import Path


class Mod:
    description = "Tether (USDT) on-chain integration"
    path = r"/Users/broski/mod/mod/orbit/tether"

    def __init__(self):
        self._cfg = json.loads(Path(self.path, "config.json").read_text())

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {
            "name": "tether",
            "description": self.description,
            "owner": self._cfg.get("owner"),
            "icon": self._cfg.get("icon"),
            "ports": {"api": self._cfg.get("port"), "app": self._cfg.get("app_port")},
        }

    def health(self) -> dict:
        return {"service": "tether", "status": "ok"}

    def balance(self, address: str) -> dict:
        return {"address": address, "balance": "0", "symbol": "USDT", "note": "stub — wire to chain.eth_call(usdt, balanceOf)"}

    def transfer(self, to: str, amount: str, key=None) -> dict:
        return {"to": to, "amount": amount, "symbol": "USDT", "tx": None, "note": "stub"}

    def approve(self, spender: str, amount: str, key=None) -> dict:
        return {"spender": spender, "amount": amount, "tx": None, "note": "stub"}
