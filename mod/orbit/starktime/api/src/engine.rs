//! StarkTime protocol engine — time-locked STRK staking with a lock-length
//! multiplier, streaming emissions and lapse-to-1x on expiry.
//!
//! This is the PAPER ledger: same math as contracts/src/lib.cairo, run
//! off-chain against paper STRK so anyone can try the protocol without a
//! wallet or gas. Every address starts with a paper balance; nothing here
//! moves real funds.
//!
//! Model
//!   weight   = amount × multiplier(lock_seconds)   (piecewise-linear curve, bps)
//!   rewards  = emissions stream pro-rata by weight (acc-per-weight index)
//!   expiry   = position lapses to 1× weight (amount) until unstaked, so a
//!              4-year lock cannot keep earning a 6× share forever
//!   emission = per-epoch rate, geometric decay per epoch, floored
//!
//! Lapses are processed in time order during accrual, so the index is exact:
//! between two consecutive expiries the total weight is constant.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const BPS: f64 = 10_000.0;
pub const DAY: u64 = 86_400;
pub const MAX_POSITIONS_PER_OWNER: usize = 50;
pub const MAX_POSITIONS_TOTAL: usize = 10_000;

#[derive(Clone, Copy, Serialize, Deserialize, Debug)]
pub struct Point {
    pub lock_seconds: u64,
    pub multiplier_bps: u64,
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Params {
    pub max_lock_seconds: u64,
    pub epoch_seconds: u64,
    /// reward emitted in epoch 0 (paper STRK)
    pub emission_per_epoch: f64,
    /// geometric decay per epoch, basis points
    pub decay_bps: u64,
    /// emission never drops below this per epoch
    pub emission_floor: f64,
    pub min_stake: f64,
    /// paper STRK credited to an address the first time it is seen
    pub faucet: f64,
    pub points: Vec<Point>,
}

impl Default for Params {
    fn default() -> Self {
        let p = |d: u64, x: u64| Point { lock_seconds: d * DAY, multiplier_bps: x };
        Params {
            max_lock_seconds: 1460 * DAY,
            epoch_seconds: DAY,
            emission_per_epoch: 10_000.0,
            decay_bps: 50,
            emission_floor: 1_000.0,
            min_stake: 1.0,
            faucet: 10_000.0,
            points: vec![
                p(0, 10_000),
                p(7, 11_000),
                p(30, 12_500),
                p(90, 15_000),
                p(180, 20_000),
                p(365, 30_000),
                p(730, 40_000),
                p(1460, 60_000),
            ],
        }
    }
}

impl Params {
    pub fn validate(&self) -> Result<(), String> {
        if self.points.is_empty() {
            return Err("curve needs at least one point".into());
        }
        for (i, pt) in self.points.iter().enumerate() {
            if pt.multiplier_bps < 10_000 {
                return Err("multiplier must be >= 1x".into());
            }
            if pt.lock_seconds > self.max_lock_seconds {
                return Err("curve point exceeds max lock".into());
            }
            if i > 0 {
                let prev = self.points[i - 1];
                if pt.lock_seconds <= prev.lock_seconds {
                    return Err("lock points must strictly increase".into());
                }
                if pt.multiplier_bps < prev.multiplier_bps {
                    return Err("multiplier must not decrease".into());
                }
            }
        }
        if self.epoch_seconds == 0 {
            return Err("epoch_seconds must be > 0".into());
        }
        Ok(())
    }

    /// Piecewise-linear multiplier in bps, clamped to the curve's ends.
    pub fn multiplier_bps(&self, lock: u64) -> u64 {
        let pts = &self.points;
        if pts.is_empty() {
            return 10_000;
        }
        if lock <= pts[0].lock_seconds {
            return pts[0].multiplier_bps;
        }
        let last = pts[pts.len() - 1];
        if lock >= last.lock_seconds {
            return last.multiplier_bps;
        }
        for w in pts.windows(2) {
            let (a, b) = (w[0], w[1]);
            if lock >= a.lock_seconds && lock <= b.lock_seconds {
                let span = (b.lock_seconds - a.lock_seconds) as u128;
                let rise = (b.multiplier_bps - a.multiplier_bps) as u128;
                return a.multiplier_bps + ((lock - a.lock_seconds) as u128 * rise / span) as u64;
            }
        }
        last.multiplier_bps
    }

    /// Emission for epoch k (decayed, floored).
    pub fn epoch_emission(&self, k: u64) -> f64 {
        let keep = 1.0 - self.decay_bps as f64 / BPS;
        (self.emission_per_epoch * keep.powf(k as f64)).max(self.emission_floor)
    }
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Position {
    pub id: u64,
    pub owner: String,
    #[serde(default)]
    pub validator: Option<String>,
    pub amount: f64,
    pub start: u64,
    pub lock_seconds: u64,
    pub multiplier_bps: u64,
    pub weight: f64,
    /// weight × acc at last settle
    pub debt: f64,
    /// rewards settled into the position but not yet claimed
    pub unclaimed: f64,
    pub lapsed: bool,
}

impl Position {
    pub fn end(&self) -> u64 {
        self.start + self.lock_seconds
    }
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Ledger {
    pub params: Params,
    pub genesis: u64,
    pub last_ts: u64,
    /// cumulative reward per unit of weight
    pub acc: f64,
    pub total_weight: f64,
    pub total_staked: f64,
    pub total_emitted: f64,
    pub total_claimed: f64,
    pub next_id: u64,
    pub positions: BTreeMap<u64, Position>,
    /// paper STRK wallets
    pub wallets: BTreeMap<String, f64>,
    #[serde(default)]
    pub history: Vec<Event>,
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Event {
    pub ts: u64,
    pub kind: String,
    pub owner: String,
    pub id: Option<u64>,
    pub amount: f64,
}

pub type R<T> = Result<T, String>;

pub fn normalize_owner(owner: &str) -> R<String> {
    let s = owner.trim().to_lowercase();
    let hex = s.strip_prefix("0x").ok_or("owner must be a 0x Starknet address")?;
    if hex.is_empty() || hex.len() > 64 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("owner must be a 0x Starknet address (<= 64 hex chars)".into());
    }
    // canonical form: strip leading zeros so 0x0abc == 0xabc
    let t = hex.trim_start_matches('0');
    Ok(format!("0x{}", if t.is_empty() { "0" } else { t }))
}

impl Ledger {
    pub fn new(params: Params, now: u64) -> Self {
        Ledger {
            params,
            genesis: now,
            last_ts: now,
            acc: 0.0,
            total_weight: 0.0,
            total_staked: 0.0,
            total_emitted: 0.0,
            total_claimed: 0.0,
            next_id: 0,
            positions: BTreeMap::new(),
            wallets: BTreeMap::new(),
            history: Vec::new(),
        }
    }

    /// Emission between t0 and t1, integrating the per-epoch rate.
    pub fn emitted_between(&self, t0: u64, t1: u64) -> f64 {
        if t1 <= t0 {
            return 0.0;
        }
        let es = self.params.epoch_seconds;
        let mut t = t0.max(self.genesis);
        let mut total = 0.0;
        while t < t1 {
            let k = (t - self.genesis) / es;
            let epoch_end = self.genesis + (k + 1) * es;
            let seg_end = epoch_end.min(t1);
            total += self.params.epoch_emission(k) * (seg_end - t) as f64 / es as f64;
            t = seg_end;
        }
        total
    }

    pub fn rate_per_second(&self, now: u64) -> f64 {
        let k = now.saturating_sub(self.genesis) / self.params.epoch_seconds;
        self.params.epoch_emission(k) / self.params.epoch_seconds as f64
    }

    pub fn epoch(&self, now: u64) -> u64 {
        now.saturating_sub(self.genesis) / self.params.epoch_seconds
    }

    fn advance(&mut self, to: u64) {
        if to <= self.last_ts {
            return;
        }
        // with nobody staked the stream is not minted (nothing to pay)
        if self.total_weight > 0.0 {
            let e = self.emitted_between(self.last_ts, to);
            self.acc += e / self.total_weight;
            self.total_emitted += e;
        }
        self.last_ts = to;
    }

    fn settle(acc: f64, p: &mut Position) {
        p.unclaimed += p.weight * acc - p.debt;
        p.debt = p.weight * acc;
    }

    /// Bring the index up to `now`, lapsing expired positions in time order.
    pub fn accrue(&mut self, now: u64) {
        loop {
            let next = self
                .positions
                .values()
                .filter(|p| !p.lapsed && p.end() <= now)
                .map(|p| p.end())
                .min();
            let Some(t) = next else { break };
            self.advance(t.max(self.last_ts));
            let acc = self.acc;
            let mut delta = 0.0;
            for p in self.positions.values_mut().filter(|p| !p.lapsed && p.end() <= t) {
                Self::settle(acc, p);
                delta += p.amount - p.weight;
                p.weight = p.amount;
                p.multiplier_bps = 10_000;
                p.debt = p.weight * acc;
                p.lapsed = true;
            }
            self.total_weight += delta;
        }
        self.advance(now);
    }

    pub fn wallet(&mut self, owner: &str) -> f64 {
        let faucet = self.params.faucet;
        *self.wallets.entry(owner.to_string()).or_insert(faucet)
    }

    fn log(&mut self, ts: u64, kind: &str, owner: &str, id: Option<u64>, amount: f64) {
        self.history.push(Event { ts, kind: kind.into(), owner: owner.into(), id, amount });
        if self.history.len() > 500 {
            let cut = self.history.len() - 500;
            self.history.drain(..cut);
        }
    }

    pub fn pending(&self, p: &Position) -> f64 {
        p.unclaimed + p.weight * self.acc - p.debt
    }

    pub fn quote(&self, amount: f64, lock: u64) -> (u64, f64) {
        let m = self.params.multiplier_bps(lock.min(self.params.max_lock_seconds));
        (m, amount * m as f64 / BPS)
    }

    pub fn stake(&mut self, now: u64, owner: &str, amount: f64, lock: u64, validator: Option<String>) -> R<Position> {
        let owner = normalize_owner(owner)?;
        if !amount.is_finite() || amount < self.params.min_stake {
            return Err(format!("amount must be >= {}", self.params.min_stake));
        }
        if lock > self.params.max_lock_seconds {
            return Err(format!("lock exceeds max ({}s)", self.params.max_lock_seconds));
        }
        if self.positions.len() >= MAX_POSITIONS_TOTAL {
            return Err("ledger is full".into());
        }
        if self.positions.values().filter(|p| p.owner == owner).count() >= MAX_POSITIONS_PER_OWNER {
            return Err(format!("max {MAX_POSITIONS_PER_OWNER} positions per address"));
        }
        let validator = match validator.map(|v| v.trim().to_string()).filter(|v| !v.is_empty()) {
            Some(v) => Some(normalize_owner(&v).map_err(|_| "validator must be a 0x Starknet address")?),
            None => None,
        };
        self.accrue(now);
        let bal = self.wallet(&owner);
        if bal + 1e-9 < amount {
            return Err(format!("insufficient paper STRK ({bal:.4} available)"));
        }
        let (m, weight) = self.quote(amount, lock);
        let id = self.next_id;
        self.next_id += 1;
        let pos = Position {
            id,
            owner: owner.clone(),
            validator,
            amount,
            start: now,
            lock_seconds: lock,
            multiplier_bps: m,
            weight,
            debt: weight * self.acc,
            unclaimed: 0.0,
            lapsed: false,
        };
        *self.wallets.get_mut(&owner).unwrap() -= amount;
        self.total_weight += weight;
        self.total_staked += amount;
        self.positions.insert(id, pos.clone());
        self.log(now, "stake", &owner, Some(id), amount);
        // a zero-length lock is already expired: lapse it now (1x anyway)
        self.accrue(now);
        Ok(self.positions[&id].clone())
    }

    fn owned(&self, owner: &str, id: u64) -> R<String> {
        let owner = normalize_owner(owner)?;
        match self.positions.get(&id) {
            None => Err(format!("no position #{id}")),
            Some(p) if p.owner != owner => Err("not your position".into()),
            Some(_) => Ok(owner),
        }
    }

    /// Returns (principal, rewards) paid back to the paper wallet.
    pub fn unstake(&mut self, now: u64, owner: &str, id: u64) -> R<(f64, f64)> {
        let owner = self.owned(owner, id)?;
        self.accrue(now);
        let p = &self.positions[&id];
        if now < p.end() {
            return Err(format!("still locked for {}s", p.end() - now));
        }
        let mut p = self.positions.remove(&id).unwrap();
        Self::settle(self.acc, &mut p);
        self.total_weight -= p.weight;
        if self.total_weight < 1e-9 {
            self.total_weight = 0.0;
        }
        self.total_staked -= p.amount;
        self.total_claimed += p.unclaimed;
        *self.wallets.entry(owner.clone()).or_insert(0.0) += p.amount + p.unclaimed;
        self.log(now, "unstake", &owner, Some(id), p.amount);
        Ok((p.amount, p.unclaimed))
    }

    /// Re-lock from now; the new end may not be earlier than the old one.
    pub fn extend(&mut self, now: u64, owner: &str, id: u64, lock: u64) -> R<Position> {
        self.owned(owner, id)?;
        if lock > self.params.max_lock_seconds {
            return Err(format!("lock exceeds max ({}s)", self.params.max_lock_seconds));
        }
        self.accrue(now);
        let acc = self.acc;
        let m = self.params.multiplier_bps(lock);
        let p = self.positions.get_mut(&id).unwrap();
        if now + lock < p.end() {
            return Err("new lock must end at or after the current one".into());
        }
        Self::settle(acc, p);
        let new_weight = p.amount * m as f64 / BPS;
        let delta = new_weight - p.weight;
        p.start = now;
        p.lock_seconds = lock;
        p.multiplier_bps = m;
        p.weight = new_weight;
        p.debt = new_weight * acc;
        p.lapsed = false;
        let (owner, amount) = (p.owner.clone(), p.amount);
        self.total_weight += delta;
        self.log(now, "extend", &owner, Some(id), amount);
        self.accrue(now);
        Ok(self.positions[&id].clone())
    }

    pub fn claim(&mut self, now: u64, owner: &str) -> R<f64> {
        let owner = normalize_owner(owner)?;
        self.accrue(now);
        let acc = self.acc;
        let mut paid = 0.0;
        for p in self.positions.values_mut().filter(|p| p.owner == owner) {
            Self::settle(acc, p);
            paid += p.unclaimed;
            p.unclaimed = 0.0;
        }
        self.wallet(&owner);
        *self.wallets.get_mut(&owner).unwrap() += paid;
        self.total_claimed += paid;
        if paid > 0.0 {
            self.log(now, "claim", &owner, None, paid);
        }
        Ok(paid)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const A: &str = "0xaaa";
    const B: &str = "0xbbb";

    fn ledger() -> Ledger {
        Ledger::new(Params::default(), 1_000_000)
    }

    #[test]
    fn curve_interpolates_and_clamps() {
        let p = Params::default();
        assert_eq!(p.multiplier_bps(0), 10_000);
        assert_eq!(p.multiplier_bps(365 * DAY), 30_000);
        assert_eq!(p.multiplier_bps(9_999 * DAY), 60_000);
        // halfway between 180d (2x) and 365d (3x)
        let mid = p.multiplier_bps((180 + 365) * DAY / 2);
        assert!(mid > 24_900 && mid < 25_100, "{mid}");
        assert!(p.validate().is_ok());
    }

    #[test]
    fn rewards_split_by_weight() {
        let mut l = ledger();
        let t = l.genesis;
        l.stake(t, A, 1000.0, 365 * DAY, None).unwrap(); // weight 3000
        l.stake(t, B, 1000.0, 0, None).unwrap(); // weight 1000 (lapsed at once)
        l.accrue(t + DAY);
        let pa = l.pending(&l.positions[&0]);
        let pb = l.pending(&l.positions[&1]);
        let total = pa + pb;
        assert!((total - 10_000.0).abs() < 1e-6, "one epoch emits 10k: {total}");
        assert!((pa / pb - 3.0).abs() < 1e-9, "3x weight earns 3x: {pa} {pb}");
    }

    #[test]
    fn lapse_drops_to_one_x() {
        let mut l = ledger();
        let t = l.genesis;
        l.stake(t, A, 1000.0, 7 * DAY, None).unwrap(); // 1.1x for a week
        l.stake(t, B, 1000.0, 0, None).unwrap();
        l.accrue(t + 30 * DAY);
        assert!(l.positions[&0].lapsed);
        assert!((l.total_weight - 2000.0).abs() < 1e-9);
        // claim pays everything emitted
        let a = l.claim(t + 30 * DAY, A).unwrap();
        let b = l.claim(t + 30 * DAY, B).unwrap();
        assert!((a + b - l.total_emitted).abs() < 1e-6);
        assert!(a > b);
    }

    #[test]
    fn locked_cannot_unstake_then_can() {
        let mut l = ledger();
        let t = l.genesis;
        l.stake(t, A, 500.0, 30 * DAY, None).unwrap();
        assert!(l.unstake(t + DAY, A, 0).is_err());
        assert!(l.unstake(t + 31 * DAY, B, 0).is_err(), "not owner");
        let (principal, rewards) = l.unstake(t + 31 * DAY, A, 0).unwrap();
        assert_eq!(principal, 500.0);
        assert!(rewards > 0.0);
        assert!(l.total_weight == 0.0 && l.positions.is_empty());
    }

    #[test]
    fn emission_decays_with_floor() {
        let l = ledger();
        assert!((l.params.epoch_emission(0) - 10_000.0).abs() < 1e-9);
        assert!(l.params.epoch_emission(1) < 10_000.0);
        assert!((l.params.epoch_emission(100_000) - 1_000.0).abs() < 1e-9);
        let two = l.emitted_between(l.genesis, l.genesis + 2 * DAY);
        assert!((two - (10_000.0 + 9_950.0)).abs() < 1e-6, "{two}");
    }

    #[test]
    fn extend_raises_weight() {
        let mut l = ledger();
        let t = l.genesis;
        l.stake(t, A, 100.0, 30 * DAY, None).unwrap();
        assert!(l.extend(t, A, 0, 7 * DAY).is_err(), "cannot shorten");
        let p = l.extend(t + DAY, A, 0, 365 * DAY).unwrap();
        assert_eq!(p.multiplier_bps, 30_000);
        assert!((l.total_weight - 300.0).abs() < 1e-9);
    }

    #[test]
    fn owners_are_canonical_and_wallets_debit() {
        let mut l = ledger();
        let t = l.genesis;
        assert!(normalize_owner("nope").is_err());
        assert_eq!(normalize_owner("0x000ABC").unwrap(), "0xabc");
        assert!(l.stake(t, A, 20_000.0, 0, None).is_err(), "faucet is 10k");
        l.stake(t, "0x0AAA", 4_000.0, DAY, None).unwrap();
        assert!((l.wallets["0xaaa"] - 6_000.0).abs() < 1e-9);
    }
}
