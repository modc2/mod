"""The one place bloctime.yuma's callers (mod.py, the bloctime API) reach the
engine and the chain. Loaded by path so nothing else on sys.path named
`yuma` can shadow it."""
import importlib.util
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
BLOCTIME = HERE.parent
CONFIG = HERE / 'config.json'

_spec = importlib.util.spec_from_file_location('bloctime_yuma_engine', HERE / 'yuma.py')
Y = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Y)
ONE = Y.ONE

_lock = threading.RLock()
_power_cache: dict = {}
POWER_TTL = 30


def _cfg():
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def _save_cfg(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=4) + '\n')


def _bloctime_net():
    """(rpc, bloctime address) of the BlocTime the parent module serves:
    config.json's network entry, else deployment.json."""
    cfg = json.loads((BLOCTIME / 'config.json').read_text())
    c = cfg.get('contracts', {}).get(cfg.get('network', 'testnet'), {})
    addr = c.get('bloctime')
    if not addr and (BLOCTIME / 'deployment.json').exists():
        addr = json.loads((BLOCTIME / 'deployment.json').read_text()).get('blocTime')
    return c.get('url') or 'https://sepolia.base.org', addr


def live_power(address):
    """BlocTime.getVotingPower on the parent's BlocTime (30s cache)."""
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', str(address)):
        return 0
    hit = _power_cache.get(address.lower())
    if hit and time.time() - hit[1] < POWER_TTL:
        return hit[0]
    from web3 import Web3
    rpc, bt = _bloctime_net()
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
    abi = [{'name': 'getVotingPower', 'type': 'function', 'stateMutability': 'view',
            'inputs': [{'name': 'account', 'type': 'address'}],
            'outputs': [{'name': '', 'type': 'uint256'}]}]
    c = w3.eth.contract(address=Web3.to_checksum_address(bt), abi=abi)
    v = int(c.functions.getVotingPower(Web3.to_checksum_address(address)).call())
    _power_cache[address.lower()] = (v, time.time())
    return v


def ledger():
    """The paper subnet. Its stake source ('paper' | 'live') is stored in the
    ledger itself so the CLI and the API always agree."""
    L = Y.Ledger(owner=_cfg().get('paper_owner', 'owner'))
    if L.s.get('source') == 'live':
        L.stake_fn = live_power
    return L


def _num(x):
    """'1.5' → 1.5e18 wei, '123w' → 123 wei (raw), ints pass through as whole tokens."""
    if isinstance(x, int):
        return x * ONE
    s = str(x).strip()
    if s.endswith('w'):
        return int(s[:-1])
    from decimal import Decimal
    return int(Decimal(s) * ONE)


def _frac(x):
    """'0.5' → ONE/2. Accepts raw ints ≥ 1e6 as already ONE-scaled."""
    if isinstance(x, int) and x > 10 ** 6:
        return x
    from decimal import Decimal
    return int(Decimal(str(x)) * ONE)


def _weights(w):
    """JSON, a dict/list, or the CLI-friendly '1,3;0,1' (rows split by ';').
    The `m` CLI splits nested [..] on commas, so matrices go in as strings."""
    if isinstance(w, str):
        w = w.strip()
        if w[:1] in '[{':
            w = json.loads(w)
        else:
            rows = [[int(x) for x in r.split(',') if x.strip()] for r in w.split(';')]
            w = rows if ';' in w else rows[0]
    if isinstance(w, dict):  # the CLI keeps the quotes on {"0":1} keys
        w = {int(str(k).strip().strip('"\'')): int(v) for k, v in w.items()}
    return w


def _human(v):
    return str(v) if v is None else f'{v / ONE:.6f}'.rstrip('0').rstrip('.')


# ── paper ops (each holds the lock: one JSON file, many callers) ────────

def state():
    with _lock:
        L = ledger()
        st = L.state()
        st['source'] = L.s.get('source', 'paper')
        st['bloctime'] = dict(zip(('rpc', 'address'), _bloctime_net()))
        st['now'] = int(time.time())
        return st


def preview():
    with _lock:
        L = ledger()
        r = L.preview()
        pay = Y.pay(L.p['emission_per_epoch'], r['incentive'], r['dividends'],
                    L.p['owner_cut_bps'], L.p['miner_share_bps'])
        return {**r, 'miner_pay': pay[0], 'validator_pay': pay[1], 'owner_pay': pay[2]}


def set_source(mode):
    if mode not in ('paper', 'live'):
        raise ValueError("source must be 'paper' or 'live'")
    with _lock:
        L = ledger()
        L.s['source'] = mode
        L.save()
        return {'source': mode}


def set_stake(address, bloc):
    with _lock:
        return ledger().set_stake(address, _num(bloc))


def register_validator(address):
    with _lock:
        return ledger().register_validator(address)


def register_miner(address):
    with _lock:
        return ledger().register_miner(address)


def set_weights(address, weights):
    with _lock:
        return ledger().set_weights(address, _weights(weights))


def epoch(force=False):
    with _lock:
        return ledger().epoch(force=bool(force))


def claim(address):
    with _lock:
        return ledger().claim(address)


def set_params(**kw):
    conv = {'kappa': _frac, 'bond_alpha': _frac, 'emission_per_epoch': _num,
            'min_validator_bloc': _num}
    with _lock:
        return ledger().set_params(**{k: conv.get(k, int)(v) for k, v in kw.items()})


def reset():
    with _lock:
        L = ledger()
        src = L.s.get('source', 'paper')
        r = L.reset()
        if src != 'paper':
            L.s['source'] = src
            L.save()
        return r


def simulate(stakes, weights, kappa=0.5, alpha=1, emission=100, owner_cut_bps=1000,
             miner_share_bps=5000, epochs=1):
    """Stateless: stakes in BLOC (list), weights V x M rows. `epochs` repeats
    the same weights so the bond EMA can settle."""
    if isinstance(stakes, str):
        stakes = json.loads(stakes) if stakes.strip().startswith('[') else stakes.split(',')
    W = _weights(weights)
    S = [_num(s) for s in stakes]
    if len(W) != len(S):
        raise ValueError('need one weight row per validator')
    M = len(W[0]) if W else 0
    B = [[0] * M for _ in S]
    k, a, e = _frac(kappa), _frac(alpha), _num(emission)
    r = None
    for _ in range(max(1, min(int(epochs), 100))):
        S_eff = [S[i] if sum(W[i]) else 0 for i in range(len(S))]
        r = Y.yuma(S_eff, W, B, k, a)
    m, v, o = Y.pay(e, r['incentive'], r['dividends'], int(owner_cut_bps), int(miner_share_bps))
    return {'incentive': r['incentive'], 'dividends': r['dividends'], 'consensus': r['consensus'],
            'bonds': B, 'miner_pay': m, 'validator_pay': v, 'owner_pay': o,
            'human': {'incentive': [_human(x) for x in r['incentive']],
                      'dividends': [_human(x) for x in r['dividends']],
                      'miner_pay': [_human(x) for x in m], 'validator_pay': [_human(x) for x in v],
                      'owner_pay': _human(o)}}


# ── chain ───────────────────────────────────────────────────────────────

HH = './node_modules/.bin/hardhat'


def deployments():
    return _cfg().get('deployments', {})


def deploy(network='hardhat', bloctime=None, reward=None, params=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', network):
        raise ValueError('bad network name')
    env = dict(os.environ)
    if bloctime is None and network not in ('hardhat', 'localhost'):
        bloctime = _bloctime_net()[1]
    if bloctime:
        env['BLOCTIME'] = bloctime
    if reward:
        env['REWARD'] = reward
    if params:
        env['YUMA_PARAMS'] = params if isinstance(params, str) else json.dumps(params)
    r = subprocess.run([HH, 'run', 'scripts/yuma/deploy.js', '--network', network], cwd=str(BLOCTIME),
                       capture_output=True, text=True, timeout=600, env=env)
    out = r.stdout + r.stderr
    m = re.search(r'__RESULT__(.+?)__END__', out)
    if not m:
        raise RuntimeError(f'deploy failed: {out[-800:]}')
    res = json.loads(m.group(1))
    if network != 'hardhat':  # the in-process hardhat chain dies with the script
        cfg = _cfg()
        cfg.setdefault('deployments', {})[network] = res
        _save_cfg(cfg)
    return res


def _abi():
    p = BLOCTIME / 'artifacts' / 'contracts' / 'yuma' / 'YumaBlocTime.sol' / 'YumaBlocTime.json'
    return json.loads(p.read_text())['abi']


def chain_state(network=None):
    """Read a deployed YumaBlocTime: params, validators (with live BLOC),
    miners, last epoch outputs."""
    deps = deployments()
    if not deps:
        return {'deployed': False, 'hint': 'm bloctime.yuma/deploy network=base_sepolia'}
    network = network or next(iter(deps))
    d = deps.get(network)
    if not d:
        return {'deployed': False, 'networks': list(deps)}
    from web3 import Web3
    rpc = d.get('rpc') or _bloctime_net()[0]
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
    c = w3.eth.contract(address=Web3.to_checksum_address(d['yuma']), abi=_abi())
    f = c.functions
    p = f.params().call()
    keys = ['tempo', 'emissionPerEpoch', 'kappa', 'bondAlpha', 'ownerCutBps', 'minerShareBps',
            'minValidatorBloc', 'maxValidators', 'maxMiners']
    vals = f.getValidators().call()
    mins = f.getMiners().call()
    pv = f.preview().call() if vals and mins else [[], [], [], []]
    return {
        'deployed': True, 'network': network, **d,
        'params': {k: str(v) for k, v in zip(keys, p)},
        'epoch_count': f.epochCount().call(), 'next_epoch_time': f.nextEpochTime().call(),
        'total_emitted': str(f.totalEmitted().call()), 'total_pending': str(f.totalPending().call()),
        'validators': [{'uid': i, 'address': a, 'bloctime': str(pv[0][i]) if pv[0] else '0',
                        'dividend': str(f.lastDividend(i).call()), 'pending': str(f.pending(a).call())}
                       for i, a in enumerate(vals)],
        'miners': [{'uid': j, 'address': a, 'incentive': str(f.lastIncentive(j).call()),
                    'consensus': str(f.lastConsensus(j).call()), 'pending': str(f.pending(a).call())}
                   for j, a in enumerate(mins)],
        'preview': {'incentive': [str(x) for x in pv[1]], 'dividends': [str(x) for x in pv[2]]},
    }


def test(chains=True):
    out = {}
    r = subprocess.run(['python3', '-m', 'pytest', '-q', str(HERE / 'tests')], capture_output=True, text=True, timeout=300)
    out['pytest'] = {'ok': r.returncode == 0, 'tail': r.stdout.strip().splitlines()[-1:]}
    if chains:
        r = subprocess.run([HH, 'test', 'test/yuma/YumaBlocTime.test.js'], cwd=str(BLOCTIME),
                           capture_output=True, text=True, timeout=600)
        tail = [l.strip() for l in r.stdout.splitlines() if 'passing' in l or 'failing' in l]
        out['hardhat'] = {'ok': r.returncode == 0, 'tail': tail}
    out['ok'] = all(v['ok'] for v in out.values())
    return out
