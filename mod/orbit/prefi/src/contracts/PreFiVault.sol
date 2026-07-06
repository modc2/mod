// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

interface IPreFiToken {
    function mint(address to, uint256 amount) external;
}

interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

/// @title PreFiVault - Trading vault that routes through Uniswap V3
/// @notice Opens/closes positions, captures profit to treasury, mints PREFI to traders
contract PreFiVault is ReentrancyGuard, Pausable, Ownable {
    using SafeERC20 for IERC20;

    struct Position {
        address trader;
        address asset;
        uint256 usdcIn;
        uint256 assetAmount;
        uint256 openTime;
        bool closed;
    }

    struct Market {
        uint24 feeTier;
        bool active;
        bool exists; // track if ever added (prevents duplicate pushes)
    }

    IERC20 public usdc;
    IPreFiToken public prefiToken;
    ISwapRouter public swapRouter;

    uint256 public treasuryBalance;
    uint256 public totalProfitCaptured;
    uint256 public totalPrefiMinted;
    uint256 public minPositionSize; // minimum USDC per position (6 decimals)

    mapping(uint256 => Position) public positions;
    uint256 public nextPositionId;
    mapping(address => uint256[]) public traderPositions; // trader → position IDs

    mapping(address => Market) public markets;
    address[] public marketList;

    mapping(address => bool) public treasuryWithdrawers;

    // USDC has 6 decimals, PREFI has 18 — scale factor
    uint256 constant PREFI_SCALE = 1e12;

    event MarketAdded(address indexed token, uint24 feeTier);
    event MarketRemoved(address indexed token);
    event PositionOpened(uint256 indexed positionId, address indexed trader, address asset, uint256 usdcIn, uint256 assetAmount);
    event PositionClosed(uint256 indexed positionId, address indexed trader, uint256 usdcOut, int256 pnl, uint256 prefiMinted);
    event TreasuryWithdrawal(address indexed to, uint256 amount);
    event WithdrawerSet(address indexed account, bool allowed);

    constructor(address _usdc, address _prefiToken, address _swapRouter) {
        usdc = IERC20(_usdc);
        prefiToken = IPreFiToken(_prefiToken);
        swapRouter = ISwapRouter(_swapRouter);
        minPositionSize = 1e6; // 1 USDC default minimum
    }

    // ── Markets ──────────────────────────────────────────────────────

    function addMarket(address token, uint24 feeTier) external onlyOwner {
        require(token != address(0), "zero address");
        require(feeTier == 100 || feeTier == 500 || feeTier == 3000 || feeTier == 10000, "invalid fee tier");

        if (!markets[token].exists) {
            marketList.push(token);
        }
        markets[token] = Market(feeTier, true, true);
        emit MarketAdded(token, feeTier);
    }

    function removeMarket(address token) external onlyOwner {
        require(markets[token].active, "not active");
        markets[token].active = false;
        emit MarketRemoved(token);
    }

    function getMarkets() external view returns (address[] memory tokens, uint24[] memory fees) {
        uint256 count;
        for (uint256 i = 0; i < marketList.length; i++) {
            if (markets[marketList[i]].active) count++;
        }
        tokens = new address[](count);
        fees = new uint24[](count);
        uint256 j;
        for (uint256 i = 0; i < marketList.length; i++) {
            if (markets[marketList[i]].active) {
                tokens[j] = marketList[i];
                fees[j] = markets[marketList[i]].feeTier;
                j++;
            }
        }
    }

    // ── Positions ────────────────────────────────────────────────────

    /// @param asset Token address to buy
    /// @param usdcAmount USDC to spend (6 decimals)
    /// @param minAssetOut Minimum asset tokens to receive (slippage protection)
    function openPosition(address asset, uint256 usdcAmount, uint256 minAssetOut) external nonReentrant whenNotPaused returns (uint256 positionId) {
        require(markets[asset].active, "market not active");
        require(usdcAmount >= minPositionSize, "below min position size");

        usdc.safeTransferFrom(msg.sender, address(this), usdcAmount);

        // Reset approval then approve (safe pattern for tokens like USDT)
        usdc.safeApprove(address(swapRouter), 0);
        usdc.safeApprove(address(swapRouter), usdcAmount);

        uint256 assetAmount = swapRouter.exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn: address(usdc),
                tokenOut: asset,
                fee: markets[asset].feeTier,
                recipient: address(this),
                deadline: block.timestamp + 300,
                amountIn: usdcAmount,
                amountOutMinimum: minAssetOut,
                sqrtPriceLimitX96: 0
            })
        );

        positionId = nextPositionId++;
        positions[positionId] = Position({
            trader: msg.sender,
            asset: asset,
            usdcIn: usdcAmount,
            assetAmount: assetAmount,
            openTime: block.timestamp,
            closed: false
        });
        traderPositions[msg.sender].push(positionId);

        emit PositionOpened(positionId, msg.sender, asset, usdcAmount, assetAmount);
    }

    /// @param positionId Position to close
    /// @param minUsdcOut Minimum USDC to receive back (slippage protection)
    function closePosition(uint256 positionId, uint256 minUsdcOut) external nonReentrant whenNotPaused {
        Position storage pos = positions[positionId];
        require(pos.trader == msg.sender, "not your position");
        require(!pos.closed, "already closed");

        pos.closed = true;

        IERC20(pos.asset).safeApprove(address(swapRouter), 0);
        IERC20(pos.asset).safeApprove(address(swapRouter), pos.assetAmount);

        uint256 usdcOut = swapRouter.exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn: pos.asset,
                tokenOut: address(usdc),
                fee: markets[pos.asset].feeTier,
                recipient: address(this),
                deadline: block.timestamp + 300,
                amountIn: pos.assetAmount,
                amountOutMinimum: minUsdcOut,
                sqrtPriceLimitX96: 0
            })
        );

        uint256 prefiMinted;
        int256 pnl;

        if (usdcOut > pos.usdcIn) {
            uint256 profit = usdcOut - pos.usdcIn;
            pnl = int256(profit);
            treasuryBalance += profit;
            totalProfitCaptured += profit;

            // 1 PREFI (18 dec) per 1 USDC (6 dec) of profit
            prefiMinted = profit * PREFI_SCALE;
            totalPrefiMinted += prefiMinted;
            prefiToken.mint(msg.sender, prefiMinted);

            // Principal back to trader
            usdc.safeTransfer(msg.sender, pos.usdcIn);
        } else {
            pnl = -int256(pos.usdcIn - usdcOut);
            // Loss: all remaining USDC back to trader
            usdc.safeTransfer(msg.sender, usdcOut);
        }

        emit PositionClosed(positionId, msg.sender, usdcOut, pnl, prefiMinted);
    }

    // ── Treasury ─────────────────────────────────────────────────────

    function setWithdrawer(address account, bool allowed) external onlyOwner {
        treasuryWithdrawers[account] = allowed;
        emit WithdrawerSet(account, allowed);
    }

    function withdrawTreasury(uint256 amount, address to) external {
        require(treasuryWithdrawers[msg.sender] || msg.sender == owner(), "not authorized");
        require(amount <= treasuryBalance, "exceeds treasury");
        treasuryBalance -= amount;
        usdc.safeTransfer(to, amount);
        emit TreasuryWithdrawal(to, amount);
    }

    // ── Admin ────────────────────────────────────────────────────────

    function setMinPositionSize(uint256 _min) external onlyOwner {
        minPositionSize = _min;
    }

    function pause() external onlyOwner { _pause(); }
    function unpause() external onlyOwner { _unpause(); }

    // ── Views ────────────────────────────────────────────────────────

    function getPosition(uint256 positionId) external view returns (
        address trader, address asset, uint256 usdcIn,
        uint256 assetAmount, uint256 openTime, bool closed
    ) {
        Position storage p = positions[positionId];
        return (p.trader, p.asset, p.usdcIn, p.assetAmount, p.openTime, p.closed);
    }

    function getTraderPositions(address trader) external view returns (uint256[] memory) {
        return traderPositions[trader];
    }

    function marketCount() external view returns (uint256) {
        uint256 count;
        for (uint256 i = 0; i < marketList.length; i++) {
            if (markets[marketList[i]].active) count++;
        }
        return count;
    }
}
