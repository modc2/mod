// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Factory.sol";
import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Pool.sol";
import "./libraries/UniswapV3OracleLibrary.sol";

/**
 * @title UniswapV3Oracle
 * @dev TWAP price oracle using Uniswap V3 pools on Base
 */
contract UniswapV3Oracle {
    address public immutable factory;
    uint32 public defaultTwapInterval = 1800; // 30 minutes

    // Base Uniswap V3 Factory: 0x33128a8fC17869897dcE68Ed026d694621f6FDfD
    constructor(address _factory) {
        require(_factory != address(0), "Invalid factory");
        factory = _factory;
    }

    /**
     * @notice Get TWAP price for a token pair
     * @param tokenIn Input token address
     * @param tokenOut Output token address (typically USDC or WETH)
     * @param fee Pool fee tier (500, 3000, or 10000)
     * @param twapInterval Time window for TWAP calculation
     * @return price Time-weighted average price
     */
    function getTWAP(
        address tokenIn,
        address tokenOut,
        uint24 fee,
        uint32 twapInterval
    ) public view returns (uint256 price) {
        address pool = IUniswapV3Factory(factory).getPool(tokenIn, tokenOut, fee);
        require(pool != address(0), "Pool does not exist");

        uint32 interval = twapInterval > 0 ? twapInterval : defaultTwapInterval;

        (int24 arithmeticMeanTick) = UniswapV3OracleLibrary.consult(pool, interval);

        // Calculate quote for 1 token (1e18)
        price = UniswapV3OracleLibrary.getQuoteAtTick(
            arithmeticMeanTick,
            uint128(1e18), // Amount in
            tokenIn,
            tokenOut
        );

        return price;
    }

    /**
     * @notice Get current spot price from pool
     * @param tokenIn Input token
     * @param tokenOut Output token
     * @param fee Pool fee tier
     */
    function getSpotPrice(
        address tokenIn,
        address tokenOut,
        uint24 fee
    ) external view returns (uint256) {
        address pool = IUniswapV3Factory(factory).getPool(tokenIn, tokenOut, fee);
        require(pool != address(0), "Pool does not exist");

        (uint160 sqrtPriceX96, , , , , , ) = IUniswapV3Pool(pool).slot0();

        // Convert sqrtPriceX96 to price
        // price = (sqrtPriceX96 / 2^96)^2
        uint256 price = uint256(sqrtPriceX96) * uint256(sqrtPriceX96) / (2**192);

        // Adjust for token decimals if needed
        return price * 1e18;
    }

    /**
     * @notice Check if pool exists for token pair
     */
    function poolExists(
        address tokenA,
        address tokenB,
        uint24 fee
    ) external view returns (bool) {
        return IUniswapV3Factory(factory).getPool(tokenA, tokenB, fee) != address(0);
    }
}
