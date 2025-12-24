// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract Newma is ERC20, Ownable {
    uint256 public constant MAX_SUPPLY = 21_000_000 * 10**18; // 21 million tokens (Bitcoin style)
    uint256 public constant HALVING_INTERVAL = 210_000; // Blocks between halvings
    uint256 public constant INITIAL_REWARD = 50 * 10**18; // Initial block reward
    
    uint256 public totalMinted;
    uint256 public currentEpoch;
    uint256 public lastMintBlock;
    
    // Inflation curve parameters (Bitcoin-style default)
    uint256 public halvingInterval;
    uint256 public initialReward;
    uint256 public minReward;
    
    // DAO mode
    bool public daoMode;
    address public multisig;
    
    // Custom inflation curve function selector
    bool public useCustomCurve;
    uint256[] public customEpochRewards;
    
    event InflationCurveUpdated(uint256 halvingInterval, uint256 initialReward);
    event DAOModeEnabled(bool enabled);
    event MultisigUpdated(address newMultisig);
    event TokensMinted(address indexed to, uint256 amount, uint256 epoch);
    event CustomCurveSet(uint256[] rewards);
    
    modifier onlyGovernance() {
        if (daoMode) {
            require(msg.sender == owner(), "Only DAO can call");
        } else {
            require(msg.sender == multisig || msg.sender == owner(), "Only multisig/owner can call");
        }
        _;
    }
    
    constructor(address _multisig) ERC20("Newma", "NEWMA") Ownable(msg.sender) {
        multisig = _multisig;
        
        // Bitcoin-style defaults
        halvingInterval = HALVING_INTERVAL;
        initialReward = INITIAL_REWARD;
        minReward = 1 * 10**18; // Minimum 1 token reward
        
        lastMintBlock = block.number;
        daoMode = false;
        useCustomCurve = false;
    }
    
    /// @notice Calculate current block reward based on inflation curve
    function getCurrentReward() public view returns (uint256) {
        if (totalMinted >= MAX_SUPPLY) return 0;
        
        if (useCustomCurve && customEpochRewards.length > currentEpoch) {
            return customEpochRewards[currentEpoch];
        }
        
        // Bitcoin-style halving curve
        uint256 reward = initialReward;
        uint256 halvings = currentEpoch;
        
        for (uint256 i = 0; i < halvings && reward > minReward; i++) {
            reward = reward / 2;
        }
        
        return reward < minReward ? minReward : reward;
    }
    
    /// @notice Mint tokens based on inflation curve
    function mint(address to) external onlyGovernance returns (uint256) {
        require(totalMinted < MAX_SUPPLY, "Max supply reached");
        
        uint256 blocksPassed = block.number - lastMintBlock;
        require(blocksPassed > 0, "Must wait for next block");
        
        uint256 reward = getCurrentReward();
        uint256 mintAmount = reward * blocksPassed;
        
        // Cap at max supply
        if (totalMinted + mintAmount > MAX_SUPPLY) {
            mintAmount = MAX_SUPPLY - totalMinted;
        }
        
        // Check for epoch advancement
        uint256 newEpoch = totalMinted / (halvingInterval * initialReward);
        if (newEpoch > currentEpoch) {
            currentEpoch = newEpoch;
        }
        
        totalMinted += mintAmount;
        lastMintBlock = block.number;
        
        _mint(to, mintAmount);
        emit TokensMinted(to, mintAmount, currentEpoch);
        
        return mintAmount;
    }
    
    /// @notice Update inflation curve parameters (Bitcoin-style)
    function setInflationCurve(
        uint256 _halvingInterval,
        uint256 _initialReward,
        uint256 _minReward
    ) external onlyGovernance {
        require(_halvingInterval > 0, "Invalid halving interval");
        require(_initialReward > 0, "Invalid initial reward");
        
        halvingInterval = _halvingInterval;
        initialReward = _initialReward;
        minReward = _minReward;
        useCustomCurve = false;
        
        emit InflationCurveUpdated(_halvingInterval, _initialReward);
    }
    
    /// @notice Set custom epoch rewards array
    function setCustomCurve(uint256[] calldata _epochRewards) external onlyGovernance {
        require(_epochRewards.length > 0, "Empty rewards array");
        customEpochRewards = _epochRewards;
        useCustomCurve = true;
        emit CustomCurveSet(_epochRewards);
    }
    
    /// @notice Enable/disable DAO mode
    function setDAOMode(bool _enabled) external onlyGovernance {
        daoMode = _enabled;
        emit DAOModeEnabled(_enabled);
    }
    
    /// @notice Update multisig address
    function setMultisig(address _multisig) external onlyGovernance {
        require(_multisig != address(0), "Invalid multisig");
        multisig = _multisig;
        emit MultisigUpdated(_multisig);
    }
    
    /// @notice Burn tokens
    function burn(uint256 amount) external {
        _burn(msg.sender, amount);
    }
    
    /// @notice Get remaining mintable supply
    function remainingSupply() external view returns (uint256) {
        return MAX_SUPPLY - totalMinted;
    }
}
