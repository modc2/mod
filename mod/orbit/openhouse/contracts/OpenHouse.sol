// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title OpenHouse — rent-to-own, on-chain
/// @notice Renters pay monthly. Every payment is recorded as PRINCIPAL toward the
///         home, not profit for a landlord. Each quarter the contract redistributes
///         ownership in proportion to principal paid off. The current owner routes
///         pooled payments into low-risk yield (lowfi), earning interest while
///         renters build equity. Pay 100% of the price → own the house outright.
contract OpenHouse {
    // ─────────────────────────────────────────── The home ──
    string  public description;          // the property
    uint256 public immutable homePrice;  // principal required to own outright (wei)
    address public owner;                // current legal owner / asset provider
    address public yieldVault;           // lowfi vault idle funds are routed to

    // ───────────────────────────────────── Rent-to-own ─────
    uint256 public totalPrincipalPaid;                 // across all renters
    mapping(address => uint256) public principalPaid;  // per renter
    address[] public renters;
    mapping(address => bool) private _known;

    // ─────────────────────────────── Quarterly cadence ─────
    uint256 public constant QUARTER = 90 days;
    uint256 public lastRedistribution;
    uint256 public quarter;              // redistribution epoch counter

    // ─────────────────────────────────────────── Events ────
    event RentPaid(address indexed renter, uint256 amount, uint256 principalToDate);
    event FundsRoutedToYield(address indexed vault, uint256 amount);
    event Redistributed(uint256 indexed quarter, uint256 totalPrincipal, uint256 timestamp);
    event HomeFullyOwned(uint256 timestamp);
    event YieldVaultSet(address indexed vault);
    event OwnerTransferred(address indexed from, address indexed to);

    modifier onlyOwner() {
        require(msg.sender == owner, "OpenHouse: not owner");
        _;
    }

    constructor(string memory _description, uint256 _homePrice, address _yieldVault) {
        require(_homePrice > 0, "OpenHouse: zero price");
        description = _description;
        homePrice = _homePrice;
        owner = msg.sender;
        yieldVault = _yieldVault;
        lastRedistribution = block.timestamp;
    }

    // ─────────────────────────────────────── Pay rent ──────

    /// @notice Pay rent. The full payment is credited as principal toward ownership
    ///         and routed to the owner's low-risk yield vault (lowfi).
    function payRent() external payable {
        require(msg.value > 0, "OpenHouse: no payment");
        require(totalPrincipalPaid + msg.value <= homePrice, "OpenHouse: home already paid off");

        _track(msg.sender);
        principalPaid[msg.sender] += msg.value;
        totalPrincipalPaid += msg.value;
        emit RentPaid(msg.sender, msg.value, principalPaid[msg.sender]);

        // Route funds to lowfi yield so idle capital earns interest for the owner.
        address sink = yieldVault != address(0) ? yieldVault : owner;
        (bool ok, ) = sink.call{value: msg.value}("");
        require(ok, "OpenHouse: routing failed");
        if (sink == yieldVault) emit FundsRoutedToYield(yieldVault, msg.value);

        if (totalPrincipalPaid == homePrice) emit HomeFullyOwned(block.timestamp);
    }

    // ─────────────────────────── Quarterly redistribution ──

    /// @notice Once per quarter, checkpoint ownership from principal paid off.
    ///         Stakes are always derivable from principal; this anchors them to a
    ///         fixed 90-day cadence and emits the snapshot on-chain.
    function redistribute() external {
        require(block.timestamp >= lastRedistribution + QUARTER, "OpenHouse: quarter not elapsed");
        lastRedistribution = block.timestamp;
        quarter += 1;
        emit Redistributed(quarter, totalPrincipalPaid, block.timestamp);
    }

    // ─────────────────────────────────────────── Views ─────

    /// @notice A renter's equity in basis points (10000 = 100% of the home).
    function equityBps(address renter) public view returns (uint256) {
        return (principalPaid[renter] * 10_000) / homePrice;
    }

    /// @notice Share of the home owned by renters so far, in basis points.
    function ownedBps() external view returns (uint256) {
        return (totalPrincipalPaid * 10_000) / homePrice;
    }

    /// @notice Principal still owed before the home is fully owned.
    function remainingPrincipal() external view returns (uint256) {
        return homePrice - totalPrincipalPaid;
    }

    function renterCount() external view returns (uint256) { return renters.length; }
    function fullyOwned() external view returns (bool) { return totalPrincipalPaid == homePrice; }
    function quarterReady() external view returns (bool) { return block.timestamp >= lastRedistribution + QUARTER; }

    // ─────────────────────────────────────── Governance ────

    function setYieldVault(address vault) external onlyOwner {
        yieldVault = vault;
        emit YieldVaultSet(vault);
    }

    function transferOwnership(address to) external onlyOwner {
        require(to != address(0), "OpenHouse: zero address");
        emit OwnerTransferred(owner, to);
        owner = to;
    }

    // ─────────────────────────────────────────── Internal ──

    function _track(address who) internal {
        if (!_known[who]) { _known[who] = true; renters.push(who); }
    }
}
