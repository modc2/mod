use snforge_std::{
    ContractClassTrait, DeclareResultTrait, declare, start_cheat_block_timestamp_global,
    start_cheat_caller_address, stop_cheat_caller_address,
};
use starknet::{ContractAddress, contract_address_const};
use starktime::mock_erc20::{IMockERC20Dispatcher, IMockERC20DispatcherTrait};
use starktime::{IStarkTimeDispatcher, IStarkTimeDispatcherTrait, Point};

const DAY: u64 = 86400;
const E18: u256 = 1_000_000_000_000_000_000;
const T0: u64 = 1_700_000_000;

fn owner() -> ContractAddress {
    contract_address_const::<'owner'>()
}
fn alice() -> ContractAddress {
    contract_address_const::<'alice'>()
}
fn bob() -> ContractAddress {
    contract_address_const::<'bob'>()
}

fn setup() -> (IStarkTimeDispatcher, IMockERC20Dispatcher) {
    start_cheat_block_timestamp_global(T0);
    let (token_addr, _) = declare("MockERC20").unwrap().contract_class().deploy(@array![]).unwrap();
    let token = IMockERC20Dispatcher { contract_address: token_addr };
    let mut args = array![];
    owner().serialize(ref args);
    token_addr.serialize(ref args);
    (1460 * DAY).serialize(ref args);
    let (st_addr, _) = declare("StarkTime").unwrap().contract_class().deploy(@args).unwrap();
    let st = IStarkTimeDispatcher { contract_address: st_addr };
    for who in array![owner(), alice(), bob()] {
        token.mint(who, 1_000_000 * E18);
        start_cheat_caller_address(token_addr, who);
        token.approve(st_addr, 1_000_000 * E18);
        stop_cheat_caller_address(token_addr);
    };
    (st, token)
}

fn stake_as(st: IStarkTimeDispatcher, who: ContractAddress, amount: u256, lock: u64) -> u64 {
    start_cheat_caller_address(st.contract_address, who);
    let id = st.stake(amount, lock);
    stop_cheat_caller_address(st.contract_address);
    id
}

fn fund(st: IStarkTimeDispatcher, amount: u256, duration: u64) {
    start_cheat_caller_address(st.contract_address, owner());
    st.notify_reward(amount, duration);
    stop_cheat_caller_address(st.contract_address);
}

#[test]
fn curve_matches_paper_engine() {
    let (st, _) = setup();
    assert(st.get_multiplier(0) == 10000, '0d');
    assert(st.get_multiplier(365 * DAY) == 30000, '1y');
    assert(st.get_multiplier(9999 * DAY) == 60000, 'clamp');
    let mid = st.get_multiplier((180 + 365) * DAY / 2);
    assert(mid > 24900 && mid < 25100, 'interp');
}

#[test]
fn rewards_split_by_weight() {
    let (st, _) = setup();
    let a = stake_as(st, alice(), 1000 * E18, 365 * DAY); // 3x
    let b = stake_as(st, bob(), 1000 * E18, 0); // 1x
    fund(st, 86400 * E18, DAY); // 1 token / second
    start_cheat_block_timestamp_global(T0 + DAY);
    let pa = st.pending(a);
    let pb = st.pending(b);
    // 3:1 split of 86400 tokens, allowing integer dust
    assert(pa > 64799 * E18 && pa <= 64800 * E18, 'alice 3/4');
    assert(pb > 21599 * E18 && pb <= 21600 * E18, 'bob 1/4');
}

#[test]
#[should_panic(expected: ('still locked',))]
fn cannot_unstake_while_locked() {
    let (st, _) = setup();
    let id = stake_as(st, alice(), 100 * E18, 30 * DAY);
    start_cheat_block_timestamp_global(T0 + DAY);
    start_cheat_caller_address(st.contract_address, alice());
    st.unstake(id);
}

#[test]
#[should_panic(expected: ('not your position',))]
fn cannot_unstake_others() {
    let (st, _) = setup();
    let id = stake_as(st, alice(), 100 * E18, 0);
    start_cheat_caller_address(st.contract_address, bob());
    st.unstake(id);
}

#[test]
fn unstake_returns_principal_and_rewards() {
    let (st, token) = setup();
    let id = stake_as(st, alice(), 100 * E18, 30 * DAY);
    fund(st, 1000 * E18, 10 * DAY);
    start_cheat_block_timestamp_global(T0 + 31 * DAY);
    let before = token.balance_of(alice());
    start_cheat_caller_address(st.contract_address, alice());
    st.unstake(id);
    let got = token.balance_of(alice()) - before;
    // sole staker: principal + the whole 1000 stream (minus dust)
    assert(got > 1099 * E18 && got <= 1100 * E18, 'payout');
    let (staked, weight, open, _) = st.get_stats();
    assert(staked == 0 && weight == 0 && open == 0, 'cleared');
}

#[test]
fn poke_lapses_to_one_x() {
    let (st, _) = setup();
    let id = stake_as(st, alice(), 100 * E18, 7 * DAY); // 1.1x
    let (_, w0, _, _) = st.get_stats();
    assert(w0 == 110 * E18, 'boosted');
    start_cheat_block_timestamp_global(T0 + 8 * DAY);
    st.poke(id);
    let (_, w1, _, _) = st.get_stats();
    assert(w1 == 100 * E18, 'lapsed');
    assert(st.get_position(id).lapsed, 'flag');
}

#[test]
fn extend_reboosts() {
    let (st, _) = setup();
    let id = stake_as(st, alice(), 100 * E18, 30 * DAY);
    start_cheat_caller_address(st.contract_address, alice());
    st.extend(id, 1460 * DAY);
    let p = st.get_position(id);
    assert(p.multiplier_bps == 60000, '6x');
    assert(p.weight == 600 * E18, 'weight');
}

#[test]
#[should_panic(expected: ('only owner',))]
fn set_points_is_owner_only() {
    let (st, _) = setup();
    start_cheat_caller_address(st.contract_address, alice());
    st.set_points(array![Point { lock_seconds: 0, multiplier_bps: 10000 }]);
}

#[test]
#[should_panic(expected: ('multiplier decreased',))]
fn set_points_rejects_decreasing_curve() {
    let (st, _) = setup();
    start_cheat_caller_address(st.contract_address, owner());
    st.set_points(
        array![
            Point { lock_seconds: 0, multiplier_bps: 20000 },
            Point { lock_seconds: DAY, multiplier_bps: 15000 },
        ],
    );
}
