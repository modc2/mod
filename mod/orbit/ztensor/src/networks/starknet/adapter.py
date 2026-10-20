"""ztensor on Starknet: contract/ (Scarb package `ztensor`, snforge suite) — the
Cairo verifier, which checks the ring signature natively with the EC
builtin. Writes go through sncast; reads are raw JSON-RPC.

Networks:
    devnet    local starknet-devnet on :51332 (seed 0; accounts owner,
              relayer, payee), state in data/devnet/
    sepolia   sncast account (STRK-funded) via account=, rpc= or
              ZTENSOR_RPC_SEPOLIA
    mainnet   same, ZTENSOR_RPC_MAINNET
"""
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request

from ... import DATA
from ...core import util
from ...core.util import felt, hexs, load, save
from ..base import Network

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, 'contract')
DEVNET_DIR = os.path.join(DATA, 'devnet')
CLASSES = os.path.join(DATA, 'classes.json')  # {network: {src, class_hash}}
DEVNET_PORT = int(os.environ.get('ZTENSOR_DEVNET_PORT', 51332))
DEFAULT_ACCOUNTS = os.path.expanduser(
    '~/.starknet_accounts/starknet_open_zeppelin_accounts.json')
NETWORKS = {
    # sncast picks the accounts-file section from the chain id; devnet
    # reports SN_SEPOLIA, so its accounts live under alpha-sepolia too
    'devnet': {'rpc': f'http://127.0.0.1:{DEVNET_PORT}/rpc',
               'section': 'alpha-sepolia', 'fee': 'eth'},
    'sepolia': {'rpc': os.environ.get(
        'ZTENSOR_RPC_SEPOLIA', 'https://starknet-sepolia-rpc.publicnode.com'),
        'section': 'alpha-sepolia', 'fee': 'strk'},
    'mainnet': {'rpc': os.environ.get(
        'ZTENSOR_RPC_MAINNET', 'https://starknet-rpc.publicnode.com'),
        'section': 'alpha-mainnet', 'fee': 'strk'},
}


# Sierra gas per VM resource (Starknet versioned constants, 0.13.4+): what an
# execution costs in L2 gas, the unit mainnet fees are charged in
SIERRA_GAS = {'steps': 100, 'range_check_builtin_applications': 70,
              'pedersen_builtin_applications': 4050,
              'poseidon_builtin_applications': 491,
              'ec_op_builtin_applications': 4085,
              'bitwise_builtin_applications': 583,
              'ecdsa_builtin_applications': 10561,
              'keccak_builtin_applications': 136189}
OTHER_PER_STEP = 17  # range_check+pedersen+poseidon per step, ring-4 receipt


def l2_gas(er):
    """L2 gas of one execution from its receipt's execution_resources."""
    return sum(int(er.get(k) or 0) * w for k, w in SIERRA_GAS.items())


def selector(name):
    """starknet_keccak(name): keccak256 truncated to 250 bits."""
    from eth_hash.auto import keccak
    return hex(int.from_bytes(keccak(name.encode()), 'big') & ((1 << 250) - 1))


def sig_calldata(sig):
    """sign() result -> (ring calldata, signature calldata)."""
    cd = [len(sig['ring'])]
    for p in sig['ring']:
        cd += [int(p['x'], 16), int(p['y'], 16)]
    return cd, [int(sig['c0'], 16), len(sig['s']),
                *[int(s, 16) for s in sig['s']],
                int(sig['image']['x'], 16), int(sig['image']['y'], 16)]


class Starknet(Network):
    chain = 'starknet'
    title = 'Starknet'
    nets = ('devnet', 'sepolia', 'mainnet')
    order = 10
    max_ring = 64
    sim_tempo = 60
    cost_fields = ('steps', 'ec_op', 'l2_gas', 'da_gas')
    token, symbol = 'starknet', 'STRK'
    deploy_fields = ('class_hash',)
    ui = {'name': 'Starknet', 'local': 'starknet-devnet', 'color': '#ec796b',
          'verifier': 'by a Cairo contract (native EC builtin)',
          'deploying': 'declaring + deploying the Cairo verifier'}
    sources = (
        ('Starknet · Cairo', [
            ('contract/src/lsag.cairo', 'the scheme: LSAG over the STARK curve'),
            ('contract/src/ztensor.cairo', 'the subnet contract + Zuma settle'),
            ('contract/tests/test_ztensor.cairo', 'snforge suite'),
            ('contract/Scarb.toml', 'package')]),
        ('Starknet · adapter', [
            ('adapter.py', 'devnet, sncast, deploy, actions'),
            ('tests/test_starknet.py', 'adapter tests')]),
    )

    # ---- mainnet economics ---------------------------------------------------

    def mainnet(self):
        from ...core.fees import rpc
        u = NETWORKS['mainnet']['rpc']
        head = rpc(u, 'starknet_getBlockWithTxHashes', ['latest'])
        n = head['block_number']
        old = rpc(u, 'starknet_getBlockWithTxHashes', [{'block_number': n - 100}])
        fri = lambda k: int(head[k]['price_in_fri'], 16) / 1e18  # noqa: E731
        return {'block_seconds': (head['timestamp'] - old['timestamp']) / 100,
                'gas': {'l2_strk': fri('l2_gas_price'),
                        'l1_data_strk': fri('l1_data_gas_price'),
                        'version': head.get('starknet_version')},
                'block_capacity': None, 'capacity_unit': 'L2 gas',
                'note': 'L2 gas (steps x100 + builtins at Sierra-gas weights) x '
                        'L2 gas price + data-availability gas; estimate, the '
                        'account\'s own validate/execute is not counted'}

    def fee(self, cost, m):
        if not cost.get('steps'):
            return None
        l2 = cost.get('l2_gas') or (
            cost['steps'] * (SIERRA_GAS['steps'] + OTHER_PER_STEP) +
            (cost.get('ec_op') or 0) * SIERRA_GAS['ec_op_builtin_applications'])
        g = m['gas']
        return l2 * g['l2_strk'] + (cost.get('da_gas') or 0) * g['l1_data_strk']

    # ---- lifecycle ---------------------------------------------------------

    def health(self):
        return {'tools': {t: os.path.exists(os.path.join(util.BIN, t)) for t in
                          ('scarb', 'snforge', 'sncast', 'starknet-devnet')},
                'built': os.path.exists(os.path.join(
                    CONTRACT, 'target', 'dev', 'ztensor_ZTensor.contract_class.json')),
                'up': self._alive()}

    def _run(self, cmd, timeout=900):
        out = subprocess.run(cmd, cwd=CONTRACT, capture_output=True, text=True,
                             timeout=timeout, env=util.env())
        log = '\n'.join(l for l in (out.stdout + out.stderr).splitlines()
                        if not l.startswith(('warning:', '   ', ' -->')))
        return {'ok': out.returncode == 0, 'log': log.strip()[-4000:]}

    def build(self):
        return self._run([os.path.join(util.BIN, 'scarb'), 'build'])

    def test(self):
        """snforge: LSAG soundness/linkability, full epoch, every rejection."""
        r = self._run([os.path.join(util.BIN, 'snforge'), 'test'])
        m = re.search(r'Tests: (\d+) passed, (\d+) failed', r['log'])
        r['passed'], r['failed'] = (int(m.group(1)), int(m.group(2))) \
            if m else (0, None)
        r['tests'] = [list(t) for t in
                      re.findall(r'\[(PASS|FAIL)\] \S+::(\w+)', r['log'])]
        return r

    def _alive(self):
        try:
            urllib.request.urlopen(
                f'http://127.0.0.1:{DEVNET_PORT}/is_alive', timeout=1).read()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _devnet(self, path, body=None):
        url = f'http://127.0.0.1:{DEVNET_PORT}{path}'
        req = url if body is None else urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())

    def up(self, action='status'):
        """start | stop | reset | status for starknet-devnet on :51332."""
        if action == 'stop':
            r = subprocess.run(['fuser', '-k', f'{DEVNET_PORT}/tcp'],
                               capture_output=True)
            return {'stopped': r.returncode == 0}
        if action == 'reset':
            self.up('stop')
            time.sleep(0.5)
            d = load(util.DEPLOYS, {})
            d.pop('devnet', None)
            save(util.DEPLOYS, d)
            for f in ('devnet.json', 'accounts.json'):
                p = os.path.join(DEVNET_DIR, f)
                if os.path.exists(p):
                    os.remove(p)
            return self.up('start')
        if action == 'start' and not self._alive():
            exe = os.path.join(util.BIN, 'starknet-devnet')
            if not os.path.exists(exe):
                return {'up': False, 'error': 'starknet-devnet not in ~/.local/bin'}
            os.makedirs(DEVNET_DIR, exist_ok=True)
            log = open(os.path.join(DEVNET_DIR, 'devnet.log'), 'a')
            subprocess.Popen(
                [exe, '--host', '127.0.0.1', '--port', str(DEVNET_PORT),
                 '--seed', '0', '--accounts', '3', '--dump-on', 'block',
                 '--dump-path', os.path.join(DEVNET_DIR, 'devnet.json')],
                stdout=log, stderr=log, cwd=DEVNET_DIR, start_new_session=True)
            for _ in range(150):
                if self._alive():
                    break
                time.sleep(0.1)
            else:
                return {'up': False, 'error': 'devnet did not come up'}
        up = self._alive()
        if up and not os.path.exists(os.path.join(DEVNET_DIR, 'accounts.json')):
            self._devnet_accounts()
        out = {'up': up, 'port': DEVNET_PORT}
        if up:
            out['block'] = self.block('devnet')
        return out

    def _devnet_accounts(self):
        rows = self._devnet('/predeployed_accounts')
        cls = self._rpc('devnet', 'starknet_getClassHashAt',
                        ['latest', rows[0]['address']])
        book = {name: {'address': r['address'], 'class_hash': cls,
                       'deployed': True, 'legacy': False,
                       'private_key': r['private_key'],
                       'public_key': r['public_key'],
                       'type': 'open_zeppelin'}
                for name, r in zip(('owner', 'relayer', 'payee'), rows)}
        save(os.path.join(DEVNET_DIR, 'accounts.json'),
             {NETWORKS['devnet']['section']: book}, private=True)

    def _rpc(self, network, method, params):
        req = urllib.request.Request(
            NETWORKS[network]['rpc'], data=json.dumps(
                {'jsonrpc': '2.0', 'id': 1, 'method': method,
                 'params': params}).encode(),
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'ztensor/0.4'})
        r = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if 'error' in r:
            raise RuntimeError(r['error'])
        return r['result']

    def block(self, net):
        return self._rpc(net, 'starknet_blockNumber', [])

    def advance(self, net, to_block):
        if net != 'devnet':
            while self.block(net) < to_block:
                time.sleep(5)
            return
        while self.block(net) < to_block:
            self._devnet('/create_block', {})

    # ---- sncast --------------------------------------------------------------

    def _ctx(self, network, account=None, accounts_file=None, rpc=None):
        net = dict(NETWORKS[network])
        if rpc:
            net['rpc'] = rpc
        if network == 'devnet':
            st = self.up('start')
            if not st.get('up'):
                raise RuntimeError(st.get('error', 'devnet down'))
            net['accounts_file'] = os.path.join(DEVNET_DIR, 'accounts.json')
            net['owner'] = account or 'owner'
            net['relayer'] = 'relayer'
        else:
            d = load(util.DEPLOYS, {}).get(network, {})
            net['accounts_file'] = accounts_file or d.get(
                'accounts_file') or DEFAULT_ACCOUNTS
            net['owner'] = account or d.get('account')
            net['relayer'] = d.get('relayer') or net['owner']
            if not net['owner']:
                raise ValueError(f'{network}: pass account=<sncast account '
                                 'name> (create one with `sncast account '
                                 'create` and fund it)')
        return net

    def _sncast(self, net, args, account):
        cmd = [os.path.join(util.BIN, 'sncast'), '--accounts-file',
               net['accounts_file'], '--account', account, '--json']
        if not net['rpc'].startswith('http://127.0.0.1'):
            cmd.append('--wait')
        out = subprocess.run(cmd + args + ['--url', net['rpc']],
                             cwd=CONTRACT, capture_output=True, text=True,
                             timeout=600, env=util.env())
        text = out.stdout + '\n' + out.stderr
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith('{') and '"command"' in line:
                r = json.loads(line)
                if 'error' not in r:
                    return r
        m = re.findall(r"Failure reason: (?:0x[0-9a-f]+ )?\(?'([^']*)'\)?",
                       text)
        raise RuntimeError(m[-1] if m else text.strip()[-800:])

    def _dep(self, network):
        d = load(util.DEPLOYS, {}).get(network)
        if not d:
            raise RuntimeError(f'not deployed on {network}; run deploy first')
        return d

    def _invoke(self, network, fn, calldata, as_owner=False):
        net = self._ctx(network)
        args = ['invoke', '--contract-address', self._dep(network)['address'],
                '--function', fn, '--fee-token', net['fee']]
        if calldata:
            args += ['--calldata', *map(hexs, calldata)]
        r = self._sncast(net, args, net['owner'] if as_owner
                         else net['relayer'])
        return r.get('transaction_hash')

    def _call(self, network, fn, calldata=()):
        """View call over raw JSON-RPC (ms, not an sncast process per read)."""
        if network == 'devnet' and not self._alive():
            self._ctx(network)  # boots the devnet
        r = self._rpc(network, 'starknet_call', [{
            'contract_address': self._dep(network)['address'],
            'entry_point_selector': selector(fn),
            'calldata': [hexs(c) for c in calldata]}, 'latest'])
        return [int(x, 16) for x in r]

    def _steps(self, network, tx):
        """Cairo VM cost of a tx: steps + ec_op builtin calls."""
        try:
            r = self._rpc(network, 'starknet_getTransactionReceipt', [tx])
            er = r.get('execution_resources') or {}
            out = {'steps': er.get('steps'),
                   'ec_op': er.get('ec_op_builtin_applications')}
            if er.get('l2_gas'):  # RPC 0.8+: the chain says it outright
                out['l2_gas'] = er['l2_gas']
            else:
                out['l2_gas'] = l2_gas(er)
            da = er.get('data_availability') or {}
            out['da_gas'] = er.get('l1_data_gas') or da.get('l1_data_gas')
            return out
        except Exception:  # noqa: BLE001
            return {}

    # ---- deploy + registry ---------------------------------------------------

    def deploy(self, net, tempo=None, min_ring=3, emission=1000,
               account=None, accounts_file=None, rpc=None, relayer=None, **_):
        """Declare + deploy. devnet needs nothing; sepolia/mainnet need
        account= (an sncast account funded with STRK), optionally relayer=
        (posts the anonymous actions), accounts_file=, rpc=."""
        ctx = self._ctx(net, account, accounts_file, rpc)
        tempo = int(tempo or (50 if net == 'devnet' else 360))
        # declare is the slow step (sncast builds the package); skip it when
        # this exact source was already declared on this network's chain
        src = self._src_hash()
        known = load(CLASSES, {}).get(net, {})
        class_hash = known.get('class_hash') if known.get('src') == src else None
        if class_hash and not self._class_declared(net, class_hash):
            class_hash = None
        if not class_hash:
            try:
                dec = self._sncast(ctx, ['declare', '--contract-name', 'ZTensor',
                                         '--fee-token', ctx['fee']],
                                   ctx['owner'])
                class_hash = dec['class_hash']
            except RuntimeError as e:
                m = re.search(r'(0x[0-9a-fA-F]+) is already declared', str(e))
                if not m:
                    return {'ok': False, 'stage': 'declare', 'error': str(e)}
                class_hash = m.group(1)
            c = load(CLASSES, {})
            c[net] = {'src': src, 'class_hash': class_hash}
            save(CLASSES, c)
        owner_addr = load(ctx['accounts_file'], {}).get(
            ctx['section'], {}).get(ctx['owner'], {}).get('address')
        if not owner_addr:
            return {'ok': False, 'error': f"account {ctx['owner']!r} not in "
                    f"{ctx['accounts_file']} [{ctx['section']}]"}
        ctor = [int(owner_addr, 16), tempo, int(min_ring), int(emission)]
        try:
            dep = self._sncast(ctx, ['deploy', '--class-hash', class_hash,
                                     '--fee-token', ctx['fee'],
                                     '--constructor-calldata',
                                     *map(hexs, ctor)], ctx['owner'])
        except RuntimeError as e:
            return {'ok': False, 'stage': 'deploy', 'error': str(e)}
        rec = {'address': dep['contract_address'], 'class_hash': class_hash,
               'tx': dep['transaction_hash'], 'owner': owner_addr,
               'tempo': tempo, 'min_ring': int(min_ring),
               'emission': int(emission), 'rpc': ctx['rpc'],
               'at': int(time.time())}
        if net != 'devnet':
            rec.update(account=ctx['owner'], relayer=relayer or ctx['owner'],
                       accounts_file=ctx['accounts_file'])
        d = load(util.DEPLOYS, {})
        d[net] = rec
        save(util.DEPLOYS, d)
        return {'ok': True, 'network': net, **rec}

    def _src_hash(self):
        h = hashlib.sha256()
        for dp, dn, fn in sorted(os.walk(CONTRACT)):
            dn[:] = sorted(d for d in dn if d != 'target')
            for f in sorted(fn):
                if f.endswith(('.cairo', '.toml')):
                    fp = os.path.join(dp, f)
                    h.update(fp[len(CONTRACT):].encode())
                    h.update(open(fp, 'rb').read())
        return h.hexdigest()

    def _class_declared(self, network, class_hash):
        try:
            self._rpc(network, 'starknet_getClass', ['latest', class_hash])
            return True
        except Exception:  # noqa: BLE001
            return False

    def register(self, net, role, key):
        return {'tx': self._invoke(net, 'register', [
            role, int(key['x'], 16), int(key['y'], 16)], as_owner=True)}

    def set_open(self, net, flag):
        return self._invoke(net, 'set_open', [int(flag)], as_owner=True)

    def members(self, net, role):
        out = []
        for i in range(self._call(net, 'member_count', [role])[0]):
            x, y = self._call(net, 'member', [role, i])
            out.append({'x': hex(x), 'y': hex(y)})
        return out

    def epoch(self, net):
        d, b = self._dep(net), self.block(net)
        return {'epoch': self._call(net, 'epoch')[0], 'block': b,
                'tempo': d['tempo'], 'blocks_left': d['tempo'] - b % d['tempo']}

    def mine_scope(self, net, epoch):
        return self.signer.scope('mine', self._dep(net)['address'], epoch)

    # ---- anonymous actions ---------------------------------------------------

    def submit(self, net, name, work, ring):
        e = self._call(net, 'epoch')[0]
        sig, ms = self.signer.timed_sign(name, self.mine_scope(net, e), work, ring)
        ring_cd, sig_cd = sig_calldata(sig)
        tx = self._invoke(net, 'submit', ring_cd + [felt(work)] + sig_cd)
        return {'ok': True, 'epoch': e, 'tag': sig['tag'],
                'ring_size': len(sig['ring']), 'tx': tx, 'sign_ms': ms,
                **self._steps(net, tx)}

    def score(self, net, name, tag, score, ring):
        e, s = self._call(net, 'epoch')[0], int(score)
        sc = self.signer.scope('score', self._dep(net)['address'], e, tag)
        sig, ms = self.signer.timed_sign(name, sc, s, ring)
        ring_cd, sig_cd = sig_calldata(sig)
        tx = self._invoke(net, 'score', ring_cd + [felt(tag), s] + sig_cd)
        return {'ok': True, 'epoch': e, 'tag': hex(felt(tag)), 'score': s,
                'nullifier': sig['tag'], 'tx': tx, 'sign_ms': ms,
                **self._steps(net, tx)}

    def settle(self, net, epoch, tag):
        e, t = int(epoch), felt(tag)
        tx = self._invoke(net, 'settle', [e, t])
        c, r, _ = self._call(net, 'yuma', [e, t])
        return {'ok': True, 'epoch': e, 'tag': hex(t), 'consensus': c,
                'rank': r, 'tx': tx, **self._steps(net, tx)}

    def claim(self, net, name, epoch, recipient, ring):
        to = felt(recipient)
        sig, ms = self.signer.timed_sign(name, self.mine_scope(net, epoch), to,
                                         ring)
        ring_cd, sig_cd = sig_calldata(sig)
        tx = self._invoke(net, 'claim', [int(epoch)] + ring_cd + [to] + sig_cd)
        return {'ok': True, 'epoch': int(epoch), 'tag': sig['tag'],
                'recipient': hex(to), 'earned': self._call(net, 'earned', [to])[0],
                'tx': tx, 'sign_ms': ms, **self._steps(net, tx)}

    # ---- reads ---------------------------------------------------------------

    def tally(self, net, epoch=None):
        e = int(epoch) if epoch is not None else self._call(net, 'epoch')[0]
        rows = []
        for i in range(self._call(net, 'epoch_tags', [e])[0]):
            tag = self._call(net, 'tag_at', [e, i])[0]
            s, c = self._call(net, 'tally', [e, tag])
            con, rank, done = self._call(net, 'yuma', [e, tag])
            rows.append({'tag': hex(tag), 'score_sum': s, 'scores': c,
                         'claimed': bool(self._call(net, 'claimed', [e, tag])[0]),
                         'consensus': con, 'rank': rank, 'settled': bool(done)})
        v, rank_total, settled = self._call(net, 'yuma_epoch', [e])
        return {'epoch': e, 'total': self._call(net, 'epoch_total', [e])[0],
                'validators': v, 'rank_total': rank_total, 'settled': settled,
                'tags': rows}

    def earned(self, net, recipient):
        return {'recipient': hex(felt(recipient)),
                'earned': self._call(net, 'earned', [felt(recipient)])[0]}

    def board(self, net, epochs=6):
        d = load(util.DEPLOYS, {}).get(net)
        out = {'network': net, 'deployed': bool(d),
               'devnet_up': self._alive() if net == 'devnet' else None}
        if not d or (net == 'devnet' and not out['devnet_up']):
            return out
        owner, tempo, min_ring, emission, is_open = self._call(net, 'params')
        b = self.block(net)
        e = b // tempo
        out.update(contract=d['address'], owner=hex(owner), tempo=tempo,
                   min_ring=min_ring, emission=emission, open=bool(is_open),
                   block=b, epoch=e, blocks_left=tempo - b % tempo,
                   miners=self.members(net, 1), validators=self.members(net, 2))
        rows = []
        for ep in range(e, max(-1, e - int(epochs)), -1):
            t = self.tally(net, ep)
            if t['tags']:
                t['open'] = ep == e
                rows.append(t)
        out['epochs'] = rows
        return out

    # ---- simulate hooks --------------------------------------------------------

    def payee(self, net, dep):
        return load(os.path.join(DEVNET_DIR, 'accounts.json'), {})[
            NETWORKS[net]['section']]['payee']['address']


ADAPTER = Starknet
