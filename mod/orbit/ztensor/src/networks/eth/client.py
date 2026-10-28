"""ztensor on Ethereum, the wire layer: web3, anvil, forge build/test, deploy
of contract/ (the Foundry project). adapter.py turns it into a Network.

Signatures come from the SAME Cairo prover (core/prover/). The EVM has no
STARK curve, so contract/src/StarkCurve.sol does the group law in Yul on mulmod, and
the Poseidon permutation is a hand-assembled contract (Hades) at a fixed
CREATE2 address. Only the scope's `subnet` felt is chain-specific: it is
the contract address as a number, which the contract reports through
mineScope(epoch) / scoreScope(epoch, tag), so the signer asks the chain.

Networks:
    eth          local anvil on :51334 (chain id 31337, state in data/anvil/),
                 owner/relayer/payee = anvil's first three dev accounts
    eth-sepolia  Sepolia via ZTENSOR_RPC_ETH_SEPOLIA + ZTENSOR_ETH_KEY (a funded
                 key; it is owner and relayer). Not exercised from here.

The verifier costs ~0.41M gas per ring member, so rings stop at 32
(~13.5M gas, inside the 2^24 per-transaction cap of EIP-7825).
"""

import json
import os
import re
import subprocess
import time
import urllib.request

from ... import DATA

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.join(HERE, 'contract')
ANVIL_DIR = os.path.join(DATA, 'anvil')
ANVIL_PORT = int(os.environ.get('ZTENSOR_ANVIL_PORT', 51334))
FOUNDRY = os.path.expanduser('~/.foundry/bin')
ARTIFACT = os.path.join(PROJECT, 'out', 'ZTensor.sol', 'ZTensor.json')
HADES = os.path.join(PROJECT, 'hades.json')
MAX_RING = 32
TX_GAS_CAP = 1 << 24  # EIP-7825

# anvil's deterministic dev accounts (mnemonic "test test ... junk")
DEV_KEYS = {
    'owner': '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80',
    'relayer': '0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d',
}
DEV_PAYEE = '0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC'  # anvil account 2
NETS = {
    'eth': {'rpc': f'http://127.0.0.1:{ANVIL_PORT}', 'chain_id': 31337},
    'eth-sepolia': {'rpc': os.environ.get('ZTENSOR_RPC_ETH_SEPOLIA',
                                          'https://ethereum-sepolia-rpc.publicnode.com'),
                    'chain_id': 11155111},
}


class EthError(RuntimeError):
    pass


def _env():
    env = dict(os.environ)
    env['PATH'] = FOUNDRY + os.pathsep + env.get('PATH', '')
    return env


class EthZTensor:
    def __init__(self, data_dir):
        self.deploys_path = os.path.join(data_dir, 'deployments.json')
        self._w3 = {}
        self._c = {}   # (net, address, artifact mtime) -> web3 contract

    # ---- plumbing ------------------------------------------------------------

    def w3(self, net):
        if net not in self._w3:
            from web3 import Web3
            self._w3[net] = Web3(Web3.HTTPProvider(NETS[net]['rpc'],
                                                   request_kwargs={'timeout': 120}))
        return self._w3[net]

    def _key(self, net, role):
        if net == 'eth':
            return DEV_KEYS[role]
        k = os.environ.get('ZTENSOR_ETH_KEY')
        if not k:
            raise EthError('set ZTENSOR_ETH_KEY (a funded key) for ' + net)
        return k

    def _addr(self, net, role):
        from eth_account import Account
        return Account.from_key(self._key(net, role)).address

    def _abi(self):
        return json.load(open(ARTIFACT))['abi']

    def _contract(self, net):
        """Cached: rebuilding it re-read the ABI json on every view."""
        d = self._dep(net)
        k = (net, d['address'], os.path.getmtime(ARTIFACT))
        if k not in self._c:
            self._c = {k: self.w3(net).eth.contract(address=d['address'],
                                                    abi=self._abi())}
        return self._c[k]

    def _send(self, net, tx, role='relayer'):
        """Sign + send, wait for the receipt; a revert raises with its reason."""
        w3 = self.w3(net)
        key = self._key(net, role)
        sender = self._addr(net, role)
        tx = dict(tx)
        tx.setdefault('from', sender)
        tx['nonce'] = w3.eth.get_transaction_count(sender, 'pending')
        tx['chainId'] = NETS[net]['chain_id']
        if 'gas' not in tx and net == 'eth':
            tx['gas'] = TX_GAS_CAP  # local anvil: estimating re-runs the verifier ~10x
        if 'gas' not in tx:
            try:
                tx['gas'] = min(int(w3.eth.estimate_gas(tx) * 1.2), TX_GAS_CAP)
            except Exception as e:  # noqa: BLE001
                raise EthError(_reason(e)) from None
        fees = w3.eth.fee_history(1, 'latest', [50])
        base = fees['baseFeePerGas'][-1]
        tx['maxPriorityFeePerGas'] = w3.to_wei(1, 'gwei')
        tx['maxFeePerGas'] = 2 * base + tx['maxPriorityFeePerGas']
        signed = w3.eth.account.sign_transaction(tx, key)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        r = w3.eth.wait_for_transaction_receipt(h, timeout=180)
        if r['status'] != 1:
            raise EthError(f'tx {h.hex()} reverted')
        return {'hash': '0x' + h.hex().removeprefix('0x'), 'gas': r['gasUsed'],
                'block': r['blockNumber'], 'address': r.get('contractAddress'),
                'logs': r['logs']}

    def block(self, net):
        return self.w3(net).eth.block_number

    def view(self, net, fn, *args):
        return getattr(self._contract(net).functions, fn)(*args).call()

    # ---- local anvil ---------------------------------------------------------

    def _alive(self):
        try:
            req = urllib.request.Request(
                NETS['eth']['rpc'], data=json.dumps({
                    'jsonrpc': '2.0', 'id': 1, 'method': 'eth_chainId',
                    'params': []}).encode(),
                headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=1).read()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _rpc(self, method, params):
        return self.w3('eth').provider.make_request(method, params)

    def anvil(self, action='status'):
        """start | stop | reset | status for the local chain on :51334.
        Blocks are mined per transaction (no interval), so an epoch only
        moves when told to (anvil_mine), like starknet-devnet."""
        if action == 'stop':
            r = subprocess.run(['fuser', '-k', f'{ANVIL_PORT}/tcp'],
                               capture_output=True)
            return {'stopped': r.returncode == 0}
        if action == 'reset':
            self.anvil('stop')
            time.sleep(1)
            subprocess.run(['rm', '-rf', ANVIL_DIR])
            d = self._deploys()
            d.pop('eth', None)
            self._save_deploys(d)
            return self.anvil('start')
        if action == 'start' and not self._alive():
            os.makedirs(ANVIL_DIR, exist_ok=True)
            log = open(os.path.join(ANVIL_DIR, 'anvil.log'), 'a')
            # --gas-limit: a block must hold a 2^24 tx; the tx cap is ours
            subprocess.Popen(
                [os.path.join(FOUNDRY, 'anvil'), '--port', str(ANVIL_PORT),
                 '--host', '127.0.0.1', '--hardfork', 'prague',
                 '--gas-limit', '60000000',
                 '--state', os.path.join(ANVIL_DIR, 'state.json'),
                 '--state-interval', '30', '--silent'],
                stdout=log, stderr=log, cwd=ANVIL_DIR, start_new_session=True)
            for _ in range(100):
                if self._alive():
                    break
                time.sleep(0.1)
            else:
                return {'up': False, 'error': 'anvil did not come up'}
        up = self._alive()
        return {'up': up, 'port': ANVIL_PORT,
                **({'block': self.block('eth')} if up else {})}

    def mine(self, net, blocks):
        """Local only: mine `blocks` empty blocks (the epoch clock)."""
        if net != 'eth':
            raise EthError('mine is anvil-only')
        self._rpc('anvil_mine', [hex(int(blocks))])

    # ---- build / test ----------------------------------------------------------

    def _run(self, cmd, timeout=900):
        out = subprocess.run(cmd, cwd=PROJECT, capture_output=True, text=True,
                             timeout=timeout, env=_env())
        return out.returncode, (out.stdout + out.stderr)

    def build(self):
        rc, log = self._run([os.path.join(FOUNDRY, 'forge'), 'build', '--sizes'])
        sizes = {m.group(1): int(m.group(2).replace(',', '')) for m in re.finditer(
            r'^\|\s*(\w+)\s*\|\s*([\d,]+)\s*\|', log, re.M)}
        return {'ok': rc == 0 and os.path.exists(ARTIFACT), 'sizes': sizes,
                'log': log.strip()[-1500:]}

    def _stale(self):
        if not os.path.exists(ARTIFACT):
            return True
        t = os.path.getmtime(ARTIFACT)
        return any(os.path.getmtime(os.path.join(PROJECT, 'src', f)) > t
                   for f in os.listdir(os.path.join(PROJECT, 'src')))

    def test(self):
        """forge test: Cairo fixtures (Poseidon, Hp, a Cairo-made signature
        verifying in Solidity), the full epoch, every rejection path, and
        gas by ring size."""
        rc, log = self._run([os.path.join(FOUNDRY, 'forge'), 'test', '-vv'])
        tests = re.findall(r'^\[(PASS|FAIL)[^\]]*\] (\w+)\(', log, re.M)
        gas = [{'ring': int(n), 'gas': int(g)} for n, g in re.findall(
            r'gas submit ring (\d+): (\d+)', log)]
        if gas:
            json.dump({'at': int(time.time()), 'cap': TX_GAS_CAP,
                       'max_ring': MAX_RING, 'rows': gas},
                      open(os.path.join(os.path.dirname(self.deploys_path),
                                        'eth_gas.json'), 'w'), indent=1)
        return {'ok': rc == 0,
                'passed': sum(1 for s, _ in tests if s == 'PASS'),
                'failed': sum(1 for s, _ in tests if s != 'PASS'),
                'tests': [[s, n] for s, n in tests], 'gas': gas,
                'log': log.strip()[-3000:]}

    # ---- deploy ------------------------------------------------------------------

    def _deploys(self):
        try:
            return json.load(open(self.deploys_path))
        except Exception:  # noqa: BLE001
            return {}

    def _save_deploys(self, d):
        tmp = self.deploys_path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, self.deploys_path)

    def _dep(self, net):
        d = self._deploys().get(net)
        if not d:
            raise EthError(f'not deployed on {net}; run deploy network={net}')
        return d

    def _ensure_hades(self, net):
        """The Poseidon permutation at its CREATE2 address: through the
        deterministic deployer when the chain has one, else (anvil only)
        anvil_setCode with the same runtime."""
        h = json.load(open(HADES))
        w3 = self.w3(net)
        if len(w3.eth.get_code(h['address'])) > 0:
            return {'address': h['address'], 'fresh': False}
        dep = w3.to_checksum_address(h['deployer'])
        if len(w3.eth.get_code(dep)) > 0:
            self._send(net, {'to': dep, 'data': h['salt'] + h['init'][2:],
                             'gas': 3_000_000}, role='owner')
        elif net == 'eth':
            self._rpc('anvil_setCode', [h['address'], h['runtime']])
        code = w3.eth.get_code(h['address'])
        if code.hex().removeprefix('0x') != h['runtime'][2:]:
            raise EthError('Hades did not land at ' + h['address'])
        return {'address': h['address'], 'fresh': True}

    def deploy(self, net='eth', tempo=None, min_ring=3, emission=1000):
        if self._stale():
            b = self.build()
            if not b['ok']:
                return {'ok': False, 'stage': 'build', 'error': b['log']}
        if net == 'eth':
            st = self.anvil('start')
            if not st.get('up'):
                return {'ok': False, 'stage': 'anvil', 'error': st.get('error')}
        tempo = int(tempo or 60)
        art = json.load(open(ARTIFACT))
        try:
            hades = self._ensure_hades(net)
            w3 = self.w3(net)
            owner = self._addr(net, 'owner')
            c = w3.eth.contract(abi=art['abi'], bytecode=art['bytecode']['object'])
            tx = c.constructor(owner, tempo, int(min_ring), int(emission)) \
                .build_transaction({'from': owner, 'gas': 6_000_000,
                                    'nonce': 0, 'chainId': NETS[net]['chain_id'],
                                    'maxFeePerGas': 0, 'maxPriorityFeePerGas': 0})
            r = self._send(net, {'data': tx['data'], 'gas': 6_000_000}, role='owner')
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'stage': 'deploy', 'error': _reason(e)}
        rec = {'address': r['address'], 'owner': owner,
               'relayer': self._addr(net, 'relayer'),
               'payee': DEV_PAYEE if net == 'eth' else owner,
               'hades': hades['address'], 'tempo': tempo,
               'min_ring': int(min_ring), 'emission': int(emission),
               'tx': r['hash'], 'gas': r['gas'],
               'code_bytes': len(w3.eth.get_code(r['address'])),
               'rpc': NETS[net]['rpc'], 'at': int(time.time())}
        d = self._deploys()
        d[net] = rec
        self._save_deploys(d)
        return {'ok': True, 'network': net, **rec}

    # ---- contract calls ----------------------------------------------------------

    def invoke(self, net, fn, *args, role='relayer'):
        c = self._contract(net)
        f = getattr(c.functions, fn)(*args)
        sender = self._addr(net, role)
        try:
            ret = f.call({'from': sender})  # the return value + a clean revert
        except Exception as e:  # noqa: BLE001
            raise EthError(_reason(e)) from None
        tx = f.build_transaction({'from': sender, 'gas': TX_GAS_CAP, 'nonce': 0,
                                  'chainId': NETS[net]['chain_id'],
                                  'maxFeePerGas': 0, 'maxPriorityFeePerGas': 0})
        r = self._send(net, {'to': c.address, 'data': tx['data']}, role=role)
        return {**r, 'result': ret}

    def members(self, net, role):
        return [{'x': hex(p[0]), 'y': hex(p[1])}
                for p in self.view(net, 'members', role)]

    def board(self, net, epochs=6):
        d = self._deploys().get(net)
        out = {'network': net, 'chain': 'eth', 'deployed': bool(d),
               'devnet_up': self._alive() if net == 'eth' else None}
        if not d or (net == 'eth' and not out['devnet_up']):
            return out
        try:
            owner, tempo, min_ring, emission, is_open = self.view(net, 'params')
        except Exception as e:  # noqa: BLE001  (anvil restarted without state)
            out.update(deployed=False, error=_reason(e))
            return out
        b = self.block(net)
        e = b // tempo
        out.update(contract=d['address'], owner=owner, tempo=tempo,
                   min_ring=min_ring, emission=emission, open=is_open,
                   hades=d.get('hades'), block=b, epoch=e,
                   blocks_left=tempo - b % tempo,
                   miners=self.members(net, 1), validators=self.members(net, 2))
        rows = []
        for ep in range(e, max(-1, e - int(epochs)), -1):
            t = self.tally(net, ep)
            if t['tags']:
                t['open'] = ep == e
                rows.append(t)
        out['epochs'] = rows
        return out

    def tally(self, net, epoch=None):
        e = int(epoch) if epoch is not None else self.view(net, 'epoch')
        rows = []
        for tag in self.view(net, 'tagsOf', e):
            s, n = self.view(net, 'tally', e, tag)
            c, r, done = self.view(net, 'yuma', e, tag)
            rows.append({'tag': hex(tag), 'score_sum': s, 'scores': n,
                         'claimed': self.view(net, 'claimed', e, tag),
                         'consensus': c, 'rank': r, 'settled': done})
        vset, rank_total, nsettled = self.view(net, 'yumaEpoch', e)
        return {'epoch': e, 'total': self.view(net, 'epochTotal', e),
                'validators': vset, 'rank_total': rank_total,
                'settled': nsettled, 'tags': rows}


def _reason(e):
    """The revert string out of whatever web3 raised."""
    s = str(e)
    m = re.search(r"execution reverted: ([^'\"]+)", s)
    if m:
        return m.group(1).strip().rstrip(')')
    args = getattr(e, 'args', None)
    if args and isinstance(args[0], str):
        return args[0][:300]
    return s[:300]
