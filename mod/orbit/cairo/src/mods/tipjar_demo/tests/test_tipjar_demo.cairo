use snforge_std::{
    ContractClassTrait, DeclareResultTrait, EventSpyAssertionsTrait, declare, spy_events,
    start_cheat_caller_address, stop_cheat_caller_address,
};
use starknet::ContractAddress;
use tipjar_demo::TipjarDemo;
use tipjar_demo::{ITipjarDemoDispatcher, ITipjarDemoDispatcherTrait};

fn owner() -> ContractAddress {
    'owner'.try_into().unwrap()
}

fn alice() -> ContractAddress {
    'alice'.try_into().unwrap()
}

fn bob() -> ContractAddress {
    'bob'.try_into().unwrap()
}

fn deploy() -> ITipjarDemoDispatcher {
    let contract = declare("TipjarDemo").unwrap().contract_class();
    let mut calldata = array![];
    Serde::serialize(@owner(), ref calldata);
    let (addr, _) = contract.deploy(@calldata).unwrap();
    ITipjarDemoDispatcher { contract_address: addr }
}

fn tip_as(j: ITipjarDemoDispatcher, who: ContractAddress, amount: u256) {
    start_cheat_caller_address(j.contract_address, who);
    j.tip(amount);
    stop_cheat_caller_address(j.contract_address);
}

#[test]
fn test_starts_empty_with_owner() {
    let j = deploy();
    assert(j.owner() == owner(), 'owner set');
    assert(j.total() == 0, 'total starts 0');
    assert(j.tipped_by(alice()) == 0, 'alice starts 0');
}

#[test]
fn test_tips_accumulate_per_tipper_and_total() {
    let j = deploy();
    tip_as(j, alice(), 10);
    tip_as(j, bob(), 5);
    tip_as(j, alice(), 7);
    assert(j.tipped_by(alice()) == 17, 'alice 17');
    assert(j.tipped_by(bob()) == 5, 'bob 5');
    assert(j.total() == 22, 'total 22');
}

#[test]
fn test_owner_can_tip_too() {
    let j = deploy();
    tip_as(j, owner(), 3);
    assert(j.tipped_by(owner()) == 3, 'owner tipped');
    assert(j.total() == 3, 'total 3');
}

#[test]
#[should_panic(expected: 'tip must be > 0')]
fn test_zero_tip_reverts() {
    let j = deploy();
    tip_as(j, alice(), 0);
}

#[test]
fn test_owner_reset_clears_total_keeps_per_tipper() {
    let j = deploy();
    tip_as(j, alice(), 10);
    tip_as(j, bob(), 4);

    start_cheat_caller_address(j.contract_address, owner());
    j.reset_total();
    stop_cheat_caller_address(j.contract_address);

    assert(j.total() == 0, 'total reset');
    assert(j.tipped_by(alice()) == 10, 'alice kept');
    assert(j.tipped_by(bob()) == 4, 'bob kept');

    // the jar keeps working after a reset
    tip_as(j, bob(), 1);
    assert(j.total() == 1, 'total restarts');
    assert(j.tipped_by(bob()) == 5, 'bob 5');
}

#[test]
#[should_panic(expected: 'only owner')]
fn test_non_owner_cannot_reset() {
    let j = deploy();
    tip_as(j, alice(), 10);
    start_cheat_caller_address(j.contract_address, alice());
    j.reset_total();
}

#[test]
fn test_events() {
    let j = deploy();
    let mut spy = spy_events();
    tip_as(j, alice(), 9);
    start_cheat_caller_address(j.contract_address, owner());
    j.reset_total();
    stop_cheat_caller_address(j.contract_address);

    spy
        .assert_emitted(
            @array![
                (
                    j.contract_address,
                    TipjarDemo::Event::Tipped(TipjarDemo::Tipped { tipper: alice(), amount: 9 }),
                ),
                (
                    j.contract_address,
                    TipjarDemo::Event::TotalReset(
                        TipjarDemo::TotalReset { by: owner(), previous: 9 },
                    ),
                ),
            ],
        );
}

#[test]
fn test_zero_owner_rejected() {
    let contract = declare("TipjarDemo").unwrap().contract_class();
    let calldata = array![0];
    assert(contract.deploy(@calldata).is_err(), 'zero owner must fail');
}
