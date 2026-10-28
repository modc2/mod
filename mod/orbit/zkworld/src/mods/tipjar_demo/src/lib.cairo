// A tip jar: anyone can tip, the jar remembers how much each tipper gave and a
// running total, and the owner (fixed at deploy) can reset the total.
//
// Tips are recorded amounts, not token transfers: this keeps the contract
// dependency-free and easy to read. Wiring in a real ERC20 `transfer_from`
// inside `tip` is the natural next step if the jar should hold funds.

use starknet::ContractAddress;

#[starknet::interface]
pub trait ITipjarDemo<TContractState> {
    fn tip(ref self: TContractState, amount: u256);
    fn reset_total(ref self: TContractState);
    fn total(self: @TContractState) -> u256;
    fn tipped_by(self: @TContractState, tipper: ContractAddress) -> u256;
    fn owner(self: @TContractState) -> ContractAddress;
}

#[starknet::contract]
pub mod TipjarDemo {
    use starknet::{ContractAddress, get_caller_address};
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };

    #[storage]
    struct Storage {
        owner: ContractAddress,
        total: u256,
        // lifetime per tipper: a reset clears the jar's running total, not
        // anyone's record of what they gave
        tips: Map<ContractAddress, u256>,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Tipped: Tipped,
        TotalReset: TotalReset,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Tipped {
        #[key]
        pub tipper: ContractAddress,
        pub amount: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct TotalReset {
        #[key]
        pub by: ContractAddress,
        // what the total was, so the reset is auditable from events alone
        pub previous: u256,
    }

    #[constructor]
    fn constructor(ref self: ContractState, owner: ContractAddress) {
        // a zero owner would make reset_total uncallable forever
        assert(owner.into() != 0_felt252, 'owner is zero');
        self.owner.write(owner);
    }

    #[abi(embed_v0)]
    impl TipjarDemoImpl of super::ITipjarDemo<ContractState> {
        fn tip(ref self: ContractState, amount: u256) {
            // zero tips would only spam the event log
            assert(amount > 0, 'tip must be > 0');
            let tipper = get_caller_address();
            self.tips.write(tipper, self.tips.read(tipper) + amount);
            self.total.write(self.total.read() + amount);
            self.emit(Tipped { tipper, amount });
        }

        fn reset_total(ref self: ContractState) {
            let caller = get_caller_address();
            assert(caller == self.owner.read(), 'only owner');
            let previous = self.total.read();
            self.total.write(0);
            self.emit(TotalReset { by: caller, previous });
        }

        fn total(self: @ContractState) -> u256 {
            self.total.read()
        }

        fn tipped_by(self: @ContractState, tipper: ContractAddress) -> u256 {
            self.tips.read(tipper)
        }

        fn owner(self: @ContractState) -> ContractAddress {
            self.owner.read()
        }
    }
}
