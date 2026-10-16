// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

/**
 * @title DollarTime (DTT)
 * @dev Collective ownership of one revenue-generating thing (a house, a
 *      machine), paid for in dollars and time.
 *
 *      THE RULES — identical to the paper ledger (engine.py) and the NEAR
 *      contract (near/src/lib.rs):
 *
 *      cap       21,000,000 DTT ever.
 *      emission  at most 7,200 DTT a day: day d emits 7,200 for d < 2916,
 *                4,800 on day 2916, nothing after. 2916*7200+4800 = 21M.
 *      buying    contribute(dollars, lockDays) puts dollars (the stable
 *                ERC20, 6 decimals) in the treasury. weight = dollars *
 *                (365 + lockDays), lockDays 0..1460. A day's emission is
 *                split by weight: tokens = emission * w / W. Claim after
 *                the day closes.
 *      burning   a day with no buyer is burned: its emission is never
 *                minted and still counts against the cap.
 *      lock      tokens from a locked contribution cannot be transferred
 *                until unlockAt; they still earn revenue.
 *      revenue   depositRevenue(dollars) is split pro-rata across every
 *                DTT in existence at that moment (acc-per-share, 1e36).
 *      treasury  the steward (owner) spends it on the asset and sets the
 *                valuation, so valueOf(you) = valuation * balance / supply.
 */
contract DollarTime is ERC20, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    uint256 public constant DAY = 86400;
    uint256 public constant CAP = 21_000_000 ether;
    uint256 public constant DAILY = 7_200 ether;
    uint256 public constant FULL_DAYS = CAP / DAILY;                  // 2916
    uint256 public constant LAST_DAY_EMISSION = CAP - FULL_DAYS * DAILY; // 4,800 DTT
    uint256 public constant MAX_LOCK_DAYS = 1460;
    uint256 public constant ACC = 1e36;

    struct Position {
        address owner;
        uint64 day;
        uint64 lockDays;
        uint64 unlockAt;
        bool claimed;
        uint256 dollars;
        uint256 weight;
        uint256 minted;
    }

    IERC20 public immutable dollar;      // the stablecoin contributions are made in
    uint256 public immutable genesis;

    string public assetName;
    string public assetDescription;
    uint256 public valuation;            // steward's mark, in dollar units

    uint256 public treasury;             // dollars held for / from the asset
    uint256 public allocated;            // sum of emission(d) for days with weight
    mapping(uint256 => uint256) public dayWeight;

    uint256 public nextId = 1;
    mapping(uint256 => Position) public positions;
    mapping(address => uint256[]) private _positionsOf;

    uint256 public accPerShare;
    uint256 public revenueTotal;
    mapping(address => uint256) private _owed;
    mapping(address => uint256) private _debt;

    event Contributed(address indexed who, uint256 indexed id, uint256 day, uint256 dollars, uint256 lockDays, uint256 weight);
    event Claimed(address indexed who, uint256 indexed id, uint256 minted);
    event Revenue(address indexed from, uint256 dollars, bool toHolders, string memo);
    event Withdrawn(address indexed who, uint256 dollars);
    event Spent(address indexed to, uint256 dollars, string memo);
    event AssetSet(string name, uint256 valuation);

    constructor(address _dollar, string memory _assetName, string memory _symbol)
        ERC20(string(abi.encodePacked("DollarTime ", _assetName)), _symbol)
    {
        require(_dollar != address(0), "dollar required");
        dollar = IERC20(_dollar);
        genesis = block.timestamp;
        assetName = _assetName;
    }

    // ── the schedule ─────────────────────────────────────────────────────────

    function currentDay() public view returns (uint256) {
        return (block.timestamp - genesis) / DAY;
    }

    function emission(uint256 day) public pure returns (uint256) {
        if (day < FULL_DAYS) return DAILY;
        if (day == FULL_DAYS) return LAST_DAY_EMISSION;
        return 0;
    }

    /// @dev total emission for days 0..days-1
    function cumulative(uint256 days_) public pure returns (uint256) {
        return days_ <= FULL_DAYS ? days_ * DAILY : CAP;
    }

    function emitted() public view returns (uint256) {
        return cumulative(currentDay());
    }

    /// @dev emission of every closed day nobody bought — gone for good
    function burned() public view returns (uint256) {
        uint256 d = currentDay();
        uint256 allocatedClosed = allocated;
        if (dayWeight[d] > 0) allocatedClosed -= emission(d);
        return cumulative(d) - allocatedClosed;
    }

    function weightOf(uint256 dollars, uint256 lockDays) public pure returns (uint256) {
        return dollars * (365 + lockDays);
    }

    // ── contribute / claim ──────────────────────────────────────────────────

    function contribute(uint256 dollars, uint256 lockDays) external nonReentrant returns (uint256 id) {
        require(dollars > 0, "dollars must be > 0");
        require(lockDays <= MAX_LOCK_DAYS, "lock too long");
        uint256 d = currentDay();
        require(emission(d) > 0, "emission is over");

        dollar.safeTransferFrom(msg.sender, address(this), dollars);
        treasury += dollars;

        uint256 w = weightOf(dollars, lockDays);
        if (dayWeight[d] == 0) allocated += emission(d);
        dayWeight[d] += w;

        id = nextId++;
        positions[id] = Position({
            owner: msg.sender, day: uint64(d), lockDays: uint64(lockDays),
            unlockAt: uint64(block.timestamp + lockDays * DAY), claimed: false,
            dollars: dollars, weight: w, minted: 0
        });
        _positionsOf[msg.sender].push(id);
        emit Contributed(msg.sender, id, d, dollars, lockDays, w);
    }

    function claimable(uint256 id) public view returns (uint256) {
        Position storage p = positions[id];
        if (p.owner == address(0) || p.claimed || p.day >= currentDay()) return 0;
        return emission(p.day) * p.weight / dayWeight[p.day];
    }

    function claim(uint256 id) public nonReentrant returns (uint256 minted) {
        Position storage p = positions[id];
        require(p.owner == msg.sender, "not your position");
        require(!p.claimed, "already claimed");
        require(p.day < currentDay(), "day still open");
        minted = emission(p.day) * p.weight / dayWeight[p.day];
        p.claimed = true;
        p.minted = minted;
        _mint(msg.sender, minted);
        emit Claimed(msg.sender, id, minted);
    }

    function claimAll() external returns (uint256 total) {
        uint256[] storage ids = _positionsOf[msg.sender];
        for (uint256 i = 0; i < ids.length; i++) {
            if (claimable(ids[i]) > 0) total += claim(ids[i]);
        }
    }

    // ── locks ───────────────────────────────────────────────────────────────

    function lockedBalance(address who) public view returns (uint256 locked) {
        uint256[] storage ids = _positionsOf[who];
        for (uint256 i = 0; i < ids.length; i++) {
            Position storage p = positions[ids[i]];
            if (p.claimed && p.unlockAt > block.timestamp) locked += p.minted;
        }
    }

    function unlockedBalance(address who) public view returns (uint256) {
        return balanceOf(who) - lockedBalance(who);
    }

    function positionsOf(address who) external view returns (uint256[] memory) {
        return _positionsOf[who];
    }

    function _beforeTokenTransfer(address from, address to, uint256 amount) internal override {
        super._beforeTokenTransfer(from, to, amount);
        if (from != address(0)) {
            require(amount <= unlockedBalance(from), "amount exceeds unlocked balance");
            _settleOwed(from);
        }
        if (to != address(0)) _settleOwed(to);
    }

    function _afterTokenTransfer(address from, address to, uint256 amount) internal override {
        super._afterTokenTransfer(from, to, amount);
        if (from != address(0)) _debt[from] = balanceOf(from) * accPerShare / ACC;
        if (to != address(0)) _debt[to] = balanceOf(to) * accPerShare / ACC;
    }

    // ── revenue ─────────────────────────────────────────────────────────────

    function _settleOwed(address who) internal {
        uint256 bal = balanceOf(who);
        _owed[who] += bal * accPerShare / ACC - _debt[who];
        _debt[who] = bal * accPerShare / ACC;
    }

    function owed(address who) public view returns (uint256) {
        return _owed[who] + balanceOf(who) * accPerShare / ACC - _debt[who];
    }

    /// @dev What the asset earned. Split across every DTT alive right now;
    ///      pooled in the treasury if none exist yet.
    function depositRevenue(uint256 dollars, string calldata memo) external nonReentrant {
        require(dollars > 0, "dollars must be > 0");
        dollar.safeTransferFrom(msg.sender, address(this), dollars);
        bool toHolders = totalSupply() > 0;
        if (toHolders) accPerShare += dollars * ACC / totalSupply();
        else treasury += dollars;
        revenueTotal += dollars;
        emit Revenue(msg.sender, dollars, toHolders, memo);
    }

    function withdrawRevenue() external nonReentrant returns (uint256 amount) {
        _settleOwed(msg.sender);
        amount = _owed[msg.sender];
        _owed[msg.sender] = 0;
        if (amount > 0) dollar.safeTransfer(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    // ── steward ─────────────────────────────────────────────────────────────

    function setAsset(string calldata name_, string calldata description_, uint256 valuation_) external onlyOwner {
        assetName = name_;
        assetDescription = description_;
        valuation = valuation_;
        emit AssetSet(name_, valuation_);
    }

    function spend(address to, uint256 dollars, string calldata memo) external onlyOwner nonReentrant {
        require(dollars > 0 && dollars <= treasury, "amount exceeds treasury");
        treasury -= dollars;
        dollar.safeTransfer(to, dollars);
        emit Spent(to, dollars, memo);
    }

    // ── views ───────────────────────────────────────────────────────────────

    function shareBps(address who) external view returns (uint256) {
        return totalSupply() == 0 ? 0 : balanceOf(who) * 10000 / totalSupply();
    }

    function valueOfHolder(address who) external view returns (uint256) {
        return totalSupply() == 0 ? 0 : valuation * balanceOf(who) / totalSupply();
    }

    function stats() external view returns (
        uint256 day, uint256 emissionToday, uint256 weightToday, uint256 emitted_,
        uint256 burned_, uint256 supply, uint256 treasury_, uint256 valuation_, uint256 accPerShare_
    ) {
        day = currentDay();
        emissionToday = emission(day);
        weightToday = dayWeight[day];
        emitted_ = emitted();
        burned_ = burned();
        supply = totalSupply();
        treasury_ = treasury;
        valuation_ = valuation;
        accPerShare_ = accPerShare;
    }
}
