// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title OpenHouse - Collective Asset Ownership Smart Contract
 * @notice Allows people to collectively own assets by pooling liquidity
 * @dev Managed by a registered legal entity (trust/company) with fiduciary responsibility
 */
contract OpenHouse {
    // State variables
    address public authority; // Legal entity address (trust/company)
    string public propertyDetails; // Property information
    uint256 public totalShares;
    uint256 public sharePrice;
    bool public isActive;
    
    mapping(address => uint256) public shares; // User shares
    mapping(address => uint256) public contributions; // User contributions in wei
    address[] public shareholders;
    
    // Events
    event SharesPurchased(address indexed buyer, uint256 amount, uint256 shareCount);
    event PropertyManaged(string action, uint256 timestamp);
    event DividendsDistributed(uint256 totalAmount);
    event AuthorityTransferred(address indexed oldAuthority, address indexed newAuthority);
    
    // Modifiers
    modifier onlyAuthority() {
        require(msg.sender == authority, "Only authority can call this");
        _;
    }
    
    modifier whenActive() {
        require(isActive, "Contract is not active");
        _;
    }
    
    /**
     * @notice Initialize the OpenHouse contract
     * @param _authority Address of the legal entity managing the property
     * @param _propertyDetails Details about the property
     * @param _totalShares Total number of shares available
     * @param _sharePrice Price per share in wei
     */
    constructor(
        address _authority,
        string memory _propertyDetails,
        uint256 _totalShares,
        uint256 _sharePrice
    ) {
        require(_authority != address(0), "Invalid authority address");
        require(_totalShares > 0, "Total shares must be greater than 0");
        require(_sharePrice > 0, "Share price must be greater than 0");
        
        authority = _authority;
        propertyDetails = _propertyDetails;
        totalShares = _totalShares;
        sharePrice = _sharePrice;
        isActive = true;
    }
    
    /**
     * @notice Purchase shares in the property
     * @param _shareCount Number of shares to purchase
     */
    function purchaseShares(uint256 _shareCount) external payable whenActive {
        require(_shareCount > 0, "Must purchase at least 1 share");
        uint256 cost = _shareCount * sharePrice;
        require(msg.value >= cost, "Insufficient payment");
        require(getAvailableShares() >= _shareCount, "Not enough shares available");
        
        // Add to shareholders list if new
        if (shares[msg.sender] == 0) {
            shareholders.push(msg.sender);
        }
        
        shares[msg.sender] += _shareCount;
        contributions[msg.sender] += msg.value;
        
        // Refund excess payment
        if (msg.value > cost) {
            payable(msg.sender).transfer(msg.value - cost);
        }
        
        emit SharesPurchased(msg.sender, msg.value, _shareCount);
    }
    
    /**
     * @notice Get available shares for purchase
     */
    function getAvailableShares() public view returns (uint256) {
        uint256 allocated = 0;
        for (uint256 i = 0; i < shareholders.length; i++) {
            allocated += shares[shareholders[i]];
        }
        return totalShares - allocated;
    }
    
    /**
     * @notice Distribute dividends to shareholders (rental income, etc.)
     */
    function distributeDividends() external payable onlyAuthority {
        require(msg.value > 0, "No dividends to distribute");
        require(shareholders.length > 0, "No shareholders");
        
        uint256 totalAllocated = totalShares - getAvailableShares();
        
        for (uint256 i = 0; i < shareholders.length; i++) {
            address shareholder = shareholders[i];
            uint256 shareholderShares = shares[shareholder];
            if (shareholderShares > 0) {
                uint256 dividend = (msg.value * shareholderShares) / totalAllocated;
                payable(shareholder).transfer(dividend);
            }
        }
        
        emit DividendsDistributed(msg.value);
    }
    
    /**
     * @notice Record property management action
     * @param _action Description of the management action
     */
    function recordManagementAction(string memory _action) external onlyAuthority {
        emit PropertyManaged(_action, block.timestamp);
    }
    
    /**
     * @notice Transfer authority to new legal entity
     * @param _newAuthority Address of new authority
     */
    function transferAuthority(address _newAuthority) external onlyAuthority {
        require(_newAuthority != address(0), "Invalid new authority");
        address oldAuthority = authority;
        authority = _newAuthority;
        emit AuthorityTransferred(oldAuthority, _newAuthority);
    }
    
    /**
     * @notice Get shareholder information
     * @param _shareholder Address of shareholder
     */
    function getShareholderInfo(address _shareholder) external view returns (
        uint256 shareCount,
        uint256 contribution,
        uint256 ownershipPercentage
    ) {
        shareCount = shares[_shareholder];
        contribution = contributions[_shareholder];
        uint256 totalAllocated = totalShares - getAvailableShares();
        ownershipPercentage = totalAllocated > 0 ? (shareCount * 100) / totalAllocated : 0;
    }
    
    /**
     * @notice Get contract balance
     */
    function getBalance() external view returns (uint256) {
        return address(this).balance;
    }
    
    /**
     * @notice Toggle contract active status
     */
    function toggleActive() external onlyAuthority {
        isActive = !isActive;
    }
    
    /**
     * @notice Get total number of shareholders
     */
    function getShareholderCount() external view returns (uint256) {
        return shareholders.length;
    }
}