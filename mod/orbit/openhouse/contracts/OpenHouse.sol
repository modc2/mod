// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title OpenHouse — rent-to-own, on-chain
/// @notice Renters pay monthly. The protocol skims 1–5% (owner-set, hard-capped in
///         code) and everything else stays with the property: a share is credited to
///         the renter as PRINCIPAL toward the home, the rest is the owner's rent
///         income. Compare an Airbnb-style 14–16% take. Each quarter the contract
///         checkpoints ownership in proportion to principal paid off. Pay 100% of
///         the price → own the house outright.
contract OpenHouse {
    // ─────────────────────────────────────────── The home ──
    string  public description;          // the property
    uint256 public immutable homePrice;  // principal required to own outright (wei)
    address public owner;                // current legal owner / asset provider
    address public yieldVault;           // lowfi vault principal is routed to
    address public treasury;             // protocol fee sink

    // ────────────────────────────────── Owner-set terms ────
    /// The protocol take is bounded in code, not by promise: the owner picks a
    /// number inside this band and can never widen it.
    uint256 public constant MIN_FEE_BPS = 100;   // 1%
    uint256 public constant MAX_FEE_BPS = 500;   // 5%
    uint256 public platformFeeBps;               // protocol take, MIN..MAX
    /// Share of the post-fee payment credited to the renter as principal. This is
    /// the rent-to-own model dial: 10000 = every net dollar buys the house,
    /// 2500 = a classic lease-option rent credit, 0 = a plain lease.
    uint256 public rentCreditBps;

    // ───────────────────────────────────── Rent-to-own ─────
    uint256 public totalPrincipalPaid;                 // across all renters
    uint256 public totalRentPaid;                      // gross, across all renters
    uint256 public totalFees;                          // taken by the protocol
    uint256 public totalOwnerIncome;                   // rent kept by the owner
    mapping(address => uint256) public principalPaid;  // per renter
    mapping(address => uint256) public rentPaid;       // per renter, gross
    address[] public renters;
    mapping(address => bool) private _known;

    // ─────────────────────────────── Quarterly cadence ─────
    uint256 public constant QUARTER = 90 days;
    uint256 public lastRedistribution;
    uint256 public quarter;              // redistribution epoch counter

    // ─────────────────────────────────────────── Events ────
    event RentPaid(
        address indexed renter,
        uint256 amount,
        uint256 fee,
        uint256 credit,
        uint256 ownerIncome,
        uint256 principalToDate
    );
    event TermsSet(uint256 platformFeeBps, uint256 rentCreditBps);
    event TreasurySet(address indexed treasury);
    event FundsRoutedToYield(address indexed vault, uint256 amount);
    event Redistributed(uint256 indexed quarter, uint256 totalPrincipal, uint256 timestamp);
    event HomeFullyOwned(uint256 timestamp);
    event YieldVaultSet(address indexed vault);
    event OwnerTransferred(address indexed from, address indexed to);

    modifier onlyOwner() {
        require(msg.sender == owner, "OpenHouse: not owner");
        _;
    }

    constructor(
        string memory _description,
        uint256 _homePrice,
        address _yieldVault,
        address _treasury,
        uint256 _platformFeeBps,
        uint256 _rentCreditBps
    ) {
        require(_homePrice > 0, "OpenHouse: zero price");
        require(_treasury != address(0), "OpenHouse: zero treasury");
        description = _description;
        homePrice = _homePrice;
        owner = msg.sender;
        yieldVault = _yieldVault;
        treasury = _treasury;
        _setTerms(_platformFeeBps, _rentCreditBps);
        lastRedistribution = block.timestamp;
    }

    // ─────────────────────────────────────── Pay rent ──────

    /// @notice Pay rent. The protocol takes `platformFeeBps`; of what's left,
    ///         `rentCreditBps` is credited to you as principal toward the home and
    ///         the remainder is the owner's rent income. Principal is routed to the
    ///         owner's low-risk yield vault (lowfi) while it sits.
    function payRent() external payable {
        require(msg.value > 0, "OpenHouse: no payment");
        require(totalPrincipalPaid < homePrice, "OpenHouse: home already paid off");

        (uint256 fee, uint256 credit, uint256 ownerIncome) = quoteRent(msg.value);

        _track(msg.sender);
        principalPaid[msg.sender] += credit;
        rentPaid[msg.sender] += msg.value;
        totalPrincipalPaid += credit;
        totalRentPaid += msg.value;
        totalFees += fee;
        totalOwnerIncome += ownerIncome;

        emit RentPaid(msg.sender, msg.value, fee, credit, ownerIncome, principalPaid[msg.sender]);

        // Principal sits in lowfi yield; rent income and the protocol fee settle now.
        if (credit > 0) {
            address sink = yieldVault != address(0) ? yieldVault : owner;
            _send(sink, credit);
            if (sink == yieldVault) emit FundsRoutedToYield(yieldVault, credit);
        }
        if (ownerIncome > 0) _send(owner, ownerIncome);
        if (fee > 0) _send(treasury, fee);

        if (totalPrincipalPaid == homePrice) emit HomeFullyOwned(block.timestamp);
    }

    /// @notice Split a payment the way `payRent` would, without paying.
    /// @return fee protocol take, credit principal toward the home, ownerIncome the owner's rent
    function quoteRent(uint256 amount)
        public view
        returns (uint256 fee, uint256 credit, uint256 ownerIncome)
    {
        fee = (amount * platformFeeBps) / 10_000;
        uint256 net = amount - fee;
        credit = (net * rentCreditBps) / 10_000;
        // Never credit past the price — the overflow is rent, not equity.
        uint256 room = homePrice - totalPrincipalPaid;
        if (credit > room) credit = room;
        ownerIncome = net - credit;
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

    /// @notice Share of all rent ever paid that stayed with the property
    ///         (renter equity + owner income), in basis points.
    function toPropertyBps() external view returns (uint256) {
        if (totalRentPaid == 0) return 10_000 - platformFeeBps;
        return ((totalRentPaid - totalFees) * 10_000) / totalRentPaid;
    }

    function renterCount() external view returns (uint256) { return renters.length; }
    function fullyOwned() external view returns (bool) { return totalPrincipalPaid == homePrice; }
    function quarterReady() external view returns (bool) { return block.timestamp >= lastRedistribution + QUARTER; }

    // ─────────────────────────────────────── Governance ────

    /// @notice The owner tunes the deal: protocol take (1–5%) and how much of each
    ///         payment becomes the renter's equity.
    function setTerms(uint256 feeBps, uint256 creditBps) external onlyOwner {
        _setTerms(feeBps, creditBps);
    }

    function setTreasury(address _treasury) external onlyOwner {
        require(_treasury != address(0), "OpenHouse: zero treasury");
        treasury = _treasury;
        emit TreasurySet(_treasury);
    }

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

    function _setTerms(uint256 feeBps, uint256 creditBps) internal {
        require(feeBps >= MIN_FEE_BPS && feeBps <= MAX_FEE_BPS, "OpenHouse: fee out of band");
        require(creditBps <= 10_000, "OpenHouse: credit > 100%");
        platformFeeBps = feeBps;
        rentCreditBps = creditBps;
        emit TermsSet(feeBps, creditBps);
    }

    function _send(address to, uint256 amount) internal {
        (bool ok, ) = to.call{value: amount}("");
        require(ok, "OpenHouse: transfer failed");
    }

    function _track(address who) internal {
        if (!_known[who]) { _known[who] = true; renters.push(who); }
    }
}
