// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title NamespaceRegistry
 * @dev Proxy-router namespace prioritised by staketime.
 *
 *      Mods claim a name (e.g. "polymarket") by locking STT.
 *      The current claim with the highest STT stake owns the
 *      name in the proxy router.
 *
 *      Anyone can outbid an existing claim by staking strictly
 *      more STT and providing their own target_url. The previous
 *      owner's STT is unlocked and held for them to withdraw.
 *
 *      The proxy router (routy) periodically syncs from this
 *      contract: getActiveClaims() → POST /_api/sync.
 */
contract NamespaceRegistry is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ── Types ────────────────────────────────────────────────────────────

    struct Claim {
        address owner;
        string  name;
        string  targetUrl;
        string  kind;          // "app" | "api"
        uint256 stake;
        uint256 claimedAt;
        bool    active;
    }

    // ── Events ───────────────────────────────────────────────────────────

    event Claimed(bytes32 indexed nameHash, string name, address indexed owner, uint256 stake, string targetUrl);
    event Outbid(bytes32 indexed nameHash, address indexed prevOwner, address indexed newOwner, uint256 prevStake, uint256 newStake);
    event ToppedUp(bytes32 indexed nameHash, address indexed owner, uint256 added, uint256 newStake);
    event Released(bytes32 indexed nameHash, address indexed owner, uint256 returned);
    event TargetUpdated(bytes32 indexed nameHash, address indexed owner, string targetUrl, string kind);
    event Withdrew(address indexed user, uint256 amount);

    // ── State ────────────────────────────────────────────────────────────

    IERC20 public immutable stt;            // StakeTime token (STT)
    uint256 public minClaimStake;           // floor for any claim

    mapping(bytes32 => Claim) public claims;
    bytes32[] public nameHashes;
    mapping(bytes32 => bool) internal _seen;

    // STT held for users whose claims were outbid / released
    mapping(address => uint256) public withdrawable;

    // ── Constructor ──────────────────────────────────────────────────────

    constructor(address _stt, uint256 _minClaimStake) {
        require(_stt != address(0), "zero stt");
        stt = IERC20(_stt);
        minClaimStake = _minClaimStake;
    }

    // ── Claiming ─────────────────────────────────────────────────────────

    /**
     * @notice Claim a name (or outbid the current holder) by locking STT.
     * @param name       Namespace key (e.g. "polymarket"). Case-sensitive.
     * @param stake      STT amount to lock; must exceed current claim's stake
     *                   (or be ≥ minClaimStake if name is unclaimed).
     * @param targetUrl  Proxy target URL (e.g. "http://localhost:3000").
     * @param kind       "app" or "api".
     */
    function claim(
        string calldata name,
        uint256 stake,
        string calldata targetUrl,
        string calldata kind
    ) external nonReentrant {
        require(bytes(name).length > 0 && bytes(name).length <= 64, "bad name");
        require(_validName(name), "name chars");
        require(bytes(targetUrl).length > 0, "bad url");
        require(_isKind(kind), "kind app|api");
        require(stake >= minClaimStake, "below floor");

        bytes32 h = keccak256(bytes(name));
        Claim storage c = claims[h];

        if (c.active) {
            require(stake > c.stake, "must outbid");
            address prev = c.owner;
            uint256 prevStake = c.stake;

            // Pull new stake first, then credit previous owner.
            stt.safeTransferFrom(msg.sender, address(this), stake);
            withdrawable[prev] += prevStake;

            c.owner     = msg.sender;
            c.targetUrl = targetUrl;
            c.kind      = kind;
            c.stake     = stake;
            c.claimedAt = block.timestamp;

            emit Outbid(h, prev, msg.sender, prevStake, stake);
            emit Claimed(h, name, msg.sender, stake, targetUrl);
        } else {
            stt.safeTransferFrom(msg.sender, address(this), stake);

            claims[h] = Claim({
                owner:     msg.sender,
                name:      name,
                targetUrl: targetUrl,
                kind:      kind,
                stake:     stake,
                claimedAt: block.timestamp,
                active:    true
            });
            if (!_seen[h]) {
                _seen[h] = true;
                nameHashes.push(h);
            }

            emit Claimed(h, name, msg.sender, stake, targetUrl);
        }
    }

    /// @notice Add more STT to your existing claim (raises outbid floor).
    function topUp(string calldata name, uint256 amount) external nonReentrant {
        require(amount > 0, "zero");
        bytes32 h = keccak256(bytes(name));
        Claim storage c = claims[h];
        require(c.active, "no claim");
        require(c.owner == msg.sender, "not owner");

        stt.safeTransferFrom(msg.sender, address(this), amount);
        c.stake += amount;

        emit ToppedUp(h, msg.sender, amount, c.stake);
    }

    /// @notice Update the target URL or kind without changing stake.
    function setTarget(
        string calldata name,
        string calldata targetUrl,
        string calldata kind
    ) external {
        require(bytes(targetUrl).length > 0, "bad url");
        require(_isKind(kind), "kind app|api");
        bytes32 h = keccak256(bytes(name));
        Claim storage c = claims[h];
        require(c.active, "no claim");
        require(c.owner == msg.sender, "not owner");

        c.targetUrl = targetUrl;
        c.kind = kind;
        emit TargetUpdated(h, msg.sender, targetUrl, kind);
    }

    /// @notice Release a name and recover staked STT.
    function release(string calldata name) external nonReentrant {
        bytes32 h = keccak256(bytes(name));
        Claim storage c = claims[h];
        require(c.active, "no claim");
        require(c.owner == msg.sender, "not owner");

        uint256 amt = c.stake;
        c.active = false;
        c.stake = 0;

        stt.safeTransfer(msg.sender, amt);
        emit Released(h, msg.sender, amt);
    }

    /// @notice Withdraw STT credited from being outbid.
    function withdraw() external nonReentrant {
        uint256 amt = withdrawable[msg.sender];
        require(amt > 0, "nothing");
        withdrawable[msg.sender] = 0;
        stt.safeTransfer(msg.sender, amt);
        emit Withdrew(msg.sender, amt);
    }

    // ── Views ────────────────────────────────────────────────────────────

    function getClaim(string calldata name) external view returns (Claim memory) {
        return claims[keccak256(bytes(name))];
    }

    function exists(string calldata name) external view returns (bool) {
        return claims[keccak256(bytes(name))].active;
    }

    /**
     * @notice Minimum stake required to outbid an existing claim
     *         (or minClaimStake if the name is unclaimed).
     */
    function outbidThreshold(string calldata name) external view returns (uint256) {
        Claim storage c = claims[keccak256(bytes(name))];
        if (!c.active) return minClaimStake;
        return c.stake + 1;
    }

    /// @notice Active claims, in registration order.
    function getActiveClaims() external view returns (Claim[] memory result) {
        uint256 n;
        for (uint256 i = 0; i < nameHashes.length; i++) {
            if (claims[nameHashes[i]].active) n++;
        }
        result = new Claim[](n);
        uint256 j;
        for (uint256 i = 0; i < nameHashes.length; i++) {
            Claim storage c = claims[nameHashes[i]];
            if (c.active) result[j++] = c;
        }
    }

    function totalNames() external view returns (uint256) {
        return nameHashes.length;
    }

    // ── Admin ────────────────────────────────────────────────────────────

    function setMinClaimStake(uint256 v) external onlyOwner {
        minClaimStake = v;
    }

    // ── Internal ─────────────────────────────────────────────────────────

    function _isKind(string calldata kind) private pure returns (bool) {
        bytes32 k = keccak256(bytes(kind));
        return k == keccak256("app") || k == keccak256("api");
    }

    function _validName(string calldata name) private pure returns (bool) {
        bytes memory b = bytes(name);
        for (uint256 i = 0; i < b.length; i++) {
            bytes1 ch = b[i];
            bool ok =
                (ch >= 0x30 && ch <= 0x39) ||  // 0-9
                (ch >= 0x41 && ch <= 0x5A) ||  // A-Z
                (ch >= 0x61 && ch <= 0x7A) ||  // a-z
                ch == 0x2D || ch == 0x5F;      // - _
            if (!ok) return false;
        }
        return true;
    }
}
