// Zuma — zk Yuma consensus. Yuma, but nobody knows who is who.
//
// Miners and validators are two public sets of STARK-curve keys. Being IN a
// set is public (that is what makes the subnet permissioned); every ACTION
// is a ring signature (see lsag.cairo), so it proves "one of the miners" or
// "one of the validators" and nothing more. That breaks the usual collusion
// loops of a subnet:
//
//   - a validator can't favour a friend: miners show up under a fresh TAG
//     every round, unlinkable to their key and to last round's tag;
//   - a miner can't court or bribe a validator: it never learns who scored it;
//   - validators can't copy each other: weights are sealed until every
//     validator has locked in (commit-reveal), and a seal is bound to its
//     author's nullifier so it can't be lifted either.
//
// Each round (epoch) has four stages:
//
//   0 SUBMIT    a miner ring-signs `work` (a commitment to its output) in
//               scope H('zuma-mine', contract, epoch). Key image -> TAG:
//               one submission per miner per round.
//   1 COMMIT    a validator ring-signs its seal in scope
//               H('zuma-vote', contract, epoch). Key image -> NULLIFIER: one
//               vote per validator per round. seal = H('zuma', contract,
//               epoch, nullifier, salt, w_0..w_n), one weight per tag.
//   2 REVEAL    the voter (or anyone it hands salt+weights to) opens the seal.
//   3 FINALIZE  anyone runs Yuma over the opened weights; the round rolls.
//
// Afterwards a miner (validator) re-signs the same scope with msg=recipient.
// Same key + same scope = same key image, so it proves "I am that tag (that
// nullifier)" and credits its share to any address. Credits are paper points
// on public books; no token moves.
//
// Yuma (fixed point, ONE = 1e9) over the round's voters and tags:
//   w_ij = voter i's weights normalized to ONE; s_i = i's share of voting stake
//   c_j  = stake-weighted median of w_.j (kappa 1/2)    consensus
//   I_j  = sum_i s_i min(w_ij, c_j), normalized         miner incentive
//   B_ij = s_i min(w_ij, c_j) / sum_i (same)            bonds (this round)
//   D_i  = sum_j B_ij I_j, normalized                   validator dividends
// Emission splits half to miners by I, half to validators by D.
//
// Stake without identity: a validator key is registered with a stake and a
// ring must be ONE stake tier (all members equal stake). The vote weighs
// that stake, so the stake is public but the voter is any of the tier.
// Bonds are per round, not an EMA: an EMA needs one identity across rounds,
// which is exactly the link zuma refuses to make.
//
// Limits, honestly: the anonymity set is the ring the signer picked (>=
// min_ring) — a ring of 3 hides among 3. Nobody checks the caller, so post
// through a relayer: the tx sender is public. And a miner can still
// whisper its tag (or watermark its work) to a validator off-chain; zuma
// makes collusion unverifiable on-chain, it can't make people not talk.

pub mod lsag;

use core::poseidon::poseidon_hash_span;
use starknet::ContractAddress;
use crate::lsag::{Point, Signature};

pub const ONE: u128 = 1_000_000_000;
pub const ROLE_MINER: u8 = 1;
pub const ROLE_VALIDATOR: u8 = 2;
pub const MAX_RING: u32 = 32;
pub const MAX_TAGS: u32 = 32;
pub const MAX_VOTERS: u32 = 16;

pub const PHASE_SUBMIT: u8 = 0;
pub const PHASE_COMMIT: u8 = 1;
pub const PHASE_REVEAL: u8 = 2;
pub const PHASE_FINALIZE: u8 = 3;

pub fn mine_scope_of(contract: ContractAddress, epoch: u64) -> felt252 {
    poseidon_hash_span(array!['zuma-mine', contract.into(), epoch.into()].span())
}

pub fn vote_scope_of(contract: ContractAddress, epoch: u64) -> felt252 {
    poseidon_hash_span(array!['zuma-vote', contract.into(), epoch.into()].span())
}

/// The seal a voter commits: weights (one per tag, in tag order) hidden under
/// `salt`, bound to the contract, round and the voter's nullifier.
pub fn seal_of(
    contract: ContractAddress, epoch: u64, nullifier: felt252, weights: Span<u16>, salt: felt252,
) -> felt252 {
    let mut data: Array<felt252> = array!['zuma', contract.into(), epoch.into(), nullifier, salt];
    for w in weights {
        data.append((*w).into());
    };
    poseidon_hash_span(data.span())
}

#[starknet::interface]
pub trait IZuma<T> {
    // registry: owner, or anyone while open; only between rounds' activity
    fn register(ref self: T, role: u8, key: Point, stake: u128);
    fn set_open(ref self: T, open: bool);
    // anonymous actions (post via a relayer)
    fn submit(ref self: T, ring: Array<Point>, work: felt252, sig: Signature);
    fn commit(ref self: T, ring: Array<Point>, seal: felt252, sig: Signature);
    fn reveal(ref self: T, nullifier: felt252, weights: Span<u16>, salt: felt252);
    fn finalize(ref self: T);
    fn claim_miner(ref self: T, epoch: u64, ring: Array<Point>, recipient: ContractAddress, sig: Signature);
    fn claim_validator(
        ref self: T, epoch: u64, ring: Array<Point>, recipient: ContractAddress, sig: Signature,
    );
    // reads
    fn phase(self: @T) -> u8;
    fn epoch(self: @T) -> u64;
    fn params(self: @T) -> (ContractAddress, u32, u64, u64, u64, u128, bool);
    fn mine_scope(self: @T, epoch: u64) -> felt252;
    fn vote_scope(self: @T, epoch: u64) -> felt252;
    fn seal(self: @T, nullifier: felt252, weights: Span<u16>, salt: felt252) -> felt252;
    fn member_count(self: @T, role: u8) -> u32;
    fn member(self: @T, role: u8, index: u32) -> Point;
    fn role_of(self: @T, key: Point) -> u8;
    fn stake_of(self: @T, key: Point) -> u128;
    fn tags(self: @T, epoch: u64) -> Array<felt252>;
    fn work_of(self: @T, epoch: u64, tag: felt252) -> felt252;
    fn voters(self: @T, epoch: u64) -> Array<felt252>;
    fn sealed(self: @T) -> u32;
    fn revealed(self: @T) -> u32;
    fn weights_of(self: @T, epoch: u64, nullifier: felt252) -> Array<u16>;
    fn consensus(self: @T, epoch: u64) -> Array<u128>;
    fn incentive(self: @T, epoch: u64) -> Array<u128>;
    fn dividends(self: @T, epoch: u64) -> Array<u128>;
    fn miner_reward(self: @T, epoch: u64, tag: felt252) -> u128;
    fn validator_reward(self: @T, epoch: u64, nullifier: felt252) -> u128;
    fn claimed(self: @T, epoch: u64, id: felt252) -> bool;
    fn earned(self: @T, who: ContractAddress) -> u128;
}

#[starknet::contract]
pub mod Zuma {
    use core::num::traits::Zero;
    use starknet::{ContractAddress, get_block_timestamp, get_caller_address, get_contract_address};
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };
    use crate::lsag;
    use crate::lsag::{Point, Signature};
    use super::{
        MAX_RING, MAX_TAGS, MAX_VOTERS, ONE, PHASE_COMMIT, PHASE_FINALIZE, PHASE_REVEAL,
        PHASE_SUBMIT, ROLE_MINER, ROLE_VALIDATOR, mine_scope_of, seal_of, vote_scope_of,
    };

    #[storage]
    struct Storage {
        owner: ContractAddress,
        open: bool,
        min_ring: u32,
        submit_secs: u64,
        commit_secs: u64,
        reveal_secs: u64,
        emission: u128,
        // the two public sets: key_id -> role / stake, role -> keys
        role: Map<felt252, u8>,
        stake: Map<felt252, u128>,
        count: Map<u8, u32>,
        members: Map<(u8, u32), Point>,
        // the running round
        epoch: u64,
        epoch_start: u64,
        // miners: tag slots per round
        ntags: Map<u64, u32>,
        tag_at: Map<(u64, u32), felt252>,
        tag_slot: Map<(u64, felt252), u32>, // slot + 1, 0 = no such tag
        work: Map<(u64, felt252), felt252>,
        // voters: nullifier slots per round
        nvoters: Map<u64, u32>,
        nrevealed: Map<u64, u32>,
        voter_at: Map<(u64, u32), felt252>,
        voter_slot: Map<(u64, felt252), u32>, // slot + 1
        voter_stake: Map<(u64, u32), u128>,
        seals: Map<(u64, u32), felt252>,
        opened: Map<(u64, u32), bool>,
        weight: Map<(u64, u32, u32), u16>,
        // results per round
        cons: Map<(u64, u32), u128>,
        incent: Map<(u64, u32), u128>,
        divs: Map<(u64, u32), u128>,
        miner_amt: Map<(u64, u32), u128>,
        voter_amt: Map<(u64, u32), u128>,
        is_claimed: Map<(u64, felt252), bool>,
        credit: Map<ContractAddress, u128>,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Registered: Registered,
        Submitted: Submitted,
        Sealed: Sealed,
        Revealed: Revealed,
        Finalized: Finalized,
        Claimed: Claimed,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Registered {
        #[key]
        pub role: u8,
        pub key_id: felt252,
        pub index: u32,
        pub stake: u128,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Submitted {
        #[key]
        pub epoch: u64,
        pub tag: felt252,
        pub work: felt252,
        pub ring_size: u32,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Sealed {
        #[key]
        pub epoch: u64,
        pub nullifier: felt252,
        pub stake: u128,
        pub ring_size: u32,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Revealed {
        #[key]
        pub epoch: u64,
        pub nullifier: felt252,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Finalized {
        #[key]
        pub epoch: u64,
        pub miners: u32,
        pub voters: u32,
        pub emission: u128,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Claimed {
        #[key]
        pub epoch: u64,
        pub role: u8,
        pub id: felt252,
        pub recipient: ContractAddress,
        pub amount: u128,
    }

    #[constructor]
    fn constructor(
        ref self: ContractState,
        owner: ContractAddress,
        min_ring: u32,
        submit_secs: u64,
        commit_secs: u64,
        reveal_secs: u64,
        emission: u128,
    ) {
        // explicit owner: deploys go through the UDC, so the caller is not the deployer
        assert(!owner.is_zero(), 'zero owner');
        assert(min_ring > 0 && min_ring <= MAX_RING, 'min_ring 1..32');
        self.owner.write(owner);
        self.min_ring.write(min_ring);
        self.submit_secs.write(submit_secs);
        self.commit_secs.write(commit_secs);
        self.reveal_secs.write(reveal_secs);
        self.emission.write(emission);
        self.epoch_start.write(get_block_timestamp());
    }

    #[generate_trait]
    impl Internal of InternalTrait {
        /// Ring = strictly increasing key ids, every one registered with
        /// `role` and all the same stake (one tier). Returns that stake.
        fn check_ring(self: @ContractState, ring: Span<Point>, role: u8) -> u128 {
            let n = ring.len();
            assert(n >= self.min_ring.read(), 'ring below min_ring');
            assert(n <= MAX_RING, 'ring above MAX_RING');
            let mut prev: u256 = 0;
            let mut tier: u128 = 0;
            let mut first = true;
            for p in ring {
                let id = lsag::key_id(*p);
                assert(self.role.read(id) == role, 'ring has a non-member');
                let st = self.stake.read(id);
                let idu: u256 = id.into();
                assert(first || idu > prev, 'ring not sorted/unique');
                assert(first || st == tier, 'ring mixes stake tiers');
                prev = idu;
                tier = st;
                first = false;
            };
            tier
        }

        // each stage closes on its clock, or early once everyone is in
        fn phase_now(self: @ContractState) -> u8 {
            let e = self.epoch.read();
            let now = get_block_timestamp();
            let t0 = self.epoch_start.read();
            let t1 = t0 + self.submit_secs.read();
            let t2 = t1 + self.commit_secs.read();
            let t3 = t2 + self.reveal_secs.read();
            let miners = self.count.read(ROLE_MINER);
            let vals = self.count.read(ROLE_VALIDATOR);
            let tags = self.ntags.read(e);
            let voters = self.nvoters.read(e);
            let submit_done = now >= t1 || (miners > 0 && tags == miners);
            if !submit_done {
                return PHASE_SUBMIT;
            }
            if !(now >= t2 || (vals > 0 && voters == vals)) {
                return PHASE_COMMIT;
            }
            if now >= t3 || (voters > 0 && self.nrevealed.read(e) == voters) {
                return PHASE_FINALIZE;
            }
            PHASE_REVEAL
        }

        fn credit(ref self: ContractState, epoch: u64, role: u8, id: felt252, to: ContractAddress, amount: u128) {
            assert(!self.is_claimed.read((epoch, id)), 'already claimed');
            self.is_claimed.write((epoch, id), true);
            self.credit.write(to, self.credit.read(to) + amount);
            self.emit(Claimed { epoch, role, id, recipient: to, amount });
        }
    }

    #[abi(embed_v0)]
    impl ZumaImpl of super::IZuma<ContractState> {
        fn register(ref self: ContractState, role: u8, key: Point, stake: u128) {
            assert(role == ROLE_MINER || role == ROLE_VALIDATOR, 'role is 1 or 2');
            assert(self.open.read() || get_caller_address() == self.owner.read(), 'registry closed');
            // a new member would move the "everyone is in" goalposts mid-round
            let e = self.epoch.read();
            assert(self.ntags.read(e) == 0 && self.nvoters.read(e) == 0, 'round already running');
            assert(lsag::to_ec(key).is_some(), 'key not on curve');
            assert(role == ROLE_MINER || stake > 0, 'validator needs stake');
            let id = lsag::key_id(key);
            assert(self.role.read(id) == 0, 'key already registered');
            self.role.write(id, role);
            let st = if role == ROLE_MINER { 0 } else { stake };
            self.stake.write(id, st);
            let i = self.count.read(role);
            self.members.write((role, i), key);
            self.count.write(role, i + 1);
            self.emit(Registered { role, key_id: id, index: i, stake: st });
        }

        fn set_open(ref self: ContractState, open: bool) {
            assert(get_caller_address() == self.owner.read(), 'only owner');
            self.open.write(open);
        }

        fn submit(ref self: ContractState, ring: Array<Point>, work: felt252, sig: Signature) {
            assert(self.phase_now() == PHASE_SUBMIT, 'submit window closed');
            let ring = ring.span();
            self.check_ring(ring, ROLE_MINER);
            let e = self.epoch.read();
            assert(lsag::verify(ring, mine_scope_of(get_contract_address(), e), work, sig), 'bad ring signature');
            let tag = lsag::key_id(sig.image);
            assert(self.tag_slot.read((e, tag)) == 0, 'already submitted this round');
            let i = self.ntags.read(e);
            assert(i < MAX_TAGS, 'round full');
            self.tag_at.write((e, i), tag);
            self.tag_slot.write((e, tag), i + 1);
            self.work.write((e, tag), work);
            self.ntags.write(e, i + 1);
            self.emit(Submitted { epoch: e, tag, work, ring_size: ring.len() });
        }

        fn commit(ref self: ContractState, ring: Array<Point>, seal: felt252, sig: Signature) {
            assert(self.phase_now() == PHASE_COMMIT, 'not commit phase');
            assert(seal != 0, 'empty seal');
            let e = self.epoch.read();
            assert(self.ntags.read(e) > 0, 'no miners this round');
            let ring = ring.span();
            let stake = self.check_ring(ring, ROLE_VALIDATOR);
            // msg = seal: a relayer can't swap in another seal under this sig
            assert(lsag::verify(ring, vote_scope_of(get_contract_address(), e), seal, sig), 'bad ring signature');
            let nullifier = lsag::key_id(sig.image);
            assert(self.voter_slot.read((e, nullifier)) == 0, 'already voted this round');
            let i = self.nvoters.read(e);
            assert(i < MAX_VOTERS, 'round full');
            self.voter_at.write((e, i), nullifier);
            self.voter_slot.write((e, nullifier), i + 1);
            self.voter_stake.write((e, i), stake);
            self.seals.write((e, i), seal);
            self.nvoters.write(e, i + 1);
            self.emit(Sealed { epoch: e, nullifier, stake, ring_size: ring.len() });
        }

        fn reveal(ref self: ContractState, nullifier: felt252, weights: Span<u16>, salt: felt252) {
            // no signature: only the sealer knows salt + weights
            assert(self.phase_now() == PHASE_REVEAL, 'not reveal phase');
            let e = self.epoch.read();
            let s = self.voter_slot.read((e, nullifier));
            assert(s != 0, 'no such voter');
            let i = s - 1;
            assert(!self.opened.read((e, i)), 'already revealed');
            let nt = self.ntags.read(e);
            assert(weights.len() == nt, 'one weight per tag');
            let seal = seal_of(get_contract_address(), e, nullifier, weights, salt);
            assert(seal == self.seals.read((e, i)), 'bad reveal');
            let mut j: u32 = 0;
            for w in weights {
                self.weight.write((e, i, j), *w);
                j += 1;
            };
            self.opened.write((e, i), true);
            self.nrevealed.write(e, self.nrevealed.read(e) + 1);
            self.emit(Revealed { epoch: e, nullifier });
        }

        fn finalize(ref self: ContractState) {
            assert(self.phase_now() == PHASE_FINALIZE, 'round still open');
            let e = self.epoch.read();
            let nv = self.nvoters.read(e);
            let nm = self.ntags.read(e);

            // voters: revealed with nonzero weights
            let mut vi: Array<u32> = array![];
            let mut total_stake: u128 = 0;
            for i in 0..nv {
                if self.opened.read((e, i)) {
                    let mut sum: u128 = 0;
                    for j in 0..nm {
                        sum += self.weight.read((e, i, j)).into();
                    };
                    if sum > 0 {
                        vi.append(i);
                        total_stake += self.voter_stake.read((e, i));
                    }
                }
            };
            let k = vi.len();
            // w[a * nm + j] normalized weight of voter a on tag j; s[a] stake share
            let mut w: Array<u128> = array![];
            let mut s: Array<u128> = array![];
            for a in 0..k {
                let i = *vi.at(a);
                let mut sum: u128 = 0;
                for j in 0..nm {
                    sum += self.weight.read((e, i, j)).into();
                };
                for j in 0..nm {
                    let raw: u128 = self.weight.read((e, i, j)).into();
                    w.append(raw * ONE / sum);
                };
                s.append(self.voter_stake.read((e, i)) * ONE / total_stake);
            };

            // consensus c_j = stake-weighted median; clipped rank
            let mut c: Array<u128> = array![];
            let mut rank: Array<u128> = array![];
            let mut rank_total: u128 = 0;
            for j in 0..nm {
                let mut best: u128 = 0;
                for b in 0..k {
                    let cand = *w.at(b * nm + j);
                    if cand > best {
                        let mut support: u128 = 0;
                        for a in 0..k {
                            if *w.at(a * nm + j) >= cand {
                                support += *s.at(a);
                            }
                        };
                        if support * 2 >= ONE {
                            best = cand;
                        }
                    }
                };
                c.append(best);
                self.cons.write((e, j), best);
                let mut r: u128 = 0;
                for a in 0..k {
                    r += *s.at(a) * core::cmp::min(*w.at(a * nm + j), best) / ONE;
                };
                rank.append(r);
                rank_total += r;
            };

            // incentive -> miner amounts (claimed later by tag)
            let half = self.emission.read() / 2;
            let mut inc: Array<u128> = array![];
            for j in 0..nm {
                let ij = if rank_total == 0 { 0 } else { *rank.at(j) * ONE / rank_total };
                inc.append(ij);
                self.incent.write((e, j), ij);
                self.miner_amt.write((e, j), half * ij / ONE);
            };

            // dividends D_a = sum_j B_aj I_j with this round's bonds
            let mut d: Array<u128> = array![];
            let mut d_total: u128 = 0;
            for a in 0..k {
                let mut x: u128 = 0;
                for j in 0..nm {
                    let r = *rank.at(j);
                    if r > 0 {
                        let mine = *s.at(a) * core::cmp::min(*w.at(a * nm + j), *c.at(j)) / ONE;
                        x += (mine * ONE / r) * *inc.at(j) / ONE;
                    }
                };
                d.append(x);
                d_total += x;
            };
            for a in 0..k {
                let da = if d_total == 0 { 0 } else { *d.at(a) * ONE / d_total };
                let i = *vi.at(a);
                self.divs.write((e, i), da);
                self.voter_amt.write((e, i), half * da / ONE);
            };

            self.emit(Finalized { epoch: e, miners: nm, voters: k, emission: self.emission.read() });
            self.epoch.write(e + 1);
            self.epoch_start.write(get_block_timestamp());
        }

        fn claim_miner(
            ref self: ContractState, epoch: u64, ring: Array<Point>, recipient: ContractAddress, sig: Signature,
        ) {
            assert(epoch < self.epoch.read(), 'round not finalized');
            assert(!recipient.is_zero(), 'zero recipient');
            let ring = ring.span();
            self.check_ring(ring, ROLE_MINER);
            let scope = mine_scope_of(get_contract_address(), epoch);
            assert(lsag::verify(ring, scope, recipient.into(), sig), 'bad ring signature');
            let tag = lsag::key_id(sig.image);
            let s = self.tag_slot.read((epoch, tag));
            assert(s != 0, 'tag did not submit');
            self.credit(epoch, ROLE_MINER, tag, recipient, self.miner_amt.read((epoch, s - 1)));
        }

        fn claim_validator(
            ref self: ContractState, epoch: u64, ring: Array<Point>, recipient: ContractAddress, sig: Signature,
        ) {
            assert(epoch < self.epoch.read(), 'round not finalized');
            assert(!recipient.is_zero(), 'zero recipient');
            let ring = ring.span();
            self.check_ring(ring, ROLE_VALIDATOR);
            let scope = vote_scope_of(get_contract_address(), epoch);
            assert(lsag::verify(ring, scope, recipient.into(), sig), 'bad ring signature');
            let nullifier = lsag::key_id(sig.image);
            let s = self.voter_slot.read((epoch, nullifier));
            assert(s != 0, 'nullifier did not vote');
            self.credit(epoch, ROLE_VALIDATOR, nullifier, recipient, self.voter_amt.read((epoch, s - 1)));
        }

        fn phase(self: @ContractState) -> u8 {
            self.phase_now()
        }

        fn epoch(self: @ContractState) -> u64 {
            self.epoch.read()
        }

        fn params(self: @ContractState) -> (ContractAddress, u32, u64, u64, u64, u128, bool) {
            (
                self.owner.read(),
                self.min_ring.read(),
                self.submit_secs.read(),
                self.commit_secs.read(),
                self.reveal_secs.read(),
                self.emission.read(),
                self.open.read(),
            )
        }

        fn mine_scope(self: @ContractState, epoch: u64) -> felt252 {
            mine_scope_of(get_contract_address(), epoch)
        }

        fn vote_scope(self: @ContractState, epoch: u64) -> felt252 {
            vote_scope_of(get_contract_address(), epoch)
        }

        fn seal(self: @ContractState, nullifier: felt252, weights: Span<u16>, salt: felt252) -> felt252 {
            seal_of(get_contract_address(), self.epoch.read(), nullifier, weights, salt)
        }

        fn member_count(self: @ContractState, role: u8) -> u32 {
            self.count.read(role)
        }

        fn member(self: @ContractState, role: u8, index: u32) -> Point {
            assert(index < self.count.read(role), 'index out of range');
            self.members.read((role, index))
        }

        fn role_of(self: @ContractState, key: Point) -> u8 {
            self.role.read(lsag::key_id(key))
        }

        fn stake_of(self: @ContractState, key: Point) -> u128 {
            self.stake.read(lsag::key_id(key))
        }

        fn tags(self: @ContractState, epoch: u64) -> Array<felt252> {
            let mut out = array![];
            for j in 0..self.ntags.read(epoch) {
                out.append(self.tag_at.read((epoch, j)));
            };
            out
        }

        fn work_of(self: @ContractState, epoch: u64, tag: felt252) -> felt252 {
            self.work.read((epoch, tag))
        }

        fn voters(self: @ContractState, epoch: u64) -> Array<felt252> {
            let mut out = array![];
            for i in 0..self.nvoters.read(epoch) {
                out.append(self.voter_at.read((epoch, i)));
            };
            out
        }

        fn sealed(self: @ContractState) -> u32 {
            self.nvoters.read(self.epoch.read())
        }

        fn revealed(self: @ContractState) -> u32 {
            self.nrevealed.read(self.epoch.read())
        }

        fn weights_of(self: @ContractState, epoch: u64, nullifier: felt252) -> Array<u16> {
            let s = self.voter_slot.read((epoch, nullifier));
            assert(s != 0, 'no such voter');
            assert(self.opened.read((epoch, s - 1)), 'not revealed');
            let mut out = array![];
            for j in 0..self.ntags.read(epoch) {
                out.append(self.weight.read((epoch, s - 1, j)));
            };
            out
        }

        fn consensus(self: @ContractState, epoch: u64) -> Array<u128> {
            let mut out = array![];
            for j in 0..self.ntags.read(epoch) {
                out.append(self.cons.read((epoch, j)));
            };
            out
        }

        fn incentive(self: @ContractState, epoch: u64) -> Array<u128> {
            let mut out = array![];
            for j in 0..self.ntags.read(epoch) {
                out.append(self.incent.read((epoch, j)));
            };
            out
        }

        fn dividends(self: @ContractState, epoch: u64) -> Array<u128> {
            let mut out = array![];
            for i in 0..self.nvoters.read(epoch) {
                out.append(self.divs.read((epoch, i)));
            };
            out
        }

        fn miner_reward(self: @ContractState, epoch: u64, tag: felt252) -> u128 {
            let s = self.tag_slot.read((epoch, tag));
            if s == 0 { 0 } else { self.miner_amt.read((epoch, s - 1)) }
        }

        fn validator_reward(self: @ContractState, epoch: u64, nullifier: felt252) -> u128 {
            let s = self.voter_slot.read((epoch, nullifier));
            if s == 0 { 0 } else { self.voter_amt.read((epoch, s - 1)) }
        }

        fn claimed(self: @ContractState, epoch: u64, id: felt252) -> bool {
            self.is_claimed.read((epoch, id))
        }

        fn earned(self: @ContractState, who: ContractAddress) -> u128 {
            self.credit.read(who)
        }
    }
}
