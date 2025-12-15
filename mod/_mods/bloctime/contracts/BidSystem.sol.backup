// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "./PaymentTokenWhitelist.sol";

/**
 * @title BlocTimeBidSystem
 * @dev Modular bidding system for rental slots with whitelisted token support
 * Allows users to bid on slots and slot owners to accept/reject bids
 */
contract BlocTimeBidSystem is ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct Bid {
        address bidder;
        uint256 rentalId;
        uint256 fromBlock;
        uint256 toBlock;
        uint256 bidAmount;
        address paymentToken;
        bool active;
        bool accepted;
    }

    PaymentTokenWhitelist public whitelist;
    address public marketplace;
    
    uint256 public nextBidId = 1;
    mapping(uint256 => Bid) public bids;
    mapping(uint256 => uint256[]) public rentalBids; // rentalId => bidIds
    mapping(address => uint256[]) public userBids;
    
    event BidCreated(uint256 indexed bidId, uint256 indexed rentalId, address indexed bidder, uint256 fromBlock, uint256 toBlock, uint256 amount, address paymentToken);
    event BidAccepted(uint256 indexed bidId, address indexed slotOwner);
    event BidRejected(uint256 indexed bidId, address indexed slotOwner);
    event BidCancelled(uint256 indexed bidId, address indexed bidder);

    modifier onlyMarketplace() {
        require(msg.sender == marketplace, "Only marketplace");
        _;
    }

    constructor(address _whitelist, address _marketplace) {
        require(_whitelist != address(0), "Invalid whitelist");
        require(_marketplace != address(0), "Invalid marketplace");
        
        whitelist = PaymentTokenWhitelist(_whitelist);
        marketplace = _marketplace;
    }

    /**
     * @dev Create a bid on a rental slot
     */
    function createBid(
        uint256 rentalId,
        uint256 fromBlock,
        uint256 toBlock,
        uint256 bidAmount,
        address paymentToken
    ) external nonReentrant returns (uint256) {
        require(whitelist.isTokenWhitelisted(paymentToken), "Token not whitelisted");
        require(fromBlock < toBlock, "Invalid block range");
        require(bidAmount > 0, "Invalid bid amount");

        // Lock bid amount in escrow
        IERC20(paymentToken).safeTransferFrom(msg.sender, address(this), bidAmount);

        uint256 bidId = nextBidId++;
        bids[bidId] = Bid({
            bidder: msg.sender,
            rentalId: rentalId,
            fromBlock: fromBlock,
            toBlock: toBlock,
            bidAmount: bidAmount,
            paymentToken: paymentToken,
            active: true,
            accepted: false
        });

        rentalBids[rentalId].push(bidId);
        userBids[msg.sender].push(bidId);

        emit BidCreated(bidId, rentalId, msg.sender, fromBlock, toBlock, bidAmount, paymentToken);
        return bidId;
    }

    /**
     * @dev Accept a bid (called by slot owner via marketplace)
     */
    function acceptBid(uint256 bidId, address slotOwner) external onlyMarketplace nonReentrant {
        Bid storage bid = bids[bidId];
        require(bid.active, "Bid not active");
        require(!bid.accepted, "Bid already accepted");

        bid.accepted = true;
        bid.active = false;

        // Transfer funds to slot owner
        IERC20(bid.paymentToken).safeTransfer(slotOwner, bid.bidAmount);

        emit BidAccepted(bidId, slotOwner);
    }

    /**
     * @dev Reject a bid (called by slot owner via marketplace)
     */
    function rejectBid(uint256 bidId, address slotOwner) external onlyMarketplace nonReentrant {
        Bid storage bid = bids[bidId];
        require(bid.active, "Bid not active");

        bid.active = false;

        // Refund bidder
        IERC20(bid.paymentToken).safeTransfer(bid.bidder, bid.bidAmount);

        emit BidRejected(bidId, slotOwner);
    }

    /**
     * @dev Cancel own bid
     */
    function cancelBid(uint256 bidId) external nonReentrant {
        Bid storage bid = bids[bidId];
        require(bid.bidder == msg.sender, "Not bidder");
        require(bid.active, "Bid not active");
        require(!bid.accepted, "Bid already accepted");

        bid.active = false;

        // Refund bidder
        IERC20(bid.paymentToken).safeTransfer(msg.sender, bid.bidAmount);

        emit BidCancelled(bidId, msg.sender);
    }

    /**
     * @dev Get bid details
     */
    function getBid(uint256 bidId) external view returns (
        address bidder,
        uint256 rentalId,
        uint256 fromBlock,
        uint256 toBlock,
        uint256 bidAmount,
        address paymentToken,
        bool active,
        bool accepted
    ) {
        Bid storage bid = bids[bidId];
        return (
            bid.bidder,
            bid.rentalId,
            bid.fromBlock,
            bid.toBlock,
            bid.bidAmount,
            bid.paymentToken,
            bid.active,
            bid.accepted
        );
    }

    /**
     * @dev Get all bids for a rental
     */
    function getRentalBids(uint256 rentalId) external view returns (uint256[] memory) {
        return rentalBids[rentalId];
    }

    /**
     * @dev Get all bids by a user
     */
    function getUserBids(address user) external view returns (uint256[] memory) {
        return userBids[user];
    }
}
