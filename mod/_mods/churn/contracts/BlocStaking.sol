// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

contract BlocStaking is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

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

    IERC20 public stakingToken;
    
    mapping(uint256 => Bloc) public blocs;
    uint256 public nextBlocId;
    
    mapping(uint256 => mapping(address => StakeInfo)) public stakes;
    mapping(uint256 => ExclusiveAccess[]) public exclusiveAccessSlots;
    
    uint256 public pricePerInterval;
    uint256 public intervalDuration;
    uint256 public maxConcurrentUsers;
    uint256 public treasuryBalance;

    event BlocRegistered(uint256 indexed blocId, address indexed creator, string ipfsHash);
    event BlocUpdated(uint256 indexed blocId, string newIpfsHash);
    event BlocDeregistered(uint256 indexed blocId);
    event BlocTransferred(uint256 indexed blocId, address indexed from, address indexed to);
    event Staked(uint256 indexed blocId, address indexed user, uint256 amount);
    event Unstaked(uint256 indexed blocId, address indexed user, uint256 amount);
    event ExclusiveAccessPurchased(uint256 indexed blocId, address indexed user, uint256 slotIndex, uint256 intervals, uint256 amount);
    event ExclusiveAccessExpired(uint256 indexed blocId, uint256 slotIndex);

    constructor(address _stakingToken, uint256 _pricePerInterval, uint256 _intervalDuration, uint256 _maxConcurrentUsers) {
        stakingToken = IERC20(_stakingToken);
        pricePerInterval = _pricePerInterval;
        intervalDuration = _intervalDuration;
        maxConcurrentUsers = _maxConcurrentUsers;
    }

    function registerBloc(string memory ipfsHash) external returns (uint256) {
        require(bytes(ipfsHash).length > 0, "IPFS hash cannot be empty");
        
        uint256 blocId = nextBlocId++;
        blocs[blocId] = Bloc({
            creator: msg.sender,
            ipfsHash: ipfsHash,
            totalStaked: 0,
            active: true
        });
        
        emit BlocRegistered(blocId, msg.sender, ipfsHash);
        return blocId;
    }

    function updateBloc(uint256 blocId, string memory newIpfsHash) external {
        require(blocs[blocId].active, "Bloc not active");
        require(blocs[blocId].creator == msg.sender, "Only creator can update");
        require(bytes(newIpfsHash).length > 0, "IPFS hash cannot be empty");
        
        blocs[blocId].ipfsHash = newIpfsHash;
        emit BlocUpdated(blocId, newIpfsHash);
    }

    function transferBloc(uint256 blocId, address newCreator) external {
        require(blocs[blocId].active, "Bloc not active");
        require(blocs[blocId].creator == msg.sender, "Only creator can transfer");
        require(newCreator != address(0), "Invalid new creator");
        
        address oldCreator = blocs[blocId].creator;
        blocs[blocId].creator = newCreator;
        emit BlocTransferred(blocId, oldCreator, newCreator);
    }

    function deregisterBloc(uint256 blocId) external {
        require(blocs[blocId].active, "Bloc not active");
        require(blocs[blocId].creator == msg.sender, "Only creator can deregister");
        require(blocs[blocId].totalStaked == 0, "Cannot deregister with active stakes");
        
        blocs[blocId].active = false;
        emit BlocDeregistered(blocId);
    }

    function stake(uint256 blocId, uint256 amount) external nonReentrant {
        require(blocs[blocId].active, "Bloc not active");
        require(amount > 0, "Cannot stake 0");
        
        stakingToken.safeTransferFrom(msg.sender, address(this), amount);
        
        StakeInfo storage stakeInfo = stakes[blocId][msg.sender];
        stakeInfo.amount += amount;
        if (stakeInfo.startTime == 0) {
            stakeInfo.startTime = block.timestamp;
        }
        
        blocs[blocId].totalStaked += amount;
        
        emit Staked(blocId, msg.sender, amount);
    }

    function unstake(uint256 blocId, uint256 amount) external nonReentrant {
        StakeInfo storage stakeInfo = stakes[blocId][msg.sender];
        require(stakeInfo.amount >= amount, "Insufficient stake");
        
        stakeInfo.amount -= amount;
        blocs[blocId].totalStaked -= amount;
        
        stakingToken.safeTransfer(msg.sender, amount);
        
        emit Unstaked(blocId, msg.sender, amount);
    }

    function purchaseExclusiveAccess(uint256 blocId, uint256 intervals) external nonReentrant {
        require(blocs[blocId].active, "Bloc not active");
        require(intervals > 0, "Intervals must be positive");
        
        _cleanupExpiredSlots(blocId);
        
        require(exclusiveAccessSlots[blocId].length < maxConcurrentUsers, "All slots occupied");
        
        uint256 cost = pricePerInterval * intervals;
        stakingToken.safeTransferFrom(msg.sender, address(this), cost);
        
        uint256 creatorShare = cost * 20 / 100;
        uint256 treasuryShare = cost - creatorShare;
        
        treasuryBalance += treasuryShare;
        stakingToken.safeTransfer(blocs[blocId].creator, creatorShare);
        
        uint256 slotIndex = exclusiveAccessSlots[blocId].length;
        exclusiveAccessSlots[blocId].push(ExclusiveAccess({
            user: msg.sender,
            expiresAt: block.timestamp + (intervals * intervalDuration),
            paidAmount: cost
        }));
        
        emit ExclusiveAccessPurchased(blocId, msg.sender, slotIndex, intervals, cost);
    }

    function _cleanupExpiredSlots(uint256 blocId) internal {
        ExclusiveAccess[] storage slots = exclusiveAccessSlots[blocId];
        uint256 i = 0;
        while (i < slots.length) {
            if (block.timestamp >= slots[i].expiresAt) {
                emit ExclusiveAccessExpired(blocId, i);
                slots[i] = slots[slots.length - 1];
                slots.pop();
            } else {
                i++;
            }
        }
    }

    function hasExclusiveAccess(uint256 blocId, address user) external view returns (bool) {
        ExclusiveAccess[] memory slots = exclusiveAccessSlots[blocId];
        for (uint256 i = 0; i < slots.length; i++) {
            if (slots[i].user == user && block.timestamp < slots[i].expiresAt) {
                return true;
            }
        }
        return false;
    }

    function getActiveSlots(uint256 blocId) external view returns (uint256) {
        ExclusiveAccess[] memory slots = exclusiveAccessSlots[blocId];
        uint256 count = 0;
        for (uint256 i = 0; i < slots.length; i++) {
            if (block.timestamp < slots[i].expiresAt) {
                count++;
            }
        }
        return count;
    }

    function getBlocInfo(uint256 blocId) external view returns (
        address creator,
        string memory ipfsHash,
        uint256 totalStaked,
        bool active,
        uint256 activeSlots
    ) {
        Bloc memory bloc = blocs[blocId];
        uint256 slots = 0;
        ExclusiveAccess[] memory accessSlots = exclusiveAccessSlots[blocId];
        for (uint256 i = 0; i < accessSlots.length; i++) {
            if (block.timestamp < accessSlots[i].expiresAt) {
                slots++;
            }
        }
        
        return (
            bloc.creator,
            bloc.ipfsHash,
            bloc.totalStaked,
            bloc.active,
            slots
        );
    }

    function getStakeInfo(uint256 blocId, address user) external view returns (
        uint256 amount,
        uint256 startTime
    ) {
        StakeInfo memory stakeInfo = stakes[blocId][user];
        return (stakeInfo.amount, stakeInfo.startTime);
    }

    function setPricePerInterval(uint256 newPrice) external onlyOwner {
        pricePerInterval = newPrice;
    }

    function setIntervalDuration(uint256 newDuration) external onlyOwner {
        intervalDuration = newDuration;
    }

    function setMaxConcurrentUsers(uint256 newMax) external onlyOwner {
        maxConcurrentUsers = newMax;
    }

    function withdrawTreasury(uint256 amount) external onlyOwner {
        require(amount <= treasuryBalance, "Insufficient treasury balance");
        treasuryBalance -= amount;
        stakingToken.safeTransfer(msg.sender, amount);
    }
}
