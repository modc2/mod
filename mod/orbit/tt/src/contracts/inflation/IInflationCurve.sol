// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title IInflationCurve
 * @dev Interface for pluggable inflation/emission curves.
 *      Each consensus module can reference an IInflationCurve to determine
 *      how many tokens to mint per epoch. If no curve is set the consensus
 *      module falls back to its flat emissionRate.
 */
interface IInflationCurve {
    /// @notice Return the emission amount for the given epoch number.
    function getEmission(uint64 epoch) external view returns (uint256);
}
