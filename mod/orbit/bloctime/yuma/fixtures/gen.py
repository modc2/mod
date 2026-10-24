"""Regenerate the cross-implementation fixtures: python3 yuma/fixtures/gen.py

Each case is a sequence of epochs over a fixed validator/miner set. The
expected numbers come from yuma.py; the hardhat suite replays the same
inputs on YumaBlocTime.sol and must land on exactly the same ints."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from yuma import ONE, yuma, pay  # noqa: E402

E = 10 ** 18
CASES = [
    {
        'name': 'honest majority, one outlier',
        'params': {'kappa': ONE // 2, 'bond_alpha': ONE // 10, 'owner_cut_bps': 1000,
                   'miner_share_bps': 5000, 'emission': 100 * E},
        'stakes': [3000 * E, 1000 * E, 500 * E],
        'epochs': [
            [[10, 30, 60, 0], [10, 30, 60, 0], [0, 0, 0, 100]],
            [[10, 30, 60, 0], [20, 20, 60, 0], [0, 0, 0, 100]],
            [[5, 45, 50, 0], [10, 30, 60, 0], [0, 0, 0, 100]],
        ],
    },
    {
        'name': 'long lock beats big wallet',
        # 100 NAT locked 30 days vs 5000 NAT locked 1 hour: 2.59e8 vs 1.8e7 BLOC
        'params': {'kappa': ONE // 2, 'bond_alpha': ONE // 5, 'owner_cut_bps': 0,
                   'miner_share_bps': 4100, 'emission': 7200 * E},
        'stakes': [100 * 2592000 * E, 5000 * 3600 * E],
        'epochs': [
            [[1, 0, 3], [0, 1, 0]],
            [[1, 0, 3], [0, 1, 0]],
        ],
    },
    {
        'name': 'silent and zero-stake validators',
        'params': {'kappa': 6 * ONE // 10, 'bond_alpha': ONE, 'owner_cut_bps': 1800,
                   'miner_share_bps': 5000, 'emission': 1000 * E + 7},
        'stakes': [700 * E, 0, 300 * E, 250 * E],
        'epochs': [
            [[1, 1, 1], [9, 0, 0], [3, 1, 0], [0, 0, 0]],
            [[2, 1, 0], [9, 0, 0], [3, 1, 0], [1, 2, 3]],
        ],
    },
]


def run(case):
    p = case['params']
    S = case['stakes']
    V = len(S)
    M = len(case['epochs'][0][0])
    B = [[0] * M for _ in range(V)]
    out = []
    for W in case['epochs']:
        # the contract only counts stake of validators that set a non-empty row
        S_eff = [S[i] if sum(W[i]) > 0 else 0 for i in range(V)]
        r = yuma(S_eff, W, B, p['kappa'], p['bond_alpha'])
        m, v, o = pay(p['emission'], r['incentive'], r['dividends'], p['owner_cut_bps'], p['miner_share_bps'])
        out.append({
            'incentive': [str(x) for x in r['incentive']],
            'dividends': [str(x) for x in r['dividends']],
            'consensus': [str(x) for x in r['consensus']],
            'bonds': [[str(x) for x in row] for row in B],
            'miner_pay': [str(x) for x in m],
            'validator_pay': [str(x) for x in v],
            'owner_pay': str(o),
        })
    return {**case, 'stakes': [str(x) for x in S],
            'params': {k: str(v) for k, v in p.items()}, 'expected': out}


if __name__ == '__main__':
    data = [run(c) for c in CASES]
    (HERE / 'cases.json').write_text(json.dumps(data, indent=1))
    print(f'wrote {len(data)} cases to {HERE / "cases.json"}')
