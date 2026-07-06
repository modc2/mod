use near_sdk::json_types::U128;
use near_sdk::{env, AccountId};

use crate::types::*;
use crate::Subnet;

impl Subnet {
    // ── Checkin (dispatches to consensus-specific logic) ─────────────────────

    pub fn checkin_impl(&mut self, key: &str) {
        let kh = key_hash(key);
        let v = self.validators.get(&kh).expect("validator not found");
        assert!(v.active, "validator not active");

        assert!(
            env::predecessor_account_id() == v.owner
                || env::predecessor_account_id() == self.owner,
            "not authorized to checkin"
        );

        match self.consensus_type {
            ConsensusType::Yuma => self.apply_checkin_yuma(kh),
            ConsensusType::Linear => self.apply_checkin_linear(kh),
            ConsensusType::Staked => self.apply_checkin_staked(kh),
            ConsensusType::Priv => {
                env::panic_str("use anon_checkin for Priv consensus")
            }
        }
    }

    pub fn batch_checkin_impl(&mut self, keys: &[String]) {
        for key in keys {
            self.checkin_impl(key);
        }
    }

    // ── Block Production ────────────────────────────────────────────────────

    pub fn produce_block_impl(&mut self) -> Option<String> {
        self.consensus.current_block += 1;

        // Select proposer
        let proposer = match self.consensus_type {
            ConsensusType::Yuma => self.select_proposer_yuma(),
            ConsensusType::Linear => self.select_proposer_linear(),
            ConsensusType::Staked => self.select_proposer_staked(),
            ConsensusType::Priv => self.select_proposer_priv(),
        };

        // Check if epoch boundary reached
        let blocks_since = self
            .consensus
            .current_block
            .saturating_sub(self.consensus.last_emission_block);
        if blocks_since >= self.consensus.epoch_length && self.consensus.epoch_length > 0 {
            self.distribute_emissions_internal();
            self.consensus.last_emission_block = self.consensus.current_block;
            self.consensus.current_epoch += 1;
        }

        proposer.and_then(|kh| {
            self.validators
                .get(&kh)
                .map(|v| v.key.clone())
        })
    }

    // ── Emission Distribution ───────────────────────────────────────────────

    pub(crate) fn distribute_emissions_internal(&mut self) {
        let emission = self.get_effective_emission();
        if emission == 0 {
            return;
        }

        match self.consensus_type.clone() {
            ConsensusType::Yuma => self.distribute_yuma(emission),
            ConsensusType::Linear => self.distribute_linear(emission),
            ConsensusType::Staked => self.distribute_staked(emission),
            ConsensusType::Priv => self.distribute_priv(emission),
        }

        self.inflation_total_minted += emission;
    }

    /// Shared distribution helper: splits validator share into commission + staker pool
    pub(crate) fn distribute_validator_share(
        &mut self,
        kh: &Vec<u8>,
        validator_share: u128,
    ) {
        if validator_share == 0 {
            return;
        }

        let v = match self.validators.get(kh) {
            Some(v) => v.clone(),
            None => return,
        };

        let total_stt = self
            .validator_total_minted
            .get(kh)
            .copied()
            .unwrap_or(0);

        if total_stt == 0 {
            let bal = self.validator_balances.get(kh).copied().unwrap_or(0);
            self.validator_balances
                .insert(kh.clone(), bal + validator_share);
            self.total_supply += validator_share;
            return;
        }

        let commission =
            (validator_share * v.commission_bps as u128) / 10000;
        let staker_pool = validator_share - commission;

        let vbal = self.validator_balances.get(kh).copied().unwrap_or(0);
        self.validator_balances
            .insert(kh.clone(), vbal + commission);

        if let Some(stake_ids) = self.validator_stake_ids.get(kh) {
            let ids: Vec<u64> = stake_ids.iter().copied().collect();
            let mut distributed: u128 = 0;

            for &sid in &ids {
                if let Some(pos) = self.stake_positions.get(&sid) {
                    if pos.minted_balance == 0 {
                        continue;
                    }
                    let reward =
                        (staker_pool * pos.minted_balance) / total_stt;
                    if reward > 0 {
                        let existing = self
                            .staker_rewards
                            .get(&pos.staker)
                            .copied()
                            .unwrap_or(0);
                        self.staker_rewards
                            .insert(pos.staker.clone(), existing + reward);
                        distributed += reward;
                    }
                }
            }

            let dust = staker_pool - distributed;
            if dust > 0 {
                let vbal2 = self
                    .validator_balances
                    .get(kh)
                    .copied()
                    .unwrap_or(0);
                self.validator_balances
                    .insert(kh.clone(), vbal2 + dust);
            }
        }

        self.total_supply += validator_share;
    }

    // ── Reward Claims ───────────────────────────────────────────────────────

    pub fn claim_staker_rewards_impl(&mut self) -> U128 {
        let caller = env::predecessor_account_id();
        let reward = self.staker_rewards.get(&caller).copied().unwrap_or(0);
        assert!(reward > 0, "no rewards to claim");

        self.staker_rewards.insert(caller.clone(), 0);
        let bal = self.balances.get(&caller).copied().unwrap_or(0);
        self.balances.insert(caller, bal + reward);

        U128(reward)
    }

    pub fn claim_validator_rewards_impl(&mut self, key: &str, to: &AccountId) -> U128 {
        let kh = key_hash(key);
        let v = self.validators.get(&kh).expect("validator not found");
        assert_eq!(
            env::predecessor_account_id(),
            v.owner,
            "not validator owner"
        );

        let reward = self.validator_balances.get(&kh).copied().unwrap_or(0);
        assert!(reward > 0, "no rewards to claim");

        self.validator_balances.insert(kh, 0);
        let bal = self.balances.get(to).copied().unwrap_or(0);
        self.balances.insert(to.clone(), bal + reward);

        U128(reward)
    }

    // ── Views ───────────────────────────────────────────────────────────────

    pub fn get_consensus_state_impl(&self) -> ConsensusStateView {
        ConsensusStateView {
            current_block: self.consensus.current_block,
            last_emission_block: self.consensus.last_emission_block,
            total_blocktime: U128(self.consensus.total_blocktime),
            epoch_length: self.consensus.epoch_length,
            current_epoch: self.consensus.current_epoch,
            consensus_type: self.consensus_type.clone(),
            inflation_type: self.inflation_type.clone(),
            emission_rate: U128(self.emission_rate),
            total_supply: U128(self.total_supply),
        }
    }

    pub fn get_validator_score_impl(&self, key: &str) -> Option<ValidatorScore> {
        let kh = key_hash(key);
        self.scores.get(&kh).cloned()
    }

    pub fn get_validator_balance_impl(&self, key: &str) -> U128 {
        let kh = key_hash(key);
        U128(self.validator_balances.get(&kh).copied().unwrap_or(0))
    }

    pub fn get_staker_rewards_impl(&self, staker: &AccountId) -> U128 {
        U128(self.staker_rewards.get(staker).copied().unwrap_or(0))
    }

    pub fn get_leaderboard_impl(&self, limit: u32) -> Vec<LeaderboardEntry> {
        let mut entries: Vec<LeaderboardEntry> = Vec::new();

        for kh in self.validator_keys.iter() {
            if let Some(v) = self.validators.get(kh) {
                if !v.active {
                    continue;
                }
                let score = self
                    .scores
                    .get(kh)
                    .map(|s| s.blocktime_score)
                    .unwrap_or(0);
                let total_stt = self
                    .validator_total_minted
                    .get(kh)
                    .copied()
                    .unwrap_or(0);
                entries.push(LeaderboardEntry {
                    key: v.key.clone(),
                    score: U128(score),
                    total_stt: U128(total_stt),
                    commission_bps: v.commission_bps,
                    active: v.active,
                });
            }
        }

        entries.sort_by(|a, b| b.score.0.cmp(&a.score.0));
        entries.truncate(limit as usize);
        entries
    }

    // ── Weighted Random Selection Helper ────────────────────────────────────

    pub(crate) fn weighted_random_select(
        &self,
        candidates: &[(Vec<u8>, u128)],
        total_weight: u128,
    ) -> Option<Vec<u8>> {
        if candidates.is_empty() || total_weight == 0 {
            return None;
        }

        let seed = env::random_seed();
        let mut rand_val: u128 = 0;
        for (i, &byte) in seed.iter().take(16).enumerate() {
            rand_val |= (byte as u128) << (i * 8);
        }
        let target = rand_val % total_weight;

        let mut cumulative: u128 = 0;
        for (kh, weight) in candidates {
            cumulative += weight;
            if target < cumulative {
                return Some(kh.clone());
            }
        }

        candidates.last().map(|(kh, _)| kh.clone())
    }
}
