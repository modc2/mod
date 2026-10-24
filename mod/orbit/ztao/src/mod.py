"""ztao — anonymous Bittensor-style subnets, on any chain with an adapter.

Miners and validators where neither side knows who the other is — only
that they are in the registered set. Every action is a scope-linkable ring
signature (LSAG over the STARK curve):

    submit  miner proves "one of the miners" for (subnet, epoch); its key
            image is the epoch TAG: one submission per miner per epoch,
            unlinkable to its key or to its tags in other epochs
    score   validator proves "one of the validators" for (subnet, epoch,
            tag); its key image is a nullifier: one score per validator per
            tag, and its scores on different tags are unlinkable
    settle  anyone, after the epoch: YUMA consensus per tag on chain —
            consensus = the highest score half the validator set agrees on,
            every score clipped to it (core/yuma.py is the reference)
    claim   once every tag is settled, the miner re-signs in the epoch scope
            (same image = same tag) and credits emission * rank / sum rank

Layout (all code under src/, state in data/ next to config.json):
    mod.py          this file: the module's fns, chain-agnostic
    server.py       the console + JSON API
    core/           signer.py (keys + the Cairo prover in core/prover/),
                    util.py, stark.py (curve + Poseidon in Python)
    networks/       one package per chain: adapter.py (a networks.base.
                    Network), its contract/, tools/ and tests/. Adding a
                    chain = adding a package; see networks/README.md
    tests/          module-level tests (server, every adapter's contract)

Every write fn takes network=<any adapter's net>, e.g. devnet | sepolia |
mainnet | near | near-testnet | eth | eth-sepolia | solana | solana-devnet.
"""

import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))  # src/
_PKG = 'ztao_src'


def _package():
    """src/ as the package `ztao_src`, loaded by path: a bare `import core`
    or `import mod` would resolve to the fleet's, not ours."""
    m = sys.modules.get(_PKG)
    if m is None or list(getattr(m, '__path__', [])) != [HERE]:
        spec = importlib.util.spec_from_file_location(
            _PKG, os.path.join(HERE, '__init__.py'),
            submodule_search_locations=[HERE])
        m = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = m
        spec.loader.exec_module(m)
    return m


_package()
from ztao_src import DATA, ROOT  # noqa: E402
from ztao_src.core import (budget as bg, consensus as cs, experiment as ex,  # noqa: E402
                           fees, forecast as fc, guard, secagg as sa, util, yuma)
from ztao_src.core.signer import Signer  # noqa: E402
from ztao_src.core.util import ROLES, load, save  # noqa: E402
from ztao_src.networks import Registry  # noqa: E402

SIGNER = Signer()
NETS = Registry(SIGNER)
CHAINS = {a.chain: a.local for a in NETS}  # simulate(chain=) -> local net
MAX_RING = max(a.max_ring for a in NETS)
SHARED_SOURCES = [
    ('Signer · every chain', 'shared', [
        ('src/core/prover/src/lib.cairo', 'the signer (cairo-run): one signature, every verifier'),
        ('src/core/signer.py', 'keys + prover calls'),
        ('src/core/stark.py', 'curve + Poseidon in Python: the Solana hints'),
        ('src/core/yuma.py', 'Zuma consensus: the reference every contract matches')]),
    ('Shared · every chain', 'shared', [
        ('src/mod.py', 'the module: dispatch, simulate, compare'),
        ('src/networks/base.py', 'the adapter contract every chain implements'),
        ('src/networks/__init__.py', 'the registry: one package = one chain'),
        ('src/server.py', 'this console + JSON API')]),
]


def _sim_path(chain):
    return os.path.join(DATA, 'last_sim.json' if chain == 'starknet'
                        else f'last_sim_{chain}.json')


def _gas_path(chain):
    return os.path.join(DATA, f'{chain}_gas.json')


class Mod:
    description = ('Anonymous subnets on any chain: miners and validators '
                   'act through ring signatures over the registered set, so '
                   'neither side learns who the other is.')
    path = ROOT
    config = json.load(open(os.path.join(ROOT, 'config.json')))

    # ---- module protocol ---------------------------------------------------

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {'name': 'ztao', 'description': self.description,
                'version': self.config.get('version'),
                'protocol': self.config.get('protocol'),
                'networks': list(NETS.by_net),
                'deployments': load(util.DEPLOYS, {}),
                'keys': self.keys(),
                'fns': self.config.get('fns')}

    def networks(self):
        """Every chain adapter: its nets, ring cap, cost units, console meta."""
        return [a.describe() for a in NETS]

    def health(self):
        out = {'ok': True, 'module': 'ztao', 'prover_built': SIGNER.built()}
        for a in NETS:
            try:
                out[a.chain] = a.health()
            except Exception as e:  # noqa: BLE001
                out[a.chain] = {'error': str(e)}
        # pre-adapter field names, kept for existing readers
        sn = out.get('starknet') or {}
        out.update(tools=sn.get('tools'), contract_built=sn.get('built'),
                   devnet_up=sn.get('up'))
        return out

    def readme(self):
        p = os.path.join(ROOT, 'README.md')
        return open(p).read() if os.path.exists(p) else None

    # ---- build / test --------------------------------------------------------

    def _chains(self, chain):
        return list(NETS) if chain == 'all' else [NETS.chain(chain)]

    def build(self, chain='all'):
        """Compile the signer (Cairo prover) + each chain's contract.
        chain = all | starknet | near | eth | solana | ..."""
        out = {}
        if chain in ('all', 'starknet'):
            out['prover'] = SIGNER.build()
        for a in self._chains(chain):
            out[a.chain] = a.build()
        out['ok'] = all(v['ok'] for v in out.values())
        return out

    def test(self, chain='all'):
        """Each chain's own native suite (snforge, cargo, forge, ...) — the
        same cases everywhere plus fixtures pinning the port to Cairo
        output. Results are cached for the TESTS tab."""
        out = {a.chain: a.test() for a in self._chains(chain)}
        cache = load(util.TESTS, {})
        for c, r in out.items():
            cache[c] = {'at': int(time.time()), 'ok': r['ok'],
                        'passed': r.get('passed'), 'failed': r.get('failed'),
                        'tests': [list(t) for t in r.get('tests', [])]}
        save(util.TESTS, cache)
        out['ok'] = all(v['ok'] for v in out.values())
        out['passed'] = sum(v.get('passed') or 0 for v in out.values()
                            if isinstance(v, dict))
        out['failed'] = sum(v.get('failed') or 0 for v in out.values()
                            if isinstance(v, dict))
        return out

    def tests(self):
        """The last test run per chain (public: names + pass/fail only)."""
        return load(util.TESTS, {})

    # ---- the code, the paper, the numbers (all public reads) ---------------

    def _sources(self):
        groups = [SHARED_SOURCES[0]]
        for a in NETS:
            rel = os.path.relpath(a.dir, ROOT)
            for group, paths in a.sources:
                groups.append((group, a.chain, [(os.path.join(rel, p), n)
                                                for p, n in paths]))
        return groups + SHARED_SOURCES[1:]

    def files(self):
        """Every source file of every version, grouped, with line counts."""
        out = []
        for group, chain, paths in self._sources():
            rows = []
            for rel, note in paths:
                fp = os.path.join(ROOT, rel)
                if os.path.exists(fp):
                    rows.append({'path': rel, 'note': note,
                                 'lines': sum(1 for _ in open(fp))})
            out.append({'group': group, 'chain': chain, 'files': rows})
        return out

    def source(self, path):
        """One whitelisted source file (never data/ or keys)."""
        allowed = {r for _, _, ps in self._sources() for r, _ in ps}
        if path not in allowed:
            raise ValueError(f'not a published source file: {path!r}')
        return {'path': path, 'text': open(os.path.join(ROOT, path)).read()}

    def whitepaper(self, kind='technical'):
        """The paper: kind=technical (WHITEPAPER.md, LaTeX math) or
        kind=simple (PAPER_SIMPLE.md, plain language)."""
        p = os.path.join(ROOT, 'PAPER_SIMPLE.md' if kind == 'simple' else 'WHITEPAPER.md')
        return open(p).read() if os.path.exists(p) else None

    def gas(self):
        """NEAR verification gas by ring size (measured by gas_sweep)."""
        return self.costs().get('near')

    def costs(self):
        """Verification cost by ring size, per chain, as last measured
        (gas_sweep, or the chain's own suite — eth's forge test)."""
        return {a.chain: load(_gas_path(a.chain), a.default_costs) for a in NETS}

    def gas_sweep(self, sizes='4,8,16,24,32', chain='near'):
        """Measure submit cost per ring size on a chain's LOCAL net: fresh
        deploy, len(sizes) miners each submit with a ring of that size."""
        a = NETS.chain(chain)
        net = a.local
        sizes = [int(x) for x in str(sizes).split(',')]
        st = a.up('start')
        if not st.get('up'):
            return {'ok': False, **st}
        d = a.deploy(net, tempo=a.sweep_tempo, min_ring=3)
        if not d.get('ok'):
            return d
        names = [f'ring-m{i}' for i in range(max(sizes))]
        for n in names:
            self.keygen(n)
            self.register(n, 'miner', net)
        a.open_epoch(net, d['tempo'], lambda *x, **k: None)
        rows = []
        for i, k in enumerate(sizes):
            t0 = time.time()
            try:
                r = self.submit(names[i], f'cost {k}', ring_size=k, network=net)
                rows.append({'ring': k, **a.cost(r),
                             'seconds': round(time.time() - t0, 1)})
            except Exception as e:  # noqa: BLE001
                rows.append({'ring': k, 'error': str(e)[:200]})
        out = {'at': int(time.time()), 'max_ring': a.max_ring,
               **a.sweep_meta, 'rows': rows}
        save(_gas_path(chain), out)
        return out

    def compare(self):
        """Every version side by side: each chain's last full epoch, its
        tests, and the cost tables."""
        out = {}
        for a in NETS:
            sim = self.last_sim(a.chain) or {}
            res = sim.get('result') or {}
            ev = sim.get('events') or []
            acts = [e for e in ev
                    if e['kind'] in ('submit', 'score', 'settle', 'claim')]
            dep = next((e for e in ev if e['kind'] == 'deploy'), {})
            row = {'at': sim.get('at'), 'seconds': res.get('seconds'),
                   'contract': dep.get('address'),
                   'double_claim': res.get('double_claim'),
                   'actions': len(acts),
                   'ring': next((e.get('ring') for e in acts if e.get('ring')),
                                None),
                   'cost_fields': list(a.cost_fields),
                   'tests': self.tests().get(a.chain)}
            for k in ('tgas', 'gas', 'cu', 'units', 'txs', 'steps', 'ec_op'):
                row[k] = [e.get(k) for e in acts if e.get(k)]
            row['cu_max'] = max([e.get('cu_max') or 0 for e in acts] or [0])
            out[a.chain] = row
        out['gas'] = self.gas()
        out['costs'] = self.costs()
        return out

    def economics(self, fresh=False):
        """Mainnet dollars per action and what an epoch can hold, per chain:
        live gas + token price (cached 10 min) applied to the costs measured
        on the local chain. Per action kind (ring of the last epoch) and by
        ring size; block time -> blocks in the 1 h minimum epoch; block
        capacity -> how many actions one block can carry."""
        q = fees.quote(NETS, fresh=str(fresh).lower() in ('1', 'true', 'yes'))
        tables = self.costs()
        out = {'at': q.get('at'), 'min_epoch_s': fees.MIN_EPOCH_S,
               'price_error': q.get('price_error'), 'chains': {}}
        for a in NETS:
            m = (q.get('chains') or {}).get(a.chain)
            if not m or not m.get('gas'):
                continue
            usd = m.get('usd')

            def price(cost):
                n = a.fee(cost, m)
                return {'native': n, 'usd': n * usd if n is not None and usd
                        else None}
            ev = (self.last_sim(a.chain) or {}).get('events') or []
            kinds, ring = {}, None
            for k in ('submit', 'score', 'settle', 'claim', 'register'):
                rows = [a.cost(e) for e in ev if e['kind'] == k]
                rows = [r for r in rows if r]
                if not rows:
                    continue
                ring = ring or next((e.get('ring') for e in ev
                                     if e['kind'] == k and e.get('ring')), None)
                avg = {f: sum(r.get(f) or 0 for r in rows) / len(rows)
                       for f in a.cost_fields}
                load = a.load(avg)
                kinds[k] = {**price(avg), 'load': load, 'n': len(rows),
                            'ms': next((e['ms'] for e in ev
                                        if e['kind'] == k and e.get('ms')), None)}
            by_ring = []
            for r in (tables.get(a.chain) or {}).get('rows') or []:
                c = a.cost(r)
                if c and not r.get('error'):
                    by_ring.append({'ring': r['ring'], **price(c),
                                    'load': a.load(c)})
            bs, cap = m.get('block_seconds'), m.get('block_capacity')
            act = kinds.get('score') or kinds.get('submit') or {}
            if not by_ring and ring and act.get('usd'):
                # no sweep table: verification is linear in the ring
                by_ring = [{'ring': r, 'usd': act['usd'] * r / ring,
                            'native': act['native'] * r / ring, 'est': True}
                           for r in (4, 8, 16, 32, 64) if r <= a.max_ring]
            per_block = (cap / act['load']) if cap and act.get('load') else None
            out['chains'][a.chain] = {
                'symbol': a.symbol, 'usd_price': usd, 'stale': m.get('stale'),
                'error': m.get('error'), 'gas': m['gas'], 'note': m.get('note'),
                'block_seconds': bs, 'min_tempo': fees.min_tempo(bs),
                'block_capacity': cap, 'capacity_unit': m.get('capacity_unit'),
                'tx_cap': m.get('tx_cap'), 'rounds': a.rounds,
                'capacity_estimated': bool(m.get('capacity_estimated')),
                'max_ring': a.max_ring, 'ring': ring, 'actions': kinds,
                'by_ring': by_ring,
                'actions_per_block': per_block,
                'actions_per_hour': per_block * 3600 / bs
                if per_block and bs else None}
        return out

    def _priced(self, a, row, m):
        """(units, caps) for one chain: forecast.units() with the modeled
        validity proof and the minimal transaction priced through the
        adapter's own fee(), plus the per-transaction limit a rung has to fit
        in (a proof that does not fit cannot be used, however cheap it is)."""
        def usd(cost):
            if not cost or not m.get('usd'):
                return None
            n = a.fee(cost, m)
            return n * m['usd'] if n else None
        pc, tc = fc.PROOF_COST.get(a.chain), fc.TX_COST.get(a.chain)
        return fc.units(row, usd(pc), usd(tc)), {
            'tx_cap': row.get('tx_cap'), 'unit': row.get('capacity_unit'),
            'proof_load': a.load(pc) if pc else None,
            'proof_estimated': pc is not None, 'tx_estimated': tc is not None}

    def _chain_units(self, chain=None):
        """{chain: (adapter, economics row, units, caps)} for every priced
        chain — what feasible()/scale()/forecast() all start from."""
        eco = self.economics()
        q = (fees.quote(NETS).get('chains') or {})
        out = {}
        for a in NETS:
            row = eco['chains'].get(a.chain)
            if not row or (chain and a.chain != chain):
                continue
            u, caps = self._priced(a, row, q.get(a.chain) or {})
            if u:
                out[a.chain] = (a, row, u, caps)
        return eco, out

    def feasible(self, target=1.0, miners=256, validators=64, chain=None,
                 ring=16, hours='1,2,4,6,12,24', epoch_hours=None, k=None,
                 c=None):
        """CAN IT BE AFFORDED? Every chain x strategy x epoch length against a
        $/week budget for an M x V subnet (default: the module's own bar, $1 a
        week for 256 miners and 64 validators). Per cell PASS/FAIL, per chain
        the cheapest passing config (`best`) and the largest subnet that
        config still affords. See core/budget."""
        eco, chains = self._chain_units(chain)
        hs = tuple(sorted({int(float(x)) for x in str(hours).split(',')
                           if x.strip()}) or bg.HOURS)
        if epoch_hours:
            hs = (int(float(epoch_hours)),)
        out = {'at': eco.get('at'), 'target': float(target),
               'miners': int(miners), 'validators': int(validators),
               'chains': {}, 'feasible_on': []}
        for name, (a, row, u, caps) in chains.items():
            r = bg.feasible(u, float(target), int(miners), int(validators),
                            min(int(ring), a.max_ring), hs, K=k and int(k),
                            C=int(c or fc.COMMITTEE), caps=caps)
            out['chains'][name] = {'symbol': a.symbol, 'stale': row.get('stale'),
                                   'usd_price': row.get('usd_price'),
                                   'max_ring': a.max_ring, **caps, **r}
            if r['feasible']:
                out['feasible_on'].append(name)
        passing = [(c['best']['usd_week'], n) for n, c in out['chains'].items()
                   if c.get('best')]
        out['cheapest'] = min(passing)[1] if passing else None
        return out

    def scale(self, chain=None, sizes='64,256,1024,4096,10000,16384,65536',
              ratio=4, ring=16, epoch_hours=1, target=1.0, k=None, c=None):
        """HOW THE PRICE SCALES with the set sizes, past 10,000 of each: one
        row per size (M = n miners, V = n / ratio validators), every strategy
        priced per week, plus each strategy's measured growth order (the
        log-log slope) and the largest n it keeps under `target`.
        See core/budget."""
        eco, chains = self._chain_units(chain)
        ns = tuple(sorted({int(float(x)) for x in str(sizes).split(',')
                           if x.strip()}) or bg.SIZES)
        out = {'at': eco.get('at'), 'sizes': list(ns), 'ratio': int(ratio),
               'target': float(target), 'chains': {}}
        for name, (a, row, u, caps) in chains.items():
            out['chains'][name] = {
                'symbol': a.symbol, 'usd_price': row.get('usd_price'),
                'stale': row.get('stale'),
                **bg.scale(u, ns, int(ratio), min(int(ring), a.max_ring),
                           epoch_hours=float(epoch_hours), K=k and int(k),
                           C=int(c or fc.COMMITTEE), target=float(target))}
        return out

    def rules(self):
        """MODULAR CONSENSUS: every settlement rule a subnet can pick, what it
        does to a tag's scores and which aggregate it needs (core/consensus).
        Each one is a pure function of the score histogram, which is exactly
        what secure aggregation can produce without seeing a single score."""
        return {'rules': cs.rules(), 'default': 'yuma',
                'max_score': cs.MAX_SCORE,
                'note': 'rule=<name> on yuma(), aggregate() and simulate()'}

    def aggregate(self, validators=8, tags=4, rule='yuma', drop=0,
                  emission=1000, scores=None, seed=7):
        """SIMPLE SECURE AGGREGATION, end to end: every validator masks its
        one-hot score vector with pads that cancel pairwise, publishes it, and
        the SUM — the histogram `rule` settles on — falls out without any one
        score ever being revealed. `drop` validators go silent to exercise the
        recovery round. Returns the aggregate, the plain scores it is checked
        against, and what went on the wire. See core/secagg."""
        if isinstance(scores, str):
            scores = json.loads(scores)
        v = min(max(int(validators), 1), 48)
        return sa.demo(v, min(max(int(tags), 1), 32), scores, rule,
                       int(drop), int(emission), int(seed))

    def forecast(self, chain=None, miners='16,256', validators='4,64',
                 months=12, shape='exp', ring=16, epoch_hours=1, k=None, f=0.25):
        """Cost over time as the subnet grows: miners/validators = 'start,end'
        over `months` (shape exp | linear | logistic), every epoch priced at
        today's mainnet gas for today's contract (baseline) and for each
        compression step — modeled, see core/forecast: the linking ladder
        (batch, lazy, proof) and the privacy-preserving one (committee,
        private), each with what an observer learns (`privacy`).
        No chain = every priced chain, side by side."""
        eco, chains = self._chain_units(chain)
        pair = lambda v: tuple(int(x) for x in str(v).replace(' ', '').split(',')[:2])  # noqa: E731
        M, V = pair(miners), pair(validators)
        if len(M) == 1:
            M = (M[0], M[0])
        if len(V) == 1:
            V = (V[0], V[0])
        out = {'at': eco.get('at'), 'miners': M, 'validators': V,
               'months': int(months), 'shape': shape, 'chains': {},
               'entry_of_settle': fc.ENTRY_OF_SETTLE,
               'prover_usd_per_mstep': fc.PROVER_USD_PER_MSTEP}
        for name, (a, row, u, caps) in chains.items():
            out['chains'][name] = {
                'symbol': a.symbol, **caps,
                **fc.project(u, M, V, int(months), shape, int(ring),
                             float(epoch_hours), int(k) if k else None,
                             f=float(f))}
        return out

    def experiment(self, chain='near', hours='1,2,4,6,12,24', ring=16,
                   strategy='baseline', share=0.1, budget_day=None,
                   trials=200, vol=0.5, p_min=0.8, seed=7):
        """ONE chain: sweep epoch length x miners x validators. Per epoch
        length the largest subnet that fits `share` of the chain's blockspace
        (and $/day <= budget_day if set), and a Monte Carlo of $ per epoch
        (gas lognormal(vol), turnout uniform(p_min, 1)) on a miners x
        validators grid. See core/experiment."""
        a = NETS.chain(chain)
        eco = self.economics()
        row = eco['chains'].get(a.chain)
        if not row:
            return {'ok': False, 'chain': a.chain,
                    'error': f'{a.chain} has no mainnet price (no token or no '
                             'priced run yet)'}
        q = (fees.quote(NETS).get('chains') or {}).get(a.chain) or {}
        u, caps = self._priced(a, row, q)
        if not u:
            return {'ok': False, 'chain': a.chain, 'error': 'no priced run yet'}
        lu = ex.load_units(row, caps['proof_load'])
        ints = lambda v: tuple(sorted({int(float(x)) for x in str(v).split(',') if x.strip()}))  # noqa: E731
        out = ex.run(row, u, lu, hours=ints(hours) or ex.HOURS, ring=int(ring),
                     strategy=strategy, share=float(share),
                     budget_day=float(budget_day) if budget_day not in (None, '', '0') else None,
                     trials=max(20, min(int(trials), 1000)), vol=float(vol),
                     p_min=min(max(float(p_min), 0.05), 1.0), seed=int(seed))
        return {'ok': True, 'chain': a.chain, 'symbol': a.symbol,
                'usd_price': row.get('usd_price'), 'at': eco.get('at'),
                'max_ring': a.max_ring, 'modeled': strategy != 'baseline', **out}

    # ---- keys + the signer (chain-independent) -------------------------------

    def keygen(self, name, secret=None):
        """Create a member key. Without `secret`, a fresh random one is
        drawn. Only the public key ever leaves this box."""
        return SIGNER.keygen(name, secret)

    def keys(self):
        return SIGNER.keys()

    def pubkey(self, name):
        return SIGNER.pubkey(name)

    def sign(self, name, scope, msg, ring):
        """Low-level: ring-sign `msg` in `scope` as key `name`. `ring` is a
        list of {x,y} (yours included, any order — the prover sorts)."""
        return SIGNER.sign(name, scope, msg, ring)

    def verify(self, scope, msg, sig):
        """Off-chain check of a sign() result (same code as the contract)."""
        return {'valid': SIGNER.verify(scope, msg, sig)}

    def scope(self, kind, subnet, epoch, tag=0):
        """mine scope = H('zuma-mine', subnet, epoch); score scope adds tag."""
        return SIGNER.scope(kind, subnet, epoch, tag)

    def hash_point(self, scope):
        """Hp(scope): the hint verifiers check instead of a square root."""
        return SIGNER.hash_point(scope)

    def mytag(self, name, epoch, network='devnet'):
        """Your own tag for an epoch — only the key holder can compute it."""
        sc = NETS.net(network).mine_scope(network, epoch)
        return {'epoch': int(epoch), 'tag': hex(SIGNER.tag(name, sc))}

    # ---- chain dispatch --------------------------------------------------------

    def devnet(self, action='status', chain='starknet'):
        """start | stop | reset | status of a chain's local net (starknet-
        devnet :51502, near-sandbox :51503, anvil :51504, solana :51510)."""
        return NETS.chain(chain).up(action)

    def deploy(self, network='devnet', tempo=None, min_ring=3, emission=1000,
               **kw):
        """Deploy the subnet contract. Local nets need nothing; public ones
        take the adapter's own kwargs (starknet: account=, relayer=,
        accounts_file=, rpc=; near-testnet: account=). tempo = blocks per
        epoch; on a public net an epoch lasts at least fees.MIN_EPOCH_S (1 h)
        of that chain's measured mainnet block time, and that is the default."""
        a = NETS.net(network)
        if network != a.local:
            bs = (fees.quote(NETS)['chains'].get(a.chain) or {}).get(
                'block_seconds')
            floor = fees.min_tempo(bs)
            if floor and tempo is None:
                tempo = floor
            elif floor and int(tempo) < floor:
                raise ValueError(
                    f'tempo {tempo} blocks is {int(tempo) * bs / 60:.0f} min on '
                    f'{a.title} ({bs:.2f}s blocks); the minimum epoch is '
                    f'{fees.MIN_EPOCH_S // 60} min = {floor} blocks')
        return a.deploy(network, tempo=tempo, min_ring=min_ring,
                        emission=emission, **kw)

    def register(self, name, role, network='devnet'):
        """Owner registers a member key (public: set membership is what
        makes the subnet permissioned). role = miner | validator."""
        r = NETS.net(network).register(network, ROLES[str(role)],
                                       self.pubkey(name))
        return {'ok': True, 'name': name, 'role': role, **r}

    def set_open(self, open=True, network='devnet'):
        """Let anyone register (open) or only the owner (closed)."""
        flag = str(open).lower() in ('1', 'true', 'yes', 'on')
        return {'open': flag, 'tx': NETS.net(network).set_open(network, flag)}

    def members(self, role, network='devnet'):
        return NETS.net(network).members(network, ROLES[str(role)])

    def epoch(self, network='devnet'):
        return NETS.net(network).epoch(network)

    def _min_ring(self, network):
        dep = load(util.DEPLOYS, {}).get(network) or {}
        return int(dep.get('min_ring') or guard.MIN_RING)

    def _tally(self, a, network, epoch=None):
        """Best-effort public read for the pre-flight checks: None when the
        chain can't answer, so a flaky read never blocks a valid action."""
        try:
            return a.tally(network, epoch)
        except Exception:  # noqa: BLE001
            return None

    def submit(self, name, work, ring_size=None, network='devnet', check=True):
        """Miner: post a work commitment for the current epoch, anonymously.
        Returns your tag (validators see only this). check=False skips the
        local pre-flight (core/guard.py) that refuses what the chain would
        revert — a reverted tx still costs the relayer its fee."""
        a = NETS.net(network)
        ring = a.ring(network, name, 'miner', ring_size)
        if str(check).lower() not in ('0', 'false', 'no', 'off'):
            guard.ring_size(ring, self._min_ring(network))
        return a.submit(network, name, work, ring)

    def score(self, name, tag, score, ring_size=None, network='devnet',
              check=True):
        """Validator: score a tag (0-100) in the current epoch, anonymously."""
        a = NETS.net(network)
        ring = a.ring(network, name, 'validator', ring_size)
        if str(check).lower() not in ('0', 'false', 'no', 'off'):
            score = guard.score_value(score)
            guard.ring_size(ring, self._min_ring(network))
            t = self._tally(a, network)
            if t is not None:
                guard.tag_open(t, tag)
        return a.score(network, name, tag, score, ring)

    def claim(self, name, epoch, recipient, ring_size=None, network='devnet',
              check=True):
        """Miner: after `epoch` closed, credit your share to `recipient`
        (in the chain's own address format)."""
        a = NETS.net(network)
        ring = a.ring(network, name, 'miner', ring_size)
        if str(check).lower() not in ('0', 'false', 'no', 'off'):
            guard.ring_size(ring, self._min_ring(network))
            t = self._tally(a, network, epoch)
            if t is not None:
                guard.claimable(t, self.mytag(name, epoch, network)['tag'])
        return a.claim(network, name, epoch, recipient, ring)

    def preflight(self, action, name, network='devnet', tag=None, score=None,
                  epoch=None, ring_size=None):
        """Dry-run the checks submit/score/claim would make, without signing
        or posting: {'ok': True} or {'ok': False, 'reason': ...}."""
        a = NETS.net(network)
        role = 'validator' if action == 'score' else 'miner'
        try:
            ring = a.ring(network, name, role, ring_size)
            guard.ring_size(ring, self._min_ring(network))
            if action == 'score':
                guard.score_value(score)
                t = self._tally(a, network)
                if t is not None:
                    guard.tag_open(t, tag)
            elif action == 'claim':
                t = self._tally(a, network, epoch)
                if t is not None:
                    guard.claimable(t, self.mytag(name, epoch, network)['tag'])
            elif action != 'submit':
                raise ValueError(f'action must be submit | score | claim, got {action!r}')
        except ValueError as e:
            return {'ok': False, 'action': action, 'reason': str(e)}
        return {'ok': True, 'action': action, 'ring': len(ring)}

    def anonymity(self, role='miner', network='devnet', ring_size=None):
        """How big a crowd each member of `role` hides in on this net: ring
        size, rings, bits of anonymity and plain-language warnings."""
        a = NETS.net(network)
        n = len(a.members(network, ROLES[str(role)]))
        return {'role': role, 'network': network,
                **guard.anonymity(n, ring_size, a.max_ring,
                                  self._min_ring(network))}

    def settle(self, epoch, tag=None, network='devnet'):
        """Anyone, after `epoch` closed: run Yuma on chain for `tag`, or for
        every tag not settled yet. Claims open once all tags are settled."""
        a = NETS.net(network)
        if tag is not None:
            return a.settle(network, int(epoch), tag)
        return {'epoch': int(epoch), 'settled': [
            a.settle(network, int(epoch), t['tag'])
            for t in a.tally(network, epoch)['tags'] if not t.get('settled')]}

    def yuma(self, scores, validators=None, emission=1000, rule='yuma'):
        """Yuma by hand, off chain: scores = {tag: [s, ...]} (or JSON of it).
        What a chain's settle() must produce for the same votes. rule= picks
        any modular-consensus rule (see rules()); 'yuma' is the chain's."""
        if isinstance(scores, str):
            scores = json.loads(scores)
        v = validators or max([len(x) for x in scores.values()] or [0])
        return cs.epoch(scores, int(v), int(emission), rule)

    def tally(self, epoch=None, network='devnet'):
        """Every tag in an epoch: score sum/count and its Yuma consensus +
        clipped rank once settled — never identities."""
        return NETS.net(network).tally(network, epoch)

    def earned(self, recipient, network='devnet'):
        return NETS.net(network).earned(network, recipient)

    def board(self, network='devnet', epochs=6):
        """Everything the console draws in one read: params, epoch clock,
        both member SETS (public keys only), and the recent epochs' tags.
        Never touches a secret — it is exactly what any chain reader sees."""
        return NETS.net(network).board(network, epochs)

    def status(self, network='devnet'):
        out = {'health': self.health(), 'keys': self.keys(),
               'deployments': load(util.DEPLOYS, {})}
        if network in out['deployments']:
            try:
                out['epoch'] = self.epoch(network)
            except Exception as e:  # noqa: BLE001
                out['epoch_error'] = str(e)
        return out

    # ---- end-to-end demo -----------------------------------------------------

    def simulate(self, miners=4, validators=4, on_event=None,
                 chain='starknet'):
        """Full anonymous epoch on a chain's LOCAL net: deploy, register sim
        keys, 3 miners submit, every validator scores every tag (the first
        validator pumps the weakest tag to 100 — Yuma clips it), roll the
        epoch, settle Yuma per tag, one miner claims, and its replay is
        rejected. The settled numbers are checked against core/yuma.py. Same keys,
        same prover, same signatures on every chain — only the verifier
        differs. `on_event(dict)` streams each step (the console animates
        them); the run is kept in data/last_sim[_<chain>].json."""
        a = NETS.chain(chain)
        net = a.local
        t0 = time.time()
        events = []

        def ev(kind, **kw):
            e = {'kind': kind, 't': round(time.time() - t0, 2),
                 'chain': chain, **kw}
            events.append(e)
            if on_event:
                on_event(e)

        miners, validators = int(miners), int(validators)
        if not (3 <= miners <= a.max_ring and 3 <= validators <= a.max_ring):
            raise ValueError(f'miners/validators must be 3..{a.max_ring} '
                             '(min_ring is 3)')
        st = a.up('start')
        if not st.get('up'):
            ev('error', error=st.get('error'), stage='local chain')
            return {'ok': False, **st}
        ev('devnet', block=a.block(net))
        ev('deploying')
        tempo = a.sim_tempo
        dep = a.deploy(net, tempo=tempo, min_ring=3, emission=1000)
        if not dep.get('ok'):
            ev('error', error=dep.get('error'), stage=dep.get('stage'))
            return dep
        contract = a.contract_id(dep)
        ev('deploy', address=contract, tempo=dep['tempo'],
           min_ring=dep['min_ring'], emission=dep['emission'],
           **{k: dep.get(k) for k in a.deploy_fields})
        mk = [f'sim-m{i}' for i in range(miners)]
        vk = [f'sim-v{i}' for i in range(validators)]
        for n in mk + vk:
            self.keygen(n)  # no-op if it exists
        for role, names in (('miner', mk), ('validator', vk)):
            for n in names:
                r = self.register(n, role, net)
                ev('register', role=role, x=self.pubkey(n)['x'], **a.cost(r))
        a.open_epoch(net, tempo, ev)
        subs = []
        for n in mk[:3]:
            s0 = time.time()
            s = self.submit(n, f'work from {n}', network=net)
            subs.append(s)
            ev('submit', epoch=s['epoch'], tag=s['tag'], ring=s['ring_size'],
               ms=int((time.time() - s0) * 1000), tx=s['tx'], **a.cost(s))
        e = subs[0]['epoch']
        votes = {s['tag']: [] for s in subs}
        for j, v in enumerate(vk):
            for i, s in enumerate(subs):
                pump = j == 0 and i == len(subs) - 1  # one dishonest vote
                r = self.score(v, s['tag'], 100 if pump else 90 - 20 * i,
                               network=net)
                votes[s['tag']].append(r['score'])
                ev('score', tag=s['tag'], score=r['score'],
                   nullifier=r['nullifier'], ring=len(vk), tx=r['tx'],
                   **a.cost(r))
        a.close_epoch(net, e, tempo, ev)
        ev('close', epoch=e, block=a.block(net))
        want = yuma.epoch(votes, validators, dep['emission'])
        for s in subs:
            r = a.settle(net, e, s['tag'])
            w = want['tags'][s['tag']]
            ev('settle', tag=s['tag'], consensus=r['consensus'],
               rank=r['rank'], raw=w['raw'], share=w['share'],
               ok=(r['consensus'], r['rank']) == (w['consensus'], w['rank']),
               tx=r['tx'], **a.cost(r))
        payee = a.payee(net, dep)
        c = self.claim(mk[0], e, payee, network=net)
        ev('claim', epoch=e, tag=c['tag'], recipient=c['recipient'],
           earned=c['earned'], expected=want['tags'][c['tag']]['share'],
           tx=c['tx'], **a.cost(c))
        # the replay must fail: same key, same epoch -> same tag
        try:
            self.claim(mk[0], e, payee, network=net)
            replay = 'ACCEPTED (bug)'
        except RuntimeError as ex:
            replay = f'rejected: {ex}'
        ev('replay', ok=replay.startswith('rejected'), result=replay)
        audit = a.audit(net)
        if audit is not None:
            ev('audit', **audit)
        out = {'ok': True, 'chain': chain, 'network': net,
               'contract': contract, 'epoch': e,
               'steps': [f"{x['kind']}" for x in events],
               'tally': self.tally(e, net), 'claim': c, 'yuma': want,
               'yuma_ok': all(x.get('ok') for x in events
                              if x['kind'] == 'settle'),
               'double_claim': replay,
               **({'audit': audit} if audit is not None else {}),
               'seconds': round(time.time() - t0, 1)}
        ev('done', seconds=out['seconds'])
        save(_sim_path(chain), {'at': int(time.time()), 'chain': chain,
                                'events': events, 'result': out})
        return out

    def last_sim(self, chain='starknet'):
        """The most recent simulate() run on `chain`, event by event."""
        return load(_sim_path(chain), None)

    # ---- HTTP server (console + API) ----------------------------------------

    def serve(self, port=None):
        """Run server.py: the console at /ztao and the JSON API at
        /ztao/api/<fn>, one stdlib process."""
        import subprocess
        port = int(port or self.config['port'])
        subprocess.run([sys.executable, os.path.join(HERE, 'server.py'),
                        str(port)], cwd=HERE)


if __name__ == '__main__':
    m = Mod()
    argv = sys.argv[1:]
    if not argv:
        print(json.dumps(m.status(), indent=2, default=str))
    else:
        fn, kw = argv[0], {}
        for a in argv[1:]:
            k, _, v = a.partition('=')
            kw[k] = v
        print(json.dumps(getattr(m, fn)(**kw), indent=2, default=str))
