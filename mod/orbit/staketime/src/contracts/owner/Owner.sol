// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/**
 * @title Owner
 * @dev Flexible ownership contract for protocol governance.
 *      Supports three modes:
 *        - Eoa:      single admin, immediate execution
 *        - Multisig: M-of-N signers approve proposals before execution
 *        - Token:    governance token holders vote on proposals
 *
 *      Deployed once and set as the Ownable.owner() of all protocol
 *      contracts. Authorized callers forward admin calls through
 *      execute() or the proposal system.
 *
 *      Self-governing: configuration changes (mode transitions, signer
 *      updates, admin transfers) are executed through the same
 *      execute/proposal flow targeting address(this).
 */
contract Owner {

    // ── Types ────────────────────────────────────────────────────────────

    enum OwnerType { Eoa, Multisig, Token }

    struct Proposal {
        address target;
        bytes   data;
        address proposer;
        uint256 createdBlock;
        uint256 approvalCount;
        uint256 votesFor;
        uint256 votesAgainst;
        bool    executed;
        bool    cancelled;
    }

    // ── Events ───────────────────────────────────────────────────────────

    event Executed(address indexed target, bytes data, uint256 indexed proposalId);
    event Proposed(uint256 indexed proposalId, address indexed target, address indexed proposer);
    event Approved(uint256 indexed proposalId, address indexed signer);
    event Voted(uint256 indexed proposalId, address indexed voter, bool support, uint256 weight);
    event ProposalExecuted(uint256 indexed proposalId);
    event ProposalCancelled(uint256 indexed proposalId);
    event OwnerTypeChanged(OwnerType oldType, OwnerType newType);
    event SignersUpdated(uint256 signerCount, uint256 threshold);
    event AdminChanged(address indexed oldAdmin, address indexed newAdmin);
    event TimelockChanged(uint256 oldTimelock, uint256 newTimelock);
    event TokenConfigChanged(address indexed token, uint256 quorumBps, uint256 votingPeriod);

    // ── State ────────────────────────────────────────────────────────────

    OwnerType public ownerType;

    // EOA mode
    address public admin;

    // Multisig mode
    address[] public signers;
    mapping(address => bool) public isSigner;
    uint256 public threshold;

    // Token mode
    IERC20  public token;
    uint256 public quorumBps;       // basis points of total supply
    uint256 public votingPeriod;    // blocks

    // Proposals (multisig + token)
    Proposal[] public proposals;
    mapping(uint256 => mapping(address => bool)) public hasVoted;

    // Timelock
    uint256 public timelockBlocks;

    // ── Modifiers ────────────────────────────────────────────────────────

    modifier onlySelf() {
        require(msg.sender == address(this), "only self");
        _;
    }

    // ── Constructor ──────────────────────────────────────────────────────

    /**
     * @dev Deploys in EOA mode with the given admin.
     *      Transition to Multisig or Token via execute(address(this), ...).
     */
    constructor(address _admin) {
        require(_admin != address(0), "zero admin");
        admin = _admin;
        ownerType = OwnerType.Eoa;
        emit AdminChanged(address(0), _admin);
    }

    // ── EOA: Immediate Execution ─────────────────────────────────────────

    /**
     * @dev Forward a call to target. EOA mode only — immediate execution.
     */
    function execute(address target, bytes calldata data) external returns (bytes memory) {
        require(ownerType == OwnerType.Eoa, "not eoa mode");
        require(msg.sender == admin, "not admin");

        (bool ok, bytes memory result) = target.call(data);
        require(ok, "execution failed");

        emit Executed(target, data, type(uint256).max);
        return result;
    }

    /**
     * @dev Batch execute multiple calls. EOA mode only.
     */
    function executeBatch(
        address[] calldata targets,
        bytes[]   calldata dataList
    ) external returns (bytes[] memory results) {
        require(ownerType == OwnerType.Eoa, "not eoa mode");
        require(msg.sender == admin, "not admin");
        require(targets.length == dataList.length, "length mismatch");

        results = new bytes[](targets.length);
        for (uint256 i = 0; i < targets.length; i++) {
            (bool ok, bytes memory result) = targets[i].call(dataList[i]);
            require(ok, "execution failed");
            results[i] = result;
            emit Executed(targets[i], dataList[i], type(uint256).max);
        }
    }

    // ── Proposals (Multisig + Token) ─────────────────────────────────────

    /**
     * @dev Create a proposal. Multisig: any signer. Token: any token holder.
     */
    function propose(address target, bytes calldata data) external returns (uint256 proposalId) {
        require(
            ownerType == OwnerType.Multisig || ownerType == OwnerType.Token,
            "proposals not enabled"
        );

        if (ownerType == OwnerType.Multisig) {
            require(isSigner[msg.sender], "not a signer");
        } else {
            require(token.balanceOf(msg.sender) > 0, "no governance tokens");
        }

        proposalId = proposals.length;
        proposals.push(Proposal({
            target:        target,
            data:          data,
            proposer:      msg.sender,
            createdBlock:  block.number,
            approvalCount: 0,
            votesFor:      0,
            votesAgainst:  0,
            executed:      false,
            cancelled:     false
        }));

        emit Proposed(proposalId, target, msg.sender);
    }

    /**
     * @dev Approve a proposal (multisig mode). Each signer approves once.
     */
    function approve(uint256 proposalId) external {
        require(ownerType == OwnerType.Multisig, "not multisig mode");
        require(isSigner[msg.sender], "not a signer");
        require(proposalId < proposals.length, "invalid proposal");

        Proposal storage p = proposals[proposalId];
        require(!p.executed, "already executed");
        require(!p.cancelled, "cancelled");
        require(!hasVoted[proposalId][msg.sender], "already approved");

        hasVoted[proposalId][msg.sender] = true;
        p.approvalCount++;

        emit Approved(proposalId, msg.sender);

        // Auto-execute if threshold reached and timelock satisfied
        if (p.approvalCount >= threshold && block.number >= p.createdBlock + timelockBlocks) {
            _executeProposal(proposalId);
        }
    }

    /**
     * @dev Vote on a proposal (token mode). Weight = token balance at call time.
     */
    function vote(uint256 proposalId, bool support) external {
        require(ownerType == OwnerType.Token, "not token mode");
        require(proposalId < proposals.length, "invalid proposal");

        Proposal storage p = proposals[proposalId];
        require(!p.executed, "already executed");
        require(!p.cancelled, "cancelled");
        require(!hasVoted[proposalId][msg.sender], "already voted");
        require(block.number <= p.createdBlock + votingPeriod, "voting ended");

        uint256 weight = token.balanceOf(msg.sender);
        require(weight > 0, "no governance tokens");

        hasVoted[proposalId][msg.sender] = true;

        if (support) {
            p.votesFor += weight;
        } else {
            p.votesAgainst += weight;
        }

        emit Voted(proposalId, msg.sender, support, weight);
    }

    /**
     * @dev Execute a proposal after conditions are met.
     *      Multisig: threshold approvals + timelock.
     *      Token: voting period ended + quorum met + majority.
     */
    function executeProposal(uint256 proposalId) external {
        require(proposalId < proposals.length, "invalid proposal");

        Proposal storage p = proposals[proposalId];
        require(!p.executed, "already executed");
        require(!p.cancelled, "cancelled");
        require(block.number >= p.createdBlock + timelockBlocks, "timelock active");

        if (ownerType == OwnerType.Multisig) {
            require(p.approvalCount >= threshold, "insufficient approvals");
        } else if (ownerType == OwnerType.Token) {
            require(block.number > p.createdBlock + votingPeriod, "voting not ended");
            uint256 quorum = (token.totalSupply() * quorumBps) / 10000;
            require(p.votesFor >= quorum, "quorum not met");
            require(p.votesFor > p.votesAgainst, "no majority");
        } else {
            revert("proposals not enabled");
        }

        _executeProposal(proposalId);
    }

    /**
     * @dev Cancel a proposal. Only the proposer or admin can cancel.
     */
    function cancel(uint256 proposalId) external {
        require(proposalId < proposals.length, "invalid proposal");

        Proposal storage p = proposals[proposalId];
        require(!p.executed, "already executed");
        require(!p.cancelled, "already cancelled");
        require(msg.sender == p.proposer || msg.sender == admin, "not authorized");

        p.cancelled = true;
        emit ProposalCancelled(proposalId);
    }

    // ── Self-Governance (called via execute/proposal targeting this) ─────

    function setOwnerType(OwnerType _type) external onlySelf {
        OwnerType old = ownerType;

        if (_type == OwnerType.Multisig) {
            require(signers.length > 0 && threshold > 0, "configure signers first");
        } else if (_type == OwnerType.Token) {
            require(address(token) != address(0), "configure token first");
        }

        ownerType = _type;
        emit OwnerTypeChanged(old, _type);
    }

    function setAdmin(address _admin) external onlySelf {
        require(_admin != address(0), "zero admin");
        address old = admin;
        admin = _admin;
        emit AdminChanged(old, _admin);
    }

    function setSigners(address[] calldata _signers, uint256 _threshold) external onlySelf {
        require(_signers.length > 0, "empty signers");
        require(_threshold > 0 && _threshold <= _signers.length, "invalid threshold");

        // Clear old signers
        for (uint256 i = 0; i < signers.length; i++) {
            isSigner[signers[i]] = false;
        }
        delete signers;

        // Set new signers
        for (uint256 i = 0; i < _signers.length; i++) {
            require(_signers[i] != address(0), "zero signer");
            require(!isSigner[_signers[i]], "duplicate signer");
            isSigner[_signers[i]] = true;
            signers.push(_signers[i]);
        }
        threshold = _threshold;

        emit SignersUpdated(_signers.length, _threshold);
    }

    function changeThreshold(uint256 _threshold) external onlySelf {
        require(_threshold > 0 && _threshold <= signers.length, "invalid threshold");
        threshold = _threshold;
        emit SignersUpdated(signers.length, _threshold);
    }

    function addSigner(address signer, uint256 _threshold) external onlySelf {
        require(signer != address(0), "zero signer");
        require(!isSigner[signer], "already a signer");
        require(_threshold > 0 && _threshold <= signers.length + 1, "invalid threshold");

        isSigner[signer] = true;
        signers.push(signer);
        threshold = _threshold;

        emit SignersUpdated(signers.length, _threshold);
    }

    function removeSigner(address signer, uint256 _threshold) external onlySelf {
        require(isSigner[signer], "not a signer");
        require(signers.length - 1 > 0, "cannot remove last signer");
        require(_threshold > 0 && _threshold <= signers.length - 1, "invalid threshold");

        isSigner[signer] = false;

        // Swap-and-pop
        for (uint256 i = 0; i < signers.length; i++) {
            if (signers[i] == signer) {
                signers[i] = signers[signers.length - 1];
                signers.pop();
                break;
            }
        }
        threshold = _threshold;

        emit SignersUpdated(signers.length, _threshold);
    }

    function setTokenConfig(address _token, uint256 _quorumBps, uint256 _votingPeriod) external onlySelf {
        require(_token != address(0), "zero token");
        require(_quorumBps > 0 && _quorumBps <= 10000, "invalid quorum");
        require(_votingPeriod > 0, "zero voting period");

        token = IERC20(_token);
        quorumBps = _quorumBps;
        votingPeriod = _votingPeriod;

        emit TokenConfigChanged(_token, _quorumBps, _votingPeriod);
    }

    function setTimelock(uint256 _blocks) external onlySelf {
        uint256 old = timelockBlocks;
        timelockBlocks = _blocks;
        emit TimelockChanged(old, _blocks);
    }

    // ── Views ────────────────────────────────────────────────────────────

    function proposalCount() external view returns (uint256) {
        return proposals.length;
    }

    function getProposal(uint256 proposalId) external view returns (
        address target,
        address proposer,
        uint256 createdBlock,
        uint256 approvalCount,
        uint256 votesFor,
        uint256 votesAgainst,
        bool    executed,
        bool    cancelled
    ) {
        require(proposalId < proposals.length, "invalid proposal");
        Proposal storage p = proposals[proposalId];
        return (
            p.target, p.proposer, p.createdBlock,
            p.approvalCount, p.votesFor, p.votesAgainst,
            p.executed, p.cancelled
        );
    }

    function getSigners() external view returns (address[] memory) {
        return signers;
    }

    function signerCount() external view returns (uint256) {
        return signers.length;
    }

    // ── Internal ─────────────────────────────────────────────────────────

    function _executeProposal(uint256 proposalId) internal {
        Proposal storage p = proposals[proposalId];
        p.executed = true;

        (bool ok,) = p.target.call(p.data);
        require(ok, "proposal execution failed");

        emit ProposalExecuted(proposalId);
        emit Executed(p.target, p.data, proposalId);
    }

    // ── Receive ──────────────────────────────────────────────────────────

    receive() external payable {}
}
