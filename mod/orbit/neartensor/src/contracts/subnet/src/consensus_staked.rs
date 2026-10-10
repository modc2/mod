use crate::Subnet;

/// ConsensusStaked: Capital-weighted scoring (pure DPoS).
///
/// Port of ConsensusStaked.sol:
/// - score = total STT staked on validator
/// - Validator must checkin during epoch to be eligible
/// - Proposer selected by weighted random on STT
/// - Distribution proportional to STT only
impl Subnet {
    pub(crate) fn apply_checkin_staked(&mut self, kh: Vec<u8>) {
        // Record that validator checked in for current epoch
        self.last_checkin_epoch
            .insert(kh.clone(), self.consensus.current_epoch);

        // Score = total STT staked (recalculated each time)
        let total_stt = self
            .validator_total_minted
            .get(&kh)
            .copied()
            .unwrap_or(0);

        let mut score = self.scores.get(&kh).cloned().unwrap_or_default();

        // Update total
        self.consensus.total_blocktime = self
            .consensus
            .total_blocktime
            .saturating_sub(score.blocktime_score);

        score.blocktime_score = total_stt;
        score.last_seen_block = self.consensus.current_block;

        self.consensus.total_blocktime += score.blocktime_score;
        self.scores.insert(kh, score);
    }

    pub(crate) fn select_proposer_staked(&self) -> Option<Vec<u8>> {
        let mut candidates: Vec<(Vec<u8>, u128)> = Vec::new();
        let mut total: u128 = 0;

        for kh in self.validator_keys.iter() {
            if let Some(v) = self.validators.get(kh) {
                if !v.active {
                    continue;
                }
                // Must have checked in this epoch
                let checkin_epoch = self
                    .last_checkin_epoch
                    .get(kh)
                    .copied()
                    .unwrap_or(0);
                if checkin_epoch != self.consensus.current_epoch {
                    continue;
                }
                let stt = self
                    .validator_total_minted
                    .get(kh)
                    .copied()
                    .unwrap_or(0);
                if stt > 0 {
                    candidates.push((kh.clone(), stt));
                    total += stt;
                }
            }
        }

        self.weighted_random_select(&candidates, total)
    }

    pub(crate) fn distribute_staked(&mut self, emission: u128) {
        let mut eligible: Vec<(Vec<u8>, u128)> = Vec::new();
        let mut total_stt: u128 = 0;

        let keys: Vec<Vec<u8>> = self.validator_keys.iter().cloned().collect();

        for kh in &keys {
            if let Some(v) = self.validators.get(kh) {
                if !v.active {
                    continue;
                }
                // Must have checked in this epoch
                let checkin_epoch = self
                    .last_checkin_epoch
                    .get(kh)
                    .copied()
                    .unwrap_or(0);
                if checkin_epoch != self.consensus.current_epoch {
                    continue;
                }
                let stt = self
                    .validator_total_minted
                    .get(kh)
                    .copied()
                    .unwrap_or(0);
                if stt > 0 {
                    eligible.push((kh.clone(), stt));
                    total_stt += stt;
                }
            }
        }

        if total_stt == 0 {
            return;
        }

        for (kh, stt) in &eligible {
            let share = (emission * stt) / total_stt;
            self.distribute_validator_share(kh, share);
        }
    }
}
