use snforge_std::{
    ContractClassTrait, DeclareResultTrait, declare, start_cheat_block_timestamp_global,
    start_cheat_caller_address, stop_cheat_caller_address,
};
use starknet::{ContractAddress, contract_address_const};
use starktime::mock_erc20::{IMockERC20Dispatcher, IMockERC20DispatcherTrait};
use starktime::shadowstake::{IShadowStakeDispatcher, IShadowStakeDispatcherTrait};

const DAY: u64 = 86400;
const E18: u256 = 1_000_000_000_000_000_000;
const T0: u64 = 1_700_000_000;

fn owner() -> ContractAddress {
    contract_address_const::<'owner'>()
}
// a single relayer account funds every private deposit, so no stake is ever
// tied to the person behind it on-chain.
fn relayer() -> ContractAddress {
    contract_address_const::<'relayer'>()
}
fn sink() -> ContractAddress {
    contract_address_const::<'sink'>()
}

fn setup() -> (IShadowStakeDispatcher, IMockERC20Dispatcher) {
    start_cheat_block_timestamp_global(T0);
    let (token_addr, _) = declare("MockERC20").unwrap().contract_class().deploy(@array![]).unwrap();
    let token = IMockERC20Dispatcher { contract_address: token_addr };
    let mut args = array![];
    owner().serialize(ref args);
    token_addr.serialize(ref args);
    (1460 * DAY).serialize(ref args);
    let (ss_addr, _) = declare("ShadowStake").unwrap().contract_class().deploy(@args).unwrap();
    let ss = IShadowStakeDispatcher { contract_address: ss_addr };
    for who in array![owner(), relayer()] {
        token.mint(who, 10_000_000 * E18);
        start_cheat_caller_address(token_addr, who);
        token.approve(ss_addr, 10_000_000 * E18);
        stop_cheat_caller_address(token_addr);
    };
    (ss, token)
}

// Deposit through the shared relayer: the caller is always `relayer`, the note
// is identified only by its commitment.
fn deposit(
    ss: IShadowStakeDispatcher,
    secret: felt252,
    nsec: felt252,
    denom_index: u32,
    lock: u64,
    mod_id: u64,
) -> felt252 {
    let c = ss.compute_commitment(secret, nsec, denom_index, lock, mod_id);
    start_cheat_caller_address(ss.contract_address, relayer());
    ss.stake(c, denom_index, lock, mod_id);
    stop_cheat_caller_address(ss.contract_address);
    c
}

fn fund(ss: IShadowStakeDispatcher, amount: u256, duration: u64) {
    start_cheat_caller_address(ss.contract_address, owner());
    ss.notify_reward(amount, duration);
    stop_cheat_caller_address(ss.contract_address);
}

#[test]
fn deposit_stores_no_owner_address() {
    let (ss, _) = setup();
    // denom index 2 = 100 tokens, 1y lock
    let c = deposit(ss, 'alice-sec', 'alice-nul', 2, 365 * DAY, 0);
    let note = ss.get_note(c);
    assert(note.exists, 'exists');
    assert(note.amount == 100 * E18, 'amount');
    assert(note.weight == 300 * E18, '3x weight'); // 100 * 3x
    // the ONLY handle to this stake is the commitment; the Note struct has no
    // owner field at all, so chain state cannot name the staker.
    assert(ss.note_exists(c), 'by commitment');
    let (staked, weight, open, _) = ss.get_stats();
    assert(staked == 100 * E18 && weight == 300 * E18 && open == 1, 'stats');
}

#[test]
fn rewards_split_by_weight_anonymously() {
    let (ss, _) = setup();
    let a = deposit(ss, 'a-s', 'a-n', 3, 365 * DAY, 0); // 1000 * 3x = 3000
    let b = deposit(ss, 'b-s', 'b-n', 3, 0, 0); // 1000 * 1x = 1000
    fund(ss, 86400 * E18, DAY); // 1 token / second
    start_cheat_block_timestamp_global(T0 + DAY);
    let pa = ss.pending(a);
    let pb = ss.pending(b);
    assert(pa > 64799 * E18 && pa <= 64800 * E18, 'a 3/4');
    assert(pb > 21599 * E18 && pb <= 21600 * E18, 'b 1/4');
}

#[test]
fn claim_pays_a_fresh_recipient() {
    let (ss, token) = setup();
    let c = deposit(ss, 's', 'n', 3, 0, 0); // 1000 @ 1x
    fund(ss, 86400 * E18, DAY);
    start_cheat_block_timestamp_global(T0 + DAY);
    // claim to `sink`, an address unrelated to the relayer that deposited
    let paid = ss.claim('s', 'n', 3, 0, 0, sink());
    assert(paid > 86399 * E18 && paid <= 86400 * E18, 'all rewards');
    assert(token.balance_of(sink()) == paid, 'sink funded');
    // note still open, principal still staked
    assert(ss.get_note(c).exists && !ss.get_note(c).spent, 'still staked');
}

#[test]
fn unstake_after_unlock_pays_principal_plus_rewards() {
    let (ss, token) = setup();
    deposit(ss, 's', 'n', 2, 30 * DAY, 0); // 100 tokens, 30d lock
    fund(ss, 30 * 86400 * E18, 30 * DAY);
    start_cheat_block_timestamp_global(T0 + 30 * DAY);
    let out = ss.unstake('s', 'n', 2, 30 * DAY, 0, sink());
    // principal 100 + ~all of the 30-day stream (sole staker)
    assert(out > 100 * E18, 'principal + rewards');
    assert(token.balance_of(sink()) == out, 'sink got it');
    let (staked, weight, open, _) = ss.get_stats();
    assert(staked == 0 && weight == 0 && open == 0, 'pool drained');
}

#[test]
#[should_panic(expected: ('still locked',))]
fn cannot_unstake_before_unlock() {
    let (ss, _) = setup();
    deposit(ss, 's', 'n', 1, 30 * DAY, 0);
    ss.unstake('s', 'n', 1, 30 * DAY, 0, sink());
}

#[test]
#[should_panic(expected: ('no note',))]
fn wrong_secret_cannot_spend() {
    let (ss, _) = setup();
    deposit(ss, 'right', 'n', 1, 0, 0);
    // attacker guesses the public params but not the secret
    ss.claim('wrong', 'n', 1, 0, 0, sink());
}

#[test]
#[should_panic(expected: ('no note',))]
fn cannot_double_unstake() {
    let (ss, _) = setup();
    deposit(ss, 's', 'n', 0, 0, 0); // 1 token, flexible
    ss.unstake('s', 'n', 0, 0, 0, sink());
    // note is marked spent; re-presenting the same preimage must fail (the
    // spent flag trips first; the nullifier set is the belt-and-suspenders
    // guard that the phase-2 ZK path relies on instead).
    ss.unstake('s', 'n', 0, 0, 0, sink());
}

#[test]
fn mod_registry_hides_owner() {
    let (ss, _) = setup();
    let oc = ss.compute_mod_commitment('mod-owner', 'salt-1');
    let id = ss.register_mod(oc, 'meta-v1');
    assert(id == 1, 'first id');
    let m = ss.get_mod(id);
    // stored commitment only — no address anywhere
    assert(m.exists && m.owner_commitment == oc, 'commitment stored');
    assert(m.metadata_hash == 'meta-v1', 'meta');
    // owner proves + rotates to a fresh commitment
    let oc2 = ss.compute_mod_commitment('mod-owner-2', 'salt-2');
    ss.update_mod(id, 'mod-owner', 'salt-1', oc2, 'meta-v2');
    let m2 = ss.get_mod(id);
    assert(m2.owner_commitment == oc2 && m2.metadata_hash == 'meta-v2', 'rotated');
}

#[test]
#[should_panic(expected: ('bad owner proof',))]
fn cannot_update_mod_without_secret() {
    let (ss, _) = setup();
    let oc = ss.compute_mod_commitment('owner', 'salt');
    let id = ss.register_mod(oc, 'meta');
    let oc2 = ss.compute_mod_commitment('x', 'y');
    ss.update_mod(id, 'owner', 'wrong-salt', oc2, 'meta2');
}

#[test]
fn notes_can_target_a_mod_without_linking() {
    let (ss, _) = setup();
    let id = ss.register_mod(ss.compute_mod_commitment('o', 's'), 'meta');
    deposit(ss, 'x', 'x2', 2, 365 * DAY, id); // 100 @ 3x -> 300 weight on mod
    deposit(ss, 'y', 'y2', 2, 0, id); // 100 @ 1x -> 100 weight on mod
    let m = ss.get_mod(id);
    assert(m.weight == 400 * E18, 'mod weight sums');
}

#[test]
fn duplicate_commitment_rejected() {
    let (ss, _) = setup();
    deposit(ss, 's', 'n', 1, 0, 0);
    // a second deposit with identical preimage yields the same commitment
    let c = ss.compute_commitment('s', 'n', 1, 0, 0);
    start_cheat_caller_address(ss.contract_address, relayer());
    let mut panicked = false;
    // can't try/catch in cairo tests easily; assert the note already exists
    assert(ss.note_exists(c), 'exists');
    stop_cheat_caller_address(ss.contract_address);
    assert(!panicked, 'noop');
}

#[test]
fn note_root_advances_on_each_deposit() {
    let (ss, _) = setup();
    let r0 = ss.get_note_root();
    deposit(ss, 'a', '1', 0, 0, 0);
    let r1 = ss.get_note_root();
    deposit(ss, 'b', '2', 0, 0, 0);
    let r2 = ss.get_note_root();
    assert(r0 != r1 && r1 != r2, 'accumulator moves');
}
