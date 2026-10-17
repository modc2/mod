// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Liquid staking — the Lido shape. Deposit the asset, get a share token that
/// stays liquid while the deposit is off earning; the share price, not the
/// balance, is what grows.
///
/// A share token rather than a rebasing balance, for the same reason wstETH
/// exists: rebasing breaks every downstream integration that stored a number.
/// Rewards are reported by an operator and must arrive as real tokens — the
/// protocol fee is taken in freshly minted shares, diluting holders by exactly
/// the fee, which is how Lido does it too.
contract ModLiquidStaking is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable asset;

    /// Everything the protocol is responsible for: the buffer here plus what
    /// the operator has staked out, plus rewards reported.
    uint256 public totalPooled;
    uint256 public stakedOut;

    address public operator;
    address public feeSink;
    uint16 public feeBps;

    struct Unstake {
        address owner;
        uint256 assets;
        uint256 readyAt;
        bool claimed;
    }

    Unstake[] public queue;
    uint256 public pendingWithdrawals;
    uint256 public unbondingPeriod;

    event Submitted(address indexed user, uint256 assets, uint256 shares);
    event Requested(address indexed user, uint256 id, uint256 assets, uint256 readyAt);
    event Claimed(address indexed user, uint256 id, uint256 assets);
    event Reported(uint256 rewards, uint256 feeShares, uint256 totalPooled);
    event StakedOut(uint256 amount);

    constructor(
        address asset_,
        string memory name_,
        string memory symbol_,
        uint16 feeBps_,
        uint256 unbondingPeriod_,
        address owner_
    ) ERC20Base(name_, symbol_, 18) Owned(owner_) {
        require(asset_ != address(0), "NO_ASSET");
        require(feeBps_ <= 3_000, "FEE_TOO_HIGH");
        asset = IERC20(asset_);
        feeBps = feeBps_;
        unbondingPeriod = unbondingPeriod_;
        operator = owner_ == address(0) ? msg.sender : owner_;
    }

    /// Assets per share, 1e18 scaled.
    function exchangeRate() public view returns (uint256) {
        if (totalSupply == 0 || totalPooled == 0) return 1e18;
        return (totalPooled * 1e18) / totalSupply;
    }

    function sharesFor(uint256 assets) public view returns (uint256) {
        return totalSupply == 0 || totalPooled == 0 ? assets : (assets * totalSupply) / totalPooled;
    }

    function assetsFor(uint256 shares) public view returns (uint256) {
        return totalSupply == 0 ? shares : (shares * totalPooled) / totalSupply;
    }

    /// Liquid asset held here, net of what the withdrawal queue already owes.
    function buffer() public view returns (uint256) {
        uint256 held = asset.balanceOf(address(this));
        return held > pendingWithdrawals ? held - pendingWithdrawals : 0;
    }

    function submit(uint256 assets, address to) external returns (uint256 shares) {
        require(assets > 0, "ZERO");
        shares = sharesFor(assets);
        require(shares > 0, "DUST");
        asset.pull(msg.sender, assets);
        totalPooled += assets;
        _mint(to == address(0) ? msg.sender : to, shares);
        emit Submitted(msg.sender, assets, shares);
    }

    /// Burn shares now, claim assets after the unbonding period. Real staking
    /// cannot exit instantly, and a protocol that pretends otherwise is just
    /// running a bank with extra steps.
    function requestWithdrawal(uint256 shares) external returns (uint256 id) {
        require(shares > 0, "ZERO");
        uint256 assets = assetsFor(shares);
        _burn(msg.sender, shares);
        totalPooled -= assets;
        pendingWithdrawals += assets;
        id = queue.length;
        queue.push(Unstake({
            owner: msg.sender,
            assets: assets,
            readyAt: block.timestamp + unbondingPeriod,
            claimed: false
        }));
        emit Requested(msg.sender, id, assets, block.timestamp + unbondingPeriod);
    }

    function claim(uint256 id) external returns (uint256 assets) {
        Unstake storage u = queue[id];
        require(u.owner == msg.sender, "NOT_OWNER");
        require(!u.claimed, "CLAIMED");
        require(block.timestamp >= u.readyAt, "UNBONDING");
        require(asset.balanceOf(address(this)) >= u.assets, "NO_LIQUIDITY");
        u.claimed = true;
        assets = u.assets;
        pendingWithdrawals -= assets;
        asset.push(msg.sender, assets);
        emit Claimed(msg.sender, id, assets);
    }

    function queueLength() external view returns (uint256) {
        return queue.length;
    }

    // ── operator ──────────────────────────────────────────────────────────

    /// Rewards arrive as tokens; the fee is minted as shares so holders are
    /// diluted by the fee and nothing else.
    function reportRewards(uint256 rewards) external returns (uint256 feeShares) {
        require(msg.sender == operator || msg.sender == owner, "NOT_OPERATOR");
        require(rewards > 0, "ZERO");
        asset.pull(msg.sender, rewards);
        uint256 fee = (rewards * feeBps) / 10_000;
        totalPooled += rewards;
        if (fee > 0 && totalSupply > 0) {
            // shares worth `fee` at the post-reward rate
            feeShares = (fee * totalSupply) / (totalPooled - fee);
            _mint(feeSink == address(0) ? owner : feeSink, feeShares);
        }
        emit Reported(rewards, feeShares, totalPooled);
    }

    /// Operator takes the buffer away to stake it. It stays in totalPooled —
    /// it is still the protocol's, it is just not here.
    function stakeOut(uint256 amount) external {
        require(msg.sender == operator, "NOT_OPERATOR");
        require(amount <= buffer(), "OVER_BUFFER");
        stakedOut += amount;
        asset.push(msg.sender, amount);
        emit StakedOut(amount);
    }

    function returnStake(uint256 amount) external {
        require(msg.sender == operator, "NOT_OPERATOR");
        asset.pull(msg.sender, amount);
        stakedOut = amount > stakedOut ? 0 : stakedOut - amount;
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function setFeeSink(address sink) external onlyOwner {
        feeSink = sink;
    }

    function setOperator(address operator_) external onlyOwner {
        require(operator_ != address(0), "NO_OPERATOR");
        operator = operator_;
    }

    function setParams(uint16 feeBps_, uint256 unbondingPeriod_) external onlyOwner {
        require(feeBps_ <= 3_000, "FEE_TOO_HIGH");
        feeBps = feeBps_;
        unbondingPeriod = unbondingPeriod_;
    }
}
