// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title Staking
 * @dev Validator registration and delegated staking. Users stake nativeToken
 *      ON validators, receiving STT (StakeTime) ERC20 proportional to
 *      amount x M(lockBlocks).
 *
 *      Consensus modules (ConsensusYuma, etc.) read validator/stake data
 *      from this contract and handle scoring, block production, and emissions
 *      separately.
 *
 *      Slashing: Validators can be slashed for misbehavior. All stakers on
 *      a slashed validator lose a percentage of their stake. Auto-deactivates
 *      after maxSlashCount slashes.
 */
contract Staking is ERC20, ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    // ── Types ────────────────────────────────────────────────────────────────

    enum KeyType { Ecdsa, Ed25519, Sr25519 }

    struct Validator {
        string key;
        KeyType keyType;
        uint64 registeredBlock;
        uint256 commissionBps;  // basis points (1000 = 10%)
        bool active;
    }

    struct StakePosition {
        uint256 stakeId;
        address staker;
        bytes32 validatorKeyHash;
        uint256 amount;         // nativeToken locked
        uint256 startBlock;
        uint256 lockBlocks;
        uint256 stakeTimeBalance; // STT minted
    }

    struct Point {
        uint256 blocks;
        uint256 multiplier;     // basis points (10000 = 1x)
    }

    // ── Events ───────────────────────────────────────────────────────────────

    event ValidatorRegistered(bytes32 indexed keyHash, string key, uint8 keyType, uint256 commissionBps);
    event CommissionUpdated(bytes32 indexed keyHash, uint256 newCommissionBps);
    event ValidatorDeactivated(bytes32 indexed keyHash);

    event Staked(address indexed staker, bytes32 indexed validatorKeyHash, uint256 stakeId, uint256 amount, uint256 lockBlocks, uint256 stakeTimeEarned);
    event Unstaked(address indexed staker, bytes32 indexed validatorKeyHash, uint256 stakeId, uint256 amount, uint256 stakeTimeBurned);

    event Slashed(bytes32 indexed keyHash, uint256 totalSlashed, uint256 slashCount);
    event PointsSet(uint256 pointCount);
    event MaxStakersUpdated(uint256 newMax);

    // ── State ────────────────────────────────────────────────────────────────

    IERC20 public nativeToken;

    // Validators
    mapping(bytes32 => Validator) public validators;
    bytes32[] public validatorKeys;

    // Staking
    uint256 public nextStakeId;
    uint256 public maxStakersPerValidator;
    uint256 public maxLockBlocks;
    uint256 public defaultCommissionBps;

    mapping(uint256 => StakePosition) public stakePositions;
    mapping(address => uint256[]) internal _userStakeIds;
    mapping(bytes32 => uint256[]) internal _validatorStakeIds;
    mapping(bytes32 => uint256) public validatorTotalStakeTime;

    // Max stakers per validator per epoch
    uint256 public currentEpoch;
    mapping(bytes32 => mapping(uint256 => uint256)) public validatorStakerCountPerEpoch;
    mapping(bytes32 => mapping(uint256 => mapping(address => bool))) public hasStakedInEpoch;

    // Slashing
    uint256 public slashBps;
    uint256 public maxSlashCount;
    address public slashTreasury;
    mapping(bytes32 => uint256) public validatorSlashed;
    mapping(bytes32 => uint256) public validatorSlashCount;

    // Multiplier curve
    Point[] public points;

    // ── Constructor ──────────────────────────────────────────────────────────

    constructor(
        address _nativeToken,
        uint256 _maxLockBlocks,
        uint256 _maxStakersPerValidator,
        uint256 _defaultCommissionBps
    ) ERC20("StakeTime", "STT") {
        require(_defaultCommissionBps <= 5000, "commission max 50%");

        nativeToken = IERC20(_nativeToken);
        maxLockBlocks = _maxLockBlocks;
        maxStakersPerValidator = _maxStakersPerValidator;
        defaultCommissionBps = _defaultCommissionBps;

        points.push(Point({ blocks: 0, multiplier: 10000 }));
    }

    // ── Validator Registration ───────────────────────────────────────────────

    function registerValidator(string calldata key, KeyType keyType) external {
        bytes32 kh = keccak256(abi.encodePacked(key));
        require(!validators[kh].active, "already registered");

        validators[kh] = Validator({
            key: key,
            keyType: keyType,
            registeredBlock: uint64(block.number),
            commissionBps: defaultCommissionBps,
            active: true
        });
        validatorKeys.push(kh);

        emit ValidatorRegistered(kh, key, uint8(keyType), defaultCommissionBps);
    }

    function registerValidatorAdmin(
        string calldata key,
        KeyType keyType,
        uint256 commissionBps
    ) external onlyOwner {
        require(commissionBps <= 5000, "commission max 50%");
        bytes32 kh = keccak256(abi.encodePacked(key));
        require(!validators[kh].active, "already registered");

        validators[kh] = Validator({
            key: key,
            keyType: keyType,
            registeredBlock: uint64(block.number),
            commissionBps: commissionBps,
            active: true
        });
        validatorKeys.push(kh);

        emit ValidatorRegistered(kh, key, uint8(keyType), commissionBps);
    }

    function setValidatorCommission(string calldata key, uint256 newCommissionBps) external {
        require(newCommissionBps <= 5000, "commission max 50%");
        bytes32 kh = keccak256(abi.encodePacked(key));
        Validator storage v = validators[kh];
        require(v.active, "not registered");

        if (v.keyType == KeyType.Ecdsa) {
            require(
                keccak256(abi.encodePacked(_addressToString(msg.sender))) ==
                keccak256(abi.encodePacked(_toLower(v.key))),
                "sender mismatch"
            );
        } else {
            require(msg.sender == owner(), "only owner for non-ECDSA");
        }

        v.commissionBps = newCommissionBps;
        emit CommissionUpdated(kh, newCommissionBps);
    }

    function deactivateValidator(string calldata key) external onlyOwner {
        bytes32 kh = keccak256(abi.encodePacked(key));
        validators[kh].active = false;
        emit ValidatorDeactivated(kh);
    }

    // ── Epoch (advanced by consensus module or owner) ────────────────────────

    function advanceEpoch() external onlyOwner {
        currentEpoch++;
    }

    // ── Slashing ─────────────────────────────────────────────────────────────

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
            uint256 sttPenalty = (pos.stakeTimeBalance * slashBps) / 10000;

            pos.amount -= penalty;
            pos.stakeTimeBalance -= sttPenalty;
            validatorTotalStakeTime[kh] -= sttPenalty;
            _burn(pos.staker, sttPenalty);

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

    // ── Staking ──────────────────────────────────────────────────────────────

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
            stakeTimeBalance: sttAmount
        });

        _userStakeIds[msg.sender].push(stakeId);
        _validatorStakeIds[kh].push(stakeId);
        validatorTotalStakeTime[kh] += sttAmount;

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
        uint256 sttAmount = pos.stakeTimeBalance;

        validatorTotalStakeTime[kh] -= sttAmount;
        _burn(msg.sender, sttAmount);

        _removeFromArray(_userStakeIds[msg.sender], stakeId);
        _removeFromArray(_validatorStakeIds[kh], stakeId);

        delete stakePositions[stakeId];

        nativeToken.safeTransfer(msg.sender, amount);
        emit Unstaked(msg.sender, kh, stakeId, amount, sttAmount);
    }

    // ── Multiplier Curve ─────────────────────────────────────────────────────

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

    // ── Admin ────────────────────────────────────────────────────────────────

    function setMaxStakersPerValidator(uint256 _max) external onlyOwner {
        maxStakersPerValidator = _max;
        emit MaxStakersUpdated(_max);
    }

    function setMaxLockBlocks(uint256 _max) external onlyOwner {
        maxLockBlocks = _max;
    }

    function setDefaultCommissionBps(uint256 bps) external onlyOwner {
        require(bps <= 5000, "max 50%");
        defaultCommissionBps = bps;
    }

    function setSlashBps(uint256 bps) external onlyOwner {
        require(bps <= 5000, "max 50%");
        slashBps = bps;
    }

    function setSlashTreasury(address treasury) external onlyOwner {
        slashTreasury = treasury;
    }

    function setMaxSlashCount(uint256 count) external onlyOwner {
        maxSlashCount = count;
    }

    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(owner(), amount);
    }

    // ── Views ────────────────────────────────────────────────────────────────

    function getValidator(string calldata key) external view returns (
        string memory _key, uint8 _keyType, uint64 _registeredBlock,
        uint256 _commissionBps, bool _active
    ) {
        bytes32 kh = keccak256(abi.encodePacked(key));
        Validator storage v = validators[kh];
        return (v.key, uint8(v.keyType), v.registeredBlock, v.commissionBps, v.active);
    }

    function getValidatorByHash(bytes32 kh) external view returns (
        string memory _key, uint8 _keyType, uint64 _registeredBlock,
        uint256 _commissionBps, bool _active
    ) {
        Validator storage v = validators[kh];
        return (v.key, uint8(v.keyType), v.registeredBlock, v.commissionBps, v.active);
    }

    function validatorCount() external view returns (uint256) {
        return validatorKeys.length;
    }

    function getValidatorKeyHash(uint256 index) external view returns (bytes32) {
        return validatorKeys[index];
    }

    function isValidatorActive(bytes32 kh) external view returns (bool) {
        return validators[kh].active;
    }

    function getValidatorCommission(bytes32 kh) external view returns (uint256) {
        return validators[kh].commissionBps;
    }

    function getUserStakeIds(address user) external view returns (uint256[] memory) {
        return _userStakeIds[user];
    }

    function getValidatorStakeIds(string calldata key) external view returns (uint256[] memory) {
        return _validatorStakeIds[keccak256(abi.encodePacked(key))];
    }

    function getValidatorStakeIdsByHash(bytes32 kh) external view returns (uint256[] memory) {
        return _validatorStakeIds[kh];
    }

    function getStakePosition(uint256 stakeId) external view returns (
        address staker, bytes32 validatorKeyHash, uint256 amount,
        uint256 startBlock, uint256 lockBlocks, uint256 stakeTimeBalance,
        uint256 blocksRemaining
    ) {
        StakePosition storage pos = stakePositions[stakeId];
        uint256 elapsed = block.number > pos.startBlock ? block.number - pos.startBlock : 0;
        uint256 remaining = pos.lockBlocks > elapsed ? pos.lockBlocks - elapsed : 0;
        return (pos.staker, pos.validatorKeyHash, pos.amount, pos.startBlock,
                pos.lockBlocks, pos.stakeTimeBalance, remaining);
    }

    function getValidatorTotalStakeTime(string calldata key) external view returns (uint256) {
        return validatorTotalStakeTime[keccak256(abi.encodePacked(key))];
    }

    function getValidatorTotalStakeTimeByHash(bytes32 kh) external view returns (uint256) {
        return validatorTotalStakeTime[kh];
    }

    // ── Internal Utils ───────────────────────────────────────────────────────

    function _interpolate(uint256 x0, uint256 y0, uint256 x1, uint256 y1, uint256 x) internal pure returns (uint256) {
        if (x0 == x1) return y0;
        return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
    }

    function _removeFromArray(uint256[] storage arr, uint256 val) internal {
        for (uint256 i = 0; i < arr.length; i++) {
            if (arr[i] == val) {
                arr[i] = arr[arr.length - 1];
                arr.pop();
                return;
            }
        }
    }

    function _addressToString(address addr) internal pure returns (string memory) {
        bytes memory alphabet = "0123456789abcdef";
        bytes20 data = bytes20(addr);
        bytes memory str = new bytes(42);
        str[0] = "0";
        str[1] = "x";
        for (uint256 i = 0; i < 20; i++) {
            str[2 + i * 2] = alphabet[uint8(data[i] >> 4)];
            str[3 + i * 2] = alphabet[uint8(data[i] & 0x0f)];
        }
        return string(str);
    }

    function _toLower(string memory s) internal pure returns (string memory) {
        bytes memory b = bytes(s);
        for (uint256 i = 0; i < b.length; i++) {
            if (b[i] >= 0x41 && b[i] <= 0x5A) {
                b[i] = bytes1(uint8(b[i]) + 32);
            }
        }
        return string(b);
    }
}
