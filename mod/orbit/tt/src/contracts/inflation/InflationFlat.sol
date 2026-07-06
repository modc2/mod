// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IInflationCurve.sol";

/**
 * @title InflationFlat
 * @dev Constant emission — same amount every epoch.
 */
contract InflationFlat is IInflationCurve {
    uint256 public rate;

    constructor(uint256 _rate) {
        rate = _rate;
    }

    function getEmission(uint64) external view override returns (uint256) {
        return rate;
    }
}
