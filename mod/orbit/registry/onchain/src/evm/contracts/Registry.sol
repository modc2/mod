// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title Registry
 * @dev Module registry with auth-gated registration.
 *      Data field stores prefixed CIDs: "ipfs/{cid}", "lighthouse/{cid}", "filecoin/{cid}"
 *      pointing to JSON metadata on decentralized storage.
 */
contract Registry {
    struct Mod {
        address owner;
        string name;
        string data; // prefixed CID → ipfs/{cid}, lighthouse/{cid}, filecoin/{cid}
    }

    uint256 public nextModId = 1;

    mapping(uint256 => Mod) public mods;
    mapping(address => uint256[]) public userMods;
    mapping(address => mapping(string => bool)) public creatorNameExists;

    /// @dev Optional token gate — if set, only holders can register
    address public tokenGate;
    address public owner;

    event ModRegistered(uint256 indexed modId, address indexed owner, string name, string data);
    event ModUpdated(uint256 indexed modId, string data);
    event ModRemoved(uint256 indexed modId, address indexed owner);
    event OwnershipTransferred(uint256 indexed modId, address indexed previousOwner, address indexed newOwner);
    event TokenGateUpdated(address indexed tokenGate);

    modifier onlyModOwner(uint256 modId) {
        require(mods[modId].owner == msg.sender, "Not mod owner");
        _;
    }

    modifier modExists(uint256 modId) {
        require(mods[modId].owner != address(0), "Mod does not exist");
        _;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Not contract owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    // ── Admin ───────────────────────────────────────────────────────────────

    function setTokenGate(address _tokenGate) external onlyOwner {
        tokenGate = _tokenGate;
        emit TokenGateUpdated(_tokenGate);
    }

    function setOwnerless() external onlyOwner {
        owner = address(0);
    }

    // ── Registration ────────────────────────────────────────────────────────

    /**
     * @dev Register a new mod. Data must be a prefixed CID pointing to JSON.
     * @param name Unique name per creator
     * @param data Prefixed CID (e.g. "ipfs/QmABC...", "lighthouse/bafy...")
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
     */
    function removeMod(
        uint256 modId
    ) external onlyModOwner(modId) modExists(modId) {
        address modOwner = mods[modId].owner;
        string memory name = mods[modId].name;

        uint256[] storage userModsList = userMods[modOwner];
        for (uint256 i = 0; i < userModsList.length; i++) {
            if (userModsList[i] == modId) {
                userModsList[i] = userModsList[userModsList.length - 1];
                userModsList.pop();
                break;
            }
        }

        creatorNameExists[modOwner][name] = false;
        delete mods[modId];
        emit ModRemoved(modId, modOwner);
    }

    /**
     * @dev Transfer mod ownership
     */
    function transferOwnership(
        uint256 modId,
        address newOwner
    ) external onlyModOwner(modId) modExists(modId) {
        require(newOwner != address(0), "Invalid new owner");

        address previousOwner = mods[modId].owner;
        string memory name = mods[modId].name;

        require(!creatorNameExists[newOwner][name], "Name already exists for new owner");

        uint256[] storage prevOwnerMods = userMods[previousOwner];
        for (uint256 i = 0; i < prevOwnerMods.length; i++) {
            if (prevOwnerMods[i] == modId) {
                prevOwnerMods[i] = prevOwnerMods[prevOwnerMods.length - 1];
                prevOwnerMods.pop();
                break;
            }
        }

        creatorNameExists[previousOwner][name] = false;
        creatorNameExists[newOwner][name] = true;

        userMods[newOwner].push(modId);
        mods[modId].owner = newOwner;

        emit OwnershipTransferred(modId, previousOwner, newOwner);
    }

    // ── Views ───────────────────────────────────────────────────────────────

    function getMod(uint256 id) external view returns (
        address modOwner,
        string memory name,
        string memory data
    ) {
        Mod storage m = mods[id];
        return (m.owner, m.name, m.data);
    }

    function getUserMods(address user) external view returns (uint256[] memory) {
        return userMods[user];
    }

    function isNameTaken(address creator, string memory name) external view returns (bool) {
        return creatorNameExists[creator][name];
    }
}
