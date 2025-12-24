// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title Whitelist
 * @dev Smart contract for managing whitelisted wallet addresses
 * Compatible with Base, Ethereum, and other EVM chains
 */
contract Whitelist {
    address public owner;
    mapping(address => bool) private _whitelisted;
    address[] private _whitelistedList;
    mapping(address => uint256) private _whitelistedIndex;
    
    event AddressWhitelisted(address indexed account);
    event AddressRemoved(address indexed account);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
    
    constructor() {
        owner = msg.sender;
    }
    
    function addToWhitelist(address _address) external onlyOwner {
        require(_address != address(0), "Zero address");
        require(!_whitelisted[_address], "Already whitelisted");
        
        _whitelisted[_address] = true;
        _whitelistedIndex[_address] = _whitelistedList.length;
        _whitelistedList.push(_address);
        
        emit AddressWhitelisted(_address);
    }
    
    function addBatchToWhitelist(address[] calldata _addresses) external onlyOwner {
        for (uint256 i = 0; i < _addresses.length; i++) {
            if (_addresses[i] != address(0) && !_whitelisted[_addresses[i]]) {
                _whitelisted[_addresses[i]] = true;
                _whitelistedIndex[_addresses[i]] = _whitelistedList.length;
                _whitelistedList.push(_addresses[i]);
                emit AddressWhitelisted(_addresses[i]);
            }
        }
    }
    
    function removeFromWhitelist(address _address) external onlyOwner {
        require(_whitelisted[_address], "Not whitelisted");
        
        _whitelisted[_address] = false;
        
        uint256 index = _whitelistedIndex[_address];
        uint256 lastIndex = _whitelistedList.length - 1;
        
        if (index != lastIndex) {
            address lastAddress = _whitelistedList[lastIndex];
            _whitelistedList[index] = lastAddress;
            _whitelistedIndex[lastAddress] = index;
        }
        
        _whitelistedList.pop();
        delete _whitelistedIndex[_address];
        
        emit AddressRemoved(_address);
    }
    
    function isWhitelisted(address _address) external view returns (bool) {
        return _whitelisted[_address];
    }
    
    function getWhitelistedAddresses() external view returns (address[] memory) {
        return _whitelistedList;
    }
    
    function getWhitelistedCount() external view returns (uint256) {
        return _whitelistedList.length;
    }
    
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
