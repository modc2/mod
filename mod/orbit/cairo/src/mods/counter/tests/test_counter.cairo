use snforge_std::{ContractClassTrait, DeclareResultTrait, declare};
use counter::{ICounterDispatcher, ICounterDispatcherTrait};

fn deploy() -> ICounterDispatcher {
    let contract = declare("Counter").unwrap().contract_class();
    let (addr, _) = contract.deploy(@array![]).unwrap();
    ICounterDispatcher { contract_address: addr }
}

#[test]
fn test_starts_zero() {
    let c = deploy();
    assert(c.get() == 0, 'should start at 0');
}

#[test]
fn test_set_get() {
    let c = deploy();
    c.set(42);
    assert(c.get() == 42, 'value should be 42');
}
