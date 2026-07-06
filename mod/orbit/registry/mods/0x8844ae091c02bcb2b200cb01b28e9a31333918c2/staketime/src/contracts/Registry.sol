// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/security/ReentrancyGuard.sol";
import "./Mod.sol";
import "./staking/Staking.sol";

/**
 * @title Registry
 * @dev Subnet registry with bonding-curve bloctime pools.
 *      Max 420 subnets. Priority = total STT (bloctime) deposited.
 *      Anyone can deposit STT from any registered subnet's Staking
 *      contract to boost a subnet. Early backers get more shares
 *      via a linear bonding curve.
 *
 *      Pool ratio (totalSTT / subnetTokenSupply) gives the price
 *      of bloctime relative to the subnet token.
 */
contract Registry is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ── Types ────────────────────────────────────────────────────────────

    struct SubnetInfo {
        uint256 id;
        address owner;
        string name;
        address subnet;      // Subnet ERC20 token
        address staking;   // Staking contract (IS the STT ERC20)
        address consensus;   // Consensus module
        uint256 registeredBlock;
        bool active;
    }

    // ── Events ───────────────────────────────────────────────────────────

    event SubnetRegistered(uint256 indexed id, address indexed owner, string name, address subnet, address staking, address consensus);
    event SubnetDeregistered(uint256 indexed id, address indexed replacedBy);
    event ImmunityPeriodUpdated(uint256 newPeriod);
    event RegistrationCostUpdated(uint256 newCost);
    event Boosted(uint256 indexed subnetId, address indexed user, address sttToken, uint256 sttAmount, uint256 sharesReceived);
    event BoostSold(uint256 indexed subnetId, address indexed user, address sttToken, uint256 sttReturned, uint256 sharesSold);
    event CurveSlopeUpdated(uint256 newSlope);

    // ── Constants & State ────────────────────────────────────────────────

    uint256 public constant MAX_SUBNETS = 420;

    IERC20 public governanceToken;
    uint256 public registrationCost;
    uint256 public immunityPeriod;
    uint256 public nextSubnetId;

    mapping(uint256 => SubnetInfo) public subnets;
    mapping(uint256 => uint256) public lockedStake;
    uint256[] public subnetIds;
    mapping(address => uint256[]) internal _ownerSubnets;

    // ── Bonding curve pool ───────────────────────────────────────────���───

    uint256 public curveSlope;  // price = curveSlope * totalShares / 1e18

    mapping(uint256 => uint256) public subnetTotalShares;
    mapping(uint256 => uint256) public subnetBloctime;
    mapping(uint256 => mapping(address => uint256)) public userShares;
    mapping(uint256 => mapping(address => uint256)) public sttReserves;

    // ── Constructor ───────────────────────────────────────────────��──────

    constructor(uint256 _immunityPeriod, address _governanceToken, uint256 _registrationCost) {
        require(_governanceToken != address(0), "zero governanceToken");
        immunityPeriod = _immunityPeriod;
        governanceToken = IERC20(_governanceToken);
        registrationCost = _registrationCost;
        curveSlope = 1e12;
    }

    // ── Registration ─────────────────────────────────────────────────────

    function registerSubnet(
        string calldata name,
        address subnet,
        address staking,
        address consensus
    ) external returns (uint256 subnetId) {
        require(subnet != address(0), "zero subnet");
        require(staking != address(0), "zero staking");
        require(consensus != address(0), "zero consensus");

        if (registrationCost > 0) {
            governanceToken.safeTransferFrom(msg.sender, address(this), registrationCost);
        }

        if (_activeCount() >= MAX_SUBNETS) {
            (uint256 weakId, bool found) = _findWeakest();
            require(found, "all subnets immune");
            _deregister(weakId, msg.sender);
        }

        subnetId = nextSubnetId++;
        subnets[subnetId] = SubnetInfo({
            id: subnetId,
            owner: msg.sender,
            name: name,
            subnet: subnet,
            staking: staking,
            consensus: consensus,
            registeredBlock: block.number,
            active: true
        });
        lockedStake[subnetId] = registrationCost;
        subnetIds.push(subnetId);
        _ownerSubnets[msg.sender].push(subnetId);

        emit SubnetRegistered(subnetId, msg.sender, name, subnet, staking, consensus);
    }

    function deregisterSubnet(uint256 subnetId) external {
        SubnetInfo storage s = subnets[subnetId];
        require(s.active, "not active");
        require(
            msg.sender == s.owner || msg.sender == owner(),
            "not authorized"
        );
        _deregister(subnetId, address(0));
    }

    // ── Bonding Curve: Boost ─────────────────────────────────────────────

    /**
     * @dev Deposit STT (bloctime) from any registered subnet's Staking
     *      contract to boost a subnet. Shares priced on linear bonding curve.
     */
    function boostSubnet(
        uint256 subnetId,
        address sttToken,
        uint256 amount
    ) external nonReentrant {
        require(amount > 0, "zero amount");
        SubnetInfo storage s = subnets[subnetId];
        require(s.active, "subnet not active");
        require(_isValidSttToken(sttToken), "invalid STT token");

        IERC20(sttToken).safeTransferFrom(msg.sender, address(this), amount);

        uint256 shares = _calcSharesForDeposit(subnetId, amount);
        require(shares > 0, "zero shares");

        subnetTotalShares[subnetId] += shares;
        subnetBloctime[subnetId] += amount;
        userShares[subnetId][msg.sender] += shares;
        sttReserves[subnetId][sttToken] += amount;

        emit Boosted(subnetId, msg.sender, sttToken, amount, shares);
    }

    /**
     * @dev Sell shares back. Receive STT from the pool at current curve price.
     *      User specifies which STT token to withdraw (must have reserves).
     */
    function sellBoost(
        uint256 subnetId,
        uint256 shares,
        address sttToken
    ) external nonReentrant {
        require(shares > 0, "zero shares");
        require(userShares[subnetId][msg.sender] >= shares, "insufficient shares");

        uint256 sttReturn = _calcReturnForSell(subnetId, shares);
        require(sttReturn > 0, "zero return");
        require(sttReserves[subnetId][sttToken] >= sttReturn, "insufficient reserves for token");

        subnetTotalShares[subnetId] -= shares;
        subnetBloctime[subnetId] -= sttReturn;
        userShares[subnetId][msg.sender] -= shares;
        sttReserves[subnetId][sttToken] -= sttReturn;

        IERC20(sttToken).safeTransfer(msg.sender, sttReturn);

        emit BoostSold(subnetId, msg.sender, sttToken, sttReturn, shares);
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

    function _calcSharesForDeposit(uint256 subnetId, uint256 amount) internal view returns (uint256) {
        uint256 s = subnetTotalShares[subnetId];
        // n = -s + sqrt(s² + 2 * amount * 1e18 / slope)
        uint256 inner = s * s + (2 * amount * 1e18) / curveSlope;
        uint256 root = _sqrt(inner);
        return root > s ? root - s : 0;
    }

    function _calcReturnForSell(uint256 subnetId, uint256 shares) internal view returns (uint256) {
        uint256 s = subnetTotalShares[subnetId];
        require(shares <= s, "shares exceed supply");
        // return = slope * (2*s*n - n²) / (2 * 1e18)
        return curveSlope * (2 * s * shares - shares * shares) / (2 * 1e18);
    }

    /**
     * @dev Cost in STT to buy numShares at current supply.
     */
    function getBoostPrice(uint256 subnetId, uint256 numShares) external view returns (uint256) {
        uint256 s = subnetTotalShares[subnetId];
        return curveSlope * (2 * s * numShares + numShares * numShares) / (2 * 1e18);
    }

    /**
     * @dev STT returned for selling numShares at current supply.
     */
    function getSellReturn(uint256 subnetId, uint256 numShares) external view returns (uint256) {
        uint256 s = subnetTotalShares[subnetId];
        if (numShares > s) return 0;
        return curveSlope * (2 * s * numShares - numShares * numShares) / (2 * 1e18);
    }

    /**
     * @dev Price of bloctime relative to subnet token.
     *      Returns (totalBloctime * 1e18) / subnetTokenStakedSupply.
     */
    function getBloctimePrice(uint256 subnetId) external view returns (uint256) {
        SubnetInfo storage s = subnets[subnetId];
        if (!s.active || s.staking == address(0)) return 0;
        try Staking(s.staking).totalSupply() returns (uint256 supply) {
            if (supply == 0) return 0;
            return (subnetBloctime[subnetId] * 1e18) / supply;
        } catch {
            return 0;
        }
    }

    // ── Views ────────────────────────────────────────────────────────────

    function getSubnet(uint256 subnetId) external view returns (
        uint256 id, address subnetOwner, string memory name,
        address subnet, address staking, address consensus,
        uint256 registeredBlock, bool active
    ) {
        SubnetInfo storage s = subnets[subnetId];
        return (s.id, s.owner, s.name, s.subnet, s.staking, s.consensus, s.registeredBlock, s.active);
    }

    function getSubnetCount() external view returns (uint256) {
        return _activeCount();
    }

    function getAllSubnets() external view returns (SubnetInfo[] memory result) {
        uint256 count = _activeCount();
        result = new SubnetInfo[](count);
        uint256 idx;
        for (uint256 i = 0; i < subnetIds.length; i++) {
            SubnetInfo storage s = subnets[subnetIds[i]];
            if (s.active) {
                result[idx++] = s;
            }
        }
    }

    function isImmune(uint256 subnetId) public view returns (bool) {
        SubnetInfo storage s = subnets[subnetId];
        if (!s.active) return false;
        return block.number < s.registeredBlock + immunityPeriod;
    }

    /**
     * @dev Subnet priority score = total bloctime deposited + locked GOV.
     */
    function getStakeScore(uint256 subnetId) public view returns (uint256) {
        if (!subnets[subnetId].active) return 0;
        return subnetBloctime[subnetId] + lockedStake[subnetId];
    }

    function getWeakestSubnet() external view returns (uint256 weakId, uint256 weakScore, bool found) {
        uint256 minScore = type(uint256).max;
        for (uint256 i = 0; i < subnetIds.length; i++) {
            uint256 sid = subnetIds[i];
            SubnetInfo storage s = subnets[sid];
            if (!s.active) continue;
            if (isImmune(sid)) continue;

            uint256 score = getStakeScore(sid);
            if (score < minScore) {
                minScore = score;
                weakId = sid;
                found = true;
            }
        }
        weakScore = found ? minScore : 0;
    }

    function getOwnerSubnets(address subnetOwner) external view returns (uint256[] memory) {
        return _ownerSubnets[subnetOwner];
    }

    function getLockedStake(uint256 subnetId) external view returns (uint256) {
        return lockedStake[subnetId];
    }

    function getRegistrationCost() external view returns (uint256) {
        return registrationCost;
    }

    function getUserShares(uint256 subnetId, address user) external view returns (uint256) {
        return userShares[subnetId][user];
    }

    function getPoolInfo(uint256 subnetId) external view returns (
        uint256 totalShares,
        uint256 totalBloctime,
        uint256 currentPrice,
        uint256 locked
    ) {
        totalShares = subnetTotalShares[subnetId];
        totalBloctime = subnetBloctime[subnetId];
        currentPrice = curveSlope * totalShares / 1e18;
        locked = lockedStake[subnetId];
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
        for (uint256 i = 0; i < subnetIds.length; i++) {
            SubnetInfo storage s = subnets[subnetIds[i]];
            if (s.active && s.staking == sttToken) return true;
        }
        return false;
    }

    function _activeCount() internal view returns (uint256 count) {
        for (uint256 i = 0; i < subnetIds.length; i++) {
            if (subnets[subnetIds[i]].active) count++;
        }
    }

    function _findWeakest() internal view returns (uint256 weakId, bool found) {
        uint256 minScore = type(uint256).max;
        for (uint256 i = 0; i < subnetIds.length; i++) {
            uint256 sid = subnetIds[i];
            SubnetInfo storage s = subnets[sid];
            if (!s.active) continue;
            if (isImmune(sid)) continue;

            uint256 score = getStakeScore(sid);
            if (score < minScore) {
                minScore = score;
                weakId = sid;
                found = true;
            }
        }
    }

    function _deregister(uint256 subnetId, address replacedBy) internal {
        SubnetInfo storage s = subnets[subnetId];
        s.active = false;

        uint256 locked = lockedStake[subnetId];
        if (locked > 0) {
            lockedStake[subnetId] = 0;
            governanceToken.safeTransfer(s.owner, locked);
        }

        emit SubnetDeregistered(subnetId, replacedBy);
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
