// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/security/Pausable.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/**
 * @title CopyTradeProxy
 * @dev Proxy contract for copy trading through whitelisted DEX routers.
 * Safety: router whitelist, max trade size, slippage guards, daily volume cap, kill switch.
 * Supports batched multicall execution for gas efficiency.
 */
contract CopyTradeProxy is Ownable, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // --- Safety limits ---
    uint256 public maxTradeSizeWei;       // max single trade input amount (in token wei)
    uint256 public maxSlippageBps;        // max allowed slippage in basis points (10000 = 100%)
    uint256 public dailyVolumeLimit;      // max daily volume (sum of amountIn, in wei of input token)
    uint256 public dailyVolumeUsed;
    uint256 public dailyVolumeResetTime;

    // --- Router whitelist ---
    mapping(address => bool) public allowedRouters;
    address[] public routerList;

    // --- Trade tracking ---
    uint256 public nextTradeId = 1;

    struct TradeCall {
        address router;         // DEX router to call
        address tokenIn;        // token being sold
        address tokenOut;       // token being bought
        uint256 amountIn;       // amount of tokenIn to swap
        uint256 minAmountOut;   // minimum acceptable output (slippage guard)
        bytes callData;         // encoded swap call for the router
    }

    // --- Events ---
    event TradeExecuted(
        uint256 indexed tradeId,
        address indexed router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOut
    );
    event TradeFailed(uint256 indexed tradeId, address indexed router, bytes reason);
    event RouterAdded(address indexed router);
    event RouterRemoved(address indexed router);
    event EmergencyWithdraw(address indexed token, uint256 amount);
    event MaxTradeSizeUpdated(uint256 newSize);
    event MaxSlippageUpdated(uint256 newBps);
    event DailyVolumeLimitUpdated(uint256 newLimit);
    event ContractSetOwnerless();

    constructor(
        uint256 _maxTradeSizeWei,
        uint256 _maxSlippageBps,
        uint256 _dailyVolumeLimit
    ) {
        require(_maxSlippageBps <= 10000, "Slippage > 100%");
        maxTradeSizeWei = _maxTradeSizeWei;
        maxSlippageBps = _maxSlippageBps;
        dailyVolumeLimit = _dailyVolumeLimit;
        dailyVolumeResetTime = block.timestamp + 1 days;
    }

    // ========== TRADE EXECUTION ==========

    /**
     * @dev Execute a single copy trade through a whitelisted router.
     */
    function executeTrade(TradeCall calldata trade)
        external
        onlyOwner
        whenNotPaused
        nonReentrant
        returns (uint256 tradeId, uint256 amountOut)
    {
        (tradeId, amountOut) = _executeTrade(trade);
    }

    /**
     * @dev Execute a batch of trades via multicall. Failures are logged but don't revert the batch.
     */
    function executeBatch(TradeCall[] calldata trades)
        external
        onlyOwner
        whenNotPaused
        nonReentrant
        returns (uint256[] memory tradeIds, uint256[] memory amountsOut)
    {
        tradeIds = new uint256[](trades.length);
        amountsOut = new uint256[](trades.length);

        for (uint256 i = 0; i < trades.length; i++) {
            try this.executeTradeDelegated(trades[i]) returns (uint256 tid, uint256 aout) {
                tradeIds[i] = tid;
                amountsOut[i] = aout;
            } catch (bytes memory reason) {
                uint256 failedId = nextTradeId++;
                tradeIds[i] = failedId;
                amountsOut[i] = 0;
                emit TradeFailed(failedId, trades[i].router, reason);
            }
        }
    }

    /**
     * @dev Internal delegated call for batch try/catch pattern.
     */
    function executeTradeDelegated(TradeCall calldata trade)
        external
        returns (uint256 tradeId, uint256 amountOut)
    {
        require(msg.sender == address(this), "Only self");
        return _executeTrade(trade);
    }

    function _executeTrade(TradeCall calldata trade) internal returns (uint256 tradeId, uint256 amountOut) {
        // Validate router
        require(allowedRouters[trade.router], "Router not whitelisted");

        // Validate trade size
        require(trade.amountIn <= maxTradeSizeWei, "Exceeds max trade size");
        require(trade.amountIn > 0, "Zero amount");

        // Daily volume check (reset if 24h passed)
        if (block.timestamp >= dailyVolumeResetTime) {
            dailyVolumeUsed = 0;
            dailyVolumeResetTime = block.timestamp + 1 days;
        }
        dailyVolumeUsed += trade.amountIn;
        require(dailyVolumeUsed <= dailyVolumeLimit, "Daily volume exceeded");

        // Snapshot output balance before swap
        uint256 balBefore;
        if (trade.tokenOut == address(0)) {
            balBefore = address(this).balance;
        } else {
            balBefore = IERC20(trade.tokenOut).balanceOf(address(this));
        }

        // Approve router to spend tokenIn
        if (trade.tokenIn != address(0)) {
            IERC20(trade.tokenIn).safeApprove(trade.router, 0);
            IERC20(trade.tokenIn).safeApprove(trade.router, trade.amountIn);
        }

        // Execute the swap
        uint256 ethValue = trade.tokenIn == address(0) ? trade.amountIn : 0;
        (bool success, bytes memory result) = trade.router.call{value: ethValue}(trade.callData);
        require(success, string(abi.encodePacked("Swap failed: ", result)));

        // Calculate actual output
        uint256 balAfter;
        if (trade.tokenOut == address(0)) {
            balAfter = address(this).balance;
        } else {
            balAfter = IERC20(trade.tokenOut).balanceOf(address(this));
        }
        amountOut = balAfter - balBefore;

        // Slippage check
        require(amountOut >= trade.minAmountOut, "Slippage exceeded");

        tradeId = nextTradeId++;
        emit TradeExecuted(tradeId, trade.router, trade.tokenIn, trade.tokenOut, trade.amountIn, amountOut);
    }

    // ========== ROUTER MANAGEMENT ==========

    function addRouter(address router) external onlyOwner {
        require(router != address(0), "Invalid router");
        require(!allowedRouters[router], "Already added");
        allowedRouters[router] = true;
        routerList.push(router);
        emit RouterAdded(router);
    }

    function removeRouter(address router) external onlyOwner {
        require(allowedRouters[router], "Not a router");
        allowedRouters[router] = false;
        for (uint256 i = 0; i < routerList.length; i++) {
            if (routerList[i] == router) {
                routerList[i] = routerList[routerList.length - 1];
                routerList.pop();
                break;
            }
        }
        emit RouterRemoved(router);
    }

    function getRouters() external view returns (address[] memory) {
        return routerList;
    }

    // ========== TOKEN APPROVALS ==========

    function approveToken(address token, address spender, uint256 amount) external onlyOwner {
        IERC20(token).safeApprove(spender, 0);
        IERC20(token).safeApprove(spender, amount);
    }

    // ========== SAFETY SETTINGS ==========

    function setMaxTradeSize(uint256 _maxTradeSizeWei) external onlyOwner {
        maxTradeSizeWei = _maxTradeSizeWei;
        emit MaxTradeSizeUpdated(_maxTradeSizeWei);
    }

    function setMaxSlippage(uint256 _maxSlippageBps) external onlyOwner {
        require(_maxSlippageBps <= 10000, "Slippage > 100%");
        maxSlippageBps = _maxSlippageBps;
        emit MaxSlippageUpdated(_maxSlippageBps);
    }

    function setDailyVolumeLimit(uint256 _dailyVolumeLimit) external onlyOwner {
        dailyVolumeLimit = _dailyVolumeLimit;
        emit DailyVolumeLimitUpdated(_dailyVolumeLimit);
    }

    // ========== KILL SWITCH ==========

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    // ========== FUND RECOVERY ==========

    function withdrawToken(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(msg.sender, amount);
        emit EmergencyWithdraw(token, amount);
    }

    function withdrawETH(uint256 amount) external onlyOwner {
        (bool sent,) = msg.sender.call{value: amount}("");
        require(sent, "ETH transfer failed");
        emit EmergencyWithdraw(address(0), amount);
    }

    // ========== OWNERSHIP ==========

    function setOwnerless() external onlyOwner {
        emit ContractSetOwnerless();
        renounceOwnership();
    }

    // Accept ETH
    receive() external payable {}
}
