// Counter — a felt252 get/set example contract.
//
// A minimal starter contract: one felt252 of storage behind a get/set
// interface. Replace this with your own logic, or hand it to the agent
// (`m cairo/vibe name=counter prompt='...'`) to grow it.

#[starknet::interface]
pub trait ICounter<TContractState> {
    fn get(self: @TContractState) -> felt252;
    fn set(ref self: TContractState, value: felt252);
}

#[starknet::contract]
pub mod Counter {
    use starknet::storage::{StoragePointerReadAccess, StoragePointerWriteAccess};

    #[storage]
    struct Storage {
        value: felt252,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    enum Event {
        ValueSet: ValueSet,
    }

    #[derive(Drop, starknet::Event)]
    struct ValueSet {
        value: felt252,
    }

    #[abi(embed_v0)]
    impl CounterImpl of super::ICounter<ContractState> {
        fn get(self: @ContractState) -> felt252 {
            self.value.read()
        }

        fn set(ref self: ContractState, value: felt252) {
            self.value.write(value);
            self.emit(ValueSet { value });
        }
    }
}
