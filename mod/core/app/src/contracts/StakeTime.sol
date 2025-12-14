// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

contract StakeTime is Ownable, ReentrancyGuard {
    IERC20 public nativeToken;
    IERC20 public blocktimeToken;
    
    uint256 public maxBlocks;
    uint256 public treasuryDistributionPercent = 50;
    address public treasuryAddress;
    
    struct MultiplierPoint {
        uint256 blocks;
        uint256 multiplier;
    }
    
    MultiplierPoint[] public multiplierCurve;
    
    struct Stake {
        uint256 amount;
        uint256 blocks;
        uint256 startBlock;
        uint256 blocktimeMinted;
        bool active;
    }
    
    mapping(address => Stake[]) public userStakes;
    mapping(address => uint256) public totalBlocktimeMinted;
    
    event Staked(address indexed user, uint256 amount, uint256 blocks, uint256 blocktimeMinted, uint256 stakeId);
    event Unstaked(address indexed user, uint256 stakeId, uint256 amount);
    event MultiplierUpdated(uint256 blocks, uint256 multiplier);
    event MaxBlocksUpdated(uint256 newMaxBlocks);
    event TreasuryUpdated(address newTreasury, uint256 newPercent);
    
    constructor(
        address _nativeToken,
        address _blocktimeToken,
        address _treasury,
        uint256 _maxBlocks
    ) {
        nativeToken = IERC20(_nativeToken);
        blocktimeToken = IERC20(_blocktimeToken);
        treasuryAddress = _treasury;
        maxBlocks = _maxBlocks;
        
        multiplierCurve.push(MultiplierPoint(100, 100));
        multiplierCurve.push(MultiplierPoint(1000, 150));
        multiplierCurve.push(MultiplierPoint(10000, 200));
        multiplierCurve.push(MultiplierPoint(100000, 300));
    }
    
    function setMaxBlocks(uint256 _maxBlocks) external onlyOwner {
        maxBlocks = _maxBlocks;
        emit MaxBlocksUpdated(_maxBlocks);
    }
    
    function setTreasuryConfig(address _treasury, uint256 _percent) external onlyOwner {
        require(_percent <= 100, "Percent must be <= 100");
        treasuryAddress = _treasury;
        treasuryDistributionPercent = _percent;
        emit TreasuryUpdated(_treasury, _percent);
    }
    
    function setMultiplierPoint(uint256 _blocks, uint256 _multiplier) external onlyOwner {
        bool found = false;
        for (uint256 i = 0; i < multiplierCurve.length; i++) {
            if (multiplierCurve[i].blocks == _blocks) {
                multiplierCurve[i].multiplier = _multiplier;
                found = true;
                break;
            }
        }
        
        if (!found) {
            multiplierCurve.push(MultiplierPoint(_blocks, _multiplier));
            sortMultiplierCurve();
        }
        
        emit MultiplierUpdated(_blocks, _multiplier);
    }
    
    function removeMultiplierPoint(uint256 _blocks) external onlyOwner {
        for (uint256 i = 0; i < multiplierCurve.length; i++) {
            if (multiplierCurve[i].blocks == _blocks) {
                multiplierCurve[i] = multiplierCurve[multiplierCurve.length - 1];
                multiplierCurve.pop();
                sortMultiplierCurve();
                break;
            }
        }
    }
    
    function sortMultiplierCurve() private {
        for (uint256 i = 0; i < multiplierCurve.length; i++) {
            for (uint256 j = i + 1; j < multiplierCurve.length; j++) {
                if (multiplierCurve[i].blocks > multiplierCurve[j].blocks) {
                    MultiplierPoint memory temp = multiplierCurve[i];
                    multiplierCurve[i] = multiplierCurve[j];
                    multiplierCurve[j] = temp;
                }
            }
        }
    }
    
    function getMultiplier(uint256 _blocks) public view returns (uint256) {
        require(_blocks <= maxBlocks, "Blocks exceed maximum");
        
        if (multiplierCurve.length == 0) return 100;
        if (_blocks <= multiplierCurve[0].blocks) return multiplierCurve[0].multiplier;
        if (_blocks >= multiplierCurve[multiplierCurve.length - 1].blocks) {
            return multiplierCurve[multiplierCurve.length - 1].multiplier;
        }
        
        for (uint256 i = 0; i < multiplierCurve.length - 1; i++) {
            if (_blocks >= multiplierCurve[i].blocks && _blocks < multiplierCurve[i + 1].blocks) {
                uint256 x1 = multiplierCurve[i].blocks;
                uint256 y1 = multiplierCurve[i].multiplier;
                uint256 x2 = multiplierCurve[i + 1].blocks;
                uint256 y2 = multiplierCurve[i + 1].multiplier;
                
                return y1 + ((_blocks - x1) * (y2 - y1)) / (x2 - x1);
            }
        }
        
        return multiplierCurve[multiplierCurve.length - 1].multiplier;
    }
    
    function calculateBlocktimeMint(uint256 _amount, uint256 _blocks) public view returns (uint256) {
        uint256 multiplier = getMultiplier(_blocks);
        return (_amount * multiplier) / 100;
    }
    
    function stake(uint256 _amount, uint256 _blocks) external nonReentrant {
        require(_amount > 0, "Amount must be > 0");
        require(_blocks > 0 && _blocks <= maxBlocks, "Invalid block count");
        require(nativeToken.balanceOf(msg.sender) >= _amount, "Insufficient balance");
        
        uint256 blocktimeMinted = calculateBlocktimeMint(_amount, _blocks);
        
        require(
            nativeToken.transferFrom(msg.sender, address(this), _amount),
            "Transfer failed"
        );
        
        userStakes[msg.sender].push(Stake({
            amount: _amount,
            blocks: _blocks,
            startBlock: block.number,
            blocktimeMinted: blocktimeMinted,
            active: true
        }));
        
        totalBlocktimeMinted[msg.sender] += blocktimeMinted;
        
        uint256 treasuryAmount = (blocktimeMinted * treasuryDistributionPercent) / 100;
        if (treasuryAmount > 0 && treasuryAddress != address(0)) {
            require(
                blocktimeToken.transfer(msg.sender, blocktimeMinted - treasuryAmount),
                "Blocktime transfer failed"
            );
            require(
                blocktimeToken.transfer(treasuryAddress, treasuryAmount),
                "Treasury transfer failed"
            );
        } else {
            require(
                blocktimeToken.transfer(msg.sender, blocktimeMinted),
                "Blocktime transfer failed"
            );
        }
        
        emit Staked(msg.sender, _amount, _blocks, blocktimeMinted, userStakes[msg.sender].length - 1);
    }
    
    function unstake(uint256 _stakeId) external nonReentrant {
        require(_stakeId < userStakes[msg.sender].length, "Invalid stake ID");
        Stake storage userStake = userStakes[msg.sender][_stakeId];
        require(userStake.active, "Stake not active");
        require(
            block.number >= userStake.startBlock + userStake.blocks,
            "Lock period not ended"
        );
        
        uint256 amount = userStake.amount;
        userStake.active = false;
        
        require(
            nativeToken.transfer(msg.sender, amount),
            "Transfer failed"
        );
        
        emit Unstaked(msg.sender, _stakeId, amount);
    }
    
    function getUserStakes(address _user) external view returns (Stake[] memory) {
        return userStakes[_user];
    }
    
    function getActiveStakes(address _user) external view returns (uint256[] memory) {
        uint256 count = 0;
        for (uint256 i = 0; i < userStakes[_user].length; i++) {
            if (userStakes[_user][i].active) count++;
        }
        
        uint256[] memory activeIds = new uint256[](count);
        uint256 index = 0;
        for (uint256 i = 0; i < userStakes[_user].length; i++) {
            if (userStakes[_user][i].active) {
                activeIds[index] = i;
                index++;
            }
        }
        
        return activeIds;
    }
    
    function getMultiplierCurve() external view returns (MultiplierPoint[] memory) {
        return multiplierCurve;
    }
    
    function emergencyWithdraw(address _token, uint256 _amount) external onlyOwner {
        require(IERC20(_token).transfer(owner(), _amount), "Emergency withdraw failed");
    }
}