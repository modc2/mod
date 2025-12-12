// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../interfaces/IBlocStaking.sol";

library BlocStorage {
    struct Layout {
        mapping(uint256 => IBlocStaking.Bloc) blocs;
        uint256 nextBlocId;
        mapping(uint256 => mapping(address => IBlocStaking.StakeInfo)) stakes;
        mapping(uint256 => IBlocStaking.ExclusiveAccess[]) exclusiveAccessSlots;
        uint256 pricePerInterval;
        uint256 intervalDuration;
        uint256 maxConcurrentUsers;
        uint256 treasuryBalance;
    }

    bytes32 internal constant STORAGE_SLOT = keccak256("bloc.staking.storage");

    function layout() internal pure returns (Layout storage l) {
        bytes32 slot = STORAGE_SLOT;
        assembly {
            l.slot := slot
        }
    }
}
