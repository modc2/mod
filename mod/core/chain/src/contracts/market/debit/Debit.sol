// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title IMarket
 * @dev Interface for Market contract debit operations
 */
interface IMarket {
    function debitBurn(address from, uint256 amount) external;
    function debitMint(address to, uint256 amount) external;
    function addTreasuryFees(uint256 amount) external;
    function balanceOf(address account) external view returns (uint256);
    function treasury() external view returns (address);
}

/**
 * @title Debit
 * @dev EIP-712 signature-based debit contract with multisig authority approvals.
 * Authorities must approve debits with a max spending limit and deadline (capped at 1 day).
 * Client sets a threshold of required approvals. Defaults to 1 with owner as authority.
 */
contract Debit is EIP712, ReentrancyGuard, Ownable {
    using ECDSA for bytes32;

    IMarket public market;
    uint256 public constant TREASURY_FEE_PERCENT = 5;
    uint256 public nextTransactionId = 1;
    bool public signatureRequired;

    bytes32 public constant DEBIT_TYPEHASH = keccak256(
        "DebitAuthorization(address client,address provider,uint256 amount,uint256 nonce,uint256 deadline)"
    );

    mapping(address => uint256) public nonces;

    // Authority management: client => authority[]
    mapping(address => address[]) internal _clientAuthorities;
    mapping(address => mapping(address => bool)) public isAuthority;
    mapping(address => uint256) public approvalThreshold; // 0 = default to 1

    // Multisig approvals: client => authority => Approval
    struct Approval {
        uint256 maxAmount;   // remaining spending allowance
        uint256 deadline;    // capped at 1 day from submission
    }
    mapping(address => mapping(address => Approval)) public approvals;

    // Daily spending limit (8 decimals). Default: 1000 * 10^8
    uint256 public constant DEFAULT_DAILY_LIMIT = 1000_00000000;
    mapping(address => uint256) public dailyLimit;
    mapping(address => mapping(uint256 => uint256)) public dailySpent;

    struct CachedDebit {
        uint256 amount;
        uint256 timestamp;
        uint256 nonce;
    }

    mapping(address => mapping(address => CachedDebit)) public lastDebit;

    event DebitExecuted(
        uint256 indexed txId,
        address indexed client,
        address indexed provider,
        uint256 amount,
        uint256 treasuryFee,
        uint256 providerAmount,
        uint256 nonce
    );
    event MarketUpdated(address indexed newMarket);
    event SignatureRequirementUpdated(bool required);
    event DailyLimitUpdated(address indexed client, uint256 newLimit);
    event AuthorityAdded(address indexed client, address indexed authority);
    event AuthorityRemoved(address indexed client, address indexed authority);
    event ThresholdUpdated(address indexed client, uint256 threshold);
    event DebitApproved(address indexed client, address indexed authority, uint256 maxAmount, uint256 deadline);
    event ApprovalRevoked(address indexed client, address indexed authority);
    event ContractSetOwnerless();

    constructor(
        address _market
    ) EIP712("MarketDebit", "1") {
        require(_market != address(0), "Invalid market");
        market = IMarket(_market);
    }

    function setMarket(address _market) external onlyOwner {
        require(_market != address(0), "Invalid market");
        market = IMarket(_market);
        emit MarketUpdated(_market);
    }

    function setSignatureRequired(bool _required) external onlyOwner {
        signatureRequired = _required;
        emit SignatureRequirementUpdated(_required);
    }

    /**
     * @dev Permanently renounce ownership, making the contract fully decentralized.
     * After calling, setMarket() and setSignatureRequired() become permanently locked.
     * executeDebitUnsigned() will only be callable by the market contract.
     * Clients must use the authority system for approval management.
     * This action is irreversible.
     */
    function setOwnerless() external onlyOwner {
        emit ContractSetOwnerless();
        renounceOwnership();
    }

    // ========== AUTHORITY MANAGEMENT ==========

    function setDailyLimit(uint256 _limit) external {
        require(_limit > 0, "Limit must be > 0");
        dailyLimit[msg.sender] = _limit;
        emit DailyLimitUpdated(msg.sender, _limit);
    }

    /**
     * @dev Add an authority that can approve debits on your behalf.
     */
    function addAuthority(address authority) external {
        require(authority != address(0), "Invalid authority");
        require(!isAuthority[msg.sender][authority], "Already authorized");
        isAuthority[msg.sender][authority] = true;
        _clientAuthorities[msg.sender].push(authority);
        emit AuthorityAdded(msg.sender, authority);
    }

    /**
     * @dev Remove an authority. Also clears their pending approval.
     */
    function removeAuthority(address authority) external {
        require(isAuthority[msg.sender][authority], "Not authorized");
        isAuthority[msg.sender][authority] = false;

        // Remove from array (swap-and-pop)
        address[] storage auths = _clientAuthorities[msg.sender];
        for (uint256 i = 0; i < auths.length; i++) {
            if (auths[i] == authority) {
                auths[i] = auths[auths.length - 1];
                auths.pop();
                break;
            }
        }

        // Clear any pending approval
        delete approvals[msg.sender][authority];

        // Auto-reduce threshold if it exceeds remaining authority count
        if (approvalThreshold[msg.sender] > auths.length && auths.length > 0) {
            approvalThreshold[msg.sender] = auths.length;
            emit ThresholdUpdated(msg.sender, auths.length);
        }

        emit AuthorityRemoved(msg.sender, authority);
    }

    /**
     * @dev Set the number of authority approvals required for a debit.
     * Must be > 0 and <= number of authorities.
     */
    function setApprovalThreshold(uint256 threshold) external {
        uint256 authCount = _clientAuthorities[msg.sender].length;
        if (authCount > 0) {
            require(threshold > 0 && threshold <= authCount, "Invalid threshold");
        }
        approvalThreshold[msg.sender] = threshold;
        emit ThresholdUpdated(msg.sender, threshold);
    }

    /**
     * @dev Authority approves spending for a client. Specifies a max spending
     * allowance and a deadline that cannot exceed 1 day from now.
     */
    function approveDebit(address client, uint256 maxAmount, uint256 deadline) external {
        if (_clientAuthorities[client].length == 0) {
            require(msg.sender == owner(), "Not owner");
        } else {
            require(isAuthority[client][msg.sender], "Not authorized");
        }
        require(maxAmount > 0, "Invalid amount");
        require(deadline > block.timestamp, "Deadline passed");
        require(deadline <= block.timestamp + 1 days, "Deadline exceeds 1 day");

        approvals[client][msg.sender] = Approval({
            maxAmount: maxAmount,
            deadline: deadline
        });
        emit DebitApproved(client, msg.sender, maxAmount, deadline);
    }

    /**
     * @dev Authority revokes their pending approval for a client.
     */
    function revokeApproval(address client) external {
        require(approvals[client][msg.sender].deadline > 0, "No approval");
        delete approvals[client][msg.sender];
        emit ApprovalRevoked(client, msg.sender);
    }

    // ========== VIEW: AUTHORITY ==========

    function getEffectiveThreshold(address client) public view returns (uint256) {
        uint256 t = approvalThreshold[client];
        return t > 0 ? t : 1;
    }

    function getAuthorities(address client) external view returns (address[] memory) {
        return _clientAuthorities[client];
    }

    function getAuthorityCount(address client) external view returns (uint256) {
        return _clientAuthorities[client].length;
    }

    function getApproval(address client, address authority) external view returns (uint256 maxAmount, uint256 deadline) {
        Approval memory a = approvals[client][authority];
        return (a.maxAmount, a.deadline);
    }

    /**
     * @dev Count how many valid (non-expired, sufficient amount) approvals exist.
     */
    function getValidApprovalCount(address client, uint256 amount) external view returns (uint256 count) {
        if (_clientAuthorities[client].length == 0) {
            Approval memory a = approvals[client][owner()];
            if (a.deadline >= block.timestamp && a.maxAmount >= amount) return 1;
            return 0;
        }
        address[] storage auths = _clientAuthorities[client];
        for (uint256 i = 0; i < auths.length; i++) {
            Approval memory a = approvals[client][auths[i]];
            if (a.deadline >= block.timestamp && a.maxAmount >= amount) {
                count++;
            }
        }
    }

    // ========== INTERNAL: APPROVALS ==========

    /**
     * @dev Verify threshold approvals exist and consume the amount from them.
     * Each consumed approval has its maxAmount reduced by the debit amount.
     * Reverts if insufficient valid approvals.
     */
    function _checkAndConsumeApprovals(address client, uint256 amount) internal {
        if (_clientAuthorities[client].length == 0) {
            // No authorities configured — approval system not active.
            // If owner has explicitly set an approval, enforce it; otherwise auto-pass.
            Approval storage a = approvals[client][owner()];
            if (a.deadline > 0) {
                require(a.deadline >= block.timestamp && a.maxAmount >= amount, "No valid approval");
                a.maxAmount -= amount;
            }
            return;
        }

        uint256 threshold = getEffectiveThreshold(client);
        uint256 validCount = 0;
        address[] storage auths = _clientAuthorities[client];
        for (uint256 i = 0; i < auths.length && validCount < threshold; i++) {
            Approval storage a = approvals[client][auths[i]];
            if (a.deadline >= block.timestamp && a.maxAmount >= amount) {
                a.maxAmount -= amount;
                validCount++;
            }
        }
        require(validCount >= threshold, "Insufficient approvals");
    }

    // ========== DAILY LIMIT ==========

    function getEffectiveDailyLimit(address client) public view returns (uint256) {
        uint256 limit = dailyLimit[client];
        return limit > 0 ? limit : DEFAULT_DAILY_LIMIT;
    }

    function _getCurrentDay() internal view returns (uint256) {
        return block.timestamp / 1 days;
    }

    function getDailySpent(address client) external view returns (uint256) {
        return dailySpent[client][_getCurrentDay()];
    }

    function getDailyRemaining(address client) external view returns (uint256) {
        uint256 limit = getEffectiveDailyLimit(client);
        uint256 spent = dailySpent[client][_getCurrentDay()];
        return spent >= limit ? 0 : limit - spent;
    }

    // ========== SIGNATURE ==========

    function _verifySignature(
        address client,
        address provider,
        uint256 stableAmount,
        uint256 deadline,
        bytes memory signature
    ) internal returns (uint256 usedNonce) {
        require(signature.length == 65, "Invalid signature length");
        require(block.timestamp <= deadline, "Signature expired");

        usedNonce = nonces[client];
        bytes32 structHash = keccak256(abi.encode(
            DEBIT_TYPEHASH,
            client,
            provider,
            stableAmount,
            usedNonce,
            deadline
        ));

        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := mload(add(signature, 32))
            s := mload(add(signature, 64))
            v := byte(0, mload(add(signature, 96)))
        }

        address signer = ECDSA.recover(_hashTypedDataV4(structHash), v, r, s);
        require(signer == client, "Invalid signature");

        nonces[client] = usedNonce + 1;
    }

    // ========== TRANSFER ==========

    function _executeTransfer(
        address client,
        address provider,
        uint256 stableAmount,
        uint256 usedNonce
    ) internal returns (uint256 txId) {
        uint256 today = _getCurrentDay();
        uint256 limit = getEffectiveDailyLimit(client);
        uint256 spent = dailySpent[client][today];
        require(spent + stableAmount <= limit, "Daily spending limit exceeded");
        dailySpent[client][today] = spent + stableAmount;

        uint256 treasuryFee = (stableAmount * TREASURY_FEE_PERCENT) / 100;
        uint256 providerAmount = stableAmount - treasuryFee;

        market.debitBurn(client, stableAmount);
        market.debitMint(market.treasury(), treasuryFee);
        market.addTreasuryFees(treasuryFee);
        market.debitMint(provider, providerAmount);

        lastDebit[client][provider] = CachedDebit({
            amount: stableAmount,
            timestamp: block.timestamp,
            nonce: usedNonce
        });

        txId = nextTransactionId++;
        emit DebitExecuted(txId, client, provider, stableAmount, treasuryFee, providerAmount, usedNonce);
    }

    // ========== EXECUTE ==========

    /**
     * @dev Execute a debit with client EIP-712 signature + multisig authority approvals.
     * Requires threshold valid approvals and a valid client signature.
     */
    function executeDebit(
        address client,
        address provider,
        uint256 stableAmount,
        uint256 deadline,
        bytes memory signature
    ) external nonReentrant returns (uint256) {
        require(stableAmount > 0, "Invalid amount");
        require(client != address(0), "Invalid client");
        require(provider != address(0), "Invalid provider");
        require(market.balanceOf(client) >= stableAmount, "Insufficient balance");

        _checkAndConsumeApprovals(client, stableAmount);
        uint256 usedNonce = _verifySignature(client, provider, stableAmount, deadline, signature);
        return _executeTransfer(client, provider, stableAmount, usedNonce);
    }

    /**
     * @dev Execute a debit without client signature. Requires multisig authority approvals.
     * Only works when signatureRequired is false.
     */
    function executeDebitUnsigned(
        address client,
        address provider,
        uint256 stableAmount
    ) external nonReentrant returns (uint256) {
        require(!signatureRequired, "Signatures required");
        require(msg.sender == owner() || msg.sender == address(market), "Unauthorized");
        require(stableAmount > 0, "Invalid amount");
        require(client != address(0), "Invalid client");
        require(provider != address(0), "Invalid provider");
        require(market.balanceOf(client) >= stableAmount, "Insufficient balance");

        _checkAndConsumeApprovals(client, stableAmount);

        uint256 usedNonce = nonces[client];
        nonces[client] = usedNonce + 1;

        return _executeTransfer(client, provider, stableAmount, usedNonce);
    }

    // ========== VIEW ==========

    function getLastDebit(address _client, address _provider) external view returns (
        uint256 amount,
        uint256 timestamp,
        uint256 nonce
    ) {
        CachedDebit memory cached = lastDebit[_client][_provider];
        return (cached.amount, cached.timestamp, cached.nonce);
    }

    function getNonce(address _client) external view returns (uint256) {
        return nonces[_client];
    }

    function getDomainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }
}
