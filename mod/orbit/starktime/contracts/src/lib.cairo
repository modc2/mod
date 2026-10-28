// StarkTime — StakeTime for Starknet.
//
// Lock STRK (or any ERC-20 set at deploy) for a chosen time. Each position
// gets weight = amount x multiplier(lock_seconds), read off an owner-set
// piecewise-linear curve (basis points, 10000 = 1x). Rewards stream
// pro-rata by weight through a cumulative reward-per-weight index
// (Synthetix-style: the owner funds a stream with notify_reward).
//
// Once a lock expires the position LAPSES to 1x weight, so a long lock cannot
// keep a boosted share forever. Anyone can `poke` an expired position to
// apply the lapse; until someone does, it keeps its boosted weight. (The
// paper ledger in api/src/engine.rs lapses at the exact expiry second.)
//
// Weights and the index are u256 at 1e18 precision. The reward token and the
// stake token are the same, so principal and rewards share one balance.
// notify_reward pulls the funds in first, so the stream never promises more
// than the contract was sent.

pub mod mock_erc20;
pub mod shadowstake;

use starknet::ContractAddress;

#[derive(Copy, Drop, Serde, starknet::Store)]
pub struct Point {
    pub lock_seconds: u64,
    pub multiplier_bps: u64,
}

#[derive(Copy, Drop, Serde, starknet::Store)]
pub struct Position {
    pub owner: ContractAddress,
    pub amount: u256,
    pub start: u64,
    pub lock_seconds: u64,
    pub multiplier_bps: u64,
    pub weight: u256,
    pub debt: u256,
    pub unclaimed: u256,
    pub lapsed: bool,
}

#[starknet::interface]
pub trait IERC20<T> {
    fn transfer(ref self: T, recipient: ContractAddress, amount: u256) -> bool;
    fn transfer_from(
        ref self: T, sender: ContractAddress, recipient: ContractAddress, amount: u256,
    ) -> bool;
}

#[starknet::interface]
pub trait IStarkTime<T> {
    fn stake(ref self: T, amount: u256, lock_seconds: u64) -> u64;
    fn unstake(ref self: T, id: u64);
    fn extend(ref self: T, id: u64, lock_seconds: u64);
    fn claim(ref self: T, id: u64) -> u256;
    fn claim_all(ref self: T) -> u256;
    fn poke(ref self: T, id: u64);
    fn notify_reward(ref self: T, amount: u256, duration: u64);
    fn set_points(ref self: T, points: Array<Point>);
    fn get_position(self: @T, id: u64) -> Position;
    fn positions_of(self: @T, owner: ContractAddress) -> Array<u64>;
    fn pending(self: @T, id: u64) -> u256;
    fn get_multiplier(self: @T, lock_seconds: u64) -> u64;
    fn get_points(self: @T) -> Array<Point>;
    // (total_staked, total_weight, open_positions, reward_rate_per_second)
    fn get_stats(self: @T) -> (u256, u256, u64, u256);
    fn get_reward_period(self: @T) -> (u64, u64);
}

#[starknet::contract]
pub mod StarkTime {
    use core::num::traits::Zero;
    use starknet::storage::{
        Map, StorageMapReadAccess, StorageMapWriteAccess, StoragePointerReadAccess,
        StoragePointerWriteAccess,
    };
    use starknet::{ContractAddress, get_block_timestamp, get_caller_address, get_contract_address};
    use super::{IERC20Dispatcher, IERC20DispatcherTrait, Point, Position};

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
        positions: Map<u64, Position>,
        next_id: u64,
        open_positions: u64,
        owner_ids: Map<(ContractAddress, u32), u64>,
        owner_count: Map<ContractAddress, u32>,
        total_staked: u256,
        total_weight: u256,
        acc: u256, // reward per weight unit, x1e18
        last_update: u64,
        reward_rate: u256, // reward tokens per second
        period_finish: u64,
    }

    #[event]
    #[derive(Drop, starknet::Event)]
    pub enum Event {
        Staked: Staked,
        Unstaked: Unstaked,
        Extended: Extended,
        Claimed: Claimed,
        Lapsed: Lapsed,
        RewardNotified: RewardNotified,
        PointsSet: PointsSet,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Staked {
        #[key]
        pub owner: ContractAddress,
        pub id: u64,
        pub amount: u256,
        pub lock_seconds: u64,
        pub weight: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Unstaked {
        #[key]
        pub owner: ContractAddress,
        pub id: u64,
        pub amount: u256,
        pub rewards: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Extended {
        #[key]
        pub owner: ContractAddress,
        pub id: u64,
        pub lock_seconds: u64,
        pub weight: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Claimed {
        #[key]
        pub owner: ContractAddress,
        pub amount: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct Lapsed {
        pub id: u64,
        pub weight: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct RewardNotified {
        pub amount: u256,
        pub duration: u64,
        pub rate: u256,
    }

    #[derive(Drop, starknet::Event)]
    pub struct PointsSet {
        pub count: u32,
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
        // default curve: 1x flexible -> 6x at four years (clamped to max_lock)
        let defaults: Array<(u64, u64)> = array![
            (0, 10000), (7, 11000), (30, 12500), (90, 15000), (180, 20000), (365, 30000),
            (730, 40000), (1460, 60000),
        ];
        let mut n: u32 = 0;
        for (
            d, m
        ) in defaults {
            if d * DAY <= max_lock {
                self.points.write(n, Point { lock_seconds: d * DAY, multiplier_bps: m });
                n += 1;
            }
        };
        self.point_count.write(n);
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

        fn settle(self: @ContractState, ref p: Position, acc: u256) {
            let accrued = p.weight * acc / PRECISION;
            p.unclaimed = p.unclaimed + accrued - p.debt;
            p.debt = accrued;
        }

        fn load_owned(self: @ContractState, id: u64) -> Position {
            let p = self.positions.read(id);
            assert(p.amount > 0, 'no position');
            assert(p.owner == get_caller_address(), 'not your position');
            p
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

        fn weight_of(self: @ContractState, amount: u256, mult: u64) -> u256 {
            amount * mult.into() / BPS.into()
        }

        fn pay(ref self: ContractState, to: ContractAddress, amount: u256) {
            if amount > 0 {
                let ok = IERC20Dispatcher { contract_address: self.token.read() }
                    .transfer(to, amount);
                assert(ok, 'transfer failed');
            }
        }
    }

    #[abi(embed_v0)]
    impl StarkTimeImpl of super::IStarkTime<ContractState> {
        fn stake(ref self: ContractState, amount: u256, lock_seconds: u64) -> u64 {
            assert(amount > 0, 'amount is zero');
            assert(lock_seconds <= self.max_lock.read(), 'lock exceeds max');
            self.update_index();
            let caller = get_caller_address();
            let mult = self.multiplier(lock_seconds);
            let weight = self.weight_of(amount, mult);
            let acc = self.acc.read();
            let id = self.next_id.read();
            self.next_id.write(id + 1);
            self
                .positions
                .write(
                    id,
                    Position {
                        owner: caller,
                        amount,
                        start: get_block_timestamp(),
                        lock_seconds,
                        multiplier_bps: mult,
                        weight,
                        debt: weight * acc / PRECISION,
                        unclaimed: 0,
                        lapsed: false,
                    },
                );
            let k = self.owner_count.read(caller);
            self.owner_ids.write((caller, k), id);
            self.owner_count.write(caller, k + 1);
            self.open_positions.write(self.open_positions.read() + 1);
            self.total_staked.write(self.total_staked.read() + amount);
            self.total_weight.write(self.total_weight.read() + weight);
            let ok = IERC20Dispatcher { contract_address: self.token.read() }
                .transfer_from(caller, get_contract_address(), amount);
            assert(ok, 'transfer_from failed');
            self.emit(Staked { owner: caller, id, amount, lock_seconds, weight });
            id
        }

        fn unstake(ref self: ContractState, id: u64) {
            let mut p = self.load_owned(id);
            assert(get_block_timestamp() >= p.start + p.lock_seconds, 'still locked');
            self.update_index();
            self.settle(ref p, self.acc.read());
            let (amount, rewards, weight) = (p.amount, p.unclaimed, p.weight);
            p.amount = 0;
            p.weight = 0;
            p.debt = 0;
            p.unclaimed = 0;
            self.positions.write(id, p);
            self.open_positions.write(self.open_positions.read() - 1);
            self.total_staked.write(self.total_staked.read() - amount);
            self.total_weight.write(self.total_weight.read() - weight);
            self.pay(get_caller_address(), amount + rewards);
            self.emit(Unstaked { owner: get_caller_address(), id, amount, rewards });
        }

        fn extend(ref self: ContractState, id: u64, lock_seconds: u64) {
            let mut p = self.load_owned(id);
            assert(lock_seconds <= self.max_lock.read(), 'lock exceeds max');
            let now = get_block_timestamp();
            assert(now + lock_seconds >= p.start + p.lock_seconds, 'cannot shorten lock');
            self.update_index();
            let acc = self.acc.read();
            self.settle(ref p, acc);
            let mult = self.multiplier(lock_seconds);
            let weight = self.weight_of(p.amount, mult);
            self.total_weight.write(self.total_weight.read() - p.weight + weight);
            p.start = now;
            p.lock_seconds = lock_seconds;
            p.multiplier_bps = mult;
            p.weight = weight;
            p.debt = weight * acc / PRECISION;
            p.lapsed = false;
            self.positions.write(id, p);
            self.emit(Extended { owner: p.owner, id, lock_seconds, weight });
        }

        fn claim(ref self: ContractState, id: u64) -> u256 {
            let mut p = self.load_owned(id);
            self.update_index();
            self.settle(ref p, self.acc.read());
            let amount = p.unclaimed;
            p.unclaimed = 0;
            self.positions.write(id, p);
            self.pay(p.owner, amount);
            self.emit(Claimed { owner: p.owner, amount });
            amount
        }

        fn claim_all(ref self: ContractState) -> u256 {
            let caller = get_caller_address();
            self.update_index();
            let acc = self.acc.read();
            let n = self.owner_count.read(caller);
            let mut total: u256 = 0;
            let mut i: u32 = 0;
            while i < n {
                let id = self.owner_ids.read((caller, i));
                let mut p = self.positions.read(id);
                if p.amount > 0 {
                    self.settle(ref p, acc);
                    total += p.unclaimed;
                    p.unclaimed = 0;
                    self.positions.write(id, p);
                }
                i += 1;
            };
            self.pay(caller, total);
            self.emit(Claimed { owner: caller, amount: total });
            total
        }

        // Permissionless: drop an expired position to 1x weight.
        fn poke(ref self: ContractState, id: u64) {
            let mut p = self.positions.read(id);
            assert(p.amount > 0, 'no position');
            assert(!p.lapsed, 'already lapsed');
            assert(get_block_timestamp() >= p.start + p.lock_seconds, 'still locked');
            self.update_index();
            let acc = self.acc.read();
            self.settle(ref p, acc);
            self.total_weight.write(self.total_weight.read() - p.weight + p.amount);
            p.weight = p.amount;
            p.multiplier_bps = BPS;
            p.debt = p.weight * acc / PRECISION;
            p.lapsed = true;
            self.positions.write(id, p);
            self.emit(Lapsed { id, weight: p.weight });
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

        // Applies to new stakes and extends only; open positions keep their
        // multiplier until extended.
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
            self.emit(PointsSet { count: n });
        }

        fn get_position(self: @ContractState, id: u64) -> Position {
            self.positions.read(id)
        }

        fn positions_of(self: @ContractState, owner: ContractAddress) -> Array<u64> {
            let mut out = array![];
            let n = self.owner_count.read(owner);
            let mut i: u32 = 0;
            while i < n {
                let id = self.owner_ids.read((owner, i));
                if self.positions.read(id).amount > 0 {
                    out.append(id);
                }
                i += 1;
            };
            out
        }

        fn pending(self: @ContractState, id: u64) -> u256 {
            let p = self.positions.read(id);
            p.unclaimed + p.weight * self.current_acc() / PRECISION - p.debt
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
            (self.total_staked.read(), self.total_weight.read(), self.open_positions.read(), rate)
        }

        fn get_reward_period(self: @ContractState) -> (u64, u64) {
            (self.last_update.read(), self.period_finish.read())
        }
    }
}
