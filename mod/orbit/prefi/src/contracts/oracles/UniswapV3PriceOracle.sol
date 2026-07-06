// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "../interfaces/IPriceOracle.sol";
import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Factory.sol";
import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Pool.sol";
import "../libraries/UniswapV3OracleLibrary.sol";

/**
 * @title UniswapV3PriceOracle
 * @notice Price oracle using Uniswap V3 TWAP
 */
contract UniswapV3PriceOracle is IPriceOracle {
    address public immutable factory;
    uint32 public defaultTwapInterval = 1800; // 30 minutes

    struct UniswapV3Data {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        uint32 twapInterval;
    }

    constructor(address _factory) {
        require(_factory != address(0), "Invalid factory");
        factory = _factory;
    }

    /**
     * @notice Get TWAP price from Uniswap V3
     * @param asset Asset identifier (not used, data contains token addresses)
     * @param data ABI encoded UniswapV3Data struct
     */
    function getPrice(
        bytes32 asset,
        bytes calldata data
    ) external view override returns (
        uint256 price,
        uint256 timestamp,
        uint8 confidence
    ) {
        UniswapV3Data memory params = abi.decode(data, (UniswapV3Data));

        address pool = IUniswapV3Factory(factory).getPool(
            params.tokenIn,
            params.tokenOut,
            params.fee
        );
        require(pool != address(0), "Pool does not exist");

        uint32 interval = params.twapInterval > 0 ? params.twapInterval : defaultTwapInterval;

        (int24 arithmeticMeanTick) = UniswapV3OracleLibrary.consult(pool, interval);

        price = UniswapV3OracleLibrary.getQuoteAtTick(
            arithmeticMeanTick,
            uint128(1e18),
            params.tokenIn,
            params.tokenOut
        );

        timestamp = block.timestamp;
        confidence = 95; // High confidence for Uniswap V3

        return (price, timestamp, confidence);
    }

    function supportsAsset(bytes32) external pure override returns (bool) {
        return true; // Supports any Uniswap V3 pair
    }

    function getMetadata() external pure override returns (
        string memory name,
        string memory description,
        string memory oracleType
    ) {
        return (
            "Uniswap V3 TWAP Oracle",
            "Time-weighted average price from Uniswap V3 liquidity pools",
            "UNISWAP_V3"
        );
    }
}
