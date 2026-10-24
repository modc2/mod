//! Scope-linkable ring signatures (LSAG, Liu-Wei-Wong) over the STARK curve.
//!
//! A signer proves "I hold the secret key of ONE of these public keys"
//! without revealing which. The signature carries a key image
//!
//!     I = x * Hp(scope)
//!
//! which is the same for every signature one key makes in one scope and
//! unlinkable across scopes. ztensor uses it two ways:
//!   - miner scope  = (subnet, epoch)        -> I is the miner's per-epoch tag
//!   - score scope  = (subnet, epoch, tag)   -> I is a one-score-per-validator nullifier
//!
//! Everything here is pure Cairo over the native EC builtin, so the exact
//! same code verifies on Starknet (contract) and signs off-chain (core/prover/).
//! Hp has no known discrete log: x = poseidon(tag, scope, ctr), first ctr
//! that lands on the curve. The STARK curve has prime order (no cofactor).

use core::ec::{EcPoint, EcPointTrait, NonZeroEcPoint, stark_curve};
use core::poseidon::poseidon_hash_span;
use core::dict::Felt252Dict;

pub const TAG_H2P: felt252 = 'zuma-h2p';
pub const TAG_MSG: felt252 = 'zuma-msg';
pub const TAG_CHAL: felt252 = 'zuma-chal';
pub const TAG_KEY: felt252 = 'zuma-key';

#[derive(Drop, Copy, Serde, PartialEq, Debug, starknet::Store)]
pub struct Point {
    pub x: felt252,
    pub y: felt252,
}

#[derive(Drop, Copy, Serde, Debug)]
pub struct Signature {
    pub c0: felt252,
    pub s: Span<felt252>,
    pub image: Point,
}

fn generator() -> EcPoint {
    EcPointTrait::new(stark_curve::GEN_X, stark_curve::GEN_Y).unwrap()
}

fn order() -> u256 {
    stark_curve::ORDER.into()
}

/// Affine coordinates; the point at infinity maps to (0, 0), which is not
/// on the curve, so it can never be confused with a real point.
pub fn xy(p: EcPoint) -> (felt252, felt252) {
    let nz: Option<NonZeroEcPoint> = p.try_into();
    match nz {
        Option::Some(q) => q.coordinates(),
        Option::None => (0, 0),
    }
}

pub fn to_point(p: EcPoint) -> Point {
    let (x, y) = xy(p);
    Point { x, y }
}

/// Some(point) iff (x, y) is a non-zero point on the curve.
pub fn to_ec(p: Point) -> Option<EcPoint> {
    EcPointTrait::new(p.x, p.y)
}

/// Canonical scalar: secret mod ORDER, as a felt.
pub fn scalar(v: felt252) -> felt252 {
    let r: u256 = v.into() % order();
    r.try_into().unwrap()
}

pub fn public_key(secret: felt252) -> Point {
    to_point(generator().mul(scalar(secret)))
}

/// Stable id of a public key — what the contract keys membership by and
/// what rings are sorted by.
pub fn key_id(p: Point) -> felt252 {
    poseidon_hash_span(array![TAG_KEY, p.x, p.y].span())
}

pub fn hash_to_point(scope: felt252) -> EcPoint {
    let mut ctr: felt252 = 0;
    loop {
        let x = poseidon_hash_span(array![TAG_H2P, scope, ctr].span());
        match EcPointTrait::new_from_x(x) {
            Option::Some(p) => { break p; },
            Option::None => { ctr += 1; },
        }
    }
}

/// The key image a secret produces in a scope (also the miner tag point).
pub fn key_image(secret: felt252, scope: felt252) -> Point {
    to_point(hash_to_point(scope).mul(scalar(secret)))
}

fn digest(ring: Span<Point>, scope: felt252, msg: felt252, image: Point) -> felt252 {
    let mut data: Array<felt252> = array![TAG_MSG, scope, msg, image.x, image.y, ring.len().into()];
    for p in ring {
        data.append(*p.x);
        data.append(*p.y);
    };
    poseidon_hash_span(data.span())
}

fn challenge(d: felt252, l: EcPoint, r: EcPoint) -> felt252 {
    let (lx, ly) = xy(l);
    let (rx, ry) = xy(r);
    poseidon_hash_span(array![TAG_CHAL, d, lx, ly, rx, ry].span())
}

/// Verify a ring signature. Pure: no storage, no syscalls.
pub fn verify(ring: Span<Point>, scope: felt252, msg: felt252, sig: Signature) -> bool {
    let n = ring.len();
    if n == 0 || sig.s.len() != n {
        return false;
    }
    let image = match to_ec(sig.image) {
        Option::Some(p) => p,
        Option::None => { return false; },
    };
    let h = hash_to_point(scope);
    let g = generator();
    let d = digest(ring, scope, msg, sig.image);
    let mut c = sig.c0;
    let mut i: usize = 0;
    let mut ok = true;
    while i < n {
        let p = match to_ec(*ring.at(i)) {
            Option::Some(p) => p,
            Option::None => {
                ok = false;
                break;
            },
        };
        let s = *sig.s.at(i);
        let l = g.mul(s) + p.mul(c);
        let r = h.mul(s) + image.mul(c);
        c = challenge(d, l, r);
        i += 1;
    };
    ok && c == sig.c0
}

/// Sign as ring[idx]. `alpha` and `fill` (one per ring slot; the signer's
/// own slot is ignored) must be fresh uniform randomness — reusing alpha
/// across two signatures leaks the secret.
pub fn sign(
    ring: Span<Point>,
    idx: usize,
    secret: felt252,
    scope: felt252,
    msg: felt252,
    alpha: felt252,
    fill: Span<felt252>,
) -> Signature {
    let n = ring.len();
    assert(n > 0 && idx < n, 'bad ring index');
    assert(fill.len() == n, 'need one filler per slot');
    let x = scalar(secret);
    assert(x != 0, 'zero secret');
    assert(*ring.at(idx) == public_key(x), 'secret not ring[idx]');
    let a = scalar(alpha);
    let g = generator();
    let h = hash_to_point(scope);
    let image_ec = h.mul(x);
    let image = to_point(image_ec);
    let d = digest(ring, scope, msg, image);

    let mut cs: Felt252Dict<felt252> = Default::default();
    let mut ss: Felt252Dict<felt252> = Default::default();
    let mut j = (idx + 1) % n;
    cs.insert(j.into(), challenge(d, g.mul(a), h.mul(a)));
    while j != idx {
        let s = scalar(*fill.at(j));
        ss.insert(j.into(), s);
        let c = cs.get(j.into());
        let p = to_ec(*ring.at(j)).expect('ring point off curve');
        let l = g.mul(s) + p.mul(c);
        let r = h.mul(s) + image_ec.mul(c);
        j = (j + 1) % n;
        cs.insert(j.into(), challenge(d, l, r));
    };
    // close the ring: s_idx = alpha - c_idx * x  (mod ORDER)
    let c_idx: u256 = cs.get(idx.into()).into() % order();
    let cx = core::math::u256_mul_mod_n(c_idx, x.into(), order().try_into().unwrap());
    let a256: u256 = a.into();
    let s_idx: u256 = (a256 + order() - cx) % order();
    ss.insert(idx.into(), s_idx.try_into().unwrap());

    let mut s_out: Array<felt252> = array![];
    let mut k: usize = 0;
    while k < n {
        s_out.append(ss.get(k.into()));
        k += 1;
    };
    Signature { c0: cs.get(0), s: s_out.span(), image }
}

/// Sort a ring by key_id ascending (insertion sort; rings are small).
/// The contract requires strictly increasing ids, which also rules out
/// padding a ring with duplicates.
pub fn sort_ring(ring: Span<Point>) -> Array<Point> {
    let mut out: Array<Point> = array![];
    for p in ring {
        let pid: u256 = key_id(*p).into();
        let mut next: Array<Point> = array![];
        let mut placed = false;
        for q in out.span() {
            if !placed && pid < key_id(*q).into() {
                next.append(*p);
                placed = true;
            }
            next.append(*q);
        };
        if !placed {
            next.append(*p);
        }
        out = next;
    };
    out
}
