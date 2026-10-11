// Minimal ERC-20 used only by the snforge tests (stands in for STRK).
#[starknet::interface]
pub trait IMockERC20<T> {
    fn mint(ref self: T, to: starknet::ContractAddress, amount: u256);
    fn approve(ref self: T, spender: starknet::ContractAddress, amount: u256) -> bool;
    fn balance_of(self: @T, account: starknet::ContractAddress) -> u256;
    fn transfer(ref self: T, recipient: starknet::ContractAddress, amount: u256) -> bool;
    fn transfer_from(
        ref self: T,
        sender: starknet::ContractAddress,
        recipient: starknet::ContractAddress,
        amount: u256,
    ) -> bool;
}

#[starknet::contract]
pub mod MockERC20 {
    use starknet::storage::{Map, StorageMapReadAccess, StorageMapWriteAccess};
    use starknet::{ContractAddress, get_caller_address};

    #[storage]
    struct Storage {
        balances: Map<ContractAddress, u256>,
        allowances: Map<(ContractAddress, ContractAddress), u256>,
    }

    #[abi(embed_v0)]
    impl MockERC20Impl of super::IMockERC20<ContractState> {
        fn mint(ref self: ContractState, to: ContractAddress, amount: u256) {
            self.balances.write(to, self.balances.read(to) + amount);
        }

        fn approve(ref self: ContractState, spender: ContractAddress, amount: u256) -> bool {
            self.allowances.write((get_caller_address(), spender), amount);
            true
        }

        fn balance_of(self: @ContractState, account: ContractAddress) -> u256 {
            self.balances.read(account)
        }

        fn transfer(ref self: ContractState, recipient: ContractAddress, amount: u256) -> bool {
            let sender = get_caller_address();
            assert(self.balances.read(sender) >= amount, 'insufficient balance');
            self.balances.write(sender, self.balances.read(sender) - amount);
            self.balances.write(recipient, self.balances.read(recipient) + amount);
            true
        }

        fn transfer_from(
            ref self: ContractState, sender: ContractAddress, recipient: ContractAddress, amount: u256,
        ) -> bool {
            let spender = get_caller_address();
            let allowed = self.allowances.read((sender, spender));
            assert(allowed >= amount, 'insufficient allowance');
            assert(self.balances.read(sender) >= amount, 'insufficient balance');
            self.allowances.write((sender, spender), allowed - amount);
            self.balances.write(sender, self.balances.read(sender) - amount);
            self.balances.write(recipient, self.balances.read(recipient) + amount);
            true
        }
    }
}
