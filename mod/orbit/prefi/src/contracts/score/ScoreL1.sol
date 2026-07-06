// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title ScoreL1
 * @dev Separate contract for calculating prediction scores using L1 distance
 */
contract ScoreL1 {
    
    struct PlayerScore {
        address player;
        uint256 predictedPrice;
        uint256 lockedAmount;
        uint256 l1Distance;
        uint256 score;
    }
    
    /**
     * @dev Calculate L1 distance between predicted and actual price
     */
    function calculateL1Distance(
        uint256 predictedPrice,
        uint256 actualPrice
    ) public pure returns (uint256) {
        return predictedPrice > actualPrice 
            ? predictedPrice - actualPrice 
            : actualPrice - predictedPrice;
    }
    
    /**
     * @dev Calculate score based on L1 distance and locked amount
     * Score = lockedAmount / (1 + l1Distance)
     */
    function calculateScore(
        uint256 lockedAmount,
        uint256 l1Distance
    ) public pure returns (uint256) {
        return (lockedAmount * 1e18) / (1e18 + l1Distance);
    }
    
    /**
     * @dev Calculate reward based on score proportion
     */
    function calculateReward(
        uint256 playerScore,
        uint256 totalScore,
        uint256 totalPool
    ) public pure returns (uint256) {
        if (totalScore == 0) return 0;
        return (playerScore * totalPool) / totalScore;
    }
    
    /**
     * @dev Batch calculate scores for multiple players
     */
    function calculateBatchScores(
        uint256[] memory predictedPrices,
        uint256[] memory lockedAmounts,
        uint256 actualPrice
    ) public pure returns (uint256[] memory scores, uint256 totalScore) {
        require(predictedPrices.length == lockedAmounts.length, "Array length mismatch");
        
        scores = new uint256[](predictedPrices.length);
        totalScore = 0;
        
        for (uint256 i = 0; i < predictedPrices.length; i++) {
            uint256 distance = calculateL1Distance(predictedPrices[i], actualPrice);
            uint256 score = calculateScore(lockedAmounts[i], distance);
            scores[i] = score;
            totalScore += score;
        }
        
        return (scores, totalScore);
    }
    
    /**
     * @dev Calculate all rewards for a batch of players
     */
    function calculateBatchRewards(
        uint256[] memory scores,
        uint256 totalScore,
        uint256 totalPool
    ) public pure returns (uint256[] memory rewards) {
        rewards = new uint256[](scores.length);
        
        for (uint256 i = 0; i < scores.length; i++) {
            rewards[i] = calculateReward(scores[i], totalScore, totalPool);
        }
        
        return rewards;
    }
}
