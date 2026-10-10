use crate::Subnet;

/// ConsensusYuma: Blocktime-based scoring with exponential decay.
///
/// Port of ConsensusYuma.sol:
/// - score = decay(old_score) + min(blocks_since_last_checkin, epoch_length)
/// - decay(s) = s * (10000 - decay_bps) / 10000
/// - Proposer selected by weighted random on blocktime score
/// - Distribution proportional to blocktime score, then decay all
impl Subnet {
    pub(crate) fn apply_checkin_yuma(&mut self, kh: Vec<u8>) {
        let mut score = self.scores.get(&kh).cloned().unwrap_or_default();
        let current = self.consensus.current_block.max(score.last_seen_block);

        // Decay existing score
        let decayed = self.decay(score.blocktime_score);

        // Delta = blocks since last checkin, capped at epoch_length
        let delta = current.saturating_sub(score.last_seen_block);
        let capped_delta = delta.min(self.consensus.epoch_length) as u128;

        // Remove old score from total, add new
        self.consensus.total_blocktime = self
            .consensus
            .total_blocktime
            .saturating_sub(score.blocktime_score);

        score.blocktime_score = decayed + capped_delta;
        score.last_seen_block = current;

        self.consensus.total_blocktime += score.blocktime_score;
        self.scores.insert(kh, score);
    }

    pub(crate) fn select_proposer_yuma(&self) -> Option<Vec<u8>> {
        let mut candidates: Vec<(Vec<u8>, u128)> = Vec::new();

        for kh in self.validator_keys.iter() {
            if let Some(v) = self.validators.get(kh) {
                if !v.active {
                    continue;
                }
                if let Some(s) = self.scores.get(kh) {
                    if s.blocktime_score > 0 {
                        candidates.push((kh.clone(), s.blocktime_score));
                    }
                }
            }
        }

        let total = self.consensus.total_blocktime;
        self.weighted_random_select(&candidates, total)
    }

    pub(crate) fn distribute_yuma(&mut self, emission: u128) {
        let total = self.consensus.total_blocktime;
        if total == 0 {
            return;
        }

        // Collect active validator scores
        let keys: Vec<Vec<u8>> = self.validator_keys.iter().cloned().collect();
        let mut distributions: Vec<(Vec<u8>, u128)> = Vec::new();

        for kh in &keys {
            if let Some(v) = self.validators.get(kh) {
                if !v.active {
                    continue;
                }
                if let Some(s) = self.scores.get(kh) {
                    if s.blocktime_score > 0 {
                        let share =
                            (emission * s.blocktime_score) / total;
                        distributions.push((kh.clone(), share));
                    }
                }
            }
        }

        // Distribute
        for (kh, share) in &distributions {
            self.distribute_validator_share(kh, *share);
        }

        // Decay all scores after distribution
        let mut new_total: u128 = 0;
        for kh in &keys {
            if let Some(mut s) = self.scores.get(kh).cloned() {
                s.blocktime_score = self.decay(s.blocktime_score);
                new_total += s.blocktime_score;
                self.scores.insert(kh.clone(), s);
            }
        }
        self.consensus.total_blocktime = new_total;
    }

    fn decay(&self, score: u128) -> u128 {
        (score * (10000 - self.decay_bps as u128)) / 10000
    }
}
