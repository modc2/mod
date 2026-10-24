use crate::Subnet;

/// ConsensusLinear: Simple checkin counter scoring.
///
/// Port of ConsensusLinear.sol:
/// - score += 1 per checkin
/// - Proposer selected by weighted random on checkin count
/// - Distribution proportional to checkin count, then reset all scores
impl Subnet {
    pub(crate) fn apply_checkin_linear(&mut self, kh: Vec<u8>) {
        let mut score = self.scores.get(&kh).cloned().unwrap_or_default();

        // Remove old from total
        self.consensus.total_blocktime = self
            .consensus
            .total_blocktime
            .saturating_sub(score.blocktime_score);

        score.blocktime_score += 1;
        score.last_seen_block = self.consensus.current_block;

        self.consensus.total_blocktime += score.blocktime_score;
        self.scores.insert(kh, score);
    }

    pub(crate) fn select_proposer_linear(&self) -> Option<Vec<u8>> {
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

    pub(crate) fn distribute_linear(&mut self, emission: u128) {
        let total = self.consensus.total_blocktime;
        if total == 0 {
            return;
        }

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

        for (kh, share) in &distributions {
            self.distribute_validator_share(kh, *share);
        }

        // Reset all scores to 0 after distribution
        for kh in &keys {
            if let Some(mut s) = self.scores.get(kh).cloned() {
                s.blocktime_score = 0;
                self.scores.insert(kh.clone(), s);
            }
        }
        self.consensus.total_blocktime = 0;
    }
}
