// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Registry {
    // Double map: owner => modId => ipfsHash
    mapping(address => mapping(uint256 => string)) private modules;
    
    // Track module ownership and existence
    mapping(address => mapping(uint256 => bool)) private moduleExists;
    
    // Events
    event ModuleRegistered(address indexed owner, uint256 indexed modId, string ipfsHash);
    event ModuleUpdated(address indexed owner, uint256 indexed modId, string ipfsHash);
    event ModuleDeregistered(address indexed owner, uint256 indexed modId);
    event ModuleTransferred(address indexed from, address indexed to, uint256 indexed modId, string ipfsHash);
    
    // Register a new module
    function register(uint256 modId, string memory ipfsHash) public {
        require(!moduleExists[msg.sender][modId], "Module already exists");
        require(bytes(ipfsHash).length > 0, "IPFS hash cannot be empty");
        
        modules[msg.sender][modId] = ipfsHash;
        moduleExists[msg.sender][modId] = true;
        
        emit ModuleRegistered(msg.sender, modId, ipfsHash);
    }
    
    // Update an existing module
    function update(uint256 modId, string memory ipfsHash) public {
        require(moduleExists[msg.sender][modId], "Module does not exist");
        require(bytes(ipfsHash).length > 0, "IPFS hash cannot be empty");
        
        modules[msg.sender][modId] = ipfsHash;
        
        emit ModuleUpdated(msg.sender, modId, ipfsHash);
    }
    
    // Deregister a module
    function deregister(uint256 modId) public {
        require(moduleExists[msg.sender][modId], "Module does not exist");
        
        delete modules[msg.sender][modId];
        delete moduleExists[msg.sender][modId];
        
        emit ModuleDeregistered(msg.sender, modId);
    }
    
    // Transfer module to another owner
    function transfer(address to, uint256 modId) public {
        require(moduleExists[msg.sender][modId], "Module does not exist");
        require(to != address(0), "Invalid recipient address");
        require(to != msg.sender, "Cannot transfer to yourself");
        require(!moduleExists[to][modId], "Module already exists for recipient");
        
        string memory ipfsHash = modules[msg.sender][modId];
        
        // Remove from sender
        delete modules[msg.sender][modId];
        delete moduleExists[msg.sender][modId];
        
        // Add to recipient
        modules[to][modId] = ipfsHash;
        moduleExists[to][modId] = true;
        
        emit ModuleTransferred(msg.sender, to, modId, ipfsHash);
    }
    
    // Get module IPFS hash
    function getModule(address owner, uint256 modId) public view returns (string memory) {
        require(moduleExists[owner][modId], "Module does not exist");
        return modules[owner][modId];
    }
    
    // Check if module exists
    function exists(address owner, uint256 modId) public view returns (bool) {
        return moduleExists[owner][modId];
    }
}
