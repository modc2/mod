use crate::Registry;

/// Bonding curve math.
///
/// Port of Registry.sol bonding curve:
///   Linear curve: price(s) = slope * s / 1e18
///   Buy: shares_for_deposit = -s + sqrt(s^2 + 2*amount*1e18/slope)
///   Sell: return_for_sell = slope * (2*s*n - n^2) / (2 * 1e18)
const PRECISION: u128 = 1_000_000_000_000_000_000; // 1e18

impl Registry {
    pub(crate) fn calc_shares_for_deposit(
        &self,
        subnet_id: u32,
        amount: u128,
    ) -> u128 {
        let s = self
            .subnet_total_shares
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        // n = -s + sqrt(s^2 + 2 * amount * 1e18 / slope)
        let inner = s
            .checked_mul(s)
            .unwrap()
            .checked_add(
                2u128
                    .checked_mul(amount)
                    .unwrap()
                    .checked_mul(PRECISION)
                    .unwrap()
                    / self.curve_slope,
            )
            .unwrap();
        let root = isqrt(inner);
        if root > s {
            root - s
        } else {
            0
        }
    }

    pub(crate) fn calc_return_for_sell(
        &self,
        subnet_id: u32,
        shares: u128,
    ) -> u128 {
        let s = self
            .subnet_total_shares
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        assert!(shares <= s, "shares exceed supply");
        // return = slope * (2*s*n - n^2) / (2 * 1e18)
        let numerator = self
            .curve_slope
            .checked_mul(
                2u128
                    .checked_mul(s)
                    .unwrap()
                    .checked_mul(shares)
                    .unwrap()
                    .checked_sub(shares.checked_mul(shares).unwrap())
                    .unwrap(),
            )
            .unwrap();
        numerator / (2 * PRECISION)
    }

    pub(crate) fn get_boost_price_internal(
        &self,
        subnet_id: u32,
        num_shares: u128,
    ) -> u128 {
        let s = self
            .subnet_total_shares
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        // cost = slope * (2*s*n + n^2) / (2 * 1e18)
        let numerator = self.curve_slope
            * (2 * s * num_shares + num_shares * num_shares);
        numerator / (2 * PRECISION)
    }
}

/// Babylonian integer square root.
fn isqrt(x: u128) -> u128 {
    if x == 0 {
        return 0;
    }
    let mut y = x;
    let mut z = (x + 1) / 2;
    while z < y {
        y = z;
        z = (x / z + z) / 2;
    }
    y
}
