// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

interface IMintableToken {
    function mint(address to, uint256 amount) external;
    function burnFrom(address from, uint256 amount) external;
    function transferOwnership(address newOwner) external;
}

/**
 * @title Treasury
 * @dev The dollar door of a BlocTime instance: deposit the reserve token
 *      (a dollar stable like USDC) and the treasury mints the instance's
 *      NativeToken 1:1 per dollar — one whole token per $1, whatever the
 *      reserve's decimals. Redeem burns tokens and pays the dollar back.
 *
 *      For minting to work the treasury must OWN the NativeToken (the
 *      deploy flow hands it `transferOwnership` right after both exist).
 *      The owner of the treasury can spend the reserve (`ownerWithdraw`) —
 *      it is their treasury — after which redemptions are first-come,
 *      first-served against whatever backing remains.
 */
contract Treasury is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    IERC20 public immutable token;          // NativeToken, 18 decimals
    IERC20 public immutable reserve;        // the dollar (e.g. USDC)
    uint8 public immutable reserveDecimals;
    uint256 public totalDeposited;          // reserve units taken in
    uint256 public totalRedeemed;           // reserve units paid back out

    event Deposited(address indexed from, uint256 reserveIn, uint256 minted);
    event Redeemed(address indexed from, uint256 burned, uint256 reserveOut);
    event OwnerWithdrawal(address indexed to, uint256 amount);

    constructor(address _token, address _reserve) Ownable(msg.sender) {
        require(_token != address(0) && _reserve != address(0), "Zero address");
        token = IERC20(_token);
        reserve = IERC20(_reserve);
        uint8 dec = IERC20Metadata(_reserve).decimals();
        require(dec <= 18, "Reserve decimals > 18");
        reserveDecimals = dec;
    }

    /// @dev Reserve units → 18-decimal token units (USDC's 6 → ×1e12).
    function scale() public view returns (uint256) {
        return 10 ** (18 - reserveDecimals);
    }

    /// @notice Tokens minted for `reserveAmount` of the reserve: 1 per $1.
    function quoteMint(uint256 reserveAmount) public view returns (uint256) {
        return reserveAmount * scale();
    }

    /// @notice Deposit dollars, receive tokens 1:1. Approve the reserve first.
    function deposit(uint256 reserveAmount) external nonReentrant returns (uint256 minted) {
        require(reserveAmount > 0, "Amount > 0");
        reserve.safeTransferFrom(msg.sender, address(this), reserveAmount);
        minted = quoteMint(reserveAmount);
        totalDeposited += reserveAmount;
        IMintableToken(address(token)).mint(msg.sender, minted);
        emit Deposited(msg.sender, reserveAmount, minted);
    }

    /// @notice Burn tokens, receive dollars 1:1. Approve the token first.
    ///         Burns only the exact amount the payout covers — sub-cent dust
    ///         stays in the caller's wallet instead of vanishing.
    function redeem(uint256 tokenAmount) external nonReentrant returns (uint256 reserveOut) {
        reserveOut = tokenAmount / scale();
        require(reserveOut > 0, "Amount too small");
        uint256 burned = reserveOut * scale();
        IMintableToken(address(token)).burnFrom(msg.sender, burned);
        totalRedeemed += reserveOut;
        reserve.safeTransfer(msg.sender, reserveOut);
        emit Redeemed(msg.sender, burned, reserveOut);
    }

    function reserveBalance() public view returns (uint256) {
        return reserve.balanceOf(address(this));
    }

    /// @notice Everything a UI needs in one read.
    function info() external view returns (
        address token_, address reserve_, uint8 reserveDecimals_,
        uint256 reserveBalance_, uint256 totalDeposited_, uint256 totalRedeemed_
    ) {
        return (address(token), address(reserve), reserveDecimals,
                reserveBalance(), totalDeposited, totalRedeemed);
    }

    /// @notice The owner spending their treasury. Redemptions after this are
    ///         backed only by what remains.
    function ownerWithdraw(uint256 amount) external onlyOwner {
        reserve.safeTransfer(owner(), amount);
        emit OwnerWithdrawal(owner(), amount);
    }

    /// @notice Pass the NativeToken's mint keys onward (e.g. to a successor
    ///         treasury). This treasury can no longer mint afterwards.
    function transferTokenOwnership(address newOwner) external onlyOwner {
        IMintableToken(address(token)).transferOwnership(newOwner);
    }

    /// @notice Rescue tokens that are NOT the reserve (mis-sent airdrops etc).
    function rescue(address other, uint256 amount) external onlyOwner {
        require(other != address(reserve), "Use ownerWithdraw for the reserve");
        IERC20(other).safeTransfer(owner(), amount);
    }
}
