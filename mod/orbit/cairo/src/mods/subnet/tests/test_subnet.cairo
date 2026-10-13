use snforge_std::{ContractClassTrait, DeclareResultTrait, declare};
use subnet::{ISubnetDispatcher, ISubnetDispatcherTrait};

fn deploy() -> ISubnetDispatcher {
    let contract = declare("Subnet").unwrap().contract_class();
    let (addr, _) = contract.deploy(@array![]).unwrap();
    ISubnetDispatcher { contract_address: addr }
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
