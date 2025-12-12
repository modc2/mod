// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract BitcoinInflationToken {
    string public name = "Bitcoin Inflation Token";
    string public symbol = "BIT";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    
    address public owner;
    uint256 public genesisBlock;
    uint256 public constant HALVING_INTERVAL = 210000; // Bitcoin halving blocks
    uint256 public constant INITIAL_REWARD = 50 * 10**18; // 50 tokens
    uint256 public constant BLOCK_TIME = 600; // 10 minutes in seconds
    
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event OwnerChanged(address indexed oldOwner, address indexed newOwner);
    event TokensDistributed(address indexed to, uint256 amount, uint256 blockNumber);
    
    modifier onlyOwner() {
        require(msg.sender == owner, "Only owner can call this");
        _;
    }
    
    constructor() {
        owner = msg.sender;
        genesisBlock = block.number;
    }
    
    // Calculate reward based on Bitcoin's inflation curve
    function calculateReward(uint256 blockNumber) public view returns (uint256) {
        uint256 blocksSinceGenesis = blockNumber - genesisBlock;
        uint256 halvings = blocksSinceGenesis / HALVING_INTERVAL;
        
        if (halvings >= 64) return 0; // No more rewards after 64 halvings
        
        uint256 reward = INITIAL_REWARD >> halvings; // Bitshift right = divide by 2^halvings
        return reward;
    }
    
    // Distribute tokens following the inflation curve
    function distribute(address to) public onlyOwner returns (uint256) {
        uint256 reward = calculateReward(block.number);
        require(reward > 0, "No more tokens to distribute");
        
        balanceOf[to] += reward;
        totalSupply += reward;
        
        emit Transfer(address(0), to, reward);
        emit TokensDistributed(to, reward, block.number);
        
        return reward;
    }
    
    // Revolution: Stakers with 50%+ can replace owner
    function revolution(address newOwner) public {
        require(balanceOf[msg.sender] > totalSupply / 2, "Need >50% of supply to revolt");
        require(newOwner != address(0), "Invalid new owner");
        
        address oldOwner = owner;
        owner = newOwner;
        
        emit OwnerChanged(oldOwner, newOwner);
    }
    
    // Owner can voluntarily transfer ownership
    function transferOwnership(address newOwner) public onlyOwner {
        require(newOwner != address(0), "Invalid new owner");
        
        address oldOwner = owner;
        owner = newOwner;
        
        emit OwnerChanged(oldOwner, newOwner);
    }
    
    // Standard ERC20 functions
    function transfer(address to, uint256 value) public returns (bool) {
        require(balanceOf[msg.sender] >= value, "Insufficient balance");
        
        balanceOf[msg.sender] -= value;
        balanceOf[to] += value;
        
        emit Transfer(msg.sender, to, value);
        return true;
    }
    
    function approve(address spender, uint256 value) public returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }
    
    function transferFrom(address from, address to, uint256 value) public returns (bool) {
        require(balanceOf[from] >= value, "Insufficient balance");
        require(allowance[from][msg.sender] >= value, "Insufficient allowance");
        
        balanceOf[from] -= value;
        balanceOf[to] += value;
        allowance[from][msg.sender] -= value;
        
        emit Transfer(from, to, value);
        return true;
    }
    
    // Get current block reward
    function getCurrentReward() public view returns (uint256) {
        return calculateReward(block.number);
    }
    
    // Get estimated blocks until next halving
    function blocksUntilHalving() public view returns (uint256) {
        uint256 blocksSinceGenesis = block.number - genesisBlock;
        uint256 currentCycle = blocksSinceGenesis % HALVING_INTERVAL;
        return HALVING_INTERVAL - currentCycle;
    }
}