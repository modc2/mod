// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IInflationCurve.sol";

/**
 * @title InflationSigmoid
 * @dev S-curve emission: ramps up from `floor` to `peak` over the first half
 *      of the lifecycle, then decays back to `floor` in the second half.
 *
 *      Uses a piecewise quadratic approximation of a sigmoid:
 *        phase 1 (epoch < midpoint): floor + (peak-floor) * (epoch/midpoint)^2
 *        phase 2 (epoch >= midpoint): floor + (peak-floor) * ((totalEpochs-epoch)/midpoint)^2
 *
 *      After `totalEpochs`, emission stays at `floor`.
 */
contract InflationSigmoid is IInflationCurve {
    uint256 public peak;
    uint256 public floor;
    uint64  public totalEpochs;

    constructor(uint256 _peak, uint256 _floor, uint64 _totalEpochs) {
        require(_peak >= _floor, "peak must be >= floor");
        require(_totalEpochs > 1, "totalEpochs must be > 1");
        peak = _peak;
        floor = _floor;
        totalEpochs = _totalEpochs;
    }

    function getEmission(uint64 epoch) external view override returns (uint256) {
        if (epoch >= totalEpochs) return floor;

        uint256 mid = uint256(totalEpochs) / 2;
        if (mid == 0) mid = 1;

        uint256 diff = peak - floor;
        uint256 ratio;

        if (uint256(epoch) <= mid) {
            // ramp up: quadratic increase
            ratio = (uint256(epoch) * uint256(epoch) * 1e18) / (mid * mid);
        } else {
            // decay: quadratic decrease
            uint256 remaining = uint256(totalEpochs) - uint256(epoch);
            ratio = (remaining * remaining * 1e18) / (mid * mid);
        }

        return floor + (diff * ratio) / 1e18;
    }
}
