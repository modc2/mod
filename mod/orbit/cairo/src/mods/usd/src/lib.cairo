// A cash-backed digital dollar
//
// A cash-backed stablecoin, the USDC / USDT kind. The plain-English version:
//
//   1. You hand the issuer $10 (off-chain: a bank transfer).
//   2. The issuer writes "the bank now holds $10 more" (set_reserves)
//      and mints you 10 coins.
//   3. You can send the coins to anyone, like any token.
//   4. When you want your dollars back you redeem: your coins are burned
//      and the issuer pays you from the bank.
//
// The one rule that makes it "stable": the issuer can never mint more coins
// than there are dollars in reserve. That's checked on every mint, on chain,
// where everyone can see it (is_fully_backed).

use starknet::ContractAddress;

#[starknet::interface]
pub trait IUsd<TContractState> {
    fn name(self: @TContractState) -> felt252;
    fn symbol(self: @TContractState) -> felt252;
    fn decimals(self: @TContractState) -> u8;
    fn issuer(self: @TContractState) -> ContractAddress;
    fn total_supply(self: @TContractState) -> u256;
    fn balance_of(self: @TContractState, account: ContractAddress) -> u256;
    fn reserves(self: @TContractState) -> u256;
    fn is_fully_backed(self: @TContractState) -> bool;
    fn transfer(ref self: TContractState, to: ContractAddress, amount: u256) -> bool;
    fn set_reserves(ref self: TContractState, amount: u256);
    fn mint(ref self: TContractState, to: ContractAddress, amount: u256);
    fn redeem(ref self: TContractState, amount: u256);
}

#[starknet::contract]
pub mod Usd {
    use starknet::{ContractAddress, get_caller_address};
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };

    #[storage]
    struct Storage {
        name: felt252,
        symbol: felt252,
        issuer: ContractAddress,
        supply: u256,
        // dollars the issuer says it holds in the bank (an attestation)
        reserves: u256,
        balances: Map<ContractAddress, u256>,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Transfer: Transfer,
        ReservesSet: ReservesSet,
        Redeemed: Redeemed,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Transfer {
        #[key]
        pub from: ContractAddress,
        #[key]
        pub to: ContractAddress,
        pub amount: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct ReservesSet {
        pub amount: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Redeemed {
        #[key]
        pub who: ContractAddress,
        pub amount: u256,
    }

    #[constructor]
    fn constructor(ref self: ContractState, name: felt252, symbol: felt252, issuer: ContractAddress) {
        self.name.write(name);
        self.symbol.write(symbol);
        self.issuer.write(issuer);
    }

    #[abi(embed_v0)]
    impl UsdImpl of super::IUsd<ContractState> {
        fn name(self: @ContractState) -> felt252 {
            self.name.read()
        }

        fn symbol(self: @ContractState) -> felt252 {
            self.symbol.read()
        }

        fn decimals(self: @ContractState) -> u8 {
            18
        }

        fn issuer(self: @ContractState) -> ContractAddress {
            self.issuer.read()
        }

        fn total_supply(self: @ContractState) -> u256 {
            self.supply.read()
        }

        fn balance_of(self: @ContractState, account: ContractAddress) -> u256 {
            self.balances.read(account)
        }

        fn reserves(self: @ContractState) -> u256 {
            self.reserves.read()
        }

        fn is_fully_backed(self: @ContractState) -> bool {
            self.reserves.read() >= self.supply.read()
        }

        fn transfer(ref self: ContractState, to: ContractAddress, amount: u256) -> bool {
            let from = get_caller_address();
            let bal = self.balances.read(from);
            assert(bal >= amount, 'not enough coins');
            self.balances.write(from, bal - amount);
            self.balances.write(to, self.balances.read(to) + amount);
            self.emit(Transfer { from, to, amount });
            true
        }

        // The issuer reports how many dollars sit in the bank. Real issuers
        // back this with an auditor's report; here it's their word, on chain.
        fn set_reserves(ref self: ContractState, amount: u256) {
            self.only_issuer();
            // can't claim less cash than coins already out: that would be
            // admitting the coin isn't backed
            assert(amount >= self.supply.read(), 'reserves below supply');
            self.reserves.write(amount);
            self.emit(ReservesSet { amount });
        }

        fn mint(ref self: ContractState, to: ContractAddress, amount: u256) {
            self.only_issuer();
            let supply = self.supply.read() + amount;
            // THE rule: every coin has a dollar behind it
            assert(supply <= self.reserves.read(), 'not enough cash in reserve');
            self.supply.write(supply);
            self.balances.write(to, self.balances.read(to) + amount);
            let zero: ContractAddress = 0.try_into().unwrap();
            self.emit(Transfer { from: zero, to, amount });
        }

        // Cash out: burn your coins; the issuer pays you the same number of
        // dollars from the bank, so the reserves shrink by the same amount.
        fn redeem(ref self: ContractState, amount: u256) {
            let who = get_caller_address();
            let bal = self.balances.read(who);
            assert(bal >= amount, 'not enough coins');
            self.balances.write(who, bal - amount);
            self.supply.write(self.supply.read() - amount);
            self.reserves.write(self.reserves.read() - amount);
            let zero: ContractAddress = 0.try_into().unwrap();
            self.emit(Transfer { from: who, to: zero, amount });
            self.emit(Redeemed { who, amount });
        }
    }

    #[generate_trait]
    impl InternalImpl of InternalTrait {
        fn only_issuer(self: @ContractState) {
            assert(get_caller_address() == self.issuer.read(), 'only the issuer');
        }
    }
}
