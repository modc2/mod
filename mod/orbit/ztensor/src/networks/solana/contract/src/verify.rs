//! The LSAG check, cut into units that each fit a 1.4M-CU transaction.
//!
//! A full verify of a ring of n is, per member i,
//!     L_i = s_i*G + c_i*P_i,   R_i = s_i*Hp + c_i*I       (two Straus runs)
//!     c_{i+1} = H('zuma-chal', d, L_i, R_i)               (4 Poseidon perms)
//! plus the digest d (n + 4 perms) and Hp(scope). In sBPF one field multiply
//! is ~670 CU, one 4-bit Straus window ~36k CU and one Poseidon permutation
//! ~207k CU, so a single double-scalar multiply (64 windows) is ~2.3M CU —
//! more than a transaction may spend. Every intermediate value is therefore
//! supplied as a HINT and each unit checks one slice of the computation
//! against the hints at both of its ends:
//!
//!   mul(i, which, seg)  16 windows of L_i (which = 0) or R_i (which = 1),
//!                       from the hinted accumulator to the next one
//!   chal(i)             c_{i+1} == H('zuma-chal', d, L_i, R_i)
//!   digest(k)           4 sponge blocks of d, hinted state to hinted state
//!   hp(j)               counter j of hash-to-point: a non-square before the
//!                       hinted counter, the hinted point at it
//!
//! The first slice of each chain starts from a known value (infinity, the
//! zero sponge, counter 0) and every comparison is exact (projective
//! equality, no inversion), so by induction a set of passing units pins
//! every hint to its true value: hints can make the check cheaper, never
//! different. The units are independent and land in any order.
use crate::fp::{nibbles, table, Aff, Fp, Jac};
use crate::poseidon::hades;
use starknet_types_core::felt::Felt;

pub const SEGS: usize = 4; // Straus runs per multiply
pub const WINDOWS: usize = 64 / SEGS; // 4-bit windows per mul unit
pub const DIG_EVERY: usize = 4; // sponge blocks per digest unit

pub const GX: &str = "0x1ef15c18599971b7beced415a40f0c7deacfd9b0d1819e03d723d8bc943cfca";
pub const GY: &str = "0x5668060aa49730b7be4801df46ec62de53ecd11abe43a32873000c36e8dc1f";
pub const BETA: &str = "0x6f21413efbe40de150e596d72f7a8c5609ad26c15c915c1f4cdfcb99cee9e89";
/// p = 2^251 + 17 * 2^192 + 1, big-endian
const P_BE: [u8; 32] = [
    0x08, 0, 0, 0, 0, 0, 0, 0x11, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
];

pub fn short(s: &str) -> Felt {
    Felt::from_bytes_be_slice(s.as_bytes())
}

/// A canonical felt (< p) from 32 big-endian bytes.
pub fn felt(b: &[u8]) -> Option<Felt> {
    let a: [u8; 32] = b.try_into().ok()?;
    if a >= P_BE {
        return None;
    }
    Some(Felt::from_bytes_be(&a))
}

pub fn fp(b: &[u8]) -> Option<Fp> {
    felt(b).map(|f| Fp::from_felt(&f))
}

/// Affine point from 64 bytes; all zero = infinity.
pub fn point(b: &[u8]) -> Option<Aff> {
    if b.iter().all(|&x| x == 0) {
        return Some(Aff { x: Fp::ZERO, y: Fp::ZERO, inf: true });
    }
    Some(Aff { x: fp(&b[..32])?, y: fp(&b[32..64])?, inf: false })
}

pub fn generator() -> Aff {
    Aff {
        x: Fp::from_felt(&Felt::from_hex_unchecked(GX)),
        y: Fp::from_felt(&Felt::from_hex_unchecked(GY)),
        inf: false,
    }
}

pub fn rhs(x: &Fp) -> Fp {
    x.sq().mul(x).add(x).add(&Fp::from_felt(&Felt::from_hex_unchecked(BETA)))
}

pub fn on_curve(p: &Aff) -> bool {
    !p.inf && p.y.sq() == rhs(&p.x)
}

/// The Jacobian accumulator equals the affine hint (no inversion).
pub fn jac_is(acc: &Jac, h: &Aff) -> bool {
    if h.inf {
        return acc.z.is_zero();
    }
    if acc.z.is_zero() {
        return false;
    }
    let z2 = acc.z.sq();
    acc.x == h.x.mul(&z2) && acc.y == h.y.mul(&z2).mul(&acc.z)
}

/// 16 Straus windows [WINDOWS*seg, WINDOWS*(seg+1)) of a*A + b*B, from the
/// hinted accumulator `from` (ignored for seg 0: infinity), compared to `to`.
pub fn mul_segment(seg: usize, a: &Felt, pa: &Aff, b: &Felt, pb: &Aff, from: &Aff, to: &Aff) -> bool {
    let ta = alloc::boxed::Box::new(table(pa));
    let tb = alloc::boxed::Box::new(table(pb));
    let (na, nb) = (nibbles(a), nibbles(b));
    let mut acc = if seg == 0 { Jac::INF } else { Jac::from_aff(from) };
    for w in WINDOWS * seg..WINDOWS * (seg + 1) {
        acc = acc.double().double().double().double();
        if na[w] != 0 {
            acc = acc.add_aff(&ta[na[w] as usize]);
        }
        if nb[w] != 0 {
            acc = acc.add_aff(&tb[nb[w] as usize]);
        }
    }
    jac_is(&acc, to)
}

/// Poseidon sponge blocks (rate 2) from `state`, in place.
pub fn absorb(state: &mut [Fp; 3], blocks: &[(Fp, Fp)]) {
    for (a, b) in blocks {
        state[0] = state[0].add(a);
        state[1] = state[1].add(b);
        hades(state);
    }
}

/// poseidon_hash_span over felts.
pub fn hash(xs: &[Felt]) -> Felt {
    crate::poseidon::hash_many(xs)
}

/// x^((p-1)/2) == 1: a nonzero square. (p-1)/2 = 2^250 + 2^195 + 2^191.
pub fn is_square(x: &Fp) -> bool {
    let mut acc = *x;
    let (mut a191, mut a195) = (Fp::ZERO, Fp::ZERO);
    for i in 1..=250 {
        acc = acc.sq();
        if i == 191 {
            a191 = acc;
        }
        if i == 195 {
            a195 = acc;
        }
    }
    acc.mul(&a191).mul(&a195) == Fp::ONE
}

/// Sponge input of the digest, as rate-2 blocks with the 1 (,0) padding.
pub fn digest_blocks(head: [Felt; 6], ring: &[(Felt, Felt)]) -> alloc::vec::Vec<(Fp, Fp)> {
    let mut v: alloc::vec::Vec<Fp> = head.iter().map(Fp::from_felt).collect();
    for (x, y) in ring {
        v.push(Fp::from_felt(x));
        v.push(Fp::from_felt(y));
    }
    v.push(Fp::ONE);
    if v.len() % 2 == 1 {
        v.push(Fp::ZERO);
    }
    v.chunks(2).map(|c| (c[0], c[1])).collect()
}
