// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "../IPriceOracleAdapter.sol";

interface IPyth {
    struct Price {
        int64 price;
        uint64 conf;
        int32 expo;
        uint256 publishTime;
    }
    
    function getPriceUnsafe(bytes32 id) external view returns (Price memory price);
    function getPrice(bytes32 id) external view returns (Price memory price);
}

/**
 * @title PythAdapter
 * @dev Oracle adapter for Pyth Network price feeds
 */
contract PythAdapter is IPriceOracleAdapter, Ownable {
    
    string public constant override adapterName = "Pyth";
    
    mapping(address => bool) public override supportsAsset;
    mapping(address => bytes32) public priceIds; // asset => Pyth price ID
    
    address public priceOracle;
    address public pythContract;
    uint256 public constant PRICE_DECIMALS = 18;
    
    event AssetAdded(address indexed asset, bytes32 priceId);
    event AssetRemoved(address indexed asset);
    event PriceUpdated(address indexed asset, uint256 price);
    
    constructor(address _priceOracle, address _pythContract) {
        priceOracle = _priceOracle;
        pythContract = _pythContract;
    }
    
    /**
     * @dev Add supported asset with Pyth price ID
     */
    function addAsset(address _asset, bytes32 _priceId) external onlyOwner {
        require(_asset != address(0), "Invalid asset");
        require(_priceId != bytes32(0), "Invalid price ID");
        
        supportsAsset[_asset] = true;
        priceIds[_asset] = _priceId;
        emit AssetAdded(_asset, _priceId);
    }
    
    /**
     * @dev Remove supported asset
     */
    function removeAsset(address _asset) external onlyOwner {
        supportsAsset[_asset] = false;
        delete priceIds[_asset];
        emit AssetRemoved(_asset);
    }
    
    /**
     * @dev Get price from Pyth
     */
    function getPrice(address _asset) external view override returns (uint256) {
        require(supportsAsset[_asset], "Asset not supported");
        
        bytes32 priceId = priceIds[_asset];
        require(priceId != bytes32(0), "No price ID");
        
        IPyth pyth = IPyth(pythContract);
        IPyth.Price memory priceData = pyth.getPriceUnsafe(priceId);
        
        require(priceData.price > 0, "Invalid price");
        require(block.timestamp - priceData.publishTime < 1 hours, "Price stale");
        
        // Convert Pyth price to 18 decimals
        uint256 price = uint256(uint64(priceData.price));
        int32 expo = priceData.expo;
        
        if (expo < 0) {
            uint32 absExpo = uint32(-expo);
            if (absExpo < PRICE_DECIMALS) {
                price = price * (10 ** (PRICE_DECIMALS - absExpo));
            } else {
                price = price / (10 ** (absExpo - PRICE_DECIMALS));
            }
        } else {
            price = price * (10 ** (PRICE_DECIMALS + uint32(expo)));
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
    
    /**
     * @dev Update Pyth contract address
     */
    function updatePythContract(address _newPyth) external onlyOwner {
        require(_newPyth != address(0), "Invalid address");
        pythContract = _newPyth;
    }
}
