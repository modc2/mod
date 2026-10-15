//! ZQuorum — the on-chain books for zquorum's anonymous quorums.
//!
//! The contract holds, per quorum (keyed by its public `topic` felt):
//! the member COMMITMENTS, the spent NULLIFIERS and the yes/no tally.
//! It never sees a secret and never learns who voted:
//!
//!   create(topic, threshold)       open a quorum
//!   join(topic, commitment)        register C = pedersen('zq-commit', secret)
//!   vote(topic, nullifier, choice) record N = pedersen(pedersen('zq-null',
//!                                  secret), topic); a repeated N is rejected
//!
//! The first vote FREEZES the member set and fixes its root on-chain with
//! the circuit's own `zq::root` over the sorted commitments, so the root
//! here is byte-identical to the one the anchor computes off-chain.
//!
//! Trust (v0.1): writes come from the TALLIER account. The tallier checks
//! membership by running the zq circuit off-chain (it sees the secret
//! transiently) and posts only (nullifier, choice). Everything else is
//! enforced here, publicly: the frozen root, one vote per nullifier,
//! the threshold and the tally. Every write is sent by the tallier, so
//! the transaction sender reveals nothing about the voter. v0.2 replaces
//! the tallier's attestation with a STARK proof of the same circuit.

use starknet::ContractAddress;

pub const MAX_MEMBERS: u32 = 64;
pub const CHOICE_NO: u8 = 0;
pub const CHOICE_YES: u8 = 1;

#[derive(Copy, Drop, Serde, PartialEq, Debug)]
pub struct Tally {
    pub yes: u32,
    pub no: u32,
    pub threshold: u32,
    pub members: u32,
    pub frozen: bool,
    pub root: felt252,
    pub reached: bool,
}

#[starknet::interface]
pub trait IZQuorum<T> {
    // tallier writes
    fn create(ref self: T, topic: felt252, threshold: u32);
    fn join(ref self: T, topic: felt252, commitment: felt252);
    fn vote(ref self: T, topic: felt252, nullifier: felt252, choice: u8);
    fn set_tallier(ref self: T, tallier: ContractAddress);
    // views
    fn tallier(self: @T) -> ContractAddress;
    fn exists(self: @T, topic: felt252) -> bool;
    fn tally(self: @T, topic: felt252) -> Tally;
    fn member_count(self: @T, topic: felt252) -> u32;
    fn member(self: @T, topic: felt252, index: u32) -> felt252;
    fn is_member(self: @T, topic: felt252, commitment: felt252) -> bool;
    fn spent(self: @T, topic: felt252, nullifier: felt252) -> bool;
    fn quorum_count(self: @T) -> u32;
    fn quorum_at(self: @T, index: u32) -> felt252;
}

#[starknet::contract]
pub mod ZQuorum {
    use starknet::{ContractAddress, get_caller_address};
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };
    use super::{Tally, MAX_MEMBERS, CHOICE_NO, CHOICE_YES};

    #[storage]
    struct Storage {
        tallier: ContractAddress,
        nquorums: u32,
        quorums: Map<u32, felt252>, // index -> topic
        threshold: Map<felt252, u32>, // 0 = no such quorum
        nmembers: Map<felt252, u32>,
        members: Map<(felt252, u32), felt252>,
        joined: Map<(felt252, felt252), bool>,
        frozen: Map<felt252, bool>,
        root: Map<felt252, felt252>,
        yes: Map<felt252, u32>,
        no: Map<felt252, u32>,
        spent: Map<(felt252, felt252), bool>,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Created: Created,
        Joined: Joined,
        Frozen: Frozen,
        Voted: Voted,
        Reached: Reached,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Created {
        #[key]
        pub topic: felt252,
        pub threshold: u32,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Joined {
        #[key]
        pub topic: felt252,
        pub commitment: felt252,
        pub index: u32,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Frozen {
        #[key]
        pub topic: felt252,
        pub root: felt252,
        pub members: u32,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Voted {
        #[key]
        pub topic: felt252,
        pub nullifier: felt252,
        pub choice: u8,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Reached {
        #[key]
        pub topic: felt252,
        pub yes: u32,
    }

    #[constructor]
    fn constructor(ref self: ContractState, tallier: ContractAddress) {
        self.tallier.write(tallier);
    }

    #[generate_trait]
    impl Internal of InternalTrait {
        fn only_tallier(self: @ContractState) {
            assert(get_caller_address() == self.tallier.read(), 'only the tallier');
        }

        fn must_exist(self: @ContractState, topic: felt252) {
            assert(self.threshold.read(topic) != 0, 'no such quorum');
        }

        /// Members sorted ascending (as u256) — the canonical order the
        /// zq root is defined over. Insertion sort; n <= MAX_MEMBERS.
        fn sorted_members(self: @ContractState, topic: felt252) -> Array<felt252> {
            let n = self.nmembers.read(topic);
            let mut vals: Array<u256> = array![];
            let mut i: u32 = 0;
            while i < n {
                let c: u256 = self.members.read((topic, i)).into();
                // rebuild with c inserted at its place
                let mut next: Array<u256> = array![];
                let mut placed = false;
                for v in vals.span() {
                    if !placed && c < *v {
                        next.append(c);
                        placed = true;
                    }
                    next.append(*v);
                };
                if !placed {
                    next.append(c);
                }
                vals = next;
                i += 1;
            };
            let mut out: Array<felt252> = array![];
            for v in vals.span() {
                out.append((*v).try_into().unwrap());
            };
            out
        }
    }

    #[abi(embed_v0)]
    impl ZQuorumImpl of super::IZQuorum<ContractState> {
        fn create(ref self: ContractState, topic: felt252, threshold: u32) {
            self.only_tallier();
            assert(threshold >= 1, 'threshold must be >= 1');
            assert(self.threshold.read(topic) == 0, 'quorum exists');
            self.threshold.write(topic, threshold);
            let n = self.nquorums.read();
            self.quorums.write(n, topic);
            self.nquorums.write(n + 1);
            self.emit(Created { topic, threshold });
        }

        fn join(ref self: ContractState, topic: felt252, commitment: felt252) {
            self.only_tallier();
            self.must_exist(topic);
            assert(!self.frozen.read(topic), 'member set is frozen');
            assert(!self.joined.read((topic, commitment)), 'already a member');
            let n = self.nmembers.read(topic);
            assert(n < MAX_MEMBERS, 'member set full');
            self.members.write((topic, n), commitment);
            self.joined.write((topic, commitment), true);
            self.nmembers.write(topic, n + 1);
            self.emit(Joined { topic, commitment, index: n });
        }

        fn vote(ref self: ContractState, topic: felt252, nullifier: felt252, choice: u8) {
            self.only_tallier();
            self.must_exist(topic);
            assert(choice == CHOICE_NO || choice == CHOICE_YES, 'choice is 0 or 1');
            let n = self.nmembers.read(topic);
            assert(n > 0, 'quorum has no members');
            assert(!self.spent.read((topic, nullifier)), 'already voted');
            if !self.frozen.read(topic) {
                let leaves = self.sorted_members(topic);
                let r = zq::root(leaves.span());
                self.root.write(topic, r);
                self.frozen.write(topic, true);
                self.emit(Frozen { topic, root: r, members: n });
            }
            let yes_before = self.yes.read(topic);
            assert(yes_before + self.no.read(topic) < n, 'more votes than members');
            self.spent.write((topic, nullifier), true);
            if choice == CHOICE_YES {
                self.yes.write(topic, yes_before + 1);
            } else {
                self.no.write(topic, self.no.read(topic) + 1);
            }
            self.emit(Voted { topic, nullifier, choice });
            let t = self.threshold.read(topic);
            if choice == CHOICE_YES && yes_before + 1 == t {
                self.emit(Reached { topic, yes: t });
            }
        }

        fn set_tallier(ref self: ContractState, tallier: ContractAddress) {
            self.only_tallier();
            self.tallier.write(tallier);
        }

        fn tallier(self: @ContractState) -> ContractAddress {
            self.tallier.read()
        }

        fn exists(self: @ContractState, topic: felt252) -> bool {
            self.threshold.read(topic) != 0
        }

        fn tally(self: @ContractState, topic: felt252) -> Tally {
            self.must_exist(topic);
            let yes = self.yes.read(topic);
            let threshold = self.threshold.read(topic);
            Tally {
                yes,
                no: self.no.read(topic),
                threshold,
                members: self.nmembers.read(topic),
                frozen: self.frozen.read(topic),
                root: self.root.read(topic),
                reached: yes >= threshold,
            }
        }

        fn member_count(self: @ContractState, topic: felt252) -> u32 {
            self.nmembers.read(topic)
        }

        fn member(self: @ContractState, topic: felt252, index: u32) -> felt252 {
            assert(index < self.nmembers.read(topic), 'index out of range');
            self.members.read((topic, index))
        }

        fn is_member(self: @ContractState, topic: felt252, commitment: felt252) -> bool {
            self.joined.read((topic, commitment))
        }

        fn spent(self: @ContractState, topic: felt252, nullifier: felt252) -> bool {
            self.spent.read((topic, nullifier))
        }

        fn quorum_count(self: @ContractState) -> u32 {
            self.nquorums.read()
        }

        fn quorum_at(self: @ContractState, index: u32) -> felt252 {
            assert(index < self.nquorums.read(), 'index out of range');
            self.quorums.read(index)
        }
    }
}
