// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

/**
 * @title BlocTimeRegistry
 * @dev Modular registry for managing module metadata and ownership
 * Separated from marketplace logic for better modularity
 */
contract BlocTimeRegistry is ReentrancyGuard {
    struct Module {
        address owner;
        uint256 pricePerBlock;
        uint256 maxConcurrentUsers;
        uint256 currentUsers;
        bool active;
        string ipfsHash;
    }

    uint256 public nextModuleId = 1;
    mapping(uint256 => Module) public modules;
    mapping(address => uint256[]) public userModules;

    event ModuleRegistered(uint256 indexed moduleId, address indexed owner, uint256 pricePerBlock);
    event ModuleUpdated(uint256 indexed moduleId, uint256 pricePerBlock, uint256 maxUsers);
    event ModuleDeactivated(uint256 indexed moduleId);
    event UserCountChanged(uint256 indexed moduleId, uint256 currentUsers);

    modifier onlyModuleOwner(uint256 moduleId) {
        require(modules[moduleId].owner == msg.sender, "Not module owner");
        _;
    }

    function registerModule(
        uint256 pricePerBlock,
        uint256 maxUsers,
        string memory ipfsHash
    ) external returns (uint256) {
        require(pricePerBlock > 0, "Invalid price");
        require(maxUsers > 0, "Invalid max users");
        require(bytes(ipfsHash).length > 0, "Invalid IPFS hash");
        
        uint256 id = nextModuleId++;
        modules[id] = Module({
            owner: msg.sender,
            pricePerBlock: pricePerBlock,
            maxConcurrentUsers: maxUsers,
            currentUsers: 0,
            active: true,
            ipfsHash: ipfsHash
        });
        
        userModules[msg.sender].push(id);
        emit ModuleRegistered(id, msg.sender, pricePerBlock);
        return id;
    }

    function updateModule(
        uint256 moduleId,
        uint256 pricePerBlock,
        uint256 maxUsers
    ) external onlyModuleOwner(moduleId) {
        Module storage m = modules[moduleId];
        require(m.active, "Module not active");
        require(maxUsers >= m.currentUsers, "Max users below current");
        
        m.pricePerBlock = pricePerBlock;
        m.maxConcurrentUsers = maxUsers;
        emit ModuleUpdated(moduleId, pricePerBlock, maxUsers);
    }

    function deactivateModule(uint256 moduleId) external onlyModuleOwner(moduleId) {
        modules[moduleId].active = false;
        emit ModuleDeactivated(moduleId);
    }

    function incrementUsers(uint256 moduleId) external {
        Module storage m = modules[moduleId];
        require(m.active, "Module not active");
        require(m.currentUsers < m.maxConcurrentUsers, "Max users reached");
        m.currentUsers++;
        emit UserCountChanged(moduleId, m.currentUsers);
    }

    function decrementUsers(uint256 moduleId) external {
        Module storage m = modules[moduleId];
        require(m.currentUsers > 0, "No users to decrement");
        m.currentUsers--;
        emit UserCountChanged(moduleId, m.currentUsers);
    }

    function getModule(uint256 id) external view returns (
        address owner,
        uint256 pricePerBlock,
        uint256 maxConcurrentUsers,
        uint256 currentUsers,
        bool active,
        string memory ipfsHash
    ) {
        Module storage m = modules[id];
        return (m.owner, m.pricePerBlock, m.maxConcurrentUsers, m.currentUsers, m.active, m.ipfsHash);
    }

    function isModuleAvailable(uint256 moduleId) external view returns (bool) {
        Module storage m = modules[moduleId];
        return m.active && m.currentUsers < m.maxConcurrentUsers;
    }

    function getUserModules(address user) external view returns (uint256[] memory) {
        return userModules[user];
    }
}