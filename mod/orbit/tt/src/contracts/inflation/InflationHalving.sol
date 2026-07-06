// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IInflationCurve.sol";

/**
 * @title InflationHalving
 * @dev Bitcoin-style halving: emission halves every `interval` epochs.
 *      Stops at a configurable floor to prevent zero emissions.
 */
contract InflationHalving is IInflationCurve {
    uint256 public initialRate;
    uint64  public interval;    // epochs between halvings
    uint256 public floor;       // minimum emission per epoch

    constructor(uint256 _initialRate, uint64 _interval, uint256 _floor) {
        require(_interval > 0, "interval must be > 0");
        initialRate = _initialRate;
        interval = _interval;
        floor = _floor;
    }

    function getEmission(uint64 epoch) external view override returns (uint256) {
        uint256 halvings = uint256(epoch) / uint256(interval);
        uint256 emission = initialRate;
        for (uint256 i = 0; i < halvings && i < 64; i++) {
            emission >>= 1;
            if (emission <= floor) return floor;
        }
        return emission < floor ? floor : emission;
    }
}
