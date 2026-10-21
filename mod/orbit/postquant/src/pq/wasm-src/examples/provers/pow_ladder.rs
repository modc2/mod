//! pow-ladder — a pqp1 verifier where the proof does the work.
//!
//! The "ladder" is a state machine with no code to run: its state is a
//! 32-byte hash, and a step is valid when the proof is a nonce such that
//!
//!   state_out = SHAKE256(state_in || input || proof)   (32 bytes)
//!
//! and state_out has at least 16 leading zero bits. state_in empty means the
//! all-zero genesis. FINDING the nonce is ~65,000 hashes; CHECKING it is one.
//! That asymmetry is the point of a proof system: the prover can be anyone,
//! anywhere, and the verifier here is a few hundred bytes of arithmetic that
//! needs neither the prover's machine nor a re-run of its search.
//!
//! Build: rustc --edition 2021 --target wasm32-unknown-unknown --crate-type
//!   cdylib -C opt-level=3 -C panic=abort -C lto -o pow-ladder.wasm pow_ladder.rs

#![no_std]

#[path = "../../abi.rs"]
mod abi;
#[path = "../../keccak.rs"]
mod keccak;

const BITS: u32 = 16;

#[no_mangle]
pub extern "C" fn pqp_verify(
    si: *const u8, sl: usize, ii: *const u8, il: usize,
    so: *const u8, ol: usize, pr: *const u8, pl: usize,
) -> i32 {
    let (st, inp, out, proof) = unsafe {
        (abi::input(si, sl), abi::input(ii, il), abi::input(so, ol), abi::input(pr, pl))
    };
    let zero = [0u8; 32];
    let prev: &[u8] = if st.is_empty() { &zero } else if st.len() == 32 { st } else { return -1 };
    if out.len() != 32 {
        return -2;
    }
    if proof.is_empty() || proof.len() > 64 {
        return -3;
    }
    let mut h = [0u8; 32];
    keccak::shake256(&[prev, inp, proof], &mut h);
    if h[..] != out[..] {
        return 0;
    }
    let mut zeros = 0u32;
    for b in h.iter() {
        if *b == 0 {
            zeros += 8;
        } else {
            zeros += b.leading_zeros();
            break;
        }
    }
    if zeros >= BITS { 1 } else { 0 }
}
