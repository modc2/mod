// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "../IPriceOracleAdapter.sol";

interface IUniswapV3Pool {
    function slot0() external view returns (
        uint160 sqrtPriceX96,
        int24 tick,
        uint16 observationIndex,
        uint16 observationCardinality,
        uint16 observationCardinalityNext,
        uint8 feeProtocol,
        bool unlocked
    );
    function token0() external view returns (address);
    function token1() external view returns (address);
}

/**
 * @title UniswapAdapter
 * @dev Oracle adapter for Uniswap V3 TWAP prices
 */
contract UniswapAdapter is IPriceOracleAdapter, Ownable {
    
    string public constant override adapterName = "Uniswap";
    
    mapping(address => bool) public override supportsAsset;
    mapping(address => address) public pools; // asset => Uniswap V3 pool
    mapping(address => bool) public isToken0; // asset is token0 in pool
    
    address public priceOracle;
    uint256 public constant PRICE_DECIMALS = 18;
    
    event AssetAdded(address indexed asset, address indexed pool, bool isToken0);
    event AssetRemoved(address indexed asset);
    event PriceUpdated(address indexed asset, uint256 price);
    
    constructor(address _priceOracle) {
        priceOracle = _priceOracle;
    }
    
    /**
     * @dev Add supported asset with Uniswap pool
     */
    function addAsset(address _asset, address _pool) external onlyOwner {
        require(_asset != address(0), "Invalid asset");
        require(_pool != address(0), "Invalid pool");
        
        IUniswapV3Pool pool = IUniswapV3Pool(_pool);
        address token0 = pool.token0();
        address token1 = pool.token1();
        
        require(_asset == token0 || _asset == token1, "Asset not in pool");
        
        supportsAsset[_asset] = true;
        pools[_asset] = _pool;
        isToken0[_asset] = (_asset == token0);
        
        emit AssetAdded(_asset, _pool, isToken0[_asset]);
    }
    
    /**
     * @dev Remove supported asset
     */
    function removeAsset(address _asset) external onlyOwner {
        supportsAsset[_asset] = false;
        delete pools[_asset];
        delete isToken0[_asset];
        emit AssetRemoved(_asset);
    }
    
    /**
     * @dev Get price from Uniswap pool
     */
    function getPrice(address _asset) external view override returns (uint256) {
        require(supportsAsset[_asset], "Asset not supported");
        
        address pool = pools[_asset];
        require(pool != address(0), "No pool");
        
        IUniswapV3Pool uniPool = IUniswapV3Pool(pool);
        (uint160 sqrtPriceX96, , , , , , ) = uniPool.slot0();
        
        require(sqrtPriceX96 > 0, "Invalid price");
        
        // Calculate price from sqrtPriceX96
        uint256 price = _sqrtPriceX96ToPrice(sqrtPriceX96, isToken0[_asset]);
        
        return price;
    }
    
    /**
     * @dev Convert sqrtPriceX96 to price with 18 decimals
     */
    function _sqrtPriceX96ToPrice(uint160 sqrtPriceX96, bool _isToken0) internal pure returns (uint256) {
        uint256 price;
        
        if (_isToken0) {
            // Price of token0 in terms of token1
            price = (uint256(sqrtPriceX96) * uint256(sqrtPriceX96) * 1e18) >> 192;
        } else {
            // Price of token1 in terms of token0 (inverse)
            uint256 priceX192 = (uint256(sqrtPriceX96) * uint256(sqrtPriceX96));
            price = (1e18 << 192) / priceX192;
        }
        
        return price;
    }
    
    /**
     * @dev Update price in main oracle
     */
    function updatePrice(address _asset) external {
        require(supportsAsset[_asset], "Asset not supported");
        
        uint256 price = this.getPrice(_asset);
        
        (bool success,) = priceOracle.call(
            abi.encodeWithSignature("updatePrice(address,uint256)", _asset, price)
        );
        require(success, "Oracle update failed");
        
        emit PriceUpdated(_asset, price);
    }
}
