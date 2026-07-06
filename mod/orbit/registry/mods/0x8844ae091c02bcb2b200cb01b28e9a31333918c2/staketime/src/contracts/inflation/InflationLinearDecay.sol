// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IInflationCurve.sol";

/**
 * @title InflationLinearDecay
 * @dev Linearly decreasing emission from `initialRate` to `floor` over
 *      `decayEpochs` epochs. After that, emission stays at `floor`.
 */
contract InflationLinearDecay is IInflationCurve {
    uint256 public initialRate;
    uint256 public floor;
    uint64  public decayEpochs; // number of epochs to reach floor

    constructor(uint256 _initialRate, uint256 _floor, uint64 _decayEpochs) {
        require(_initialRate >= _floor, "initial must be >= floor");
        require(_decayEpochs > 0, "decayEpochs must be > 0");
        initialRate = _initialRate;
        floor = _floor;
        decayEpochs = _decayEpochs;
    }

    function getEmission(uint64 epoch) external view override returns (uint256) {
        if (epoch >= decayEpochs) return floor;
        uint256 drop = ((initialRate - floor) * uint256(epoch)) / uint256(decayEpochs);
        return initialRate - drop;
    }
}
