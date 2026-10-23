"""What it actually takes to run a validator here — measured, not claimed.

Every number below is read off THIS process and THIS chain at the moment you
ask: resident memory from /proc, the block log's real size on disk, the real
cost of one post-quantum signature check, the real bytes a block weighs. The
published minimums of other chains are quoted beside them for scale, flagged
as what they are — their documentation, not a measurement taken here.

WHY THE NUMBER MATTERS
    A validator's requirements are a franchise fee. When running one costs a
    256 GB machine and two NVMe drives, the set of people who can check the
    chain for themselves is the set of people who can afford that, and
    everyone else is a client of someone who can. Requirements are
    decentralisation, stated in gigabytes.

WHAT MAKES THEM SMALL HERE
    Three deliberate absences.

    No general VM. Seven transaction kinds, no loops, no contract execution
    in consensus (contracts run off chain and prove it, see contracts.py).
    A block is validated by checking signatures and re-deriving a Merkle
    root, so the CPU cost is hashes and witness checks, and nothing else.

    State is rented, not bought. Every byte in the store pays per byte-hour
    against an escrow and expires when the escrow runs dry, so the state has
    a natural ceiling instead of a monotonic climb — the thing that actually
    kills a validator's disk on chains where storage is a one-time payment.

    Recursion. A subchain of ten thousand keys occupies 32 bytes of the
    parent's state (subchain.py). An appchain's growth lands on the
    appchain's own sequencer, not on every validator of the root chain.

    The implementation is stdlib Python with zero dependencies, which is not
    a performance claim — it is an auditability one: there is no supply
    chain to compromise, and the whole node is a few thousand lines a person
    can read in an afternoon.

HONESTLY
    This is a one-node devnet implementation in pure Python: the witness
    checks are reference implementations and a production node would use the
    wasm verifiers (already the default path) or native code, moving the CPU
    numbers by orders of magnitude in the good direction. The disk and
    bandwidth numbers are structural and would not move.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Published minimums, from each project's own validator documentation. Quoted
# for scale — nothing here is measured by this module.
PUBLISHED = [
    {'chain': 'Bitcoin (full node)', 'cpu': '1 core', 'ram': '2 GB',
     'disk': '~700 GB (pruned 10 GB)', 'net': '~500 GB/mo',
     'note': 'the cheapest serious chain to verify'},
    {'chain': 'Ethereum (validator)', 'cpu': '4 cores', 'ram': '16 GB',
     'disk': '2 TB NVMe SSD', 'net': '~1.2 TB/mo',
     'note': 'execution + consensus client, archive excluded'},
    {'chain': 'Solana (validator)', 'cpu': '12-24 cores', 'ram': '256-512 GB',
     'disk': '2 x 1 TB+ NVMe', 'net': '1 Gbps symmetric',
     'note': 'plus a vote-fee budget to stay in the set'},
    {'chain': 'Cosmos appchain (typical)', 'cpu': '4 cores', 'ram': '16 GB',
     'disk': '500 GB - 1 TB SSD', 'net': '100 Mbps',
     'note': 'per appchain, and each one needs its own validator set'},
]


def _rss():
    """Resident and peak memory of this process, in bytes."""
    out = {}
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    out['rss'] = int(line.split()[1]) * 1024
                elif line.startswith('VmHWM:'):
                    out['peak'] = int(line.split()[1]) * 1024
    except OSError:
        pass
    return out


def _tree_bytes(root, exts=('.py',)):
    total, files, lines = 0, 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ('__pycache__', 'target', 'node_modules')]
        for fn in filenames:
            if not fn.endswith(exts):
                continue
            p = os.path.join(dirpath, fn)
            try:
                total += os.path.getsize(p)
                with open(p, 'rb') as f:
                    lines += sum(1 for _ in f)
                files += 1
            except OSError:
                continue
    return {'bytes': total, 'files': files, 'lines': lines}


def _file_bytes(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _dir_bytes(p):
    total = 0
    for dirpath, _dirs, files in os.walk(p):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def _human(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} TB'


def hash_bench(ms=60):
    """SHA3-256 throughput — the only primitive consensus here depends on."""
    data = b'x' * 1024
    t0 = time.perf_counter()
    n = 0
    while (time.perf_counter() - t0) * 1000 < ms:
        for _ in range(200):
            hashlib.sha3_256(data).digest()
        n += 200
    dt = time.perf_counter() - t0
    return {'hashes_per_second': int(n / dt), 'block_bytes': 1024,
            'mb_per_second': round(n * 1024 / dt / 1e6, 1)}


def witness_bench(schemes=('ML-DSA-44',)):
    """What one witness check costs on this box, per key type."""
    sys.path.append(HERE)
    from pq import algos
    out = []
    msg = b'postquant-footprint'
    for name in schemes:
        algo = algos.get(name)
        if algo is None:
            continue
        try:
            pk, sk = algo.keygen(bytes(range(32)))
            t0 = time.perf_counter()
            sig = algo.sign(sk, msg, b'bench')
            t1 = time.perf_counter()
            ok = algo.verify(pk, msg, sig, b'bench')
            t2 = time.perf_counter()
        except Exception as e:                             # noqa: BLE001
            out.append({'scheme': name, 'error': str(e)})
            continue
        out.append({
            'scheme': name, 'ok': bool(ok),
            'sig_bytes': algo.sizes['sig'], 'pk_bytes': algo.sizes['pk'],
            'sign_ms': round((t1 - t0) * 1000, 1),
            'verify_ms': round((t2 - t1) * 1000, 1),
            'verifies_per_second': round(1 / max(t2 - t1, 1e-9), 1),
        })
    return out


def chain_disk(node):
    """The block log and the snapshot, as they sit on disk."""
    d = node.dir
    log = os.path.join(d, 'blocks.jsonl')
    log_bytes = _file_bytes(log)
    height = node.blocks[-1]['height'] if node.blocks else 0
    per_block = log_bytes / max(height + 1, 1)
    first = node.blocks[0].get('timestamp') if node.blocks else None
    last = node.blocks[-1].get('timestamp') if node.blocks else None
    span = max(1, (last or 0) - (first or 0))
    blocks_per_day = (height + 1) / span * 86400
    return {
        'dir': d,
        'log_bytes': log_bytes, 'log': _human(log_bytes),
        'snapshot_bytes': _file_bytes(os.path.join(d, 'state.json')),
        'vault_bytes': _dir_bytes(os.path.join(os.path.dirname(d), 'vault')),
        'height': height, 'blocks': height + 1,
        'bytes_per_block': round(per_block),
        'blocks_per_day': round(blocks_per_day, 1),
        'bytes_per_day': round(per_block * blocks_per_day),
        'growth_per_day': _human(per_block * blocks_per_day),
        'growth_per_year': _human(per_block * blocks_per_day * 365),
        'state_entries': len(node.state.store),
        'state_billable_bytes': node.state.state_bytes(),
        'accounts': len(node.state.accounts),
        'rent_note': 'the store is rented: an entry whose escrow runs out '
                     'expires and is swept, so state bytes have a ceiling '
                     'the fee market sets, not a one-way climb',
    }


def requirements(node, bench=True):
    """The whole footprint: measured, projected, and what to buy."""
    code = _tree_bytes(HERE)
    wasm = _tree_bytes(os.path.join(HERE, 'pq', 'wasm'), ('.wasm',))
    mem = _rss()
    disk = chain_disk(node)
    hashes = hash_bench() if bench else None
    witness = witness_bench() if bench else None

    rss = mem.get('rss') or 120 << 20
    year = disk['bytes_per_day'] * 365
    min_disk = max(1 << 30, int((disk['log_bytes'] + year) * 1.5))
    min_ram = max(256 << 20, int(rss * 2))

    return {
        'what': 'what one validator of this chain needs, measured on this '
                'node right now',
        'measured': {
            'memory': {**mem, 'rss_human': _human(mem.get('rss')),
                       'peak_human': _human(mem.get('peak')),
                       'note': 'one python process: the state machine, the '
                               'block loop, the REST/MCP/agent surface and '
                               'the console, all of it'},
            'code': {**code, 'wasm_verifiers': wasm,
                     'dependencies': 0,
                     'note': 'python standard library only — nothing to '
                             'install, no supply chain, readable in an '
                             'afternoon'},
            'disk': disk,
            'cpu': {'sha3': hashes, 'witness': witness,
                    'note': 'consensus costs hashes and signature checks and '
                            'nothing else: there is no VM in the block path'},
            'network': {
                'bytes_per_block': disk['bytes_per_block'],
                'per_day': _human(disk['bytes_per_day']),
                'per_month': _human(disk['bytes_per_day'] * 30),
                'note': 'a block is its header, its transactions and their '
                        'witnesses; post-quantum signatures are the bulk of '
                        'it, which is why they are priced per byte',
            },
        },
        'minimum': {
            'cpu': '1 core',
            'ram': _human(min_ram),
            'disk': _human(min_disk),
            'network': 'anything; a phone tether is enough at this rate',
            'gpu': 'none, ever',
            'dependencies': 'python 3 (+ a node runtime only if you want '
                            'wasm witness verification)',
            'derivation': f'ram = 2x measured RSS ({_human(rss)}), '
                          f'disk = 1.5x (log today + one year at the '
                          f'current rate), floor 256 MB / 1 GB',
        },
        'published_elsewhere': PUBLISHED,
        'published_note': 'quoted from each project\'s own validator docs '
                          'for scale — not measured here, and not an apples '
                          'comparison: those chains carry general-purpose '
                          'execution and far more traffic. The structural '
                          'difference is that none of their numbers can be '
                          'small, and this one cannot be large.',
        'why_small': [
            'no general-purpose VM in the consensus path — seven transaction '
            'kinds, no loops, contracts run off chain and prove it',
            'state is rented per byte-hour and expires, so the store has a '
            'ceiling instead of a ratchet',
            'a subchain of any size occupies 32 bytes of this chain',
            'zero dependencies: the node is the standard library',
        ],
        'why_it_matters': 'validator requirements are the franchise. A chain '
                          'you can only verify on a 256 GB machine is a '
                          'chain most people take on trust. This one is '
                          'verifiable on hardware people already own, which '
                          'is what makes "run your own" a real answer rather '
                          'than a slogan.',
        'appchains': appchains(node, min_ram, min_disk),
    }


def appchains(node, min_ram, min_disk):
    """Why a footprint this small plus recursion is an appchain story."""
    import state as S
    base = node.state.base_fee
    slot = node.state.quote('chain/yourapp', '00' * 32, 'hash',
                            seconds=86400 * 30, base_fee=base)
    return {
        'claim': 'an appchain here is one key, not one network',
        'the_old_way': [
            'stand up a validator set (people, machines, incentives)',
            'bootstrap security — a new chain is as safe as its smallest '
            'honest majority',
            'run a bridge, and own its failure modes',
            'per the table above, every validator of yours needs its own '
            'machine',
        ],
        'here': [
            'pq_subchain action=create name=yourapp — the chain exists',
            'your users sign their own writes with their own post-quantum '
            'wallets; you order them',
            'pq_subchain action=seal — one signed head',
            f'anchor it: one L1 key, 32 bytes, '
            f'{S.to_pq(slot["total"])} PQ for 30 days at the current base '
            'fee',
            'anyone proves any leaf of your chain against the L1 state root '
            'with SHA3-256 and no permission',
        ],
        'anchor_cost': {
            'key': 'chain/yourapp', 'bytes': 32,
            'total_nq': slot['total'], 'total_pq': S.to_pq(slot['total']),
            'days': 30,
            'note': 'the same bill whether your chain holds ten keys or ten '
                    'million — the parent stores one hash',
        },
        'what_you_get': [
            'anchoring: your head is in a chain with its own validator set, '
            'so rewriting your history means rewriting theirs',
            'proofs: a leaf eight levels down verifies against one root with '
            'about log2(keys) hashes per level',
            'composition: your appchain can sell slots to appchains of its '
            'own, recursively, at 32 bytes each',
            'the same post-quantum key types all the way down — ML-DSA, '
            'SLH-DSA, a composite, or a private key type nobody can even '
            'identify',
        ],
        'what_you_owe': [
            'ordering and data availability are yours: a batch nobody kept '
            'is a root nobody can expand',
            'the L1 does not execute your transitions — it commits to them. '
            'A fraudulent head is refutable by replay, not prevented',
            'the sequencer can censor; censorship is visible in the signed '
            'batch that left you out',
        ],
        'validator_for_one': {
            'ram': _human(min_ram), 'disk': _human(min_disk),
            'cpu': '1 core',
            'note': 'and if you want your appchain to be a chain in its own '
                    'right rather than a subchain, this is the whole bill — '
                    'which is the other half of why appchains get cheap',
        },
    }
