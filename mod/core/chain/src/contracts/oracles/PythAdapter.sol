// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "./IOracleAdapter.sol";

/**
 * @title Pyth Network Interface
 */
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
contract PythAdapter is IOracleAdapter {
    IPyth public pyth;
    
    // Mapping from token address to Pyth price feed ID
    mapping(address => bytes32) public priceIds;
    
    address public owner;
    
    event PriceIdSet(address indexed token, bytes32 indexed priceId);
    event PriceIdRemoved(address indexed token);
    event ContractSetOwnerless();

    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner");
        _;
    }
    
    /**
     * @param _pyth Address of the Pyth contract
     */
    constructor(address _pyth) {
        require(_pyth != address(0), "Invalid Pyth address");
        pyth = IPyth(_pyth);
        owner = msg.sender;
    }
    
    /**
     * @dev Permanently renounce ownership, making the contract fully decentralized.
     * Locks: setPriceId, removePriceId, transferOwnership.
     * This action is irreversible.
     */
    function setOwnerless() external onlyOwner {
        emit ContractSetOwnerless();
        owner = address(0);
    }

    /**
     * @dev Set Pyth price ID for a token
     * @param token Token address
     * @param priceId Pyth price feed ID (32 bytes)
     */
    function setPriceId(address token, bytes32 priceId) external onlyOwner {
        require(token != address(0), "Invalid token");
        require(priceId != bytes32(0), "Invalid price ID");
        
        priceIds[token] = priceId;
        emit PriceIdSet(token, priceId);
    }
    
    /**
     * @dev Remove price ID for a token
     * @param token Token address
     */
    function removePriceId(address token) external onlyOwner {
        require(priceIds[token] != bytes32(0), "Price ID not set");
        
        delete priceIds[token];
        emit PriceIdRemoved(token);
    }
    
    /**
     * @dev Get the latest price for a token from Pyth
     * @param token Address of the token
     * @return price Latest price normalized to 8 decimals
     * @return decimals Number of decimals (always 8 for consistency)
     * @return timestamp Last update timestamp
     */
    function getPrice(address token) external view override returns (
        uint256 price,
        uint8 decimals,
        uint256 timestamp
    ) {
        bytes32 priceId = priceIds[token];
        require(priceId != bytes32(0), "Price ID not set");
        
        IPyth.Price memory pythPrice = pyth.getPriceUnsafe(priceId);
        
        require(pythPrice.price > 0, "Invalid price");
        require(pythPrice.publishTime > 0, "Price not published");
        
        // Normalize to 8 decimals
        // Pyth expo is negative (e.g., -8 means 8 decimals)
        int32 targetDecimals = 8;
        int32 adjustment = targetDecimals + pythPrice.expo;
        
        uint256 normalizedPrice;
        if (adjustment >= 0) {
            normalizedPrice = uint256(uint64(pythPrice.price)) * (10 ** uint32(adjustment));
        } else {
            normalizedPrice = uint256(uint64(pythPrice.price)) / (10 ** uint32(-adjustment));
        }
        
        return (
            normalizedPrice,
            8,
            pythPrice.publishTime
        );
    }
    
    /**
     * @dev Check if price feed exists for a token
     * @param token Address of the token
     * @return exists True if price feed is available
     */
    function hasPriceFeed(address token) external view override returns (bool exists) {
        return priceIds[token] != bytes32(0);
    }
    
    /**
     * @dev Transfer ownership
     * @param newOwner New owner address
     */
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Invalid owner");
        owner = newOwner;
    }
}
