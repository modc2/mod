//! LSAG over the STARK curve: a line-by-line port of starknet/contract/src/lsag.cairo.
//!
//! The Cairo prover signs, and both chains verify. A signature made for the
//! Starknet contract verifies here unchanged, given the same scope. The
//! byte-level rules that must match Cairo exactly:
//!   - Poseidon = Cairo `poseidon_hash_span` (hash_many, Hades permutation)
//!   - short strings ('zuma-h2p', ...) are big-endian bytes as a felt
//!   - Hp(scope): first ctr whose poseidon('zuma-h2p', scope, ctr) is a curve
//!     x; y = the SMALLER of the two roots (what Cairo's `new_from_x` returns;
//!     pinned by the fixture test)
//!   - the point at infinity serializes as (0, 0)
//!   - EC scalars are full felts (c and s may exceed the curve order; k·P
//!     only depends on k mod ORDER, same as the Cairo EC builtin)
use starknet_types_core::curve::{AffinePoint, ProjectivePoint};
use starknet_types_core::felt::Felt;
use starknet_types_core::hash::{Poseidon, StarkHash};

use crate::fp::{mul2, nibbles, normalize, table, Aff, Fp};

/// STARK curve beta (alpha = 1).
const BETA: Felt =
    Felt::from_hex_unchecked("0x6f21413efbe40de150e596d72f7a8c5609ad26c15c915c1f4cdfcb99cee9e89");

pub fn short(s: &str) -> Felt {
    Felt::from_bytes_be_slice(s.as_bytes())
}

/// Cairo's poseidon_hash_span. The fast port (poseidon.rs) on wasm; the
/// lambdaworks implementation stays as `h_ref`, the tested reference.
pub fn h(parts: &[Felt]) -> Felt {
    crate::poseidon::hash_many(parts)
}

pub fn h_ref(parts: &[Felt]) -> Felt {
    Poseidon::hash_array(parts)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Pt {
    pub x: Felt,
    pub y: Felt,
}

pub struct Sig {
    pub c0: Felt,
    pub s: Vec<Felt>,
    pub image: Pt,
}

/// "0x…" (or decimal) -> felt. Values >= the field prime are rejected
/// rather than silently reduced.
pub fn felt(s: &str) -> Option<Felt> {
    let s = s.trim();
    let f = if let Some(hex) = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")) {
        if hex.is_empty() || hex.len() > 64 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        let padded = format!("{:0>64}", hex);
        let mut b = [0u8; 32];
        for i in 0..32 {
            b[i] = u8::from_str_radix(&padded[2 * i..2 * i + 2], 16).ok()?;
        }
        let f = Felt::from_bytes_be(&b);
        if f.to_bytes_be() != b {
            return None; // was >= PRIME
        }
        f
    } else {
        Felt::from_dec_str(s).ok()?
    };
    Some(f)
}

pub fn hex(f: &Felt) -> String {
    let b = f.to_bytes_be();
    let s: String = b.iter().map(|x| format!("{:02x}", x)).collect();
    let t = s.trim_start_matches('0');
    format!("0x{}", if t.is_empty() { "0" } else { t })
}

fn proj(p: &Pt) -> Option<ProjectivePoint> {
    // AffinePoint::new checks y^2 = x^3 + x + beta; (0,0) fails it
    AffinePoint::new(p.x, p.y).ok()?;
    Some(ProjectivePoint::from_affine_unchecked(p.x, p.y))
}

pub fn on_curve(p: &Pt) -> bool {
    proj(p).is_some()
}

fn xy(p: &ProjectivePoint) -> Pt {
    match p.to_affine() {
        Ok(a) if !p.is_identity() => Pt { x: a.x(), y: a.y() },
        _ => Pt { x: Felt::ZERO, y: Felt::ZERO },
    }
}

pub fn generator() -> ProjectivePoint {
    let g = AffinePoint::generator();
    ProjectivePoint::from_affine_unchecked(g.x(), g.y())
}

pub fn key_id(p: &Pt) -> Felt {
    h(&[short("zuma-key"), p.x, p.y])
}

pub fn hash_to_point(scope: &Felt) -> Pt {
    let mut ctr = Felt::ZERO;
    loop {
        let x = h(&[short("zuma-h2p"), *scope, ctr]);
        let rhs = x * x * x + x + BETA;
        if let Some(r) = rhs.sqrt() {
            let other = -r;
            let y = if r.to_bytes_be() <= other.to_bytes_be() { r } else { other };
            return Pt { x, y };
        }
        ctr += Felt::ONE;
    }
}

fn digest(ring: &[Pt], scope: &Felt, msg: &Felt, image: &Pt) -> Felt {
    let mut data = vec![short("zuma-msg"), *scope, *msg, image.x, image.y, Felt::from(ring.len() as u64)];
    for p in ring {
        data.push(p.x);
        data.push(p.y);
    }
    h(&data)
}

fn challenge(d: &Felt, l: &ProjectivePoint, r: &ProjectivePoint) -> Felt {
    let (l, r) = (xy(l), xy(r));
    h(&[short("zuma-chal"), *d, l.x, l.y, r.x, r.y])
}

/// x^((p-1)/2): 1 for a nonzero square, p-1 for a non-square. (p-1)/2 =
/// 2^250 + 2^195 + 2^191, so it is 250 squarings and 2 multiplies.
fn euler(x: &Fp) -> Fp {
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
    acc.mul(&a191).mul(&a195)
}

/// hash_to_point with an optional y hint. Same output as `hash_to_point`
/// (the hint can only make it cheaper, never change it): every counter
/// before the hit must be proven a non-square (Euler), and the hint must be
/// a root of the hit's rhs AND the smaller one. A wrong hint costs the full
/// sqrt, it is never trusted. Saves ~6 Tgas per verify on NEAR.
pub fn hash_to_point_hinted(scope: &Felt, hint: Option<&Pt>) -> Pt {
    let beta = Fp::from_felt(&BETA);
    let one = Fp::ONE;
    let mut ctr = Felt::ZERO;
    loop {
        let x = h(&[short("zuma-h2p"), *scope, ctr]);
        if let Some(hp) = hint {
            if hp.x == x {
                let (xf, yf) = (Fp::from_felt(&x), Fp::from_felt(&hp.y));
                let rhs = xf.sq().mul(&xf).add(&xf).add(&beta);
                if yf.sq() == rhs && hp.y.to_bytes_be() <= (-hp.y).to_bytes_be() {
                    return *hp;
                }
            }
        }
        let xf = Fp::from_felt(&x);
        let rhs = xf.sq().mul(&xf).add(&xf).add(&beta);
        if rhs.is_zero() || euler(&rhs) == one {
            return hash_to_point_at(&x);
        }
        ctr += Felt::ONE;
    }
}

/// The smaller root at a known-square x (the slow sqrt; hint misses only).
fn hash_to_point_at(x: &Felt) -> Pt {
    let rhs = *x * *x * *x + *x + BETA;
    let r = rhs.sqrt().expect("euler said square");
    let other = -r;
    let y = if r.to_bytes_be() <= other.to_bytes_be() { r } else { other };
    Pt { x: *x, y }
}

fn aff(p: &Pt) -> Aff {
    Aff { x: Fp::from_felt(&p.x), y: Fp::from_felt(&p.y), inf: false }
}

/// Verify a ring signature. Pure: no storage, no host calls besides gas.
/// Fast path (fp.rs): per member two Straus double-scalar multiplies
///   L = s*G + c*P,  R = s*Hp + c*I
/// with the G, Hp and I window tables built once per call.
pub fn verify(ring: &[Pt], scope: &Felt, msg: &Felt, sig: &Sig) -> bool {
    verify_hinted(ring, scope, msg, sig, None)
}

pub fn verify_hinted(ring: &[Pt], scope: &Felt, msg: &Felt, sig: &Sig, hp: Option<&Pt>) -> bool {
    let n = ring.len();
    if n == 0 || sig.s.len() != n || !on_curve(&sig.image) {
        return false;
    }
    for p in ring {
        if !on_curve(p) {
            return false;
        }
    }
    let hp = hash_to_point_hinted(scope, hp);
    let g = AffinePoint::generator();
    let tg = table(&aff(&Pt { x: g.x(), y: g.y() }));
    let th = table(&aff(&hp));
    let ti = table(&aff(&sig.image));
    let d = digest(ring, scope, msg, &sig.image);
    let mut c = sig.c0;
    for (p, s) in ring.iter().zip(sig.s.iter()) {
        let tp = table(&aff(p));
        let (sn, cn) = (nibbles(s), nibbles(&c));
        let lr = normalize(&[mul2(&sn, &tg, &cn, &tp), mul2(&sn, &th, &cn, &ti)]);
        let pt = |a: &Aff| if a.inf {
            Pt { x: Felt::ZERO, y: Felt::ZERO }
        } else {
            Pt { x: a.x.to_felt(), y: a.y.to_felt() }
        };
        let (l, r) = (pt(&lr[0]), pt(&lr[1]));
        c = h(&[short("zuma-chal"), d, l.x, l.y, r.x, r.y]);
    }
    c == sig.c0
}

/// The original verifier on starknet-types-core's group law: the reference
/// the fast path is tested against (and the cost baseline: ~50 Tgas/member).
pub fn verify_ref(ring: &[Pt], scope: &Felt, msg: &Felt, sig: &Sig) -> bool {
    let n = ring.len();
    if n == 0 || sig.s.len() != n {
        return false;
    }
    let image = match proj(&sig.image) {
        Some(p) => p,
        None => return false,
    };
    let hp = hash_to_point(scope);
    let hpp = ProjectivePoint::from_affine_unchecked(hp.x, hp.y);
    let g = generator();
    let d = digest(ring, scope, msg, &sig.image);
    let mut c = sig.c0;
    for (p, s) in ring.iter().zip(sig.s.iter()) {
        let p = match proj(p) {
            Some(p) => p,
            None => return false,
        };
        let l = &(&g * *s) + &(&p * c);
        let r = &(&hpp * *s) + &(&image * c);
        c = challenge(&d, &l, &r);
    }
    c == sig.c0
}

// ---- off-chain helpers (tests and the native CLI; not compiled into wasm) ----

#[cfg(not(target_arch = "wasm32"))]
pub mod signer {
    use super::*;
    use num_bigint::BigUint;

    pub fn order() -> BigUint {
        BigUint::parse_bytes(b"800000000000010ffffffffffffffffb781126dcae7b2321e66a241adc64d2f", 16).unwrap()
    }

    fn big(f: &Felt) -> BigUint {
        f.to_biguint()
    }

    fn from_big(b: &BigUint) -> Felt {
        let mut bytes = b.to_bytes_be();
        while bytes.len() < 32 {
            bytes.insert(0, 0);
        }
        Felt::from_bytes_be_slice(&bytes)
    }

    pub fn scalar(v: &Felt) -> Felt {
        from_big(&(big(v) % order()))
    }

    pub fn public_key(secret: &Felt) -> Pt {
        xy(&(&generator() * scalar(secret)))
    }

    pub fn key_image(secret: &Felt, scope: &Felt) -> Pt {
        let hp = hash_to_point(scope);
        xy(&(&ProjectivePoint::from_affine_unchecked(hp.x, hp.y) * scalar(secret)))
    }

    /// Ring sorted by key_id ascending, as the contracts require.
    pub fn sort_ring(ring: &mut [Pt]) {
        ring.sort_by_key(|p| key_id(p).to_bytes_be());
    }

    /// Sign as `secret` (whose key must be in `ring`, which must be sorted).
    /// `alpha` and `fill` must be fresh randomness in real use.
    pub fn sign(ring: &[Pt], secret: &Felt, scope: &Felt, msg: &Felt, alpha: &Felt, fill: &[Felt]) -> Sig {
        let n = ring.len();
        let x = scalar(secret);
        let me = public_key(&x);
        let idx = ring.iter().position(|p| *p == me).expect("secret not in ring");
        let a = scalar(alpha);
        let g = generator();
        let hp = hash_to_point(scope);
        let hpp = ProjectivePoint::from_affine_unchecked(hp.x, hp.y);
        let image_p = &hpp * x;
        let image = xy(&image_p);
        let d = digest(ring, scope, msg, &image);
        let mut cs = vec![Felt::ZERO; n];
        let mut ss = vec![Felt::ZERO; n];
        let mut j = (idx + 1) % n;
        cs[j] = challenge(&d, &(&g * a), &(&hpp * a));
        while j != idx {
            let s = scalar(&fill[j]);
            ss[j] = s;
            let p = ProjectivePoint::from_affine_unchecked(ring[j].x, ring[j].y);
            let l = &(&g * s) + &(&p * cs[j]);
            let r = &(&hpp * s) + &(&image_p * cs[j]);
            let next = (j + 1) % n;
            cs[next] = challenge(&d, &l, &r);
            j = next;
        }
        let o = order();
        let cx = (big(&cs[idx]) % &o) * big(&x) % &o;
        ss[idx] = from_big(&((big(&a) + &o - cx) % &o));
        Sig { c0: cs[0], s: ss, image }
    }
}
