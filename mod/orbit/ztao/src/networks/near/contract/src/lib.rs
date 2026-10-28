//! ztao on NEAR: the same anonymous subnet as starknet/contract/src/ztao.cairo,
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
//!   settle(epoch, tag)                 anyone, epoch closed, once per tag:
//!                                      Yuma consensus + clipped rank
//!   claim(epoch, ring, recipient, sig) same scope as submit, so the same
//!                                      image: credits emission * rank /
//!                                      rank_total (every tag settled)
//!
//! Yuma (the reference is core/yuma.py `settle`): a validator weighs 1 (ring
//! membership IS the stake). score() bumps the tag's histogram; settle() walks
//! it from the top score down until need = ceil(Vt/2) votes are covered, where
//! Vt = max(validators at the epoch's first submit, scores). That score is the
//! consensus C; rank R = sum over votes of min(score, C).
//! Layout: one `YumaTag` per (epoch, tag) holding a SPARSE histogram (sorted
//! (score, count) pairs: a tag rarely sees more than a few distinct scores,
//! so it stores ~5 bytes per distinct score instead of a 101-slot array; the
//! zero buckets a dense walk would visit add nothing) + one `YumaEpoch` per
//! epoch (vset snapshot, rank total, tags settled).
//!
//! Chain-specific bits, and nothing else differs from the Cairo contract:
//!   subnet    = felt(sha256(contract account id)), top 5 bits cleared
//!   epoch     = block_height / tempo
//!   recipient = a NEAR account; msg = felt(sha256(recipient)), same rule
//!
//! No method checks the caller except owner admin, so a relayer can post on a
//! member's behalf (the signer of a NEAR tx is public).
pub mod fp;
pub mod lsag;
pub mod poseidon;

use lsag::{felt, hex, Pt, Sig};
use near_sdk::json_types::U128;
use near_sdk::store::{LookupMap, LookupSet, Vector};
use near_sdk::{env, near, require, AccountId, BorshStorageKey, PanicOnDefault};
use starknet_types_core::felt::Felt;

pub const ROLE_MINER: u8 = 1;
pub const ROLE_VALIDATOR: u8 = 2;
/// Gas-bound, not a protocol choice: verification costs ~7.5 Tgas per ring
/// member + ~6 Tgas fixed (measured on near-sandbox: ring 32 = 247 Tgas,
/// ring 40 exceeds the 300 Tgas per-call limit). Starknet's cap is 64.
pub const MAX_RING: u32 = 32;
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
    YumaTag,
    YumaEpoch,
}

/// Yuma state of one (epoch, tag).
#[near(serializers = [borsh])]
#[derive(Clone, Default)]
pub struct YumaTag {
    /// (score, validators who gave it), ascending by score; last = top
    pub hist: Vec<(u8, u32)>,
    pub consensus: u8,
    pub rank: u128,
    pub settled: bool,
}

/// Yuma state of one epoch.
#[near(serializers = [borsh])]
#[derive(Clone, Default)]
pub struct YumaEpoch {
    /// validator count when the epoch's first tag was submitted
    pub vset: u32,
    pub rank_total: u128,
    pub nsettled: u32,
}

/// ceil(kappa * v), kappa = 1/2 (core/yuma.py `need`).
pub fn need(v: u32) -> u32 {
    ((v as u64 + 1) / 2) as u32
}

/// (consensus, rank) from a histogram: core/yuma.py `settle`, the same walk.
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
            // scores >= s clip to s; scores below s are kept as they are
            return (s, score_sum - mass + s as u128 * above);
        }
    }
    (0, 0) // unreachable: the lowest bucket covers all n >= k votes
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
    /// Optional Hp(scope) hint (NEAR only; Starknet computes it). Checked,
    /// never trusted: a wrong hint just costs the slow sqrt. Get it free
    /// from the `hash_point` view.
    #[serde(default)]
    pub hp: Option<Point>,
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
    pub consensus: u8,
    pub rank: U128,
    pub settled: bool,
}

#[near(serializers = [json])]
pub struct Tally {
    pub epoch: u64,
    pub total: U128,
    pub validators: u32,
    pub rank_total: U128,
    pub settled: u32,
    pub tags: Vec<TagRow>,
}

#[near(serializers = [json])]
pub struct YumaView {
    pub consensus: u8,
    pub rank: U128,
    pub settled: bool,
}

#[near(serializers = [json])]
pub struct YumaEpochView {
    pub vset: u32,
    pub rank_total: U128,
    pub nsettled: u32,
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

/// Checked add. The release profile turns overflow-checks OFF (they make
/// every u64/u128 multiply in the inlined field/Poseidon code a `__multi3`
/// call in wasm), so the contract's own arithmetic is checked explicitly.
fn add(a: u128, b: u128) -> u128 {
    a.checked_add(b).unwrap_or_else(|| env::panic_str("overflow"))
}

fn ek(epoch: u64, tag: &str) -> String {
    format!("{epoch}:{tag}")
}

fn event(kind: &str, data: String) {
    env::log_str(&format!(
        "EVENT_JSON:{{\"standard\":\"ztao\",\"version\":\"1.0.0\",\"event\":\"{kind}\",\"data\":[{data}]}}"
    ));
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct ZTao {
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
    ytag: LookupMap<String, YumaTag>,   // "epoch:tag" -> histogram + result
    yepoch: LookupMap<u64, YumaEpoch>,
}

#[near]
impl ZTao {
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
            ytag: LookupMap::new(Key::YumaTag),
            yepoch: LookupMap::new(Key::YumaEpoch),
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
        require!(self.check_sig(&ring, &self.scope_mine(epoch), &w, &sig), "bad ring signature");
        let tag = hex(&lsag::key_id(&pt(&sig.image)));
        let k = ek(epoch, &tag);
        require!(!self.work.contains_key(&k), "already submitted this epoch");
        self.work.insert(k, hex(&w));
        let mut tags = self.tags.get(&epoch).cloned().unwrap_or_default();
        if tags.is_empty() {
            // snapshot V: registrations after this cannot move the payouts
            let vset = self.validators.len();
            self.yepoch.insert(epoch, YumaEpoch { vset, ..Default::default() });
        }
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
        require!(self.check_sig(&ring, &scope, &Felt::from(score), &sig), "bad ring signature");
        let nullifier = hex(&lsag::key_id(&pt(&sig.image)));
        require!(self.spent.insert(nullifier.clone()), "already scored this tag");
        let (s, n) = self.tally.get(&k).copied().unwrap_or((0, 0));
        self.tally.insert(k.clone(), (add(s, score as u128), n.checked_add(1).unwrap_or_else(|| env::panic_str("overflow"))));
        let t = self.total.get(&epoch).copied().unwrap_or(0);
        self.total.insert(epoch, add(t, score as u128));
        let mut y = self.ytag.get(&k).cloned().unwrap_or_default();
        match y.hist.binary_search_by_key(&score, |h| h.0) {
            Ok(i) => y.hist[i].1 += 1,
            Err(i) => y.hist.insert(i, (score, 1)),
        }
        self.ytag.insert(k, y);
        event("scored", format!(
            "{{\"epoch\":{epoch},\"tag\":\"{tag}\",\"score\":{score},\"nullifier\":\"{nullifier}\",\"ring_size\":{}}}",
            ring.len()));
        nullifier
    }

    /// Yuma for one tag of a closed epoch. Anyone may call it, once per tag.
    pub fn settle(&mut self, epoch: u64, tag: String) -> YumaView {
        require!(epoch < self.epoch(), "epoch still open");
        let tag = hex(&f(&tag, "tag"));
        let k = ek(epoch, &tag);
        require!(self.work.contains_key(&k), "no such tag this epoch");
        let mut y = self.ytag.get(&k).cloned().unwrap_or_default();
        require!(!y.settled, "already settled");
        let mut ye = self.yepoch.get(&epoch).cloned().unwrap_or_default();
        let sum = self.tally.get(&k).map(|t| t.0).unwrap_or(0);
        let (c, r) = yuma_settle(&y.hist, ye.vset, sum);
        y.consensus = c;
        y.rank = r;
        y.settled = true;
        self.ytag.insert(k, y);
        ye.rank_total = add(ye.rank_total, r);
        ye.nsettled += 1;
        self.yepoch.insert(epoch, ye);
        event("settled", format!(
            "{{\"epoch\":{epoch},\"tag\":\"{tag}\",\"consensus\":{c},\"rank\":\"{r}\"}}"));
        YumaView { consensus: c, rank: U128(r), settled: true }
    }

    pub fn claim(&mut self, epoch: u64, ring: Vec<Point>, recipient: AccountId, sig: Signature) -> U128 {
        require!(epoch < self.epoch(), "epoch still open");
        let ring = self.check_ring(&ring, ROLE_MINER);
        let msg = text_felt(recipient.as_str());
        require!(self.check_sig(&ring, &self.scope_mine(epoch), &msg, &sig), "bad ring signature");
        let tag = hex(&lsag::key_id(&pt(&sig.image)));
        let k = ek(epoch, &tag);
        require!(self.work.contains_key(&k), "tag did not submit");
        let ye = self.yepoch.get(&epoch).cloned().unwrap_or_default();
        let ntags = self.tags.get(&epoch).map(|t| t.len()).unwrap_or(0) as u32;
        require!(ye.nsettled == ntags, "epoch not settled");
        require!(self.claimed.insert(k.clone()), "already claimed");
        let total = ye.rank_total;
        let sum = self.ytag.get(&k).map(|y| y.rank).unwrap_or(0);
        // emission * rank / rank_total without overflow: rank <= total, so split
        // emission = q*total + r  ->  q*sum + r*sum/total, each term fits
        let amount = if total == 0 {
            0
        } else {
            let (q, r) = (self.emission / total, self.emission % total);
            add(q.checked_mul(sum).unwrap_or_else(|| env::panic_str("overflow")),
                r.checked_mul(sum).unwrap_or_else(|| env::panic_str("overflow")) / total)
        };
        let c = self.credit.get(&recipient).copied().unwrap_or(0);
        self.credit.insert(recipient.clone(), add(c, amount));
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

    pub fn yuma(&self, epoch: u64, tag: String) -> YumaView {
        let y = self.ytag.get(&ek(epoch, &hex(&f(&tag, "tag")))).cloned().unwrap_or_default();
        YumaView { consensus: y.consensus, rank: U128(y.rank), settled: y.settled }
    }

    pub fn yuma_epoch(&self, epoch: u64) -> YumaEpochView {
        let y = self.yepoch.get(&epoch).cloned().unwrap_or_default();
        YumaEpochView { vset: y.vset, rank_total: U128(y.rank_total), nsettled: y.nsettled }
    }

    pub fn tally(&self, epoch: u64) -> Tally {
        let tags = self.tags.get(&epoch).cloned().unwrap_or_default();
        let ye = self.yepoch.get(&epoch).cloned().unwrap_or_default();
        Tally {
            epoch,
            total: U128(self.total.get(&epoch).copied().unwrap_or(0)),
            validators: ye.vset,
            rank_total: U128(ye.rank_total),
            settled: ye.nsettled,
            tags: tags
                .into_iter()
                .map(|tag| {
                    let k = ek(epoch, &tag);
                    let (s, n) = self.tally.get(&k).copied().unwrap_or((0, 0));
                    let y = self.ytag.get(&k).cloned().unwrap_or_default();
                    TagRow {
                        consensus: y.consensus,
                        rank: U128(y.rank),
                        settled: y.settled,
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
        self.check_sig(&ring, &f(&scope, "scope"), &f(&msg, "msg"), &sig)
    }

    /// Hp(scope), the point key images are taken over. Pass it back as
    /// `sig.hp` to skip the on-chain square root (~6 Tgas).
    pub fn hash_point(&self, scope: String) -> Point {
        let p = lsag::hash_to_point(&f(&scope, "scope"));
        Point { x: hex(&p.x), y: hex(&p.y) }
    }
}

impl ZTao {
    fn subnet_f(&self) -> Felt {
        text_felt(env::current_account_id().as_str())
    }

    fn scope_mine(&self, epoch: u64) -> Felt {
        lsag::h(&[lsag::short("zuma-mine"), self.subnet_f(), Felt::from(epoch)])
    }

    fn scope_score_f(&self, epoch: u64, tag: &Felt) -> Felt {
        lsag::h(&[lsag::short("zuma-score"), self.subnet_f(), Felt::from(epoch), *tag])
    }

    fn check_sig(&self, ring: &[Pt], scope: &Felt, msg: &Felt, s: &Signature) -> bool {
        let hint = s.hp.as_ref().map(pt);
        lsag::verify_hinted(ring, scope, msg, &self.sig(s), hint.as_ref())
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

#[cfg(feature = "bench")]
pub mod bench;

#[cfg(test)]
mod tests;
