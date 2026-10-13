// Airdrop: claim your allocation with a Poseidon Merkle proof.
//
// A Merkle allowlist / airdrop. The owner publishes one Poseidon root; each
// claimant proves (address, amount) is a leaf with a sibling path. The chain
// never stores the list, only the root and who has claimed.

use core::poseidon::poseidon_hash_span;
use starknet::ContractAddress;

/// Leaf for one allowlist entry.
pub fn leaf(who: ContractAddress, amount: u128) -> felt252 {
    poseidon_hash_span(array![who.into(), amount.into()].span())
}

/// Parent of two nodes. Sorted, so a proof is just the siblings with no
/// left/right flags: the same convention OpenZeppelin uses.
pub fn hash_pair(a: felt252, b: felt252) -> felt252 {
    let (x, y): (u256, u256) = (a.into(), b.into());
    if x < y {
        poseidon_hash_span(array![a, b].span())
    } else {
        poseidon_hash_span(array![b, a].span())
    }
}

/// Fold a leaf up its proof and compare with the root.
pub fn verify(root: felt252, leaf: felt252, proof: Span<felt252>) -> bool {
    let mut node = leaf;
    for sibling in proof {
        node = hash_pair(node, *sibling);
    };
    node == root
}

#[starknet::interface]
pub trait IAirdrop<TContractState> {
    fn root(self: @TContractState) -> felt252;
    fn claim(ref self: TContractState, amount: u128, proof: Span<felt252>);
    fn is_claimed(self: @TContractState, who: ContractAddress) -> bool;
    fn claimed_of(self: @TContractState, who: ContractAddress) -> u128;
    fn total_claimed(self: @TContractState) -> u128;
}

#[starknet::contract]
pub mod Airdrop {
    use starknet::{ContractAddress, get_caller_address};
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };

    #[storage]
    struct Storage {
        root: felt252,
        claimed: Map<ContractAddress, u128>,
        total: u128,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Claimed: Claimed,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Claimed {
        #[key]
        pub who: ContractAddress,
        pub amount: u128,
    }

    #[constructor]
    fn constructor(ref self: ContractState, root: felt252) {
        self.root.write(root);
    }

    #[abi(embed_v0)]
    impl AirdropImpl of super::IAirdrop<ContractState> {
        fn root(self: @ContractState) -> felt252 {
            self.root.read()
        }

        fn claim(ref self: ContractState, amount: u128, proof: Span<felt252>) {
            let who = get_caller_address();
            assert(amount > 0, 'zero amount');
            // the leaf is rebuilt from the CALLER, so a leaked proof is
            // useless to anyone but the address it was issued to
            assert(self.claimed.read(who) == 0, 'already claimed');
            let ok = super::verify(self.root.read(), super::leaf(who, amount), proof);
            assert(ok, 'invalid proof');
            self.claimed.write(who, amount);
            self.total.write(self.total.read() + amount);
            self.emit(Claimed { who, amount });
        }

        fn is_claimed(self: @ContractState, who: ContractAddress) -> bool {
            self.claimed.read(who) != 0
        }

        fn claimed_of(self: @ContractState, who: ContractAddress) -> u128 {
            self.claimed.read(who)
        }

        fn total_claimed(self: @ContractState) -> u128 {
            self.total.read()
        }
    }
}
