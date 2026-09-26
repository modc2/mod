// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Escrow — buyer funds, seller delivers, an arbiter breaks ties.
/// @notice Three parties and four states. The buyer can always release; the
/// seller can never take unilaterally; the arbiter can settle either way but
/// only after the deadline, so it cannot front-run a happy path.
contract Escrow {
    enum State { AwaitingPayment, Funded, Released, Refunded }

    address public immutable buyer;
    address public immutable seller;
    address public immutable arbiter;
    uint256 public immutable deadline;
    uint256 public amount;
    State public state;
    string public terms;

    event Funded(uint256 amount);
    event Released(address indexed to, uint256 amount);
    event Refunded(address indexed to, uint256 amount);

    error WrongState();
    error NotAllowed();
    error TooEarly();

    constructor(address _seller, address _arbiter, uint256 daysToDeadline, string memory _terms) payable {
        buyer = msg.sender;
        seller = _seller;
        arbiter = _arbiter;
        deadline = block.timestamp + (daysToDeadline * 1 days);
        terms = _terms;
        if (msg.value > 0) {
            amount = msg.value;
            state = State.Funded;
            emit Funded(msg.value);
        }
    }

    function fund() external payable {
        if (state != State.AwaitingPayment) revert WrongState();
        if (msg.sender != buyer) revert NotAllowed();
        amount += msg.value;
        state = State.Funded;
        emit Funded(msg.value);
    }

    /// @notice The buyer says it arrived — or the arbiter says so after the deadline.
    function release() external {
        if (state != State.Funded) revert WrongState();
        if (msg.sender != buyer && msg.sender != arbiter) revert NotAllowed();
        if (msg.sender == arbiter && block.timestamp < deadline) revert TooEarly();
        state = State.Released;
        uint256 payout = amount;
        amount = 0;
        (bool ok, ) = payable(seller).call{value: payout}("");
        require(ok, "payout failed");
        emit Released(seller, payout);
    }

    /// @notice The seller gives up, or the arbiter rules for the buyer after the deadline.
    function refund() external {
        if (state != State.Funded) revert WrongState();
        if (msg.sender != seller && msg.sender != arbiter) revert NotAllowed();
        if (msg.sender == arbiter && block.timestamp < deadline) revert TooEarly();
        state = State.Refunded;
        uint256 payout = amount;
        amount = 0;
        (bool ok, ) = payable(buyer).call{value: payout}("");
        require(ok, "refund failed");
        emit Refunded(buyer, payout);
    }

    function status() external view returns (State current, uint256 held, uint256 secondsLeft) {
        current = state;
        held = amount;
        secondsLeft = block.timestamp >= deadline ? 0 : deadline - block.timestamp;
    }
}
