// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Shared surface every block in the catalog speaks. Blocks are wired to each
// other by ADDRESS, so the only contract they need in common is the interface
// each port type resolves to — `erc20` ports pass an IERC20, `oracle` ports an
// IOracle, and so on. Keeping these here (rather than per-block) is what makes
// two independently-authored blocks composable on the canvas.

interface IERC20 {
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function decimals() external view returns (uint8);
}

/// Price of one whole token, scaled 1e18.
interface IOracle {
    function price() external view returns (uint256);
}

/// Anything a vault can push idle assets into.
interface IStrategy {
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function deposit(uint256 amount) external;
    function withdraw(uint256 amount) external;
}

abstract contract Owned {
    address public owner;

    event OwnerChanged(address indexed from, address indexed to);

    constructor(address owner_) {
        owner = owner_ == address(0) ? msg.sender : owner_;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "NOT_OWNER");
        _;
    }

    function setOwner(address owner_) external onlyOwner {
        emit OwnerChanged(owner, owner_);
        owner = owner_;
    }
}

/// Minimal ERC20 the share-issuing blocks (vault, AMM) inherit.
abstract contract ERC20Base {
    string public name;
    string public symbol;
    uint8 public immutable decimals;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(string memory name_, string memory symbol_, uint8 decimals_) {
        name = name_;
        symbol = symbol_;
        decimals = decimals_;
    }

    function approve(address spender, uint256 amount) public returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) public returns (bool) {
        _move(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            require(allowed >= amount, "ALLOWANCE");
            allowance[from][msg.sender] = allowed - amount;
        }
        _move(from, to, amount);
        return true;
    }

    function _move(address from, address to, uint256 amount) internal {
        require(balanceOf[from] >= amount, "BALANCE");
        unchecked {
            balanceOf[from] -= amount;
            balanceOf[to] += amount;
        }
        emit Transfer(from, to, amount);
    }

    function _mint(address to, uint256 amount) internal {
        totalSupply += amount;
        unchecked {
            balanceOf[to] += amount;
        }
        emit Transfer(address(0), to, amount);
    }

    function _burn(address from, uint256 amount) internal {
        require(balanceOf[from] >= amount, "BALANCE");
        unchecked {
            balanceOf[from] -= amount;
            totalSupply -= amount;
        }
        emit Transfer(from, address(0), amount);
    }
}

library SafeTransfer {
    function pull(IERC20 token, address from, uint256 amount) internal {
        require(token.transferFrom(from, address(this), amount), "PULL_FAILED");
    }

    function push(IERC20 token, address to, uint256 amount) internal {
        require(token.transfer(to, amount), "PUSH_FAILED");
    }
}
