//! zuma on Solana: an anonymous Bittensor-style subnet.
//!
//! Same protocol, same signatures as contracts/ (Starknet), near/ (NEAR) and
//! eth/ (Ethereum): two public sets of STARK-curve keys, MINERS and
//! VALIDATORS, and every action is an LSAG ring signature over one set.
//!
//!   submit  key image under H('zuma-mine', subnet, epoch) = the epoch TAG
//!   score   key image under H('zuma-score', subnet, epoch, tag) = nullifier
//!   claim   re-sign the mine scope (same image = same tag), credit
//!           emission * sum / total to a recipient's EARNED account
//!
//! What is Solana-specific is HOW a signature gets verified: no transaction
//! can afford it (verify.rs), so an action is a SESSION account the relayer
//! fills with the signature plus hints, independent UNITS that each check a
//! slice and set a bit, and `finish`, which applies the action once every
//! bit is set and refunds the session's rent.
//!
//! Accounts
//!   subnet       client-created, program-owned: params + both member sets
//!   session      client-created, program-owned, one per action
//!   epoch PDA    ["epoch", subnet, epoch u64 le]: tags, scores, total
//!   null PDA     ["null", subnet, nullifier]: exists = validator spent
//!   earned PDA   ["earned", subnet, recipient]: u128 points
//!
//! Chain-specific inputs (nothing else differs from the Cairo contract):
//!   subnet felt     sha256(subnet account) with the top 5 bits cleared
//!   epoch           (slot - start slot) / tempo
//!   claim message   sha256(recipient pubkey), top 5 bits cleared
#![allow(clippy::too_many_arguments, deprecated)]
extern crate alloc;

#[path = "../../near/src/fp.rs"]
pub mod fp;
#[path = "../../near/src/poseidon.rs"]
pub mod poseidon;
pub mod verify;
#[cfg(test)]
mod tests;

use alloc::vec::Vec;
use solana_program::{
    account_info::AccountInfo,
    clock::Clock,
    entrypoint::ProgramResult,
    hash::hash,
    msg,
    program::invoke_signed,
    program_error::ProgramError,
    pubkey::Pubkey,
    rent::Rent,
    system_instruction,
    sysvar::Sysvar,
};
use starknet_types_core::felt::Felt;
use fp::{Aff, Fp};
use verify::*;

#[cfg(not(feature = "no-entrypoint"))]
solana_program::entrypoint!(process);

pub const MAX_RING: usize = 32;
pub const MAX_MEMBERS: usize = 64;
pub const MAX_TAGS: usize = 32;
pub const MAX_HP_CTR: usize = 15;
pub const MAX_SCORE: u8 = 100;
pub const ROLE_MINER: u8 = 1;
pub const ROLE_VALIDATOR: u8 = 2;
pub const KIND_SUBMIT: u8 = 1;
pub const KIND_SCORE: u8 = 2;
pub const KIND_CLAIM: u8 = 3;

// ---- subnet layout ---------------------------------------------------------------
const SN_MAGIC: &[u8; 8] = b"zumaSN01";
const SN_OWNER: usize = 8;
const SN_TEMPO: usize = 40;
const SN_START: usize = 48;
const SN_MIN_RING: usize = 56;
const SN_OPEN: usize = 60;
const SN_EMISSION: usize = 64;
const SN_FELT: usize = 80;
const SN_COUNT: usize = 112; // u32 miners, u32 validators
const SN_MEMBERS: usize = 120; // [role][64] x (x, y, key_id)
const MEMBER: usize = 96;
pub const SUBNET_SIZE: usize = SN_MEMBERS + 2 * MAX_MEMBERS * MEMBER;

// ---- epoch layout -------------------------------------------------------------------
const EP_MAGIC: &[u8; 8] = b"zumaEP01";
const EP_TOTAL: usize = 8;
const EP_COUNT: usize = 24;
const EP_TAGS: usize = 32;
const TAG: usize = 88; // tag, work, sum u128, n u32, claimed u8, pad
pub const EPOCH_SIZE: usize = EP_TAGS + MAX_TAGS * TAG;

// ---- session layout -----------------------------------------------------------------
const SS_MAGIC: &[u8; 8] = b"zumaSS01";
const SS_SUBNET: usize = 8;
const SS_RELAYER: usize = 40;
const SS_KIND: usize = 72;
const SS_N: usize = 73;
const SS_STATE: usize = 74;
const SS_EPOCH: usize = 80;
const SS_MSG: usize = 88;
const SS_TAG: usize = 120;
const SS_RECIPIENT: usize = 152;
const SS_SCOPE: usize = 184;
const SS_DONE: usize = 216; // 512-bit bitmap
pub const SS_PUT: usize = 280; // everything from here on is relayer-written
const SS_C0: usize = 280;
const SS_IMAGE: usize = 312;
const SS_HP: usize = 376;
const SS_D: usize = 440;
const SS_HP_CTR: usize = 472;
const SS_RING: usize = 480; // member indices, u8 each
pub const SS_ARRAYS: usize = 512;

/// Byte offsets of the per-ring arrays for a ring of n.
pub struct Arrays {
    pub s: usize,
    pub c: usize,
    pub l: usize,
    pub r: usize,
    pub mids: usize, // [i][which][SEGS-1] x 64
    pub sponge: usize, // [ndig-1] x 96
    pub end: usize,
}

pub fn n_blocks(n: usize) -> usize {
    n + 4 // (6 + 2n felts, + 1, + 0) / 2
}

pub fn n_digest(n: usize) -> usize {
    n_blocks(n).div_ceil(DIG_EVERY)
}

pub fn arrays(n: usize) -> Arrays {
    let s = SS_ARRAYS;
    let c = s + 32 * n;
    let l = c + 32 * n;
    let r = l + 64 * n;
    let mids = r + 64 * n;
    let sponge = mids + 64 * n * 2 * (SEGS - 1);
    let end = sponge + 96 * (n_digest(n) - 1);
    Arrays { s, c, l, r, mids, sponge, end }
}

pub fn session_size(n: usize) -> usize {
    arrays(n).end
}

/// Bit layout of the done-map: mul units, then chal, digest, hp.
pub fn bit_mul(i: usize, which: usize, seg: usize) -> usize {
    (i * 2 + which) * SEGS + seg
}
pub fn bit_chal(n: usize, i: usize) -> usize {
    2 * n * SEGS + i
}
pub fn bit_digest(n: usize, k: usize) -> usize {
    2 * n * SEGS + n + k
}
pub fn bit_hp(n: usize, j: usize) -> usize {
    2 * n * SEGS + n + n_digest(n) + j
}

fn err(m: &str) -> ProgramError {
    msg!("zuma: {}", m);
    ProgramError::Custom(1)
}

fn need(cond: bool, m: &str) -> ProgramResult {
    if cond {
        Ok(())
    } else {
        Err(err(m))
    }
}

fn u32_at(d: &[u8], o: usize) -> u32 {
    u32::from_le_bytes(d[o..o + 4].try_into().unwrap())
}
fn u64_at(d: &[u8], o: usize) -> u64 {
    u64::from_le_bytes(d[o..o + 8].try_into().unwrap())
}
fn u128_at(d: &[u8], o: usize) -> u128 {
    u128::from_le_bytes(d[o..o + 16].try_into().unwrap())
}

fn felt_bytes(f: &Felt) -> [u8; 32] {
    f.to_bytes_be()
}

/// sha256 with the top 5 bits cleared: a felt (< 2^251 < p).
pub fn felt_of(bytes: &[u8]) -> Felt {
    let mut h = hash(bytes).to_bytes();
    h[0] &= 0x07;
    Felt::from_bytes_be(&h)
}

fn key_id(x: &Felt, y: &Felt) -> Felt {
    verify::hash(&[short("zuma-key"), *x, *y])
}

fn owned(a: &AccountInfo, program: &Pubkey, magic: &[u8; 8]) -> ProgramResult {
    need(a.owner == program, "account not owned by zuma")?;
    need(&a.try_borrow_data()?[..8] == magic, "wrong account kind")
}

fn epoch_now(sn: &[u8]) -> Result<u64, ProgramError> {
    let slot = Clock::get()?.slot;
    Ok(slot.saturating_sub(u64_at(sn, SN_START)) / u64_at(sn, SN_TEMPO))
}

fn member(sn: &[u8], role: u8, i: usize) -> &[u8] {
    let o = SN_MEMBERS + ((role - 1) as usize * MAX_MEMBERS + i) * MEMBER;
    &sn[o..o + MEMBER]
}

fn count(sn: &[u8], role: u8) -> usize {
    u32_at(sn, SN_COUNT + 4 * (role - 1) as usize) as usize
}

fn role_of(kind: u8) -> u8 {
    if kind == KIND_SCORE {
        ROLE_VALIDATOR
    } else {
        ROLE_MINER
    }
}

/// Create a PDA owned by this program (payer = the relayer).
fn create_pda<'a>(
    program: &Pubkey,
    payer: &AccountInfo<'a>,
    pda: &AccountInfo<'a>,
    system: &AccountInfo<'a>,
    seeds: &[&[u8]],
    space: usize,
) -> ProgramResult {
    let (key, bump) = Pubkey::find_program_address(seeds, program);
    need(&key == pda.key, "wrong PDA")?;
    let rent = Rent::get()?.minimum_balance(space);
    let mut full: Vec<&[u8]> = seeds.to_vec();
    let b = [bump];
    full.push(&b);
    invoke_signed(
        &system_instruction::create_account(payer.key, pda.key, rent, space as u64, program),
        &[payer.clone(), pda.clone(), system.clone()],
        &[&full],
    )
}

pub fn process(program: &Pubkey, accounts: &[AccountInfo], data: &[u8]) -> ProgramResult {
    let (&tag, body) = data.split_first().ok_or(ProgramError::InvalidInstructionData)?;
    match tag {
        0 => init(program, accounts, body),
        1 => register(program, accounts, body),
        2 => set_open(program, accounts, body),
        3 => open(program, accounts, body),
        4 => put(program, accounts, body),
        5 => seal(program, accounts),
        6 => unit(program, accounts, body),
        7 => finish(program, accounts),
        8 => abort(program, accounts),
        _ => Err(ProgramError::InvalidInstructionData),
    }
}

// ---- subnet ----------------------------------------------------------------------------

/// [owner (signer), subnet (w, zeroed, SUBNET_SIZE)] tempo u64, min_ring u32, emission u128
fn init(program: &Pubkey, a: &[AccountInfo], b: &[u8]) -> ProgramResult {
    let (owner, sn) = (&a[0], &a[1]);
    need(owner.is_signer, "owner must sign")?;
    need(sn.owner == program && sn.data_len() == SUBNET_SIZE, "subnet account: wrong owner/size")?;
    let mut d = sn.try_borrow_mut_data()?;
    need(d[..8] == [0u8; 8], "subnet already initialised")?;
    let (tempo, min_ring, emission) = (u64_at(b, 0), u32_at(b, 8), u128_at(b, 12));
    need(tempo > 0, "tempo must be > 0")?;
    need(min_ring > 0 && min_ring as usize <= MAX_RING, "bad min_ring")?;
    d[..8].copy_from_slice(SN_MAGIC);
    d[SN_OWNER..SN_OWNER + 32].copy_from_slice(owner.key.as_ref());
    d[SN_TEMPO..SN_TEMPO + 8].copy_from_slice(&tempo.to_le_bytes());
    d[SN_START..SN_START + 8].copy_from_slice(&Clock::get()?.slot.to_le_bytes());
    d[SN_MIN_RING..SN_MIN_RING + 4].copy_from_slice(&min_ring.to_le_bytes());
    d[SN_EMISSION..SN_EMISSION + 16].copy_from_slice(&emission.to_le_bytes());
    d[SN_FELT..SN_FELT + 32].copy_from_slice(&felt_bytes(&felt_of(sn.key.as_ref())));
    msg!("zuma: subnet up, tempo {} slots", tempo);
    Ok(())
}

/// [signer, subnet (w)] role u8, x [32], y [32]
fn register(program: &Pubkey, a: &[AccountInfo], b: &[u8]) -> ProgramResult {
    let (who, sn) = (&a[0], &a[1]);
    owned(sn, program, SN_MAGIC)?;
    need(who.is_signer, "signer required")?;
    let mut d = sn.try_borrow_mut_data()?;
    need(d[SN_OPEN] == 1 || who.key.as_ref() == &d[SN_OWNER..SN_OWNER + 32], "registry closed")?;
    let role = b[0];
    need(role == ROLE_MINER || role == ROLE_VALIDATOR, "role is 1 or 2")?;
    let (x, y) = (felt(&b[1..33]).ok_or(err("x not a felt"))?, felt(&b[33..65]).ok_or(err("y not a felt"))?);
    need(on_curve(&Aff { x: Fp::from_felt(&x), y: Fp::from_felt(&y), inf: false }), "key not on curve")?;
    let id = felt_bytes(&key_id(&x, &y));
    for r in [ROLE_MINER, ROLE_VALIDATOR] {
        for i in 0..count(&d, r) {
            need(member(&d, r, i)[64..96] != id, "key already registered")?;
        }
    }
    let n = count(&d, role);
    need(n < MAX_MEMBERS, "member set full")?;
    let o = SN_MEMBERS + ((role - 1) as usize * MAX_MEMBERS + n) * MEMBER;
    d[o..o + 64].copy_from_slice(&b[1..65]);
    d[o + 64..o + 96].copy_from_slice(&id);
    let c = SN_COUNT + 4 * (role - 1) as usize;
    d[c..c + 4].copy_from_slice(&((n + 1) as u32).to_le_bytes());
    msg!("zuma: registered role {} index {}", role, n);
    Ok(())
}

/// [owner (signer), subnet (w)] open u8
fn set_open(program: &Pubkey, a: &[AccountInfo], b: &[u8]) -> ProgramResult {
    owned(&a[1], program, SN_MAGIC)?;
    let mut d = a[1].try_borrow_mut_data()?;
    need(a[0].is_signer && a[0].key.as_ref() == &d[SN_OWNER..SN_OWNER + 32], "only owner")?;
    d[SN_OPEN] = (b[0] != 0) as u8;
    Ok(())
}

// ---- sessions ---------------------------------------------------------------------------

/// [relayer (signer), session (w, zeroed, session_size(n)), subnet]
/// kind u8, n u8, epoch u64 (claim), msg [32] (submit: work felt; score:
/// score in the last byte), tag [32] (score), recipient [32] (claim)
fn open(program: &Pubkey, a: &[AccountInfo], b: &[u8]) -> ProgramResult {
    let (relayer, ss, sn) = (&a[0], &a[1], &a[2]);
    need(relayer.is_signer, "relayer must sign")?;
    owned(sn, program, SN_MAGIC)?;
    let snd = sn.try_borrow_data()?;
    let (kind, n) = (b[0], b[1] as usize);
    need((KIND_SUBMIT..=KIND_CLAIM).contains(&kind), "kind is 1..3")?;
    need(n >= u32_at(&snd, SN_MIN_RING) as usize, "ring below min_ring")?;
    need(n <= MAX_RING, "ring above MAX_RING")?;
    need(n <= count(&snd, role_of(kind)), "ring larger than the set")?;
    need(ss.owner == program && ss.data_len() == session_size(n), "session account: wrong owner/size")?;
    let mut d = ss.try_borrow_mut_data()?;
    need(d[..8] == [0u8; 8], "session already used")?;
    let now = epoch_now(&snd)?;
    let sub = Felt::from_bytes_be(&snd[SN_FELT..SN_FELT + 32].try_into().unwrap());
    let (epoch, msg_b, scope) = match kind {
        KIND_SUBMIT => {
            felt(&b[10..42]).ok_or(err("work not a felt"))?;
            (now, b[10..42].to_vec(), verify::hash(&[short("zuma-mine"), sub, Felt::from(now)]))
        }
        KIND_SCORE => {
            need(b[10..41].iter().all(|&x| x == 0) && b[41] <= MAX_SCORE, "score is 0..100")?;
            let tag = felt(&b[42..74]).ok_or(err("tag not a felt"))?;
            (now, b[10..42].to_vec(), verify::hash(&[short("zuma-score"), sub, Felt::from(now), tag]))
        }
        _ => {
            let e = u64_at(b, 2);
            need(e < now, "epoch still open")?;
            let to = felt_of(&b[74..106]);
            (e, felt_bytes(&to).to_vec(), verify::hash(&[short("zuma-mine"), sub, Felt::from(e)]))
        }
    };
    d[..8].copy_from_slice(SS_MAGIC);
    d[SS_SUBNET..SS_SUBNET + 32].copy_from_slice(sn.key.as_ref());
    d[SS_RELAYER..SS_RELAYER + 32].copy_from_slice(relayer.key.as_ref());
    d[SS_KIND] = kind;
    d[SS_N] = n as u8;
    d[SS_EPOCH..SS_EPOCH + 8].copy_from_slice(&epoch.to_le_bytes());
    d[SS_MSG..SS_MSG + 32].copy_from_slice(&msg_b);
    d[SS_TAG..SS_TAG + 32].copy_from_slice(&b[42..74]);
    d[SS_RECIPIENT..SS_RECIPIENT + 32].copy_from_slice(&b[74..106]);
    d[SS_SCOPE..SS_SCOPE + 32].copy_from_slice(&felt_bytes(&scope));
    msg!("zuma: session kind {} ring {} epoch {}", kind, n, epoch);
    Ok(())
}

/// [relayer (signer), session (w)] offset u32, bytes
fn put(program: &Pubkey, a: &[AccountInfo], b: &[u8]) -> ProgramResult {
    let (relayer, ss) = (&a[0], &a[1]);
    owned(ss, program, SS_MAGIC)?;
    let mut d = ss.try_borrow_mut_data()?;
    need(relayer.is_signer && relayer.key.as_ref() == &d[SS_RELAYER..SS_RELAYER + 32], "not this session's relayer")?;
    need(d[SS_STATE] == 0, "session sealed")?;
    let off = u32_at(b, 0) as usize;
    let bytes = &b[4..];
    need(off >= SS_PUT && off + bytes.len() <= d.len(), "put out of range")?;
    d[off..off + bytes.len()].copy_from_slice(bytes);
    Ok(())
}

/// [relayer (signer), session (w), subnet] freeze the hints; check what
/// needs no heavy math: the ring (members of the right role, strictly
/// increasing key ids), the image on the curve, c[0] == c0, Hp's root choice.
fn seal(program: &Pubkey, a: &[AccountInfo]) -> ProgramResult {
    let (relayer, ss, sn) = (&a[0], &a[1], &a[2]);
    owned(ss, program, SS_MAGIC)?;
    owned(sn, program, SN_MAGIC)?;
    let mut d = ss.try_borrow_mut_data()?;
    let snd = sn.try_borrow_data()?;
    need(relayer.is_signer && relayer.key.as_ref() == &d[SS_RELAYER..SS_RELAYER + 32], "not this session's relayer")?;
    need(&d[SS_SUBNET..SS_SUBNET + 32] == sn.key.as_ref(), "wrong subnet")?;
    need(d[SS_STATE] == 0, "already sealed")?;
    let n = d[SS_N] as usize;
    let role = role_of(d[SS_KIND]);
    let cnt = count(&snd, role);
    let mut prev: Option<&[u8]> = None;
    for i in 0..n {
        let idx = d[SS_RING + i] as usize;
        need(idx < cnt, "ring has a non-member")?;
        let id = &member(&snd, role, idx)[64..96];
        need(prev.map_or(true, |p| id > p), "ring not sorted/unique")?;
        prev = Some(id);
    }
    let image = point(&d[SS_IMAGE..SS_IMAGE + 64]).ok_or(err("image not felts"))?;
    need(on_curve(&image), "image not on curve")?;
    let ar = arrays(n);
    need(d[ar.c..ar.c + 32] == d[SS_C0..SS_C0 + 32], "c[0] != c0")?;
    let hy = felt(&d[SS_HP + 32..SS_HP + 64]).ok_or(err("hp not felts"))?;
    need(hy.to_bytes_be() <= (-hy).to_bytes_be(), "hp: y not the smaller root")?;
    need(u32_at(&d, SS_HP_CTR) as usize <= MAX_HP_CTR, "hp counter too large")?;
    d[SS_STATE] = 1;
    Ok(())
}

fn mark(d: &mut [u8], bit: usize) {
    d[SS_DONE + bit / 8] |= 1 << (bit % 8);
}

fn is_marked(d: &[u8], bit: usize) -> bool {
    d[SS_DONE + bit / 8] & (1 << (bit % 8)) != 0
}

fn ring_point(d: &[u8], sn: &[u8], i: usize) -> Aff {
    let m = member(sn, role_of(d[SS_KIND]), d[SS_RING + i] as usize);
    point(&m[..64]).unwrap()
}

/// [session (w), subnet] kind u8 (0 mul, 1 chal, 2 digest, 3 hp), i u8,
/// which u8, seg u8. Anyone may run a unit: it only checks.
fn unit(program: &Pubkey, a: &[AccountInfo], b: &[u8]) -> ProgramResult {
    let (ss, sn) = (&a[0], &a[1]);
    owned(ss, program, SS_MAGIC)?;
    owned(sn, program, SN_MAGIC)?;
    let mut d = ss.try_borrow_mut_data()?;
    let snd = sn.try_borrow_data()?;
    need(&d[SS_SUBNET..SS_SUBNET + 32] == sn.key.as_ref(), "wrong subnet")?;
    need(d[SS_STATE] == 1, "session not sealed")?;
    let n = d[SS_N] as usize;
    let ar = arrays(n);
    let (kind, i, which, seg) = (b[0], b[1] as usize, b[2] as usize, b[3] as usize);
    let bad = || err("bad hint");
    let bit = match kind {
        0 => {
            need(i < n && which < 2 && seg < SEGS, "unit out of range")?;
            let s = felt(&d[ar.s + 32 * i..ar.s + 32 * i + 32]).ok_or_else(bad)?;
            let c = felt(&d[ar.c + 32 * i..ar.c + 32 * i + 32]).ok_or_else(bad)?;
            let (pa, pb) = if which == 0 {
                (generator(), ring_point(&d, &snd, i))
            } else {
                (point(&d[SS_HP..SS_HP + 64]).ok_or_else(bad)?, point(&d[SS_IMAGE..SS_IMAGE + 64]).ok_or_else(bad)?)
            };
            let mid = |k: usize| ar.mids + 64 * ((i * 2 + which) * (SEGS - 1) + k);
            let from = if seg == 0 { Aff { x: Fp::ZERO, y: Fp::ZERO, inf: true } } else {
                point(&d[mid(seg - 1)..mid(seg - 1) + 64]).ok_or_else(bad)?
            };
            let to_o = if seg == SEGS - 1 { if which == 0 { ar.l + 64 * i } else { ar.r + 64 * i } } else { mid(seg) };
            let to = point(&d[to_o..to_o + 64]).ok_or_else(bad)?;
            need(mul_segment(seg, &s, &pa, &c, &pb, &from, &to), "mul: hint mismatch")?;
            bit_mul(i, which, seg)
        }
        1 => {
            need(i < n, "unit out of range")?;
            let f = |o: usize| felt(&d[o..o + 32]).ok_or_else(bad);
            let (lo, ro) = (ar.l + 64 * i, ar.r + 64 * i);
            let next = f(ar.c + 32 * ((i + 1) % n))?;
            let got = verify::hash(&[short("zuma-chal"), f(SS_D)?, f(lo)?, f(lo + 32)?, f(ro)?, f(ro + 32)?]);
            need(got == next, "chal: hint mismatch")?;
            bit_chal(n, i)
        }
        2 => {
            let nd = n_digest(n);
            need(i < nd, "unit out of range")?;
            let f = |o: usize| felt(&d[o..o + 32]).ok_or_else(bad);
            let mut ring = Vec::with_capacity(n);
            for k in 0..n {
                let p = ring_point(&d, &snd, k);
                ring.push((p.x.to_felt(), p.y.to_felt()));
            }
            let head = [short("zuma-msg"), f(SS_SCOPE)?, f(SS_MSG)?, f(SS_IMAGE)?, f(SS_IMAGE + 32)?, Felt::from(n as u64)];
            let blocks = digest_blocks(head, &ring);
            let mut st = if i == 0 { [Fp::ZERO; 3] } else {
                let o = ar.sponge + 96 * (i - 1);
                [fp(&d[o..o + 32]).ok_or_else(bad)?, fp(&d[o + 32..o + 64]).ok_or_else(bad)?, fp(&d[o + 64..o + 96]).ok_or_else(bad)?]
            };
            let hi = (DIG_EVERY * (i + 1)).min(blocks.len());
            absorb(&mut st, &blocks[DIG_EVERY * i..hi]);
            if i == nd - 1 {
                need(st[0].to_felt() == f(SS_D)?, "digest: hint mismatch")?;
            } else {
                let o = ar.sponge + 96 * i;
                for k in 0..3 {
                    need(Some(st[k]) == fp(&d[o + 32 * k..o + 32 * k + 32]), "digest: hint mismatch")?;
                }
            }
            bit_digest(n, i)
        }
        3 => {
            let ctr = u32_at(&d, SS_HP_CTR) as usize;
            need(i <= ctr, "unit out of range")?;
            let scope = felt(&d[SS_SCOPE..SS_SCOPE + 32]).ok_or_else(bad)?;
            let x = verify::hash(&[short("zuma-h2p"), scope, Felt::from(i as u64)]);
            let xf = Fp::from_felt(&x);
            let r = rhs(&xf);
            if i < ctr {
                need(!r.is_zero() && !is_square(&r), "hp: skipped a curve point")?;
            } else {
                let hp = point(&d[SS_HP..SS_HP + 64]).ok_or_else(bad)?;
                need(!hp.inf && hp.x == xf && hp.y.sq() == r, "hp: hint is not Hp(scope)")?;
            }
            bit_hp(n, i)
        }
        _ => return Err(ProgramError::InvalidInstructionData),
    };
    mark(&mut d, bit);
    Ok(())
}

fn close_session(ss: &AccountInfo, to: &AccountInfo) -> ProgramResult {
    let l = ss.lamports();
    **ss.try_borrow_mut_lamports()? -= l;
    **to.try_borrow_mut_lamports()? += l;
    ss.try_borrow_mut_data()?.fill(0);
    Ok(())
}

fn find_tag(ep: &[u8], tag: &[u8]) -> Option<usize> {
    (0..u32_at(ep, EP_COUNT) as usize).map(|k| EP_TAGS + k * TAG).find(|&o| &ep[o..o + 32] == tag)
}

/// [relayer (signer, w), session (w), subnet, epoch PDA (w), extra (w):
/// null PDA (score) | earned PDA (claim) | anything (submit), system program]
fn finish(program: &Pubkey, a: &[AccountInfo]) -> ProgramResult {
    let (relayer, ss, sn, ep, extra, system) = (&a[0], &a[1], &a[2], &a[3], &a[4], &a[5]);
    owned(ss, program, SS_MAGIC)?;
    owned(sn, program, SN_MAGIC)?;
    let (kind, n, epoch, msg_b, tag_b, recipient, image) = {
        let d = ss.try_borrow_data()?;
        need(relayer.is_signer && relayer.key.as_ref() == &d[SS_RELAYER..SS_RELAYER + 32], "not this session's relayer")?;
        need(&d[SS_SUBNET..SS_SUBNET + 32] == sn.key.as_ref(), "wrong subnet")?;
        need(d[SS_STATE] == 1, "session not sealed")?;
        let n = d[SS_N] as usize;
        let total = bit_hp(n, u32_at(&d, SS_HP_CTR) as usize + 1);
        for bit in 0..total {
            need(is_marked(&d, bit), "signature not fully verified yet")?;
        }
        (d[SS_KIND], n, u64_at(&d, SS_EPOCH), d[SS_MSG..SS_MSG + 32].to_vec(), d[SS_TAG..SS_TAG + 32].to_vec(),
         d[SS_RECIPIENT..SS_RECIPIENT + 32].to_vec(), d[SS_IMAGE..SS_IMAGE + 64].to_vec())
    };
    // the key image's id: the TAG (submit/claim) or the NULLIFIER (score)
    let img_id = felt_bytes(&key_id(&felt(&image[..32]).unwrap(), &felt(&image[32..]).unwrap()));
    let (now, emission) = {
        let snd = sn.try_borrow_data()?;
        (epoch_now(&snd)?, u128_at(&snd, SN_EMISSION))
    };
    let e_le = epoch.to_le_bytes();
    let seeds: [&[u8]; 3] = [b"epoch", sn.key.as_ref(), &e_le];
    need(Pubkey::find_program_address(&seeds, program).0 == *ep.key, "wrong epoch account")?;
    match kind {
        KIND_SUBMIT => {
            need(epoch == now, "epoch closed before the signature was verified")?;
            if ep.data_len() == 0 {
                create_pda(program, relayer, ep, system, &seeds, EPOCH_SIZE)?;
                ep.try_borrow_mut_data()?[..8].copy_from_slice(EP_MAGIC);
            }
            owned(ep, program, EP_MAGIC)?;
            let mut e = ep.try_borrow_mut_data()?;
            need(find_tag(&e, &img_id).is_none(), "already submitted this epoch")?;
            let k = u32_at(&e, EP_COUNT) as usize;
            need(k < MAX_TAGS, "epoch full")?;
            let o = EP_TAGS + k * TAG;
            e[o..o + 32].copy_from_slice(&img_id);
            e[o + 32..o + 64].copy_from_slice(&msg_b);
            e[EP_COUNT..EP_COUNT + 4].copy_from_slice(&((k + 1) as u32).to_le_bytes());
            msg!("zuma: submitted epoch {} ring {} tag 0x{}", epoch, n, hex(&img_id));
        }
        KIND_SCORE => {
            need(epoch == now, "epoch closed before the signature was verified")?;
            owned(ep, program, EP_MAGIC)?;
            need(extra.data_len() == 0, "already scored this tag")?;
            create_pda(program, relayer, extra, system, &[b"null", sn.key.as_ref(), &img_id], 1)?;
            extra.try_borrow_mut_data()?[0] = 1;
            let mut e = ep.try_borrow_mut_data()?;
            let o = find_tag(&e, &tag_b).ok_or(err("no such tag this epoch"))?;
            let score = msg_b[31] as u128;
            let sum = u128_at(&e, o + 64) + score;
            e[o + 64..o + 80].copy_from_slice(&sum.to_le_bytes());
            let cnt = u32_at(&e, o + 80) + 1;
            e[o + 80..o + 84].copy_from_slice(&cnt.to_le_bytes());
            let tot = u128_at(&e, EP_TOTAL) + score;
            e[EP_TOTAL..EP_TOTAL + 16].copy_from_slice(&tot.to_le_bytes());
            msg!("zuma: scored {} ring {} nullifier 0x{}", score, n, hex(&img_id));
        }
        _ => {
            need(epoch < now, "epoch still open")?;
            owned(ep, program, EP_MAGIC)?;
            let amount = {
                let mut e = ep.try_borrow_mut_data()?;
                let o = find_tag(&e, &img_id).ok_or(err("tag did not submit"))?;
                need(e[o + 84] == 0, "already claimed")?;
                e[o + 84] = 1;
                let total = u128_at(&e, EP_TOTAL);
                if total == 0 { 0 } else { emission * u128_at(&e, o + 64) / total }
            };
            if extra.data_len() == 0 {
                create_pda(program, relayer, extra, system, &[b"earned", sn.key.as_ref(), &recipient], 16)?;
            } else {
                let (k, _) = Pubkey::find_program_address(&[b"earned", sn.key.as_ref(), &recipient], program);
                need(&k == extra.key && extra.owner == program, "wrong earned account")?;
            }
            let mut x = extra.try_borrow_mut_data()?;
            let v = u128_at(&x, 0) + amount;
            x[..16].copy_from_slice(&v.to_le_bytes());
            msg!("zuma: claimed epoch {} amount {}", epoch, amount);
        }
    }
    close_session(ss, relayer)
}

/// [relayer (signer, w), session (w)] give up on a session, refund its rent.
fn abort(program: &Pubkey, a: &[AccountInfo]) -> ProgramResult {
    let (relayer, ss) = (&a[0], &a[1]);
    owned(ss, program, SS_MAGIC)?;
    {
        let d = ss.try_borrow_data()?;
        need(relayer.is_signer && relayer.key.as_ref() == &d[SS_RELAYER..SS_RELAYER + 32], "not this session's relayer")?;
    }
    close_session(ss, relayer)
}

fn hex(b: &[u8]) -> alloc::string::String {
    b.iter().map(|x| alloc::format!("{:02x}", x)).collect()
}
