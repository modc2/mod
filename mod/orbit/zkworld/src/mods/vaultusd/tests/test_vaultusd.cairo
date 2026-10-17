use snforge_std::{
    ContractClassTrait, DeclareResultTrait, declare, start_cheat_caller_address,
    stop_cheat_caller_address,
};
use starknet::ContractAddress;
use vaultusd::{IVaultusdDispatcher, IVaultusdDispatcherTrait};

fn oracle() -> ContractAddress {
    'oracle'.try_into().unwrap()
}

fn alice() -> ContractAddress {
    'alice'.try_into().unwrap()
}

fn bob() -> ContractAddress {
    'bob'.try_into().unwrap()
}

// ETH at $3000
fn deploy() -> IVaultusdDispatcher {
    let contract = declare("Vaultusd").unwrap().contract_class();
    let mut calldata = array![];
    Serde::serialize(@'Vault Dollar', ref calldata);
    Serde::serialize(@'VUSD', ref calldata);
    Serde::serialize(@oracle(), ref calldata);
    Serde::serialize(@3000_u256, ref calldata);
    let (addr, _) = contract.deploy(@calldata).unwrap();
    IVaultusdDispatcher { contract_address: addr }
}

fn as_(c: IVaultusdDispatcher, who: ContractAddress) {
    start_cheat_caller_address(c.contract_address, who);
}

#[test]
fn test_borrow_two_thirds() {
    let c = deploy();
    as_(c, alice());
    c.deposit(1); // 1 ETH = $3000
    assert(c.max_mint(alice()) == 2000, 'can borrow 2000');
    c.mint(2000);
    assert(c.balance_of(alice()) == 2000, 'alice has 2000');
    assert(c.ratio_of(alice()) == 150, 'ratio 150%');
    assert(c.is_safe(alice()), 'safe');
}

#[test]
#[should_panic(expected: 'not enough collateral')]
fn test_cannot_overborrow() {
    let c = deploy();
    as_(c, alice());
    c.deposit(1);
    c.mint(2001);
}

#[test]
#[should_panic(expected: 'would make your loan unsafe')]
fn test_cannot_withdraw_backing() {
    let c = deploy();
    as_(c, alice());
    c.deposit(2);
    c.mint(3000);
    c.withdraw(1);
}

#[test]
fn test_repay_then_withdraw() {
    let c = deploy();
    as_(c, alice());
    c.deposit(1);
    c.mint(1000);
    c.repay(1000);
    c.withdraw(1);
    assert(c.debt_of(alice()) == 0, 'no debt');
    assert(c.collateral_of(alice()) == 0, 'all out');
    assert(c.total_supply() == 0, 'burned');
}

#[test]
fn test_price_drop_lets_anyone_liquidate() {
    let c = deploy();
    // bob has spare dollars from his own safe loan
    as_(c, bob());
    c.deposit(10);
    c.mint(2000);
    stop_cheat_caller_address(c.contract_address);
    as_(c, alice());
    c.deposit(1);
    c.mint(2000);
    stop_cheat_caller_address(c.contract_address);
    // ETH falls to $2500: alice's $2000 loan has only $2500 behind it (125%)
    as_(c, oracle());
    c.set_price(2500);
    stop_cheat_caller_address(c.contract_address);
    assert(!c.is_safe(alice()), 'alice unsafe');
    as_(c, bob());
    c.liquidate(alice());
    assert(c.debt_of(alice()) == 0, 'loan closed');
    assert(c.collateral_of(bob()) == 11, 'bob got the ETH');
    assert(c.balance_of(bob()) == 0, 'bob paid 2000');
}

#[test]
#[should_panic(expected: 'that loan is still safe')]
fn test_cannot_liquidate_safe_loan() {
    let c = deploy();
    as_(c, alice());
    c.deposit(1);
    c.mint(1000);
    stop_cheat_caller_address(c.contract_address);
    as_(c, bob());
    c.liquidate(alice());
}
