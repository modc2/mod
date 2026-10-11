//! zq — the zquorum circuit.
//!
//! Anonymous quorum votes with a commit/nullifier scheme (Semaphore-style,
//! Pedersen flavoured). All hashing lives HERE so every party — the anchor,
//! a future on-chain verifier, a future STARK prover — agrees byte-for-byte:
//!
//!   commitment  C = H(tag_c, secret)            registered once per member
//!   nullifier   N = H(H(tag_n, secret), topic)  revealed when voting
//!   root        R = fold H over the sorted commitment set
//!
//! N is unlinkable to any C without the secret, and a repeated N is a
//! double vote. Run under a Cairo prover the private input is only the
//! secret; everything main() returns is public.

use core::pedersen::pedersen;

const COMMIT_TAG: felt252 = 'zq-commit';
const NULLIFIER_TAG: felt252 = 'zq-null';
const ROOT_SEED: felt252 = 'zq-root';

pub fn commit(secret: felt252) -> felt252 {
    pedersen(COMMIT_TAG, secret)
}

pub fn nullifier(secret: felt252, topic: felt252) -> felt252 {
    pedersen(pedersen(NULLIFIER_TAG, secret), topic)
}

/// Canonical accumulator over the member set. Callers must pass the
/// commitments in a canonical (sorted) order or roots will disagree.
pub fn root(leaves: Span<felt252>) -> felt252 {
    let mut acc = ROOT_SEED;
    let mut i = 0;
    while i < leaves.len() {
        acc = pedersen(acc, *leaves.at(i));
        i += 1;
    };
    acc
}

pub fn is_member(commitment: felt252, leaves: Span<felt252>) -> bool {
    let mut found = false;
    let mut i = 0;
    while i < leaves.len() {
        if *leaves.at(i) == commitment {
            found = true;
        }
        i += 1;
    };
    found
}

/// The vote statement. Private: secret. Public: everything returned.
/// ok = 1 iff commit(secret) is one of the leaves.
pub fn vote_check(
    secret: felt252, topic: felt252, leaves: Span<felt252>,
) -> (felt252, felt252, felt252) {
    let ok = if is_member(commit(secret), leaves) {
        1
    } else {
        0
    };
    (root(leaves), nullifier(secret, topic), ok)
}

/// CLI entry for `scarb cairo-run`. args[0] selects the op; the return is
/// always a zero-padded triple so the runner prints plain values:
///   1 commit:     [1, secret]                 -> (C, 0, 0)
///   2 nullifier:  [2, secret, topic]          -> (N, 0, 0)
///   3 root:       [3, leaf...]                -> (R, 0, 0)
///   4 vote:       [4, secret, topic, leaf...] -> (R, N, ok)
fn main(mut args: Array<felt252>) -> (felt252, felt252, felt252) {
    let op = args.pop_front().expect('op required');
    if op == 1 {
        (commit(args.pop_front().expect('secret')), 0, 0)
    } else if op == 2 {
        let secret = args.pop_front().expect('secret');
        let topic = args.pop_front().expect('topic');
        (nullifier(secret, topic), 0, 0)
    } else if op == 3 {
        (root(args.span()), 0, 0)
    } else if op == 4 {
        let secret = args.pop_front().expect('secret');
        let topic = args.pop_front().expect('topic');
        vote_check(secret, topic, args.span())
    } else {
        core::panic_with_felt252('bad op')
    }
}

#[cfg(test)]
mod tests {
    use super::{commit, is_member, nullifier, root, vote_check};

    #[test]
    fn commit_is_deterministic_and_distinct() {
        assert(commit(7) == commit(7), 'det');
        assert(commit(7) != commit(8), 'distinct');
    }

    #[test]
    fn nullifier_binds_secret_and_topic() {
        assert(nullifier(7, 1) != nullifier(7, 2), 'topic binds');
        assert(nullifier(7, 1) != nullifier(8, 1), 'secret binds');
        // domain separation: a nullifier never collides with a commitment
        assert(nullifier(7, 1) != commit(7), 'domains');
    }

    #[test]
    fn root_is_order_sensitive_hence_canonical_order() {
        let a = array![commit(1), commit(2)];
        let b = array![commit(2), commit(1)];
        assert(root(a.span()) != root(b.span()), 'order matters');
        assert(root(a.span()) == root(a.span()), 'det');
    }

    #[test]
    fn member_vote_accepted() {
        let leaves = array![commit(7), commit(8), commit(9)];
        let (r, n, ok) = vote_check(7, 42, leaves.span());
        assert(ok == 1, 'member ok');
        assert(r == root(leaves.span()), 'root echoes');
        assert(n == nullifier(7, 42), 'null matches');
        assert(is_member(commit(8), leaves.span()), 'is_member');
    }

    #[test]
    fn outsider_rejected() {
        let leaves = array![commit(7), commit(8)];
        let (_r, _n, ok) = vote_check(99, 42, leaves.span());
        assert(ok == 0, 'outsider');
    }

    #[test]
    fn same_secret_same_topic_same_nullifier() {
        // the double-vote guard: a second vote by the same member on the
        // same topic reproduces the nullifier exactly
        let leaves = array![commit(7), commit(8)];
        let (_r1, n1, _o1) = vote_check(7, 42, leaves.span());
        let (_r2, n2, _o2) = vote_check(7, 42, leaves.span());
        assert(n1 == n2, 'replay detected');
    }
}
