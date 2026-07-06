// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title Registry
 * @dev Minimal registry for managing asset metadata via data (JSON from datahash)
 */
contract Registry {
    struct Mod {
        address owner;
        string name;
        string data;
    }

    uint256 public nextModId = 1;
    
    mapping(uint256 => Mod) public mods;
    mapping(address => uint256[]) public userMods;
    mapping(address => mapping(string => bool)) public creatorNameExists;

    event ModRegistered(uint256 indexed modId, address indexed owner, string name, string data);
    event ModUpdated(uint256 indexed modId, string data);
    event ModRemoved(uint256 indexed modId, address indexed owner);
    event OwnershipTransferred(uint256 indexed modId, address indexed previousOwner, address indexed newOwner);

    modifier onlyModOwner(uint256 modId) {
        require(mods[modId].owner == msg.sender, "Not mod owner");
        _;
    }

    modifier modExists(uint256 modId) {
        require(mods[modId].owner != address(0), "Mod does not exist");
        _;
    }

    /**
     * @dev Register a new mod with name and data (JSON metadata)
     * @param name Unique name for the mod (unique per creator)
     * @param data IPFS hash or metadata reference describing the asset as JSON
     */
    function registerMod(
        string memory name,
        string memory data
    ) external returns (uint256) {
        require(bytes(name).length > 0, "Invalid name");
        require(bytes(data).length > 0, "Invalid data");
        require(!creatorNameExists[msg.sender][name], "Name already exists for this creator");
        
        uint256 id = nextModId++;
        mods[id] = Mod({
            owner: msg.sender,
            name: name,
            data: data
        });
        
        creatorNameExists[msg.sender][name] = true;
        userMods[msg.sender].push(id);
        emit ModRegistered(id, msg.sender, name, data);
        return id;
    }

    /**
     * @dev Update mod data (only owner)
     * @param modId Mod ID
     * @param data New data
     */
    function updateMod(
        uint256 modId,
        string memory data
    ) external onlyModOwner(modId) modExists(modId) {
        require(bytes(data).length > 0, "Invalid data");
        mods[modId].data = data;
        emit ModUpdated(modId, data);
    }

    /**
     * @dev Remove a mod (only owner)
     * @param modId Mod ID to remove
     */
    function removeMod(
        uint256 modId
    ) external onlyModOwner(modId) modExists(modId) {
        address owner = mods[modId].owner;
        string memory name = mods[modId].name;
        
        // Remove from userMods array
        uint256[] storage userModsList = userMods[owner];
        for (uint256 i = 0; i < userModsList.length; i++) {
            if (userModsList[i] == modId) {
                userModsList[i] = userModsList[userModsList.length - 1];
                userModsList.pop();
                break;
            }
        }
        
        // Free up the name for reuse
        creatorNameExists[owner][name] = false;
        
        // Delete the mod
        delete mods[modId];
        emit ModRemoved(modId, owner);
    }

    /**
     * @dev Transfer ownership of a mod (only owner)
     * @param modId Mod ID
     * @param newOwner New owner address
     */
    function transferOwnership(
        uint256 modId,
        address newOwner
    ) external onlyModOwner(modId) modExists(modId) {
        require(newOwner != address(0), "Invalid new owner");
        
        address previousOwner = mods[modId].owner;
        string memory name = mods[modId].name;
        
        // Check name uniqueness for new owner
        require(!creatorNameExists[newOwner][name], "Name already exists for new owner");
        
        // Remove from previous owner's list
        uint256[] storage prevOwnerMods = userMods[previousOwner];
        for (uint256 i = 0; i < prevOwnerMods.length; i++) {
            if (prevOwnerMods[i] == modId) {
                prevOwnerMods[i] = prevOwnerMods[prevOwnerMods.length - 1];
                prevOwnerMods.pop();
                break;
            }
        }
        
        // Update name mappings
        creatorNameExists[previousOwner][name] = false;
        creatorNameExists[newOwner][name] = true;
        
        // Add to new owner's list
        userMods[newOwner].push(modId);
        
        // Update ownership
        mods[modId].owner = newOwner;
        
        emit OwnershipTransferred(modId, previousOwner, newOwner);
    }

    /**
     * @dev Get mod details
     */
    function getMod(uint256 id) external view returns (
        address owner,
        string memory name,
        string memory data
    ) {
        Mod storage m = mods[id];
        return (m.owner, m.name, m.data);
    }

    /**
     * @dev Get user's mods
     */
    function getUserMods(address user) external view returns (uint256[] memory) {
        return userMods[user];
    }

    /**
     * @dev Check if name exists for creator
     */
    function isNameTaken(address creator, string memory name) external view returns (bool) {
        return creatorNameExists[creator][name];
    }
}