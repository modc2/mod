// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../Consensus.sol";

/**
 * @title ConsensusLinear
 * @dev Simple linear consensus module — no decay, no complexity.
 *
 *      Scoring: Each checkin adds +1 to a validator's score.
 *      Scores reset to zero after each epoch distribution.
 *
 *      Selection: Weighted random by checkin count — validators that
 *      check in more often produce more blocks.
 *
 *      Distribution: Fresh Subnet tokens minted and split proportional
 *      to checkin count. Per validator, commission goes to validator,
 *      rest to stakers by STT.
 */
contract ConsensusLinear is Consensus {

    constructor(
        address _subnet,
        address _stakeTime,
        uint256 _emissionRate,
        uint64  _epochLength
    ) Consensus(_subnet, _stakeTime, _emissionRate, _epochLength) {}

    // ── Consensus Overrides ──────────────────────────────────────────────────

    function _applyCheckin(bytes32 kh) internal override {
        ValidatorScore storage s = scores[kh];
        uint64 bn = uint64(block.number);

        s.blocktimeScore += 1;
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

            s.blocktimeScore = 0;
        }

        consensus.lastEmissionBlock = consensus.currentBlock;
        consensus.totalBlocktime = 0;

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
}
