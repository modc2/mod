// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

interface IBlocTimeStaking {
    function fundTreasury(uint256 amount) external;
}

interface IBlocTimeRegistry {
    function getModule(uint256 id) external view returns (
        address owner,
        uint256 pricePerBlock,
        uint256 maxConcurrentUsers,
        uint256 currentUsers,
        bool active,
        string memory ipfsHash
    );
    function isModuleAvailable(uint256 moduleId) external view returns (bool);
    function incrementUsers(uint256 moduleId) external;
    function decrementUsers(uint256 moduleId) external;
}

/**
 * @title BlocTimeMarketplaceV3
 * @dev Enhanced marketplace with fractional rental listings
 * Allows users to list specific block ranges (from/to) instead of entire rentals
 */
contract BlocTimeMarketplaceV3 is ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct Rental {
        address renter;
        uint256 moduleId;
        uint256 startBlock;
        uint256 paidBlocks;
        bool active;
    }

    struct Listing {
        address seller;
        uint256 rentalId;
        uint256 fromBlock;  // Start of listed period (relative to rental start)
        uint256 toBlock;    // End of listed period (relative to rental start)
        uint256 price;
        bool active;
    }

    IERC20 public paymentToken;
    IBlocTimeStaking public staking;
    IBlocTimeRegistry public registry;
    uint256 public treasuryFeeBps;
    uint256 public constant MAX_FEE_BPS = 1000;
    
    uint256 public nextRentalId = 1;
    uint256 public nextListingId = 1;

    mapping(uint256 => Rental) public rentals;
    mapping(uint256 => Listing) public listings;
    mapping(address => uint256[]) public userRentals;
    mapping(uint256 => uint256[]) public rentalListings; // rentalId => listingIds

    event Rented(uint256 indexed rentalId, uint256 indexed moduleId, address indexed renter, uint256 blocks, uint256 cost);
    event ListedFractional(uint256 indexed listingId, uint256 indexed rentalId, uint256 fromBlock, uint256 toBlock, uint256 price);
    event Sold(uint256 indexed listingId, address indexed buyer, uint256 price);
    event RentalEnded(uint256 indexed rentalId);

    constructor(
        address _token,
        address _staking,
        address _registry,
        uint256 _feeBps
    ) {
        require(_staking != address(0), "Invalid staking");
        require(_registry != address(0), "Invalid registry");
        require(_feeBps <= MAX_FEE_BPS, "Fee too high");
        
        paymentToken = IERC20(_token);
        staking = IBlocTimeStaking(_staking);
        registry = IBlocTimeRegistry(_registry);
        treasuryFeeBps = _feeBps;
    }

    function rent(uint256 moduleId, uint256 blocks) external nonReentrant returns (uint256) {
        require(registry.isModuleAvailable(moduleId), "Module unavailable");
        require(blocks > 0, "Invalid blocks");

        (address owner, uint256 pricePerBlock,,,,) = registry.getModule(moduleId);
        
        uint256 cost = blocks * pricePerBlock;
        uint256 fee = (cost * treasuryFeeBps) / 10000;
        uint256 ownerAmount = cost - fee;

        paymentToken.safeTransferFrom(msg.sender, address(this), cost);
        paymentToken.safeTransfer(owner, ownerAmount);
        
        if (fee > 0) {
            paymentToken.approve(address(staking), fee);
            staking.fundTreasury(fee);
        }

        registry.incrementUsers(moduleId);

        uint256 id = nextRentalId++;
        rentals[id] = Rental({
            renter: msg.sender,
            moduleId: moduleId,
            startBlock: block.number,
            paidBlocks: blocks,
            active: true
        });
        
        userRentals[msg.sender].push(id);
        emit Rented(id, moduleId, msg.sender, blocks, cost);
        return id;
    }

    /**
     * @dev List a fractional portion of rental for sale
     * @param rentalId The rental to list from
     * @param fromBlock Start block of the period to list (relative to rental start)
     * @param toBlock End block of the period to list (relative to rental start)
     * @param price Price for this fractional period
     */
    function listFractionalForSale(
        uint256 rentalId,
        uint256 fromBlock,
        uint256 toBlock,
        uint256 price
    ) external returns (uint256) {
        Rental storage r = rentals[rentalId];
        require(r.renter == msg.sender, "Not renter");
        require(r.active, "Rental not active");
        require(fromBlock < toBlock, "Invalid block range");
        require(toBlock <= r.paidBlocks, "Range exceeds paid blocks");
        require(fromBlock >= (block.number - r.startBlock), "Cannot list past blocks");
        
        // Check for overlapping listings
        require(!hasOverlappingListing(rentalId, fromBlock, toBlock), "Overlapping listing exists");

        uint256 id = nextListingId++;
        listings[id] = Listing({
            seller: msg.sender,
            rentalId: rentalId,
            fromBlock: fromBlock,
            toBlock: toBlock,
            price: price,
            active: true
        });
        
        rentalListings[rentalId].push(id);
        emit ListedFractional(id, rentalId, fromBlock, toBlock, price);
        return id;
    }

    /**
     * @dev Check if a block range overlaps with existing active listings
     */
    function hasOverlappingListing(
        uint256 rentalId,
        uint256 fromBlock,
        uint256 toBlock
    ) public view returns (bool) {
        uint256[] memory listingIds = rentalListings[rentalId];
        
        for (uint256 i = 0; i < listingIds.length; i++) {
            Listing storage l = listings[listingIds[i]];
            if (l.active) {
                // Check for overlap: [from1, to1) overlaps [from2, to2) if from1 < to2 && from2 < to1
                if (fromBlock < l.toBlock && l.fromBlock < toBlock) {
                    return true;
                }
            }
        }
        return false;
    }

    function buy(uint256 listingId) external nonReentrant {
        Listing storage l = listings[listingId];
        require(l.active, "Listing not active");
        
        Rental storage r = rentals[l.rentalId];
        require(r.active, "Rental ended");
        require(l.fromBlock >= (block.number - r.startBlock), "Listed period has passed");

        uint256 fee = (l.price * treasuryFeeBps) / 10000;
        uint256 sellerAmount = l.price - fee;

        paymentToken.safeTransferFrom(msg.sender, address(this), l.price);
        paymentToken.safeTransfer(l.seller, sellerAmount);

        if (fee > 0) {
            paymentToken.approve(address(staking), fee);
            staking.fundTreasury(fee);
        }

        // Create new rental for buyer with the fractional period
        uint256 newRentalId = nextRentalId++;
        uint256 blockCount = l.toBlock - l.fromBlock;
        rentals[newRentalId] = Rental({
            renter: msg.sender,
            moduleId: r.moduleId,
            startBlock: r.startBlock + l.fromBlock,
            paidBlocks: blockCount,
            active: true
        });
        
        userRentals[msg.sender].push(newRentalId);
        l.active = false;

        emit Sold(listingId, msg.sender, l.price);
        emit Rented(newRentalId, r.moduleId, msg.sender, blockCount, l.price);
    }

    function cancelListing(uint256 listingId) external {
        Listing storage l = listings[listingId];
        require(l.seller == msg.sender, "Not seller");
        require(l.active, "Listing not active");
        
        l.active = false;
    }

    function endRental(uint256 rentalId) external {
        Rental storage r = rentals[rentalId];
        require(r.renter == msg.sender, "Not renter");
        require(r.active, "Already ended");
        
        r.active = false;
        registry.decrementUsers(r.moduleId);
        
        // Deactivate all active listings for this rental
        uint256[] memory listingIds = rentalListings[rentalId];
        for (uint256 i = 0; i < listingIds.length; i++) {
            if (listings[listingIds[i]].active) {
                listings[listingIds[i]].active = false;
            }
        }
        
        emit RentalEnded(rentalId);
    }

    function getRemainingBlocks(uint256 rentalId) public view returns (uint256) {
        Rental storage r = rentals[rentalId];
        if (!r.active) return 0;
        
        uint256 elapsed = block.number - r.startBlock;
        return r.paidBlocks > elapsed ? r.paidBlocks - elapsed : 0;
    }

    function getRental(uint256 id) external view returns (
        address renter,
        uint256 moduleId,
        uint256 startBlock,
        uint256 paidBlocks,
        bool active
    ) {
        Rental storage r = rentals[id];
        return (r.renter, r.moduleId, r.startBlock, r.paidBlocks, r.active);
    }

    function getListing(uint256 id) external view returns (
        address seller,
        uint256 rentalId,
        uint256 fromBlock,
        uint256 toBlock,
        uint256 price,
        bool active
    ) {
        Listing storage l = listings[id];
        return (l.seller, l.rentalId, l.fromBlock, l.toBlock, l.price, l.active);
    }

    function getUserRentals(address user) external view returns (uint256[] memory) {
        return userRentals[user];
    }

    function getRentalListings(uint256 rentalId) external view returns (uint256[] memory) {
        return rentalListings[rentalId];
    }
}