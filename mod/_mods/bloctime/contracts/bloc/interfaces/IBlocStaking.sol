// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IBlocStaking {
    struct Bloc {
        address creator;
        string ipfsHash;
        uint256 totalStaked;
        bool active;
    }

    struct StakeInfo {
        uint256 amount;
        uint256 startTime;
    }

    struct ExclusiveAccess {
        address user;
        uint256 expiresAt;
        uint256 paidAmount;
    }

    event BlocRegistered(uint256 indexed blocId, address indexed creator, string ipfsHash);
    event BlocUpdated(uint256 indexed blocId, string newIpfsHash);
    event BlocDeregistered(uint256 indexed blocId);
    event BlocTransferred(uint256 indexed blocId, address indexed from, address indexed to);
    event Staked(uint256 indexed blocId, address indexed user, uint256 amount);
    event Unstaked(uint256 indexed blocId, address indexed user, uint256 amount);
    event ExclusiveAccessPurchased(uint256 indexed blocId, address indexed user, uint256 slotIndex, uint256 intervals, uint256 amount);
    event ExclusiveAccessExpired(uint256 indexed blocId, uint256 slotIndex);

    function registerBloc(string memory ipfsHash) external returns (uint256);
    function updateBloc(uint256 blocId, string memory newIpfsHash) external;
    function transferBloc(uint256 blocId, address newCreator) external;
    function deregisterBloc(uint256 blocId) external;
    function stake(uint256 blocId, uint256 amount) external;
    function unstake(uint256 blocId, uint256 amount) external;
    function purchaseExclusiveAccess(uint256 blocId, uint256 intervals) external;
    function hasExclusiveAccess(uint256 blocId, address user) external view returns (bool);
    function getActiveSlots(uint256 blocId) external view returns (uint256);
    function getBlocInfo(uint256 blocId) external view returns (address creator, string memory ipfsHash, uint256 totalStaked, bool active, uint256 activeSlots);
    function getStakeInfo(uint256 blocId, address user) external view returns (uint256 amount, uint256 startTime);
}
