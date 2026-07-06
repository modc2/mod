// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IInflationCurve.sol";

/**
 * @title InflationBTC
 * @dev Bitcoin-modeled inflation: fixed supply cap with halving every
 *      `halvingInterval` epochs. Mirrors BTC's 21M cap / 210k-block halving
 *      schedule but with configurable parameters.
 *
 *      - Tracks cumulative minted supply on-chain.
 *      - Emission stops once `supplyCap` is reached.
 *      - Halving is deterministic: epoch / halvingInterval = number of halvings.
 */
contract InflationBTC is IInflationCurve {
    uint256 public immutable initialReward;   // tokens per epoch at genesis
    uint64  public immutable halvingInterval;  // epochs between halvings
    uint256 public immutable supplyCap;        // hard cap (like 21M BTC)

    uint256 public totalMinted;

    constructor(
        uint256 _initialReward,
        uint64  _halvingInterval,
        uint256 _supplyCap
    ) {
        require(_halvingInterval > 0, "interval must be > 0");
        require(_supplyCap > 0, "supply cap must be > 0");
        require(_initialReward > 0, "initial reward must be > 0");
        initialReward = _initialReward;
        halvingInterval = _halvingInterval;
        supplyCap = _supplyCap;
    }

    function getEmission(uint64 epoch) external view override returns (uint256) {
        if (totalMinted >= supplyCap) return 0;

        uint256 halvings = uint256(epoch) / uint256(halvingInterval);
        if (halvings >= 64) return 0;

        uint256 emission = initialReward >> halvings;
        if (emission == 0) return 0;

        // Clamp to remaining supply
        uint256 remaining = supplyCap - totalMinted;
        return emission > remaining ? remaining : emission;
    }

    /// @notice Call after minting to track cumulative supply.
    function recordMint(uint256 amount) external {
        totalMinted += amount;
    }
}
