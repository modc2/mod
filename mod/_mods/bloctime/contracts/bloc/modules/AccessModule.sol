// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "../libraries/BlocStorage.sol";
import "../interfaces/IBlocStaking.sol";

library AccessModule {
    using SafeERC20 for IERC20;

    event ExclusiveAccessPurchased(uint256 indexed blocId, address indexed user, uint256 slotIndex, uint256 intervals, uint256 amount);
    event ExclusiveAccessExpired(uint256 indexed blocId, uint256 slotIndex);

    function purchaseExclusiveAccess(IERC20 stakingToken, uint256 blocId, uint256 intervals) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        require(s.blocs[blocId].active, "Bloc not active");
        require(intervals > 0, "Intervals must be positive");
        
        _cleanupExpiredSlots(blocId);
        
        require(s.exclusiveAccessSlots[blocId].length < s.maxConcurrentUsers, "All slots occupied");
        
        uint256 cost = s.pricePerInterval * intervals;
        stakingToken.safeTransferFrom(msg.sender, address(this), cost);
        
        uint256 creatorShare = cost * 20 / 100;
        uint256 treasuryShare = cost - creatorShare;
        
        s.treasuryBalance += treasuryShare;
        stakingToken.safeTransfer(s.blocs[blocId].creator, creatorShare);
        
        uint256 slotIndex = s.exclusiveAccessSlots[blocId].length;
        s.exclusiveAccessSlots[blocId].push(IBlocStaking.ExclusiveAccess({
            user: msg.sender,
            expiresAt: block.timestamp + (intervals * s.intervalDuration),
            paidAmount: cost
        }));
        
        emit ExclusiveAccessPurchased(blocId, msg.sender, slotIndex, intervals, cost);
    }

    function _cleanupExpiredSlots(uint256 blocId) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        IBlocStaking.ExclusiveAccess[] storage slots = s.exclusiveAccessSlots[blocId];
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

    function hasExclusiveAccess(uint256 blocId, address user) internal view returns (bool) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        IBlocStaking.ExclusiveAccess[] memory slots = s.exclusiveAccessSlots[blocId];
        for (uint256 i = 0; i < slots.length; i++) {
            if (slots[i].user == user && block.timestamp < slots[i].expiresAt) {
                return true;
            }
        }
        return false;
    }

    function getActiveSlots(uint256 blocId) internal view returns (uint256) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        IBlocStaking.ExclusiveAccess[] memory slots = s.exclusiveAccessSlots[blocId];
        uint256 count = 0;
        for (uint256 i = 0; i < slots.length; i++) {
            if (block.timestamp < slots[i].expiresAt) {
                count++;
            }
        }
        return count;
    }
}
