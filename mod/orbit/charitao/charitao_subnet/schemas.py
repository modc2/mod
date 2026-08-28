import time
from dataclasses import dataclass, field, asdict


@dataclass
class Charity:
    id: str
    name: str
    address: str            # destination address donations must be sent to
    cause: str = ''
    url: str = ''
    verified: bool = False  # only verified charities are credited
    network: str = 'demo'   # 'demo' (mock chain) or a real network name
    links: list = field(default_factory=list)   # [{label, url}] evaluators, registries, press
    audits: list = field(default_factory=list)  # [{year, label, url}] audited financials / filings
    legit: bool = None      # owner override: True vouched, False flagged, None -> derived
    added_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Transfer:
    txid: str
    sender: str
    recipient: str
    amount: float
    block: int
    at: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Donation:
    """A verified transfer attributed to a miner and a whitelisted charity."""
    uid: int
    txid: str
    charity_id: str
    amount: float
    epoch: int

    def to_dict(self) -> dict:
        return asdict(self)
