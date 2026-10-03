"""
The on-chain testnet walkthrough (contracts/testnet.sh) must deploy and take
rent on a real EVM — a throwaway anvil chain. Skipped where Foundry isn't
installed; it never touches a public network (RPC_URL is cleared).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CONTRACTS = Path(__file__).resolve().parent.parent / 'contracts'
FOUNDRY = os.environ.get('FOUNDRY', '/root/.foundry/bin')


@pytest.mark.skipif(not (Path(FOUNDRY, 'anvil').exists() or shutil.which('anvil')),
                    reason='foundry (anvil/forge) not installed')
def test_deploy_and_pay_rent_on_anvil():
    env = {**os.environ, 'FOUNDRY': FOUNDRY, 'ANVIL_PORT': '8549'}
    env.pop('RPC_URL', None)
    out = subprocess.run([str(CONTRACTS / 'testnet.sh')], env=env, capture_output=True,
                         text=True, timeout=240).stdout
    assert 'ONCHAIN EXECUTION COMPLETE & SUCCESSFUL' in out, out
    principal = int(re.search(r'principalUsd18: uint256 (\d+)', out).group(1))
    pool = int(re.search(r'feePoolUsd18: uint256 (\d+)', out).group(1))
    # 0.0062 ETH at $2,500 = $15.50; 2.5% fee, full credit on the rest
    assert principal == 15_112_500_000_000_000_000
    assert pool == 387_500_000_000_000_000
