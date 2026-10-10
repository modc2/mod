use near_sdk::store::Vector;
use near_sdk::env;
use near_sdk::json_types::U128;

use crate::types::*;
use crate::Subnet;

/// ConsensusPriv: Privacy-preserving consensus via Merkle proofs.
impl Subnet {
    pub fn register_commitment_impl(&mut self, commitment: Vec<u8>) {
        assert_eq!(commitment.len(), 32, "commitment must be 32 bytes");
        assert!(
            self.consensus_type == ConsensusType::Priv,
            "not Priv consensus"
        );
        assert!(
            !self
                .commitment_registered
                .get(&commitment)
                .copied()
                .unwrap_or(false),
            "commitment already registered"
        );

        self.commitment_registered.insert(commitment.clone(), true);
        self.merkle_insert(commitment);
    }

    pub fn anon_checkin_impl(
        &mut self,
        nullifier: Vec<u8>,
        commitment: Vec<u8>,
        merkle_proof: Vec<Vec<u8>>,
        merkle_root: Vec<u8>,
        leaf_index: u64,
    ) {
        assert!(
            self.consensus_type == ConsensusType::Priv,
            "not Priv consensus"
        );
        assert_eq!(nullifier.len(), 32, "nullifier must be 32 bytes");
        assert_eq!(merkle_root.len(), 32, "root must be 32 bytes");

        assert!(
            self.merkle_is_known_root(&merkle_root),
            "unknown Merkle root"
        );

        assert!(
            self.merkle_verify_proof(&commitment, &merkle_proof, leaf_index, &merkle_root),
            "invalid Merkle proof"
        );

        let epoch = self.consensus.current_epoch;
        let nullifier_key = self.priv_nullifier_key(epoch, &nullifier);
        assert!(
            !self
                .nullifier_used
                .get(&nullifier_key)
                .copied()
                .unwrap_or(false),
            "nullifier already used this epoch"
        );

        self.nullifier_used.insert(nullifier_key, true);

        if self.epoch_nullifiers.get(&epoch).is_none() {
            self.epoch_nullifiers.insert(
                epoch,
                Vector::new(StorageKey::EpochNullifiersInner { epoch }),
            );
        }
        self.epoch_nullifiers
            .get_mut(&epoch)
            .unwrap()
            .push(nullifier.clone());

        self.epoch_checkin_count += 1;
        self.consensus.total_blocktime += 1;
    }

    pub(crate) fn select_proposer_priv(&self) -> Option<Vec<u8>> {
        let mut candidates: Vec<(Vec<u8>, u128)> = Vec::new();
        let mut total: u128 = 0;

        for kh in self.validator_keys.iter() {
            if let Some(v) = self.validators.get(kh) {
                if v.active {
                    candidates.push((kh.clone(), 1));
                    total += 1;
                }
            }
        }

        self.weighted_random_select(&candidates, total)
    }

    pub(crate) fn distribute_priv(&mut self, emission: u128) {
        let epoch = self.consensus.current_epoch;

        let nullifier_count = self
            .epoch_nullifiers
            .get(&epoch)
            .map(|v| v.len())
            .unwrap_or(0);

        if nullifier_count == 0 {
            return;
        }

        let per_nullifier = emission / nullifier_count as u128;

        if let Some(nullifiers) = self.epoch_nullifiers.get(&epoch) {
            let nuls: Vec<Vec<u8>> = nullifiers.iter().cloned().collect();
            for nul in &nuls {
                let existing = self
                    .nullifier_rewards
                    .get(nul)
                    .copied()
                    .unwrap_or(0);
                self.nullifier_rewards
                    .insert(nul.clone(), existing + per_nullifier);
            }
        }

        self.total_supply += emission;
        self.epoch_checkin_count = 0;
        self.consensus.total_blocktime = 0;
    }

    pub fn claim_priv_rewards_impl(&mut self, secret: Vec<u8>, epochs: Vec<u64>) -> U128 {
        assert!(
            self.consensus_type == ConsensusType::Priv,
            "not Priv consensus"
        );
        let caller = env::predecessor_account_id();
        let mut total_reward: u128 = 0;

        for epoch in epochs {
            let mut data = secret.clone();
            data.extend_from_slice(&epoch.to_le_bytes());
            let nullifier = env::keccak256(&data);

            let reward = self
                .nullifier_rewards
                .get(&nullifier)
                .copied()
                .unwrap_or(0);

            if reward > 0 {
                self.nullifier_rewards.insert(nullifier, 0);
                total_reward += reward;
            }
        }

        assert!(total_reward > 0, "no rewards to claim");

        let bal = self.balances.get(&caller).copied().unwrap_or(0);
        self.balances.insert(caller, bal + total_reward);

        U128(total_reward)
    }

    fn priv_nullifier_key(&self, epoch: u64, nullifier: &[u8]) -> Vec<u8> {
        let mut key = epoch.to_le_bytes().to_vec();
        key.extend_from_slice(nullifier);
        key
    }

    pub fn is_nullifier_used_impl(&self, epoch: u64, nullifier: Vec<u8>) -> bool {
        let key = self.priv_nullifier_key(epoch, &nullifier);
        self.nullifier_used.get(&key).copied().unwrap_or(false)
    }
}
