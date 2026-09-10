// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title SelfInsure — a member-owned mutual, on-chain, with nothing hidden.
/// @notice A premium becomes pool money and stays pool money. There is no house
///         account: what is left after claims is owed back to the people who paid
///         it in, pro rata, and `distribute` is how it goes back. An operator fee
///         exists because some pools want to pay for their own admin — it defaults
///         to zero, is hard-capped at 10% in code, can only be RAISED after a
///         seven-day public notice, and every unit of it is published by
///         `transparency()` as `operatorShareBps`: the provider's profit, as a
///         number anyone can read off the chain.
///
///         Claims are adjudicated by registered agents (AI or human — each says
///         which) who vote with a written reason the claimant can read. An
///         OPTIONAL oracle brings real-world data in: a signed hospital bill, a
///         verified procedure price, a parametric trigger. It can be advisory
///         (recorded beside the votes), required (a claim cannot settle without
///         it, and its verified amount caps the payout), or automatic (the
///         attestation alone settles the claim — parametric cover).
///
///         A claim the pool cannot fund is not quietly reduced: it is accepted,
///         recorded as UNFUNDED for the amount still owed, and paid oldest-first
///         from the next premiums in. `distribute` refuses while anything is owed.
///
///         Money is native ETH or any ERC-20 (a stablecoin is the sane choice for
///         a health mutual). Amounts are the asset's smallest unit throughout.
contract SelfInsure {
    // ───────────────────────────────────────────── constants ──
    /// A mutual that keeps 10% of every premium is not a mutual. This is the
    /// contract's one hard opinion, and it cannot be changed after deployment.
    uint16  public constant MAX_FEE_BPS = 1000;
    /// Raising the fee takes effect this long after it is announced on chain, so
    /// no member ever pays a fee they had no chance to see coming.
    uint256 public constant FEE_NOTICE = 7 days;
    uint256 public constant YEAR = 365 days;
    uint256 private constant ACC = 1e18;

    enum OracleMode { None, Advisory, Required, Automatic }
    enum ClaimState { Open, Accepted, Rejected, Withdrawn }

    // ───────────────────────────────────────────── the pool ───
    struct Terms {
        uint256 premium;        // per period, per member (0 = donation-funded pool)
        uint256 period;         // seconds one premium covers
        uint256 coverage;       // max payout per claim (0 = uncapped)
        uint256 deductible;     // the first N of any claim the member bears
        uint256 annualCap;      // max paid to one member per policy year (0 = none)
        uint256 waitingPeriod;  // seconds after joining before a claim may be filed
        uint256 reserveFloor;   // never distributed as surplus
        uint16  feeBps;         // operator take of each premium, 0..MAX_FEE_BPS
        uint16  quorum;         // agent votes needed before a claim settles
        uint16  thresholdBps;   // share of those votes that must accept
        bool    approvedAgentsOnly;
    }

    struct Config {
        string  name;
        string  about;          // what is and is not covered — agents judge against it
        address asset;          // address(0) = native ETH, else an ERC-20
        address owner;          // the operator; can set terms, never touch the pot
        address oracle;         // optional ISelfInsureOracle
        OracleMode oracleMode;
        Terms   terms;
    }

    string  public name;
    string  public about;
    address public asset;
    address public owner;
    address public oracle;
    OracleMode public oracleMode;
    Terms   public terms;
    bool    public closed;                  // closed = no new members
    uint64  public createdAt;
    bool    public initialized;

    // A fee raise is announced first and applied after FEE_NOTICE.
    uint16  public pendingFeeBps;
    uint64  public pendingFeeAt;

    // ───────────────────────────────────────────── money ──────
    uint256 public balance;             // the pot: what claims are paid from
    uint256 public premiumsIn;          // gross, lifetime
    uint256 public donationsIn;
    uint256 public feesAccrued;         // operator's cut, lifetime
    uint256 public feesWithdrawn;
    uint256 public paidOut;             // claims paid, lifetime
    uint256 public distributed;         // surplus returned to members, lifetime
    uint256 public rebatesUnclaimed;    // distributed but not yet pulled
    uint256 public openExposure;        // every open claim at its payable amount
    uint256 public unfundedOwed;        // accepted, not yet paid

    // ───────────────────────────────────────────── members ────
    struct Member {
        bool    exists;
        uint64  joinedAt;
        uint64  coveredFrom;
        uint64  paidThrough;
        uint256 contributed;    // net of fee
        uint256 received;       // claims paid
        uint256 rebated;        // surplus pulled
    }
    mapping(address => Member) public members;
    address[] public memberList;
    // paid claims per member per policy year — the annual cap without a loop
    mapping(address => mapping(uint256 => uint256)) public paidInYear;

    // Surplus is split by stake (contributed − received − rebated) with a
    // per-stake accumulator, so a distribution is O(1) and a member pulls
    // their share whenever they like.
    uint256 public totalStake;
    uint256 private _accRebatePerStake;
    mapping(address => uint256) private _rebateDebt;
    mapping(address => uint256) public rebateOwed;

    // ───────────────────────────────────────────── agents ─────
    struct Agent {
        bool    exists;
        bool    active;
        string  name;
        string  kind;           // "ai" or "human" — the claimant is entitled to know
        string  model;
        uint32  votes;
        uint32  accepts;
        uint32  withMajority;   // how often they landed where the pool settled
        uint64  registeredAt;
    }
    mapping(address => Agent) public agents;
    address[] public agentList;

    // ───────────────────────────────────────────── claims ─────
    struct Frozen {             // the terms a claim is judged under, fixed at filing
        uint256 coverage;
        uint256 deductible;
        uint256 annualCap;
        uint16  quorum;
        uint16  thresholdBps;
        address oracle;
        OracleMode oracleMode;
    }
    struct Claim {
        address member;
        uint256 amount;
        string  title;
        string  evidence;       // URI or CID of the case file
        uint64  filedAt;
        uint64  decidedAt;
        ClaimState state;
        uint256 paid;
        uint256 shortfall;
        uint16  accepts;
        uint16  rejects;
        uint256 exposure;       // what openExposure holds for this claim
        Frozen  frozen;
    }
    struct Ballot {
        address agent;
        bool    accept;
        string  reason;
        uint64  at;
    }
    uint256 public claimCount;
    mapping(uint256 => Claim) private _claims;
    mapping(uint256 => Ballot[]) private _ballots;
    mapping(uint256 => mapping(address => bool)) public voted;
    uint256[] private _unfunded;    // FIFO of accepted-but-unpaid claim ids
    uint256 private _unfundedHead;

    // ───────────────────────────────────────────── events ─────
    event Joined(address indexed member, uint256 paid, uint256 fee, uint64 coveredFrom);
    event PremiumPaid(address indexed member, uint256 paid, uint256 fee, uint256 toPool, uint64 paidThrough);
    event Donated(address indexed from, uint256 amount);
    event AgentRegistered(address indexed agent, string name, string kind, string model, bool active);
    event AgentAdmitted(address indexed agent, bool active);
    event ClaimFiled(uint256 indexed id, address indexed member, uint256 amount, uint256 payableIfAccepted, string title, string evidence);
    event Voted(uint256 indexed id, address indexed agent, bool accept, string reason);
    event OracleConsulted(uint256 indexed id, address oracle, bool ok, uint256 verifiedAmount, bytes32 dataHash);
    event ClaimAccepted(uint256 indexed id, uint256 paid, uint256 shortfall);
    event ClaimRejected(uint256 indexed id, uint16 accepts, uint16 rejects, bool byOracle);
    event ClaimWithdrawn(uint256 indexed id);
    event Payout(uint256 indexed id, address indexed member, uint256 amount, uint256 stillOwed);
    event Distributed(uint256 amount, uint256 totalStake, uint256 lifetime);
    event RebateClaimed(address indexed member, uint256 amount);
    event FeeProposed(uint16 fromBps, uint16 toBps, uint64 effectiveAt);
    event FeeChanged(uint16 fromBps, uint16 toBps);
    event FeesWithdrawn(address indexed to, uint256 amount, uint256 lifetime, uint256 operatorShareBps);
    event TermsChanged(Terms terms);
    event OracleChanged(address oracle, OracleMode mode);
    event Closed(bool closed);
    event OwnerChanged(address indexed from, address indexed to);

    // ───────────────────────────────────────────── errors ─────
    error NotOwner();
    error AlreadyInitialized();
    error NotMember();
    error AlreadyMember();
    error PoolClosed();
    error FeeAboveCap(uint16 bps, uint16 cap);
    error FeeNoticePending(uint64 effectiveAt);
    error NoFeeProposed();
    error BadTerms(string what);
    error WrongPayment(uint256 sent, uint256 wanted);
    error PremiumShort(uint256 sent, uint256 premium);
    error NothingSent();
    error NotCovered(uint64 coveredFrom);
    error NoSuchClaim();
    error ClaimNotOpen();
    error NotAgent();
    error AgentNotAdmitted();
    error AlreadyVoted();
    error OwnClaim();
    error ReasonRequired();
    error ClaimsOwed(uint256 owed);
    error NotDistributable(uint256 wanted, uint256 free);
    error NoStake();
    error NothingToClaim();
    error Reentered();
    error TransferFailed();

    // ───────────────────────────────────────────── guards ─────
    uint256 private _lock = 1;
    modifier nonReentrant() {
        if (_lock == 2) revert Reentered();
        _lock = 2;
        _;
        _lock = 1;
    }
    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    // ───────────────────────────────────────────── deploy ─────
    /// Deploy directly with a config, or deploy once with an empty name as the
    /// implementation behind SelfInsureFactory's clones (which call `initialize`).
    constructor(Config memory c) {
        if (bytes(c.name).length == 0) initialized = true;   // bare implementation: locked
        else _init(c);
    }

    function initialize(Config memory c) external {
        _init(c);
    }

    function _init(Config memory c) private {
        if (initialized) revert AlreadyInitialized();
        if (bytes(c.name).length == 0) revert BadTerms("name");
        _checkTerms(c.terms);
        initialized = true;
        _lock = 1;
        name = c.name;
        about = c.about;
        asset = c.asset;
        owner = c.owner == address(0) ? msg.sender : c.owner;
        oracle = c.oracle;
        oracleMode = c.oracle == address(0) ? OracleMode.None : c.oracleMode;
        terms = c.terms;
        createdAt = uint64(block.timestamp);
        emit TermsChanged(c.terms);
        emit OracleChanged(oracle, oracleMode);
    }

    function _checkTerms(Terms memory t) private pure {
        if (t.feeBps > MAX_FEE_BPS) revert FeeAboveCap(t.feeBps, MAX_FEE_BPS);
        if (t.quorum == 0) revert BadTerms("quorum");
        if (t.thresholdBps == 0 || t.thresholdBps > 10000) revert BadTerms("threshold");
        if (t.premium != 0 && t.period == 0) revert BadTerms("period");
    }

    // ───────────────────────────────────────────── owner ──────
    /// Everything but the fee. Never retroactive: an open claim is judged under
    /// the terms frozen when it was filed.
    function setTerms(Terms calldata t) external onlyOwner {
        if (t.feeBps != terms.feeBps) revert BadTerms("use proposeFee");
        _checkTerms(t);
        terms = t;
        emit TermsChanged(t);
    }

    /// Lowering the fee is immediate. Raising it is announced on chain and
    /// takes effect after FEE_NOTICE, and never above MAX_FEE_BPS.
    function proposeFee(uint16 bps) external onlyOwner {
        if (bps > MAX_FEE_BPS) revert FeeAboveCap(bps, MAX_FEE_BPS);
        if (bps <= terms.feeBps) {
            emit FeeChanged(terms.feeBps, bps);
            terms.feeBps = bps;
            pendingFeeBps = 0;
            pendingFeeAt = 0;
            return;
        }
        pendingFeeBps = bps;
        pendingFeeAt = uint64(block.timestamp + FEE_NOTICE);
        emit FeeProposed(terms.feeBps, bps, pendingFeeAt);
    }

    function applyFee() external {
        if (pendingFeeAt == 0) revert NoFeeProposed();
        if (block.timestamp < pendingFeeAt) revert FeeNoticePending(pendingFeeAt);
        emit FeeChanged(terms.feeBps, pendingFeeBps);
        terms.feeBps = pendingFeeBps;
        pendingFeeBps = 0;
        pendingFeeAt = 0;
    }

    function setOracle(address o, OracleMode mode) external onlyOwner {
        oracle = o;
        oracleMode = o == address(0) ? OracleMode.None : mode;
        emit OracleChanged(oracle, oracleMode);
    }

    function setClosed(bool c) external onlyOwner {
        closed = c;
        emit Closed(c);
    }

    function transferOwnership(address to) external onlyOwner {
        emit OwnerChanged(owner, to);
        owner = to;
    }

    function admitAgent(address a, bool active) external onlyOwner {
        if (!agents[a].exists) revert NotAgent();
        agents[a].active = active;
        emit AgentAdmitted(a, active);
    }

    /// The operator's cut, and only that. Pulled explicitly so it is a line in
    /// the log, with the lifetime share of premium it represents.
    function withdrawFees(address to, uint256 amount) external onlyOwner nonReentrant {
        uint256 avail = feesAccrued - feesWithdrawn;
        if (amount == 0) amount = avail;
        if (amount == 0 || amount > avail) revert NotDistributable(amount, avail);
        feesWithdrawn += amount;
        _push(to, amount);
        emit FeesWithdrawn(to, amount, feesWithdrawn, operatorShareBps());
    }

    // ───────────────────────────────────────────── money in ───
    /// Join and pay the first premium. Coverage starts after the waiting period.
    /// `amount` is in the asset's smallest unit: for ETH send it as msg.value,
    /// for an ERC-20 approve this contract first and send no ETH.
    function join(uint256 amount) external payable nonReentrant {
        if (closed) revert PoolClosed();
        if (members[msg.sender].exists) revert AlreadyMember();
        _take(amount);
        if (amount < terms.premium) revert PremiumShort(amount, terms.premium);
        Member storage m = members[msg.sender];
        m.exists = true;
        m.joinedAt = uint64(block.timestamp);
        m.coveredFrom = uint64(block.timestamp + terms.waitingPeriod);
        m.paidThrough = uint64(block.timestamp);
        memberList.push(msg.sender);
        uint256 fee = _credit(m, amount);
        emit Joined(msg.sender, amount, fee, m.coveredFrom);
    }

    /// Top up. Extends coverage by one period per premium paid, and — if the
    /// pool owes anyone an unfunded claim — pays that down first, oldest first.
    function payPremium(uint256 amount) external payable nonReentrant {
        Member storage m = members[msg.sender];
        if (!m.exists) revert NotMember();
        _take(amount);
        uint256 fee = _credit(m, amount);
        emit PremiumPaid(msg.sender, amount, fee, amount - fee, m.paidThrough);
    }

    /// Money in from someone who is not buying coverage — seed capital, a grant,
    /// a backstop. Buys no claim rights and no share of surplus. Takes no fee.
    function donate(uint256 amount) external payable nonReentrant {
        _take(amount);
        donationsIn += amount;
        balance += amount;
        emit Donated(msg.sender, amount);
        _payBacklog(16);
    }

    function _credit(Member storage m, uint256 amount) private returns (uint256 fee) {
        fee = amount * terms.feeBps / 10000;
        uint256 toPool = amount - fee;
        premiumsIn += amount;
        feesAccrued += fee;
        balance += toPool;
        _bank(msg.sender);
        m.contributed += toPool;
        _resync(msg.sender);
        if (terms.premium != 0 && amount >= terms.premium) {
            uint256 periods = amount / terms.premium;
            uint64 from = m.paidThrough > block.timestamp ? m.paidThrough : uint64(block.timestamp);
            m.paidThrough = uint64(from + periods * terms.period);
        }
        _payBacklog(16);
    }

    // ───────────────────────────────────────────── agents ─────
    /// Register as an adjudicator. Say honestly whether you are ai or human and
    /// which model — every ballot you cast carries it.
    function registerAgent(string calldata name_, string calldata kind, string calldata model) external {
        if (bytes(name_).length == 0) revert BadTerms("name");
        Agent storage a = agents[msg.sender];
        if (!a.exists) {
            a.exists = true;
            a.registeredAt = uint64(block.timestamp);
            agentList.push(msg.sender);
        }
        a.name = name_;
        a.kind = kind;
        a.model = model;
        a.active = !terms.approvedAgentsOnly;
        emit AgentRegistered(msg.sender, name_, kind, model, a.active);
    }

    // ───────────────────────────────────────────── claims ─────
    function fileClaim(uint256 amount, string calldata title, string calldata evidence)
        external returns (uint256 id)
    {
        Member storage m = members[msg.sender];
        if (!m.exists) revert NotMember();
        if (block.timestamp < m.coveredFrom) revert NotCovered(m.coveredFrom);
        if (amount == 0) revert NothingSent();
        if (bytes(title).length == 0) revert BadTerms("title");
        id = ++claimCount;
        Claim storage c = _claims[id];
        c.member = msg.sender;
        c.amount = amount;
        c.title = title;
        c.evidence = evidence;
        c.filedAt = uint64(block.timestamp);
        c.frozen = Frozen({
            coverage: terms.coverage, deductible: terms.deductible, annualCap: terms.annualCap,
            quorum: terms.quorum, thresholdBps: terms.thresholdBps,
            oracle: oracle, oracleMode: oracleMode
        });
        uint256 payable_ = _payable(c);
        c.exposure = payable_;
        openExposure += payable_;
        emit ClaimFiled(id, msg.sender, amount, payable_, title, evidence);
    }

    function withdrawClaim(uint256 id) external {
        Claim storage c = _open(id);
        if (c.member != msg.sender) revert NotMember();
        c.state = ClaimState.Withdrawn;
        c.decidedAt = uint64(block.timestamp);
        _release(c);
        emit ClaimWithdrawn(id);
    }

    /// Accept or reject, with a reason the claimant reads. When your vote
    /// completes the quorum the claim settles in this same call.
    function vote(uint256 id, bool accept, string calldata reason) external nonReentrant {
        Claim storage c = _open(id);
        Agent storage a = agents[msg.sender];
        if (!a.exists) revert NotAgent();
        if (!a.active) revert AgentNotAdmitted();
        if (voted[id][msg.sender]) revert AlreadyVoted();
        if (c.member == msg.sender) revert OwnClaim();
        if (bytes(reason).length == 0) revert ReasonRequired();
        voted[id][msg.sender] = true;
        _ballots[id].push(Ballot(msg.sender, accept, reason, uint64(block.timestamp)));
        a.votes += 1;
        if (accept) { a.accepts += 1; c.accepts += 1; } else { c.rejects += 1; }
        emit Voted(id, msg.sender, accept, reason);
        _trySettle(id, c);
    }

    /// Anyone may nudge a claim whose votes are in but which was waiting on the
    /// oracle — or, in automatic mode, one the oracle has now attested.
    function settle(uint256 id) external nonReentrant {
        _trySettle(id, _open(id));
    }

    function _trySettle(uint256 id, Claim storage c) private {
        OracleMode mode = c.frozen.oracleMode;
        bool attested; bool ok; uint256 verified; bytes32 dataHash;
        if (mode != OracleMode.None && c.frozen.oracle != address(0)) {
            (attested, ok, verified, dataHash) = _consult(c.frozen.oracle, id);
        }
        bool decideAccept;
        bool byOracle;
        uint16 n = c.accepts + c.rejects;
        if (mode == OracleMode.Automatic) {
            if (!attested) return;
            decideAccept = ok;
            byOracle = !ok;
        } else {
            if (n < c.frozen.quorum) return;
            if (mode == OracleMode.Required && !attested) return;
            decideAccept = uint256(c.accepts) * 10000 >= uint256(c.frozen.thresholdBps) * n;
            if (mode == OracleMode.Required && !ok) { decideAccept = false; byOracle = true; }
        }
        if (attested) emit OracleConsulted(id, c.frozen.oracle, ok, verified, dataHash);
        Ballot[] storage bs = _ballots[id];
        for (uint256 i = 0; i < bs.length; i++) {
            if (bs[i].accept == decideAccept) agents[bs[i].agent].withMajority += 1;
        }
        c.decidedAt = uint64(block.timestamp);
        _release(c);
        if (!decideAccept) {
            c.state = ClaimState.Rejected;
            emit ClaimRejected(id, c.accepts, c.rejects, byOracle);
            return;
        }
        c.state = ClaimState.Accepted;
        uint256 due = _payable(c);
        if (attested && (mode == OracleMode.Required || mode == OracleMode.Automatic) && verified < due) {
            due = verified;
        }
        // annual cap is applied at settlement against what was actually paid
        uint256 y = _year();
        if (c.frozen.annualCap != 0) {
            uint256 room = c.frozen.annualCap > paidInYear[c.member][y] ? c.frozen.annualCap - paidInYear[c.member][y] : 0;
            if (due > room) due = room;
        }
        c.shortfall = due;
        uint256 paid = _pay(id, c);
        emit ClaimAccepted(id, paid, c.shortfall);
    }

    function _consult(address o, uint256 id) private view returns (bool, bool, uint256, bytes32) {
        try ISelfInsureOracle(o).attestation(address(this), id) returns (bool a, bool ok, uint256 amt, bytes32 h, uint64) {
            return (a, ok, amt, h);
        } catch {
            return (false, false, 0, bytes32(0));
        }
    }

    /// Pay what the pool actually has. What it cannot pay stays on the books as
    /// `shortfall` and in the FIFO queue — never silently reduced.
    function _pay(uint256 id, Claim storage c) private returns (uint256 pay) {
        uint256 due = c.shortfall;
        pay = due < balance ? due : balance;
        if (pay != 0) {
            balance -= pay;
            paidOut += pay;
            c.paid += pay;
            c.shortfall = due - pay;
            paidInYear[c.member][_year()] += pay;
            _bank(c.member);
            members[c.member].received += pay;
            _resync(c.member);
            _push(c.member, pay);
            emit Payout(id, c.member, pay, c.shortfall);
        }
        if (c.shortfall != 0) {
            _unfunded.push(id);
            unfundedOwed += c.shortfall;
        }
    }

    /// Money arrived. Oldest unfunded claim first, bounded so a premium is never
    /// too expensive to pay; `settleBacklog` finishes the job if needed.
    function _payBacklog(uint256 maxSteps) private {
        uint256 steps;
        while (_unfundedHead < _unfunded.length && balance != 0 && steps < maxSteps) {
            uint256 id = _unfunded[_unfundedHead];
            Claim storage c = _claims[id];
            uint256 before = c.shortfall;
            uint256 pay = before < balance ? before : balance;
            if (pay != 0) {
                balance -= pay;
                paidOut += pay;
                c.paid += pay;
                c.shortfall = before - pay;
                unfundedOwed -= pay;
                paidInYear[c.member][_year()] += pay;
                _bank(c.member);
                members[c.member].received += pay;
                _resync(c.member);
                _push(c.member, pay);
                emit Payout(id, c.member, pay, c.shortfall);
            }
            if (c.shortfall == 0) _unfundedHead += 1;
            steps++;
        }
    }

    function settleBacklog(uint256 maxSteps) external nonReentrant {
        _payBacklog(maxSteps == 0 ? 32 : maxSteps);
    }

    function _open(uint256 id) private view returns (Claim storage c) {
        c = _claims[id];
        if (c.member == address(0)) revert NoSuchClaim();
        if (c.state != ClaimState.Open) revert ClaimNotOpen();
    }

    function _release(Claim storage c) private {
        openExposure -= c.exposure;
        c.exposure = 0;
    }

    /// The most a claim could ever pay under its frozen terms: deductible first,
    /// then the per-claim cap. Solvency is a different question.
    function _payable(Claim storage c) private view returns (uint256 amt) {
        amt = c.amount > c.frozen.deductible ? c.amount - c.frozen.deductible : 0;
        if (c.frozen.coverage != 0 && amt > c.frozen.coverage) amt = c.frozen.coverage;
    }

    function _year() private view returns (uint256) {
        return (block.timestamp - createdAt) / YEAR;
    }

    // ───────────────────────────────────────────── surplus ────
    /// What could be returned today: the pot, less every open claim at its full
    /// payable amount, less what is owed, less the reserve floor.
    function distributable() public view returns (uint256) {
        uint256 held = unfundedOwed + openExposure + terms.reserveFloor;
        return balance > held ? balance - held : 0;
    }

    /// Give the surplus back. Members pull their share with `claimRebate`; the
    /// split is by stake — what each paid in and has not already had back.
    function distribute(uint256 amount) external onlyOwner {
        if (unfundedOwed != 0) revert ClaimsOwed(unfundedOwed);
        uint256 free = distributable();
        if (amount == 0) amount = free;
        if (amount == 0 || amount > free) revert NotDistributable(amount, free);
        if (totalStake == 0) revert NoStake();
        balance -= amount;
        distributed += amount;
        rebatesUnclaimed += amount;
        _accRebatePerStake += amount * ACC / totalStake;
        emit Distributed(amount, totalStake, distributed);
    }

    function pendingRebate(address who) public view returns (uint256) {
        return rebateOwed[who] + (_stake(who) * _accRebatePerStake / ACC - _rebateDebt[who]);
    }

    function claimRebate() external nonReentrant {
        _bank(msg.sender);
        uint256 amount = rebateOwed[msg.sender];
        if (amount == 0) revert NothingToClaim();
        rebateOwed[msg.sender] = 0;
        rebatesUnclaimed -= amount;
        members[msg.sender].rebated += amount;
        _resync(msg.sender);
        _push(msg.sender, amount);
        emit RebateClaimed(msg.sender, amount);
    }

    function _stake(address who) private view returns (uint256) {
        Member storage m = members[who];
        uint256 out = m.received + m.rebated;
        return m.contributed > out ? m.contributed - out : 0;
    }

    function _bank(address who) private {
        uint256 s = _stake(who);
        uint256 earned = s * _accRebatePerStake / ACC;
        if (earned > _rebateDebt[who]) rebateOwed[who] += earned - _rebateDebt[who];
        totalStake -= s;
    }

    function _resync(address who) private {
        uint256 s = _stake(who);
        _rebateDebt[who] = s * _accRebatePerStake / ACC;
        totalStake += s;
    }

    // ───────────────────────────────────────────── transfers ──
    function _take(uint256 amount) private {
        if (amount == 0) revert NothingSent();
        if (asset == address(0)) {
            if (msg.value != amount) revert WrongPayment(msg.value, amount);
            return;
        }
        if (msg.value != 0) revert WrongPayment(msg.value, 0);
        _call(abi.encodeWithSelector(IERC20.transferFrom.selector, msg.sender, address(this), amount));
    }

    function _push(address to, uint256 amount) private {
        if (asset == address(0)) {
            (bool ok, ) = to.call{value: amount}("");
            if (!ok) revert TransferFailed();
        } else {
            _call(abi.encodeWithSelector(IERC20.transfer.selector, to, amount));
        }
    }

    function _call(bytes memory data) private {
        (bool ok, bytes memory ret) = asset.call(data);
        if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
    }

    // ───────────────────────────────────────────── views ──────
    struct Transparency {
        uint256 premiumsIn;         // every premium ever paid, gross
        uint256 donationsIn;
        uint256 feesAccrued;        // the operator's cut, lifetime — THE PROVIDER'S PROFIT
        uint256 feesWithdrawn;
        uint256 paidOut;            // claims paid to members
        uint256 distributed;        // surplus returned to members
        uint256 rebatesUnclaimed;
        uint256 balance;            // the pot
        uint256 held;               // what the contract actually holds on chain
        uint256 openExposure;
        uint256 unfundedOwed;
        uint256 reserveFloor;
        uint256 distributable;
        uint256 lossRatioBps;       // claims paid / net premium
        uint256 operatorShareBps;   // fees / gross premium
        uint256 memberShareBps;     // 10000 − operatorShareBps
        uint16  feeBps;
        uint16  pendingFeeBps;
        uint64  pendingFeeAt;
        bool    reconciles;         // held ≥ balance + fees owed + rebates unclaimed
        bool    solvent;            // nothing owed and the pot covers every open claim
        uint256 members;
        uint256 agents;
        uint256 claims;
    }

    /// Everything, in one call, for anyone. The number a health insurer will
    /// never publish is `operatorShareBps`.
    function transparency() external view returns (Transparency memory t) {
        uint256 held = asset == address(0) ? address(this).balance : IERC20(asset).balanceOf(address(this));
        uint256 netIn = premiumsIn - feesAccrued + donationsIn;
        t.premiumsIn = premiumsIn;
        t.donationsIn = donationsIn;
        t.feesAccrued = feesAccrued;
        t.feesWithdrawn = feesWithdrawn;
        t.paidOut = paidOut;
        t.distributed = distributed;
        t.rebatesUnclaimed = rebatesUnclaimed;
        t.balance = balance;
        t.held = held;
        t.openExposure = openExposure;
        t.unfundedOwed = unfundedOwed;
        t.reserveFloor = terms.reserveFloor;
        t.distributable = distributable();
        t.lossRatioBps = netIn == 0 ? 0 : paidOut * 10000 / netIn;
        t.operatorShareBps = operatorShareBps();
        t.memberShareBps = 10000 - t.operatorShareBps;
        t.feeBps = terms.feeBps;
        t.pendingFeeBps = pendingFeeBps;
        t.pendingFeeAt = pendingFeeAt;
        t.reconciles = held >= balance + (feesAccrued - feesWithdrawn) + rebatesUnclaimed;
        t.solvent = unfundedOwed == 0 && balance >= openExposure;
        t.members = memberList.length;
        t.agents = agentList.length;
        t.claims = claimCount;
    }

    /// Of every unit of premium ever paid, how many basis points the operator kept.
    function operatorShareBps() public view returns (uint256) {
        return premiumsIn == 0 ? 0 : feesAccrued * 10000 / premiumsIn;
    }

    function claim(uint256 id) external view returns (Claim memory) {
        if (_claims[id].member == address(0)) revert NoSuchClaim();
        return _claims[id];
    }

    function ballots(uint256 id) external view returns (Ballot[] memory) {
        return _ballots[id];
    }

    function unfundedQueue() external view returns (uint256[] memory ids) {
        uint256 n = _unfunded.length - _unfundedHead;
        ids = new uint256[](n);
        for (uint256 i = 0; i < n; i++) ids[i] = _unfunded[_unfundedHead + i];
    }

    function memberCount() external view returns (uint256) { return memberList.length; }
    function agentCount() external view returns (uint256) { return agentList.length; }

    function stakeOf(address who) external view returns (uint256) { return _stake(who); }

    function isCovered(address who) external view returns (bool) {
        Member storage m = members[who];
        if (!m.exists || block.timestamp < m.coveredFrom) return false;
        return terms.premium == 0 || block.timestamp <= m.paidThrough;
    }

    /// The oracle's word on a claim, if it has one.
    function oracleView(uint256 id) external view returns (bool attested, bool ok, uint256 verified, bytes32 dataHash) {
        Claim storage c = _claims[id];
        if (c.member == address(0)) revert NoSuchClaim();
        if (c.frozen.oracle == address(0)) return (false, false, 0, bytes32(0));
        return _consult(c.frozen.oracle, id);
    }

    receive() external payable {
        // Plain ETH sent here is a donation.
        if (msg.value == 0) revert NothingSent();
        donationsIn += msg.value;
        balance += msg.value;
        emit Donated(msg.sender, msg.value);
    }
}

/// @notice What a real-data source has to answer. Keyed by (pool, claim) so one
///         oracle contract can serve many pools. `verifiedAmount` is what the
///         data says the loss was worth — a hospital's itemised bill, a
///         reference price for a CPT code, a parametric payout table.
interface ISelfInsureOracle {
    function attestation(address pool, uint256 claimId)
        external view
        returns (bool attested, bool ok, uint256 verifiedAmount, bytes32 dataHash, uint64 at);
}

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address who) external view returns (uint256);
}
