// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// The savings rate — DSR / sDAI / the Sky Savings Rate shape. Deposit the
/// unit, hold a share token whose redemption value grows at a governance-set
/// rate.
///
/// Yield is an accrual index (Maker calls it `chi`), not a rebase: the share
/// balance never moves, so the share token composes as an ordinary erc20 in an
/// AMM or a gauge. Interest is only payable out of the buffer someone funded,
/// which is what keeps the promise honest rather than nominal.
contract ModSavingsRate is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable asset;

    uint256 public chi = 1e18; // assets per share, 1e18 scaled
    uint256 public lastDrip;
    uint16 public rateBps;     // per year
    uint256 public totalDeposited;

    event Deposited(address indexed user, uint256 assets, uint256 shares);
    event Withdrawn(address indexed user, uint256 assets, uint256 shares);
    event Dripped(uint256 chi, uint256 owed);

    constructor(address asset_, string memory name_, string memory symbol_, uint16 rateBps_, address owner_)
        ERC20Base(name_, symbol_, 18)
        Owned(owner_)
    {
        require(asset_ != address(0), "NO_ASSET");
        require(rateBps_ <= 5_000, "RATE_TOO_HIGH");
        asset = IERC20(asset_);
        rateBps = rateBps_;
        lastDrip = block.timestamp;
    }

    /// What the index would be right now, before anyone touches storage.
    function currentChi() public view returns (uint256) {
        if (rateBps == 0 || block.timestamp <= lastDrip) return chi;
        uint256 elapsed = block.timestamp - lastDrip;
        return chi + (chi * rateBps * elapsed) / (10_000 * 365 days);
    }

    /// Assets owed to every share holder at the live index.
    function totalAssets() public view returns (uint256) {
        return (totalSupply * currentChi()) / 1e18;
    }

    /// Interest promised beyond what this contract actually holds. A healthy
    /// deployment keeps this at zero by funding the buffer.
    function shortfall() external view returns (uint256) {
        uint256 owed = totalAssets();
        uint256 held = asset.balanceOf(address(this));
        return owed > held ? owed - held : 0;
    }

    function previewDeposit(uint256 assets) public view returns (uint256) {
        return (assets * 1e18) / currentChi();
    }

    function previewRedeem(uint256 shares) public view returns (uint256) {
        return (shares * currentChi()) / 1e18;
    }

    function drip() public returns (uint256) {
        chi = currentChi();
        lastDrip = block.timestamp;
        emit Dripped(chi, totalAssets());
        return chi;
    }

    function deposit(uint256 assets, address to) external returns (uint256 shares) {
        require(assets > 0, "ZERO");
        drip();
        shares = (assets * 1e18) / chi;
        require(shares > 0, "DUST");
        asset.pull(msg.sender, assets);
        totalDeposited += assets;
        _mint(to == address(0) ? msg.sender : to, shares);
        emit Deposited(msg.sender, assets, shares);
    }

    function withdraw(uint256 shares) external returns (uint256 assets) {
        require(shares > 0, "ZERO");
        drip();
        assets = (shares * chi) / 1e18;
        uint256 held = asset.balanceOf(address(this));
        // Pay what is there. A savings rate that reverts when the buffer runs
        // dry would trap principal it does hold.
        if (assets > held) assets = held;
        _burn(msg.sender, shares);
        totalDeposited = assets > totalDeposited ? 0 : totalDeposited - assets;
        asset.push(msg.sender, assets);
        emit Withdrawn(msg.sender, assets, shares);
    }

    // ── wiring ────────────────────────────────────────────────────────────

    /// Anyone can top up the interest buffer — a CDP's surplus, a fee splitter
    /// leg, or a human with a spreadsheet.
    function fund(uint256 amount) external {
        asset.pull(msg.sender, amount);
    }

    function setRate(uint16 rateBps_) external onlyOwner {
        require(rateBps_ <= 5_000, "RATE_TOO_HIGH");
        drip();
        rateBps = rateBps_;
    }
}
