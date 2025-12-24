// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title StringDelegate
 * @dev Smart contract for delegating strings to specific addresses
 * Compatible with Base and Ganache networks
 */
contract StringDelegate {
    
    // Mapping from address to their delegated strings
    mapping(address => string[]) private delegatedStrings;
    
    // Mapping to track who delegated what string to whom
    mapping(address => mapping(address => string[])) private delegations;
    
    // Events
    event StringDelegated(address indexed from, address indexed to, string value);
    event StringRevoked(address indexed from, address indexed to, uint256 index);
    
    /**
     * @dev Delegate a string to a specific address
     * @param _to The address to delegate the string to
     * @param _value The string value to delegate
     */
    function delegateString(address _to, string memory _value) external {
        require(_to != address(0), "Cannot delegate to zero address");
        require(bytes(_value).length > 0, "String cannot be empty");
        
        delegatedStrings[_to].push(_value);
        delegations[msg.sender][_to].push(_value);
        
        emit StringDelegated(msg.sender, _to, _value);
    }
    
    /**
     * @dev Get all strings delegated to an address
     * @param _address The address to query
     * @return Array of delegated strings
     */
    function getDelegatedStrings(address _address) external view returns (string[] memory) {
        return delegatedStrings[_address];
    }
    
    /**
     * @dev Get strings delegated by sender to a specific address
     * @param _to The recipient address
     * @return Array of strings delegated by sender to _to
     */
    function getMyDelegationsTo(address _to) external view returns (string[] memory) {
        return delegations[msg.sender][_to];
    }
    
    /**
     * @dev Get count of delegated strings for an address
     * @param _address The address to query
     * @return Count of delegated strings
     */
    function getDelegatedCount(address _address) external view returns (uint256) {
        return delegatedStrings[_address].length;
    }
    
    /**
     * @dev Get a specific delegated string by index
     * @param _address The address to query
     * @param _index The index of the string
     * @return The delegated string at the index
     */
    function getDelegatedStringAt(address _address, uint256 _index) external view returns (string memory) {
        require(_index < delegatedStrings[_address].length, "Index out of bounds");
        return delegatedStrings[_address][_index];
    }
}
