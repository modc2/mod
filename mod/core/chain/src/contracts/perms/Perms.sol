// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title Perms
 * @dev Permission system with parent key control, gas fee enforcement, and configurable limits
 */
contract Perms {
    // Mapping from a key (bytes) to an array of keys (bytes[])
    mapping(bytes => bytes[]) public Perms;
    
    // Mapping from parent key to owner address
    mapping(bytes => address) public keyOwners;
    
    // Configuration limits (only owner can change)
    uint256 public maxChildKeys = 100;
    uint256 public maxKeySize = 1024; // bytes
    address public owner;
    
    // Events
    event KeyAdded(bytes indexed parentKey, bytes childKey, address indexed addedBy);
    event KeyRemoved(bytes indexed parentKey, bytes childKey, address indexed removedBy);
    event KeysSet(bytes indexed parentKey, uint256 count, address indexed setBy);
    event OwnershipTransferred(bytes indexed parentKey, address indexed previousOwner, address indexed newOwner);
    event MaxChildKeysUpdated(uint256 newMax);
    event MaxKeySizeUpdated(uint256 newMax);
    event ContractSetOwnerless();

    modifier onlyContractOwner() {
        require(msg.sender == owner, "Only contract owner");
        _;
    }
    
    modifier onlyParentKeyOwner(bytes memory parentKey) {
        require(keyOwners[parentKey] == msg.sender, "Only parent key owner");
        _;
    }
    
    constructor() {
        owner = msg.sender;
    }
    
    /**
     * @dev Add a child key to a parent key's list (only parent key owner, user pays gas)
     * @param parentKey The parent key
     * @param childKey The child key to add to the list
     */
    function addKey(bytes memory parentKey, bytes memory childKey) public {
        require(childKey.length <= maxKeySize, "Key size exceeds limit");
        require(Perms[parentKey].length < maxChildKeys, "Max child keys reached");
        
        // Set owner on first add
        if (keyOwners[parentKey] == address(0)) {
            keyOwners[parentKey] = msg.sender;
        }
        
        require(keyOwners[parentKey] == msg.sender, "Only parent key owner can add");
        
        Perms[parentKey].push(childKey);
        emit KeyAdded(parentKey, childKey, msg.sender);
    }
    
    /**
     * @dev Remove a child key from a parent key's list by value (only parent key owner, user pays gas)
     * @param parentKey The parent key
     * @param childKey The child key to remove
     */
    function removeKey(bytes memory parentKey, bytes memory childKey) public onlyParentKeyOwner(parentKey) {
        bytes[] storage keys = Perms[parentKey];
        for (uint i = 0; i < keys.length; i++) {
            if (keccak256(keys[i]) == keccak256(childKey)) {
                keys[i] = keys[keys.length - 1];
                keys.pop();
                emit KeyRemoved(parentKey, childKey, msg.sender);
                return;
            }
        }
        revert("Key not found");
    }
    
    /**
     * @dev Remove a child key from a parent key's list by index (only parent key owner, user pays gas)
     * @param parentKey The parent key
     * @param index The index of the child key to remove
     */
    function removeKeyAtIndex(bytes memory parentKey, uint index) public onlyParentKeyOwner(parentKey) {
        bytes[] storage keys = Perms[parentKey];
        require(index < keys.length, "Index out of bounds");
        bytes memory removedKey = keys[index];
        keys[index] = keys[keys.length - 1];
        keys.pop();
        emit KeyRemoved(parentKey, removedKey, msg.sender);
    }
    
    /**
     * @dev Set a new list of keys, replacing existing ones (only parent key owner, user pays gas)
     * @param parentKey The parent key
     * @param childKeys Array of child keys to set
     */
    function setKeys(bytes memory parentKey, bytes[] memory childKeys) public {
        require(childKeys.length <= maxChildKeys, "Exceeds max child keys");
        
        // Set owner on first set
        if (keyOwners[parentKey] == address(0)) {
            keyOwners[parentKey] = msg.sender;
        }
        
        require(keyOwners[parentKey] == msg.sender, "Only parent key owner can set");
        
        // Validate all keys
        for (uint i = 0; i < childKeys.length; i++) {
            require(childKeys[i].length <= maxKeySize, "Key size exceeds limit");
        }
        
        // Clear existing and set new
        delete Perms[parentKey];
        for (uint i = 0; i < childKeys.length; i++) {
            Perms[parentKey].push(childKeys[i]);
        }
        
        emit KeysSet(parentKey, childKeys.length, msg.sender);
    }
    
    /**
     * @dev Transfer ownership of a parent key (only current owner, user pays gas)
     * @param parentKey The parent key
     * @param newOwner The new owner address
     */
    function transferKeyOwnership(bytes memory parentKey, address newOwner) public onlyParentKeyOwner(parentKey) {
        require(newOwner != address(0), "Invalid new owner");
        address previousOwner = keyOwners[parentKey];
        keyOwners[parentKey] = newOwner;
        emit OwnershipTransferred(parentKey, previousOwner, newOwner);
    }
    
    /**
     * @dev Permanently renounce ownership, making the contract fully decentralized.
     * Locks: setMaxChildKeys, setMaxKeySize, transferOwnership.
     * This action is irreversible.
     */
    function setOwnerless() external onlyContractOwner {
        emit ContractSetOwnerless();
        owner = address(0);
    }

    /**
     * @dev Update max child keys limit (only contract owner)
     * @param newMax New maximum number of child keys
     */
    function setMaxChildKeys(uint256 newMax) public onlyContractOwner {
        require(newMax > 0, "Max must be > 0");
        maxChildKeys = newMax;
        emit MaxChildKeysUpdated(newMax);
    }
    
    /**
     * @dev Update max key size limit (only contract owner)
     * @param newMax New maximum key size in bytes
     */
    function setMaxKeySize(uint256 newMax) public onlyContractOwner {
        require(newMax > 0, "Max must be > 0");
        maxKeySize = newMax;
        emit MaxKeySizeUpdated(newMax);
    }
    
    /**
     * @dev Transfer contract ownership (only contract owner)
     * @param newOwner New contract owner address
     */
    function transferOwnership(address newOwner) public onlyContractOwner {
        require(newOwner != address(0), "Invalid new owner");
        owner = newOwner;
    }
    
    /**
     * @dev Get all child keys for a parent key
     * @param parentKey The parent key to query
     * @return Array of child keys
     */
    function getKeys(bytes memory parentKey) public view returns (bytes[] memory) {
        return Perms[parentKey];
    }
    
    /**
     * @dev Get the count of child keys for a parent key
     * @param parentKey The parent key to query
     * @return Number of child keys
     */
    function getKeyCount(bytes memory parentKey) public view returns (uint) {
        return Perms[parentKey].length;
    }
    
    /**
     * @dev Get a specific child key by index
     * @param parentKey The parent key
     * @param index The index of the child key
     * @return The child key at the specified index
     */
    function getKeyAtIndex(bytes memory parentKey, uint index) public view returns (bytes memory) {
        require(index < Perms[parentKey].length, "Index out of bounds");
        return Perms[parentKey][index];
    }
    
    /**
     * @dev Get owner of a parent key
     * @param parentKey The parent key
     * @return Owner address
     */
    function getKeyOwner(bytes memory parentKey) public view returns (address) {
        return keyOwners[parentKey];
    }
}
