// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IInflationCurve.sol";

/**
 * @title InflationTAO
 * @dev Bittensor TAO-style inflation: exponential decay toward a supply cap.
 *
 *      emission(epoch) = initialRate * (supplyCap - totalMinted) / supplyCap
 *
 *      As more tokens are minted, emission decreases proportionally to the
 *      remaining unminted supply. This creates an asymptotic approach to the
 *      cap — early epochs are highly rewarding, later epochs taper off.
 *
 *      A floor parameter prevents emission from dropping to dust amounts.
 */
contract InflationTAO is IInflationCurve {
    uint256 public immutable initialRate;  // max emission per epoch at 0% minted
    uint256 public immutable supplyCap;    // total supply ceiling
    uint256 public immutable floor;        // minimum emission per epoch

    uint256 public totalMinted;

    constructor(
        uint256 _initialRate,
        uint256 _supplyCap,
        uint256 _floor
    ) {
        require(_initialRate > 0, "initial rate must be > 0");
        require(_supplyCap > 0, "supply cap must be > 0");
        initialRate = _initialRate;
        supplyCap = _supplyCap;
        floor = _floor;
    }

    function getEmission(uint64) external view override returns (uint256) {
        if (totalMinted >= supplyCap) return 0;

        uint256 remaining = supplyCap - totalMinted;
        uint256 emission = (initialRate * remaining) / supplyCap;

        if (emission < floor && remaining >= floor) return floor;
        if (emission < floor) return remaining;

        return emission;
    }

    /// @notice Call after minting to track cumulative supply.
    function recordMint(uint256 amount) external {
        totalMinted += amount;
    }
}
