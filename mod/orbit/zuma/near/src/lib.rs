//! zuma on NEAR: the same anonymous subnet as contracts/src/zuma.cairo,
//! verifying the SAME ring signatures.
//!
//! Two public sets of STARK-curve keys, MINERS and VALIDATORS. Every action
//! is an LSAG ring signature over members of one role (see lsag.rs, a port
//! of lsag.cairo). NEAR has no STARK-curve host function, so the curve math
//! runs in wasm (starknet-types-core): a ring of 4 costs about 35 Tgas
//! (measured on the local sandbox; see README).
//!
//!   submit(ring, work, sig)            key image under (subnet, epoch) = TAG
//!   score(ring, tag, score, sig)       key image under (subnet, epoch, tag)
//!                                      = nullifier, one score per validator
//!   claim(epoch, ring, recipient, sig) same scope as submit, so the same
//!                                      image: credits emission * sum/total
//!
//! Chain-specific bits, and nothing else differs from the Cairo contract:
//!   subnet    = felt(sha256(contract account id)), top 5 bits cleared
//!   epoch     = block_height / tempo
//!   recipient = a NEAR account; msg = felt(sha256(recipient)), same rule
//!
//! No method checks the caller except owner admin, so a relayer can post on a
//! member's behalf (the signer of a NEAR tx is public).
pub mod lsag;

use lsag::{felt, hex, Pt, Sig};
use near_sdk::json_types::U128;
use near_sdk::store::{LookupMap, LookupSet, Vector};
use near_sdk::{env, near, require, AccountId, BorshStorageKey, PanicOnDefault};
use starknet_types_core::felt::Felt;

pub const ROLE_MINER: u8 = 1;
pub const ROLE_VALIDATOR: u8 = 2;
pub const MAX_RING: u32 = 64;
pub const MAX_SCORE: u8 = 100;

#[derive(BorshStorageKey)]
#[near(serializers = [borsh])]
enum Key {
    Role,
    Miners,
    Validators,
    Work,
    Tags,
    Tally,
    Total,
    Spent,
    Claimed,
    Credit,
}

/// A curve point as two "0x…" felts.
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub struct Point {
    pub x: String,
    pub y: String,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct Signature {
    pub c0: String,
    pub s: Vec<String>,
    pub image: Point,
}

#[near(serializers = [json])]
pub struct Params {
    pub owner: AccountId,
    pub tempo: u64,
    pub min_ring: u32,
    pub emission: U128,
    pub open: bool,
    pub subnet: String,
}

#[near(serializers = [json])]
pub struct TagRow {
    pub tag: String,
    pub work: String,
    pub score_sum: U128,
    pub scores: u32,
    pub claimed: bool,
}

#[near(serializers = [json])]
pub struct Tally {
    pub epoch: u64,
    pub total: U128,
    pub tags: Vec<TagRow>,
}

fn f(s: &str, what: &str) -> Felt {
    felt(s).unwrap_or_else(|| env::panic_str(&format!("{what} is not a felt")))
}

fn pt(p: &Point) -> Pt {
    Pt { x: f(&p.x, "point.x"), y: f(&p.y, "point.y") }
}

fn canon(p: &Point) -> Point {
    let q = pt(p);
    Point { x: hex(&q.x), y: hex(&q.y) }
}

/// sha256 with the top 5 bits cleared: always < 2^251 < PRIME.
pub fn text_felt(s: &str) -> Felt {
    let mut b: [u8; 32] = env::sha256_array(s.as_bytes());
    b[0] &= 0x07;
    Felt::from_bytes_be(&b)
}

fn ek(epoch: u64, tag: &str) -> String {
    format!("{epoch}:{tag}")
}

fn event(kind: &str, data: String) {
    env::log_str(&format!(
        "EVENT_JSON:{{\"standard\":\"zuma\",\"version\":\"1.0.0\",\"event\":\"{kind}\",\"data\":[{data}]}}"
    ));
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Zuma {
    owner: AccountId,
    open: bool,
    tempo: u64,
    min_ring: u32,
    emission: u128,
    role: LookupMap<String, u8>,      // key_id -> role
    miners: Vector<Point>,
    validators: Vector<Point>,
    work: LookupMap<String, String>,  // "epoch:tag" -> work
    tags: LookupMap<u64, Vec<String>>,
    tally: LookupMap<String, (u128, u32)>,
    total: LookupMap<u64, u128>,
    spent: LookupSet<String>,         // validator nullifiers
    claimed: LookupSet<String>,
    credit: LookupMap<AccountId, u128>,
}

#[near]
impl Zuma {
    #[init]
    pub fn new(owner: AccountId, tempo: u64, min_ring: u32, emission: U128) -> Self {
        require!(tempo > 0, "tempo must be > 0");
        require!(min_ring > 0 && min_ring <= MAX_RING, "bad min_ring");
        Self {
            owner,
            open: false,
            tempo,
            min_ring,
            emission: emission.0,
            role: LookupMap::new(Key::Role),
            miners: Vector::new(Key::Miners),
            validators: Vector::new(Key::Validators),
            work: LookupMap::new(Key::Work),
            tags: LookupMap::new(Key::Tags),
            tally: LookupMap::new(Key::Tally),
            total: LookupMap::new(Key::Total),
            spent: LookupSet::new(Key::Spent),
            claimed: LookupSet::new(Key::Claimed),
            credit: LookupMap::new(Key::Credit),
        }
    }

    // ---- registry ----------------------------------------------------------

    pub fn register(&mut self, role: u8, key: Point) {
        require!(role == ROLE_MINER || role == ROLE_VALIDATOR, "role is 1 or 2");
        require!(self.open || env::predecessor_account_id() == self.owner, "registry closed");
        let key = canon(&key);
        require!(lsag::on_curve(&pt(&key)), "key not on curve");
        let id = hex(&lsag::key_id(&pt(&key)));
        require!(!self.role.contains_key(&id), "key already registered");
        self.role.insert(id.clone(), role);
        let set = if role == ROLE_MINER { &mut self.miners } else { &mut self.validators };
        set.push(key);
        let index = set.len() - 1;
        event("registered", format!("{{\"role\":{role},\"key_id\":\"{id}\",\"index\":{index}}}"));
    }

    pub fn set_open(&mut self, open: bool) {
        require!(env::predecessor_account_id() == self.owner, "only owner");
        self.open = open;
    }

    pub fn transfer_ownership(&mut self, owner: AccountId) {
        require!(env::predecessor_account_id() == self.owner, "only owner");
        self.owner = owner;
    }

    // ---- anonymous actions -------------------------------------------------

    pub fn submit(&mut self, ring: Vec<Point>, work: String, sig: Signature) -> String {
        let ring = self.check_ring(&ring, ROLE_MINER);
        let epoch = self.epoch();
        let w = f(&work, "work");
        require!(lsag::verify(&ring, &self.scope_mine(epoch), &w, &self.sig(&sig)), "bad ring signature");
        let tag = hex(&lsag::key_id(&pt(&sig.image)));
        let k = ek(epoch, &tag);
        require!(!self.work.contains_key(&k), "already submitted this epoch");
        self.work.insert(k, hex(&w));
        let mut tags = self.tags.get(&epoch).cloned().unwrap_or_default();
        tags.push(tag.clone());
        self.tags.insert(epoch, tags);
        event("submitted", format!(
            "{{\"epoch\":{epoch},\"tag\":\"{tag}\",\"work\":\"{}\",\"ring_size\":{}}}", hex(&w), ring.len()));
        tag
    }

    pub fn score(&mut self, ring: Vec<Point>, tag: String, score: u8, sig: Signature) -> String {
        require!(score <= MAX_SCORE, "score is 0..100");
        let ring = self.check_ring(&ring, ROLE_VALIDATOR);
        let epoch = self.epoch();
        let tag = hex(&f(&tag, "tag"));
        let k = ek(epoch, &tag);
        require!(self.work.contains_key(&k), "no such tag this epoch");
        let scope = self.scope_score_f(epoch, &f(&tag, "tag"));
        require!(lsag::verify(&ring, &scope, &Felt::from(score), &self.sig(&sig)), "bad ring signature");
        let nullifier = hex(&lsag::key_id(&pt(&sig.image)));
        require!(self.spent.insert(nullifier.clone()), "already scored this tag");
        let (s, n) = self.tally.get(&k).copied().unwrap_or((0, 0));
        self.tally.insert(k, (s + score as u128, n + 1));
        let t = self.total.get(&epoch).copied().unwrap_or(0);
        self.total.insert(epoch, t + score as u128);
        event("scored", format!(
            "{{\"epoch\":{epoch},\"tag\":\"{tag}\",\"score\":{score},\"nullifier\":\"{nullifier}\",\"ring_size\":{}}}",
            ring.len()));
        nullifier
    }

    pub fn claim(&mut self, epoch: u64, ring: Vec<Point>, recipient: AccountId, sig: Signature) -> U128 {
        require!(epoch < self.epoch(), "epoch still open");
        let ring = self.check_ring(&ring, ROLE_MINER);
        let msg = text_felt(recipient.as_str());
        require!(lsag::verify(&ring, &self.scope_mine(epoch), &msg, &self.sig(&sig)), "bad ring signature");
        let tag = hex(&lsag::key_id(&pt(&sig.image)));
        let k = ek(epoch, &tag);
        require!(self.work.contains_key(&k), "tag did not submit");
        require!(self.claimed.insert(k.clone()), "already claimed");
        let total = self.total.get(&epoch).copied().unwrap_or(0);
        let sum = self.tally.get(&k).map(|t| t.0).unwrap_or(0);
        let amount = if total == 0 { 0 } else { self.emission * sum / total };
        let c = self.credit.get(&recipient).copied().unwrap_or(0);
        self.credit.insert(recipient.clone(), c + amount);
        event("claimed", format!(
            "{{\"epoch\":{epoch},\"tag\":\"{tag}\",\"recipient\":\"{recipient}\",\"amount\":\"{amount}\"}}"));
        U128(amount)
    }

    // ---- views -------------------------------------------------------------

    pub fn epoch(&self) -> u64 {
        env::block_height() / self.tempo
    }

    pub fn subnet(&self) -> String {
        hex(&self.subnet_f())
    }

    pub fn mine_scope(&self, epoch: u64) -> String {
        hex(&self.scope_mine(epoch))
    }

    pub fn score_scope(&self, epoch: u64, tag: String) -> String {
        hex(&self.scope_score_f(epoch, &f(&tag, "tag")))
    }

    /// The felt a recipient account signs as its claim message.
    pub fn recipient_felt(&self, recipient: AccountId) -> String {
        hex(&text_felt(recipient.as_str()))
    }

    pub fn role_of(&self, key: Point) -> u8 {
        let id = hex(&lsag::key_id(&pt(&key)));
        self.role.get(&id).copied().unwrap_or(0)
    }

    pub fn members(&self, role: u8) -> Vec<Point> {
        let set = if role == ROLE_MINER { &self.miners } else { &self.validators };
        set.iter().cloned().collect()
    }

    pub fn tally(&self, epoch: u64) -> Tally {
        let tags = self.tags.get(&epoch).cloned().unwrap_or_default();
        Tally {
            epoch,
            total: U128(self.total.get(&epoch).copied().unwrap_or(0)),
            tags: tags
                .into_iter()
                .map(|tag| {
                    let k = ek(epoch, &tag);
                    let (s, n) = self.tally.get(&k).copied().unwrap_or((0, 0));
                    TagRow {
                        work: self.work.get(&k).cloned().unwrap_or_default(),
                        score_sum: U128(s),
                        scores: n,
                        claimed: self.claimed.contains(&k),
                        tag,
                    }
                })
                .collect(),
        }
    }

    pub fn earned(&self, who: AccountId) -> U128 {
        U128(self.credit.get(&who).copied().unwrap_or(0))
    }

    pub fn params(&self) -> Params {
        Params {
            owner: self.owner.clone(),
            tempo: self.tempo,
            min_ring: self.min_ring,
            emission: U128(self.emission),
            open: self.open,
            subnet: self.subnet(),
        }
    }

    /// Off-chain-style check of a signature against this subnet's mine scope
    /// (no state change; handy for clients).
    pub fn verify(&self, ring: Vec<Point>, scope: String, msg: String, sig: Signature) -> bool {
        let ring: Vec<Pt> = ring.iter().map(pt).collect();
        lsag::verify(&ring, &f(&scope, "scope"), &f(&msg, "msg"), &self.sig(&sig))
    }
}

impl Zuma {
    fn subnet_f(&self) -> Felt {
        text_felt(env::current_account_id().as_str())
    }

    fn scope_mine(&self, epoch: u64) -> Felt {
        lsag::h(&[lsag::short("zuma-mine"), self.subnet_f(), Felt::from(epoch)])
    }

    fn scope_score_f(&self, epoch: u64, tag: &Felt) -> Felt {
        lsag::h(&[lsag::short("zuma-score"), self.subnet_f(), Felt::from(epoch), *tag])
    }

    fn sig(&self, s: &Signature) -> Sig {
        Sig {
            c0: f(&s.c0, "c0"),
            s: s.s.iter().map(|v| f(v, "s")).collect(),
            image: pt(&s.image),
        }
    }

    /// Ring = strictly increasing key ids, all registered with `role`.
    fn check_ring(&self, ring: &[Point], role: u8) -> Vec<Pt> {
        let n = ring.len() as u32;
        require!(n >= self.min_ring, "ring below min_ring");
        require!(n <= MAX_RING, "ring above MAX_RING");
        let mut prev: Option<[u8; 32]> = None;
        ring.iter()
            .map(|p| {
                let q = pt(p);
                let id = lsag::key_id(&q);
                require!(self.role.get(&hex(&id)).copied() == Some(role), "ring has a non-member");
                let b = id.to_bytes_be();
                require!(prev.map_or(true, |pb| b > pb), "ring not sorted/unique");
                prev = Some(b);
                q
            })
            .collect()
    }
}

#[cfg(test)]
mod tests;
