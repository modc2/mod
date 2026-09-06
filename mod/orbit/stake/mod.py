import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # appended, not prepended — this file would otherwise shadow the protocol's `mod`
    sys.path.append(HERE)


class Mod:
    description = """Stake BlocTime (BLOC) on the apps registered in the on-chain Registry.
Each registered app is a staking pool: back an app with BLOC as a curation
signal, unstake any time, and app owners can add BLOC rewards that split
pro-rata across their stakers. AppStaking.sol lives on Base Sepolia against
the live BlocTime + Registry contracts."""
    path = HERE

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def _api(self):
        import api
        return api

    def info(self):
        cfg = json.loads(open(os.path.join(HERE, "config.json")).read())
        return {
            "name": "stake",
            "description": self.description,
            "network": "base-sepolia",
            "contracts": cfg.get("contracts", {}).get("testnet", {}),
            "port": cfg.get("port"),
            "console": f"http://localhost:{cfg.get('port')}/",
        }

    def readme(self):
        p = os.path.join(HERE, "README.md")
        return open(p).read() if os.path.exists(p) else None

    # ── reads ────────────────────────────────────────────────
    def apps(self):
        """Every registered app with its staked BLOC, reward total and staker count."""
        return self._api().apps()

    def app(self, app_id: int):
        """One app's pool including the full staker book."""
        return self._api().app_detail(int(app_id))

    def positions(self, address: str):
        """A wallet's stakes, claimable rewards, BLOC balance and allowance."""
        return self._api().positions(address)

    def contract(self):
        """AppStaking address + ABI (what the console signs against)."""
        return self._api().contract_info()

    # ── writes (server-signed via a named mod key) ───────────
    def stake(self, app_id: int, amount: float, key: str = "test"):
        """Stake BLOC on a registered app (auto-approves if needed)."""
        from api import WriteReq, do_stake
        return do_stake(WriteReq(app_id=int(app_id), amount=float(amount), key=key))

    def unstake(self, app_id: int, amount: float = 0, key: str = "test"):
        """Unstake BLOC from an app; amount 0 unstakes everything."""
        from api import WriteReq, do_unstake
        return do_unstake(WriteReq(app_id=int(app_id), amount=float(amount), key=key))

    def reward(self, app_id: int, amount: float, key: str = "test"):
        """Add BLOC rewards to an app's pool, split pro-rata across its stakers."""
        from api import WriteReq, do_reward
        return do_reward(WriteReq(app_id=int(app_id), amount=float(amount), key=key))

    def claim(self, app_id: int, key: str = "test"):
        """Claim accrued rewards for one app."""
        from api import WriteReq, do_claim
        return do_claim(WriteReq(app_id=int(app_id), key=key))

    def serve(self, port: int = None):
        """Console + JSON API on one port (default 50840)."""
        self._api().serve(port)
