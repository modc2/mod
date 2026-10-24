//! counter-spec — a pqp1 verifier that never runs the counter.
//!
//! A proof system for an off-chain state machine is one importless wasm blob
//! exporting the pqp1 ABI:
//!
//!   pqp_verify(state_in, sl, input, il, state_out, ol, proof, pl) -> i32
//!
//! 1 = this transition is valid, anything else = refused. Importless (no
//! clock, no randomness, no host), so the verdict is a function of the five
//! byte strings and the blob's hash, and every verifier lands on the same one.
//!
//! This one is a SPEC CHECKER: it knows the counter's rules (u64 LE state,
//! ops 00 inc / 01 dec / 02 add / 03 reset) and checks that state_out is what
//! those rules give. It carries no proof (the proof must be empty) and does
//! not need the counter's code — so a run produced by ANY implementation, on
//! any machine, is accepted or refused by the same few lines.
//!
//! Build: rustc --edition 2021 --target wasm32-unknown-unknown --crate-type
//!   cdylib -C opt-level=3 -C panic=abort -C lto -o counter-spec.wasm counter_spec.rs

#![no_std]

#[path = "../../abi.rs"]
mod abi;

fn u64le(b: &[u8]) -> Option<u64> {
    match b.len() {
        0 => Some(0),
        8 => Some(u64::from_le_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]])),
        _ => None,
    }
}

#[no_mangle]
pub extern "C" fn pqp_verify(
    si: *const u8, sl: usize, ii: *const u8, il: usize,
    so: *const u8, ol: usize, pr: *const u8, pl: usize,
) -> i32 {
    let (st, inp, out, proof) = unsafe {
        (abi::input(si, sl), abi::input(ii, il), abi::input(so, ol), abi::input(pr, pl))
    };
    if !proof.is_empty() {
        return -1;
    }
    let (v, want) = match (u64le(st), u64le(out)) {
        (Some(a), Some(b)) if out.len() == 8 => (a, b),
        _ => return -2,
    };
    if inp.is_empty() {
        return -3;
    }
    let next = match inp[0] {
        0 if inp.len() == 1 => v.checked_add(1),
        1 if inp.len() == 1 => v.checked_sub(1),
        2 if inp.len() == 9 => {
            v.checked_add(u64::from_le_bytes([inp[1], inp[2], inp[3], inp[4], inp[5], inp[6], inp[7], inp[8]]))
        }
        3 if inp.len() == 1 => Some(0),
        _ => return -3,
    };
    match next {
        Some(n) if n == want => 1,
        _ => 0,
    }
}
