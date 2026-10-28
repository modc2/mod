// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

interface IBlocTimePower {
    function getVotingPower(address account) external view returns (uint256);
}

interface IMintable {
    function mint(address to, uint256 amount) external;
}

/**
 * @title YumaBlocTime
 * @dev Yuma consensus where a validator's stake is its BlocTime.
 *
 *      STAKE. A validator's stake is `bloctime.getVotingPower(v)`: the BLOC it
 *      holds plus every BLOC delegated to it. BLOC is minted as dollars x
 *      seconds locked, so the voice a validator has here is the time-weighted
 *      capital behind it — a long commitment outweighs a large one.
 *
 *      WEIGHTS. Each validator scores miners with `setWeights(uids, vals)`.
 *      Rows are normalised to ONE at epoch time.
 *
 *      EPOCH (permissionless, once per `tempo` seconds):
 *        s_i   = S_i / ΣS                                 normalised BlocTime
 *        W_ij  = w_ij / Σ_j w_ij                          normalised weights
 *        C_j   = max c : Σ_{i: W_ij >= c} s_i >= kappa    stake-weighted quantile
 *        Wc_ij = min(W_ij, C_j)                           clip outliers to consensus
 *        R_j   = Σ_i s_i Wc_ij ;  I_j = R_j / ΣR          miner incentive
 *        ΔB_ij = s_i Wc_ij / Σ_k s_k Wc_kj                bond share in miner j
 *        B_ij  = alpha ΔB_ij + (1 - alpha) B_ij           bonds (EMA)
 *        D_i   = Σ_j B_ij I_j ; normalised                validator dividends
 *      then the epoch's emission is split: ownerCut to the subnet owner, the
 *      rest minerShare by I and the remainder by D. Rounding dust goes to the
 *      owner so every minted token is accounted for.
 *
 *      Payouts accrue to `pending` and are pulled with `claim()`. In mint mode
 *      the emission is minted here each epoch (the reward token must name this
 *      contract as minter — e.g. a staketime `Mod`); otherwise the contract
 *      pays out of whatever has been funded to it.
 *
 *      All maths is integer, ONE = 1e18, and is reproduced bit-for-bit by
 *      bloctime/yuma/yuma.py.
 *
 *      Weights set before a miner took its uid are masked, so a recycled uid
 *      never inherits the old miner's score. Stake is read at epoch time:
 *      BLOC moved between validators right before an epoch moves the vote
 *      with it — the total cannot be inflated, only relocated.
 */
contract YumaBlocTime is ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    uint256 public constant ONE = 1e18;
    uint256 public constant BPS = 10000;
    uint256 public constant HARD_MAX_VALIDATORS = 64;
    uint256 public constant HARD_MAX_MINERS = 256;

    IBlocTimePower public immutable bloctime;
    IERC20 public immutable token;
    bool public immutable mintMode;

    struct Params {
        uint64 tempo;              // seconds between epochs
        uint256 emissionPerEpoch;  // reward tokens per epoch
        uint256 kappa;             // consensus quantile, ONE-scaled (0.5e18)
        uint256 bondAlpha;         // EMA rate for new bonds, ONE-scaled (0.1e18)
        uint256 ownerCutBps;       // to the subnet owner, off the top
        uint256 minerShareBps;     // of the remainder, to miners by incentive
        uint256 minValidatorBloc;  // voting power needed to validate
        uint16 maxValidators;
        uint16 maxMiners;
    }

    Params public params;

    address[] public validators;
    address[] public miners;
    mapping(address => uint256) public validatorUid1; // uid + 1, 0 = none
    mapping(address => uint256) public minerUid1;
    mapping(uint256 => uint64) public minerRegisteredEpoch; // epoch the uid was taken (immunity)
    mapping(uint256 => uint64) public minerRegisteredSeq;   // seq the uid was taken (weight masking)
    uint64 public seq;                                      // bumps on every registration / weight write

    mapping(uint256 => uint256[]) internal _weights;      // validator uid → raw weight row
    mapping(uint256 => uint64) public weightsSetSeq;      // seq the row was written
    mapping(uint256 => mapping(uint256 => uint256)) public bonds; // [v][m], ONE-scaled

    mapping(address => uint256) public pending;
    uint256 public totalPending;
    uint256 public totalEmitted;

    uint64 public epochCount;
    uint64 public lastEpochTime;

    // last epoch's outputs, for UIs
    mapping(uint256 => uint256) public lastIncentive;  // miner uid → ONE-scaled
    mapping(uint256 => uint256) public lastConsensus;  // miner uid → ONE-scaled
    mapping(uint256 => uint256) public lastDividend;   // validator uid → ONE-scaled
    mapping(uint256 => uint256) public lastStake;      // validator uid → BLOC used

    event ValidatorRegistered(address indexed who, uint256 uid, address replaced);
    event MinerRegistered(address indexed who, uint256 uid, address replaced);
    event WeightsSet(address indexed validator, uint256 uid, uint256 count);
    event Epoch(uint64 indexed epoch, uint256 emission, uint256 totalStake, uint256 toMiners, uint256 toValidators, uint256 toOwner);
    event Claimed(address indexed who, uint256 amount);
    event Funded(address indexed from, uint256 amount);
    event ParamsUpdated();

    constructor(address _bloctime, address _token, bool _mintMode, Params memory _p) Ownable(msg.sender) {
        require(_bloctime != address(0) && _token != address(0), "zero address");
        bloctime = IBlocTimePower(_bloctime);
        token = IERC20(_token);
        mintMode = _mintMode;
        _setParams(_p);
        lastEpochTime = uint64(block.timestamp);
    }

    // ── Admin ───────────────────────────────────────────────────

    function setParams(Params calldata _p) external onlyOwner {
        require(_p.maxValidators >= validators.length && _p.maxMiners >= miners.length, "below current size");
        _setParams(_p);
    }

    function _setParams(Params memory _p) internal {
        require(_p.tempo > 0, "tempo");
        require(_p.kappa > 0 && _p.kappa <= ONE, "kappa");
        require(_p.bondAlpha > 0 && _p.bondAlpha <= ONE, "alpha");
        require(_p.ownerCutBps <= BPS && _p.minerShareBps <= BPS, "bps");
        require(_p.maxValidators > 0 && _p.maxValidators <= HARD_MAX_VALIDATORS, "maxValidators");
        require(_p.maxMiners > 0 && _p.maxMiners <= HARD_MAX_MINERS, "maxMiners");
        params = _p;
        emit ParamsUpdated();
    }

    // ── Registration ────────────────────────────────────────────

    /// @dev Needs `minValidatorBloc` of BlocTime. When the set is full the
    ///      newcomer replaces the validator with the least BlocTime, and only
    ///      if it has more.
    function registerValidator() external returns (uint256 uid) {
        require(validatorUid1[msg.sender] == 0, "already validator");
        uint256 power = bloctime.getVotingPower(msg.sender);
        require(power >= params.minValidatorBloc && power > 0, "not enough BlocTime");

        address replaced;
        if (validators.length < params.maxValidators) {
            uid = validators.length;
            validators.push(msg.sender);
        } else {
            uint256 low = type(uint256).max;
            for (uint256 i = 0; i < validators.length; i++) {
                uint256 p = bloctime.getVotingPower(validators[i]);
                if (p < low) { low = p; uid = i; }
            }
            require(power > low, "set full: need more BlocTime than the weakest");
            replaced = validators[uid];
            validatorUid1[replaced] = 0;
            validators[uid] = msg.sender;
            for (uint256 j = 0; j < miners.length; j++) bonds[uid][j] = 0;
            delete _weights[uid];
            lastDividend[uid] = 0;
        }
        validatorUid1[msg.sender] = uid + 1;
        emit ValidatorRegistered(msg.sender, uid, replaced);
    }

    /// @dev Free. When full the newcomer takes the uid of the miner with the
    ///      lowest last incentive that has been through at least one epoch.
    function registerMiner() external returns (uint256 uid) {
        require(minerUid1[msg.sender] == 0, "already miner");
        address replaced;
        if (miners.length < params.maxMiners) {
            uid = miners.length;
            miners.push(msg.sender);
        } else {
            uint256 low = type(uint256).max;
            bool found;
            for (uint256 j = 0; j < miners.length; j++) {
                if (minerRegisteredEpoch[j] >= epochCount) continue; // immune until it has been scored
                if (lastIncentive[j] < low) { low = lastIncentive[j]; uid = j; found = true; }
            }
            require(found, "set full: everyone is immune");
            replaced = miners[uid];
            minerUid1[replaced] = 0;
            miners[uid] = msg.sender;
            for (uint256 i = 0; i < validators.length; i++) bonds[i][uid] = 0;
            lastIncentive[uid] = 0;
            lastConsensus[uid] = 0;
        }
        minerUid1[msg.sender] = uid + 1;
        minerRegisteredEpoch[uid] = epochCount;
        minerRegisteredSeq[uid] = ++seq;
        emit MinerRegistered(msg.sender, uid, replaced);
    }

    // ── Weights ─────────────────────────────────────────────────

    function setWeights(uint256[] calldata uids, uint256[] calldata vals) external {
        uint256 v1 = validatorUid1[msg.sender];
        require(v1 != 0, "not validator");
        require(uids.length == vals.length, "length mismatch");
        uint256 v = v1 - 1;
        uint256[] storage row = _weights[v];
        delete _weights[v];
        for (uint256 j = 0; j < miners.length; j++) row.push(0);
        for (uint256 k = 0; k < uids.length; k++) {
            require(uids[k] < miners.length, "bad uid");
            row[uids[k]] = vals[k];
        }
        weightsSetSeq[v] = ++seq;
        emit WeightsSet(msg.sender, v, uids.length);
    }

    // ── Funding (non-mint mode) ─────────────────────────────────

    function fund(uint256 amount) external {
        require(!mintMode, "mint mode");
        token.safeTransferFrom(msg.sender, address(this), amount);
        emit Funded(msg.sender, amount);
    }

    function available() public view returns (uint256) {
        if (mintMode) return type(uint256).max;
        uint256 bal = token.balanceOf(address(this));
        return bal > totalPending ? bal - totalPending : 0;
    }

    // ── Epoch ───────────────────────────────────────────────────

    function nextEpochTime() public view returns (uint256) {
        return uint256(lastEpochTime) + params.tempo;
    }

    function epochDue() public view returns (bool) {
        return block.timestamp >= nextEpochTime();
    }

    function epoch() external nonReentrant {
        require(epochDue(), "epoch not due");
        uint256 V = validators.length;
        uint256 M = miners.length;

        uint256[] memory S = new uint256[](V);
        uint256[][] memory W = new uint256[][](V);
        uint256[][] memory B = new uint256[][](V);
        for (uint256 i = 0; i < V; i++) {
            W[i] = _maskedRow(i, M);
            uint256 rowSum;
            for (uint256 j = 0; j < M; j++) rowSum += W[i][j];
            if (rowSum > 0) {
                uint256 p = bloctime.getVotingPower(validators[i]);
                if (p >= params.minValidatorBloc) S[i] = p;
            }
            B[i] = new uint256[](M);
            for (uint256 j = 0; j < M; j++) B[i][j] = bonds[i][j];
        }

        (uint256[] memory I, uint256[] memory D, uint256[] memory C, uint256 totalS) =
            _yuma(S, W, B, params.kappa, params.bondAlpha);

        for (uint256 i = 0; i < V; i++) {
            lastStake[i] = S[i];
            lastDividend[i] = D[i];
            for (uint256 j = 0; j < M; j++) bonds[i][j] = B[i][j];
        }
        for (uint256 j = 0; j < M; j++) { lastIncentive[j] = I[j]; lastConsensus[j] = C[j]; }

        uint256 emission = params.emissionPerEpoch;
        uint256 avail = available();
        if (emission > avail) emission = avail;
        (uint256 toMiners, uint256 toValidators, uint256 toOwner) = _pay(emission, I, D);

        epochCount++;
        lastEpochTime = uint64(block.timestamp);
        emit Epoch(epochCount, toMiners + toValidators + toOwner, totalS, toMiners, toValidators, toOwner);
    }

    function _maskedRow(uint256 i, uint256 M) internal view returns (uint256[] memory row) {
        row = new uint256[](M);
        uint256[] storage w = _weights[i];
        uint64 setAt = weightsSetSeq[i];
        for (uint256 j = 0; j < M && j < w.length; j++) {
            // a weight written before this miner took the uid scored someone else
            if (minerRegisteredSeq[j] > setAt) continue;
            row[j] = w[j];
        }
    }

    function _pay(uint256 emission, uint256[] memory I, uint256[] memory D)
        internal returns (uint256 toMiners, uint256 toValidators, uint256 toOwner)
    {
        if (emission == 0) return (0, 0, 0);
        uint256 incSum; uint256 divSum;
        for (uint256 j = 0; j < I.length; j++) incSum += I[j];
        for (uint256 i = 0; i < D.length; i++) divSum += D[i];
        if (incSum == 0 && divSum == 0) return (0, 0, 0); // nobody earned — nothing is emitted

        uint256 ownerAmt = emission * params.ownerCutBps / BPS;
        uint256 rest = emission - ownerAmt;
        uint256 minerPool = rest * params.minerShareBps / BPS;
        uint256 valPool = rest - minerPool;

        for (uint256 j = 0; j < I.length; j++) {
            uint256 a = minerPool * I[j] / ONE;
            if (a > 0) { pending[miners[j]] += a; toMiners += a; }
        }
        for (uint256 i = 0; i < D.length; i++) {
            uint256 a = valPool * D[i] / ONE;
            if (a > 0) { pending[validators[i]] += a; toValidators += a; }
        }
        toOwner = emission - toMiners - toValidators; // owner cut + rounding dust
        pending[owner()] += toOwner;

        totalPending += emission;
        totalEmitted += emission;
        if (mintMode) IMintable(address(token)).mint(address(this), emission);
    }

    /// @dev Pure Yuma. Mirrors yuma.py `yuma()` exactly. B is updated in place.
    function _yuma(
        uint256[] memory S,
        uint256[][] memory W,
        uint256[][] memory B,
        uint256 kappa,
        uint256 alpha
    ) internal pure returns (uint256[] memory I, uint256[] memory D, uint256[] memory C, uint256 totalS) {
        uint256 V = S.length;
        uint256 M = V == 0 ? 0 : W[0].length;
        I = new uint256[](M);
        D = new uint256[](V);
        C = new uint256[](M);

        for (uint256 i = 0; i < V; i++) totalS += S[i];
        if (totalS == 0 || M == 0) return (I, D, C, totalS);

        uint256[] memory s = _normalise(S, W, totalS);
        C = _consensus(W, s, kappa);
        uint256[] memory R = _ranksAndBonds(W, s, C, B, alpha);

        uint256 rSum;
        for (uint256 j = 0; j < M; j++) rSum += R[j];
        if (rSum == 0) return (I, D, C, totalS);
        for (uint256 j = 0; j < M; j++) I[j] = R[j] * ONE / rSum;

        D = _dividends(B, I);
    }

    /// @dev s_i = S_i / ΣS; rows of W normalised to ONE (zero-stake rows zeroed).
    function _normalise(uint256[] memory S, uint256[][] memory W, uint256 totalS)
        internal pure returns (uint256[] memory s)
    {
        uint256 V = S.length;
        uint256 M = W[0].length;
        s = new uint256[](V);
        for (uint256 i = 0; i < V; i++) {
            s[i] = S[i] * ONE / totalS;
            if (S[i] == 0) {                            // no voice: its row counts for nothing
                for (uint256 j = 0; j < M; j++) W[i][j] = 0;
                continue;
            }
            uint256 rowSum;
            for (uint256 j = 0; j < M; j++) rowSum += W[i][j];
            for (uint256 j = 0; j < M; j++) W[i][j] = W[i][j] * ONE / rowSum;
        }
    }

    /// @dev C_j: walk validators by W_ij high→low (ties: lower uid first) and
    ///      take the weight at which accumulated stake first reaches kappa.
    function _consensus(uint256[][] memory W, uint256[] memory s, uint256 kappa)
        internal pure returns (uint256[] memory C)
    {
        uint256 V = s.length;
        uint256 M = W[0].length;
        C = new uint256[](M);
        uint256[] memory order = new uint256[](V);
        for (uint256 j = 0; j < M; j++) {
            for (uint256 i = 0; i < V; i++) order[i] = i;
            for (uint256 a = 1; a < V; a++) {
                uint256 x = order[a];
                uint256 b = a;
                while (b > 0 && W[order[b - 1]][j] < W[x][j]) { order[b] = order[b - 1]; b--; }
                order[b] = x;
            }
            uint256 cum;
            for (uint256 k = 0; k < V; k++) {
                cum += s[order[k]];
                if (cum >= kappa) { C[j] = W[order[k]][j]; break; }
            }
        }
    }

    /// @dev Clip to consensus, rank miners, and fold this epoch's bond shares
    ///      into B by EMA. Returns R (unnormalised ranks).
    function _ranksAndBonds(
        uint256[][] memory W,
        uint256[] memory s,
        uint256[] memory C,
        uint256[][] memory B,
        uint256 alpha
    ) internal pure returns (uint256[] memory R) {
        uint256 V = s.length;
        uint256 M = C.length;
        R = new uint256[](M);
        uint256[] memory col = new uint256[](V);
        for (uint256 j = 0; j < M; j++) {
            uint256 colSum;
            for (uint256 i = 0; i < V; i++) {
                uint256 wc = W[i][j] < C[j] ? W[i][j] : C[j];
                col[i] = s[i] * wc / ONE;
                colSum += col[i];
            }
            R[j] = colSum;
            for (uint256 i = 0; i < V; i++) {
                uint256 db = colSum == 0 ? 0 : col[i] * ONE / colSum;
                B[i][j] = (alpha * db + (ONE - alpha) * B[i][j]) / ONE;
            }
        }
    }

    /// @dev D_i = Σ_j B_ij I_j, normalised to ONE (all zero if nobody bonded).
    function _dividends(uint256[][] memory B, uint256[] memory I) internal pure returns (uint256[] memory D) {
        uint256 V = B.length;
        uint256 M = I.length;
        D = new uint256[](V);
        uint256 dSum;
        for (uint256 i = 0; i < V; i++) {
            uint256 d;
            for (uint256 j = 0; j < M; j++) d += B[i][j] * I[j] / ONE;
            D[i] = d;
            dSum += d;
        }
        if (dSum == 0) return D;
        for (uint256 i = 0; i < V; i++) D[i] = D[i] * ONE / dSum;
    }

    // ── Claims ──────────────────────────────────────────────────

    function claim() external nonReentrant {
        uint256 amount = pending[msg.sender];
        require(amount > 0, "nothing to claim");
        pending[msg.sender] = 0;
        totalPending -= amount;
        token.safeTransfer(msg.sender, amount);
        emit Claimed(msg.sender, amount);
    }

    // ── Views ───────────────────────────────────────────────────

    function validatorCount() external view returns (uint256) { return validators.length; }
    function minerCount() external view returns (uint256) { return miners.length; }
    function getValidators() external view returns (address[] memory) { return validators; }
    function getMiners() external view returns (address[] memory) { return miners; }
    function getWeights(uint256 validatorUid) external view returns (uint256[] memory) { return _weights[validatorUid]; }

    function getBonds(uint256 validatorUid) external view returns (uint256[] memory row) {
        row = new uint256[](miners.length);
        for (uint256 j = 0; j < miners.length; j++) row[j] = bonds[validatorUid][j];
    }

    /// @dev What an epoch run now would produce, without writing anything.
    function preview() external view returns (
        uint256[] memory stake, uint256[] memory incentive, uint256[] memory dividends, uint256[] memory consensus_
    ) {
        uint256 V = validators.length;
        uint256 M = miners.length;
        stake = new uint256[](V);
        uint256[][] memory W = new uint256[][](V);
        uint256[][] memory B = new uint256[][](V);
        for (uint256 i = 0; i < V; i++) {
            W[i] = _maskedRow(i, M);
            uint256 rowSum;
            for (uint256 j = 0; j < M; j++) rowSum += W[i][j];
            if (rowSum > 0) {
                uint256 p = bloctime.getVotingPower(validators[i]);
                if (p >= params.minValidatorBloc) stake[i] = p;
            }
            B[i] = new uint256[](M);
            for (uint256 j = 0; j < M; j++) B[i][j] = bonds[i][j];
        }
        uint256[] memory S = new uint256[](V);
        for (uint256 i = 0; i < V; i++) S[i] = stake[i];
        (incentive, dividends, consensus_, ) = _yuma(S, W, B, params.kappa, params.bondAlpha);
    }
}
