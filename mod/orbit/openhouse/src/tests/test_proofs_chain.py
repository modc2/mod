"""
The Ethereum verifier on a real EVM: deploy contracts/OpenHouseProofs.sol to a
throwaway anvil, seat this node's issuer key as the bank, issue a receipt
through the Cairo program and relay it with proof_submit(chain='eth'). The
contract must take it once and refuse it twice. Skipped without Foundry or
scarb; never touches a public network.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parent.parent
CONTRACTS = MODULE_DIR / 'contracts'
FOUNDRY = os.environ.get('FOUNDRY', '/root/.foundry/bin')
PORT = int(os.environ.get('PROOFS_ANVIL_PORT', '8551'))
KEY0 = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80'   # anvil account 0
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent.parent))


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


openhouse_mod = _load('openhouse_under_test', MODULE_DIR / 'mod.py')
proofs = _load('openhouse_proofs', MODULE_DIR / 'proofs.py')
CAST = _load('openhouse_demo_cast', MODULE_DIR / 'demo.py').CAST


def _bin(name):
    p = Path(FOUNDRY, name)
    return str(p) if p.exists() else shutil.which(name)


@pytest.mark.skipif(not (_bin('anvil') and _bin('forge')), reason='foundry not installed')
@pytest.mark.skipif(not proofs.scarb(), reason='scarb not installed')
def test_eth_verifier_end_to_end(tmp_path):
    from web3 import Web3
    from eth_account import Account

    url = f'http://127.0.0.1:{PORT}'
    anvil = subprocess.Popen([_bin('anvil'), '--port', str(PORT), '--silent'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        w3 = Web3(Web3.HTTPProvider(url))
        for _ in range(100):
            if w3.is_connected():
                break
            time.sleep(0.1)
        assert w3.is_connected(), 'anvil did not come up'
        acct0 = Account.from_key(KEY0)

        oh = openhouse_mod.Mod(store=tmp_path)
        oh.set_terms(model='full_credit', fee_pct=2.5, home_price=250, monthly_rent=0.62,
                     owner=CAST['owner'], treasury=CAST['owner'])
        ph = proofs.program_hash()

        # deploy with THIS node's program hash, no fact registry
        head = [_bin('forge'), 'create', 'OpenHouseProofs.sol:OpenHouseProofs', '--rpc-url', url,
                '--private-key', KEY0]
        args = ['--constructor-args', acct0.address, ph, '0x' + '00' * 20]   # must stay last
        out = subprocess.run(head + ['--broadcast'] + args, cwd=CONTRACTS, capture_output=True, text=True, timeout=240)
        if out.returncode != 0 and 'unexpected argument' in (out.stderr + out.stdout):
            out = subprocess.run(head + args, cwd=CONTRACTS, capture_output=True, text=True, timeout=240)
        m = re.search(r'Deployed to: (0x[0-9a-fA-F]{40})', out.stdout + out.stderr)
        assert m, out.stdout + out.stderr
        addr = m.group(1)
        c = w3.eth.contract(address=addr, abi=proofs.ETH_ABI)

        def send(fn):
            tx = fn.build_transaction({'from': acct0.address, 'chainId': w3.eth.chain_id,
                                       'nonce': w3.eth.get_transaction_count(acct0.address)})
            h = w3.eth.send_raw_transaction(acct0.sign_transaction(tx).raw_transaction)
            return w3.eth.wait_for_transaction_receipt(h, timeout=60)

        issuer = oh.proof_status()['issuer']
        assert send(c.functions.setBank(issuer['eth'], 'Testnet Bank', True)).status == 1
        assert send(c.functions.registerProperty(1, acct0.address, 620_000, '128 Maple Ave')).status == 1
        fund = acct0.sign_transaction({'to': issuer['eth'], 'value': w3.to_wei(1, 'ether'), 'gas': 21000,
                                       'gasPrice': w3.eth.gas_price, 'chainId': w3.eth.chain_id,
                                       'nonce': w3.eth.get_transaction_count(acct0.address)})
        w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(fund.raw_transaction), timeout=60)
        assert c.functions.programHash().call().hex().lower().lstrip('0x') == ph[2:]

        # the bank issues, the bank relays, the chain verifies
        r = oh.proof_issue(tenant=CAST['maya'], amount=620_000, period=202610)
        assert 'error' not in r, r
        ok, why, f, signer = c.functions.verify([proofs.felt(o) for o in r['outputs']],
                                                bytes.fromhex(r['sig']['eth'][2:])).call()
        assert ok and why == '' and signer == issuer['eth'] and '0x' + f.hex().lstrip('0x').rjust(64, '0') == r['fact']
        res = oh.proof_submit(r['nullifier'], chain='eth', address=addr, url=url)
        assert res.get('ok'), res
        assert c.functions.paid(1, 202610).call() is True
        assert c.functions.count().call() == 1
        assert oh.proof(r['nullifier'])['chain']['eth']['ok']

        # once
        again = oh.proof_submit(r['nullifier'], chain='eth', address=addr, url=url)
        assert 'nullifier already used' in again['error'], again
        # and a stranger's attestation is not a bank's
        stranger = openhouse_mod.Mod(store=tmp_path / 'other')
        stranger.set_terms(model='full_credit', fee_pct=2.5, home_price=250, monthly_rent=0.62,
                           owner=CAST['owner'], treasury=CAST['owner'])
        s = stranger.proof_issue(tenant=CAST['jonah'], amount=620_000, period=202611)
        refused = stranger.proof_submit(s['nullifier'], chain='eth', address=addr, url=url)
        assert 'not an active bank' in refused['error'], refused

        # the post-quantum door: seat the bank's OHX-KECCAK key, relay a
        # hash-based signature, the contract burns the one-time leaf
        ohx = oh.proof_pq()['keys']['ohx']
        assert send(c.functions.setPQBank(bytes.fromhex(ohx['pub_seed'][2:]), bytes.fromhex(ohx['root'][2:]),
                                          'Testnet Bank PQ', True)).status == 1
        q = oh.proof_issue(tenant=CAST['maya'], amount=620_000, period=202612)
        leaf = int(q['sig']['pq']['ohx'][2:10], 16)
        res = oh.proof_submit(q['nullifier'], chain='eth', address=addr, url=url, door='pq')
        assert res.get('ok') and res['door'] == 'pq', res
        assert c.functions.paid(1, 202612).call() is True
        assert c.functions.leafUsed(bytes.fromhex(ohx['id'][2:]), leaf).call() is True

        # Q-day: shut the ECDSA door for good; the hash-based door still books rent
        assert send(c.functions.requirePQ()).status == 1
        assert c.functions.pqRequired().call() is True
        late = oh.proof_issue(tenant=CAST['maya'], amount=620_000, period=202701)
        closed = oh.proof_submit(late['nullifier'], chain='eth', address=addr, url=url)
        assert 'classical door closed' in closed['error'], closed
        assert oh.proof_submit(late['nullifier'], chain='eth', address=addr, url=url, door='pq').get('ok')
    finally:
        anvil.kill()
        anvil.wait()
