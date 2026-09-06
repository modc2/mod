// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./common.sol";

/// Routes an `erc20` stream to up to four payees by basis points. Drop it on
/// the canvas downstream of any fee-producing block to make the revenue split
/// part of the protocol's shape rather than an off-chain habit.
contract ModFeeSplitter is Owned {
    using SafeTransfer for IERC20;

    IERC20 public immutable token;
    address[] public payees;
    uint16[] public sharesBps;

    event Released(address indexed to, uint256 amount);
    event SplitUpdated(uint256 payees);

    constructor(
        address token_,
        address payee0,
        uint16 bps0,
        address payee1,
        uint16 bps1,
        address owner_
    ) Owned(owner_) {
        require(token_ != address(0), "NO_TOKEN");
        token = IERC20(token_);
        _add(payee0, bps0);
        _add(payee1, bps1);
        require(_totalBps() == 10_000, "BPS_SUM");
    }

    function _add(address payee, uint16 bps) internal {
        if (payee == address(0) || bps == 0) return;
        payees.push(payee);
        sharesBps.push(bps);
    }

    function _totalBps() internal view returns (uint256 total) {
        for (uint256 i = 0; i < sharesBps.length; i++) total += sharesBps[i];
    }

    function payeeCount() external view returns (uint256) {
        return payees.length;
    }

    /// Distribute everything sitting in the contract.
    function release() external returns (uint256 distributed) {
        uint256 balance = token.balanceOf(address(this));
        require(balance > 0, "NOTHING");
        for (uint256 i = 0; i < payees.length; i++) {
            uint256 cut = i == payees.length - 1
                ? balance - distributed
                : (balance * sharesBps[i]) / 10_000;
            distributed += cut;
            token.push(payees[i], cut);
            emit Released(payees[i], cut);
        }
    }

    function setSplit(address[] calldata payees_, uint16[] calldata sharesBps_) external onlyOwner {
        require(payees_.length == sharesBps_.length && payees_.length > 0, "LENGTH");
        delete payees;
        delete sharesBps;
        for (uint256 i = 0; i < payees_.length; i++) _add(payees_[i], sharesBps_[i]);
        require(_totalBps() == 10_000, "BPS_SUM");
        emit SplitUpdated(payees.length);
    }
}
