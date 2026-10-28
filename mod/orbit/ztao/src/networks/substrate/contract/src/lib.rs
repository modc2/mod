//! ztao on Substrate: the same anonymous subnet as the Starknet, NEAR,
//! Ethereum, Solana and postquant contracts, as an ink! 5 contract for
//! pallet-contracts (substrate-contracts-node locally; any parachain with
//! pallet-contracts on a public network).
//!
//! Two public sets of STARK-curve keys, MINERS and VALIDATORS. Every action
//! is an LSAG ring signature over members of one role. pallet-contracts has
//! no STARK-curve host function, so the curve math runs in wasm, metered in
//! weight (ref_time picoseconds + proof_size bytes). lsag.rs, fp.rs and
//! poseidon.rs ARE near/contract's files (build.rs), so a signature made by
//! core/signer.py verifies here unchanged.
//!
//!   submit(ring, work, sig)            key image under (subnet, epoch) = TAG
//!   score(ring, tag, score, sig)       key image under (subnet, epoch, tag)
//!                                      = nullifier, one score per validator
//!   settle(epoch, tag)                 anyone, epoch closed, once per tag:
//!                                      Zuma consensus + clipped rank
//!   claim(epoch, ring, recipient, sig) same scope as submit, so the same
//!                                      image: credits emission * rank /
//!                                      rank_total (every tag settled)
//!
//! Chain-specific bits, and nothing else differs from the NEAR contract:
//!   subnet    = felt(blake2_256(contract AccountId)), top 5 bits cleared
//!   epoch     = block_number / tempo
//!   recipient = an AccountId; msg = felt(blake2_256(recipient)), same rule
//!   felts     = [u8; 32] big-endian, rejected if >= p (never reduced)
//!   errors    = a typed `Error`; an Err return reverts the call's state
//!
//! No message checks the caller except owner admin, so a relayer can post on
//! a member's behalf (the signer of an extrinsic is public).
#![cfg_attr(not(feature = "std"), no_std, no_main)]

extern crate alloc;

// near/contract/src/{fp,lsag,poseidon}.rs with alloc imports (see build.rs)
// field code: every bound is proven by construction and differentially
// tested in near/contract (see WHITEPAPER §5), so wrapping ops are intended
#[allow(clippy::all, clippy::arithmetic_side_effects, clippy::cast_possible_truncation,
        clippy::cast_possible_wrap, clippy::cast_sign_loss, dead_code)]
pub mod fp {
    include!(concat!(env!("OUT_DIR"), "/fp.rs"));
}
// field code: every bound is proven by construction and differentially
// tested in near/contract (see WHITEPAPER §5), so wrapping ops are intended
#[allow(clippy::all, clippy::arithmetic_side_effects, clippy::cast_possible_truncation,
        clippy::cast_possible_wrap, clippy::cast_sign_loss, dead_code)]
pub mod lsag {
    include!(concat!(env!("OUT_DIR"), "/lsag.rs"));
}
// field code: every bound is proven by construction and differentially
// tested in near/contract (see WHITEPAPER §5), so wrapping ops are intended
#[allow(clippy::all, clippy::arithmetic_side_effects, clippy::cast_possible_truncation,
        clippy::cast_possible_wrap, clippy::cast_sign_loss, dead_code)]
pub mod poseidon {
    include!(concat!(env!("OUT_DIR"), "/poseidon.rs"));
}

pub const ROLE_MINER: u8 = 1;
pub const ROLE_VALIDATOR: u8 = 2;
/// Weight-bound, not a protocol choice: see MAX_RING in the adapter and
/// data/substrate_gas.json for the measured ref_time per ring size.
pub const MAX_RING: u32 = 64;
pub const MAX_SCORE: u8 = 100;

/// ceil(kappa * v), kappa = 1/2 (core/yuma.py `need`).
pub fn need(v: u32) -> u32 {
    v.div_ceil(2)
}

/// (consensus, rank) from a sparse histogram: core/yuma.py `settle`, the
/// same walk as every other chain.
pub fn yuma_settle(hist: &[(u8, u32)], validators: u32, score_sum: u128) -> (u8, u128) {
    let n: u32 = hist.iter().fold(0u32, |a, h| a.saturating_add(h.1));
    let k = need(validators.max(n));
    if k == 0 || n < k {
        return (0, 0);
    }
    // votes are u32 and scores <= 100, so no sum below can overflow a
    // u128; saturating ops only keep the lints honest
    let (mut above, mut mass) = (0u128, 0u128);
    for &(s, c) in hist.iter().rev() {
        above = above.saturating_add(u128::from(c));
        mass = mass.saturating_add(u128::from(c).saturating_mul(u128::from(s)));
        if above >= u128::from(k) {
            // scores >= s clip to s; scores below s are kept as they are
            return (s, score_sum.saturating_sub(mass).saturating_add(u128::from(s).saturating_mul(above)));
        }
    }
    (0, 0) // unreachable: the lowest bucket covers all n >= k votes
}

#[ink::contract]
pub mod ztao {
    use super::lsag::{self, Pt, Sig};
    use super::{yuma_settle, MAX_RING, MAX_SCORE, ROLE_MINER, ROLE_VALIDATOR};
    use alloc::vec::Vec;
    use ink::env::hash::{Blake2x256, HashOutput};
    use ink::storage::Mapping;
    use starknet_types_core::felt::Felt;

    /// A felt, 32 bytes big-endian.
    pub type F = [u8; 32];

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    #[cfg_attr(feature = "std", derive(ink::storage::traits::StorageLayout))]
    pub struct Point {
        pub x: F,
        pub y: F,
    }

    #[derive(Clone, Debug, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    pub struct Signature {
        pub c0: F,
        pub s: Vec<F>,
        pub image: Point,
        /// Optional Hp(scope) hint from the free `hash_point` view. Checked,
        /// never trusted: a wrong hint only costs the slow square root.
        pub hp: Option<Point>,
    }

    /// Zuma state of one (epoch, tag).
    #[derive(Clone, Debug, Default, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    #[cfg_attr(feature = "std", derive(ink::storage::traits::StorageLayout))]
    pub struct YumaTag {
        /// (score, validators who gave it), ascending by score
        pub hist: Vec<(u8, u32)>,
        pub consensus: u8,
        pub rank: u128,
        pub settled: bool,
    }

    /// Zuma state of one epoch.
    #[derive(Clone, Debug, Default, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    #[cfg_attr(feature = "std", derive(ink::storage::traits::StorageLayout))]
    pub struct YumaEpoch {
        /// validator count when the epoch's first tag was submitted
        pub vset: u32,
        pub rank_total: u128,
        pub nsettled: u32,
    }

    #[derive(Clone, Debug, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    pub struct Params {
        pub owner: AccountId,
        pub tempo: u64,
        pub min_ring: u32,
        pub max_ring: u32,
        pub emission: u128,
        pub open: bool,
        pub subnet: F,
    }

    #[derive(Clone, Debug, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    pub struct TagRow {
        pub tag: F,
        pub work: F,
        pub score_sum: u128,
        pub scores: u32,
        pub claimed: bool,
        pub consensus: u8,
        pub rank: u128,
        pub settled: bool,
    }

    #[derive(Clone, Debug, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    pub struct Tally {
        pub epoch: u64,
        pub total: u128,
        pub validators: u32,
        pub rank_total: u128,
        pub settled: u32,
        pub tags: Vec<TagRow>,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    pub enum Error {
        BadParams,
        NotAFelt,
        NotOnCurve,
        BadRole,
        RegistryClosed,
        AlreadyRegistered,
        OnlyOwner,
        RingBelowMin,
        RingAboveMax,
        NonMember,
        RingNotSorted,
        BadSignature,
        AlreadySubmitted,
        NoSuchTag,
        ScoreRange,
        AlreadyScored,
        EpochOpen,
        AlreadySettled,
        EpochNotSettled,
        AlreadyClaimed,
        Overflow,
    }

    pub type Result<T> = core::result::Result<T, Error>;

    #[ink(event)]
    pub struct Registered {
        role: u8,
        key_id: F,
        index: u32,
    }

    #[ink(event)]
    pub struct Submitted {
        epoch: u64,
        tag: F,
        work: F,
        ring_size: u32,
    }

    #[ink(event)]
    pub struct Scored {
        epoch: u64,
        tag: F,
        score: u8,
        nullifier: F,
        ring_size: u32,
    }

    #[ink(event)]
    pub struct Settled {
        epoch: u64,
        tag: F,
        consensus: u8,
        rank: u128,
    }

    #[ink(event)]
    pub struct Claimed {
        epoch: u64,
        tag: F,
        recipient: AccountId,
        amount: u128,
    }

    #[ink(storage)]
    pub struct ZTao {
        owner: AccountId,
        open: bool,
        tempo: u64,
        min_ring: u32,
        emission: u128,
        role: Mapping<F, u8>,                  // key_id -> role
        count: Mapping<u8, u32>,               // role -> members
        member: Mapping<(u8, u32), Point>,     // (role, index) -> key
        work: Mapping<(u64, F), F>,            // (epoch, tag) -> work
        ntags: Mapping<u64, u32>,
        tag_at: Mapping<(u64, u32), F>,
        tally: Mapping<(u64, F), (u128, u32)>, // score sum, scores
        total: Mapping<u64, u128>,
        spent: Mapping<F, ()>,                 // validator nullifiers
        claimed: Mapping<(u64, F), ()>,
        credit: Mapping<AccountId, u128>,
        ytag: Mapping<(u64, F), YumaTag>,
        yepoch: Mapping<u64, YumaEpoch>,
    }

    fn felt(b: &F) -> Result<Felt> {
        let f = Felt::from_bytes_be(b);
        if &f.to_bytes_be() != b {
            return Err(Error::NotAFelt); // was >= PRIME
        }
        Ok(f)
    }

    fn pt(p: &Point) -> Result<Pt> {
        Ok(Pt { x: felt(&p.x)?, y: felt(&p.y)? })
    }

    /// blake2_256 with the top 5 bits cleared: always < 2^251 < PRIME.
    fn bytes_felt(data: &[u8]) -> Felt {
        let mut out = <Blake2x256 as HashOutput>::Type::default();
        ink::env::hash_bytes::<Blake2x256>(data, &mut out);
        out[0] &= 0x07;
        Felt::from_bytes_be(&out)
    }

    fn len32<T>(v: &[T]) -> u32 {
        u32::try_from(v.len()).unwrap_or(u32::MAX)
    }

    fn add(a: u128, b: u128) -> Result<u128> {
        a.checked_add(b).ok_or(Error::Overflow)
    }

    impl ZTao {
        #[ink(constructor)]
        pub fn new(tempo: u64, min_ring: u32, emission: u128) -> Self {
            // a constructor can not return our Error without a Result
            // wrapper the clients would have to special-case: trap instead
            assert!(tempo > 0 && min_ring > 0 && min_ring <= MAX_RING, "bad params");
            Self {
                owner: Self::env().caller(),
                open: false,
                tempo,
                min_ring,
                emission,
                role: Mapping::default(),
                count: Mapping::default(),
                member: Mapping::default(),
                work: Mapping::default(),
                ntags: Mapping::default(),
                tag_at: Mapping::default(),
                tally: Mapping::default(),
                total: Mapping::default(),
                spent: Mapping::default(),
                claimed: Mapping::default(),
                credit: Mapping::default(),
                ytag: Mapping::default(),
                yepoch: Mapping::default(),
            }
        }

        // ---- registry ------------------------------------------------------

        #[ink(message)]
        pub fn register(&mut self, role: u8, key: Point) -> Result<u32> {
            if role != ROLE_MINER && role != ROLE_VALIDATOR {
                return Err(Error::BadRole);
            }
            if !self.open && self.env().caller() != self.owner {
                return Err(Error::RegistryClosed);
            }
            let q = pt(&key)?;
            if !lsag::on_curve(&q) {
                return Err(Error::NotOnCurve);
            }
            let id = lsag::key_id(&q).to_bytes_be();
            if self.role.contains(id) {
                return Err(Error::AlreadyRegistered);
            }
            self.role.insert(id, &role);
            let index = self.count.get(role).unwrap_or(0);
            self.member.insert((role, index), &key);
            self.count.insert(role, &index.checked_add(1).ok_or(Error::Overflow)?);
            self.env().emit_event(Registered { role, key_id: id, index });
            Ok(index)
        }

        #[ink(message)]
        pub fn set_open(&mut self, open: bool) -> Result<()> {
            self.only_owner()?;
            self.open = open;
            Ok(())
        }

        #[ink(message)]
        pub fn transfer_ownership(&mut self, owner: AccountId) -> Result<()> {
            self.only_owner()?;
            self.owner = owner;
            Ok(())
        }

        // ---- anonymous actions ---------------------------------------------

        #[ink(message)]
        pub fn submit(&mut self, ring: Vec<Point>, work: F, sig: Signature) -> Result<F> {
            let ring = self.check_ring(&ring, ROLE_MINER)?;
            let epoch = self.epoch();
            let w = felt(&work)?;
            self.check_sig(&ring, &self.scope_mine(epoch), &w, &sig)?;
            let tag = lsag::key_id(&pt(&sig.image)?).to_bytes_be();
            if self.work.contains((epoch, tag)) {
                return Err(Error::AlreadySubmitted);
            }
            self.work.insert((epoch, tag), &work);
            let n = self.ntags.get(epoch).unwrap_or(0);
            if n == 0 {
                // snapshot V: registrations after this cannot move the payouts
                let vset = self.count.get(ROLE_VALIDATOR).unwrap_or(0);
                self.yepoch.insert(epoch, &YumaEpoch { vset, ..Default::default() });
            }
            self.tag_at.insert((epoch, n), &tag);
            self.ntags.insert(epoch, &n.checked_add(1).ok_or(Error::Overflow)?);
            self.env().emit_event(Submitted { epoch, tag, work, ring_size: len32(&ring) });
            Ok(tag)
        }

        #[ink(message)]
        pub fn score(&mut self, ring: Vec<Point>, tag: F, score: u8, sig: Signature) -> Result<F> {
            if score > MAX_SCORE {
                return Err(Error::ScoreRange);
            }
            let ring = self.check_ring(&ring, ROLE_VALIDATOR)?;
            let epoch = self.epoch();
            let t = felt(&tag)?;
            if !self.work.contains((epoch, tag)) {
                return Err(Error::NoSuchTag);
            }
            self.check_sig(&ring, &self.scope_score(epoch, &t), &Felt::from(score), &sig)?;
            let nullifier = lsag::key_id(&pt(&sig.image)?).to_bytes_be();
            if self.spent.contains(nullifier) {
                return Err(Error::AlreadyScored);
            }
            self.spent.insert(nullifier, &());
            let (s, n) = self.tally.get((epoch, tag)).unwrap_or((0, 0));
            self.tally.insert((epoch, tag), &(add(s, score as u128)?, n.checked_add(1).ok_or(Error::Overflow)?));
            let total = self.total.get(epoch).unwrap_or(0);
            self.total.insert(epoch, &add(total, score as u128)?);
            let mut y = self.ytag.get((epoch, tag)).unwrap_or_default();
            match y.hist.binary_search_by_key(&score, |h| h.0) {
                Ok(i) => y.hist[i].1 = y.hist[i].1.checked_add(1).ok_or(Error::Overflow)?,
                Err(i) => y.hist.insert(i, (score, 1)),
            }
            self.ytag.insert((epoch, tag), &y);
            self.env().emit_event(Scored { epoch, tag, score, nullifier, ring_size: len32(&ring) });
            Ok(nullifier)
        }

        /// Zuma consensus for one tag of a closed epoch. Anyone, once per tag.
        #[ink(message)]
        pub fn settle(&mut self, epoch: u64, tag: F) -> Result<(u8, u128)> {
            if epoch >= self.epoch() {
                return Err(Error::EpochOpen);
            }
            if !self.work.contains((epoch, tag)) {
                return Err(Error::NoSuchTag);
            }
            let mut y = self.ytag.get((epoch, tag)).unwrap_or_default();
            if y.settled {
                return Err(Error::AlreadySettled);
            }
            let mut ye = self.yepoch.get(epoch).unwrap_or_default();
            let sum = self.tally.get((epoch, tag)).map(|t| t.0).unwrap_or(0);
            let (c, r) = yuma_settle(&y.hist, ye.vset, sum);
            y.consensus = c;
            y.rank = r;
            y.settled = true;
            self.ytag.insert((epoch, tag), &y);
            ye.rank_total = add(ye.rank_total, r)?;
            ye.nsettled = ye.nsettled.checked_add(1).ok_or(Error::Overflow)?;
            self.yepoch.insert(epoch, &ye);
            self.env().emit_event(Settled { epoch, tag, consensus: c, rank: r });
            Ok((c, r))
        }

        #[ink(message)]
        pub fn claim(&mut self, epoch: u64, ring: Vec<Point>, recipient: AccountId, sig: Signature) -> Result<u128> {
            if epoch >= self.epoch() {
                return Err(Error::EpochOpen);
            }
            let ring = self.check_ring(&ring, ROLE_MINER)?;
            let msg = bytes_felt(recipient.as_ref());
            self.check_sig(&ring, &self.scope_mine(epoch), &msg, &sig)?;
            let tag = lsag::key_id(&pt(&sig.image)?).to_bytes_be();
            if !self.work.contains((epoch, tag)) {
                return Err(Error::NoSuchTag);
            }
            let ye = self.yepoch.get(epoch).unwrap_or_default();
            if ye.nsettled != self.ntags.get(epoch).unwrap_or(0) {
                return Err(Error::EpochNotSettled);
            }
            if self.claimed.contains((epoch, tag)) {
                return Err(Error::AlreadyClaimed);
            }
            self.claimed.insert((epoch, tag), &());
            let total = ye.rank_total;
            let rank = self.ytag.get((epoch, tag)).map(|y| y.rank).unwrap_or(0);
            // emission * rank / rank_total without overflow: rank <= total,
            // so split emission = q*total + r -> q*rank + r*rank/total
            let amount = match (self.emission.checked_div(total), self.emission.checked_rem(total)) {
                (Some(q), Some(r)) => add(
                    q.checked_mul(rank).ok_or(Error::Overflow)?,
                    r.checked_mul(rank).ok_or(Error::Overflow)?.checked_div(total).unwrap_or(0),
                )?,
                _ => 0, // total == 0: nothing was ranked
            };
            let c = self.credit.get(recipient).unwrap_or(0);
            self.credit.insert(recipient, &add(c, amount)?);
            self.env().emit_event(Claimed { epoch, tag, recipient, amount });
            Ok(amount)
        }

        // ---- views ---------------------------------------------------------

        #[ink(message)]
        pub fn epoch(&self) -> u64 {
            u64::from(self.env().block_number()).checked_div(self.tempo).unwrap_or(0)
        }

        #[ink(message)]
        pub fn block(&self) -> u64 {
            u64::from(self.env().block_number())
        }

        #[ink(message)]
        pub fn subnet(&self) -> F {
            self.subnet_f().to_bytes_be()
        }

        #[ink(message)]
        pub fn mine_scope(&self, epoch: u64) -> F {
            self.scope_mine(epoch).to_bytes_be()
        }

        #[ink(message)]
        pub fn score_scope(&self, epoch: u64, tag: F) -> Result<F> {
            Ok(self.scope_score(epoch, &felt(&tag)?).to_bytes_be())
        }

        /// The felt a recipient signs as its claim message.
        #[ink(message)]
        pub fn recipient_felt(&self, recipient: AccountId) -> F {
            bytes_felt(recipient.as_ref()).to_bytes_be()
        }

        #[ink(message)]
        pub fn role_of(&self, key: Point) -> Result<u8> {
            Ok(self.role.get(lsag::key_id(&pt(&key)?).to_bytes_be()).unwrap_or(0))
        }

        #[ink(message)]
        pub fn count(&self, role: u8) -> u32 {
            self.count.get(role).unwrap_or(0)
        }

        /// One page of a member set (a whole set may not fit one return buffer).
        #[ink(message)]
        pub fn members(&self, role: u8, start: u32, limit: u32) -> Vec<Point> {
            let end = self.count(role).min(start.saturating_add(limit.min(128)));
            (start..end).filter_map(|i| self.member.get((role, i))).collect()
        }

        #[ink(message)]
        pub fn yuma(&self, epoch: u64, tag: F) -> YumaTag {
            self.ytag.get((epoch, tag)).unwrap_or_default()
        }

        #[ink(message)]
        pub fn yuma_epoch(&self, epoch: u64) -> YumaEpoch {
            self.yepoch.get(epoch).unwrap_or_default()
        }

        #[ink(message)]
        pub fn tally(&self, epoch: u64) -> Tally {
            let ye = self.yepoch.get(epoch).unwrap_or_default();
            let tags = (0..self.ntags.get(epoch).unwrap_or(0))
                .filter_map(|i| self.tag_at.get((epoch, i)))
                .map(|tag| {
                    let (s, n) = self.tally.get((epoch, tag)).unwrap_or((0, 0));
                    let y = self.ytag.get((epoch, tag)).unwrap_or_default();
                    TagRow {
                        tag,
                        work: self.work.get((epoch, tag)).unwrap_or_default(),
                        score_sum: s,
                        scores: n,
                        claimed: self.claimed.contains((epoch, tag)),
                        consensus: y.consensus,
                        rank: y.rank,
                        settled: y.settled,
                    }
                })
                .collect();
            Tally {
                epoch,
                total: self.total.get(epoch).unwrap_or(0),
                validators: ye.vset,
                rank_total: ye.rank_total,
                settled: ye.nsettled,
                tags,
            }
        }

        #[ink(message)]
        pub fn earned(&self, who: AccountId) -> u128 {
            self.credit.get(who).unwrap_or(0)
        }

        #[ink(message)]
        pub fn params(&self) -> Params {
            Params {
                owner: self.owner,
                tempo: self.tempo,
                min_ring: self.min_ring,
                max_ring: MAX_RING,
                emission: self.emission,
                open: self.open,
                subnet: self.subnet(),
            }
        }

        /// Check a signature without a state change (clients, tests).
        #[ink(message)]
        pub fn verify(&self, ring: Vec<Point>, scope: F, msg: F, sig: Signature) -> Result<bool> {
            let ring = ring.iter().map(pt).collect::<Result<Vec<_>>>()?;
            Ok(self.check_sig(&ring, &felt(&scope)?, &felt(&msg)?, &sig).is_ok())
        }

        /// Hp(scope), the point key images are taken over. Pass it back as
        /// `sig.hp` to skip the on-chain square root.
        #[ink(message)]
        pub fn hash_point(&self, scope: F) -> Result<Point> {
            let p = lsag::hash_to_point(&felt(&scope)?);
            Ok(Point { x: p.x.to_bytes_be(), y: p.y.to_bytes_be() })
        }

        // ---- internals -----------------------------------------------------

        fn only_owner(&self) -> Result<()> {
            if self.env().caller() != self.owner {
                return Err(Error::OnlyOwner);
            }
            Ok(())
        }

        fn subnet_f(&self) -> Felt {
            bytes_felt(self.env().account_id().as_ref())
        }

        fn scope_mine(&self, epoch: u64) -> Felt {
            lsag::h(&[lsag::short("zuma-mine"), self.subnet_f(), Felt::from(epoch)])
        }

        fn scope_score(&self, epoch: u64, tag: &Felt) -> Felt {
            lsag::h(&[lsag::short("zuma-score"), self.subnet_f(), Felt::from(epoch), *tag])
        }

        fn check_sig(&self, ring: &[Pt], scope: &Felt, msg: &Felt, s: &Signature) -> Result<()> {
            let hint = match &s.hp {
                Some(p) => Some(pt(p)?),
                None => None,
            };
            let sig = Sig {
                c0: felt(&s.c0)?,
                s: s.s.iter().map(felt).collect::<Result<Vec<_>>>()?,
                image: pt(&s.image)?,
            };
            if lsag::verify_hinted(ring, scope, msg, &sig, hint.as_ref()) {
                Ok(())
            } else {
                Err(Error::BadSignature)
            }
        }

        /// Ring = strictly increasing key ids, all registered with `role`.
        fn check_ring(&self, ring: &[Point], role: u8) -> Result<Vec<Pt>> {
            let n = len32(ring);
            if n < self.min_ring {
                return Err(Error::RingBelowMin);
            }
            if n > MAX_RING {
                return Err(Error::RingAboveMax);
            }
            let mut prev: Option<F> = None;
            let mut out = Vec::with_capacity(ring.len());
            for p in ring {
                let q = pt(p)?;
                let id = lsag::key_id(&q).to_bytes_be();
                if self.role.get(id) != Some(role) {
                    return Err(Error::NonMember);
                }
                if prev.is_some_and(|pb| id <= pb) {
                    return Err(Error::RingNotSorted);
                }
                prev = Some(id);
                out.push(q);
            }
            Ok(out)
        }
    }
}

#[cfg(test)]
mod tests;
