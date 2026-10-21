"""ztao on postquant: contract/ (crate ztao_pq) — the subnet as a pqc1
contract, an importless wasm state transition. lsag.rs / fp.rs / poseidon.rs
are the NEAR contract's files (included by path), so the SAME prover output
verifies here. client.py is the wire (our own postquant node, runs, state).

What is post-quantum here, and what is not:
    every action is a pqr1 receipt {code, state_in, input, state_out, prev}
    signed by an ML-DSA-44 wallet (FIPS 204) and anchored on a chain whose
    every commitment is SHA3-256: the execution history cannot be forged or
    rewritten by a quantum adversary. The anonymity is still LSAG over the
    STARK curve, like every other chain: a discrete-log solver could forge
    ring signatures and link key images back to keys. See README "Post-
    quantum" for the line between the two.

Networks:
    postquant    our own postquant node on :51506, chain ztao-pq
                 (data/postquant/; wallets relayer + payee)
    postquant-1  any running postquant node: ZTAO_PQ_URL (default the
                 fleet's :51030), ZTAO_PQ_TOKEN, ZTAO_PQ_WALLET

Execution is off chain: the contract owner (the relayer) runs every action
and every verifier replays it. Cost = the anchor tx's gas (witness gas for
2420 ML-DSA bytes dominates) + run_ms, the wasm time each replay repeats.
"""
import os

from ...core.util import felt
from ..base import Network
from .client import (MAX_RING, PAYEE, POSTQUANT, WASM, PostquantZTao,
                     enc_claim,
                     enc_open, enc_register, enc_score, enc_settle,
                     enc_submit, text_felt)


class Postquant(Network):
    chain = 'postquant'
    title = 'Postquant'
    nets = ('postquant', 'postquant-1')
    order = 50
    max_ring = MAX_RING
    # every action mines a block on postquant, so an epoch must outlast the
    # registers' tail + 3 submits + every score of a simulate()
    sim_tempo = 40
    sweep_tempo = 400
    cost_fields = ('gas', 'run_ms')
    deploy_fields = ('wasm_bytes', 'code', 'chain_id')
    ui = {'name': 'postquant', 'local': 'postquant node', 'color': '#9d7bff',
          'verifier': 'by a pqc1 wasm contract, run off chain and proven by '
                      'ML-DSA-signed receipts anyone replays',
          'deploying': 'storing the pqc1 verifier on the postquant chain'}
    sources = (
        ('postquant · Rust (pqc1 wasm)', [
            ('contract/src/lib.rs', 'the subnet as a pure state transition'),
            ('contract/src/tests.rs', 'cargo suite: Cairo fixture + full epoch'),
            ('contract/Cargo.toml', 'crate + build profile')]),
        ('postquant · adapter', [
            ('adapter.py', 'the Network: scopes, hints, actions'),
            ('client.py', 'own node, runs + receipts, state decoder'),
            ('tests/test_postquant.py', 'adapter tests')]),
    )

    def __init__(self, signer):
        super().__init__(signer)
        from ... import DATA
        self.c = PostquantZTao(DATA)

    # ---- lifecycle ---------------------------------------------------------

    def health(self):
        return {'up': self.c._alive(), 'built': os.path.exists(WASM),
                'node_src': os.path.exists(os.path.join(POSTQUANT, 'api.py'))}

    def build(self):
        return self.c.build()

    def test(self):
        return self.c.test()

    def up(self, action='status'):
        return self.c.node(action)

    def block(self, net):
        return self.c.block(net)

    def advance(self, net, to_block):
        gap = to_block - self.c.block(net)
        if gap > 0:
            self.c.mine(net, gap)

    # ---- the contract --------------------------------------------------------

    def deploy(self, net, tempo=None, min_ring=3, emission=1000, **_):
        if net == 'postquant':
            st = self.c.node('start')
            if not st.get('up'):
                return {'ok': False, 'stage': 'node', 'error': st.get('error')}
        return self.c.deploy(net, tempo=int(tempo or self.sim_tempo),
                             min_ring=min_ring, emission=emission)

    def payee(self, net, dep):
        return self.c.address(net, PAYEE)

    def contract_id(self, dep):
        return dep.get('contract')

    def _run(self, net, inp):
        return self.c.run(net, inp)

    def register(self, net, role, key):
        r = self._run(net, enc_register(self.c.block(net), role, key))
        return {'tx': r['tx'], 'gas': r['gas'], 'run_ms': r['run_ms'],
                'receipt': r['receipt']}

    def set_open(self, net, flag):
        return self._run(net, enc_open(self.c.block(net), flag))['tx']

    def members(self, net, role):
        s = self.c.state(net)
        return list(s['miners' if int(role) == 1 else 'validators'])

    def epoch(self, net):
        t, b = self.c.dep(net)['tempo'], self.c.block(net)
        return {'epoch': b // t, 'block': b, 'tempo': t,
                'blocks_left': t - b % t}

    def _subnet(self, net):
        return felt(self.c.dep(net)['subnet'])

    def mine_scope(self, net, epoch):
        return self.signer.scope('mine', self._subnet(net), int(epoch))

    # ---- anonymous actions ---------------------------------------------------

    def _sig(self, name, scope, msg, ring):
        sig, ms = self.signer.timed_sign(name, scope, msg, ring)
        # Hp(scope) as a hint: the contract checks it instead of a sqrt
        return sig, ms, self.signer.hash_point(scope)

    def submit(self, net, name, work, ring):
        h = self.c.block(net)
        e = h // self.c.dep(net)['tempo']
        msg = felt(work)
        sig, ms, hp = self._sig(name, self.mine_scope(net, e), msg, ring)
        r = self._run(net, enc_submit(h, msg, sig, hp))
        return {'ok': True, 'epoch': e, 'tag': sig['tag'],
                'ring_size': len(sig['ring']), 'tx': r['tx'], 'gas': r['gas'],
                'run_ms': r['run_ms'], 'receipt': r['receipt'], 'sign_ms': ms}

    def score(self, net, name, tag, score, ring):
        h = self.c.block(net)
        e = h // self.c.dep(net)['tempo']
        tag, msg = felt(tag), int(score)
        scope = self.signer.scope('score', self._subnet(net), e, tag)
        sig, ms, hp = self._sig(name, scope, msg, ring)
        r = self._run(net, enc_score(h, tag, msg, sig, hp))
        return {'ok': True, 'epoch': e, 'tag': hex(tag), 'score': msg,
                'nullifier': sig['tag'], 'tx': r['tx'], 'gas': r['gas'],
                'run_ms': r['run_ms'], 'receipt': r['receipt'], 'sign_ms': ms}

    def settle(self, net, epoch, tag):
        """Zuma consensus for one tag of a closed epoch (core/yuma.py),
        computed inside the contract; a re-settle reverts -> RuntimeError."""
        e, tag = int(epoch), felt(tag)
        r = self._run(net, enc_settle(self.c.block(net), e, tag))
        t = _find_tag(r['state'], e, tag)
        return {'ok': True, 'epoch': e, 'tag': hex(tag),
                'consensus': t['consensus'], 'rank': t['rank'],
                'tx': r['tx'], 'gas': r['gas'], 'run_ms': r['run_ms']}

    def claim(self, net, name, epoch, recipient, ring):
        e, who = int(epoch), text_felt(recipient)
        sig, ms, hp = self._sig(name, self.mine_scope(net, e), who, ring)
        before = (self.c.state(net)['credit']).get(hex(who), 0)
        r = self._run(net, enc_claim(self.c.block(net), e, who, sig, hp))
        earned = r['state']['credit'].get(hex(who), 0)
        return {'ok': True, 'epoch': e, 'tag': sig['tag'],
                'recipient': recipient, 'amount': earned - before,
                'earned': earned, 'tx': r['tx'], 'gas': r['gas'],
                'run_ms': r['run_ms'], 'receipt': r['receipt'], 'sign_ms': ms}

    # ---- reads ---------------------------------------------------------------

    def tally(self, net, epoch=None):
        s = self.c.state(net)
        e = int(epoch) if epoch is not None else \
            self.c.block(net) // s['tempo']
        return _tally(s, e)

    def earned(self, net, recipient):
        s = self.c.state(net)
        return {'recipient': recipient,
                'earned': s['credit'].get(hex(text_felt(recipient)), 0)}

    def audit(self, net):
        """Replay every receipt of the run (client.audit)."""
        return self.c.audit(net)

    def receipt(self, net):
        """Replay-verify the anchored receipt (postquant `contract verify`)."""
        return self.c.verify(net)

    def board(self, net, epochs=6):
        d = self.c._deploys().get(net)
        up = self.c._alive(net)
        out = {'network': net, 'chain': 'postquant', 'deployed': bool(d),
               'devnet_up': up if net == 'postquant' else None}
        if not d or not up:
            return out
        s = self.c.state(net)
        b = self.c.block(net)
        e = b // s['tempo']
        out.update(contract=d['contract'], owner=d['owner'], tempo=s['tempo'],
                   min_ring=s['min_ring'], emission=s['emission'],
                   open=s['open'], subnet=s['subnet'], block=b, epoch=e,
                   blocks_left=s['tempo'] - b % s['tempo'],
                   miners=s['miners'], validators=s['validators'],
                   code=d['code'], chain_id=d.get('chain_id'))
        rows = []
        for ep in range(e, max(-1, e - int(epochs)), -1):
            t = _tally(s, ep)
            if t['tags']:
                t['open'] = ep == e
                rows.append(t)
        out['epochs'] = rows
        return out


def _find_tag(state, epoch, tag):
    for ep in state['epochs']:
        if ep['epoch'] == epoch:
            for t in ep['tags']:
                if int(t['tag'], 16) == tag:
                    return t
    raise RuntimeError(f'tag {tag:#x} not in epoch {epoch}')


def _tally(s, e):
    ep = next((x for x in s['epochs'] if x['epoch'] == e), None) or {}
    return {'epoch': e, 'total': ep.get('total', 0),
            'validators': ep.get('vset', 0),
            'rank_total': ep.get('rank_total', 0),
            'settled': ep.get('nsettled', 0),
            'tags': [{'tag': t['tag'], 'score_sum': t['score_sum'],
                      'scores': t['scores'], 'claimed': t['claimed'],
                      'consensus': t['consensus'], 'rank': t['rank'],
                      'settled': t['settled']}
                     for t in ep.get('tags', [])]}


ADAPTER = Postquant
