use snforge_std::{
    ContractClassTrait, DeclareResultTrait, declare, start_cheat_block_timestamp_global,
    start_cheat_caller_address, stop_cheat_caller_address,
};
use starknet::ContractAddress;
use subnet::{ISubnetDispatcher, ISubnetDispatcherTrait, ONE, seal_of};

fn addr(name: felt252) -> ContractAddress {
    name.try_into().unwrap()
}

fn owner() -> ContractAddress {
    addr('owner')
}

// 4 miners, 100s commit window, 100s reveal window, 1000 emission per epoch
fn deploy() -> ISubnetDispatcher {
    start_cheat_block_timestamp_global(1000);
    let contract = declare("Subnet").unwrap().contract_class();
    let calldata = array![owner().into(), 4, 100, 100, 1000];
    let (a, _) = contract.deploy(@calldata).unwrap();
    let c = ISubnetDispatcher { contract_address: a };
    start_cheat_caller_address(a, owner());
    c.set_validator(addr('alice'), 40);
    c.set_validator(addr('bob'), 30);
    c.set_validator(addr('carol'), 20);
    c.set_validator(addr('dave'), 10);
    stop_cheat_caller_address(a);
    c
}

fn seal(c: ISubnetDispatcher, who: ContractAddress, w: Span<u16>, salt: felt252) {
    start_cheat_caller_address(c.contract_address, who);
    c.commit(seal_of(c.contract_address, c.epoch(), who, w, salt));
    stop_cheat_caller_address(c.contract_address);
}

fn open(c: ISubnetDispatcher, who: ContractAddress, w: Span<u16>, salt: felt252) {
    start_cheat_caller_address(c.contract_address, who);
    c.reveal(w, salt);
    stop_cheat_caller_address(c.contract_address);
}

fn honest() -> Span<u16> {
    array![4, 3, 2, 1].span()
}

// dave is an outlier pumping miner 3, which nobody else rates
fn pump() -> Span<u16> {
    array![0, 0, 0, 1].span()
}

fn run_epoch(c: ISubnetDispatcher) {
    seal(c, addr('alice'), honest(), 'a');
    seal(c, addr('bob'), honest(), 'b');
    seal(c, addr('carol'), honest(), 'c');
    seal(c, addr('dave'), pump(), 'd');
    open(c, addr('alice'), honest(), 'a');
    open(c, addr('bob'), honest(), 'b');
    open(c, addr('carol'), honest(), 'c');
    open(c, addr('dave'), pump(), 'd');
    c.finalize();
}

fn sum(xs: Array<u128>) -> u128 {
    let mut t = 0;
    for x in xs {
        t += x;
    };
    t
}

#[test]
fn test_phases_advance_when_everyone_acts() {
    let c = deploy();
    assert(c.phase() == 0, 'starts in commit');
    seal(c, addr('alice'), honest(), 'a');
    seal(c, addr('bob'), honest(), 'b');
    seal(c, addr('carol'), honest(), 'c');
    assert(c.phase() == 0, 'still waiting on dave');
    seal(c, addr('dave'), pump(), 'd');
    assert(c.phase() == 1, 'all sealed -> reveal');
    open(c, addr('alice'), honest(), 'a');
    open(c, addr('bob'), honest(), 'b');
    open(c, addr('carol'), honest(), 'c');
    open(c, addr('dave'), pump(), 'd');
    assert(c.phase() == 2, 'all revealed -> finalize');
    c.finalize();
    assert(c.epoch() == 1 && c.phase() == 0, 'next epoch');
}

#[test]
#[should_panic(expected: 'not revealed')]
fn test_weights_hidden_while_sealed() {
    let c = deploy();
    seal(c, addr('alice'), honest(), 'a');
    c.weights_of(0, addr('alice'));
}

#[test]
fn test_outlier_cannot_pump_a_miner() {
    let c = deploy();
    run_epoch(c);
    let cons = c.consensus(0);
    let inc = c.incentive(0);
    // 90% of stake gave miner 3 a 10% weight; consensus = 0.1, dave's 1.0 is
    // clipped to it. Unclipped, miner 3 would get (0.09 + 0.1) / 1 = 19%.
    assert(*cons.at(3) == ONE / 10, 'consensus is the median');
    assert(*cons.at(0) == 4 * ONE / 10, 'miner 0 consensus');
    assert(*inc.at(3) < 11 * ONE / 100, 'dave got clipped');
    assert(*inc.at(0) > *inc.at(3) * 3, 'honest ranking holds');
    assert(*inc.at(0) > *inc.at(1) && *inc.at(1) > *inc.at(2), 'ranked');
    let total = sum(inc);
    assert(total <= ONE && total + 10 >= ONE, 'incentive sums to one');
}

#[test]
fn test_bonds_reward_agreeing_validators() {
    let c = deploy();
    run_epoch(c);
    run_epoch(c);
    let d = c.dividends(1);
    // alice 40% stake on consensus vs dave 10% stake off it
    assert(*d.at(0) > *d.at(1), 'more stake more bonds');
    assert(*d.at(1) > *d.at(3), 'outlier earns least');
    let per_val = *d.at(3) * ONE / 10;
    let per_alice = *d.at(0) * ONE / 40;
    assert(per_alice > per_val, 'per-stake: agreeing wins');
    // miner 0 holds 0.36 / 0.91 of incentive, 500 per epoch to miners
    let m0 = c.miner_earned(0);
    assert(m0 >= 394 && m0 <= 396, 'miner 0 paid');
    assert(c.validator_earned(addr('alice')) > c.validator_earned(addr('dave')), 'payout');
}

#[test]
#[should_panic(expected: 'bad reveal')]
fn test_copied_seal_cannot_be_opened() {
    // dave copies alice's seal and later replays her weights + salt
    let c = deploy();
    let alices = seal_of(c.contract_address, 0, addr('alice'), honest(), 'a');
    seal(c, addr('alice'), honest(), 'a');
    start_cheat_caller_address(c.contract_address, addr('dave'));
    c.commit(alices);
    stop_cheat_caller_address(c.contract_address);
    seal(c, addr('bob'), honest(), 'b');
    seal(c, addr('carol'), honest(), 'c');
    open(c, addr('dave'), honest(), 'a');
}

#[test]
#[should_panic(expected: 'bad reveal')]
fn test_cannot_change_weights_after_sealing() {
    let c = deploy();
    seal(c, addr('alice'), honest(), 'a');
    seal(c, addr('bob'), honest(), 'b');
    seal(c, addr('carol'), honest(), 'c');
    seal(c, addr('dave'), pump(), 'd');
    open(c, addr('dave'), honest(), 'd');
}

#[test]
#[should_panic(expected: 'not reveal phase')]
fn test_no_reveal_while_others_still_sealing() {
    let c = deploy();
    seal(c, addr('alice'), honest(), 'a');
    open(c, addr('alice'), honest(), 'a');
}

#[test]
#[should_panic(expected: 'commit window closed')]
fn test_late_seal_rejected() {
    let c = deploy();
    seal(c, addr('alice'), honest(), 'a');
    start_cheat_block_timestamp_global(1100);
    seal(c, addr('bob'), honest(), 'b');
}

#[test]
fn test_silent_validators_drop_out_after_deadline() {
    let c = deploy();
    seal(c, addr('alice'), honest(), 'a');
    seal(c, addr('dave'), pump(), 'd');
    start_cheat_block_timestamp_global(1100);
    assert(c.phase() == 1, 'window over -> reveal');
    open(c, addr('alice'), honest(), 'a');
    // dave never reveals; finalize once the reveal window ends
    start_cheat_block_timestamp_global(1200);
    assert(c.phase() == 2, 'deadline -> finalize');
    c.finalize();
    let inc = c.incentive(0);
    assert(*inc.at(0) == 4 * ONE / 10, 'only alice counted');
    assert(c.validator_earned(addr('dave')) == 0, 'silent earns nothing');
}

#[test]
#[should_panic(expected: 'epoch still open')]
fn test_no_early_finalize() {
    let c = deploy();
    seal(c, addr('alice'), honest(), 'a');
    c.finalize();
}

#[test]
#[should_panic(expected: 'not a validator')]
fn test_outsider_cannot_seal() {
    let c = deploy();
    seal(c, addr('mallory'), honest(), 'm');
}

#[test]
#[should_panic(expected: 'only owner')]
fn test_only_owner_sets_stake() {
    let c = deploy();
    start_cheat_caller_address(c.contract_address, addr('mallory'));
    c.set_validator(addr('mallory'), 1000);
}
