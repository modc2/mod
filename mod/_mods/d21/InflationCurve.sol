// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract InflationCurve {
    uint256 public genesisBlock;
    uint256 public constant HALVING_INTERVAL = 210000;
    uint256 public constant INITIAL_REWARD = 50 * 10**18;
    uint256 public constant BLOCK_TIME = 600;
    
    event TokensDistributed(address indexed to, uint256 amount, uint256 blockNumber);
    
    constructor() {
        genesisBlock = block.number;
    }
    
    function calculateReward(uint256 blockNumber) public view returns (uint256) {
        uint256 blocksSinceGenesis = blockNumber - genesisBlock;
        uint256 halvings = blocksSinceGenesis / HALVING_INTERVAL;
        
        if (halvings >= 64) return 0;
        
        uint256 reward = INITIAL_REWARD >> halvings;
        return reward;
    }
    
    function getCurrentReward() public view returns (uint256) {
        return calculateReward(block.number);
    }
    
    function blocksUntilHalving() public view returns (uint256) {
        uint256 blocksSinceGenesis = block.number - genesisBlock;
        uint256 currentCycle = blocksSinceGenesis % HALVING_INTERVAL;
        return HALVING_INTERVAL - currentCycle;
    }
}