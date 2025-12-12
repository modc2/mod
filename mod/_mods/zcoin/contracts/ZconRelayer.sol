// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

contract ZconRelayer is Ownable, ReentrancyGuard {
    mapping(address => bool) public authorizedRelayers;
    mapping(address => uint256) public relayerFees;
    
    uint256 public constant MAX_FEE = 0.01 ether;
    
    event RelayerAuthorized(address indexed relayer);
    event RelayerRevoked(address indexed relayer);
    event FeeUpdated(address indexed relayer, uint256 newFee);
    event WithdrawalRelayed(address indexed relayer, address indexed recipient, uint256 fee);
    
    function authorizeRelayer(address relayer) external onlyOwner {
        authorizedRelayers[relayer] = true;
        emit RelayerAuthorized(relayer);
    }
    
    function revokeRelayer(address relayer) external onlyOwner {
        authorizedRelayers[relayer] = false;
        emit RelayerRevoked(relayer);
    }
    
    function setRelayerFee(uint256 fee) external {
        require(authorizedRelayers[msg.sender], "Not authorized");
        require(fee <= MAX_FEE, "Fee too high");
        relayerFees[msg.sender] = fee;
        emit FeeUpdated(msg.sender, fee);
    }
    
    function relayWithdrawal(
        address payable recipient,
        bytes calldata withdrawalData,
        address targetContract
    ) external nonReentrant returns (bool) {
        require(authorizedRelayers[msg.sender], "Not authorized relayer");
        
        (bool success, ) = targetContract.call(withdrawalData);
        require(success, "Withdrawal failed");
        
        uint256 fee = relayerFees[msg.sender];
        if (fee > 0) {
            payable(msg.sender).transfer(fee);
        }
        
        emit WithdrawalRelayed(msg.sender, recipient, fee);
        return true;
    }
    
    receive() external payable {}
}