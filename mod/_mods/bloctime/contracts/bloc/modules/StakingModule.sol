// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "../libraries/BlocStorage.sol";
import "../interfaces/IBlocStaking.sol";

library StakingModule {
    using SafeERC20 for IERC20;

    event Staked(uint256 indexed blocId, address indexed user, uint256 amount);
    event Unstaked(uint256 indexed blocId, address indexed user, uint256 amount);

    function stake(IERC20 stakingToken, uint256 blocId, uint256 amount) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        require(s.blocs[blocId].active, "Bloc not active");
        require(amount > 0, "Cannot stake 0");
        
        stakingToken.safeTransferFrom(msg.sender, address(this), amount);
        
        IBlocStaking.StakeInfo storage stakeInfo = s.stakes[blocId][msg.sender];
        stakeInfo.amount += amount;
        if (stakeInfo.startTime == 0) {
            stakeInfo.startTime = block.timestamp;
        }
        
        s.blocs[blocId].totalStaked += amount;
        
        emit Staked(blocId, msg.sender, amount);
    }

    function unstake(IERC20 stakingToken, uint256 blocId, uint256 amount) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        IBlocStaking.StakeInfo storage stakeInfo = s.stakes[blocId][msg.sender];
        require(stakeInfo.amount >= amount, "Insufficient stake");
        
        stakeInfo.amount -= amount;
        s.blocs[blocId].totalStaked -= amount;
        
        stakingToken.safeTransfer(msg.sender, amount);
        
        emit Unstaked(blocId, msg.sender, amount);
    }

    function getStakeInfo(uint256 blocId, address user) internal view returns (uint256 amount, uint256 startTime) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        IBlocStaking.StakeInfo memory stakeInfo = s.stakes[blocId][user];
        return (stakeInfo.amount, stakeInfo.startTime);
    }
}
