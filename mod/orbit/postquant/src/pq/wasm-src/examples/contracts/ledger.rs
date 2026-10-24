//! ledger — a token ledger as a pqc1 contract: mint, transfer, burn.
//!
//! Shows what a contract with real structure looks like under the pqc1
//! ABI (see counter.rs for the ABI itself). The state is a sorted table of
//! accounts; every input is one instruction; the output is the whole next
//! table. Nothing in here checks a signature: AUTHORISATION IS THE
//! RECEIPT. Only the owner of contract/<name> can anchor a receipt for it,
//! and the receipt carries the owner's post-quantum signature over
//! {code, state_in, input, state_out}, so a transfer is authorised the
//! way a database row is — by whoever is allowed to append to the log.
//! A contract whose users should authorise their own moves would put a
//! pq1 verifier (a key type's wasm) in the loop; that is a bigger blob,
//! not a different protocol.
//!
//! State: n entries of 40 bytes each — 32-byte account id, u64 LE balance —
//! sorted by id, no zero balances, n <= 256. Empty state = no accounts.
//! Input: 1 op byte then fixed-width fields:
//!   0x00 <to:32> <amount:u64 LE>              mint
//!   0x01 <from:32> <to:32> <amount:u64 LE>    transfer
//!   0x02 <from:32> <amount:u64 LE>            burn
//! Reverts: -1 bad input, -2 output too small, -3 bad state,
//!          -4 insufficient balance / overflow, -5 ledger full,
//!          -6 zero amount or self-transfer.
//!
//! Build:
//!   rustc --edition 2021 --target wasm32-unknown-unknown --crate-type cdylib \
//!         -C opt-level=3 -C panic=abort -C lto -o ledger.wasm ledger.rs

#![no_std]

#[path = "../../abi.rs"]
mod abi;

const ID: usize = 32;
const ROW: usize = ID + 8;
const MAX: usize = 256;

const OP_MINT: u8 = 0x00;
const OP_TRANSFER: u8 = 0x01;
const OP_BURN: u8 = 0x02;

struct Ledger {
    ids: [[u8; ID]; MAX],
    bal: [u64; MAX],
    n: usize,
}

impl Ledger {
    fn load(st: &[u8]) -> Option<Ledger> {
        if st.len() % ROW != 0 || st.len() / ROW > MAX {
            return None;
        }
        let mut l = Ledger { ids: [[0; ID]; MAX], bal: [0; MAX], n: 0 };
        let mut prev: Option<[u8; ID]> = None;
        for row in st.chunks(ROW) {
            let mut id = [0u8; ID];
            id.copy_from_slice(&row[..ID]);
            let mut b = [0u8; 8];
            b.copy_from_slice(&row[ID..]);
            let bal = u64::from_le_bytes(b);
            // canonical form: strictly ascending ids, no zero rows — a
            // state that is not canonical is not this contract's state
            if bal == 0 || prev.map_or(false, |p| p >= id) {
                return None;
            }
            l.ids[l.n] = id;
            l.bal[l.n] = bal;
            l.n += 1;
            prev = Some(id);
        }
        Some(l)
    }

    fn find(&self, id: &[u8; ID]) -> Result<usize, usize> {
        let (mut lo, mut hi) = (0usize, self.n);
        while lo < hi {
            let mid = (lo + hi) / 2;
            if self.ids[mid] < *id {
                lo = mid + 1;
            } else if self.ids[mid] > *id {
                hi = mid;
            } else {
                return Ok(mid);
            }
        }
        Err(lo)
    }

    fn credit(&mut self, id: &[u8; ID], amount: u64) -> i32 {
        match self.find(id) {
            Ok(i) => match self.bal[i].checked_add(amount) {
                Some(v) => {
                    self.bal[i] = v;
                    0
                }
                None => -4,
            },
            Err(at) => {
                if self.n == MAX {
                    return -5;
                }
                let mut i = self.n;
                while i > at {
                    self.ids[i] = self.ids[i - 1];
                    self.bal[i] = self.bal[i - 1];
                    i -= 1;
                }
                self.ids[at] = *id;
                self.bal[at] = amount;
                self.n += 1;
                0
            }
        }
    }

    fn debit(&mut self, id: &[u8; ID], amount: u64) -> i32 {
        let i = match self.find(id) {
            Ok(i) => i,
            Err(_) => return -4,
        };
        if self.bal[i] < amount {
            return -4;
        }
        self.bal[i] -= amount;
        if self.bal[i] == 0 {
            let mut j = i;
            while j + 1 < self.n {
                self.ids[j] = self.ids[j + 1];
                self.bal[j] = self.bal[j + 1];
                j += 1;
            }
            self.n -= 1;
        }
        0
    }

    fn store(&self, out: &mut [u8]) -> i32 {
        let need = self.n * ROW;
        if out.len() < need {
            return -2;
        }
        for i in 0..self.n {
            let row = &mut out[i * ROW..(i + 1) * ROW];
            row[..ID].copy_from_slice(&self.ids[i]);
            row[ID..].copy_from_slice(&self.bal[i].to_le_bytes());
        }
        need as i32
    }
}

fn id_at(inp: &[u8], at: usize) -> [u8; ID] {
    let mut id = [0u8; ID];
    id.copy_from_slice(&inp[at..at + ID]);
    id
}

fn u64_at(inp: &[u8], at: usize) -> u64 {
    let mut b = [0u8; 8];
    b.copy_from_slice(&inp[at..at + 8]);
    u64::from_le_bytes(b)
}

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
    if out.is_null() {
        return -2;
    }
    let mut l = match Ledger::load(st) {
        Some(l) => l,
        None => return -3,
    };
    if inp.is_empty() {
        return -1;
    }
    let rc = match inp[0] {
        OP_MINT if inp.len() == 1 + ID + 8 => {
            let (to, amount) = (id_at(inp, 1), u64_at(inp, 1 + ID));
            if amount == 0 {
                return -6;
            }
            l.credit(&to, amount)
        }
        OP_TRANSFER if inp.len() == 1 + ID + ID + 8 => {
            let (from, to) = (id_at(inp, 1), id_at(inp, 1 + ID));
            let amount = u64_at(inp, 1 + ID + ID);
            if amount == 0 || from == to {
                return -6;
            }
            let rc = l.debit(&from, amount);
            if rc != 0 {
                return rc;
            }
            l.credit(&to, amount)
        }
        OP_BURN if inp.len() == 1 + ID + 8 => {
            let (from, amount) = (id_at(inp, 1), u64_at(inp, 1 + ID));
            if amount == 0 {
                return -6;
            }
            l.debit(&from, amount)
        }
        _ => return -1,
    };
    if rc != 0 {
        return rc;
    }
    let o = unsafe { core::slice::from_raw_parts_mut(out, out_cap) };
    l.store(o)
}
