"""
store ↔ chain integration — BlocTime-gated access + on-chain Registry.

Mirrors the claude module's pattern ([[claude_chain_bloctime_integration]]):
  • a BlocTime *holder* (staked on-chain) is granted store access, exactly as if
    they were on the off-chain whitelist — so access can be earned on-chain
    without the owner editing ~/.mod/store/whitelist.json;
  • the module registers itself in the chain mod's permissionless Registry so the
    protocol can discover it.

Everything degrades to a safe default on any chain/RPC failure: BlocTime checks
return False (never block the request path on RPC), status reports the error.
Lazy + cached so importing this module costs nothing until first use.
"""
import time
from typing import Optional


class OnChain:
    def __init__(self, network: str = 'testnet', gate: bool = True,
                 ttl: int = 60, name: str = 'store'):
        self.network = network
        self.gate = gate
        self.ttl = int(ttl)
        self.name = name
        self._chain = None
        self._cache = {}      # addr -> (is_holder, ts)

    def chain(self):
        if self._chain is None:
            import mod as m  # local import: keep module import cheap/side-effect-free
            self._chain = m.mod('chain')(network=self.network)
        return self._chain

    # ── BlocTime ownership ─────────────────────────────────────────

    def is_bloctime_holder(self, address: Optional[str]) -> bool:
        """True if `address` holds BlocTime on-chain. Cached; fails closed."""
        if not self.gate or not address:
            return False
        addr = address.lower()
        now = time.time()
        cached = self._cache.get(addr)
        if cached and now - cached[1] < self.ttl:
            return cached[0]
        try:
            holder = bool(self.chain().is_bloctime_holder(addr))
        except Exception:
            holder = False
        self._cache[addr] = (holder, now)
        return holder

    def bloctime_balance(self, address: Optional[str]) -> int:
        try:
            return int(self.chain().bloctime_balance(address))
        except Exception:
            return 0

    # ── Registry ───────────────────────────────────────────────────

    def register(self, data: str, force: bool = False) -> dict:
        """Register (or update) `name` → `data` in the chain Registry.

        Skips the transaction when already registered with identical data,
        unless force=True (chain.reg also updates in-place if it already exists).
        """
        chain = self.chain()
        try:
            if not force and chain.mod_exists(self.name):
                existing = chain.get_mod(chain.name2id(self.name))
                if existing.get('data') == data:
                    return {'status': 'already_registered', 'name': self.name, 'data': data}
        except Exception:
            pass
        receipt = chain.reg(self.name, data)
        return {'status': 'registered', 'name': self.name, 'data': data,
                'tx': _tx_hash(receipt)}

    def status(self) -> dict:
        """Discovery snapshot: network, gate, and whether we're registered."""
        out = {'network': self.network, 'bloctime_gate': self.gate,
               'name': self.name, 'registered': False}
        try:
            chain = self.chain()
            if chain.mod_exists(self.name):
                out['registered'] = True
                out['mod'] = chain.get_mod(chain.name2id(self.name))
        except Exception as e:
            out['error'] = str(e)
        return out


def _tx_hash(receipt) -> Optional[str]:
    """Pull a tx hash out of a web3 receipt / AttributeDict / dict / str."""
    h = getattr(receipt, 'transactionHash', None)
    if h is None and isinstance(receipt, dict):
        h = receipt.get('transactionHash')
    if h is None:
        return str(receipt) if receipt is not None else None
    return h.hex() if hasattr(h, 'hex') else str(h)
