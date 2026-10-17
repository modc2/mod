//! Zuma — an anonymous subnet on Starknet.
//!
//! Two public sets of STARK-curve keys: MINERS and VALIDATORS. Membership is
//! public (that is what makes the network permissioned); every ACTION is a
//! ring signature, so it proves "some member of the set" and nothing more.
//!
//!   submit(ring, work, sig)        a miner posts work for the current epoch.
//!                                  Its key image under (subnet, epoch) is
//!                                  the miner's TAG for that epoch: one
//!                                  submission per miner per epoch, and tags
//!                                  are unlinkable across epochs.
//!   score(ring, tag, score, sig)   a validator scores a tag. Its key image
//!                                  under (subnet, epoch, tag) is a nullifier:
//!                                  one score per validator per tag, and two
//!                                  scores by the same validator on different
//!                                  tags are unlinkable.
//!   claim(epoch, ring, to, sig)    after the epoch closes, the miner re-signs
//!                                  in the same scope (same key image = same
//!                                  tag) and credits its emission share to
//!                                  `to`. Public books: every credit is visible.
//!
//! Validators never learn which miner they scored; miners never learn who
//! scored them. The anonymity set is the ring the signer chose (>= min_ring,
//! <= MAX_RING members of the right role) — a ring of 3 hides among 3.
//! No call checks the caller, so a relayer can post on a member's behalf
//! (the tx sender is public on Starknet; posting from your own registered
//! account would deanonymize you).

use starknet::ContractAddress;
use crate::lsag::{Point, Signature};

pub const ROLE_MINER: u8 = 1;
pub const ROLE_VALIDATOR: u8 = 2;
pub const MAX_RING: u32 = 64;
pub const MAX_SCORE: u8 = 100;

#[starknet::interface]
pub trait IZuma<T> {
    // registry (owner, or anyone while open)
    fn register(ref self: T, role: u8, key: Point);
    fn set_open(ref self: T, open: bool);
    fn transfer_ownership(ref self: T, owner: ContractAddress);
    // anonymous actions
    fn submit(ref self: T, ring: Array<Point>, work: felt252, sig: Signature);
    fn score(ref self: T, ring: Array<Point>, tag: felt252, score: u8, sig: Signature);
    fn claim(ref self: T, epoch: u64, ring: Array<Point>, recipient: ContractAddress, sig: Signature);
    // views
    fn epoch(self: @T) -> u64;
    fn mine_scope(self: @T, epoch: u64) -> felt252;
    fn score_scope(self: @T, epoch: u64, tag: felt252) -> felt252;
    fn role_of(self: @T, key: Point) -> u8;
    fn member_count(self: @T, role: u8) -> u32;
    fn member(self: @T, role: u8, index: u32) -> Point;
    fn submitted(self: @T, epoch: u64, tag: felt252) -> bool;
    fn work_of(self: @T, epoch: u64, tag: felt252) -> felt252;
    fn tally(self: @T, epoch: u64, tag: felt252) -> (u128, u32);
    fn epoch_total(self: @T, epoch: u64) -> u128;
    fn epoch_tags(self: @T, epoch: u64) -> u32;
    fn tag_at(self: @T, epoch: u64, index: u32) -> felt252;
    fn claimed(self: @T, epoch: u64, tag: felt252) -> bool;
    fn earned(self: @T, who: ContractAddress) -> u128;
    fn params(self: @T) -> (ContractAddress, u64, u32, u128, bool);
}

#[starknet::contract]
pub mod Zuma {
    use core::poseidon::poseidon_hash_span;
    use starknet::{ContractAddress, get_caller_address, get_contract_address, get_block_number};
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };
    use crate::lsag;
    use crate::lsag::{Point, Signature};
    use super::{ROLE_MINER, ROLE_VALIDATOR, MAX_RING, MAX_SCORE};

    #[storage]
    struct Storage {
        owner: ContractAddress,
        open: bool,
        tempo: u64,
        min_ring: u32,
        emission: u128,
        role: Map<felt252, u8>, // key_id -> role
        count: Map<u8, u32>,
        members: Map<(u8, u32), Point>,
        work: Map<(u64, felt252), felt252>, // (epoch, tag) -> work commitment
        has_work: Map<(u64, felt252), bool>,
        tags: Map<(u64, u32), felt252>,
        ntags: Map<u64, u32>,
        score_sum: Map<(u64, felt252), u128>,
        score_n: Map<(u64, felt252), u32>,
        total: Map<u64, u128>,
        spent: Map<felt252, bool>, // validator nullifiers (key image ids)
        is_claimed: Map<(u64, felt252), bool>,
        credit: Map<ContractAddress, u128>,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Registered: Registered,
        Submitted: Submitted,
        Scored: Scored,
        Claimed: Claimed,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Registered {
        #[key]
        pub role: u8,
        pub key_id: felt252,
        pub index: u32,
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
    pub struct Scored {
        #[key]
        pub epoch: u64,
        pub tag: felt252,
        pub score: u8,
        pub nullifier: felt252,
        pub ring_size: u32,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Claimed {
        #[key]
        pub epoch: u64,
        pub tag: felt252,
        pub recipient: ContractAddress,
        pub amount: u128,
    }

    #[constructor]
    fn constructor(
        ref self: ContractState, owner: ContractAddress, tempo: u64, min_ring: u32, emission: u128,
    ) {
        assert(tempo > 0, 'tempo must be > 0');
        assert(min_ring > 0 && min_ring <= MAX_RING, 'bad min_ring');
        self.owner.write(owner);
        self.tempo.write(tempo);
        self.min_ring.write(min_ring);
        self.emission.write(emission);
    }

    #[generate_trait]
    impl Internal of InternalTrait {
        fn current_epoch(self: @ContractState) -> u64 {
            get_block_number() / self.tempo.read()
        }

        fn scope_mine(self: @ContractState, epoch: u64) -> felt252 {
            poseidon_hash_span(
                array!['zuma-mine', get_contract_address().into(), epoch.into()].span(),
            )
        }

        fn scope_score(self: @ContractState, epoch: u64, tag: felt252) -> felt252 {
            poseidon_hash_span(
                array!['zuma-score', get_contract_address().into(), epoch.into(), tag].span(),
            )
        }

        /// Ring = strictly increasing key ids, all registered with `role`.
        fn check_ring(self: @ContractState, ring: Span<Point>, role: u8) {
            let n = ring.len();
            assert(n >= self.min_ring.read(), 'ring below min_ring');
            assert(n <= MAX_RING, 'ring above MAX_RING');
            let mut prev: u256 = 0;
            let mut first = true;
            for p in ring {
                let id = lsag::key_id(*p);
                assert(self.role.read(id) == role, 'ring has a non-member');
                let idu: u256 = id.into();
                assert(first || idu > prev, 'ring not sorted/unique');
                prev = idu;
                first = false;
            };
        }
    }

    #[abi(embed_v0)]
    impl ZumaImpl of super::IZuma<ContractState> {
        fn register(ref self: ContractState, role: u8, key: Point) {
            assert(role == ROLE_MINER || role == ROLE_VALIDATOR, 'role is 1 or 2');
            assert(self.open.read() || get_caller_address() == self.owner.read(), 'registry closed');
            assert(lsag::to_ec(key).is_some(), 'key not on curve');
            let id = lsag::key_id(key);
            assert(self.role.read(id) == 0, 'key already registered');
            self.role.write(id, role);
            let i = self.count.read(role);
            self.members.write((role, i), key);
            self.count.write(role, i + 1);
            self.emit(Registered { role, key_id: id, index: i });
        }

        fn set_open(ref self: ContractState, open: bool) {
            assert(get_caller_address() == self.owner.read(), 'only owner');
            self.open.write(open);
        }

        fn transfer_ownership(ref self: ContractState, owner: ContractAddress) {
            assert(get_caller_address() == self.owner.read(), 'only owner');
            self.owner.write(owner);
        }

        fn submit(ref self: ContractState, ring: Array<Point>, work: felt252, sig: Signature) {
            let ring = ring.span();
            self.check_ring(ring, ROLE_MINER);
            let epoch = self.current_epoch();
            let scope = self.scope_mine(epoch);
            assert(lsag::verify(ring, scope, work, sig), 'bad ring signature');
            let tag = lsag::key_id(sig.image);
            assert(!self.has_work.read((epoch, tag)), 'already submitted this epoch');
            self.has_work.write((epoch, tag), true);
            self.work.write((epoch, tag), work);
            let i = self.ntags.read(epoch);
            self.tags.write((epoch, i), tag);
            self.ntags.write(epoch, i + 1);
            self.emit(Submitted { epoch, tag, work, ring_size: ring.len() });
        }

        fn score(ref self: ContractState, ring: Array<Point>, tag: felt252, score: u8, sig: Signature) {
            assert(score <= MAX_SCORE, 'score is 0..100');
            let ring = ring.span();
            self.check_ring(ring, ROLE_VALIDATOR);
            let epoch = self.current_epoch();
            assert(self.has_work.read((epoch, tag)), 'no such tag this epoch');
            let scope = self.scope_score(epoch, tag);
            assert(lsag::verify(ring, scope, score.into(), sig), 'bad ring signature');
            let nullifier = lsag::key_id(sig.image);
            assert(!self.spent.read(nullifier), 'already scored this tag');
            self.spent.write(nullifier, true);
            self.score_sum.write((epoch, tag), self.score_sum.read((epoch, tag)) + score.into());
            self.score_n.write((epoch, tag), self.score_n.read((epoch, tag)) + 1);
            self.total.write(epoch, self.total.read(epoch) + score.into());
            self.emit(Scored { epoch, tag, score, nullifier, ring_size: ring.len() });
        }

        fn claim(
            ref self: ContractState,
            epoch: u64,
            ring: Array<Point>,
            recipient: ContractAddress,
            sig: Signature,
        ) {
            assert(epoch < self.current_epoch(), 'epoch still open');
            let ring = ring.span();
            self.check_ring(ring, ROLE_MINER);
            let scope = self.scope_mine(epoch);
            assert(lsag::verify(ring, scope, recipient.into(), sig), 'bad ring signature');
            let tag = lsag::key_id(sig.image);
            assert(self.has_work.read((epoch, tag)), 'tag did not submit');
            assert(!self.is_claimed.read((epoch, tag)), 'already claimed');
            self.is_claimed.write((epoch, tag), true);
            let total = self.total.read(epoch);
            let amount: u128 = if total == 0 {
                0
            } else {
                let num: u256 = self.emission.read().into() * self.score_sum.read((epoch, tag)).into();
                (num / total.into()).try_into().unwrap()
            };
            self.credit.write(recipient, self.credit.read(recipient) + amount);
            self.emit(Claimed { epoch, tag, recipient, amount });
        }

        fn epoch(self: @ContractState) -> u64 {
            self.current_epoch()
        }
        fn mine_scope(self: @ContractState, epoch: u64) -> felt252 {
            self.scope_mine(epoch)
        }
        fn score_scope(self: @ContractState, epoch: u64, tag: felt252) -> felt252 {
            self.scope_score(epoch, tag)
        }
        fn role_of(self: @ContractState, key: Point) -> u8 {
            self.role.read(lsag::key_id(key))
        }
        fn member_count(self: @ContractState, role: u8) -> u32 {
            self.count.read(role)
        }
        fn member(self: @ContractState, role: u8, index: u32) -> Point {
            assert(index < self.count.read(role), 'index out of range');
            self.members.read((role, index))
        }
        fn submitted(self: @ContractState, epoch: u64, tag: felt252) -> bool {
            self.has_work.read((epoch, tag))
        }
        fn work_of(self: @ContractState, epoch: u64, tag: felt252) -> felt252 {
            self.work.read((epoch, tag))
        }
        fn tally(self: @ContractState, epoch: u64, tag: felt252) -> (u128, u32) {
            (self.score_sum.read((epoch, tag)), self.score_n.read((epoch, tag)))
        }
        fn epoch_total(self: @ContractState, epoch: u64) -> u128 {
            self.total.read(epoch)
        }
        fn epoch_tags(self: @ContractState, epoch: u64) -> u32 {
            self.ntags.read(epoch)
        }
        fn tag_at(self: @ContractState, epoch: u64, index: u32) -> felt252 {
            self.tags.read((epoch, index))
        }
        fn claimed(self: @ContractState, epoch: u64, tag: felt252) -> bool {
            self.is_claimed.read((epoch, tag))
        }
        fn earned(self: @ContractState, who: ContractAddress) -> u128 {
            self.credit.read(who)
        }
        fn params(self: @ContractState) -> (ContractAddress, u64, u32, u128, bool) {
            (
                self.owner.read(),
                self.tempo.read(),
                self.min_ring.read(),
                self.emission.read(),
                self.open.read(),
            )
        }
    }
}
