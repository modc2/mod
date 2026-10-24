// ShadowBloc — BlocTime with private ownership, in Cairo.
//
// BlocTime (the EVM contract) mints BLOC to `msg.sender`: anyone can read who
// staked, how much, for how long and what share of the weekly pot they take.
// ShadowBloc keeps the economics (BLOC = USD value x seconds locked, one
// weekly pot paid pro-rata by BLOC, Friday 17:00 UTC) and drops the address:
//
//   * A stake is a NOTE owned by a one-time Stark key that the staker
//     generates in their browser. The contract stores the public key, never a
//     `get_caller_address()`, and no event names an account.
//   * Every later action — claim, unstake, re-key — is authorised by a
//     signature from that key over (action, note, nonce, argument), so the
//     transaction can be posted by ANY account (a relayer) and the staker's
//     own account never touches the contract. The signature binds the
//     recipient, so a relayer or a mempool watcher cannot redirect a payout,
//     and the per-note nonce kills replays.
//   * Amounts and locks come from fixed menus (denominations x lock tiers), so
//     a note is one of many identical notes in its bucket rather than a
//     fingerprint. `bucket_size` exposes how many open notes share a bucket.
//   * `rekey` hands a note to a new key: ownership changes with no address
//     anywhere on chain.
//
// What stays public, on purpose: totals, each note's bucket and BLOC, the
// pot, and the token transfers themselves (who funded a deposit, which
// address a payout went to). The books are auditable note by note; what the
// chain no longer records is which person owns which note. Pay out to a
// fresh address if you do not want the payout linked to you.

use starknet::ContractAddress;

#[derive(Copy, Drop, Serde, starknet::Store, PartialEq, Debug)]
pub struct Note {
    pub key: felt252, // one-time Stark public key — not an account
    pub denom: u32,
    pub tier: u32,
    pub amount: u256,
    pub start: u64,
    pub unlock: u64,
    pub bloc: u256,
    pub debt: u256, // pot index snapshot (acc-per-BLOC)
    pub nonce: u64,
    pub open: bool,
}

#[derive(Copy, Drop, Serde, PartialEq, Debug)]
pub struct Stats {
    pub total_staked: u256,
    pub total_bloc: u256,
    pub open_notes: u64,
    pub note_count: u64,
    pub pot: u256,
    pub distributed: u256,
    pub next_distribution: u64,
    pub price_usd_micro: u64,
    pub now: u64,
}

#[starknet::interface]
pub trait IERC20<T> {
    fn transfer(ref self: T, recipient: ContractAddress, amount: u256) -> bool;
    fn transfer_from(
        ref self: T, sender: ContractAddress, recipient: ContractAddress, amount: u256,
    ) -> bool;
}

#[starknet::interface]
pub trait IShadowBloc<T> {
    // ── stake: whoever pays is not who owns ────────────────────────────
    fn stake(ref self: T, key: felt252, denom: u32, tier: u32) -> u64;
    // ── key-signed actions (any account may submit) ────────────────────
    fn claim(ref self: T, id: u64, recipient: ContractAddress, sig_r: felt252, sig_s: felt252) -> u256;
    fn unstake(
        ref self: T, id: u64, recipient: ContractAddress, sig_r: felt252, sig_s: felt252,
    ) -> u256;
    fn rekey(ref self: T, id: u64, new_key: felt252, sig_r: felt252, sig_s: felt252);
    // ── weekly pot ─────────────────────────────────────────────────────
    fn fund_pot(ref self: T, amount: u256);
    fn distribute(ref self: T) -> u256;
    // ── owner ──────────────────────────────────────────────────────────
    fn set_price(ref self: T, price_usd_micro: u64);
    // ── views ──────────────────────────────────────────────────────────
    fn get_note(self: @T, id: u64) -> Note;
    fn pending(self: @T, id: u64) -> u256;
    fn stats(self: @T) -> Stats;
    fn denoms(self: @T) -> Array<u256>;
    fn tiers(self: @T) -> Array<u64>;
    fn bucket_size(self: @T, denom: u32, tier: u32) -> u64;
    fn quote(self: @T, denom: u32, tier: u32) -> u256;
    fn token(self: @T) -> ContractAddress;
    fn owner(self: @T) -> ContractAddress;
    fn message_hash(self: @T, action: felt252, id: u64, nonce: u64, arg: felt252) -> felt252;
}

pub const ACTION_CLAIM: felt252 = 'claim';
pub const ACTION_UNSTAKE: felt252 = 'unstake';
pub const ACTION_REKEY: felt252 = 'rekey';

#[starknet::contract]
pub mod ShadowBloc {
    use core::ecdsa::check_ecdsa_signature;
    use core::num::traits::Zero;
    use core::poseidon::poseidon_hash_span;
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };
    use starknet::{ContractAddress, get_block_timestamp, get_caller_address, get_contract_address};
    use super::{
        ACTION_CLAIM, ACTION_REKEY, ACTION_UNSTAKE, IERC20Dispatcher, IERC20DispatcherTrait, Note,
        Stats,
    };

    const DOMAIN: felt252 = 'bloctime.shadow.v1';
    const PRECISION: u256 = 1_000_000_000_000_000_000; // 1e18
    const ONE: u256 = 1_000_000_000_000_000_000; // 1 token, 18 decimals
    const DAY: u64 = 86400;
    const WEEK: u64 = 604800;
    // First Friday 17:00 UTC after the unix epoch (1970-01-02, a Friday):
    // the same 12:00 EST payout window BlocTime uses.
    const FRIDAY_1700: u64 = 147600;

    #[storage]
    struct Storage {
        owner: ContractAddress,
        token: ContractAddress,
        price_usd_micro: u64,
        denoms: Map<u32, u256>,
        denom_count: u32,
        tiers: Map<u32, u64>,
        tier_count: u32,
        notes: Map<u64, Note>,
        note_count: u64,
        open_notes: u64,
        buckets: Map<(u32, u32), u64>,
        total_staked: u256,
        total_bloc: u256,
        pot: u256,
        acc: u256,
        distributed: u256,
        last_week: u64,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Staked: Staked,
        Claimed: Claimed,
        Unstaked: Unstaked,
        Rekeyed: Rekeyed,
        PotFunded: PotFunded,
        Distributed: Distributed,
    }

    // Events name notes, never accounts.
    #[derive(Drop, starknet::Event)]
    pub struct Staked {
        #[key]
        pub id: u64,
        pub denom: u32,
        pub tier: u32,
        pub bloc: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Claimed {
        #[key]
        pub id: u64,
        pub amount: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Unstaked {
        #[key]
        pub id: u64,
        pub amount: u256,
        pub rewards: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Rekeyed {
        #[key]
        pub id: u64,
        pub nonce: u64,
    }

    #[derive(Drop, starknet::Event)]
    pub struct PotFunded {
        pub amount: u256,
        pub pot: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Distributed {
        pub week: u64,
        pub amount: u256,
        pub total_bloc: u256,
    }

    #[constructor]
    fn constructor(ref self: ContractState, owner: ContractAddress, token: ContractAddress) {
        assert(!owner.is_zero(), 'owner is zero');
        assert(!token.is_zero(), 'token is zero');
        self.owner.write(owner);
        self.token.write(token);
        self.price_usd_micro.write(1_000_000); // $1
        // denominations: 10 / 100 / 1,000 / 10,000 NAT
        let dd: Array<u256> = array![10 * ONE, 100 * ONE, 1000 * ONE, 10000 * ONE];
        let mut i: u32 = 0;
        for v in dd {
            self.denoms.write(i, v);
            i += 1;
        };
        self.denom_count.write(i);
        // lock tiers: 1 day, 1 week, 30 days, 1 year, 4 years, 8 years
        let tt: Array<u64> = array![DAY, 7 * DAY, 30 * DAY, 365 * DAY, 1460 * DAY, 2920 * DAY];
        let mut j: u32 = 0;
        for v in tt {
            self.tiers.write(j, v);
            j += 1;
        };
        self.tier_count.write(j);
        // the first payout is the first Friday window after deployment
        self.last_week.write(InternalImpl::week_of(get_block_timestamp()));
    }

    #[generate_trait]
    impl InternalImpl of InternalTrait {
        fn week_of(t: u64) -> u64 {
            if t < FRIDAY_1700 {
                0
            } else {
                (t - FRIDAY_1700) / WEEK + 1
            }
        }

        fn denom_value(self: @ContractState, denom: u32) -> u256 {
            assert(denom < self.denom_count.read(), 'bad denom');
            self.denoms.read(denom)
        }

        fn tier_seconds(self: @ContractState, tier: u32) -> u64 {
            assert(tier < self.tier_count.read(), 'bad tier');
            self.tiers.read(tier)
        }

        // BLOC = USD value x seconds locked (BlocTime v2, flat 1x curve)
        fn bloc_for(self: @ContractState, amount: u256, seconds: u64) -> u256 {
            amount * self.price_usd_micro.read().into() * seconds.into() / 1_000_000
        }

        fn digest(
            self: @ContractState, action: felt252, id: u64, nonce: u64, arg: felt252,
        ) -> felt252 {
            poseidon_hash_span(
                array![DOMAIN, get_contract_address().into(), action, id.into(), nonce.into(), arg]
                    .span(),
            )
        }

        // Load an open note and check its key signed (action, id, nonce, arg).
        // Bumps the nonce so the signature can never be replayed.
        fn authorize(
            ref self: ContractState,
            id: u64,
            action: felt252,
            arg: felt252,
            sig_r: felt252,
            sig_s: felt252,
        ) -> Note {
            let mut note = self.notes.read(id);
            assert(note.open, 'no open note');
            let msg = self.digest(action, id, note.nonce, arg);
            assert(check_ecdsa_signature(msg, note.key, sig_r, sig_s), 'bad signature');
            note.nonce += 1;
            note
        }

        fn earned(self: @ContractState, note: @Note) -> u256 {
            *note.bloc * self.acc.read() / PRECISION - *note.debt
        }

        fn pay(ref self: ContractState, to: ContractAddress, amount: u256) {
            if amount > 0 {
                let ok = IERC20Dispatcher { contract_address: self.token.read() }
                    .transfer(to, amount);
                assert(ok, 'transfer failed');
            }
        }

        fn pull(ref self: ContractState, amount: u256) {
            let ok = IERC20Dispatcher { contract_address: self.token.read() }
                .transfer_from(get_caller_address(), get_contract_address(), amount);
            assert(ok, 'transfer_from failed');
        }
    }

    #[abi(embed_v0)]
    impl ShadowBlocImpl of super::IShadowBloc<ContractState> {
        fn stake(ref self: ContractState, key: felt252, denom: u32, tier: u32) -> u64 {
            assert(key != 0, 'key is zero');
            let amount = self.denom_value(denom);
            let seconds = self.tier_seconds(tier);
            let now = get_block_timestamp();
            let bloc = self.bloc_for(amount, seconds);
            let id = self.note_count.read();
            self
                .notes
                .write(
                    id,
                    Note {
                        key,
                        denom,
                        tier,
                        amount,
                        start: now,
                        unlock: now + seconds,
                        bloc,
                        debt: bloc * self.acc.read() / PRECISION,
                        nonce: 0,
                        open: true,
                    },
                );
            self.note_count.write(id + 1);
            self.open_notes.write(self.open_notes.read() + 1);
            self.buckets.write((denom, tier), self.buckets.read((denom, tier)) + 1);
            self.total_staked.write(self.total_staked.read() + amount);
            self.total_bloc.write(self.total_bloc.read() + bloc);
            self.pull(amount);
            self.emit(Staked { id, denom, tier, bloc });
            id
        }

        fn claim(
            ref self: ContractState,
            id: u64,
            recipient: ContractAddress,
            sig_r: felt252,
            sig_s: felt252,
        ) -> u256 {
            assert(!recipient.is_zero(), 'recipient is zero');
            let mut note = self.authorize(id, ACTION_CLAIM, recipient.into(), sig_r, sig_s);
            let amount = self.earned(@note);
            note.debt += amount;
            self.notes.write(id, note);
            self.pay(recipient, amount);
            self.emit(Claimed { id, amount });
            amount
        }

        fn unstake(
            ref self: ContractState,
            id: u64,
            recipient: ContractAddress,
            sig_r: felt252,
            sig_s: felt252,
        ) -> u256 {
            assert(!recipient.is_zero(), 'recipient is zero');
            let mut note = self.authorize(id, ACTION_UNSTAKE, recipient.into(), sig_r, sig_s);
            assert(get_block_timestamp() >= note.unlock, 'still locked');
            let rewards = self.earned(@note);
            let (amount, bloc) = (note.amount, note.bloc);
            note.open = false;
            note.debt += rewards;
            self.notes.write(id, note);
            self.open_notes.write(self.open_notes.read() - 1);
            let b = (note.denom, note.tier);
            self.buckets.write(b, self.buckets.read(b) - 1);
            self.total_staked.write(self.total_staked.read() - amount);
            self.total_bloc.write(self.total_bloc.read() - bloc);
            self.pay(recipient, amount + rewards);
            self.emit(Unstaked { id, amount, rewards });
            amount + rewards
        }

        fn rekey(
            ref self: ContractState, id: u64, new_key: felt252, sig_r: felt252, sig_s: felt252,
        ) {
            assert(new_key != 0, 'key is zero');
            let mut note = self.authorize(id, ACTION_REKEY, new_key, sig_r, sig_s);
            note.key = new_key;
            self.notes.write(id, note);
            self.emit(Rekeyed { id, nonce: note.nonce });
        }

        fn fund_pot(ref self: ContractState, amount: u256) {
            assert(amount > 0, 'amount is zero');
            self.pull(amount);
            let pot = self.pot.read() + amount;
            self.pot.write(pot);
            self.emit(PotFunded { amount, pot });
        }

        // Permissionless, once per week, any time after Friday 17:00 UTC.
        // The whole pot is swept into the acc-per-BLOC index; rounding dust
        // rides to next week, exactly like BlocTime's distributeRewards.
        fn distribute(ref self: ContractState) -> u256 {
            let week = InternalImpl::week_of(get_block_timestamp());
            assert(week > self.last_week.read(), 'not distribution time');
            let total = self.total_bloc.read();
            assert(total > 0, 'nothing staked');
            let pot = self.pot.read();
            let step = pot * PRECISION / total;
            let paid = step * total / PRECISION;
            self.acc.write(self.acc.read() + step);
            self.pot.write(pot - paid);
            self.distributed.write(self.distributed.read() + paid);
            self.last_week.write(week);
            self.emit(Distributed { week, amount: paid, total_bloc: total });
            paid
        }

        fn set_price(ref self: ContractState, price_usd_micro: u64) {
            assert(get_caller_address() == self.owner.read(), 'only owner');
            assert(price_usd_micro > 0, 'price is zero');
            self.price_usd_micro.write(price_usd_micro);
        }

        fn get_note(self: @ContractState, id: u64) -> Note {
            self.notes.read(id)
        }

        fn pending(self: @ContractState, id: u64) -> u256 {
            let note = self.notes.read(id);
            if !note.open {
                return 0;
            }
            self.earned(@note)
        }

        fn stats(self: @ContractState) -> Stats {
            Stats {
                total_staked: self.total_staked.read(),
                total_bloc: self.total_bloc.read(),
                open_notes: self.open_notes.read(),
                note_count: self.note_count.read(),
                pot: self.pot.read(),
                distributed: self.distributed.read(),
                next_distribution: FRIDAY_1700 + self.last_week.read() * WEEK,
                price_usd_micro: self.price_usd_micro.read(),
                now: get_block_timestamp(),
            }
        }

        fn denoms(self: @ContractState) -> Array<u256> {
            let mut out = array![];
            let mut i: u32 = 0;
            while i < self.denom_count.read() {
                out.append(self.denoms.read(i));
                i += 1;
            };
            out
        }

        fn tiers(self: @ContractState) -> Array<u64> {
            let mut out = array![];
            let mut i: u32 = 0;
            while i < self.tier_count.read() {
                out.append(self.tiers.read(i));
                i += 1;
            };
            out
        }

        fn bucket_size(self: @ContractState, denom: u32, tier: u32) -> u64 {
            self.buckets.read((denom, tier))
        }

        fn quote(self: @ContractState, denom: u32, tier: u32) -> u256 {
            self.bloc_for(self.denom_value(denom), self.tier_seconds(tier))
        }

        fn token(self: @ContractState) -> ContractAddress {
            self.token.read()
        }

        fn owner(self: @ContractState) -> ContractAddress {
            self.owner.read()
        }

        fn message_hash(
            self: @ContractState, action: felt252, id: u64, nonce: u64, arg: felt252,
        ) -> felt252 {
            self.digest(action, id, nonce, arg)
        }
    }
}
