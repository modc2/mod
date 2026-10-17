// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @title DevnetPriceFeed
/// @notice Chainlink AggregatorV3-compatible ETH/USD feed for the zkapi devnet.
/// zkapi-serverd prices leases from `latestRoundData()` / `getRoundData()` read at
/// the `finalized` block tag, exactly as it reads the Chainlink feed on mainnet
/// (0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419). The module's keeper pushes the
/// live mainnet answer (or a fixed fallback) as new rounds. Like a Chainlink
/// report, `updatedAt` is the keeper's observation time rather than the block
/// time, so fast-forwarding the devnet clock (escape finalization) does not put
/// the price "in the future" for the server's wall-clock freshness check.
contract DevnetPriceFeed {
    struct Round {
        int256 answer;
        uint256 startedAt;
        uint256 updatedAt;
    }

    uint8 public immutable decimals;
    string public description;
    uint256 public constant version = 4;
    address public immutable keeper;
    uint80 public latestRound;
    mapping(uint80 => Round) private rounds;

    event AnswerUpdated(int256 indexed current, uint256 indexed roundId, uint256 updatedAt);

    error NotKeeper();
    error NoData();
    error BadAnswer();

    constructor(uint8 decimals_, string memory description_, int256 initialAnswer, uint256 observedAt) {
        decimals = decimals_;
        description = description_;
        keeper = msg.sender;
        _push(initialAnswer, observedAt);
    }

    /// @param observedAt unix seconds of the observation; 0 = block.timestamp.
    function pushAnswer(int256 answer, uint256 observedAt) external returns (uint80 roundId) {
        if (msg.sender != keeper) revert NotKeeper();
        return _push(answer, observedAt);
    }

    function _push(int256 answer, uint256 observedAt) private returns (uint80 roundId) {
        if (answer <= 0) revert BadAnswer();
        uint256 at = observedAt == 0 ? block.timestamp : observedAt;
        roundId = latestRound + 1;
        latestRound = roundId;
        rounds[roundId] = Round(answer, at, at);
        emit AnswerUpdated(answer, roundId, at);
    }

    function getRoundData(uint80 roundId)
        public
        view
        returns (uint80, int256, uint256, uint256, uint80)
    {
        Round memory r = rounds[roundId];
        if (r.updatedAt == 0) revert NoData();
        return (roundId, r.answer, r.startedAt, r.updatedAt, roundId);
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return getRoundData(latestRound);
    }

    function latestAnswer() external view returns (int256) {
        return rounds[latestRound].answer;
    }
}
