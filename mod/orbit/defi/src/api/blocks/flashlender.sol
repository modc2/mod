// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

interface IERC3156FlashBorrower {
    function onFlashLoan(
        address initiator,
        address token,
        uint256 amount,
        uint256 fee,
        bytes calldata data
    ) external returns (bytes32);
}

/// ERC-3156 flash lender with a depositor-owned reserve. Anyone can borrow the
/// whole reserve for one call as long as it comes back with the fee inside the
/// same transaction; the fee accrues to the people who funded it.
///
/// Deposits mint a share whose value grows with fees — so the reserve is an
/// `erc20` output like any other yield-bearing position, and can be staked,
/// paired or vaulted downstream.
contract ModFlashLender is ERC20Base, Owned {
    using SafeTransfer for IERC20;

    bytes32 internal constant CALLBACK_SUCCESS = keccak256("ERC3156FlashBorrower.onFlashLoan");

    IERC20 public immutable asset;
    uint16 public feeBps;
    uint16 public protocolShareBps; // slice of the fee routed to the sink
    address public feeSink;
    uint256 public totalFees;
    bool internal entered;

    event Deposited(address indexed user, uint256 assets, uint256 shares);
    event Withdrawn(address indexed user, uint256 assets, uint256 shares);
    event FlashLoan(address indexed borrower, uint256 amount, uint256 fee);

    constructor(
        address asset_,
        string memory name_,
        string memory symbol_,
        uint16 feeBps_,
        address owner_
    ) ERC20Base(name_, symbol_, 18) Owned(owner_) {
        require(asset_ != address(0), "NO_ASSET");
        require(feeBps_ <= 500, "FEE_TOO_HIGH");
        asset = IERC20(asset_);
        feeBps = feeBps_;
        protocolShareBps = 0;
    }

    function maxFlashLoan(address token) public view returns (uint256) {
        return token == address(asset) ? asset.balanceOf(address(this)) : 0;
    }

    function flashFee(address token, uint256 amount) public view returns (uint256) {
        require(token == address(asset), "UNSUPPORTED_TOKEN");
        return (amount * feeBps) / 10_000;
    }

    function totalAssets() public view returns (uint256) {
        return asset.balanceOf(address(this));
    }

    function sharePrice() external view returns (uint256) {
        return totalSupply == 0 ? 1e18 : (totalAssets() * 1e18) / totalSupply;
    }

    function deposit(uint256 assets) external returns (uint256 shares) {
        require(assets > 0, "ZERO");
        uint256 pool = totalAssets();
        shares = totalSupply == 0 || pool == 0 ? assets : (assets * totalSupply) / pool;
        asset.pull(msg.sender, assets);
        _mint(msg.sender, shares);
        emit Deposited(msg.sender, assets, shares);
    }

    function withdraw(uint256 shares) external returns (uint256 assets) {
        require(shares > 0, "ZERO");
        assets = (shares * totalAssets()) / totalSupply;
        _burn(msg.sender, shares);
        asset.push(msg.sender, assets);
        emit Withdrawn(msg.sender, assets, shares);
    }

    /// The loan itself. Balance in, balance out — no accounting to trust, just
    /// a before-and-after on the reserve.
    function flashLoan(
        IERC3156FlashBorrower receiver,
        address token,
        uint256 amount,
        bytes calldata data
    ) external returns (bool) {
        require(!entered, "REENTRANT");
        entered = true;
        require(token == address(asset), "UNSUPPORTED_TOKEN");
        require(amount <= maxFlashLoan(token), "OVER_RESERVE");
        uint256 fee = flashFee(token, amount);
        uint256 before = totalAssets();

        asset.push(address(receiver), amount);
        require(
            receiver.onFlashLoan(msg.sender, token, amount, fee, data) == CALLBACK_SUCCESS,
            "CALLBACK_FAILED"
        );
        asset.pull(address(receiver), amount + fee);
        require(totalAssets() >= before + fee, "NOT_REPAID");

        uint256 cut = (fee * protocolShareBps) / 10_000;
        if (cut > 0) {
            asset.push(feeSink == address(0) ? owner : feeSink, cut);
        }
        totalFees += fee;
        entered = false;
        emit FlashLoan(address(receiver), amount, fee);
        return true;
    }

    // ── wiring ────────────────────────────────────────────────────────────

    function setFeeSink(address sink) external onlyOwner {
        feeSink = sink;
    }

    function setFees(uint16 feeBps_, uint16 protocolShareBps_) external onlyOwner {
        require(feeBps_ <= 500 && protocolShareBps_ <= 10_000, "BAD_FEE");
        feeBps = feeBps_;
        protocolShareBps = protocolShareBps_;
    }
}
