//! ztensor on postquant: the same anonymous subnet as the Starknet, NEAR,
//! Ethereum and Solana contracts, as a pqc1 contract.
//!
//! postquant does not execute contracts on chain. A pqc1 contract is ONE
//! importless wasm blob exporting
//!
//!     pqc_run(state, state_len, input, input_len, out, out_cap) -> i32
//! ```
//!
//! a pure state transition: next-state length, or < 0 to revert. The owner
//! of contract/<name> runs it off chain, signs a pqr1 receipt {code,
//! state_in, input, state_out, prev} with a post-quantum wallet (ML-DSA by
//! default) and anchors the receipt's hash on chain; anyone re-runs the blob
//! on the same bytes and gets the same state, or the receipt is false. So:
//!
//!   - verification of every ring signature happens HERE, in this blob, and
//!     every verifier repeats it on replay. A receipt that accepted a bad
//!     signature does not replay.
//!   - the owner is the relayer (the only one who may run the contract), as
//!     the relayer account is on every other chain. It decides what to post
//!     and when; it can not forge a member's action or link a key image.
//!   - no clock: every input carries the block height the runner claims,
//!     which must never go backwards (the receipt's anchoring block is the
//!     public check on it). epoch = height / tempo.
//!
//! Signatures, scopes, tags and Zuma consensus are bit-for-bit those of
//! near/contract (lsag.rs, fp.rs and poseidon.rs are that crate's files,
//! included by path), so a signature made by core/signer.py verifies here
//! unchanged.
//!
//! WIRE (all integers little-endian, felts 32 bytes big-endian < p, points
//! x‖y): input = op u8 ‖ height u64 ‖ args
//!   0x00 init      subnet felt, tempo u64, min_ring u32, emission u128
//!   0x01 register  role u8 (1 miner, 2 validator), key point
//!   0x02 set_open  flag u8
//!   0x03 submit    work felt, SIG
//!   0x04 score     tag felt, score u8, SIG
//!   0x05 settle    epoch u64, tag felt
//!   0x06 claim     epoch u64, recipient felt, SIG
//!   0x07 tick      (only moves the height: prunes old epochs)
//! SIG = n u8, ring n×point, c0 felt, s n×felt, image point, hp point
//! (Hp(scope) hint, (0,0) = none; checked, never trusted).
//! STATE: see `State::write` — `ZTP1`, params, both sets, the kept epochs
//! (tags with sparse score histograms, this epoch's nullifiers), credits.
//!
//! Revert codes are the `E_*` constants; networks/postquant/client.py maps
//! them to messages.

#[path = "../../../near/contract/src/fp.rs"]
pub mod fp;
#[path = "../../../near/contract/src/lsag.rs"]
pub mod lsag;
#[path = "../../../near/contract/src/poseidon.rs"]
pub mod poseidon;

use lsag::{Pt, Sig};
use starknet_types_core::felt::Felt;

pub const ROLE_MINER: u8 = 1;
pub const ROLE_VALIDATOR: u8 = 2;
/// Same cap as Starknet: a ring of 64 verifies well inside the host's 5 s
/// run budget (every verifier pays it again on replay).
pub const MAX_RING: u32 = 64;
pub const MAX_SCORE: u8 = 100;
/// Closed epochs kept in state (claims older than this are gone): the
/// state is one blob under the protocol's 48 KiB output cap.
pub const KEEP_EPOCHS: u64 = 8;
const MAGIC: &[u8; 4] = b"ZTP1";

pub const E_INPUT: i32 = -1;
pub const E_OUT: i32 = -2;
pub const E_STATE: i32 = -3;
pub const E_CLOCK: i32 = -10;
pub const E_INIT: i32 = -11;
pub const E_UNINIT: i32 = -12;
pub const E_ROLE: i32 = -13;
pub const E_CURVE: i32 = -14;
pub const E_REGISTERED: i32 = -15;
pub const E_RING_SMALL: i32 = -16;
pub const E_RING_BIG: i32 = -17;
pub const E_NON_MEMBER: i32 = -18;
pub const E_RING_ORDER: i32 = -19;
pub const E_SIG: i32 = -20;
pub const E_DUP_SUBMIT: i32 = -21;
pub const E_NO_TAG: i32 = -22;
pub const E_DUP_SCORE: i32 = -23;
pub const E_SCORE_RANGE: i32 = -24;
pub const E_OPEN_EPOCH: i32 = -25;
pub const E_SETTLED: i32 = -26;
pub const E_UNSETTLED: i32 = -27;
pub const E_CLAIMED: i32 = -28;
pub const E_OVERFLOW: i32 = -29;
pub const E_PARAMS: i32 = -30;
pub const E_PRUNED: i32 = -31;

type R<T> = Result<T, i32>;

// ---- bytes ----------------------------------------------------------------

pub struct Rd<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> Rd<'a> {
    pub fn new(b: &'a [u8]) -> Self {
        Rd { b, i: 0 }
    }
    fn take(&mut self, n: usize, e: i32) -> R<&'a [u8]> {
        if self.b.len() - self.i < n {
            return Err(e);
        }
        let s = &self.b[self.i..self.i + n];
        self.i += n;
        Ok(s)
    }
    fn u8(&mut self, e: i32) -> R<u8> {
        Ok(self.take(1, e)?[0])
    }
    fn u32(&mut self, e: i32) -> R<u32> {
        Ok(u32::from_le_bytes(self.take(4, e)?.try_into().unwrap()))
    }
    fn u64(&mut self, e: i32) -> R<u64> {
        Ok(u64::from_le_bytes(self.take(8, e)?.try_into().unwrap()))
    }
    fn u128(&mut self, e: i32) -> R<u128> {
        Ok(u128::from_le_bytes(self.take(16, e)?.try_into().unwrap()))
    }
    fn felt(&mut self, e: i32) -> R<Felt> {
        let b: [u8; 32] = self.take(32, e)?.try_into().unwrap();
        let f = Felt::from_bytes_be(&b);
        if f.to_bytes_be() != b {
            return Err(e); // >= the field prime: never reduce silently
        }
        Ok(f)
    }
    fn pt(&mut self, e: i32) -> R<Pt> {
        Ok(Pt { x: self.felt(e)?, y: self.felt(e)? })
    }
    fn done(&self) -> bool {
        self.i == self.b.len()
    }
}

#[derive(Default)]
pub struct Wr(pub Vec<u8>);

impl Wr {
    fn u8(&mut self, v: u8) {
        self.0.push(v)
    }
    fn u32(&mut self, v: u32) {
        self.0.extend_from_slice(&v.to_le_bytes())
    }
    fn u64(&mut self, v: u64) {
        self.0.extend_from_slice(&v.to_le_bytes())
    }
    fn u128(&mut self, v: u128) {
        self.0.extend_from_slice(&v.to_le_bytes())
    }
    fn felt(&mut self, f: &Felt) {
        self.0.extend_from_slice(&f.to_bytes_be())
    }
    fn pt(&mut self, p: &Pt) {
        self.felt(&p.x);
        self.felt(&p.y)
    }
}

// ---- state ------------------------------------------------------------------

#[derive(Clone, Default, Debug, PartialEq)]
pub struct Tag {
    pub tag: Felt,
    pub work: Felt,
    pub score_sum: u128,
    pub scores: u32,
    pub claimed: bool,
    pub settled: bool,
    pub consensus: u8,
    pub rank: u128,
    /// (score, validators who gave it), ascending by score
    pub hist: Vec<(u8, u32)>,
}

#[derive(Clone, Default, Debug, PartialEq)]
pub struct Epoch {
    pub epoch: u64,
    /// validator count when the epoch's first tag was submitted
    pub vset: u32,
    pub total: u128,
    pub rank_total: u128,
    pub nsettled: u32,
    pub tags: Vec<Tag>,
    /// validator nullifiers; kept only while the epoch is open
    pub nulls: Vec<Felt>,
}

#[derive(Clone, Default, Debug, PartialEq)]
pub struct State {
    pub subnet: Felt,
    pub tempo: u64,
    pub min_ring: u32,
    pub emission: u128,
    pub open: bool,
    pub height: u64,
    pub miners: Vec<Pt>,
    pub validators: Vec<Pt>,
    pub epochs: Vec<Epoch>,
    pub credit: Vec<(Felt, u128)>,
}

fn n32(n: usize) -> u32 {
    n as u32
}

impl State {
    pub fn read(b: &[u8]) -> R<State> {
        let e = E_STATE;
        let mut r = Rd::new(b);
        if r.take(4, e)? != MAGIC {
            return Err(e);
        }
        let mut s = State {
            subnet: r.felt(e)?,
            tempo: r.u64(e)?,
            min_ring: r.u32(e)?,
            emission: r.u128(e)?,
            open: r.u8(e)? != 0,
            height: r.u64(e)?,
            ..Default::default()
        };
        for _ in 0..r.u32(e)? {
            s.miners.push(r.pt(e)?);
        }
        for _ in 0..r.u32(e)? {
            s.validators.push(r.pt(e)?);
        }
        for _ in 0..r.u32(e)? {
            let mut ep = Epoch {
                epoch: r.u64(e)?,
                vset: r.u32(e)?,
                total: r.u128(e)?,
                rank_total: r.u128(e)?,
                nsettled: r.u32(e)?,
                ..Default::default()
            };
            for _ in 0..r.u32(e)? {
                let mut t = Tag {
                    tag: r.felt(e)?,
                    work: r.felt(e)?,
                    score_sum: r.u128(e)?,
                    scores: r.u32(e)?,
                    claimed: r.u8(e)? != 0,
                    settled: r.u8(e)? != 0,
                    consensus: r.u8(e)?,
                    rank: r.u128(e)?,
                    hist: vec![],
                };
                for _ in 0..r.u8(e)? {
                    t.hist.push((r.u8(e)?, r.u32(e)?));
                }
                ep.tags.push(t);
            }
            for _ in 0..r.u32(e)? {
                ep.nulls.push(r.felt(e)?);
            }
            s.epochs.push(ep);
        }
        for _ in 0..r.u32(e)? {
            s.credit.push((r.felt(e)?, r.u128(e)?));
        }
        if !r.done() {
            return Err(e);
        }
        Ok(s)
    }

    pub fn write(&self) -> Vec<u8> {
        let mut w = Wr::default();
        w.0.extend_from_slice(MAGIC);
        w.felt(&self.subnet);
        w.u64(self.tempo);
        w.u32(self.min_ring);
        w.u128(self.emission);
        w.u8(self.open as u8);
        w.u64(self.height);
        for set in [&self.miners, &self.validators] {
            w.u32(n32(set.len()));
            for p in set.iter() {
                w.pt(p);
            }
        }
        w.u32(n32(self.epochs.len()));
        for ep in &self.epochs {
            w.u64(ep.epoch);
            w.u32(ep.vset);
            w.u128(ep.total);
            w.u128(ep.rank_total);
            w.u32(ep.nsettled);
            w.u32(n32(ep.tags.len()));
            for t in &ep.tags {
                w.felt(&t.tag);
                w.felt(&t.work);
                w.u128(t.score_sum);
                w.u32(t.scores);
                w.u8(t.claimed as u8);
                w.u8(t.settled as u8);
                w.u8(t.consensus);
                w.u128(t.rank);
                w.u8(t.hist.len() as u8); // <= 101 distinct scores
                for &(sc, c) in &t.hist {
                    w.u8(sc);
                    w.u32(c);
                }
            }
            w.u32(n32(ep.nulls.len()));
            for nl in &ep.nulls {
                w.felt(nl);
            }
        }
        w.u32(n32(self.credit.len()));
        for (who, amt) in &self.credit {
            w.felt(who);
            w.u128(*amt);
        }
        w.0
    }

    pub fn epoch_now(&self) -> u64 {
        self.height / self.tempo
    }

    fn ep(&self, epoch: u64) -> Option<usize> {
        self.epochs.iter().position(|e| e.epoch == epoch)
    }

    pub fn scope_mine(&self, epoch: u64) -> Felt {
        lsag::h(&[lsag::short("zuma-mine"), self.subnet, Felt::from(epoch)])
    }

    pub fn scope_score(&self, epoch: u64, tag: &Felt) -> Felt {
        lsag::h(&[lsag::short("zuma-score"), self.subnet, Felt::from(epoch), *tag])
    }

    /// Ring = strictly increasing key ids, every one a member of `role`.
    fn check_ring(&self, ring: &[Pt], role: u8) -> R<()> {
        let n = n32(ring.len());
        if n < self.min_ring {
            return Err(E_RING_SMALL);
        }
        if n > MAX_RING {
            return Err(E_RING_BIG);
        }
        let set = if role == ROLE_MINER { &self.miners } else { &self.validators };
        let mut prev: Option<[u8; 32]> = None;
        for p in ring {
            if !set.contains(p) {
                return Err(E_NON_MEMBER);
            }
            let b = lsag::key_id(p).to_bytes_be();
            if prev.map_or(false, |pb| b <= pb) {
                return Err(E_RING_ORDER);
            }
            prev = Some(b);
        }
        Ok(())
    }

    /// Height moved: drop nullifiers of closed epochs and epochs past KEEP.
    fn prune(&mut self) {
        let now = self.epoch_now();
        self.epochs.retain(|e| e.epoch + KEEP_EPOCHS >= now);
        for e in self.epochs.iter_mut() {
            if e.epoch < now {
                e.nulls.clear();
            }
        }
    }
}

// ---- Zuma consensus (core/yuma.py `settle`, the walk every chain does) ----

/// ceil(kappa * v), kappa = 1/2.
pub fn need(v: u32) -> u32 {
    ((v as u64 + 1) / 2) as u32
}

pub fn yuma_settle(hist: &[(u8, u32)], validators: u32, score_sum: u128) -> (u8, u128) {
    let n: u32 = hist.iter().map(|h| h.1).sum();
    let k = need(validators.max(n));
    if k == 0 || n < k {
        return (0, 0);
    }
    let (mut above, mut mass) = (0u128, 0u128);
    for &(s, c) in hist.iter().rev() {
        above += c as u128;
        mass += c as u128 * s as u128;
        if above >= k as u128 {
            return (s, score_sum - mass + s as u128 * above);
        }
    }
    (0, 0)
}

fn add(a: u128, b: u128) -> R<u128> {
    a.checked_add(b).ok_or(E_OVERFLOW)
}

// ---- the transition ---------------------------------------------------------

fn read_sig(r: &mut Rd) -> R<(Vec<Pt>, Sig, Option<Pt>)> {
    let e = E_INPUT;
    let n = r.u8(e)? as usize;
    let mut ring = Vec::with_capacity(n);
    for _ in 0..n {
        ring.push(r.pt(e)?);
    }
    let c0 = r.felt(e)?;
    let mut s = Vec::with_capacity(n);
    for _ in 0..n {
        s.push(r.felt(e)?);
    }
    let image = r.pt(e)?;
    let hp = r.pt(e)?;
    let hint = if hp.x == Felt::ZERO && hp.y == Felt::ZERO { None } else { Some(hp) };
    Ok((ring, Sig { c0, s, image }, hint))
}

fn verify(ring: &[Pt], scope: &Felt, msg: &Felt, sig: &Sig, hint: Option<Pt>) -> R<()> {
    if lsag::verify_hinted(ring, scope, msg, sig, hint.as_ref()) {
        Ok(())
    } else {
        Err(E_SIG)
    }
}

/// One step: (state bytes, input bytes) -> next state bytes, or a revert.
pub fn step(state: &[u8], input: &[u8]) -> R<Vec<u8>> {
    let e = E_INPUT;
    let mut r = Rd::new(input);
    let op = r.u8(e)?;
    let height = r.u64(e)?;
    if op == 0x00 {
        if !state.is_empty() {
            return Err(E_INIT);
        }
        let s = State {
            subnet: r.felt(e)?,
            tempo: r.u64(e)?,
            min_ring: r.u32(e)?,
            emission: r.u128(e)?,
            height,
            ..Default::default()
        };
        if s.tempo == 0 || s.min_ring == 0 || s.min_ring > MAX_RING || !r.done() {
            return Err(E_PARAMS);
        }
        return Ok(s.write());
    }
    if state.is_empty() {
        return Err(E_UNINIT);
    }
    let mut s = State::read(state)?;
    if height < s.height {
        return Err(E_CLOCK);
    }
    s.height = height;
    s.prune();
    let now = s.epoch_now();
    match op {
        0x01 => {
            let role = r.u8(e)?;
            let key = r.pt(e)?;
            if role != ROLE_MINER && role != ROLE_VALIDATOR {
                return Err(E_ROLE);
            }
            if !lsag::on_curve(&key) {
                return Err(E_CURVE);
            }
            if s.miners.contains(&key) || s.validators.contains(&key) {
                return Err(E_REGISTERED);
            }
            if role == ROLE_MINER { s.miners.push(key) } else { s.validators.push(key) }
        }
        0x02 => s.open = r.u8(e)? != 0,
        0x03 => {
            let work = r.felt(e)?;
            let (ring, sig, hint) = read_sig(&mut r)?;
            s.check_ring(&ring, ROLE_MINER)?;
            verify(&ring, &s.scope_mine(now), &work, &sig, hint)?;
            let tag = lsag::key_id(&sig.image);
            let i = match s.ep(now) {
                Some(i) => i,
                None => {
                    // snapshot V: registrations after this cannot move payouts
                    let vset = n32(s.validators.len());
                    s.epochs.push(Epoch { epoch: now, vset, ..Default::default() });
                    s.epochs.len() - 1
                }
            };
            let ep = &mut s.epochs[i];
            if ep.tags.iter().any(|t| t.tag == tag) {
                return Err(E_DUP_SUBMIT);
            }
            ep.tags.push(Tag { tag, work, ..Default::default() });
        }
        0x04 => {
            let tag = r.felt(e)?;
            let score = r.u8(e)?;
            let (ring, sig, hint) = read_sig(&mut r)?;
            if score > MAX_SCORE {
                return Err(E_SCORE_RANGE);
            }
            s.check_ring(&ring, ROLE_VALIDATOR)?;
            let i = s.ep(now).ok_or(E_NO_TAG)?;
            let j = s.epochs[i].tags.iter().position(|t| t.tag == tag).ok_or(E_NO_TAG)?;
            verify(&ring, &s.scope_score(now, &tag), &Felt::from(score), &sig, hint)?;
            let nul = lsag::key_id(&sig.image);
            let ep = &mut s.epochs[i];
            if ep.nulls.contains(&nul) {
                return Err(E_DUP_SCORE);
            }
            ep.nulls.push(nul);
            ep.total = add(ep.total, score as u128)?;
            let t = &mut ep.tags[j];
            t.score_sum = add(t.score_sum, score as u128)?;
            t.scores += 1;
            match t.hist.binary_search_by_key(&score, |h| h.0) {
                Ok(k) => t.hist[k].1 += 1,
                Err(k) => t.hist.insert(k, (score, 1)),
            }
        }
        0x05 => {
            let epoch = r.u64(e)?;
            let tag = r.felt(e)?;
            if epoch >= now {
                return Err(E_OPEN_EPOCH);
            }
            let i = s.ep(epoch).ok_or(E_PRUNED)?;
            let ep = &mut s.epochs[i];
            let t = ep.tags.iter_mut().find(|t| t.tag == tag).ok_or(E_NO_TAG)?;
            if t.settled {
                return Err(E_SETTLED);
            }
            let (c, rank) = yuma_settle(&t.hist, ep.vset, t.score_sum);
            t.consensus = c;
            t.rank = rank;
            t.settled = true;
            ep.rank_total = add(ep.rank_total, rank)?;
            ep.nsettled += 1;
        }
        0x06 => {
            let epoch = r.u64(e)?;
            let recipient = r.felt(e)?;
            let (ring, sig, hint) = read_sig(&mut r)?;
            if epoch >= now {
                return Err(E_OPEN_EPOCH);
            }
            s.check_ring(&ring, ROLE_MINER)?;
            verify(&ring, &s.scope_mine(epoch), &recipient, &sig, hint)?;
            let tag = lsag::key_id(&sig.image);
            let i = s.ep(epoch).ok_or(E_PRUNED)?;
            let emission = s.emission;
            let ep = &mut s.epochs[i];
            if ep.nsettled != n32(ep.tags.len()) {
                return Err(E_UNSETTLED);
            }
            let total = ep.rank_total;
            let t = ep.tags.iter_mut().find(|t| t.tag == tag).ok_or(E_NO_TAG)?;
            if t.claimed {
                return Err(E_CLAIMED);
            }
            t.claimed = true;
            // emission * rank / total without overflow (rank <= total)
            let amount = if total == 0 {
                0
            } else {
                let (q, rm) = (emission / total, emission % total);
                add(q.checked_mul(t.rank).ok_or(E_OVERFLOW)?,
                    rm.checked_mul(t.rank).ok_or(E_OVERFLOW)? / total)?
            };
            match s.credit.iter_mut().find(|c| c.0 == recipient) {
                Some(c) => c.1 = add(c.1, amount)?,
                None => s.credit.push((recipient, amount)),
            }
        }
        0x07 => {}
        _ => return Err(E_INPUT),
    }
    if !r.done() {
        return Err(E_INPUT);
    }
    Ok(s.write())
}

// ---- the pqc1 ABI -------------------------------------------------------------

#[no_mangle]
pub extern "C" fn pq_reset() {}

/// The host copies each buffer in here; a fresh instance per run, so the
/// leak is the instance's lifetime.
#[no_mangle]
pub extern "C" fn pq_alloc(n: usize) -> *mut u8 {
    let mut v = vec![0u8; n.max(1)];
    let p = v.as_mut_ptr();
    std::mem::forget(v);
    p
}

unsafe fn slice<'a>(p: *const u8, n: usize) -> &'a [u8] {
    if p.is_null() || n == 0 { &[] } else { std::slice::from_raw_parts(p, n) }
}

#[no_mangle]
pub unsafe extern "C" fn pqc_run(
    state: *const u8,
    state_len: usize,
    input: *const u8,
    input_len: usize,
    out: *mut u8,
    out_cap: usize,
) -> i32 {
    match step(slice(state, state_len), slice(input, input_len)) {
        Err(code) => code,
        Ok(next) if next.len() > out_cap => E_OUT,
        Ok(next) => {
            std::ptr::copy_nonoverlapping(next.as_ptr(), out, next.len());
            next.len() as i32
        }
    }
}

#[cfg(test)]
mod tests;
