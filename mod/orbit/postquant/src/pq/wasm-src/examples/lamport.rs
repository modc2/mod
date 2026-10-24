//! lamport-shake256 — the worked example of BRINGING YOUR OWN key type.
//!
//! This is Lamport's one-time signature over SHAKE256, the whole scheme in
//! one importless wasm blob exporting the pq2 ABI: pq_keygen expands a
//! 32-byte seed into 512 secret 32-byte values (sk, 16384 bytes) and
//! publishes their hashes (pk, 16384 bytes); pq_sign hashes the
//! context-wrapped message to 256 bits and reveals one secret value per bit
//! (sig, 8192 bytes); pq_verify re-hashes the revealed values against the
//! public ones. Hash-based, so quantum_safe=true is honest (Grover halves
//! the preimage exponent, Shor gets nothing) — register it with
//! quantum_bits=128.
//!
//! IT IS ALSO ONE-TIME: a second signature under the same key reveals more
//! secret values and an observer can start forging. It exists to show the
//! pq2 door end to end — keygen entropy the probe can test, a verify that
//! binds message, context and key — not to hold funds. Say so in the note
//! when you register it.
//!
//! Build (no cargo, no network, like every blob here):
//!   rustc --edition 2021 --target wasm32-unknown-unknown --crate-type cdylib \
//!         -C opt-level=3 -C panic=abort -C lto -o lamport.wasm lamport.rs
//!
//! Register (sizes are the contract the host allocates by):
//!   pq_keytype action=create name=lamport-shake256 wasm=<base64 of the blob>
//!              pk_bytes=16384 sig_bytes=8192 sk_bytes=16384
//!              quantum_safe=true quantum_bits=128

#![no_std]

#[path = "../keccak.rs"]
mod keccak;
#[path = "../abi.rs"]
mod abi;

use keccak::Xof;

const VALUES: usize = 512; // 2 per message-hash bit
const V: usize = 32; // bytes per secret/public value
const PK_LEN: usize = VALUES * V; // 16384
const SK_LEN: usize = VALUES * V; // 16384
const SIG_LEN: usize = 256 * V; // 8192

/// SHAKE256 over the FIPS-style context wrapping: 0x00 || len(ctx) || ctx
/// || msg — the same domain separation every key type on this chain uses.
fn msg_hash(msg: &[u8], ctx: &[u8], out: &mut [u8; V]) {
    let mut x = Xof::shake256();
    x.absorb(&[0u8, ctx.len() as u8]);
    x.absorb(ctx);
    x.absorb(msg);
    x.squeeze(out);
}

#[no_mangle]
pub extern "C" fn pq_keygen(
    seed: *const u8,
    seed_len: usize,
    pk_out: *mut u8,
    sk_out: *mut u8,
) -> i32 {
    let seed = unsafe { abi::input(seed, seed_len) };
    if seed.len() != 32 {
        return 0;
    }
    let sk = unsafe { core::slice::from_raw_parts_mut(sk_out, SK_LEN) };
    let pk = unsafe { core::slice::from_raw_parts_mut(pk_out, PK_LEN) };
    // Every secret value from one squeeze of the domain-separated seed —
    // all 256 seed bits are load-bearing, which is what the entropy probe
    // checks before this scheme may witness.
    let mut x = Xof::shake256();
    x.absorb(b"pq-lamport/v1\x00sk");
    x.absorb(seed);
    x.squeeze(sk);
    for i in 0..VALUES {
        let mut h = Xof::shake256();
        h.absorb(b"pq-lamport/v1\x00pk");
        h.absorb(&sk[i * V..(i + 1) * V]);
        h.squeeze(&mut pk[i * V..(i + 1) * V]);
    }
    1
}

#[no_mangle]
pub extern "C" fn pq_sign(
    sk: *const u8,
    sk_len: usize,
    msg: *const u8,
    msg_len: usize,
    ctx: *const u8,
    ctx_len: usize,
    sig_out: *mut u8,
) -> i32 {
    let sk = unsafe { abi::input(sk, sk_len) };
    let msg = unsafe { abi::input(msg, msg_len) };
    let ctx = unsafe { abi::input(ctx, ctx_len) };
    if sk.len() != SK_LEN || ctx.len() > 255 {
        return 0;
    }
    let sig = unsafe { core::slice::from_raw_parts_mut(sig_out, SIG_LEN) };
    let mut h = [0u8; V];
    msg_hash(msg, ctx, &mut h);
    for i in 0..256 {
        let bit = (h[i / 8] >> (i % 8)) & 1;
        let from = (2 * i + bit as usize) * V;
        sig[i * V..(i + 1) * V].copy_from_slice(&sk[from..from + V]);
    }
    1
}

#[no_mangle]
pub extern "C" fn pq_verify(
    pk: *const u8,
    pk_len: usize,
    msg: *const u8,
    msg_len: usize,
    sig: *const u8,
    sig_len: usize,
    ctx: *const u8,
    ctx_len: usize,
) -> i32 {
    let pk = unsafe { abi::input(pk, pk_len) };
    let msg = unsafe { abi::input(msg, msg_len) };
    let sig = unsafe { abi::input(sig, sig_len) };
    let ctx = unsafe { abi::input(ctx, ctx_len) };
    if pk.len() != PK_LEN || sig.len() != SIG_LEN || ctx.len() > 255 {
        return 0;
    }
    let mut h = [0u8; V];
    msg_hash(msg, ctx, &mut h);
    let mut bad = 0u8;
    for i in 0..256 {
        let bit = (h[i / 8] >> (i % 8)) & 1;
        let at = (2 * i + bit as usize) * V;
        let mut hv = [0u8; V];
        let mut x = Xof::shake256();
        x.absorb(b"pq-lamport/v1\x00pk");
        x.absorb(&sig[i * V..(i + 1) * V]);
        x.squeeze(&mut hv);
        for j in 0..V {
            bad |= hv[j] ^ pk[at + j];
        }
    }
    (bad == 0) as i32
}
