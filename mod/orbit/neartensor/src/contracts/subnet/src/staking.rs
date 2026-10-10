use near_sdk::json_types::U128;
use near_sdk::store::Vector;
use near_sdk::{env, AccountId, NearToken, Promise};

use crate::types::*;
use crate::Subnet;

impl Subnet {
    // ── Validator Registration ───────────────────────────────────────────────

    pub fn register_validator_impl(
        &mut self,
        key: &str,
        key_type: KeyType,
        commission_bps: u32,
    ) {
        assert!(commission_bps <= 5000, "commission cannot exceed 50%");
        let kh = key_hash(key);
        assert!(
            self.validators.get(&kh).is_none(),
            "validator already registered"
        );

        let validator = Validator {
            key: key.to_string(),
            key_type,
            owner: env::predecessor_account_id(),
            registered_block: env::block_height(),
            commission_bps,
            active: true,
        };

        self.validators.insert(kh.clone(), validator);
        self.validator_keys.push(kh.clone());
        self.scores.insert(
            kh.clone(),
            ValidatorScore {
                last_seen_block: env::block_height(),
                blocktime_score: 0,
                earned: 0,
            },
        );
        self.validator_balances.insert(kh.clone(), 0);
        self.validator_total_minted.insert(kh, 0);
    }

    pub fn set_validator_commission_impl(&mut self, key: &str, new_commission_bps: u32) {
        assert!(new_commission_bps <= 5000, "commission cannot exceed 50%");
        let kh = key_hash(key);
        let mut v = self
            .validators
            .get(&kh)
            .cloned()
            .expect("validator not found");
        assert_eq!(
            v.owner,
            env::predecessor_account_id(),
            "not validator owner"
        );
        v.commission_bps = new_commission_bps;
        self.validators.insert(kh, v);
    }

    pub fn deactivate_validator_impl(&mut self, key: &str) {
        let kh = key_hash(key);
        let mut v = self
            .validators
            .get(&kh)
            .cloned()
            .expect("validator not found");
        assert!(
            env::predecessor_account_id() == v.owner
                || env::predecessor_account_id() == self.owner,
            "not authorized"
        );
        v.active = false;
        self.validators.insert(kh, v);
    }

    // ── Staking ─────────────────────────────────────────────────────────────

    pub fn stake_on_impl(&mut self, validator_key: &str, lock_blocks: u64) -> u64 {
        let amount = env::attached_deposit().as_yoctonear();
        assert!(amount > 0, "must attach NEAR to stake");
        assert!(
            lock_blocks <= self.max_lock_blocks,
            "exceeds max lock blocks"
        );

        let kh = key_hash(validator_key);
        let v = self.validators.get(&kh).expect("validator not found");
        assert!(v.active, "validator not active");

        let validator_stakes_len = self
            .validator_stake_ids
            .get(&kh)
            .map(|v| v.len())
            .unwrap_or(0);
        assert!(
            validator_stakes_len < self.max_stakers_per_validator,
            "max stakers reached"
        );

        let multiplier = self.get_multiplier(lock_blocks);
        let stt_amount = (amount as u128) * (multiplier as u128) / 10000;

        let stake_id = self.next_stake_id;
        self.next_stake_id += 1;

        let position = StakePosition {
            stake_id,
            staker: env::predecessor_account_id(),
            validator_key_hash: kh.clone(),
            amount: amount as u128,
            start_block: env::block_height(),
            lock_blocks,
            minted_balance: stt_amount,
        };

        self.stake_positions.insert(stake_id, position);
        self.native_token_locked += amount as u128;

        let caller = env::predecessor_account_id();
        if self.user_stake_ids.get(&caller).is_none() {
            self.user_stake_ids.insert(
                caller.clone(),
                Vector::new(StorageKey::UserStakeIdsInner {
                    account_hash: env::sha256(caller.as_bytes()),
                }),
            );
        }
        self.user_stake_ids.get_mut(&caller).unwrap().push(stake_id);

        if self.validator_stake_ids.get(&kh).is_none() {
            self.validator_stake_ids.insert(
                kh.clone(),
                Vector::new(StorageKey::ValidatorStakeIdsInner {
                    key_hash: kh.clone(),
                }),
            );
        }
        self.validator_stake_ids
            .get_mut(&kh)
            .unwrap()
            .push(stake_id);

        let current_minted = self
            .validator_total_minted
            .get(&kh)
            .copied()
            .unwrap_or(0);
        self.validator_total_minted
            .insert(kh, current_minted + stt_amount);

        stake_id
    }

    pub fn unstake_from_impl(&mut self, stake_id: u64) -> Promise {
        let pos = self
            .stake_positions
            .get(&stake_id)
            .cloned()
            .expect("position not found");
        assert_eq!(pos.staker, env::predecessor_account_id(), "not your stake");
        assert!(pos.amount > 0, "no active stake");
        assert!(
            env::block_height() >= pos.start_block + pos.lock_blocks,
            "still locked"
        );

        let kh = pos.validator_key_hash.clone();
        let amount = pos.amount;
        let stt_amount = pos.minted_balance;

        let current_minted = self
            .validator_total_minted
            .get(&kh)
            .copied()
            .unwrap_or(0);
        self.validator_total_minted
            .insert(kh, current_minted.saturating_sub(stt_amount));

        self.stake_positions.remove(&stake_id);
        self.native_token_locked = self.native_token_locked.saturating_sub(amount);

        Promise::new(pos.staker).transfer(NearToken::from_yoctonear(amount))
    }

    // ── Slashing ────────────────────────────────────────────────────────────

    pub fn slash_validator_impl(&mut self, key: &str) {
        self.assert_owner();
        let kh = key_hash(key);
        let v = self.validators.get(&kh).expect("validator not found");
        assert!(v.active, "validator not active");
        assert!(self.slash_bps > 0, "slashing not configured");

        let mut total_slashed: u128 = 0;

        if let Some(stake_ids) = self.validator_stake_ids.get(&kh) {
            let ids: Vec<u64> = stake_ids.iter().copied().collect();
            for &sid in &ids {
                if let Some(mut pos) = self.stake_positions.get(&sid).cloned() {
                    if pos.amount == 0 {
                        continue;
                    }
                    let penalty = (pos.amount * self.slash_bps as u128) / 10000;
                    let minted_penalty =
                        (pos.minted_balance * self.slash_bps as u128) / 10000;

                    pos.amount -= penalty;
                    pos.minted_balance -= minted_penalty;
                    self.stake_positions.insert(sid, pos);

                    let vm = self
                        .validator_total_minted
                        .get(&kh)
                        .copied()
                        .unwrap_or(0);
                    self.validator_total_minted
                        .insert(kh.clone(), vm.saturating_sub(minted_penalty));

                    total_slashed += penalty;
                }
            }
        }

        let count = self
            .validator_slash_count
            .get(&kh)
            .copied()
            .unwrap_or(0)
            + 1;
        self.validator_slash_count.insert(kh.clone(), count);

        if total_slashed > 0 {
            self.native_token_locked = self
                .native_token_locked
                .saturating_sub(total_slashed);
            if let Some(treasury) = &self.slash_treasury {
                Promise::new(treasury.clone())
                    .transfer(NearToken::from_yoctonear(total_slashed));
            }
        }

        if self.max_slash_count > 0 && count >= self.max_slash_count {
            let mut v = self.validators.get(&kh).cloned().unwrap();
            v.active = false;
            self.validators.insert(kh, v);
        }
    }

    // ── Multiplier Curve ────────────────────────────────────────────────────

    pub fn set_points_impl(&mut self, points: Vec<MultiplierPoint>) {
        self.assert_owner();
        assert!(!points.is_empty(), "must provide at least one point");

        for i in 0..points.len() {
            assert!(
                points[i].multiplier >= 10000,
                "multiplier must be >= 1x (10000)"
            );
            assert!(
                points[i].blocks <= self.max_lock_blocks,
                "exceeds max lock blocks"
            );
            if i > 0 {
                assert!(
                    points[i].blocks > points[i - 1].blocks,
                    "blocks must increase"
                );
                assert!(
                    points[i].multiplier >= points[i - 1].multiplier,
                    "multiplier must not decrease"
                );
            }
        }

        self.multiplier_points.clear();
        for p in points {
            self.multiplier_points.push(p);
        }
    }

    pub fn get_multiplier(&self, block_count: u64) -> u32 {
        let len = self.multiplier_points.len();
        if len == 0 {
            return 10000;
        }

        let first = self.multiplier_points.get(0).unwrap();
        if block_count <= first.blocks {
            return first.multiplier;
        }

        let last = self.multiplier_points.get(len - 1).unwrap();
        if block_count >= last.blocks {
            return last.multiplier;
        }

        for i in 0..(len - 1) {
            let a = self.multiplier_points.get(i).unwrap();
            let b = self.multiplier_points.get(i + 1).unwrap();
            if block_count >= a.blocks && block_count <= b.blocks {
                return Self::interpolate(
                    a.blocks,
                    a.multiplier,
                    b.blocks,
                    b.multiplier,
                    block_count,
                );
            }
        }

        last.multiplier
    }

    fn interpolate(x0: u64, y0: u32, x1: u64, y1: u32, x: u64) -> u32 {
        if x1 == x0 {
            return y0;
        }
        let dx = (x - x0) as u128;
        let range = (x1 - x0) as u128;
        let dy = (y1 - y0) as u128;
        (y0 as u128 + (dy * dx / range)) as u32
    }

    // ── Views ───────────────────────────────────────────────────────────────

    pub fn get_validator_impl(&self, key: &str) -> Option<ValidatorView> {
        let kh = key_hash(key);
        self.validators.get(&kh).map(|v| {
            let total_stt = self
                .validator_total_minted
                .get(&kh)
                .copied()
                .unwrap_or(0);
            let score = self
                .scores
                .get(&kh)
                .map(|s| s.blocktime_score)
                .unwrap_or(0);
            ValidatorView {
                key: v.key.clone(),
                key_type: v.key_type.clone(),
                owner: v.owner.clone(),
                registered_block: v.registered_block,
                commission_bps: v.commission_bps,
                active: v.active,
                total_stt: U128(total_stt),
                score: U128(score),
            }
        })
    }

    pub fn validator_count_impl(&self) -> u32 {
        let mut count = 0u32;
        for kh in self.validator_keys.iter() {
            if let Some(v) = self.validators.get(kh) {
                if v.active {
                    count += 1;
                }
            }
        }
        count
    }

    pub fn get_stake_position_impl(&self, stake_id: u64) -> Option<StakePositionView> {
        self.stake_positions.get(&stake_id).map(|pos| {
            let vkey = self
                .validators
                .get(&pos.validator_key_hash)
                .map(|v| v.key.clone())
                .unwrap_or_default();
            StakePositionView {
                stake_id: pos.stake_id,
                staker: pos.staker.clone(),
                validator_key: vkey,
                amount: U128(pos.amount),
                start_block: pos.start_block,
                lock_blocks: pos.lock_blocks,
                minted_balance: U128(pos.minted_balance),
                unlocks_at: pos.start_block + pos.lock_blocks,
            }
        })
    }

    pub fn get_user_stake_ids_impl(&self, user: &AccountId) -> Vec<u64> {
        self.user_stake_ids
            .get(user)
            .map(|v| v.iter().copied().collect())
            .unwrap_or_default()
    }

    pub fn get_validator_total_minted_impl(&self, key: &str) -> U128 {
        let kh = key_hash(key);
        U128(
            self.validator_total_minted
                .get(&kh)
                .copied()
                .unwrap_or(0),
        )
    }
}
