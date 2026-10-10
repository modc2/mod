use crate::types::{BlocPosition, Point};
use crate::Registry;
use near_sdk::env;

/// BlocTime math — port of orbit/bloctime `BlocTime.sol` onto NEAR.
///
///   bloctime = stake × lockSeconds × multiplier(lockSeconds) / 1e4
///
/// The stake is measured in µNEAR (yocto / 1e18) so that a whole-supply
/// stake locked for the 8-year max on a 10x curve still fits in u128.
/// One BlocTime unit is therefore "1 µNEAR locked for 1 second".
/// The multiplier curve is the same piecewise-linear `Point` list as
/// BlocTime.sol (basis points, 10000 = 1x); the default is one flat 1x
/// point, i.e. pure amount × seconds.
pub const YOCTO_PER_UNIT: u128 = 1_000_000_000_000_000_000; // 1e18 → µNEAR
pub const BPS: u128 = 10_000;
pub const MAX_LOCK_SECONDS: u64 = 252_288_000; // 8 years, BlocTime default

pub fn now_seconds() -> u64 {
    env::block_timestamp() / 1_000_000_000
}

impl Registry {
    pub(crate) fn multiplier(&self, lock_seconds: u64) -> u32 {
        let pts = &self.points;
        if pts.is_empty() {
            return BPS as u32;
        }
        if lock_seconds <= pts[0].lock_seconds {
            return pts[0].multiplier;
        }
        let last = &pts[pts.len() - 1];
        if lock_seconds >= last.lock_seconds {
            return last.multiplier;
        }
        for w in pts.windows(2) {
            let (a, b) = (&w[0], &w[1]);
            if lock_seconds >= a.lock_seconds && lock_seconds <= b.lock_seconds {
                let range = (b.lock_seconds - a.lock_seconds) as u128;
                if range == 0 {
                    return a.multiplier;
                }
                let pos = (lock_seconds - a.lock_seconds) as u128;
                let y = (b.multiplier - a.multiplier) as u128;
                return a.multiplier + (y * pos / range) as u32;
            }
        }
        last.multiplier
    }

    /// `quoteBloc` — BlocTime earned by locking `amount` yocto for `lock_seconds`.
    pub(crate) fn quote(&self, amount: u128, lock_seconds: u64) -> u128 {
        (amount / YOCTO_PER_UNIT)
            .checked_mul(lock_seconds as u128)
            .and_then(|v| v.checked_mul(self.multiplier(lock_seconds) as u128))
            .expect("bloctime overflow")
            / BPS
    }

    /// Smallest deposit (yocto, stake only — excludes account funding) whose
    /// quote at `lock_seconds` reaches `target` BlocTime.
    pub(crate) fn min_stake_for(&self, target: u128, lock_seconds: u64) -> Option<u128> {
        let per_unit = (lock_seconds as u128) * (self.multiplier(lock_seconds) as u128);
        if per_unit == 0 {
            return None;
        }
        // units·per_unit / BPS >= target  ⇒  units >= ceil(target·BPS / per_unit)
        let units = (target.checked_mul(BPS)? + per_unit - 1) / per_unit;
        units.checked_mul(YOCTO_PER_UNIT)
    }

    /// Lock `amount` against `subnet_id` and credit its BlocTime to the subnet.
    pub(crate) fn open_position(
        &mut self,
        subnet_id: u32,
        amount: u128,
        lock_seconds: u64,
        registration: bool,
    ) -> BlocPosition {
        assert!(amount > 0, "amount > 0");
        assert!(lock_seconds <= self.max_lock_seconds, "exceeds max lock");
        let bloctime = self.quote(amount, lock_seconds);
        assert!(bloctime > 0, "lock too short");

        let staker = env::predecessor_account_id();
        let pos = BlocPosition {
            id: self.next_position_id,
            subnet_id,
            staker: staker.clone(),
            amount: amount.into(),
            start_seconds: now_seconds(),
            lock_seconds,
            bloctime: bloctime.into(),
            registration,
        };
        self.next_position_id += 1;
        self.positions.insert(pos.id, pos.clone());

        let mut ids = self.user_positions.get(&staker).cloned().unwrap_or_default();
        ids.push(pos.id);
        self.user_positions.insert(staker, ids);

        let bt = self.subnet_bloctime.get(&subnet_id).copied().unwrap_or(0);
        self.subnet_bloctime.insert(subnet_id, bt + bloctime);
        let locked = self.locked_stake.get(&subnet_id).copied().unwrap_or(0);
        self.locked_stake.insert(subnet_id, locked + amount);
        self.total_bloctime += bloctime;
        pos
    }

    /// `unstake` — only once the lock has run out; burns the position's
    /// BlocTime from the subnet's score and returns the stake amount.
    pub(crate) fn close_position(&mut self, position_id: u64) -> BlocPosition {
        let pos = self.positions.get(&position_id).cloned().expect("no such position");
        assert_eq!(env::predecessor_account_id(), pos.staker, "not your position");
        assert!(
            now_seconds() >= pos.start_seconds + pos.lock_seconds,
            "still locked"
        );
        let (amount, bloctime) = (pos.amount.0, pos.bloctime.0);

        let bt = self.subnet_bloctime.get(&pos.subnet_id).copied().unwrap_or(0);
        self.subnet_bloctime.insert(pos.subnet_id, bt.saturating_sub(bloctime));
        let locked = self.locked_stake.get(&pos.subnet_id).copied().unwrap_or(0);
        self.locked_stake.insert(pos.subnet_id, locked.saturating_sub(amount));
        self.total_bloctime = self.total_bloctime.saturating_sub(bloctime);

        self.positions.remove(&position_id);
        if let Some(ids) = self.user_positions.get(&pos.staker).cloned() {
            let ids: Vec<u64> = ids.into_iter().filter(|i| *i != position_id).collect();
            self.user_positions.insert(pos.staker.clone(), ids);
        }
        pos
    }

    /// Mirrors BlocTime.sol `setPoints` requires.
    pub(crate) fn validate_points(&self, points: &[Point]) {
        assert!(!points.is_empty(), "need >= 1 point");
        for (i, p) in points.iter().enumerate() {
            assert!(p.multiplier as u128 >= BPS, "mult >= 1x");
            assert!(p.lock_seconds <= self.max_lock_seconds, "exceeds max");
            if i > 0 {
                assert!(p.lock_seconds > points[i - 1].lock_seconds, "seconds must increase");
                assert!(p.multiplier >= points[i - 1].multiplier, "mult must increase");
            }
        }
    }
}
