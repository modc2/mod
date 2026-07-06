// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title IPriceOracle
 * @notice Universal oracle interface for modular price feeds
 * @dev Implement this interface for any oracle type (Uniswap, Chainlink, Polymarket, etc.)
 */
interface IPriceOracle {
    /**
     * @notice Get current price for an asset
     * @param asset Asset identifier (address, pair name, or market ID)
     * @param data Additional oracle-specific data (ABI encoded)
     * @return price Current price scaled by 1e18
     * @return timestamp Time of price update
     * @return confidence Confidence level (0-100, where 100 = highest confidence)
     */
    function getPrice(
        bytes32 asset,
        bytes calldata data
    ) external view returns (
        uint256 price,
        uint256 timestamp,
        uint8 confidence
    );

    /**
     * @notice Check if oracle supports an asset
     * @param asset Asset identifier
     * @return bool True if supported
     */
    function supportsAsset(bytes32 asset) external view returns (bool);

    /**
     * @notice Get oracle metadata
     * @return name Oracle name
     * @return description Oracle description
     * @return oracleType Type (e.g., "CHAINLINK", "UNISWAP_V3", "POLYMARKET")
     */
    function getMetadata() external view returns (
        string memory name,
        string memory description,
        string memory oracleType
    );
}
