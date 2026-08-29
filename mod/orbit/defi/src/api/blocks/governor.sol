// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

interface ITimelockLike {
    function queue(address target, uint256 value, bytes calldata data, bytes32 salt) external returns (bytes32);
    function execute(address target, uint256 value, bytes calldata data, bytes32 salt) external payable returns (bytes memory);
    function delay() external view returns (uint256);
}

/// On-chain governance — Governor Bravo, minus the parts that need an indexer.
/// Propose a batch of calls, vote with token weight, execute what passes.
/// Outputs a `governor` port: wire it into any block's owner slot and that
/// block's privileged calls can only happen by vote.
///
/// Votes are read as the caller's balance at the moment they vote, not from a
/// historical checkpoint. That is the honest simplification here, and it is
/// why the natural vote token for this block is a vote-escrow lock — a weight
/// you cannot borrow for one block and give back.
contract ModGovernor {
    enum State { Pending, Active, Defeated, Succeeded, Queued, Executed, Cancelled }

    IERC20 public immutable votes;
    ITimelockLike public timelock;

    uint256 public votingDelay;      // seconds before voting opens
    uint256 public votingPeriod;     // seconds the vote is open
    uint256 public proposalThreshold;
    uint16 public quorumBps;         // of vote-token total supply
    address public guardian;

    struct Proposal {
        address proposer;
        uint256 start;
        uint256 end;
        uint256 forVotes;
        uint256 againstVotes;
        uint256 eta;
        bool executed;
        bool cancelled;
        address[] targets;
        uint256[] values;
        bytes[] calldatas;
        string description;
    }

    Proposal[] internal proposals;
    mapping(uint256 => mapping(address => bool)) public hasVoted;

    event Proposed(uint256 indexed id, address indexed proposer, uint256 start, uint256 end, string description);
    event Voted(uint256 indexed id, address indexed voter, bool support, uint256 weight);
    event Queued(uint256 indexed id, uint256 eta);
    event Executed(uint256 indexed id);
    event Cancelled(uint256 indexed id);

    constructor(
        address votes_,
        address timelock_,
        uint256 votingDelay_,
        uint256 votingPeriod_,
        uint16 quorumBps_,
        address guardian_
    ) {
        require(votes_ != address(0), "NO_VOTES");
        require(votingPeriod_ >= 1 hours, "PERIOD_TOO_SHORT");
        require(quorumBps_ <= 10_000, "BAD_QUORUM");
        votes = IERC20(votes_);
        timelock = ITimelockLike(timelock_);
        votingDelay = votingDelay_;
        votingPeriod = votingPeriod_;
        quorumBps = quorumBps_;
        guardian = guardian_ == address(0) ? msg.sender : guardian_;
    }

    function proposalCount() external view returns (uint256) {
        return proposals.length;
    }

    function proposalActions(uint256 id)
        external
        view
        returns (address[] memory targets, uint256[] memory values, bytes[] memory calldatas)
    {
        Proposal storage p = proposals[id];
        return (p.targets, p.values, p.calldatas);
    }

    function proposal(uint256 id)
        external
        view
        returns (
            address proposer,
            uint256 start,
            uint256 end,
            uint256 forVotes,
            uint256 againstVotes,
            uint256 eta,
            string memory description
        )
    {
        Proposal storage p = proposals[id];
        return (p.proposer, p.start, p.end, p.forVotes, p.againstVotes, p.eta, p.description);
    }

    function quorum() public view returns (uint256) {
        return (votes.totalSupply() * quorumBps) / 10_000;
    }

    function state(uint256 id) public view returns (State) {
        Proposal storage p = proposals[id];
        if (p.cancelled) return State.Cancelled;
        if (p.executed) return State.Executed;
        if (block.timestamp < p.start) return State.Pending;
        if (block.timestamp <= p.end) return State.Active;
        if (p.forVotes <= p.againstVotes || p.forVotes < quorum()) return State.Defeated;
        if (p.eta != 0) return State.Queued;
        return State.Succeeded;
    }

    function propose(
        address[] calldata targets,
        uint256[] calldata values,
        bytes[] calldata calldatas,
        string calldata description
    ) external returns (uint256 id) {
        require(targets.length > 0, "NO_ACTIONS");
        require(targets.length == values.length && values.length == calldatas.length, "LENGTH_MISMATCH");
        require(votes.balanceOf(msg.sender) >= proposalThreshold, "BELOW_THRESHOLD");

        id = proposals.length;
        proposals.push();
        Proposal storage p = proposals[id];
        p.proposer = msg.sender;
        p.start = block.timestamp + votingDelay;
        p.end = p.start + votingPeriod;
        // Element-by-element: a nested calldata array cannot be assigned to
        // storage in one go without via-IR, and the batch is small anyway.
        for (uint256 i = 0; i < targets.length; i++) {
            p.targets.push(targets[i]);
            p.values.push(values[i]);
            p.calldatas.push(calldatas[i]);
        }
        p.description = description;
        emit Proposed(id, msg.sender, p.start, p.end, description);
    }

    function castVote(uint256 id, bool support) external returns (uint256 weight) {
        require(state(id) == State.Active, "NOT_ACTIVE");
        require(!hasVoted[id][msg.sender], "ALREADY_VOTED");
        weight = votes.balanceOf(msg.sender);
        require(weight > 0, "NO_WEIGHT");
        hasVoted[id][msg.sender] = true;
        Proposal storage p = proposals[id];
        if (support) p.forVotes += weight;
        else p.againstVotes += weight;
        emit Voted(id, msg.sender, support, weight);
    }

    /// Put a passed proposal into the timelock. Only meaningful when one is
    /// wired — without it a proposal executes directly.
    function queue(uint256 id) external {
        require(state(id) == State.Succeeded, "NOT_SUCCEEDED");
        require(address(timelock) != address(0), "NO_TIMELOCK");
        Proposal storage p = proposals[id];
        for (uint256 i = 0; i < p.targets.length; i++) {
            timelock.queue(p.targets[i], p.values[i], p.calldatas[i], bytes32(id));
        }
        p.eta = block.timestamp + timelock.delay();
        emit Queued(id, p.eta);
    }

    function execute(uint256 id) external payable {
        State s = state(id);
        require(s == State.Succeeded || s == State.Queued, "NOT_EXECUTABLE");
        Proposal storage p = proposals[id];
        require(p.eta == 0 || block.timestamp >= p.eta, "TIMELOCKED");
        p.executed = true;
        for (uint256 i = 0; i < p.targets.length; i++) {
            if (address(timelock) != address(0) && p.eta != 0) {
                timelock.execute{value: p.values[i]}(p.targets[i], p.values[i], p.calldatas[i], bytes32(id));
            } else {
                (bool ok, ) = p.targets[i].call{value: p.values[i]}(p.calldatas[i]);
                require(ok, "CALL_FAILED");
            }
        }
        emit Executed(id);
    }

    /// The guardian can stop a proposal but can never pass one.
    function cancel(uint256 id) external {
        Proposal storage p = proposals[id];
        require(msg.sender == guardian || msg.sender == p.proposer, "NOT_ALLOWED");
        require(!p.executed, "EXECUTED");
        p.cancelled = true;
        emit Cancelled(id);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    /// Only the governor itself — meaning a passed proposal — can retune the
    /// rules it runs by.
    function setParams(
        uint256 votingDelay_,
        uint256 votingPeriod_,
        uint256 proposalThreshold_,
        uint16 quorumBps_
    ) external {
        require(msg.sender == address(this) || msg.sender == address(timelock), "GOVERNANCE_ONLY");
        require(votingPeriod_ >= 1 hours, "PERIOD_TOO_SHORT");
        require(quorumBps_ <= 10_000, "BAD_QUORUM");
        votingDelay = votingDelay_;
        votingPeriod = votingPeriod_;
        proposalThreshold = proposalThreshold_;
        quorumBps = quorumBps_;
    }

    function setTimelock(address timelock_) external {
        require(msg.sender == guardian || msg.sender == address(this), "NOT_ALLOWED");
        timelock = ITimelockLike(timelock_);
    }

    function setGuardian(address guardian_) external {
        require(msg.sender == guardian, "NOT_GUARDIAN");
        guardian = guardian_;
    }

    receive() external payable {}
}
