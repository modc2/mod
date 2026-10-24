"""Simple secure aggregation: the validator set's scores, summed without
revealing any single one of them.

Why it is here: every rule in core/consensus.py is a pure function of a
tag's score HISTOGRAM, and a histogram is a SUM of one-hot vectors (one per
validator). A sum is the one thing that can be computed with nothing but
pairwise masks that cancel — so the set can hand the chain exactly what
consensus needs and nothing else.

The scheme (pairwise masks, the simple half of Bonawitz et al. 2017):

    shared_ij  = x_i * P_j = x_j * P_i          Diffie-Hellman on the STARK
                                                curve, the SAME keys the ring
                                                signatures already use
    m_ij       = PRG(Poseidon(shared_ij, scope), n)     identical for i and j
    y_i        = v_i + sum_{j>i} m_ij - sum_{j<i} m_ij  (mod Q)

Every pair contributes +m_ij once and -m_ij once, so

    sum_i y_i = sum_i v_i   (mod Q)

and the aggregator (or anyone, the y_i are public) learns the total and
nothing else: any single y_i is one-time-pad masked by its V-1 peers.

Dropouts. If a validator never sends its y, its masks do not cancel. Each
survivor i reveals ONE recovery vector r_i = sum_{j dropped} sign(i,j) m_ij
(the masks it shared with the dropped set, nothing about v_i), and the
aggregator subtracts them. This stays private while every survivor keeps at
least one surviving peer; with only one survivor left its input would be
exposed, so combine() refuses (MIN_SURVIVORS).

Arithmetic is mod Q = 2^32: wraparound is exact, so the recovered sum is the
true sum whenever it is below Q (a 0..100 histogram bucket over fewer than
4e7 validators always is). Masks come from the shared secret through
Poseidon (the curve's own hash, so the seed derivation matches the
contracts) and then blake2b in counter mode as the PRG — one Poseidon per
PEER, not per element, which is what keeps a 256-tag round fast.

What this changes on chain (see core/forecast.py, strategy 'secagg'): the
V x M score transactions become ONE aggregate per epoch. That is the whole
reason a 256-miner subnet can cost cents a week instead of thousands.
"""
import hashlib

from . import lsag, stark, util, yuma
from .consensus import RULES, settle

Q = 1 << 32                    # mask/aggregate modulus: exact wraparound
BUCKETS = yuma.MAX_SCORE + 1   # a tag's histogram is 0..100
MIN_SURVIVORS = 2              # below this, revealing masks exposes an input
SCOPE = stark.short('ztao-secagg')


def scope(subnet, epoch):
    """The round's domain tag — one aggregation per (subnet, epoch), so masks
    never repeat across rounds (a repeated pad is a broken pad)."""
    return stark.h(SCOPE, util.felt(subnet), util.felt(epoch))


def shared(secret, pub):
    """Poseidon(x_i * P_j) — the same felt for both sides of the pair."""
    pt = lsag.mul(int(secret) % util.ORDER,
                  (util.felt(pub[0]), util.felt(pub[1])))
    if pt is None:
        raise ValueError('degenerate shared secret (peer key is the identity)')
    return stark.h(pt[0], pt[1])


def prg(seed, n):
    """n values mod Q from one felt seed: blake2b in counter mode."""
    out, base = [], int(seed).to_bytes(32, 'big')
    for blk in range((n * 4 + 63) // 64):
        d = hashlib.blake2b(base + blk.to_bytes(8, 'big'), digest_size=64).digest()
        out += [int.from_bytes(d[k:k + 4], 'big') for k in range(0, 64, 4)]
    return out[:n]


def pairmask(secret, pub, rnd, n):
    return prg(stark.h(shared(secret, pub), rnd), n)


class Round:
    """One epoch's aggregation: a fixed, publicly ordered validator set and a
    fixed tag order, so every member builds the same vector layout and the
    same mask signs.

    members: [(id, pubkey)] in any order — sorted by id here, exactly as the
    contracts sort ring key ids, so the ordering is not something a member
    can choose.
    """

    def __init__(self, members, tags, subnet=0, epoch=0, rnd=None):
        self.members = sorted(((util.felt(i), (util.felt(p[0]), util.felt(p[1])))
                               for i, p in members), key=lambda m: m[0])
        if len({i for i, _ in self.members}) != len(self.members):
            raise ValueError('duplicate member id in the aggregation set')
        self.ids = [i for i, _ in self.members]
        self.tags = [util.felt(t) for t in tags]
        if len(set(self.tags)) != len(self.tags):
            raise ValueError('duplicate tag in the aggregation set')
        self.rnd = rnd if rnd is not None else scope(subnet, epoch)
        self.n = len(self.tags) * BUCKETS
        self._masks = {}        # (secret, peer index) -> mask, derived once

    # ---- layout -----------------------------------------------------------

    def index(self, member_id):
        """Where a member sits in the public order (its mask signs)."""
        try:
            return self.ids.index(util.felt(member_id))
        except ValueError:
            raise ValueError(f'{member_id!r} is not in this round') from None

    def slot(self, tag, score):
        t = self.tags.index(util.felt(tag))
        return t * BUCKETS + yuma_score(score)

    def onehot(self, scores_by_tag):
        """One validator's vote vector: a 1 in the bucket it gave each tag.
        Tags it did not score stay all-zero (Zuma counts a non-vote as 0 via
        Vt, so an absent vote must NOT become a vote of 0 here)."""
        v = [0] * self.n
        for tag, s in scores_by_tag.items():
            v[self.slot(tag, s)] += 1
        return v

    # ---- the three messages ------------------------------------------------

    def mask(self, secret, j):
        """The pad member `secret` shares with peer j (cached: one scalar
        mult per peer per round, whatever it is used for)."""
        key = (int(secret), j)
        if key not in self._masks:
            self._masks[key] = pairmask(secret, self.members[j][1], self.rnd,
                                        self.n)
        return self._masks[key]

    def _masked_sum(self, i, secret, peers, base):
        """base +/- the pads shared with `peers`, by the public order sign."""
        acc = base
        for j in peers:
            m = self.mask(secret, j)
            acc = [a + b for a, b in zip(acc, m)] if j > i else \
                  [a - b for a, b in zip(acc, m)]
        return [a % Q for a in acc]

    def conceal(self, member_id, secret, scores_by_tag):
        """What a validator publishes: its masked vote vector."""
        i = self.index(member_id)
        peers = [j for j in range(len(self.members)) if j != i]
        return self._masked_sum(i, secret, peers, self.onehot(scores_by_tag))

    def recover(self, member_id, secret, dropped):
        """A survivor's ONE recovery vector: the pads it shared with the
        dropped members, signed as they were used. Leaks no input."""
        i = self.index(member_id)
        drop = sorted(self.index(d) for d in dropped)
        if i in drop:
            raise ValueError('a dropped member cannot help recover')
        return self._masked_sum(i, secret, drop, [0] * self.n)

    def combine(self, masked, recovery=()):
        """Aggregate: sum the masked vectors, subtract the recovery vectors.
        masked: {member_id: y}. The result is the plain total vote vector."""
        ids = [util.felt(m) for m in masked]
        unknown = [i for i in ids if i not in set(self.ids)]
        if unknown:
            raise ValueError(f'{len(unknown)} masked vector(s) from outside the round')
        if len(ids) < MIN_SURVIVORS and len(self.members) > 1:
            raise ValueError(
                f'{len(ids)} of {len(self.members)} validators reported: below '
                f'{MIN_SURVIVORS} the recovery round would expose an input, so '
                'the aggregate is refused (the epoch has no consensus)')
        total = [0] * self.n
        for y, sign in [(y, 1) for y in masked.values()] + \
                       [(r, -1) for r in recovery]:
            if len(y) != self.n:
                raise ValueError(f'vector of {len(y)}, expected {self.n}')
            total = [a + sign * b for a, b in zip(total, y)]
        return [a % Q for a in total]

    # ---- what consensus consumes ------------------------------------------

    def hists(self, total):
        """{tag: histogram} — exactly the input core/consensus.py rules take."""
        return {t: [int(x) for x in total[i * BUCKETS:(i + 1) * BUCKETS]]
                for i, t in enumerate(self.tags)}

    def settle(self, total, validators=None, rule='yuma', emission=0):
        """Run a consensus rule straight off the aggregate. validators
        defaults to the full set (Vt = max(V, votes) does the rest)."""
        V = len(self.members) if validators is None else int(validators)
        hs = self.hists(total)
        tags, rank_total = {}, 0
        for t, h in hs.items():
            c, r = settle(h, V, rule)
            tags[t] = {'consensus': c, 'rank': r, 'votes': sum(h),
                       'raw': sum(s * n for s, n in enumerate(h))}
            rank_total += r
        for row in tags.values():
            row['share'] = int(emission) * row['rank'] // rank_total \
                if rank_total else 0
        return {'rule': rule, 'validators': V, 'need': yuma.need(V),
                'rank_total': rank_total, 'emission': int(emission),
                'tags': tags}


def yuma_score(s):
    s = int(s)
    if not 0 <= s <= yuma.MAX_SCORE:
        raise ValueError(f'score {s} outside 0..{yuma.MAX_SCORE}')
    return s


def messages(n_validators, n_tags):
    """Bytes on the wire for one round, per message kind — the cost that
    REPLACES V x M on-chain score transactions. Everything here is off
    chain; only the aggregate (or its commitment) is posted."""
    n = n_tags * BUCKETS
    vec = n * 4
    return {'buckets': BUCKETS, 'vector_values': n, 'vector_bytes': vec,
            'masked_total_bytes': vec * int(n_validators),
            'aggregate_bytes': vec,
            'onchain_values': n_tags * 2,   # (consensus, rank) per tag
            'pairmasks_per_validator': int(n_validators) - 1}


def demo(validators=8, tags=4, scores=None, rule='yuma', drop=0, emission=1000,
         seed=7):
    """A whole round end to end, as the console and tests run it: keys,
    masked vectors, (optional) dropouts and recovery, the aggregate, and the
    PROOF that it equals the histogram of the plain scores.

    scores: optional {tag: [score per validator]}; otherwise deterministic
    pseudo-scores from `seed`."""
    import random
    rng = random.Random(seed)
    V, T = max(1, int(validators)), max(1, int(tags))
    drop = max(0, min(int(drop), V - MIN_SURVIVORS)) if V > 1 else 0
    keys = {}                                   # member id -> (secret, pubkey)
    for _ in range(V):
        x = 1 + rng.randrange(util.ORDER - 1)
        p = stark.mul(x, stark.G)
        keys[stark.key_id(p)] = (x, p)
    r = Round([(i, p) for i, (_, p) in keys.items()], range(1, T + 1),
              subnet=1, epoch=1)

    # one column of scores per tag, in the round's public member order
    given = list((scores or {}).values())
    plain = {}
    for ti, t in enumerate(r.tags):
        col = [int(s) for s in ((scores or {}).get(t) or
                                (given[ti] if ti < len(given) else []))][:V]
        plain[t] = col + [rng.randrange(0, yuma.MAX_SCORE + 1)
                          for _ in range(V - len(col))]

    dropped = r.ids[V - drop:]
    live = [k for k, i in enumerate(r.ids) if i not in dropped]
    masked, recovery = {}, []
    for k, i in enumerate(r.ids):
        if i in dropped:
            continue
        masked[i] = r.conceal(i, keys[i][0], {t: plain[t][k] for t in r.tags})
        if dropped:
            recovery.append(r.recover(i, keys[i][0], dropped))
    total = r.combine(masked, recovery)

    got = r.hists(total)
    reference = {t: [plain[t][k] for k in live] for t in r.tags}
    expect = {t: yuma.hist(ss) for t, ss in reference.items()}
    out = r.settle(total, rule=rule, emission=emission)
    return {'validators': V, 'tags': T, 'rule': rule,
            'dropped': len(dropped), 'reported': len(masked),
            'modulus': Q, 'exact': got == expect,
            'hists': {str(t): got[t] for t in r.tags},
            'reference_hists': {str(t): expect[t] for t in r.tags},
            'reference_scores': {str(t): reference[t] for t in r.tags},
            'leaked': 'nothing: each y_i is one-time-pad masked by its peers',
            'wire': messages(len(masked), T),
            'rules': sorted(RULES), 'settled': out,
            'matches_plain': out == _plain_settle(reference, V, rule, emission)}


def _plain_settle(scores_by_tag, V, rule, emission):
    """The same epoch settled from the scores in the clear — what the
    aggregate has to match for the round to be sound."""
    tags, total = {}, 0
    for t, ss in scores_by_tag.items():
        c, rk = settle(yuma.hist(ss), V, rule)
        tags[t] = {'consensus': c, 'rank': rk, 'votes': len(ss),
                   'raw': sum(int(s) for s in ss)}
        total += rk
    for row in tags.values():
        row['share'] = int(emission) * row['rank'] // total if total else 0
    return {'rule': rule, 'validators': V, 'need': yuma.need(V),
            'rank_total': total, 'emission': int(emission), 'tags': tags}
