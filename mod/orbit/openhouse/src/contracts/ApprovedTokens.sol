// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20Meta {
    function decimals() external view returns (uint8);
}

/// @title ApprovedTokens — what money is good here
/// @notice The deployer's whitelist of payment tokens: a set of USD stablecoins,
///         plus the chain's native coin, each carried with the dollar value of
///         one whole token. Every OpenHouse deal points at one of these lists
///         and refuses anything not on it — so "what can I pay rent with" is a
///         single on-chain answer, curated by one accountable key, instead of a
///         per-contract guess.
///
///         Prices are deliberately manual, not oracle-fed. A stablecoin is
///         enabled at $1 and stays there; the native coin is enabled at
///         whatever it trades at and the deployer moves it when it drifts.
///         That is a trust assumption, and it is the honest one for this
///         protocol: the same key already curates WHICH tokens are money here,
///         so it may as well say what they are worth — one list, one owner,
///         no external feed to die or lie. Disabling a token only closes the
///         door on NEW payments; money already collected in it still pays out.
contract ApprovedTokens {
    /// The chain's native coin, listed under the zero address the way the
    /// chain mod's TokenGate lists ETH under a sentinel.
    address public constant NATIVE = address(0);

    /// The one key that curates the list — the deployer, until it hands over.
    address public deployer;

    struct Token {
        bool    approved;
        uint8   decimals;   // read from the token once, at enable
        uint256 price;      // USD value of ONE WHOLE token, 18 decimals (1e18 = $1)
        string  symbol;     // display only — never used in accounting
    }

    mapping(address => Token) public tokenOf;
    address[] private _list;
    mapping(address => uint256) private _index;   // into _list, for swap-and-pop

    event TokenEnabled(address indexed token, string symbol, uint8 decimals, uint256 price);
    event TokenDisabled(address indexed token);
    event TokenRepriced(address indexed token, uint256 price);
    event DeployerTransferred(address indexed from, address indexed to);

    modifier onlyDeployer() {
        require(msg.sender == deployer, "Tokens: not the deployer");
        _;
    }

    /// @param nativeSymbol what to call the chain's coin ("ETH" on Base)
    /// @param nativePrice  USD value of one whole native coin, 18 decimals.
    ///        The list is born with the native coin on it — pass zero to start
    ///        stablecoin-only and enable it later.
    constructor(string memory nativeSymbol, uint256 nativePrice) {
        deployer = msg.sender;
        if (nativePrice > 0) _enable(NATIVE, nativeSymbol, 18, nativePrice);
    }

    // ─────────────────────────────────────── The list ──────

    /// @notice Enable an ERC-20 — a stablecoin at 1e18, or anything else at
    ///         what one whole token is worth. Decimals are read off the token
    ///         here, once, so a 6-decimal USDC and an 18-decimal DAI both
    ///         count as exactly one dollar per dollar.
    /// @dev    Fee-on-transfer and rebasing tokens must NOT be enabled: the
    ///         contracts that consult this list account for the amount sent,
    ///         not the amount that survives the token's own arithmetic.
    function enable(address token, string calldata symbol, uint256 price) external onlyDeployer {
        require(token != NATIVE, "Tokens: use enableNative");
        uint8 d = IERC20Meta(token).decimals();
        require(d <= 18, "Tokens: decimals > 18");
        _enable(token, symbol, d, price);
    }

    /// @notice Enable (or re-enable) the chain's native coin.
    function enableNative(string calldata symbol, uint256 price) external onlyDeployer {
        _enable(NATIVE, symbol, 18, price);
    }

    /// @notice Take a token off the list. New payments in it stop at the door;
    ///         nothing already collected is touched, and pools that hold it
    ///         still pay it out — a delisting is never a confiscation.
    function disable(address token) external onlyDeployer {
        require(tokenOf[token].approved, "Tokens: not approved");
        tokenOf[token].approved = false;

        uint256 i = _index[token];
        uint256 last = _list.length - 1;
        if (i != last) {
            address moved = _list[last];
            _list[i] = moved;
            _index[moved] = i;
        }
        _list.pop();
        delete _index[token];
        emit TokenDisabled(token);
    }

    /// @notice Move a token's price — the native coin drifting, or a stable
    ///         that lost its peg badly enough to admit it.
    function reprice(address token, uint256 price) external onlyDeployer {
        require(tokenOf[token].approved, "Tokens: not approved");
        require(price > 0, "Tokens: zero price");
        tokenOf[token].price = price;
        emit TokenRepriced(token, price);
    }

    function transferDeployer(address to) external onlyDeployer {
        require(to != address(0), "Tokens: zero deployer");
        emit DeployerTransferred(deployer, to);
        deployer = to;
    }

    function _enable(address token, string memory symbol, uint8 decimals_, uint256 price) internal {
        require(price > 0, "Tokens: zero price");
        Token storage t = tokenOf[token];
        require(!t.approved, "Tokens: already approved");
        t.approved = true;
        t.decimals = decimals_;
        t.price = price;
        t.symbol = symbol;
        _index[token] = _list.length;
        _list.push(token);
        emit TokenEnabled(token, symbol, decimals_, price);
    }

    // ─────────────────────────────────────────── Views ─────

    function isApproved(address token) external view returns (bool) {
        return tokenOf[token].approved;
    }

    function list() external view returns (address[] memory) {
        return _list;
    }

    function count() external view returns (uint256) {
        return _list.length;
    }

    /// @notice What an amount of an approved token is worth, in the 18-decimal
    ///         USD value units every OpenHouse number is denominated in.
    function valueOf(address token, uint256 amount) external view returns (uint256) {
        Token storage t = tokenOf[token];
        require(t.approved, "Tokens: not approved");
        return (amount * t.price) / (10 ** t.decimals);
    }
}
