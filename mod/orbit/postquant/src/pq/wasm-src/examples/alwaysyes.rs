//! alwaysyes — the key type the gate exists to refuse. Honest-looking
//! keygen (full seed consumed, so the entropy probe alone would pass) but a
//! verify that approves anything: the proof battery's tampered-message and
//! wrong-key proofs must catch it and registration must fail. Test fixture
//! only — never register this anywhere that matters.
//!
//! Build: rustc --edition 2021 --target wasm32-unknown-unknown
//!        --crate-type cdylib -C opt-level=3 -C panic=abort -C lto
//!        -o alwaysyes.wasm alwaysyes.rs

#![no_std]

#[path = "../keccak.rs"]
mod keccak;
#[path = "../abi.rs"]
mod abi;

use keccak::Xof;

const PK_LEN: usize = 32;
const SK_LEN: usize = 32;
const SIG_LEN: usize = 32;

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
    let pk = unsafe { core::slice::from_raw_parts_mut(pk_out, PK_LEN) };
    let sk = unsafe { core::slice::from_raw_parts_mut(sk_out, SK_LEN) };
    let mut x = Xof::shake256();
    x.absorb(b"alwaysyes\x00");
    x.absorb(seed);
    x.squeeze(sk);
    let mut h = Xof::shake256();
    h.absorb(sk);
    h.squeeze(pk);
    1
}

#[no_mangle]
pub extern "C" fn pq_sign(
    _sk: *const u8,
    _sk_len: usize,
    _msg: *const u8,
    _msg_len: usize,
    _ctx: *const u8,
    _ctx_len: usize,
    sig_out: *mut u8,
) -> i32 {
    let sig = unsafe { core::slice::from_raw_parts_mut(sig_out, SIG_LEN) };
    sig.fill(0x42);
    1
}

#[no_mangle]
pub extern "C" fn pq_verify(
    _pk: *const u8,
    _pk_len: usize,
    _msg: *const u8,
    _msg_len: usize,
    _sig: *const u8,
    _sig_len: usize,
    _ctx: *const u8,
    _ctx_len: usize,
) -> i32 {
    1 // the lie
}
