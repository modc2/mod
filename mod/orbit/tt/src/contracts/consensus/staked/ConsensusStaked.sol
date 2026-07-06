// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../Consensus.sol";

/**
 * @title ConsensusStaked
 * @dev Pure stake-weighted consensus module.
 *
 *      Scoring: Validator score = total STT staked on them (read from
 *      StakeTime). Checkins are required for liveness — a validator must
 *      have checked in during the epoch to be eligible — but the checkin
 *      itself doesn't affect the score. More stake = more emissions.
 *
 *      Selection: Weighted random by STT staked on each live validator.
 *
 *      Distribution: Fresh Subnet tokens minted and split proportional
 *      to STT staked. Per validator, commission goes to validator,
 *      rest to stakers by STT.
 */
contract ConsensusStaked is Consensus {

    mapping(bytes32 => uint64) public lastCheckinEpoch;
    uint64 public currentEpoch;

    constructor(
        address _subnet,
        address _stakeTime,
        uint256 _emissionRate,
        uint64  _epochLength
    ) Consensus(_subnet, _stakeTime, _emissionRate, _epochLength) {
        currentEpoch = 1; // start at 1 so default 0 means "never checked in"
    }

    // ── Consensus Overrides ──────────────────────────────────────────────────

    function _applyCheckin(bytes32 kh) internal override {
        ValidatorScore storage s = scores[kh];
        uint64 bn = uint64(block.number);

        s.lastSeenBlock = bn;
        lastCheckinEpoch[kh] = currentEpoch;

        s.blocktimeScore = staking.getValidatorTotalStakeTimeByHash(kh);

        _recalcTotal();
        emit Checkin(kh, bn, s.blocktimeScore);
    }

    function _selectProposer() internal view override returns (bytes32 proposer) {
        if (consensus.totalBlocktime == 0) return proposer;

        uint256 rand = uint256(keccak256(abi.encodePacked(
            block.prevrandao, consensus.currentBlock, block.timestamp
        )));
        uint256 target = rand % consensus.totalBlocktime;

        uint256 len = staking.validatorCount();
        uint256 cumulative = 0;
        for (uint256 i = 0; i < len; i++) {
            bytes32 kh = staking.getValidatorKeyHash(i);
            if (!staking.isValidatorActive(kh)) continue;
            if (lastCheckinEpoch[kh] != currentEpoch) continue;

            uint256 score = staking.getValidatorTotalStakeTimeByHash(kh);
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
            if (lastCheckinEpoch[kh] != currentEpoch) continue;

            uint256 stt = staking.getValidatorTotalStakeTimeByHash(kh);
            if (stt == 0) continue;

            uint256 validatorShare = (consensus.emissionRate * stt) / consensus.totalBlocktime;
            totalDistributed += _distributeValidatorShare(kh, validatorShare);
        }

        consensus.lastEmissionBlock = consensus.currentBlock;
        currentEpoch++;

        _recalcTotal();

        try staking.advanceEpoch() {} catch {}

        emit EmissionsDistributed(consensus.currentBlock, totalDistributed);
    }

    function _recalcTotal() internal override {
        uint256 total = 0;
        uint256 len = staking.validatorCount();
        for (uint256 i = 0; i < len; i++) {
            bytes32 kh = staking.getValidatorKeyHash(i);
            if (!staking.isValidatorActive(kh)) continue;
            if (lastCheckinEpoch[kh] != currentEpoch) continue;

            total += staking.getValidatorTotalStakeTimeByHash(kh);
        }
        consensus.totalBlocktime = total;
    }
}
