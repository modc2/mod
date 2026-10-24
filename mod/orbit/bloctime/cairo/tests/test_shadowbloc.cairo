use shadowbloc::paper_token::{IPaperTokenDispatcher, IPaperTokenDispatcherTrait};
use shadowbloc::shadowbloc::{
    ACTION_CLAIM, ACTION_REKEY, ACTION_UNSTAKE, IShadowBlocDispatcher, IShadowBlocDispatcherTrait,
};
use snforge_std::signature::KeyPairTrait;
use snforge_std::signature::stark_curve::{StarkCurveKeyPairImpl, StarkCurveSignerImpl};
use snforge_std::{
    ContractClassTrait, DeclareResultTrait, declare, start_cheat_block_timestamp_global,
    start_cheat_caller_address, stop_cheat_caller_address,
};
use starknet::ContractAddress;

const ONE: u256 = 1_000_000_000_000_000_000;
const DAY: u64 = 86400;
const WEEK: u64 = 604800;
// Thursday 2026-10-15 00:00 UTC — the next Friday 17:00 UTC is 41h later
const T0: u64 = 1792022400;

fn owner() -> ContractAddress {
    'owner'.try_into().unwrap()
}
fn relayer() -> ContractAddress {
    'relayer'.try_into().unwrap()
}
fn payee() -> ContractAddress {
    'payee'.try_into().unwrap()
}

fn setup() -> (IShadowBlocDispatcher, IPaperTokenDispatcher) {
    start_cheat_block_timestamp_global(T0);
    let tok_class = declare("PaperToken").unwrap().contract_class();
    let (tok_addr, _) = tok_class.deploy(@array![owner().into()]).unwrap();
    let tok = IPaperTokenDispatcher { contract_address: tok_addr };
    let class = declare("ShadowBloc").unwrap().contract_class();
    let (addr, _) = class.deploy(@array![owner().into(), tok_addr.into()]).unwrap();
    let sb = IShadowBlocDispatcher { contract_address: addr };
    // the relayer holds paper NAT and has approved the pool
    start_cheat_caller_address(tok_addr, owner());
    tok.mint(relayer(), 1_000_000 * ONE);
    stop_cheat_caller_address(tok_addr);
    start_cheat_caller_address(tok_addr, relayer());
    tok.approve(addr, 1_000_000 * ONE);
    stop_cheat_caller_address(tok_addr);
    (sb, tok)
}

fn stake_as_relayer(sb: IShadowBlocDispatcher, key: felt252, denom: u32, tier: u32) -> u64 {
    start_cheat_caller_address(sb.contract_address, relayer());
    let id = sb.stake(key, denom, tier);
    stop_cheat_caller_address(sb.contract_address);
    id
}

fn sign(sk: felt252, msg: felt252) -> (felt252, felt252) {
    let kp = KeyPairTrait::<felt252, felt252>::from_secret_key(sk);
    kp.sign(msg).unwrap()
}

fn pk(sk: felt252) -> felt252 {
    KeyPairTrait::<felt252, felt252>::from_secret_key(sk).public_key
}

fn signed(
    sb: IShadowBlocDispatcher, sk: felt252, action: felt252, id: u64, arg: felt252,
) -> (felt252, felt252) {
    let nonce = sb.get_note(id).nonce;
    sign(sk, sb.message_hash(action, id, nonce, arg))
}

#[test]
fn stake_records_a_key_not_an_address() {
    let (sb, tok) = setup();
    let id = stake_as_relayer(sb, pk(111), 1, 3); // 100 NAT, 1 year
    let n = sb.get_note(id);
    assert(n.key == pk(111), 'key stored');
    assert(n.amount == 100 * ONE, 'amount');
    assert(n.unlock == T0 + 365 * DAY, 'unlock');
    // BLOC = $100 x 31,536,000 s
    assert(n.bloc == 100 * ONE * 31536000, 'bloc');
    assert(sb.quote(1, 3) == n.bloc, 'quote matches');
    assert(sb.bucket_size(1, 3) == 1, 'bucket');
    let s = sb.stats();
    assert(s.open_notes == 1 && s.total_staked == 100 * ONE, 'stats');
    assert(tok.balance_of(sb.contract_address) == 100 * ONE, 'tokens pulled');
}

#[test]
#[should_panic(expected: 'bad denom')]
fn off_menu_amounts_rejected() {
    let (sb, _) = setup();
    stake_as_relayer(sb, pk(1), 9, 0);
}

#[test]
#[should_panic(expected: 'bad tier')]
fn off_menu_locks_rejected() {
    let (sb, _) = setup();
    stake_as_relayer(sb, pk(1), 0, 9);
}

#[test]
fn weekly_pot_pays_pro_rata_by_bloc_to_any_recipient() {
    let (sb, tok) = setup();
    let a = stake_as_relayer(sb, pk(11), 1, 2); // 100 NAT x 30d
    let b = stake_as_relayer(sb, pk(22), 1, 1); // 100 NAT x 7d
    start_cheat_caller_address(sb.contract_address, relayer());
    sb.fund_pot(370 * ONE);
    stop_cheat_caller_address(sb.contract_address);
    // Friday 17:00 UTC
    start_cheat_block_timestamp_global(T0 + 41 * 3600);
    let paid = sb.distribute();
    assert(paid <= 370 * ONE && paid > 369 * ONE, 'pot swept');
    // 30:7 split
    let pa = sb.pending(a);
    let pb = sb.pending(b);
    assert(pa > 299 * ONE && pa <= 300 * ONE, 'a share');
    assert(pb > 69 * ONE && pb <= 70 * ONE, 'b share');
    let (r, s) = signed(sb, 11, ACTION_CLAIM, a, payee().into());
    let got = sb.claim(a, payee(), r, s); // posted by whoever — no caller check
    assert(got == pa, 'claimed');
    assert(tok.balance_of(payee()) == pa, 'paid to recipient');
    assert(sb.pending(a) == 0, 'settled');
}

#[test]
#[should_panic(expected: 'not distribution time')]
fn distribution_waits_for_friday() {
    let (sb, _) = setup();
    stake_as_relayer(sb, pk(11), 0, 0);
    start_cheat_caller_address(sb.contract_address, relayer());
    sb.fund_pot(10 * ONE);
    stop_cheat_caller_address(sb.contract_address);
    start_cheat_block_timestamp_global(T0 + 40 * 3600); // Friday 16:00
    sb.distribute();
}

#[test]
#[should_panic(expected: 'not distribution time')]
fn one_distribution_per_week() {
    let (sb, _) = setup();
    stake_as_relayer(sb, pk(11), 0, 0);
    start_cheat_caller_address(sb.contract_address, relayer());
    sb.fund_pot(10 * ONE);
    stop_cheat_caller_address(sb.contract_address);
    start_cheat_block_timestamp_global(T0 + 41 * 3600);
    sb.distribute();
    start_cheat_block_timestamp_global(T0 + 41 * 3600 + WEEK - 1);
    sb.distribute();
}

#[test]
#[should_panic(expected: 'bad signature')]
fn wrong_key_cannot_claim() {
    let (sb, _) = setup();
    let a = stake_as_relayer(sb, pk(11), 0, 0);
    let (r, s) = signed(sb, 99, ACTION_CLAIM, a, payee().into());
    sb.claim(a, payee(), r, s);
}

#[test]
#[should_panic(expected: 'bad signature')]
fn signature_binds_the_recipient() {
    let (sb, _) = setup();
    let a = stake_as_relayer(sb, pk(11), 0, 0);
    let (r, s) = signed(sb, 11, ACTION_CLAIM, a, payee().into());
    // a relayer swapping in its own address invalidates the signature
    sb.claim(a, relayer(), r, s);
}

#[test]
#[should_panic(expected: 'bad signature')]
fn signatures_do_not_replay() {
    let (sb, _) = setup();
    let a = stake_as_relayer(sb, pk(11), 0, 0);
    let (r, s) = signed(sb, 11, ACTION_CLAIM, a, payee().into());
    sb.claim(a, payee(), r, s);
    sb.claim(a, payee(), r, s);
}

#[test]
#[should_panic(expected: 'still locked')]
fn unstake_waits_for_unlock() {
    let (sb, _) = setup();
    let a = stake_as_relayer(sb, pk(11), 0, 1);
    let (r, s) = signed(sb, 11, ACTION_UNSTAKE, a, payee().into());
    sb.unstake(a, payee(), r, s);
}

#[test]
fn unstake_after_unlock_pays_principal_plus_pot() {
    let (sb, tok) = setup();
    let a = stake_as_relayer(sb, pk(11), 2, 0); // 1000 NAT x 1 day
    start_cheat_caller_address(sb.contract_address, relayer());
    sb.fund_pot(50 * ONE);
    stop_cheat_caller_address(sb.contract_address);
    start_cheat_block_timestamp_global(T0 + 41 * 3600);
    sb.distribute();
    let (r, s) = signed(sb, 11, ACTION_UNSTAKE, a, payee().into());
    let out = sb.unstake(a, payee(), r, s);
    assert(out > 1049 * ONE && out <= 1050 * ONE, 'principal + pot');
    assert(tok.balance_of(payee()) == out, 'paid');
    let st = sb.stats();
    assert(st.open_notes == 0 && st.total_bloc == 0, 'closed');
    assert(sb.bucket_size(2, 0) == 0, 'bucket shrank');
    assert(!sb.get_note(a).open, 'note closed');
}

#[test]
fn rekey_hands_the_note_to_a_new_key() {
    let (sb, _) = setup();
    let a = stake_as_relayer(sb, pk(11), 0, 0);
    let (r, s) = signed(sb, 11, ACTION_REKEY, a, pk(22));
    sb.rekey(a, pk(22), r, s);
    assert(sb.get_note(a).key == pk(22), 'rekeyed');
    // the new key now controls it
    let (r2, s2) = signed(sb, 22, ACTION_CLAIM, a, payee().into());
    sb.claim(a, payee(), r2, s2);
}

#[test]
#[should_panic(expected: 'bad signature')]
fn old_key_is_dead_after_rekey() {
    let (sb, _) = setup();
    let a = stake_as_relayer(sb, pk(11), 0, 0);
    let (r, s) = signed(sb, 11, ACTION_REKEY, a, pk(22));
    sb.rekey(a, pk(22), r, s);
    let (r2, s2) = signed(sb, 11, ACTION_CLAIM, a, payee().into());
    sb.claim(a, payee(), r2, s2);
}

#[test]
#[should_panic(expected: 'only owner')]
fn price_is_owner_only() {
    let (sb, _) = setup();
    sb.set_price(2_000_000);
}
