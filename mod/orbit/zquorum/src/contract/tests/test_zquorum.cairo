use snforge_std::{declare, ContractClassTrait, DeclareResultTrait, start_cheat_caller_address};
use starknet::ContractAddress;
use zquorum::zquorum::{IZQuorumDispatcher, IZQuorumDispatcherTrait};

const TOPIC: felt252 = 'launch';

fn tallier() -> ContractAddress {
    'tallier'.try_into().unwrap()
}

fn deploy() -> IZQuorumDispatcher {
    let class = declare("ZQuorum").unwrap().contract_class();
    let mut args: Array<felt252> = array![];
    tallier().serialize(ref args);
    let (addr, _) = class.deploy(@args).unwrap();
    start_cheat_caller_address(addr, tallier());
    IZQuorumDispatcher { contract_address: addr }
}

/// quorum of 3 members (secrets 7, 8, 9), joined in UNSORTED order
fn ceremony(threshold: u32) -> IZQuorumDispatcher {
    let q = deploy();
    q.create(TOPIC, threshold);
    q.join(TOPIC, zq::commit(9));
    q.join(TOPIC, zq::commit(7));
    q.join(TOPIC, zq::commit(8));
    q
}

fn sorted_root() -> felt252 {
    // the anchor's canonical order: ascending commitments
    let mut v: Array<u256> = array![
        zq::commit(7).into(), zq::commit(8).into(), zq::commit(9).into(),
    ];
    // tiny bubble sort for the test
    let mut s = v.span();
    let (a, b, c) = (*s.at(0), *s.at(1), *s.at(2));
    let (a, b) = if a > b { (b, a) } else { (a, b) };
    let (b, c) = if b > c { (c, b) } else { (b, c) };
    let (a, b) = if a > b { (b, a) } else { (a, b) };
    v = array![a, b, c];
    s = v.span();
    let leaves = array![
        (*s.at(0)).try_into().unwrap(), (*s.at(1)).try_into().unwrap(),
        (*s.at(2)).try_into().unwrap(),
    ];
    zq::root(leaves.span())
}

#[test]
fn full_ceremony_reaches_quorum() {
    let q = ceremony(2);
    q.vote(TOPIC, zq::nullifier(7, TOPIC), 1);
    q.vote(TOPIC, zq::nullifier(8, TOPIC), 0);
    assert(!q.tally(TOPIC).reached, 'not yet');
    q.vote(TOPIC, zq::nullifier(9, TOPIC), 1);
    let t = q.tally(TOPIC);
    assert(t.yes == 2 && t.no == 1, 'counts');
    assert(t.reached && t.frozen && t.members == 3, 'reached');
}

#[test]
fn root_matches_circuit_over_sorted_set() {
    let q = ceremony(1);
    q.vote(TOPIC, zq::nullifier(7, TOPIC), 1);
    assert(q.tally(TOPIC).root == sorted_root(), 'root == zq::root(sorted)');
}

#[test]
#[should_panic(expected: ('already voted',))]
fn double_vote_rejected() {
    let q = ceremony(2);
    q.vote(TOPIC, zq::nullifier(7, TOPIC), 1);
    q.vote(TOPIC, zq::nullifier(7, TOPIC), 1);
}

#[test]
#[should_panic(expected: ('member set is frozen',))]
fn late_join_rejected() {
    let q = ceremony(2);
    q.vote(TOPIC, zq::nullifier(7, TOPIC), 1);
    q.join(TOPIC, zq::commit(10));
}

#[test]
#[should_panic(expected: ('only the tallier',))]
fn strangers_cannot_write() {
    let q = ceremony(2);
    start_cheat_caller_address(q.contract_address, 'mallory'.try_into().unwrap());
    q.vote(TOPIC, zq::nullifier(7, TOPIC), 1);
}

#[test]
#[should_panic(expected: ('already a member',))]
fn duplicate_commitment_rejected() {
    let q = ceremony(2);
    q.join(TOPIC, zq::commit(7));
}

#[test]
#[should_panic(expected: ('more votes than members',))]
fn cannot_outvote_the_member_set() {
    let q = ceremony(1);
    q.vote(TOPIC, 1, 1);
    q.vote(TOPIC, 2, 1);
    q.vote(TOPIC, 3, 1);
    q.vote(TOPIC, 4, 1);
}

#[test]
fn quorums_are_listed_and_isolated() {
    let q = ceremony(1);
    q.create('other', 1);
    assert(q.quorum_count() == 2, 'two quorums');
    assert(q.quorum_at(1) == 'other', 'listed');
    // same secret, different topic -> different nullifier, independent books
    q.vote(TOPIC, zq::nullifier(7, TOPIC), 1);
    assert(q.spent(TOPIC, zq::nullifier(7, TOPIC)), 'spent here');
    assert(!q.spent('other', zq::nullifier(7, TOPIC)), 'not there');
    assert(q.member_count('other') == 0, 'empty');
}
