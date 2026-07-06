// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../Consensus.sol";

/**
 * @title ConsensusYuma
 * @dev Yuma Consensus module.
 *
 *      Scoring: Blocktime-based with exponential decay. Each checkin adds
 *      elapsed blocks to a validator's score after decaying the old score.
 *      Validators that check in consistently maintain high scores;
 *      inactive validators decay toward zero.
 *
 *      Selection: Weighted random — probability of producing a block is
 *      proportional to blocktimeScore / totalBlocktime.
 *
 *      Distribution: Each epoch, fresh Mod tokens are minted and split
 *      across validators proportional to their blocktime score. Per validator,
 *      commission goes to the validator and the rest to stakers by STT.
 */
contract ConsensusYuma is Consensus {

    uint256 public decayBps; // basis points (500 = 5%)

    constructor(
        address _subnet,
        address _stakeTime,
        uint256 _emissionRate,
        uint256 _decayBps,
        uint64  _epochLength
    ) Consensus(_subnet, _stakeTime, _emissionRate, _epochLength) {
        require(_decayBps < 10000, "decay must be < 100%");
        decayBps = _decayBps;
    }

    // ── Consensus Overrides ──────────────────────────────────────────────────

    function _applyCheckin(bytes32 kh) internal override {
        ValidatorScore storage s = scores[kh];
        uint64 bn = uint64(block.number);
        uint64 delta = s.lastSeenBlock == 0 ? 1 : bn - s.lastSeenBlock;
        if (delta > consensus.epochLength) delta = consensus.epochLength;

        s.blocktimeScore = _decay(s.blocktimeScore) + delta;
        s.lastSeenBlock = bn;

        _recalcTotal();
        emit Checkin(kh, bn, s.blocktimeScore);
    }

    function _selectProposer() internal view override returns (bytes32 proposer) {
        uint256 rand = uint256(keccak256(abi.encodePacked(
            block.prevrandao, consensus.currentBlock, block.timestamp
        )));
        uint256 target = rand % consensus.totalBlocktime;

        uint256 len = staking.validatorCount();
        uint256 cumulative = 0;
        for (uint256 i = 0; i < len; i++) {
            bytes32 kh = staking.getValidatorKeyHash(i);
            if (!staking.isValidatorActive(kh)) continue;
            uint256 score = scores[kh].blocktimeScore;
            if (score == 0) continue;

            cumulative += score;
            if (cumulative > target) {
                proposer = kh;
                break;
            }
        }
    }

    function _distribute() internal override {
        if (consensus.totalBlocktime == 0) return;

        uint256 totalDistributed = 0;
        uint256 len = staking.validatorCount();

        for (uint256 i = 0; i < len; i++) {
            bytes32 kh = staking.getValidatorKeyHash(i);
            if (!staking.isValidatorActive(kh)) continue;

            ValidatorScore storage s = scores[kh];
            if (s.blocktimeScore == 0) continue;

            uint256 validatorShare = (consensus.emissionRate * s.blocktimeScore) / consensus.totalBlocktime;
            totalDistributed += _distributeValidatorShare(kh, validatorShare);

            s.blocktimeScore = _decay(s.blocktimeScore);
        }

        consensus.lastEmissionBlock = consensus.currentBlock;
        _recalcTotal();

        try staking.advanceEpoch() {} catch {}

        emit EmissionsDistributed(consensus.currentBlock, totalDistributed);
    }

    function _recalcTotal() internal override {
        uint256 total = 0;
        uint256 len = staking.validatorCount();
        for (uint256 i = 0; i < len; i++) {
            bytes32 kh = staking.getValidatorKeyHash(i);
            if (staking.isValidatorActive(kh)) {
                total += scores[kh].blocktimeScore;
            }
        }
        consensus.totalBlocktime = total;
    }

    // ── Decay ────────────────────────────────────────────────────────────────

    function _decay(uint256 score) internal view returns (uint256) {
        return (score * (10000 - decayBps)) / 10000;
    }

    // ── Admin ────────────────────────────────────────────────────────────────

    function setDecayBps(uint256 bps) external onlyOwner {
        require(bps < 10000, "decay must be < 100%");
        decayBps = bps;
    }
}
