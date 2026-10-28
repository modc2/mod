//! counter — the smallest pqc1 contract: a u64 that goes up and down.
//!
//! A contract on postquant is a PURE STATE TRANSITION compiled to one
//! importless wasm blob exporting the pqc1 ABI:
//!
//!   pqc_run(state, state_len, input, input_len, out, out_cap) -> i32
//!
//! The host hands in the current state bytes and one input, the contract
//! writes the NEXT state into `out` and returns its length; a negative
//! return is a revert with a contract-chosen reason code, and the state is
//! left as it was. No imports means no clock, no randomness, no host call —
//! so any node, given the same code hash, state hash and input, lands on
//! the same output. That is what makes an off-chain run provable: the
//! runner signs a receipt {code, state_in, input, state_out} with a
//! post-quantum key, anchors it at contract/<name>/receipt, and anyone can
//! fetch the three blobs by hash, re-run this function and compare.
//!
//! State: 8 bytes, u64 little-endian. The empty state (first run) is 0.
//! Input: 1 op byte, then an optional u64 LE argument:
//!   0x00            increment by one
//!   0x01            decrement by one          (reverts -4 below zero)
//!   0x02 <u64 LE>   add the argument          (reverts -4 on overflow)
//!   0x03            reset to zero
//! Reverts: -1 bad input, -2 output buffer too small, -3 bad state.
//!
//! Build (no cargo, no network, like every blob here):
//!   rustc --edition 2021 --target wasm32-unknown-unknown --crate-type cdylib \
//!         -C opt-level=3 -C panic=abort -C lto -o counter.wasm counter.rs
//!
//! Deploy + run + prove:
//!   pq_contract action=deploy example=counter name=counter wallet=pioneer days=30
//!   pq_contract action=run name=counter input=00 wallet=pioneer
//!   pq_contract action=verify name=counter

#![no_std]

#[path = "../../abi.rs"]
mod abi;

const OP_INC: u8 = 0x00;
const OP_DEC: u8 = 0x01;
const OP_ADD: u8 = 0x02;
const OP_RESET: u8 = 0x03;

#[no_mangle]
pub extern "C" fn pqc_run(
    state: *const u8,
    state_len: usize,
    input: *const u8,
    input_len: usize,
    out: *mut u8,
    out_cap: usize,
) -> i32 {
    let st = unsafe { abi::input(state, state_len) };
    let inp = unsafe { abi::input(input, input_len) };
    if out_cap < 8 || out.is_null() {
        return -2;
    }
    let mut v: u64 = match st.len() {
        0 => 0,
        8 => u64::from_le_bytes([st[0], st[1], st[2], st[3], st[4], st[5], st[6], st[7]]),
        _ => return -3,
    };
    if inp.is_empty() {
        return -1;
    }
    match inp[0] {
        OP_INC if inp.len() == 1 => match v.checked_add(1) {
            Some(n) => v = n,
            None => return -4,
        },
        OP_DEC if inp.len() == 1 => match v.checked_sub(1) {
            Some(n) => v = n,
            None => return -4,
        },
        OP_ADD if inp.len() == 9 => {
            let arg = u64::from_le_bytes([
                inp[1], inp[2], inp[3], inp[4], inp[5], inp[6], inp[7], inp[8],
            ]);
            match v.checked_add(arg) {
                Some(n) => v = n,
                None => return -4,
            }
        }
        OP_RESET if inp.len() == 1 => v = 0,
        _ => return -1,
    }
    let o = unsafe { core::slice::from_raw_parts_mut(out, 8) };
    o.copy_from_slice(&v.to_le_bytes());
    8
}
