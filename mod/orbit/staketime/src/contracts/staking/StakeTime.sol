// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./Staking.sol";

/**
 * @title StakeTime (STT)
 * @dev ERC20 token that converts a native ERC20 into time-locked stake.
 *
 *      Deposit nativeToken → lock for N blocks → receive STT proportional
 *      to amount × multiplier(lockBlocks). Longer locks earn more STT via
 *      a configurable piecewise-linear multiplier curve.
 *
 *      Extends the Staking base to inherit the validator framework,
 *      epoch management, and admin controls. Adds:
 *        - stakeOn()        deposit + lock + mint STT on a validator
 *        - unstakeFrom()    burn STT + unlock + return nativeToken
 *        - slashValidator() slash all positions on a validator
 *        - setPoints()      configure the lock-time multiplier curve
 */
contract StakeTime is Staking {
    using SafeERC20 for IERC20;

    // ── Multiplier Curve ──────────────────────────────────────────────────────

    struct Point {
        uint256 blocks;
        uint256 multiplier; // basis points (10000 = 1x)
    }

    event PointsSet(uint256 pointCount);

    Point[] public points;

    constructor(
        address _nativeToken,
        uint256 _maxLockBlocks,
        uint256 _maxStakersPerValidator,
        uint256 _defaultCommissionBps
    ) ERC20("StakeTime", "STT") {
        _initStaking(_nativeToken, _maxLockBlocks, _maxStakersPerValidator, _defaultCommissionBps);
        points.push(Point({ blocks: 0, multiplier: 10000 }));
    }

    // ── Token Conversion: ERC20 → Locked STT ─────────────────────────────────

    function stakeOn(
        string calldata validatorKey,
        uint256 amount,
        uint256 lockBlocks
    ) external nonReentrant {
        require(amount > 0, "amount must be > 0");
        require(lockBlocks <= maxLockBlocks, "exceeds max lock blocks");

        bytes32 kh = keccak256(abi.encodePacked(validatorKey));
        Validator storage v = validators[kh];
        require(v.active, "validator not active");

        if (!hasStakedInEpoch[kh][currentEpoch][msg.sender]) {
            validatorStakerCountPerEpoch[kh][currentEpoch]++;
            require(
                validatorStakerCountPerEpoch[kh][currentEpoch] <= maxStakersPerValidator,
                "max stakers reached for this validator this epoch"
            );
            hasStakedInEpoch[kh][currentEpoch][msg.sender] = true;
        }

        nativeToken.safeTransferFrom(msg.sender, address(this), amount);

        uint256 multiplier = getMultiplier(lockBlocks);
        uint256 sttAmount = (amount * multiplier) / 10000;

        uint256 stakeId = nextStakeId++;
        stakePositions[stakeId] = StakePosition({
            stakeId: stakeId,
            staker: msg.sender,
            validatorKeyHash: kh,
            amount: amount,
            startBlock: block.number,
            lockBlocks: lockBlocks,
            mintedBalance: sttAmount
        });

        _userStakeIds[msg.sender].push(stakeId);
        _validatorStakeIds[kh].push(stakeId);
        validatorTotalMinted[kh] += sttAmount;

        _mint(msg.sender, sttAmount);

        emit Staked(msg.sender, kh, stakeId, amount, lockBlocks, sttAmount);
    }

    function unstakeFrom(uint256 stakeId) external nonReentrant {
        StakePosition storage pos = stakePositions[stakeId];
        require(pos.staker == msg.sender, "not your stake");
        require(pos.amount > 0, "no active stake");
        require(block.number >= pos.startBlock + pos.lockBlocks, "still locked");

        bytes32 kh = pos.validatorKeyHash;
        uint256 amount = pos.amount;
        uint256 sttAmount = pos.mintedBalance;

        validatorTotalMinted[kh] -= sttAmount;
        _burn(msg.sender, sttAmount);

        _removeFromArray(_userStakeIds[msg.sender], stakeId);
        _removeFromArray(_validatorStakeIds[kh], stakeId);

        delete stakePositions[stakeId];

        nativeToken.safeTransfer(msg.sender, amount);
        emit Unstaked(msg.sender, kh, stakeId, amount, sttAmount);
    }

    // ── Slashing ──────────────────────────────────────────────────────────────

    function slashValidator(string calldata key) external onlyOwner nonReentrant {
        bytes32 kh = keccak256(abi.encodePacked(key));
        require(validators[kh].active, "validator not active");
        require(slashBps > 0, "slashing not configured");

        uint256[] memory stakeIds = _validatorStakeIds[kh];
        uint256 totalSlashedAmount = 0;

        for (uint256 i = 0; i < stakeIds.length; i++) {
            StakePosition storage pos = stakePositions[stakeIds[i]];
            if (pos.amount == 0) continue;

            uint256 penalty = (pos.amount * slashBps) / 10000;
            uint256 mintedPenalty = (pos.mintedBalance * slashBps) / 10000;

            pos.amount -= penalty;
            pos.mintedBalance -= mintedPenalty;
            validatorTotalMinted[kh] -= mintedPenalty;
            _burn(pos.staker, mintedPenalty);

            totalSlashedAmount += penalty;
        }

        validatorSlashed[kh] += totalSlashedAmount;
        validatorSlashCount[kh]++;

        if (totalSlashedAmount > 0 && slashTreasury != address(0)) {
            nativeToken.safeTransfer(slashTreasury, totalSlashedAmount);
        }

        if (maxSlashCount > 0 && validatorSlashCount[kh] >= maxSlashCount) {
            validators[kh].active = false;
            emit ValidatorDeactivated(kh);
        }

        emit Slashed(kh, totalSlashedAmount, validatorSlashCount[kh]);
    }

    // ── Multiplier Curve ──────────────────────────────────────────────────────

    function setPoints(Point[] calldata _points) external onlyOwner {
        require(_points.length > 0, "must provide at least one point");

        for (uint256 i = 0; i < _points.length; i++) {
            require(_points[i].multiplier >= 10000, "multiplier must be >= 1x");
            require(_points[i].blocks <= maxLockBlocks, "exceeds max blocks");
            if (i > 0) {
                require(_points[i].blocks > _points[i-1].blocks, "blocks must increase");
                require(_points[i].multiplier >= _points[i-1].multiplier, "multiplier must not decrease");
            }
        }

        delete points;
        for (uint256 i = 0; i < _points.length; i++) {
            points.push(_points[i]);
        }
        emit PointsSet(_points.length);
    }

    function getMultiplier(uint256 blockCount) public view returns (uint256) {
        if (points.length == 0) return 10000;
        if (blockCount <= points[0].blocks) return points[0].multiplier;
        if (blockCount >= points[points.length - 1].blocks) return points[points.length - 1].multiplier;

        for (uint256 i = 0; i < points.length - 1; i++) {
            if (blockCount >= points[i].blocks && blockCount <= points[i + 1].blocks) {
                return _interpolate(
                    points[i].blocks, points[i].multiplier,
                    points[i + 1].blocks, points[i + 1].multiplier,
                    blockCount
                );
            }
        }
        return points[points.length - 1].multiplier;
    }
}
