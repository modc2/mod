// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title Token — an ERC-20 with a mint the deployer controls.
/// @notice Self-contained on purpose: no imports, so it compiles anywhere this
/// module runs without a package manager in the loop. If you want audited
/// inheritance, compile OpenZeppelin sources through /compile instead.
contract Token {
    string public name;
    string public symbol;
    uint8 public immutable decimals;
    uint256 public totalSupply;
    address public owner;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    event OwnershipTransferred(address indexed from, address indexed to);

    error NotOwner();
    error InsufficientBalance();
    error InsufficientAllowance();
    error ZeroAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    /// @param initialSupply in whole tokens — the constructor scales it by decimals.
    constructor(string memory _name, string memory _symbol, uint8 _decimals, uint256 initialSupply) {
        name = _name;
        symbol = _symbol;
        decimals = _decimals;
        owner = msg.sender;
        if (initialSupply > 0) _mint(msg.sender, initialSupply * (10 ** uint256(_decimals)));
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            if (allowed < amount) revert InsufficientAllowance();
            allowance[from][msg.sender] = allowed - amount;
        }
        _transfer(from, to, amount);
        return true;
    }

    /// @notice New supply, owner only. Renounce to make the cap permanent.
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    /// @notice Anyone may destroy their own tokens.
    function burn(uint256 amount) external {
        if (balanceOf[msg.sender] < amount) revert InsufficientBalance();
        unchecked { balanceOf[msg.sender] -= amount; }
        totalSupply -= amount;
        emit Transfer(msg.sender, address(0), amount);
    }

    function transferOwnership(address to) external onlyOwner {
        emit OwnershipTransferred(owner, to);
        owner = to;
    }

    /// @notice Give up minting forever. There is no way back from this.
    function renounceOwnership() external onlyOwner {
        emit OwnershipTransferred(owner, address(0));
        owner = address(0);
    }

    function _transfer(address from, address to, uint256 amount) internal {
        if (to == address(0)) revert ZeroAddress();
        if (balanceOf[from] < amount) revert InsufficientBalance();
        unchecked { balanceOf[from] -= amount; }
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }

    function _mint(address to, uint256 amount) internal {
        if (to == address(0)) revert ZeroAddress();
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }
}
