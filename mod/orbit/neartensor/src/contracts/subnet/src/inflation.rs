use crate::types::InflationType;
use crate::Subnet;

/// All 6 inflation curves.
///
/// Port of:
/// - InflationFlat.sol: constant rate
/// - InflationHalving.sol: Bitcoin-style step halving
/// - InflationLinearDecay.sol: linear decrease to floor
/// - InflationSigmoid.sol: quadratic S-curve
/// - InflationTAO.sol: Bittensor asymptotic approach to supply cap
/// - InflationBTC.sol: Bitcoin model with halving + hard cap
impl Subnet {
    pub fn get_effective_emission(&self) -> u128 {
        let epoch = self.consensus.current_epoch;
        self.get_inflation_emission(epoch)
    }

    pub(crate) fn get_inflation_emission(&self, epoch: u64) -> u128 {
        match &self.inflation_type {
            // ── Flat: constant emission ──────────────────────────────────────
            InflationType::Flat { rate } => rate.0,

            // ── Halving: step halving at fixed intervals ─────────────────────
            InflationType::Halving {
                initial_rate,
                interval,
                floor,
            } => {
                if *interval == 0 {
                    return initial_rate.0;
                }
                let halvings = epoch / interval;
                if halvings >= 64 {
                    return floor.0;
                }
                let emission = initial_rate.0 >> halvings;
                emission.max(floor.0)
            }

            // ── LinearDecay: linear from initial to floor over decay_epochs ──
            InflationType::LinearDecay {
                initial_rate,
                floor,
                decay_epochs,
            } => {
                if epoch >= *decay_epochs || *decay_epochs == 0 {
                    return floor.0;
                }
                let range = initial_rate.0.saturating_sub(floor.0);
                let drop = (range * epoch as u128) / *decay_epochs as u128;
                initial_rate.0.saturating_sub(drop)
            }

            // ── Sigmoid: quadratic S-curve (ramp up then decay) ─────────────
            InflationType::Sigmoid {
                peak,
                floor,
                total_epochs,
            } => {
                if epoch >= *total_epochs || *total_epochs == 0 {
                    return floor.0;
                }
                let mid = (*total_epochs / 2).max(1) as u128;
                let diff = peak.0.saturating_sub(floor.0);

                // Use 1e18 precision for the ratio
                let precision: u128 = 1_000_000_000_000_000_000;
                let ratio = if (epoch as u128) <= mid {
                    let e = epoch as u128;
                    (e * e * precision) / (mid * mid)
                } else {
                    let remaining = (*total_epochs - epoch) as u128;
                    (remaining * remaining * precision) / (mid * mid)
                };

                floor.0 + (diff * ratio) / precision
            }

            // ── TAO: Bittensor asymptotic approach to supply cap ─────────────
            InflationType::Tao {
                initial_rate,
                supply_cap,
            } => {
                if supply_cap.0 == 0 || self.inflation_total_minted >= supply_cap.0 {
                    return 0;
                }
                let remaining = supply_cap.0 - self.inflation_total_minted;
                let emission =
                    (initial_rate.0 * remaining) / supply_cap.0;
                // Ensure we don't emit more than remaining
                emission.min(remaining)
            }

            // ── BTC: Bitcoin halving with hard supply cap ────────────────────
            InflationType::Btc {
                initial_reward,
                halving_interval,
                supply_cap,
            } => {
                if self.inflation_total_minted >= supply_cap.0 {
                    return 0;
                }
                if *halving_interval == 0 {
                    let remaining = supply_cap.0 - self.inflation_total_minted;
                    return initial_reward.0.min(remaining);
                }
                let halvings = epoch / halving_interval;
                if halvings >= 64 {
                    return 0;
                }
                let emission = initial_reward.0 >> halvings;
                if emission == 0 {
                    return 0;
                }
                let remaining = supply_cap.0 - self.inflation_total_minted;
                emission.min(remaining)
            }
        }
    }
}
