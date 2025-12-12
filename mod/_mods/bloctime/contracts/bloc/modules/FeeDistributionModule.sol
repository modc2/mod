// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "../libraries/BlocStorage.sol";
import "../interfaces/IBlocStaking.sol";

/**
 * @title FeeDistributionModule
 * @notice Distributes fees from interval rentals to stakers based on their bloctime
 * @dev Bloctime = staked_amount * time_multiplier
 */
library FeeDistributionModule {
    using SafeERC20 for IERC20;

    event FeesDistributed(uint256 indexed blocId, uint256 totalFees, uint256 timestamp);
    event RewardsClaimed(uint256 indexed blocId, address indexed user, uint256 amount);

    /**
     * @notice Calculate bloctime for a user's stake
     * @param blocId The bloc identifier
     * @param user The staker address
     * @return bloctime The calculated bloctime value
     */
    function calculateBloctime(uint256 blocId, address user) internal view returns (uint256) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        IBlocStaking.StakeInfo memory stake = s.stakes[blocId][user];
        
        if (stake.amount == 0) return 0;
        
        uint256 timeStaked = block.timestamp - stake.startTime;
        uint256 daysStaked = timeStaked / 1 days;
        
        // Time multiplier: 1x to 2x over 365 days (linear)
        uint256 timeMultiplier = 10000; // Base 1x = 10000 basis points
        if (daysStaked > 0) {
            uint256 bonusMultiplier = (daysStaked * 10000) / 365;
            if (bonusMultiplier > 10000) bonusMultiplier = 10000; // Cap at 2x total
            timeMultiplier += bonusMultiplier;
        }
        
        // Bloctime = amount * timeMultiplier / 10000
        return (stake.amount * timeMultiplier) / 10000;
    }

    /**
     * @notice Calculate total bloctime for a bloc across all stakers
     * @param blocId The bloc identifier
     * @return totalBloctime The sum of all stakers' bloctime
     */
    function getTotalBloctime(uint256 blocId) internal view returns (uint256) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        // Note: In production, maintain a stakers array per bloc for iteration
        // For now, this is a placeholder - actual implementation needs staker tracking
        return s.blocs[blocId].totalStaked; // Simplified - needs proper iteration
    }

    /**
     * @notice Distribute fees from interval purchases to stakers
     * @param blocId The bloc that generated fees
     * @param feeAmount The amount of fees to distribute
     */
    function distributeFees(uint256 blocId, uint256 feeAmount) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        require(s.blocs[blocId].active, "Bloc not active");
        
        // Add fees to bloc's pending distribution pool
        // Note: Actual distribution happens on claim to save gas
        s.treasuryBalance += feeAmount;
        
        emit FeesDistributed(blocId, feeAmount, block.timestamp);
    }

    /**
     * @notice Calculate pending rewards for a staker
     * @param blocId The bloc identifier
     * @param user The staker address
     * @return rewards The amount of pending rewards
     */
    function calculatePendingRewards(uint256 blocId, address user) internal view returns (uint256) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        
        uint256 userBloctime = calculateBloctime(blocId, user);
        if (userBloctime == 0) return 0;
        
        uint256 totalBloctime = getTotalBloctime(blocId);
        if (totalBloctime == 0) return 0;
        
        // User's share = (userBloctime / totalBloctime) * treasuryBalance
        return (s.treasuryBalance * userBloctime) / totalBloctime;
    }

    /**
     * @notice Claim accumulated rewards for a staker
     * @param stakingToken The token used for rewards
     * @param blocId The bloc identifier
     */
    function claimRewards(IERC20 stakingToken, uint256 blocId) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        
        uint256 rewards = calculatePendingRewards(blocId, msg.sender);
        require(rewards > 0, "No rewards to claim");
        
        s.treasuryBalance -= rewards;
        stakingToken.safeTransfer(msg.sender, rewards);
        
        emit RewardsClaimed(blocId, msg.sender, rewards);
    }

    /**
     * @notice Get staker info including bloctime and pending rewards
     * @param blocId The bloc identifier
     * @param user The staker address
     * @return amount Staked amount
     * @return bloctime Calculated bloctime
     * @return pendingRewards Pending reward amount
     */
    function getStakerInfo(uint256 blocId, address user) internal view returns (
        uint256 amount,
        uint256 bloctime,
        uint256 pendingRewards
    ) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        IBlocStaking.StakeInfo memory stake = s.stakes[blocId][user];
        
        return (
            stake.amount,
            calculateBloctime(blocId, user),
            calculatePendingRewards(blocId, user)
        );
    }
}
