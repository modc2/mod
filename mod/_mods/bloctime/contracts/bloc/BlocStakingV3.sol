// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "./interfaces/IBlocStaking.sol";
import "./libraries/BlocStorage.sol";
import "./modules/BlocRegistry.sol";
import "./modules/StakingModule.sol";
import "./modules/AccessModule.sol";
import "./modules/AdminModule.sol";
import "./modules/FeeDistributionModule.sol";

/**
 * @title BlocStakingV3
 * @notice Enhanced staking system with bloctime-based fee distribution
 * @dev Stakers earn fees proportional to their bloctime (amount * time_multiplier)
 */
contract BlocStakingV3 is Ownable, ReentrancyGuard, IBlocStaking {
    IERC20 public stakingToken;

    constructor(
        address _stakingToken,
        uint256 _pricePerInterval,
        uint256 _intervalDuration,
        uint256 _maxConcurrentUsers
    ) {
        stakingToken = IERC20(_stakingToken);
        BlocStorage.Layout storage s = BlocStorage.layout();
        s.pricePerInterval = _pricePerInterval;
        s.intervalDuration = _intervalDuration;
        s.maxConcurrentUsers = _maxConcurrentUsers;
    }

    // ========== BLOC REGISTRY FUNCTIONS ==========

    function registerBloc(string memory ipfsHash) external override returns (uint256) {
        return BlocRegistry.registerBloc(ipfsHash);
    }

    function updateBloc(uint256 blocId, string memory newIpfsHash) external override {
        BlocRegistry.updateBloc(blocId, newIpfsHash);
    }

    function transferBloc(uint256 blocId, address newCreator) external override {
        BlocRegistry.transferBloc(blocId, newCreator);
    }

    function deregisterBloc(uint256 blocId) external override {
        BlocRegistry.deregisterBloc(blocId);
    }

    // ========== STAKING FUNCTIONS ==========

    function stake(uint256 blocId, uint256 amount) external override nonReentrant {
        StakingModule.stake(stakingToken, blocId, amount);
    }

    function unstake(uint256 blocId, uint256 amount) external override nonReentrant {
        StakingModule.unstake(stakingToken, blocId, amount);
    }

    // ========== ACCESS FUNCTIONS ==========

    function purchaseExclusiveAccess(uint256 blocId, uint256 intervals) external override nonReentrant {
        AccessModule.purchaseExclusiveAccess(stakingToken, blocId, intervals);
        
        // Distribute fees to stakers based on bloctime
        BlocStorage.Layout storage s = BlocStorage.layout();
        uint256 feeAmount = s.pricePerInterval * intervals;
        FeeDistributionModule.distributeFees(blocId, feeAmount);
    }

    function hasExclusiveAccess(uint256 blocId, address user) external view override returns (bool) {
        return AccessModule.hasExclusiveAccess(blocId, user);
    }

    function getActiveSlots(uint256 blocId) external view override returns (uint256) {
        return AccessModule.getActiveSlots(blocId);
    }

    // ========== FEE DISTRIBUTION FUNCTIONS ==========

    /**
     * @notice Claim accumulated rewards from fee distribution
     * @param blocId The bloc to claim rewards from
     */
    function claimRewards(uint256 blocId) external nonReentrant {
        FeeDistributionModule.claimRewards(stakingToken, blocId);
    }

    /**
     * @notice Get staker information including bloctime and rewards
     * @param blocId The bloc identifier
     * @param user The staker address
     * @return amount Staked amount
     * @return bloctime Calculated bloctime value
     * @return pendingRewards Pending reward amount
     */
    function getStakerInfo(uint256 blocId, address user) external view returns (
        uint256 amount,
        uint256 bloctime,
        uint256 pendingRewards
    ) {
        return FeeDistributionModule.getStakerInfo(blocId, user);
    }

    /**
     * @notice Calculate bloctime for a user
     * @param blocId The bloc identifier
     * @param user The staker address
     * @return bloctime The calculated bloctime
     */
    function calculateBloctime(uint256 blocId, address user) external view returns (uint256) {
        return FeeDistributionModule.calculateBloctime(blocId, user);
    }

    // ========== VIEW FUNCTIONS ==========

    function getBlocInfo(uint256 blocId) external view override returns (
        address creator,
        string memory ipfsHash,
        uint256 totalStaked,
        bool active,
        uint256 activeSlots
    ) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        Bloc memory bloc = s.blocs[blocId];
        uint256 slots = AccessModule.getActiveSlots(blocId);
        
        return (
            bloc.creator,
            bloc.ipfsHash,
            bloc.totalStaked,
            bloc.active,
            slots
        );
    }

    function getStakeInfo(uint256 blocId, address user) external view override returns (
        uint256 amount,
        uint256 startTime
    ) {
        return StakingModule.getStakeInfo(blocId, user);
    }

    // ========== ADMIN FUNCTIONS ==========

    function setPricePerInterval(uint256 newPrice) external onlyOwner {
        AdminModule.setPricePerInterval(newPrice);
    }

    function setIntervalDuration(uint256 newDuration) external onlyOwner {
        AdminModule.setIntervalDuration(newDuration);
    }

    function setMaxConcurrentUsers(uint256 newMax) external onlyOwner {
        AdminModule.setMaxConcurrentUsers(newMax);
    }

    function withdrawTreasury(uint256 amount) external onlyOwner {
        AdminModule.withdrawTreasury(stakingToken, msg.sender, amount);
    }

    function getTreasuryBalance() external view returns (uint256) {
        return AdminModule.getTreasuryBalance();
    }
}
