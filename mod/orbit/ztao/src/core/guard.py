"""Pre-flight checks: refuse an action locally when the chain would revert it.

On every chain a reverted submit/score/claim still costs the relayer its
fee (gas, Tgas, CU, weight). These checks read only public state the
adapter already exposes (members, tally, board) and raise ValueError with
the reason before anything is signed or posted.
Also: the anonymity report, i.e. how big a crowd each member hides in.
"""
import math

from .util import felt
from .yuma import MAX_SCORE as SCORE_MAX

MIN_RING = 3  # the contracts' default min_ring


def score_value(score):
    """0..SCORE_MAX as an int; the contracts reject anything else."""
    try:
        f = float(score)
    except (TypeError, ValueError):
        raise ValueError(f'score must be a number, got {score!r}') from None
    if f != int(f) or not 0 <= f <= SCORE_MAX:
        raise ValueError(f'score must be an integer 0..{SCORE_MAX}, got {score!r}')
    return int(f)


def ring_size(ring, min_ring=MIN_RING):
    """The contracts refuse rings below min_ring; so do we, before signing."""
    n = len(ring)
    if n < int(min_ring or MIN_RING):
        raise ValueError(f'ring of {n} is below min_ring {min_ring}: register '
                         f'more members (or deploy with a smaller min_ring)')
    return n


def _same(a, b):
    return felt(a) == felt(b)


def tag_open(tally, tag):
    """A score must target a tag submitted in the CURRENT epoch."""
    if not any(_same(t['tag'], tag) for t in tally.get('tags', [])):
        raise ValueError(f'tag {tag} has no submission in epoch '
                         f'{tally.get("epoch")}: nothing to score')


def claimable(tally, tag=None):
    """A claim needs every tag of the epoch settled, and (when the tag is
    known) one not claimed already — both revert on chain otherwise."""
    tags = tally.get('tags', [])
    if not tags:
        raise ValueError(f'epoch {tally.get("epoch")} has no submissions')
    left = [t['tag'] for t in tags if not t.get('settled')]
    if left:
        raise ValueError(f'{len(left)} tag(s) of epoch {tally.get("epoch")} '
                         f'not settled yet: run settle(epoch) first')
    if tag is not None:
        mine = next((t for t in tags if _same(t['tag'], tag)), None)
        if mine is None:
            raise ValueError(f'your tag {tag} did not submit in epoch '
                             f'{tally.get("epoch")}')
        if mine.get('claimed'):
            raise ValueError(f'tag {tag} already claimed epoch {tally.get("epoch")}')


def anonymity(n_members, ring, max_ring, min_ring=MIN_RING):
    """The rings a set of n_members is cut into (networks.base.partition_ring)
    and what that buys: the smallest crowd anyone hides in, in members and
    bits, plus plain-language warnings."""
    n = int(n_members)
    size = max(1, min(int(ring or max_ring), int(max_ring), n or 1))
    blocks = n // size if n else 0
    # the short tail borrows from the block before it, so every ring is full
    # but the tail's members overlap: the guaranteed crowd is still `size`
    out = {'members': n, 'ring': size if n else 0, 'rings': blocks + (n % size > 0),
           'crowd': size if n else 0, 'bits': round(math.log2(size), 2) if n else 0.0,
           'whole_set': n <= size, 'warnings': []}
    w = out['warnings']
    if n == 0:
        w.append('no members registered')
    elif n < int(min_ring or MIN_RING):
        w.append(f'only {n} member(s): below min_ring {min_ring}, actions revert')
    elif size < 8:
        w.append(f'crowd of {size}: one guess in {size} is right; aim for 8+')
    if n > size:
        w.append(f'set of {n} > ring {size}: you hide in your block of {size}, '
                 f'not the whole set (cap {max_ring} on this chain)')
    return out
