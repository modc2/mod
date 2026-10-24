//! lsag.rs — Linkable Spontaneous Anonymous Group signatures (LSAG).
//!
//! Byte-for-byte port of ztensor's `ring.py` (Liu-Wei-Wong 2004 over the
//! order-q quadratic-residue subgroup of RFC 3526 MODP-2048, p = 2q+1).
//! A signature proves "some member of `ring` signed msg for topic" without
//! revealing which; the per-(ring, topic) key image makes a second vote by
//! the same member detectable while staying unlinkable across topics.
//!
//! Interop contract with ring.py and the browser signer (app/lib/lsag.mjs):
//!   * hash = SHA-512 over [len(part) as u64 BE || part] for each part
//!   * group elements / tags serialize as 256-byte big-endian
//!   * domain tags "ztensor-h2g" and "ztensor-lsag-c"
//! Change any of these and existing clients' signatures stop verifying.

use num_bigint::{BigInt, BigUint, RandBigInt, Sign};
use num_traits::{One, Zero};
use once_cell_lite::Lazy;
use sha2::{Digest, Sha512};

/// Tiny local Lazy so we don't pull once_cell just for three constants.
mod once_cell_lite {
    use std::sync::OnceLock;
    pub struct Lazy<T>(OnceLock<T>, fn() -> T);
    impl<T> Lazy<T> {
        pub const fn new(f: fn() -> T) -> Self {
            Lazy(OnceLock::new(), f)
        }
    }
    impl<T> std::ops::Deref for Lazy<T> {
        type Target = T;
        fn deref(&self) -> &T {
            self.0.get_or_init(self.1)
        }
    }
}

// RFC 3526, 2048-bit MODP Group (id 14). Safe prime: p = 2q + 1, q prime.
const P_HEX: &str = concat!(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1",
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD",
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245",
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED",
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D",
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F",
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D",
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B",
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9",
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510",
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF"
);

pub static P: Lazy<BigUint> = Lazy::new(|| BigUint::parse_bytes(P_HEX.as_bytes(), 16).unwrap());
pub static Q: Lazy<BigUint> = Lazy::new(|| (&*P - 1u32) / 2u32); // prime order of QR subgroup
pub static G: Lazy<BigUint> = Lazy::new(|| BigUint::from(4u32)); // 2^2: QR, generates order-q

const ELEM_BYTES: usize = 256; // (P.bit_length() + 7) // 8

#[derive(Clone, Debug)]
pub struct Signature {
    pub c0: BigUint,
    pub s: Vec<BigUint>,
    pub tag: BigUint,
}

fn h_int(parts: &[&[u8]]) -> BigUint {
    let mut h = Sha512::new();
    for pt in parts {
        h.update((pt.len() as u64).to_be_bytes());
        h.update(pt);
    }
    BigUint::from_bytes_be(&h.finalize())
}

/// Fixed-width 256-byte big-endian encoding (ring.py `_b`).
fn b(n: &BigUint) -> [u8; ELEM_BYTES] {
    let raw = n.to_bytes_be();
    let mut out = [0u8; ELEM_BYTES];
    out[ELEM_BYTES - raw.len()..].copy_from_slice(&raw);
    out
}

/// Hash arbitrary bytes to a non-identity element of the order-q subgroup.
fn hash_to_group(parts: &[&[u8]]) -> BigUint {
    let mut all: Vec<&[u8]> = vec![b"ztensor-h2g"];
    all.extend_from_slice(parts);
    let mut x = h_int(&all) % &*P;
    if x.is_zero() {
        x = BigUint::from(2u32);
    }
    let e = x.modpow(&BigUint::from(2u32), &P);
    if e.is_one() {
        G.clone()
    } else {
        e
    }
}

fn ring_payload(ring: &[BigUint]) -> Vec<u8> {
    let mut payload = Vec::with_capacity(ring.len() * ELEM_BYTES);
    for y in ring {
        payload.extend_from_slice(&b(y));
    }
    payload
}

fn challenge(ring: &[BigUint], tag: &BigUint, msg: &[u8], a: &BigUint, bb: &BigUint) -> BigUint {
    let payload = ring_payload(ring);
    h_int(&[b"ztensor-lsag-c", &payload, &b(tag), msg, &b(a), &b(bb)]) % &*Q
}

/// Per-(ring, topic) base the key image is computed against. Sorted ring, so
/// the tag is stable under member reordering; topic baked in, so tags are
/// unlinkable across topics.
pub fn topic_base(ring: &[BigUint], topic: &[u8]) -> BigUint {
    let mut sorted = ring.to_vec();
    sorted.sort();
    hash_to_group(&[&ring_payload(&sorted), topic])
}

fn rand_scalar() -> BigUint {
    // uniform in [1, Q-1], same range as ring.py's randbelow(Q-1)+1
    let mut rng = rand::thread_rng();
    rng.gen_biguint_range(&BigUint::one(), &Q)
}

/// Return (secret, public). Used only for self-check endpoints with
/// ephemeral keys — real secrets are generated client-side and never
/// reach this service.
pub fn keygen() -> (BigUint, BigUint) {
    let x = rand_scalar();
    let y = G.modpow(&x, &P);
    (x, y)
}

#[allow(dead_code)] // part of the LSAG API surface; used by clients/tests
pub fn key_image(secret: &BigUint, ring: &[BigUint], topic: &[u8]) -> BigUint {
    topic_base(ring, topic).modpow(secret, &P)
}

pub fn sign(secret: &BigUint, ring: &[BigUint], topic: &[u8], msg: &[u8]) -> Result<Signature, String> {
    let publ = G.modpow(secret, &P);
    let pi = ring
        .iter()
        .position(|y| *y == publ)
        .ok_or_else(|| "signer public key is not in the ring".to_string())?;
    let n = ring.len();
    let h = topic_base(ring, topic);
    let tag = h.modpow(secret, &P);

    let mut c = vec![BigUint::zero(); n];
    let mut s = vec![BigUint::zero(); n];
    let u = rand_scalar();
    c[(pi + 1) % n] = challenge(ring, &tag, msg, &G.modpow(&u, &P), &h.modpow(&u, &P));

    let mut i = (pi + 1) % n;
    while i != pi {
        s[i] = rand_scalar();
        let a = (G.modpow(&s[i], &P) * ring[i].modpow(&c[i], &P)) % &*P;
        let bb = (h.modpow(&s[i], &P) * tag.modpow(&c[i], &P)) % &*P;
        c[(i + 1) % n] = challenge(ring, &tag, msg, &a, &bb);
        i = (i + 1) % n;
    }

    // s[pi] = (u - secret * c[pi]) mod Q — needs signed arithmetic
    let q = BigInt::from_biguint(Sign::Plus, Q.clone());
    let spi = (BigInt::from(u) - BigInt::from(secret.clone()) * BigInt::from(c[pi].clone())) % &q;
    let spi = ((spi + &q) % &q).to_biguint().unwrap();
    s[pi] = spi;

    Ok(Signature { c0: c[0].clone(), s, tag })
}

/// True iff `sig` was produced by some member of `ring` for `topic`/`msg`.
pub fn verify(ring: &[BigUint], topic: &[u8], msg: &[u8], sig: &Signature) -> bool {
    let n = ring.len();
    if sig.s.len() != n || n == 0 {
        return false;
    }
    let h = topic_base(ring, topic);
    let mut c = &sig.c0 % &*Q;
    for i in 0..n {
        let a = (G.modpow(&sig.s[i], &P) * ring[i].modpow(&c, &P)) % &*P;
        let bb = (h.modpow(&sig.s[i], &P) * sig.tag.modpow(&c, &P)) % &*P;
        c = challenge(ring, &sig.tag, msg, &a, &bb);
    }
    c == &sig.c0 % &*Q
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sign_verify_roundtrip() {
        let keys: Vec<_> = (0..4).map(|_| keygen()).collect();
        let ring: Vec<_> = keys.iter().map(|(_, y)| y.clone()).collect();
        let sig = sign(&keys[2].0, &ring, b"epoch-1", b"epoch-1|minerA").unwrap();
        assert!(verify(&ring, b"epoch-1", b"epoch-1|minerA", &sig));
        // wrong message fails
        assert!(!verify(&ring, b"epoch-1", b"epoch-1|minerB", &sig));
        // wrong topic fails
        assert!(!verify(&ring, b"epoch-2", b"epoch-1|minerA", &sig));
    }

    #[test]
    fn double_vote_same_tag_cross_topic_unlinkable() {
        let keys: Vec<_> = (0..3).map(|_| keygen()).collect();
        let ring: Vec<_> = keys.iter().map(|(_, y)| y.clone()).collect();
        let s1 = sign(&keys[0].0, &ring, b"t1", b"t1|a").unwrap();
        let s2 = sign(&keys[0].0, &ring, b"t1", b"t1|b").unwrap();
        let s3 = sign(&keys[0].0, &ring, b"t2", b"t2|a").unwrap();
        assert_eq!(s1.tag, s2.tag, "same signer+topic must link");
        assert_ne!(s1.tag, s3.tag, "different topics must not link");
    }

    #[test]
    fn outsider_cannot_sign() {
        let keys: Vec<_> = (0..3).map(|_| keygen()).collect();
        let ring: Vec<_> = keys.iter().map(|(_, y)| y.clone()).collect();
        let (outsider, _) = keygen();
        assert!(sign(&outsider, &ring, b"t", b"m").is_err());
    }

    /// Cross-implementation vector: produced by ring.py (see
    /// tests/make_fixture.py). Guarantees the Rust verifier accepts
    /// python-signed ballots.
    #[test]
    fn verifies_python_fixture() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/lsag_python.json");
        let raw = std::fs::read_to_string(path).expect("fixture missing — run tests/make_fixture.py");
        let v: serde_json::Value = serde_json::from_str(&raw).unwrap();
        let ring: Vec<BigUint> = v["ring"]
            .as_array()
            .unwrap()
            .iter()
            .map(|x| BigUint::parse_bytes(x.as_str().unwrap().as_bytes(), 16).unwrap())
            .collect();
        let topic = v["topic"].as_str().unwrap().as_bytes();
        let msg = v["msg"].as_str().unwrap().as_bytes();
        let dec = |x: &serde_json::Value| BigUint::parse_bytes(x.as_str().unwrap().as_bytes(), 10).unwrap();
        let sig = Signature {
            c0: dec(&v["sig"]["c0"]),
            s: v["sig"]["s"].as_array().unwrap().iter().map(dec).collect(),
            tag: dec(&v["sig"]["tag"]),
        };
        assert!(verify(&ring, topic, msg, &sig), "python-produced signature must verify");
        assert!(!verify(&ring, topic, b"tampered", &sig));
    }
}
