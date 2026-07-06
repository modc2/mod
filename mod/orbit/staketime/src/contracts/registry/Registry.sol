// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "../mod/Mod.sol";
import "../staking/Staking.sol";

/**
 * @title Registry
 * @dev Mod registry with bonding-curve bloctime pools.
 *      Max 420 mods. Priority = total STT (bloctime) deposited.
 *      Anyone can deposit STT from any registered mod's Staking
 *      contract to boost a mod. Early backers get more shares
 *      via a linear bonding curve.
 *
 *      Pool ratio (totalSTT / modTokenSupply) gives the price
 *      of bloctime relative to the mod token.
 */
contract Registry is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ── Types ────────────────────────────────────────────────────────────

    struct ModInfo {
        uint256 id;
        address owner;
        string name;
        address mod_token;   // Mod ERC20 token
        address staking;     // Staking contract (IS the STT ERC20)
        address consensus;   // Consensus module
        uint256 registeredBlock;
        bool active;
    }

    // ── Events ───────────────────────────────────────────────────────────

    event ModRegistered(uint256 indexed id, address indexed owner, string name, address mod_token, address staking, address consensus);
    event ModDeregistered(uint256 indexed id, address indexed replacedBy);
    event ImmunityPeriodUpdated(uint256 newPeriod);
    event RegistrationCostUpdated(uint256 newCost);
    event Boosted(uint256 indexed modId, address indexed user, address sttToken, uint256 sttAmount, uint256 sharesReceived);
    event BoostSold(uint256 indexed modId, address indexed user, address sttToken, uint256 sttReturned, uint256 sharesSold);
    event CurveSlopeUpdated(uint256 newSlope);

    // ── Constants & State ────────────────────────────────────────────────

    uint256 public constant MAX_MODS = 420;

    IERC20 public governanceToken;
    uint256 public registrationCost;
    uint256 public immunityPeriod;
    uint256 public nextModId;

    mapping(uint256 => ModInfo) public mods;
    mapping(uint256 => uint256) public lockedStake;
    uint256[] public modIds;
    mapping(address => uint256[]) internal _ownerMods;

    // ── Bonding curve pool ───────────────────────────────────────────────

    uint256 public curveSlope;  // price = curveSlope * totalShares / 1e18

    mapping(uint256 => uint256) public modTotalShares;
    mapping(uint256 => uint256) public modBloctime;
    mapping(uint256 => mapping(address => uint256)) public userShares;
    mapping(uint256 => mapping(address => uint256)) public sttReserves;

    // ── Constructor ──────────────────────────────────────────────────────

    constructor(uint256 _immunityPeriod, address _governanceToken, uint256 _registrationCost) {
        require(_governanceToken != address(0), "zero governanceToken");
        immunityPeriod = _immunityPeriod;
        governanceToken = IERC20(_governanceToken);
        registrationCost = _registrationCost;
        curveSlope = 1e12;
    }

    // ── Registration ─────────────────────────────────────────────────────

    function registerMod(
        string calldata name,
        address mod_token,
        address staking,
        address consensus
    ) external returns (uint256 modId) {
        require(mod_token != address(0), "zero mod_token");
        require(staking != address(0), "zero staking");
        require(consensus != address(0), "zero consensus");

        if (registrationCost > 0) {
            governanceToken.safeTransferFrom(msg.sender, address(this), registrationCost);
        }

        if (_activeCount() >= MAX_MODS) {
            (uint256 weakId, bool found) = _findWeakest();
            require(found, "all mods immune");
            _deregister(weakId, msg.sender);
        }

        modId = nextModId++;
        mods[modId] = ModInfo({
            id: modId,
            owner: msg.sender,
            name: name,
            mod_token: mod_token,
            staking: staking,
            consensus: consensus,
            registeredBlock: block.number,
            active: true
        });
        lockedStake[modId] = registrationCost;
        modIds.push(modId);
        _ownerMods[msg.sender].push(modId);

        emit ModRegistered(modId, msg.sender, name, mod_token, staking, consensus);
    }

    function deregisterMod(uint256 modId) external {
        ModInfo storage m = mods[modId];
        require(m.active, "not active");
        require(
            msg.sender == m.owner || msg.sender == owner(),
            "not authorized"
        );
        _deregister(modId, address(0));
    }

    // ── Bonding Curve: Boost ─────────────────────────────────────────────

    /**
     * @dev Deposit STT (bloctime) from any registered mod's Staking
     *      contract to boost a mod. Shares priced on linear bonding curve.
     */
    function boostMod(
        uint256 modId,
        address sttToken,
        uint256 amount
    ) external nonReentrant {
        require(amount > 0, "zero amount");
        ModInfo storage m = mods[modId];
        require(m.active, "mod not active");
        require(_isValidSttToken(sttToken), "invalid STT token");

        IERC20(sttToken).safeTransferFrom(msg.sender, address(this), amount);

        uint256 shares = _calcSharesForDeposit(modId, amount);
        require(shares > 0, "zero shares");

        modTotalShares[modId] += shares;
        modBloctime[modId] += amount;
        userShares[modId][msg.sender] += shares;
        sttReserves[modId][sttToken] += amount;

        emit Boosted(modId, msg.sender, sttToken, amount, shares);
    }

    /**
     * @dev Sell shares back. Receive STT from the pool at current curve price.
     *      User specifies which STT token to withdraw (must have reserves).
     */
    function sellBoost(
        uint256 modId,
        uint256 shares,
        address sttToken
    ) external nonReentrant {
        require(shares > 0, "zero shares");
        require(userShares[modId][msg.sender] >= shares, "insufficient shares");

        uint256 sttReturn = _calcReturnForSell(modId, shares);
        require(sttReturn > 0, "zero return");
        require(sttReserves[modId][sttToken] >= sttReturn, "insufficient reserves for token");

        modTotalShares[modId] -= shares;
        modBloctime[modId] -= sttReturn;
        userShares[modId][msg.sender] -= shares;
        sttReserves[modId][sttToken] -= sttReturn;

        IERC20(sttToken).safeTransfer(msg.sender, sttReturn);

        emit BoostSold(modId, msg.sender, sttToken, sttReturn, shares);
    }

    // ── Bonding Curve Math ───────────────────────────────────────────────
    //
    //  Linear curve: price(s) = slope * s / 1e18
    //
    //  Buy cost for n shares at supply s:
    //    integral s to s+n = slope * (2*s*n + n²) / (2 * 1e18)
    //
    //  Sell return for n shares at supply s:
    //    integral s-n to s = slope * (2*s*n - n²) / (2 * 1e18)
    //
    //  Given a deposit amount, solve for n (shares):
    //    amount = slope * (2*s*n + n²) / (2 * 1e18)
    //    n² + 2*s*n - 2*amount*1e18/slope = 0
    //    n = -s + sqrt(s² + 2*amount*1e18/slope)
    //

    function _calcSharesForDeposit(uint256 modId, uint256 amount) internal view returns (uint256) {
        uint256 s = modTotalShares[modId];
        // n = -s + sqrt(s² + 2 * amount * 1e18 / slope)
        uint256 inner = s * s + (2 * amount * 1e18) / curveSlope;
        uint256 root = _sqrt(inner);
        return root > s ? root - s : 0;
    }

    function _calcReturnForSell(uint256 modId, uint256 shares) internal view returns (uint256) {
        uint256 s = modTotalShares[modId];
        require(shares <= s, "shares exceed supply");
        // return = slope * (2*s*n - n²) / (2 * 1e18)
        return curveSlope * (2 * s * shares - shares * shares) / (2 * 1e18);
    }

    /**
     * @dev Cost in STT to buy numShares at current supply.
     */
    function getBoostPrice(uint256 modId, uint256 numShares) external view returns (uint256) {
        uint256 s = modTotalShares[modId];
        return curveSlope * (2 * s * numShares + numShares * numShares) / (2 * 1e18);
    }

    /**
     * @dev STT returned for selling numShares at current supply.
     */
    function getSellReturn(uint256 modId, uint256 numShares) external view returns (uint256) {
        uint256 s = modTotalShares[modId];
        if (numShares > s) return 0;
        return curveSlope * (2 * s * numShares - numShares * numShares) / (2 * 1e18);
    }

    /**
     * @dev Price of bloctime relative to mod token.
     *      Returns (totalBloctime * 1e18) / modTokenStakedSupply.
     */
    function getBloctimePrice(uint256 modId) external view returns (uint256) {
        ModInfo storage m = mods[modId];
        if (!m.active || m.staking == address(0)) return 0;
        try Staking(m.staking).totalSupply() returns (uint256 supply) {
            if (supply == 0) return 0;
            return (modBloctime[modId] * 1e18) / supply;
        } catch {
            return 0;
        }
    }

    // ── Views ────────────────────────────────────────────────────────────

    function getMod(uint256 modId) external view returns (
        uint256 id, address modOwner, string memory name,
        address mod_token, address staking, address consensus,
        uint256 registeredBlock, bool active
    ) {
        ModInfo storage m = mods[modId];
        return (m.id, m.owner, m.name, m.mod_token, m.staking, m.consensus, m.registeredBlock, m.active);
    }

    function getModCount() external view returns (uint256) {
        return _activeCount();
    }

    function getAllMods() external view returns (ModInfo[] memory result) {
        uint256 count = _activeCount();
        result = new ModInfo[](count);
        uint256 idx;
        for (uint256 i = 0; i < modIds.length; i++) {
            ModInfo storage m = mods[modIds[i]];
            if (m.active) {
                result[idx++] = m;
            }
        }
    }

    function isImmune(uint256 modId) public view returns (bool) {
        ModInfo storage m = mods[modId];
        if (!m.active) return false;
        return block.number < m.registeredBlock + immunityPeriod;
    }

    /**
     * @dev Mod priority score = total bloctime deposited + locked GOV.
     */
    function getStakeScore(uint256 modId) public view returns (uint256) {
        if (!mods[modId].active) return 0;
        return modBloctime[modId] + lockedStake[modId];
    }

    function getWeakestMod() external view returns (uint256 weakId, uint256 weakScore, bool found) {
        uint256 minScore = type(uint256).max;
        for (uint256 i = 0; i < modIds.length; i++) {
            uint256 mid = modIds[i];
            ModInfo storage m = mods[mid];
            if (!m.active) continue;
            if (isImmune(mid)) continue;

            uint256 score = getStakeScore(mid);
            if (score < minScore) {
                minScore = score;
                weakId = mid;
                found = true;
            }
        }
        weakScore = found ? minScore : 0;
    }

    function getOwnerMods(address modOwner) external view returns (uint256[] memory) {
        return _ownerMods[modOwner];
    }

    function getLockedStake(uint256 modId) external view returns (uint256) {
        return lockedStake[modId];
    }

    function getRegistrationCost() external view returns (uint256) {
        return registrationCost;
    }

    function getUserShares(uint256 modId, address user) external view returns (uint256) {
        return userShares[modId][user];
    }

    function getPoolInfo(uint256 modId) external view returns (
        uint256 totalShares,
        uint256 totalBloctime,
        uint256 currentPrice,
        uint256 locked
    ) {
        totalShares = modTotalShares[modId];
        totalBloctime = modBloctime[modId];
        currentPrice = curveSlope * totalShares / 1e18;
        locked = lockedStake[modId];
    }

    // ── Admin ────────────────────────────────────────────────────────────

    function setImmunityPeriod(uint256 _period) external onlyOwner {
        immunityPeriod = _period;
        emit ImmunityPeriodUpdated(_period);
    }

    function setRegistrationCost(uint256 _cost) external onlyOwner {
        registrationCost = _cost;
        emit RegistrationCostUpdated(_cost);
    }

    function setCurveSlope(uint256 _slope) external onlyOwner {
        require(_slope > 0, "zero slope");
        curveSlope = _slope;
        emit CurveSlopeUpdated(_slope);
    }

    // ── Internal ─────────────────────────────────────────────────────────

    function _isValidSttToken(address sttToken) internal view returns (bool) {
        for (uint256 i = 0; i < modIds.length; i++) {
            ModInfo storage m = mods[modIds[i]];
            if (m.active && m.staking == sttToken) return true;
        }
        return false;
    }

    function _activeCount() internal view returns (uint256 count) {
        for (uint256 i = 0; i < modIds.length; i++) {
            if (mods[modIds[i]].active) count++;
        }
    }

    function _findWeakest() internal view returns (uint256 weakId, bool found) {
        uint256 minScore = type(uint256).max;
        for (uint256 i = 0; i < modIds.length; i++) {
            uint256 mid = modIds[i];
            ModInfo storage m = mods[mid];
            if (!m.active) continue;
            if (isImmune(mid)) continue;

            uint256 score = getStakeScore(mid);
            if (score < minScore) {
                minScore = score;
                weakId = mid;
                found = true;
            }
        }
    }

    function _deregister(uint256 modId, address replacedBy) internal {
        ModInfo storage m = mods[modId];
        m.active = false;

        uint256 locked = lockedStake[modId];
        if (locked > 0) {
            lockedStake[modId] = 0;
            governanceToken.safeTransfer(m.owner, locked);
        }

        emit ModDeregistered(modId, replacedBy);
    }

    /// @dev Integer square root (Babylonian method).
    function _sqrt(uint256 x) internal pure returns (uint256 y) {
        if (x == 0) return 0;
        y = x;
        uint256 z = (x + 1) / 2;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }
}
