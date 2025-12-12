// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "../libraries/BlocStorage.sol";

library AdminModule {
    using SafeERC20 for IERC20;

    function setPricePerInterval(uint256 newPrice) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        s.pricePerInterval = newPrice;
    }

    function setIntervalDuration(uint256 newDuration) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        s.intervalDuration = newDuration;
    }

    function setMaxConcurrentUsers(uint256 newMax) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        s.maxConcurrentUsers = newMax;
    }

    function withdrawTreasury(IERC20 stakingToken, address recipient, uint256 amount) internal {
        BlocStorage.Layout storage s = BlocStorage.layout();
        require(amount <= s.treasuryBalance, "Insufficient treasury balance");
        s.treasuryBalance -= amount;
        stakingToken.safeTransfer(recipient, amount);
    }

    function getTreasuryBalance() internal view returns (uint256) {
        BlocStorage.Layout storage s = BlocStorage.layout();
        return s.treasuryBalance;
    }
}
