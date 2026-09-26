"""Subnet configuration — one dataclass, sourced from config.json `subnet` + env."""
import json
import os
from dataclasses import dataclass, field

DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DIR)
DATA_DIR = os.environ.get("NEARTENSOR_SN_DATA", os.path.join(ROOT, "data"))

DEFAULT_NEAR_RPCS = [
    "https://rpc.mainnet.near.org",
    "https://free.rpc.fastnear.com",
    "https://near.lava.build",
]
DEFAULT_NEAR_TESTNET_RPCS = [
    "https://rpc.testnet.near.org",
    "https://test.rpc.fastnear.com",
]


@dataclass
class SubnetConfig:
    # "local" = file-backed chain on this box; "test"/"finney" = real subtensor
    network: str = "local"
    netuid: int = 0
    near_network: str = "testnet"
    near_rpcs: list = field(default_factory=list)
    miner_port: int = 50184
    miner_host: str = "0.0.0.0"
    # bittensor wallet (only used when network != "local")
    wallet_name: str = "neartensor"
    wallet_hotkey: str = "default"
    # validator loop
    epoch_seconds: int = 60
    sample_size: int = 8           # tasks per miner per epoch
    ema_alpha: float = 0.3         # weight smoothing across epochs
    query_timeout: float = 12.0
    # scoring
    max_height_lag: int = 30       # NEAR blocks (~1s each) before freshness hits 0
    latency_target: float = 2.0    # seconds for full latency credit

    @classmethod
    def load(cls, overrides=None):
        cfg = {}
        path = os.path.join(ROOT, "config.json")
        if os.path.exists(path):
            with open(path) as f:
                cfg = json.load(f).get("subnet", {})
        cfg.update({k: v for k, v in (overrides or {}).items() if v is not None})
        known = {f.name for f in cls.__dataclass_fields__.values()}
        self = cls(**{k: v for k, v in cfg.items() if k in known})
        if not self.near_rpcs:
            self.near_rpcs = (DEFAULT_NEAR_TESTNET_RPCS if self.near_network == "testnet"
                              else DEFAULT_NEAR_RPCS)
        if os.environ.get("NEARTENSOR_SN_NETWORK"):
            self.network = os.environ["NEARTENSOR_SN_NETWORK"]
        return self

    @property
    def data_dir(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        return DATA_DIR

    @property
    def is_local(self):
        return self.network == "local"

    def miner_url(self, host="127.0.0.1"):
        return f"http://{host}:{self.miner_port}"
