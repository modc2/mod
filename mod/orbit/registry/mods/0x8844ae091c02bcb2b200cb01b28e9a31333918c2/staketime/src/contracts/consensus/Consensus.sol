// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "../Mod.sol";
import "../staking/Staking.sol";
import "../inflation/IInflationCurve.sol";

/**
 * @title Consensus
 * @dev Abstract base for consensus modules that rotate a Mod token.
 *
 *      Each epoch the consensus module mints new Mod tokens as emissions
 *      and distributes them to validators (commission) and stakers
 *      (proportional to STT). Concrete implementations define scoring,
 *      proposer selection, and distribution behavior.
 *
 *      Infrastructure provided by base:
 *        - Validator checkin routing (ECDSA self-call or owner relay)
 *        - Block production loop with epoch triggers
 *        - Reward claims (staker + validator)
 *        - Shared commission/staker distribution helper
 *        - Admin setters, views, emergency withdraw
 *
 *      Virtual functions for consensus modules:
 *        - _applyCheckin()   — how a checkin updates scoring
 *        - _selectProposer() — how the block proposer is chosen
 *        - _distribute()     — how emissions are split
 *        - _recalcTotal()    — how total score is recalculated
 */
abstract contract Consensus is ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    // ── Types ────────────────────────────────────────────────────────────────

    struct ValidatorScore {
        uint64 lastSeenBlock;
        uint256 blocktimeScore;
        uint256 earned;
    }

    struct ConsensusState {
        uint64 currentBlock;
        uint64 lastEmissionBlock;
        uint256 totalBlocktime;
        uint256 emissionRate;   // tokens minted per epoch
        uint64 epochLength;     // blocks per epoch
    }

    // ── Events ───────────────────────────────────────────────────────────────

    event Checkin(bytes32 indexed keyHash, uint64 blockNumber, uint256 newScore);
    event BlockProduced(uint64 blockNumber, bytes32 indexed proposer);
    event EmissionsDistributed(uint64 epoch, uint256 totalDistributed);
    event StakerRewardDistributed(bytes32 indexed validatorKeyHash, address indexed staker, uint256 amount);
    event ValidatorRewardClaimed(bytes32 indexed keyHash, address indexed to, uint256 amount);

    // ── State ────────────────────────────────────────────────────────────────

    Mod public mod_token;
    Staking public staking;
    ConsensusState public consensus;
    IInflationCurve public inflationCurve; // optional — address(0) means flat emissionRate

    mapping(bytes32 => ValidatorScore) public scores;
    mapping(bytes32 => uint256) public validatorBalances;
    mapping(address => uint256) public stakerRewards;

    // ── Constructor ──────────────────────────────────────────────────────────

    constructor(
        address _subnet,
        address _staking,
        uint256 _emissionRate,
        uint64  _epochLength
    ) {
        mod_token = Mod(_subnet);
        staking = Staking(_staking);

        consensus = ConsensusState({
            currentBlock: 0,
            lastEmissionBlock: 0,
            totalBlocktime: 0,
            emissionRate: _emissionRate,
            epochLength: _epochLength
        });
    }

    // ── Virtual Consensus Functions ──────────────────────────────────────────

    function _applyCheckin(bytes32 kh) internal virtual;
    function _selectProposer() internal virtual returns (bytes32);
    function _distribute() internal virtual;
    function _recalcTotal() internal virtual;

    // ── Checkin ──────────────────────────────────────────────────────────────

    function checkin(string calldata key) external {
        bytes32 kh = keccak256(abi.encodePacked(key));
        require(staking.isValidatorActive(kh), "not registered");

        (,uint8 keyType,,,) = staking.getValidatorByHash(kh);

        if (Staking.KeyType(keyType) == Staking.KeyType.Ecdsa) {
            require(
                keccak256(abi.encodePacked(_addressToString(msg.sender))) ==
                keccak256(abi.encodePacked(key)),
                "sender mismatch"
            );
        } else {
            require(msg.sender == owner(), "only owner relays non-ECDSA checkins");
        }

        _applyCheckin(kh);
    }

    function batchCheckin(string[] calldata keys) external onlyOwner {
        for (uint256 i = 0; i < keys.length; i++) {
            bytes32 kh = keccak256(abi.encodePacked(keys[i]));
            if (staking.isValidatorActive(kh)) {
                _applyCheckin(kh);
            }
        }
    }

    // ── Block Production ─────────────────────────────────────────────────────

    function produceBlock() external returns (bytes32 proposer) {
        require(consensus.totalBlocktime > 0, "no active validators");

        consensus.currentBlock++;
        proposer = _selectProposer();

        if (consensus.currentBlock - consensus.lastEmissionBlock >= consensus.epochLength) {
            _distribute();
        }

        emit BlockProduced(consensus.currentBlock, proposer);
    }

    function distributeEmissions() external {
        require(
            consensus.currentBlock - consensus.lastEmissionBlock >= consensus.epochLength,
            "epoch not reached"
        );
        _distribute();
    }

    // ── Reward Claims ────────────────────────────────────────────────────────

    function claimStakerRewards() external nonReentrant {
        uint256 amount = stakerRewards[msg.sender];
        require(amount > 0, "nothing to claim");
        stakerRewards[msg.sender] = 0;
        IERC20(address(mod_token)).safeTransfer(msg.sender, amount);
    }

    function claimValidatorRewards(string calldata key, address to) external {
        bytes32 kh = keccak256(abi.encodePacked(key));
        require(staking.isValidatorActive(kh), "not registered");

        (,uint8 keyType,,,) = staking.getValidatorByHash(kh);

        if (Staking.KeyType(keyType) == Staking.KeyType.Ecdsa) {
            require(
                keccak256(abi.encodePacked(_addressToString(msg.sender))) ==
                keccak256(abi.encodePacked(key)),
                "sender mismatch"
            );
        } else {
            require(msg.sender == owner(), "only owner for non-ECDSA");
        }

        uint256 amount = validatorBalances[kh];
        require(amount > 0, "nothing to claim");
        validatorBalances[kh] = 0;

        IERC20(address(mod_token)).safeTransfer(to, amount);
        emit ValidatorRewardClaimed(kh, to, amount);
    }

    // ── Shared Distribution Helper ───────────────────────────────────────────

    /**
     * @dev Mint and distribute a validator's emission share between commission
     *      and stakers. Mints fresh Mod tokens for emissions.
     */
    function _distributeValidatorShare(bytes32 kh, uint256 validatorShare) internal returns (uint256) {
        mod_token.mint(address(this), validatorShare);

        ValidatorScore storage s = scores[kh];
        uint256 totalSTT = staking.getValidatorTotalMintedByHash(kh);
        uint256 commissionBps = staking.getValidatorCommission(kh);

        if (totalSTT == 0) {
            validatorBalances[kh] += validatorShare;
            s.earned += validatorShare;
        } else {
            uint256 commission = (validatorShare * commissionBps) / 10000;
            uint256 stakerPool = validatorShare - commission;

            validatorBalances[kh] += commission;
            s.earned += commission;

            uint256[] memory stakeIds = staking.getValidatorStakeIdsByHash(kh);
            uint256 distributed = 0;

            for (uint256 j = 0; j < stakeIds.length; j++) {
                (address staker,, uint256 amount,,, uint256 sttBalance,) =
                    staking.getStakePosition(stakeIds[j]);
                if (amount == 0) continue;

                uint256 reward = (stakerPool * sttBalance) / totalSTT;
                stakerRewards[staker] += reward;
                distributed += reward;

                emit StakerRewardDistributed(kh, staker, reward);
            }

            if (stakerPool > distributed) {
                validatorBalances[kh] += stakerPool - distributed;
                s.earned += stakerPool - distributed;
            }
        }

        return validatorShare;
    }

    // ── Views ────────────────────────────────────────────────────────────────

    function getBlock() external view returns (
        uint64, uint64, uint256, uint256, uint64
    ) {
        ConsensusState storage c = consensus;
        return (c.currentBlock, c.lastEmissionBlock, c.totalBlocktime,
                c.emissionRate, c.epochLength);
    }

    function getValidatorScore(bytes32 kh) external view returns (
        uint64 lastSeenBlock, uint256 blocktimeScore, uint256 earned
    ) {
        ValidatorScore storage s = scores[kh];
        return (s.lastSeenBlock, s.blocktimeScore, s.earned);
    }

    function getValidatorBalance(string calldata key) external view returns (uint256) {
        return validatorBalances[keccak256(abi.encodePacked(key))];
    }

    function getStakerRewards(address staker) external view returns (uint256) {
        return stakerRewards[staker];
    }

    function getLeaderboard(uint256 limit) external view returns (
        bytes32[] memory keys, uint256[] memory topScores
    ) {
        uint256 len = staking.validatorCount();
        if (limit > len) limit = len;

        bytes32[] memory allKeys = new bytes32[](len);
        uint256[] memory allScores = new uint256[](len);
        for (uint256 i = 0; i < len; i++) {
            allKeys[i] = staking.getValidatorKeyHash(i);
            allScores[i] = scores[allKeys[i]].blocktimeScore;
        }

        for (uint256 i = 0; i < limit; i++) {
            uint256 maxIdx = i;
            for (uint256 j = i + 1; j < len; j++) {
                if (allScores[j] > allScores[maxIdx]) maxIdx = j;
            }
            if (maxIdx != i) {
                (allKeys[i], allKeys[maxIdx]) = (allKeys[maxIdx], allKeys[i]);
                (allScores[i], allScores[maxIdx]) = (allScores[maxIdx], allScores[i]);
            }
        }

        keys = new bytes32[](limit);
        topScores = new uint256[](limit);
        for (uint256 i = 0; i < limit; i++) {
            keys[i] = allKeys[i];
            topScores[i] = allScores[i];
        }
    }

    // ── Admin ────────────────────────────────────────────────────────────────

    function setEmissionRate(uint256 rate) external onlyOwner {
        consensus.emissionRate = rate;
    }

    function setInflationCurve(address _curve) external onlyOwner {
        inflationCurve = IInflationCurve(_curve);
    }

    /// @dev Returns effective emission for the current epoch. Uses inflation
    ///      curve if set, otherwise falls back to flat emissionRate.
    function getEffectiveEmission() public view returns (uint256) {
        if (address(inflationCurve) != address(0)) {
            uint64 epoch = consensus.epochLength > 0
                ? consensus.currentBlock / consensus.epochLength
                : 0;
            return inflationCurve.getEmission(epoch);
        }
        return consensus.emissionRate;
    }

    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(owner(), amount);
    }

    // ── Internal Utils ───────────────────────────────────────────────────────

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
}
