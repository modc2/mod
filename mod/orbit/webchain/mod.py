"""
webchain — staketime-priority namespace + content resolver.

Ties three existing mod primitives into one "name a site, fetch its code, run
it" flow:

  - core/chain    BlocTime staking  -> *staketime* (the priority currency)
  - core/registry CID content       -> a module's code packed into the store
  - core/store    localfs CID store -> the actual bytes served by a name

A *name* is held by whoever has the highest staketime weight (tokens x
lock-blocks) locked to it; a strictly higher weight PREEMPTS the holder (their
stake refunds, they lose the name). Subdomains ("blog.foo") are minted only by
the parent holder. The on-chain source of truth is contracts/Namespace.sol;
this module keeps a local index (~/.mod/webchain/names.json) that the resolver
and the subdomain gateway read, mirroring the contract's rules so the system is
usable before/independently of an on-chain deploy.

CLI:
    m webchain/claim foo amount=1000 lock=200000
    m webchain/publish foo mod=polymarket          # pack code -> store CID -> point foo at it
    m webchain/resolve foo                          # -> {holder, cid, weight}
    m webchain/fetch foo                            # -> {path: code}  (what a runner executes)
    m webchain/mint_sub foo blog mod=openevent      # blog.foo, parent-delegated
"""
import os
import re
import mod as m

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')


class Mod:
    description = 'staketime-priority namespace; fetch & run code by name from the localfs store'

    def __init__(self, key='webchain', network='testnet', index_path=None):
        self.key = m.key(key)
        self.network = network
        self.index_path = index_path or m.abspath('~/.mod/webchain/names.json')
        self._reg = None
        self._chain = None

    # --- lazy deps ----------------------------------------------------------

    @property
    def reg(self):
        if self._reg is None:
            self._reg = m.mod('registry')()
        return self._reg

    @property
    def chain(self):
        if self._chain is None:
            self._chain = m.mod('chain')(network=self.network, key='webchain')
        return self._chain

    # --- local name index (mirror of the on-chain Namespace) ----------------

    def _load(self) -> dict:
        return m.get(self.index_path, {})

    def _save(self, idx: dict):
        m.put(self.index_path, idx)

    def _addr(self, key=None) -> str:
        """Caller address. Defaults to this module's key; an API layer would
        instead recover the signer from a protocol-auth token (see core/store)."""
        if key is None:
            return self.key.address.lower()
        if isinstance(key, str) and key.startswith('0x') and len(key) == 42:
            return key.lower()
        return m.key(key).address.lower()

    @staticmethod
    def weight(amount, lock) -> int:
        """Staketime weight = tokens x lock-blocks. The whole priority order."""
        return int(amount) * int(lock)

    def staketime(self, address=None) -> int:
        """On-chain BlocTime balance for an address (best-effort; 0 if chain
        unavailable). This is the global staketime backing a claimant."""
        try:
            return int(self.chain.bloctime_balance(self._addr(address)))
        except Exception:
            return 0

    # --- claims -------------------------------------------------------------

    def _validate_top(self, name: str):
        # Validate the RAW name: the on-chain Namespace hashes keccak(bytes(name))
        # case-sensitively, so names must already be canonical lowercase (we
        # reject rather than silently normalize, to stay consistent on/off chain).
        assert '.' not in name, 'top-level claim only (use mint_sub for subdomains)'
        assert NAME_RE.match(name), f'invalid name (lowercase a-z/0-9/-, no leading -): {name!r}'
        return name

    def claim(self, name: str, amount: int, lock: int, key=None, onchain=False) -> dict:
        """Claim/preempt a top-level name by locking staketime weight.

        Succeeds only if weight (= amount*lock) strictly exceeds the current
        holder's. A preempted holder keeps their tokens (refunded), loses the
        name. Set onchain=True to also send the Namespace.claim tx.
        """
        name = self._validate_top(name)
        addr = self._addr(key)
        w = self.weight(amount, lock)
        idx = self._load()
        cur = idx.get(name)
        if cur and w <= int(cur.get('weight', 0)):
            raise ValueError(
                f'weight {w} too low to preempt "{name}" (held by {cur["holder"]} '
                f'at weight {cur["weight"]}); need > {cur["weight"]}')

        preempted = cur['holder'] if cur else None
        if onchain:
            self._claim_onchain(name, amount, lock, key)

        idx[name] = {
            'holder': addr, 'weight': w, 'amount': int(amount), 'lock': int(lock),
            'cid': '', 'is_sub': False, 'parent': None,
        }
        self._save(idx)
        return {'name': name, 'holder': addr, 'weight': w,
                'preempted': preempted, 'cid': '', 'onchain': bool(onchain)}

    def _claim_onchain(self, name, amount, lock, key):
        """Hook: send Namespace.claim via the chain mod once Namespace.sol is
        deployed and wired into the chain contract config. Left explicit so the
        local index stays usable standalone."""
        raise NotImplementedError(
            'on-chain claim requires Namespace.sol deployed + registered in '
            'core/chain config; use onchain=False for the local index for now')

    def release(self, name: str, key=None) -> dict:
        name = name.lower()
        idx = self._load()
        cur = idx.get(name)
        addr = self._addr(key)
        if not cur or cur['holder'] != addr:
            raise ValueError(f'{addr} does not hold "{name}"')
        # also drop subdomains parented to this name
        dropped = [n for n, e in idx.items() if e.get('parent') == name]
        for n in dropped:
            del idx[n]
        del idx[name]
        self._save(idx)
        return {'released': name, 'subdomains_dropped': dropped, 'refund_amount': cur.get('amount', 0)}

    # --- content + subdomains ----------------------------------------------

    def set_content(self, name: str, cid: str, key=None) -> dict:
        """Point a held name at an existing store CID."""
        name = name.lower()
        idx = self._load()
        cur = idx.get(name)
        addr = self._addr(key)
        if not cur or cur['holder'] != addr:
            raise ValueError(f'{addr} does not hold "{name}"')
        cur['cid'] = cid
        self._save(idx)
        return {'name': name, 'cid': cid}

    def publish(self, name: str, mod: str, key=None, comment=None) -> dict:
        """Pack a module's code into the localfs store (CID) and point `name`
        at it. `mod` is any local module name; uses registry.add_content."""
        cid = self.reg.add_content(mod=mod, comment=comment)
        self.set_content(name, cid, key=key)
        return {'name': name, 'mod': mod, 'cid': cid}

    def mint_sub(self, parent: str, label: str, mod: str = None, cid: str = None, key=None) -> dict:
        """Mint/repoint a parent-delegated subdomain `label`.`parent`. Provide
        either a module to publish, or an existing cid."""
        assert NAME_RE.match(label), f'invalid label (lowercase a-z/0-9/-, no leading -): {label!r}'
        parent = parent.lower()
        idx = self._load()
        p = idx.get(parent)
        addr = self._addr(key)
        if not p or p['holder'] != addr:
            raise ValueError(f'{addr} does not hold parent "{parent}"')
        assert not p.get('is_sub'), 'cannot nest under a subdomain'
        if cid is None:
            assert mod, 'provide mod= or cid='
            cid = self.reg.add_content(mod=mod)
        full = f'{label}.{parent}'
        idx[full] = {'holder': addr, 'weight': 0, 'amount': 0, 'lock': 0,
                     'cid': cid, 'is_sub': True, 'parent': parent}
        self._save(idx)
        return {'name': full, 'parent': parent, 'cid': cid, 'mod': mod}

    # --- resolution ---------------------------------------------------------

    def resolve(self, name: str) -> dict:
        """name -> {holder, cid, weight, is_sub, parent}. Unclaimed -> None."""
        return self._load().get(name.lower())

    def fetch(self, name: str, expand=True) -> dict:
        """Resolve a name to its code: {path: file_content}. This is exactly what
        a runner would load and execute (sandbox before running untrusted code —
        see the claude module's nobody-drop job sandbox)."""
        entry = self.resolve(name)
        if not entry:
            raise KeyError(f'name not found: {name}')
        cid = entry.get('cid')
        if not cid:
            raise ValueError(f'"{name}" has no content set')
        return self.reg.content(cid, expand=expand)

    # site() is the gateway-facing alias: serve a name's files as a website.
    site = fetch

    def names(self, search: str = None, holder: str = None) -> dict:
        idx = self._load()
        out = {}
        for n, e in idx.items():
            if search and search not in n:
                continue
            if holder and e.get('holder') != holder.lower():
                continue
            out[n] = e
        return out

    # --- meta ---------------------------------------------------------------

    def forward(self, **kwargs):
        return self.info()

    def test(self, verbose=True):
        """Run the pytest suite (m webchain/test).

        Runs from the framework root so the local `mod.py` doesn't shadow the
        `mod` package on sys.path.
        """
        import subprocess
        import sys
        here = os.path.dirname(os.path.abspath(__file__))
        root = list(m.__path__)[0]
        rel = os.path.relpath(os.path.join(here, 'tests'), root)
        args = ['-m', 'pytest', rel, '-v' if verbose else '-q']
        return subprocess.run([sys.executable, *args], cwd=root).returncode

    def info(self) -> dict:
        idx = self._load()
        return {
            'name': 'webchain',
            'description': self.description,
            'network': self.network,
            'names_registered': len(idx),
            'index': self.index_path,
            'contract': 'contracts/Namespace.sol',
        }
