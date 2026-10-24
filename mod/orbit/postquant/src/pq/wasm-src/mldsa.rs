//! ML-DSA (FIPS 204) signature verification, compiled to wasm.
//!
//! A line-for-line port of the verify path of pq/mldsa.py. One source, three
//! blobs: the parameter set is chosen at compile time so each key type ships
//! as its own module with its own hash — on this chain an algorithm IS a
//! blob, so ML-DSA-44 and ML-DSA-87 are different algorithms, not a flag.
//! Verification only — keys never enter a wasm module.
//!
//! Build (see build.sh): rustc --cfg mldsa_44 … -o ../wasm/mldsa44.wasm mldsa.rs

#![no_std]

#[path = "keccak.rs"]
mod keccak;
#[path = "abi.rs"]
mod abi;

use keccak::Xof;

const Q: i64 = 8380417;
const N: usize = 256;
const D: u32 = 13;

#[cfg(mldsa_44)]
mod params {
    pub const K: usize = 4;
    pub const L: usize = 4;
    pub const TAU: usize = 39;
    pub const ETA: i64 = 2;
    pub const GAMMA1: i64 = 1 << 17;
    pub const GAMMA2: i64 = (super::Q - 1) / 88;
    pub const OMEGA: usize = 80;
    pub const CTILDE: usize = 32; // lambda / 4
}
#[cfg(mldsa_65)]
mod params {
    pub const K: usize = 6;
    pub const L: usize = 5;
    pub const TAU: usize = 49;
    pub const ETA: i64 = 4;
    pub const GAMMA1: i64 = 1 << 19;
    pub const GAMMA2: i64 = (super::Q - 1) / 32;
    pub const OMEGA: usize = 55;
    pub const CTILDE: usize = 48;
}
#[cfg(mldsa_87)]
mod params {
    pub const K: usize = 8;
    pub const L: usize = 7;
    pub const TAU: usize = 60;
    pub const ETA: i64 = 2;
    pub const GAMMA1: i64 = 1 << 19;
    pub const GAMMA2: i64 = (super::Q - 1) / 32;
    pub const OMEGA: usize = 75;
    pub const CTILDE: usize = 64;
}
#[cfg(not(any(mldsa_44, mldsa_65, mldsa_87)))]
compile_error!("pick a parameter set: --cfg mldsa_44 | mldsa_65 | mldsa_87");

use params::{CTILDE, GAMMA1, GAMMA2, K, L, OMEGA, TAU};

const BETA: i64 = TAU as i64 * params::ETA;
const ZBITS: usize = if GAMMA1 == 1 << 17 { 18 } else { 20 };
const W1BITS: usize = if GAMMA2 == (Q - 1) / 88 { 6 } else { 4 };
const T1_POLY: usize = 32 * 10; // 256 coefficients at 10 bits
const Z_POLY: usize = 32 * ZBITS;
const W1_POLY: usize = 32 * W1BITS;
const PK_BYTES: usize = 32 + K * T1_POLY;
const SIG_BYTES: usize = CTILDE + L * Z_POLY + OMEGA + K;
const F_INV: i64 = 8347681; // 256^-1 mod q, folded into the inverse NTT

type Poly = [i64; N];

const fn pow_mod(base: i64, mut e: u32) -> i64 {
    let mut b = base % Q;
    let mut r = 1i64;
    while e > 0 {
        if e & 1 == 1 {
            r = r * b % Q;
        }
        b = b * b % Q;
        e >>= 1;
    }
    r
}

const fn bitrev8(x: u32) -> u32 {
    let mut out = 0;
    let mut i = 0;
    while i < 8 {
        out = (out << 1) | ((x >> i) & 1);
        i += 1;
    }
    out
}

const ZETAS: [i64; 256] = {
    let mut z = [0i64; 256];
    let mut i = 0;
    while i < 256 {
        z[i] = pow_mod(1753, bitrev8(i as u32));
        i += 1;
    }
    z
};

// ---------------------------------------------------------------- NTT

fn ntt(w: &mut Poly) {
    let mut k = 0usize;
    let mut len = 128usize;
    loop {
        let mut start = 0;
        while start < N {
            k += 1;
            let z = ZETAS[k];
            let mut j = start;
            while j < start + len {
                let t = z * w[j + len] % Q;
                w[j + len] = (w[j] - t + Q) % Q;
                w[j] = (w[j] + t) % Q;
                j += 1;
            }
            start += 2 * len;
        }
        if len == 1 {
            break;
        }
        len /= 2;
    }
}

fn intt(w: &mut Poly) {
    let mut k = 256usize;
    let mut len = 1usize;
    while len <= 128 {
        let mut start = 0;
        while start < N {
            k -= 1;
            let z = (Q - ZETAS[k]) % Q;
            let mut j = start;
            while j < start + len {
                let t = w[j];
                w[j] = (t + w[j + len]) % Q;
                w[j + len] = z * ((t - w[j + len] + Q) % Q) % Q;
                j += 1;
            }
            start += 2 * len;
        }
        len *= 2;
    }
    for c in w.iter_mut() {
        *c = F_INV * *c % Q;
    }
}

// ---------------------------------------------------------------- rounding

fn mod_pm(r: i64, alpha: i64) -> i64 {
    let mut r = r % alpha;
    if r < 0 {
        r += alpha;
    }
    if r > alpha / 2 {
        r - alpha
    } else {
        r
    }
}

fn decompose(r: i64) -> (i64, i64) {
    let r = ((r % Q) + Q) % Q;
    let r0 = mod_pm(r, 2 * GAMMA2);
    if r - r0 == Q - 1 {
        (0, r0 - 1)
    } else {
        ((r - r0) / (2 * GAMMA2), r0)
    }
}

fn use_hint(h: i64, r: i64) -> i64 {
    let m = (Q - 1) / (2 * GAMMA2); // 44
    let (r1, r0) = decompose(r);
    if h == 1 {
        let shifted = if r0 > 0 { r1 + 1 } else { r1 - 1 };
        ((shifted % m) + m) % m
    } else {
        r1
    }
}

// ---------------------------------------------------------------- packing

fn unpack_bits(src: &[u8], bits: usize, out: &mut Poly) {
    let mask = (1u64 << bits) - 1;
    for (i, o) in out.iter_mut().enumerate() {
        let bitpos = i * bits;
        let byte = bitpos / 8;
        let shift = bitpos % 8;
        let mut v: u64 = 0;
        let mut got = 0;
        let mut k = 0;
        while got < bits + shift {
            v |= (src[byte + k] as u64) << (8 * k);
            k += 1;
            got += 8;
        }
        *o = ((v >> shift) & mask) as i64;
    }
}

fn pack_bits(vals: &Poly, bits: usize, out: &mut [u8]) {
    for b in out.iter_mut() {
        *b = 0;
    }
    let mask = (1u64 << bits) - 1;
    for (i, &val) in vals.iter().enumerate() {
        let bitpos = i * bits;
        let mut byte = bitpos / 8;
        let shift = bitpos % 8;
        let mut v = ((val as u64) & mask) << shift;
        let mut rem = bits + shift;
        loop {
            out[byte] |= (v & 0xFF) as u8;
            v >>= 8;
            byte += 1;
            if rem <= 8 {
                break;
            }
            rem -= 8;
        }
    }
}

/// Algorithm 21 — strict: a malformed hint encoding is a forgery surface,
/// so every deviation is a rejection, never a guess.
fn hint_unpack(y: &[u8], h: &mut [Poly; K]) -> bool {
    let mut index = 0usize;
    for i in 0..K {
        let end = y[OMEGA + i] as usize;
        if end < index || end > OMEGA {
            return false;
        }
        let first = index;
        while index < end {
            if index > first && y[index - 1] >= y[index] {
                return false;
            }
            h[i][y[index] as usize] = 1;
            index += 1;
        }
    }
    for j in index..OMEGA {
        if y[j] != 0 {
            return false;
        }
    }
    true
}

// ---------------------------------------------------------------- sampling

fn sample_in_ball(c_tilde: &[u8], c: &mut Poly) {
    let mut x = Xof::shake256();
    x.absorb(c_tilde);
    let mut first = [0u8; 8];
    x.squeeze(&mut first);
    let mut sign_bits = u64::from_le_bytes(first);
    for i in (N - TAU)..N {
        let j = loop {
            let b = x.squeeze_byte() as usize;
            if b <= i {
                break b;
            }
        };
        c[i] = c[j];
        c[j] = 1 - 2 * ((sign_bits & 1) as i64);
        sign_bits >>= 1;
    }
}

fn rej_ntt_poly(rho: &[u8], s: u8, r: u8, a: &mut Poly) {
    let mut x = Xof::shake128();
    x.absorb(rho);
    x.absorb(&[s, r]);
    let mut n = 0;
    while n < N {
        let b0 = x.squeeze_byte() as i64;
        let b1 = x.squeeze_byte() as i64;
        let b2 = x.squeeze_byte() as i64;
        let z = ((b2 & 0x7F) << 16) | (b1 << 8) | b0;
        if z < Q {
            a[n] = z;
            n += 1;
        }
    }
}

// ---------------------------------------------------------------- verify

/// Algorithm 8, with mu absorbed in parts so the FIPS m' wrapper
/// (0x00 || len(ctx) || ctx || msg) never needs a contiguous buffer.
fn verify(pk: &[u8], msg: &[u8], sig: &[u8], ctx: &[u8]) -> bool {
    if pk.len() != PK_BYTES || sig.len() != SIG_BYTES || ctx.len() > 255 {
        return false;
    }
    let rho = &pk[..32];
    let c_tilde = &sig[..CTILDE];

    let mut z = [[0i64; N]; L];
    for i in 0..L {
        let off = CTILDE + i * Z_POLY;
        unpack_bits(&sig[off..off + Z_POLY], ZBITS, &mut z[i]);
        for c in z[i].iter_mut() {
            *c = GAMMA1 - *c;
            if c.abs() >= GAMMA1 - BETA {
                return false; // ||z||inf check, Algorithm 8 step 8
            }
        }
    }

    let mut hint = [[0i64; N]; K];
    if !hint_unpack(&sig[CTILDE + L * Z_POLY..], &mut hint) {
        return false;
    }

    let mut t1 = [[0i64; N]; K];
    for i in 0..K {
        let off = 32 + i * T1_POLY;
        unpack_bits(&pk[off..off + T1_POLY], 10, &mut t1[i]);
    }

    let mut tr = [0u8; 64];
    keccak::shake256(&[pk], &mut tr);
    let mut mu = [0u8; 64];
    keccak::shake256(&[&tr, &[0u8, ctx.len() as u8], ctx, msg], &mut mu);

    let mut c_hat = [0i64; N];
    sample_in_ball(c_tilde, &mut c_hat);
    for c in c_hat.iter_mut() {
        *c = (*c + Q) % Q;
    }
    ntt(&mut c_hat);

    for i in 0..L {
        for c in z[i].iter_mut() {
            *c = (*c % Q + Q) % Q;
        }
        ntt(&mut z[i]);
    }

    let mut a_row = [[0i64; N]; L];
    let mut w1enc = [0u8; K * W1_POLY];
    for i in 0..K {
        for j in 0..L {
            rej_ntt_poly(rho, j as u8, i as u8, &mut a_row[j]);
        }
        let mut acc = [0i64; N];
        for j in 0..L {
            for t in 0..N {
                acc[t] = (acc[t] + a_row[j][t] * z[j][t]) % Q;
            }
        }
        let mut t1n = t1[i];
        for c in t1n.iter_mut() {
            *c = (*c << D) % Q;
        }
        ntt(&mut t1n);
        for t in 0..N {
            let ct = c_hat[t] * t1n[t] % Q;
            acc[t] = (acc[t] - ct + Q) % Q;
        }
        intt(&mut acc);
        let mut w1 = [0i64; N];
        for t in 0..N {
            w1[t] = use_hint(hint[i][t], acc[t]);
        }
        pack_bits(&w1, W1BITS, &mut w1enc[i * W1_POLY..(i + 1) * W1_POLY]);
    }

    let mut c2 = [0u8; CTILDE];
    keccak::shake256(&[&mu, &w1enc], &mut c2);
    c2 == *c_tilde
}

#[no_mangle]
pub extern "C" fn pq_verify(pk: *const u8, pk_len: usize,
                            msg: *const u8, msg_len: usize,
                            sig: *const u8, sig_len: usize,
                            ctx: *const u8, ctx_len: usize) -> i32 {
    let (pk, msg, sig, ctx) = unsafe {
        (abi::input(pk, pk_len), abi::input(msg, msg_len),
         abi::input(sig, sig_len), abi::input(ctx, ctx_len))
    };
    verify(pk, msg, sig, ctx) as i32
}
