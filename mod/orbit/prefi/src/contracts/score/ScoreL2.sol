// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title ScoreL2
 * @dev Separate contract for calculating prediction scores using L2 distance (Euclidean)
 */
contract ScoreL2 {
    
    struct PlayerScore {
        address player;
        uint256 predictedPrice;
        uint256 lockedAmount;
        uint256 l2Distance;
        uint256 score;
    }
    
    /**
     * @dev Calculate L2 distance (squared Euclidean) between predicted and actual price
     * Returns squared distance to avoid sqrt computation on-chain
     */
    function calculateL2Distance(
        uint256 predictedPrice,
        uint256 actualPrice
    ) public pure returns (uint256) {
        uint256 diff = predictedPrice > actualPrice 
            ? predictedPrice - actualPrice 
            : actualPrice - predictedPrice;
        return diff * diff;
    }
    
    /**
     * @dev Calculate score based on L2 distance and locked amount
     * Score = lockedAmount / (1 + l2Distance)
     */
    function calculateScore(
        uint256 lockedAmount,
        uint256 l2Distance
    ) public pure returns (uint256) {
        return (lockedAmount * 1e18) / (1e18 + l2Distance);
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
     * @dev Batch calculate scores for multiple players using L2 distance
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
            uint256 distance = calculateL2Distance(predictedPrices[i], actualPrice);
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
    
    /**
     * @dev Calculate actual L2 distance with square root approximation
     * Uses Babylonian method for sqrt approximation
     */
    function calculateL2DistanceWithSqrt(
        uint256 predictedPrice,
        uint256 actualPrice
    ) public pure returns (uint256) {
        uint256 squaredDiff = calculateL2Distance(predictedPrice, actualPrice);
        return sqrt(squaredDiff);
    }
    
    /**
     * @dev Square root approximation using Babylonian method
     */
    function sqrt(uint256 x) internal pure returns (uint256) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        uint256 y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
        return y;
    }
}
