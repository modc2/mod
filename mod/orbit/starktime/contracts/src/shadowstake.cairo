// ShadowStake — privacy-preserving StakeTime for Starknet.
//
// This is the "staketime" subnet system (mods + time-weighted stake) ported to
// Starknet with one new property: the chain never learns WHO owns a mod or WHO
// owns a stake. No account address is ever written to storage or an event.
// Identity is knowledge of a secret, not a `get_caller_address()`.
//
// Two privacy surfaces:
//
//   1. Private stake pool. A stake is a NOTE identified only by a Poseidon
//      commitment c = H(secret, nullifier_secret, denom_index, lock_seconds,
//      mod_id). The depositor transfers one fixed DENOMINATION of the token in
//      and the contract records weight = amount x multiplier(lock) against the
//      commitment — never against an address. Rewards stream pro-rata by weight
//      (Synthetix acc-per-weight index, same math as StarkTime). To claim or
//      unstake you re-present the preimage, the contract recomputes c, settles
//      the note and pays a RECIPIENT you name at spend time (a fresh address or
//      a relayer), so the payout is not tied to the depositor.
//
//   2. Private mod registry. A mod/subnet is registered under an
//      owner_commitment oc = H(owner_secret, salt) and public metadata only —
//      never the registrant's address. Owner actions re-present the preimage and
//      ROTATE to a fresh commitment, so the revealed secret is burned each use.
//
// Honest guarantees (MVP, no SNARK): nobody can read an owner address from
// chain state, and an account's TOTAL stake is unlinkable across its notes
// (set anonymity across equal-denomination notes). What is still visible:
// individual note denominations, and — because a spend re-presents the
// commitment — the link between one deposit tx and its own later spend. Hiding
// that link (unlinkable withdraw) and hiding amounts below denomination
// granularity needs a ZK membership proof; the commitment accumulator + the
// nullifier plumbing here are the hooks a garaga/Noir verifier plugs into in
// phase 2. See PRIVACY.md for the threat model and roadmap.

use starknet::ContractAddress;

#[derive(Copy, Drop, Serde, starknet::Store)]
pub struct Note {
    pub denom_index: u32,
    pub amount: u256,
    pub start: u64,
    pub lock_seconds: u64,
    pub weight: u256,
    pub debt: u256,
    pub unclaimed: u256,
    pub mod_id: u64, // 0 = unassigned
    pub spent: bool,
    pub exists: bool,
}

#[derive(Copy, Drop, Serde, starknet::Store)]
pub struct ModEntry {
    pub owner_commitment: felt252,
    pub metadata_hash: felt252,
    pub weight: u256, // sum of note weights targeting this mod
    pub exists: bool,
}

#[derive(Copy, Drop, Serde, starknet::Store)]
pub struct Point {
    pub lock_seconds: u64,
    pub multiplier_bps: u64,
}

#[starknet::interface]
pub trait IERC20<T> {
    fn transfer(ref self: T, recipient: ContractAddress, amount: u256) -> bool;
    fn transfer_from(
        ref self: T, sender: ContractAddress, recipient: ContractAddress, amount: u256,
    ) -> bool;
}

#[starknet::interface]
pub trait IShadowStake<T> {
    // ── private stake pool ──────────────────────────────────────────────
    fn stake(ref self: T, commitment: felt252, denom_index: u32, lock_seconds: u64, mod_id: u64);
    fn claim(
        ref self: T,
        secret: felt252,
        nullifier_secret: felt252,
        denom_index: u32,
        lock_seconds: u64,
        mod_id: u64,
        recipient: ContractAddress,
    ) -> u256;
    fn unstake(
        ref self: T,
        secret: felt252,
        nullifier_secret: felt252,
        denom_index: u32,
        lock_seconds: u64,
        mod_id: u64,
        recipient: ContractAddress,
    ) -> u256;
    fn notify_reward(ref self: T, amount: u256, duration: u64);
    // ── private mod registry ────────────────────────────────────────────
    fn register_mod(ref self: T, owner_commitment: felt252, metadata_hash: felt252) -> u64;
    fn update_mod(
        ref self: T,
        mod_id: u64,
        owner_secret: felt252,
        salt: felt252,
        new_owner_commitment: felt252,
        metadata_hash: felt252,
    );
    // ── config (owner) ──────────────────────────────────────────────────
    fn set_denoms(ref self: T, denoms: Array<u256>);
    fn set_points(ref self: T, points: Array<Point>);
    // ── views ───────────────────────────────────────────────────────────
    fn get_note(self: @T, commitment: felt252) -> Note;
    fn note_exists(self: @T, commitment: felt252) -> bool;
    fn is_nullified(self: @T, nullifier: felt252) -> bool;
    fn get_note_root(self: @T) -> felt252;
    fn get_mod(self: @T, mod_id: u64) -> ModEntry;
    fn mod_count(self: @T) -> u64;
    fn pending(self: @T, commitment: felt252) -> u256;
    fn get_denoms(self: @T) -> Array<u256>;
    fn get_multiplier(self: @T, lock_seconds: u64) -> u64;
    fn get_points(self: @T) -> Array<Point>;
    // (total_staked, total_weight, open_notes, reward_rate_per_second)
    fn get_stats(self: @T) -> (u256, u256, u64, u256);
    fn compute_commitment(
        self: @T,
        secret: felt252,
        nullifier_secret: felt252,
        denom_index: u32,
        lock_seconds: u64,
        mod_id: u64,
    ) -> felt252;
    fn compute_nullifier(self: @T, nullifier_secret: felt252, commitment: felt252) -> felt252;
    fn compute_mod_commitment(self: @T, owner_secret: felt252, salt: felt252) -> felt252;
}

#[starknet::contract]
pub mod ShadowStake {
    use core::num::traits::Zero;
    use core::poseidon::poseidon_hash_span;
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };
    use starknet::{ContractAddress, get_block_timestamp, get_caller_address, get_contract_address};
    use super::{IERC20Dispatcher, IERC20DispatcherTrait, ModEntry, Note, Point};

    const BPS: u64 = 10000;
    const DAY: u64 = 86400;
    const PRECISION: u256 = 1_000_000_000_000_000_000; // 1e18

    #[storage]
    struct Storage {
        owner: ContractAddress,
        token: ContractAddress,
        max_lock: u64,
        points: Map<u32, Point>,
        point_count: u32,
        denoms: Map<u32, u256>,
        denom_count: u32,
        // notes keyed only by commitment — never by address
        notes: Map<felt252, Note>,
        nullifiers: Map<felt252, bool>,
        open_notes: u64,
        // append-only commitment accumulator (hash chain). Phase 2 swaps this
        // for a sparse Merkle tree so membership can be proven in ZK.
        note_root: felt252,
        // private mod registry
        mods: Map<u64, ModEntry>,
        next_mod_id: u64,
        // reward streaming (Synthetix acc-per-weight)
        total_staked: u256,
        total_weight: u256,
        acc: u256,
        last_update: u64,
        reward_rate: u256,
        period_finish: u64,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Deposit: Deposit,
        Withdraw: Withdraw,
        Claimed: Claimed,
        RewardNotified: RewardNotified,
        ModRegistered: ModRegistered,
        ModUpdated: ModUpdated,
    }

    // Note: events carry commitments / mod ids only. No owner address.
    #[derive(Drop, starknet::Event)]
    pub struct Deposit {
        #[key]
        pub commitment: felt252,
        pub denom_index: u32,
        pub lock_seconds: u64,
        pub weight: u256,
        pub mod_id: u64,
        pub note_root: felt252,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Withdraw {
        #[key]
        pub nullifier: felt252,
        pub amount: u256,
        pub rewards: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Claimed {
        #[key]
        pub commitment: felt252,
        pub amount: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct RewardNotified {
        pub amount: u256,
        pub duration: u64,
        pub rate: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct ModRegistered {
        #[key]
        pub mod_id: u64,
        pub owner_commitment: felt252,
        pub metadata_hash: felt252,
    }

    #[derive(Drop, starknet::Event)]
    pub struct ModUpdated {
        #[key]
        pub mod_id: u64,
        pub new_owner_commitment: felt252,
        pub metadata_hash: felt252,
    }

    #[constructor]
    fn constructor(
        ref self: ContractState, owner: ContractAddress, token: ContractAddress, max_lock: u64,
    ) {
        assert(!owner.is_zero(), 'owner is zero');
        assert(!token.is_zero(), 'token is zero');
        self.owner.write(owner);
        self.token.write(token);
        self.max_lock.write(max_lock);
        self.next_mod_id.write(1);
        // default curve: 1x flexible -> 6x at four years (clamped to max_lock)
        let defaults: Array<(u64, u64)> = array![
            (0, 10000), (7, 11000), (30, 12500), (90, 15000), (180, 20000), (365, 30000),
            (730, 40000), (1460, 60000),
        ];
        let mut n: u32 = 0;
        for (d, m) in defaults {
            if d * DAY <= max_lock {
                self.points.write(n, Point { lock_seconds: d * DAY, multiplier_bps: m });
                n += 1;
            }
        };
        self.point_count.write(n);
        // default denominations: 1, 10, 100, 1000 tokens (1e18 base)
        let dd: Array<u256> = array![
            PRECISION, 10 * PRECISION, 100 * PRECISION, 1000 * PRECISION,
        ];
        let mut i: u32 = 0;
        for v in dd {
            self.denoms.write(i, v);
            i += 1;
        };
        self.denom_count.write(i);
    }

    #[generate_trait]
    impl Internal of InternalTrait {
        fn only_owner(self: @ContractState) {
            assert(get_caller_address() == self.owner.read(), 'only owner');
        }

        fn last_applicable(self: @ContractState) -> u64 {
            let now = get_block_timestamp();
            let finish = self.period_finish.read();
            if now < finish {
                now
            } else {
                finish
            }
        }

        fn current_acc(self: @ContractState) -> u256 {
            let tw = self.total_weight.read();
            let last = self.last_update.read();
            let t = self.last_applicable();
            if tw == 0 || t <= last {
                return self.acc.read();
            }
            let dt: u256 = (t - last).into();
            self.acc.read() + self.reward_rate.read() * dt * PRECISION / tw
        }

        fn update_index(ref self: ContractState) {
            let acc = self.current_acc();
            self.acc.write(acc);
            let t = self.last_applicable();
            if t > self.last_update.read() {
                self.last_update.write(t);
            }
        }

        fn settle(self: @ContractState, ref note: Note, acc: u256) {
            let accrued = note.weight * acc / PRECISION;
            note.unclaimed = note.unclaimed + accrued - note.debt;
            note.debt = accrued;
        }

        fn multiplier(self: @ContractState, lock: u64) -> u64 {
            let n = self.point_count.read();
            if n == 0 {
                return BPS;
            }
            let first = self.points.read(0);
            if lock <= first.lock_seconds {
                return first.multiplier_bps;
            }
            let last = self.points.read(n - 1);
            if lock >= last.lock_seconds {
                return last.multiplier_bps;
            }
            let mut i: u32 = 0;
            let mut out = last.multiplier_bps;
            while i + 1 < n {
                let a = self.points.read(i);
                let b = self.points.read(i + 1);
                if lock >= a.lock_seconds && lock <= b.lock_seconds {
                    let span: u128 = (b.lock_seconds - a.lock_seconds).into();
                    let rise: u128 = (b.multiplier_bps - a.multiplier_bps).into();
                    let off: u128 = (lock - a.lock_seconds).into();
                    let add: u64 = (off * rise / span).try_into().unwrap();
                    out = a.multiplier_bps + add;
                    break;
                }
                i += 1;
            };
            out
        }

        fn denom_value(self: @ContractState, index: u32) -> u256 {
            assert(index < self.denom_count.read(), 'bad denom');
            self.denoms.read(index)
        }

        fn commitment_of(
            self: @ContractState,
            secret: felt252,
            nullifier_secret: felt252,
            denom_index: u32,
            lock_seconds: u64,
            mod_id: u64,
        ) -> felt252 {
            poseidon_hash_span(
                array![
                    secret,
                    nullifier_secret,
                    denom_index.into(),
                    lock_seconds.into(),
                    mod_id.into(),
                ]
                    .span(),
            )
        }

        fn nullifier_of(
            self: @ContractState, nullifier_secret: felt252, commitment: felt252,
        ) -> felt252 {
            poseidon_hash_span(array![nullifier_secret, commitment].span())
        }

        fn pay(ref self: ContractState, to: ContractAddress, amount: u256) {
            if amount > 0 {
                let ok = IERC20Dispatcher { contract_address: self.token.read() }
                    .transfer(to, amount);
                assert(ok, 'transfer failed');
            }
        }

        fn add_mod_weight(ref self: ContractState, mod_id: u64, delta: u256, add: bool) {
            if mod_id == 0 {
                return;
            }
            let mut m = self.mods.read(mod_id);
            if !m.exists {
                return;
            }
            m.weight = if add {
                m.weight + delta
            } else {
                m.weight - delta
            };
            self.mods.write(mod_id, m);
        }
    }

    #[abi(embed_v0)]
    impl ShadowStakeImpl of super::IShadowStake<ContractState> {
        fn stake(
            ref self: ContractState,
            commitment: felt252,
            denom_index: u32,
            lock_seconds: u64,
            mod_id: u64,
        ) {
            assert(commitment != 0, 'commitment is zero');
            assert(!self.notes.read(commitment).exists, 'commitment used');
            assert(lock_seconds <= self.max_lock.read(), 'lock exceeds max');
            if mod_id != 0 {
                assert(self.mods.read(mod_id).exists, 'no such mod');
            }
            let amount = self.denom_value(denom_index);
            self.update_index();
            let mult = self.multiplier(lock_seconds);
            let weight = amount * mult.into() / BPS.into();
            let acc = self.acc.read();
            let note = Note {
                denom_index,
                amount,
                start: get_block_timestamp(),
                lock_seconds,
                weight,
                debt: weight * acc / PRECISION,
                unclaimed: 0,
                mod_id,
                spent: false,
                exists: true,
            };
            self.notes.write(commitment, note);
            self.open_notes.write(self.open_notes.read() + 1);
            self.total_staked.write(self.total_staked.read() + amount);
            self.total_weight.write(self.total_weight.read() + weight);
            self.add_mod_weight(mod_id, weight, true);
            // extend the commitment accumulator
            let root = poseidon_hash_span(array![self.note_root.read(), commitment].span());
            self.note_root.write(root);
            // pull the stake in (caller funds; recipient on the way out can differ)
            let ok = IERC20Dispatcher { contract_address: self.token.read() }
                .transfer_from(get_caller_address(), get_contract_address(), amount);
            assert(ok, 'transfer_from failed');
            self
                .emit(
                    Deposit {
                        commitment, denom_index, lock_seconds, weight, mod_id, note_root: root,
                    },
                );
        }

        fn claim(
            ref self: ContractState,
            secret: felt252,
            nullifier_secret: felt252,
            denom_index: u32,
            lock_seconds: u64,
            mod_id: u64,
            recipient: ContractAddress,
        ) -> u256 {
            assert(!recipient.is_zero(), 'recipient is zero');
            let c = self
                .commitment_of(secret, nullifier_secret, denom_index, lock_seconds, mod_id);
            let mut note = self.notes.read(c);
            assert(note.exists && !note.spent, 'no note');
            self.update_index();
            self.settle(ref note, self.acc.read());
            let amount = note.unclaimed;
            note.unclaimed = 0;
            self.notes.write(c, note);
            self.pay(recipient, amount);
            self.emit(Claimed { commitment: c, amount });
            amount
        }

        fn unstake(
            ref self: ContractState,
            secret: felt252,
            nullifier_secret: felt252,
            denom_index: u32,
            lock_seconds: u64,
            mod_id: u64,
            recipient: ContractAddress,
        ) -> u256 {
            assert(!recipient.is_zero(), 'recipient is zero');
            let c = self
                .commitment_of(secret, nullifier_secret, denom_index, lock_seconds, mod_id);
            let mut note = self.notes.read(c);
            assert(note.exists && !note.spent, 'no note');
            assert(get_block_timestamp() >= note.start + note.lock_seconds, 'still locked');
            let nullifier = self.nullifier_of(nullifier_secret, c);
            assert(!self.nullifiers.read(nullifier), 'nullified');
            self.update_index();
            self.settle(ref note, self.acc.read());
            let (amount, rewards, weight) = (note.amount, note.unclaimed, note.weight);
            note.spent = true;
            note.unclaimed = 0;
            self.notes.write(c, note);
            self.nullifiers.write(nullifier, true);
            self.open_notes.write(self.open_notes.read() - 1);
            self.total_staked.write(self.total_staked.read() - amount);
            self.total_weight.write(self.total_weight.read() - weight);
            self.add_mod_weight(mod_id, weight, false);
            self.pay(recipient, amount + rewards);
            self.emit(Withdraw { nullifier, amount, rewards });
            amount + rewards
        }

        fn notify_reward(ref self: ContractState, amount: u256, duration: u64) {
            self.only_owner();
            assert(duration > 0, 'duration is zero');
            self.update_index();
            let now = get_block_timestamp();
            let finish = self.period_finish.read();
            let mut total = amount;
            if now < finish {
                let left: u256 = (finish - now).into();
                total += left * self.reward_rate.read();
            }
            let rate = total / duration.into();
            assert(rate > 0, 'rate is zero');
            self.reward_rate.write(rate);
            self.last_update.write(now);
            self.period_finish.write(now + duration);
            let ok = IERC20Dispatcher { contract_address: self.token.read() }
                .transfer_from(get_caller_address(), get_contract_address(), amount);
            assert(ok, 'transfer_from failed');
            self.emit(RewardNotified { amount, duration, rate });
        }

        fn register_mod(
            ref self: ContractState, owner_commitment: felt252, metadata_hash: felt252,
        ) -> u64 {
            assert(owner_commitment != 0, 'commitment is zero');
            let id = self.next_mod_id.read();
            self.next_mod_id.write(id + 1);
            self
                .mods
                .write(id, ModEntry { owner_commitment, metadata_hash, weight: 0, exists: true });
            self.emit(ModRegistered { mod_id: id, owner_commitment, metadata_hash });
            id
        }

        fn update_mod(
            ref self: ContractState,
            mod_id: u64,
            owner_secret: felt252,
            salt: felt252,
            new_owner_commitment: felt252,
            metadata_hash: felt252,
        ) {
            assert(new_owner_commitment != 0, 'commitment is zero');
            let mut m = self.mods.read(mod_id);
            assert(m.exists, 'no such mod');
            let oc = poseidon_hash_span(array![owner_secret, salt].span());
            assert(oc == m.owner_commitment, 'bad owner proof');
            // rotate: the revealed secret is now burned
            m.owner_commitment = new_owner_commitment;
            m.metadata_hash = metadata_hash;
            self.mods.write(mod_id, m);
            self.emit(ModUpdated { mod_id, new_owner_commitment, metadata_hash });
        }

        fn set_denoms(ref self: ContractState, denoms: Array<u256>) {
            self.only_owner();
            let n = denoms.len();
            assert(n > 0, 'need a denom');
            let mut i: u32 = 0;
            while i < n {
                let v = *denoms.at(i);
                assert(v > 0, 'denom is zero');
                self.denoms.write(i, v);
                i += 1;
            };
            self.denom_count.write(n);
        }

        fn set_points(ref self: ContractState, points: Array<Point>) {
            self.only_owner();
            let n = points.len();
            assert(n > 0, 'need a point');
            let max = self.max_lock.read();
            let mut i: u32 = 0;
            while i < n {
                let p = *points.at(i);
                assert(p.multiplier_bps >= BPS, 'multiplier below 1x');
                assert(p.lock_seconds <= max, 'point exceeds max lock');
                if i > 0 {
                    let q = *points.at(i - 1);
                    assert(p.lock_seconds > q.lock_seconds, 'locks must increase');
                    assert(p.multiplier_bps >= q.multiplier_bps, 'multiplier decreased');
                }
                self.points.write(i, p);
                i += 1;
            };
            self.point_count.write(n);
        }

        fn get_note(self: @ContractState, commitment: felt252) -> Note {
            self.notes.read(commitment)
        }

        fn note_exists(self: @ContractState, commitment: felt252) -> bool {
            self.notes.read(commitment).exists
        }

        fn is_nullified(self: @ContractState, nullifier: felt252) -> bool {
            self.nullifiers.read(nullifier)
        }

        fn get_note_root(self: @ContractState) -> felt252 {
            self.note_root.read()
        }

        fn get_mod(self: @ContractState, mod_id: u64) -> ModEntry {
            self.mods.read(mod_id)
        }

        fn mod_count(self: @ContractState) -> u64 {
            self.next_mod_id.read() - 1
        }

        fn pending(self: @ContractState, commitment: felt252) -> u256 {
            let note = self.notes.read(commitment);
            if !note.exists || note.spent {
                return 0;
            }
            note.unclaimed + note.weight * self.current_acc() / PRECISION - note.debt
        }

        fn get_denoms(self: @ContractState) -> Array<u256> {
            let mut out = array![];
            let n = self.denom_count.read();
            let mut i: u32 = 0;
            while i < n {
                out.append(self.denoms.read(i));
                i += 1;
            };
            out
        }

        fn get_multiplier(self: @ContractState, lock_seconds: u64) -> u64 {
            self.multiplier(lock_seconds)
        }

        fn get_points(self: @ContractState) -> Array<Point> {
            let mut out = array![];
            let n = self.point_count.read();
            let mut i: u32 = 0;
            while i < n {
                out.append(self.points.read(i));
                i += 1;
            };
            out
        }

        fn get_stats(self: @ContractState) -> (u256, u256, u64, u256) {
            let rate = if get_block_timestamp() < self.period_finish.read() {
                self.reward_rate.read()
            } else {
                0
            };
            (self.total_staked.read(), self.total_weight.read(), self.open_notes.read(), rate)
        }

        fn compute_commitment(
            self: @ContractState,
            secret: felt252,
            nullifier_secret: felt252,
            denom_index: u32,
            lock_seconds: u64,
            mod_id: u64,
        ) -> felt252 {
            self.commitment_of(secret, nullifier_secret, denom_index, lock_seconds, mod_id)
        }

        fn compute_nullifier(
            self: @ContractState, nullifier_secret: felt252, commitment: felt252,
        ) -> felt252 {
            self.nullifier_of(nullifier_secret, commitment)
        }

        fn compute_mod_commitment(
            self: @ContractState, owner_secret: felt252, salt: felt252,
        ) -> felt252 {
            poseidon_hash_span(array![owner_secret, salt].span())
        }
    }
}
