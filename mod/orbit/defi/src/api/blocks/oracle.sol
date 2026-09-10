// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Owner-published price feed, 1e18 scaled. Deliberately simple: the point of
/// an `oracle` port is that any block reading a price does not care whether the
/// number came from here, a Chainlink adapter, or a TWAP block.
contract ModFixedOracle is IOracle, Owned {
    uint256 private _price;
    uint256 public updatedAt;
    uint256 public maxStaleness;

    event PriceUpdated(uint256 price, uint256 at);

    constructor(uint256 initialPrice, uint256 maxStaleness_, address owner_) Owned(owner_) {
        _price = initialPrice;
        maxStaleness = maxStaleness_;
        updatedAt = block.timestamp;
    }

    function price() external view returns (uint256) {
        if (maxStaleness > 0) {
            require(block.timestamp - updatedAt <= maxStaleness, "STALE_PRICE");
        }
        return _price;
    }

    /// Unchecked read for UIs that want to show a stale price rather than revert.
    function peek() external view returns (uint256 value, uint256 at) {
        return (_price, updatedAt);
    }

    function setPrice(uint256 newPrice) external onlyOwner {
        require(newPrice > 0, "ZERO_PRICE");
        _price = newPrice;
        updatedAt = block.timestamp;
        emit PriceUpdated(newPrice, block.timestamp);
    }
}
