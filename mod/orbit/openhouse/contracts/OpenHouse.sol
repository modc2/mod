// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title OpenHouse — rent-to-own, on-chain
/// @notice Renters pay monthly. The protocol takes 0–5% (owner-set, hard-capped in
///         code — and zero is inside the band) and everything else stays with the
///         property: a share is credited to the renter as PRINCIPAL toward the home,
///         the rest is the owner's rent income. Compare an Airbnb-style 14–16% take.
///         Pay 100% of the price → own the house outright.
///
///         What the fee does collect is not kept. It pools in this contract and every
///         quarter the pool is split by BLOCTIME — dollars x seconds of liquidity
///         locked in the protocol. A dollar locked for the whole quarter earns twice
///         what a dollar locked for half of it. Renters earn on the principal they
///         have paid in; the owner earns on the part of the house nobody has bought
///         out yet. The two always sum to the whole house, so the quarter's total
///         weight is exactly homePrice x elapsed, and the fee flows back to whoever
///         actually left money in the deal.
///
///         Governance is a 2-of-2 between the owner and the BANK — a co-signing
///         seat (an institution, or a Safe). Every lever moves by proposal: one
///         seat opens it, the other approves it, either one can cancel it, and
///         either one can freeze the contract alone. A stolen key can pause the
///         money — it cannot move it.
contract OpenHouse {
    // ─────────────────────────────────────────── The home ──
    string  public description;          // the property
    uint256 public immutable homePrice;  // principal required to own outright (wei)
    address public owner;                // current legal owner / asset provider
    address public yieldVault;           // lowfi vault principal is routed to
    address public treasury;             // where unclaimed pool dust is swept, nothing else

    // ─────────────────────────────────────────── The bank ──
    /// Every lever that can move the deal — where principal is routed, where dust
    /// is swept, who holds the owner seat, what the terms are — is behind a 2-of-2:
    /// one seat proposes, the OTHER seat approves, and either seat can cancel a
    /// pending operation or freeze the contract on the spot. A single stolen key
    /// can therefore do exactly one thing alone: pause — which protects the money,
    /// not the thief. Unpausing takes both keys, like everything else.
    address public bank;                 // the co-signer: an institution or a Safe
    bool    public paused;               // circuit breaker — blocks all fund movement

    /// A pending 2-of-2 operation. Proposals age out so an approval given today
    /// can never execute a forgotten op months later.
    uint256 public constant OP_TTL = 7 days;

    enum OpKind { SetTerms, SetTreasury, SetYieldVault, TransferOwner, SetBank, Sweep, Unpause }

    struct Op {
        OpKind  kind;
        address addr;        // target address, for the address-shaped ops
        uint256 a;           // feeBps, or the quarter index for Sweep
        uint256 b;           // creditBps
        address proposedBy;  // the seat that opened it — the other one must close it
        uint64  proposedAt;
        bool    executed;
        bool    cancelled;
    }

    Op[] public ops;

    // ────────────────────────────────── Owner-set terms ────
    /// The protocol take is bounded in code, not by promise: the owner picks a
    /// number inside this band and can never widen it. The floor is zero — an
    /// owner who wants to run the protocol at cost is allowed to, and nothing
    /// else about the deal changes when they do.
    uint256 public constant MIN_FEE_BPS = 0;     // 0% — take nothing
    uint256 public constant MAX_FEE_BPS = 500;   // 5%
    uint256 public platformFeeBps;               // protocol take, MIN..MAX
    /// Share of the post-fee payment credited to the renter as principal. This is
    /// the rent-to-own model dial: 10000 = every net dollar buys the house,
    /// 2500 = a classic lease-option rent credit, 0 = a plain lease.
    uint256 public rentCreditBps;

    // ───────────────────────────────────── Rent-to-own ─────
    uint256 public totalPrincipalPaid;                 // across all renters
    uint256 public totalRentPaid;                      // gross, across all renters
    uint256 public totalFees;                          // pooled by the protocol, lifetime
    uint256 public totalOwnerIncome;                   // rent kept by the owner
    mapping(address => uint256) public principalPaid;  // per renter
    mapping(address => uint256) public rentPaid;       // per renter, gross
    address[] public renters;
    mapping(address => bool) private _known;

    // ─────────────────────── Quarterly bloctime pool ───────
    uint256 public constant QUARTER = 90 days;
    /// Quarters a claim stays open before the owner may sweep what is left to the
    /// treasury. Four quarters — a full year to come and collect.
    uint256 public constant CLAIM_WINDOW = 4;

    uint256 public quarter;              // index of the quarter now accruing
    uint256 public quarterStart;         // when it started (the last close, or deploy)
    uint256 public pendingPool;          // fees collected since that start

    mapping(uint256 => uint256) public quarterPool;     // q → wei to split
    mapping(uint256 => uint256) public quarterEnd;      // q → close timestamp
    mapping(uint256 => uint256) public quarterWeight;   // q → total bloctime, final at close
    mapping(uint256 => uint256) public quarterClaimed;  // q → wei paid out so far
    mapping(uint256 => bool)    public swept;           // q → dust sent to the treasury
    mapping(uint256 => mapping(address => uint256)) public weightOf;  // q → who → bloctime
    mapping(uint256 => mapping(address => bool))    public claimed;   // q → who → paid

    /// Where an account's accrual was last settled to. Bloctime is banked lazily —
    /// on payment, on claim — so nothing has to loop over every renter.
    struct Accrual { uint256 lastTs; uint256 lastQuarter; }
    mapping(address => Accrual) private _acc;
    uint256 private _totalTs;        // the same two fields for totalPrincipalPaid,
    uint256 private _totalQuarter;   // settled eagerly so quarterWeight is exact

    // ─────────────────────────────────────────── Events ────
    event RentPaid(
        address indexed renter,
        uint256 amount,
        uint256 fee,
        uint256 credit,
        uint256 ownerIncome,
        uint256 principalToDate
    );
    event TermsSet(uint256 platformFeeBps, uint256 rentCreditBps);
    event TreasurySet(address indexed treasury);
    event FundsRoutedToYield(address indexed vault, uint256 amount);
    event Redistributed(uint256 indexed quarter, uint256 totalPrincipal, uint256 timestamp);
    event QuarterClosed(uint256 indexed quarter, uint256 pool, uint256 totalWeight, uint256 endedAt);
    event PoolClaimed(uint256 indexed quarter, address indexed who, uint256 weight, uint256 amount);
    event PoolSwept(uint256 indexed quarter, uint256 amount);
    event HomeFullyOwned(uint256 timestamp);
    event YieldVaultSet(address indexed vault);
    event OwnerTransferred(address indexed from, address indexed to);
    event BankSet(address indexed bank);
    event OpProposed(uint256 indexed id, OpKind kind, address indexed by);
    event OpCancelled(uint256 indexed id, address indexed by);
    event OpExecuted(uint256 indexed id, OpKind kind, address indexed approvedBy);
    event PausedBy(address indexed by);
    event Unpaused();

    /// Owner or bank — the two seats of the 2-of-2.
    modifier onlySeat() {
        require(msg.sender == owner || msg.sender == bank, "OpenHouse: not a seat");
        _;
    }

    modifier notPaused() {
        require(!paused, "OpenHouse: paused");
        _;
    }

    constructor(
        string memory _description,
        uint256 _homePrice,
        address _yieldVault,
        address _treasury,
        address _bank,
        uint256 _platformFeeBps,
        uint256 _rentCreditBps
    ) {
        require(_homePrice > 0, "OpenHouse: zero price");
        require(_treasury != address(0), "OpenHouse: zero treasury");
        require(_bank != address(0), "OpenHouse: zero bank");
        require(_bank != msg.sender, "OpenHouse: bank is the owner");
        description = _description;
        homePrice = _homePrice;
        owner = msg.sender;
        yieldVault = _yieldVault;
        treasury = _treasury;
        bank = _bank;
        _setTerms(_platformFeeBps, _rentCreditBps);
        quarterStart = block.timestamp;
        _totalTs = block.timestamp;
    }

    // ─────────────────────────────────────── Pay rent ──────

    /// @notice Pay rent. The protocol takes `platformFeeBps` — possibly nothing; of
    ///         what's left, `rentCreditBps` is credited to you as principal toward
    ///         the home and the remainder is the owner's rent income. Principal is
    ///         routed to the owner's low-risk yield vault (lowfi) while it sits, and
    ///         it starts earning bloctime here the moment it lands.
    function payRent() external payable notPaused {
        require(msg.value > 0, "OpenHouse: no payment");
        require(totalPrincipalPaid < homePrice, "OpenHouse: home already paid off");

        (uint256 fee, uint256 credit, uint256 ownerIncome) = quoteRent(msg.value);

        // Bank the bloctime earned on the old balances before they change.
        _accrue(msg.sender);
        _accrueTotal();

        _track(msg.sender);
        principalPaid[msg.sender] += credit;
        rentPaid[msg.sender] += msg.value;
        totalPrincipalPaid += credit;
        totalRentPaid += msg.value;
        totalFees += fee;
        totalOwnerIncome += ownerIncome;

        emit RentPaid(msg.sender, msg.value, fee, credit, ownerIncome, principalPaid[msg.sender]);

        // Principal sits in lowfi yield and rent income settles now. The fee stays
        // here, in the quarter's pool — it is owed back, not taken.
        if (credit > 0) {
            address sink = yieldVault != address(0) ? yieldVault : owner;
            _send(sink, credit);
            if (sink == yieldVault) emit FundsRoutedToYield(yieldVault, credit);
        }
        if (ownerIncome > 0) _send(owner, ownerIncome);
        pendingPool += fee;

        if (totalPrincipalPaid == homePrice) emit HomeFullyOwned(block.timestamp);
    }

    /// @notice Split a payment the way `payRent` would, without paying.
    /// @return fee protocol take, credit principal toward the home, ownerIncome the owner's rent
    function quoteRent(uint256 amount)
        public view
        returns (uint256 fee, uint256 credit, uint256 ownerIncome)
    {
        fee = (amount * platformFeeBps) / 10_000;
        uint256 net = amount - fee;
        credit = (net * rentCreditBps) / 10_000;
        // Never credit past the price — the overflow is rent, not equity.
        uint256 room = homePrice - totalPrincipalPaid;
        if (credit > room) credit = room;
        ownerIncome = net - credit;
    }

    // ─────────────────────────── Quarterly redistribution ──

    /// @notice Close the quarter: freeze the pool and the bloctime that earned it,
    ///         and checkpoint ownership from principal paid off. Permissionless once
    ///         the 90 days are up — the numbers are already fixed by then, and only
    ///         the calling costs anything. Payouts are pull-based: see `claim`.
    function closeQuarter() external {
        require(block.timestamp >= quarterStart + QUARTER, "OpenHouse: quarter not elapsed");
        uint256 q = quarter;

        _accrueTotal();   // renter bloctime for q, exact and now final

        // Everything the renters haven't bought out is still the owner's stake, and
        // it was locked here all quarter. Renter weight + owner weight is therefore
        // exactly homePrice x elapsed, with no per-renter loop to get there.
        uint256 span = block.timestamp - quarterStart;
        uint256 ownerWeight = (homePrice * span) - quarterWeight[q];
        weightOf[q][owner] += ownerWeight;
        quarterWeight[q] += ownerWeight;

        quarterEnd[q] = block.timestamp;
        quarterPool[q] = pendingPool;
        pendingPool = 0;

        quarter = q + 1;
        quarterStart = block.timestamp;

        emit QuarterClosed(q, quarterPool[q], quarterWeight[q], block.timestamp);
        emit Redistributed(q, totalPrincipalPaid, block.timestamp);
    }

    /// @notice Claim your share of a closed quarter's pool: the fee back, in
    ///         proportion to the dollars x seconds you had locked that quarter.
    function claim(uint256 q) external notPaused returns (uint256 amount) {
        require(q < quarter, "OpenHouse: quarter still open");
        require(!swept[q], "OpenHouse: quarter swept");
        require(!claimed[q][msg.sender], "OpenHouse: already claimed");

        _accrue(msg.sender);   // settle a renter who hasn't touched the contract since

        uint256 weight = weightOf[q][msg.sender];
        require(weight > 0, "OpenHouse: no bloctime that quarter");

        claimed[q][msg.sender] = true;
        amount = (quarterPool[q] * weight) / quarterWeight[q];
        quarterClaimed[q] += amount;
        emit PoolClaimed(q, msg.sender, weight, amount);
        if (amount > 0) _send(msg.sender, amount);
    }

    /// @dev After a full year unclaimed, what's left of a quarter's pool (plus the
    ///      wei of rounding dust every division leaves) goes to the treasury. A
    ///      sweep moves money, so it is a 2-of-2 operation: propose(Sweep, q).
    function _sweepUnclaimed(uint256 q) internal returns (uint256 amount) {
        require(quarter > q + CLAIM_WINDOW, "OpenHouse: claim window still open");
        require(!swept[q], "OpenHouse: already swept");
        swept[q] = true;
        amount = quarterPool[q] - quarterClaimed[q];
        emit PoolSwept(q, amount);
        if (amount > 0) _send(treasury, amount);
    }

    // ─────────────────────────────────────────── Views ─────

    /// @notice Bloctime an account has earned in the quarter now accruing,
    ///         including the stretch not yet banked.
    function currentWeightOf(address who) public view returns (uint256 weight) {
        weight = weightOf[quarter][who];
        Accrual storage a = _acc[who];
        if (a.lastTs == 0) return weight;
        // Settled in an earlier quarter → the whole of this one is still owed.
        uint256 from = a.lastQuarter < quarter ? quarterStart : a.lastTs;
        if (block.timestamp > from) weight += principalPaid[who] * (block.timestamp - from);
    }

    /// @notice Total bloctime this quarter. Renter principal and the owner's
    ///         remaining stake are the whole house between them, always.
    function currentTotalWeight() public view returns (uint256) {
        return homePrice * (block.timestamp - quarterStart);
    }

    /// @notice The owner's bloctime this quarter: the part of the house nobody has
    ///         bought out yet, integrated over the time it stayed that way.
    function currentOwnerWeight() public view returns (uint256) {
        uint256 renterWeight = currentTotalRenterWeight();
        uint256 total = currentTotalWeight();
        return total > renterWeight ? total - renterWeight : 0;
    }

    function currentTotalRenterWeight() public view returns (uint256 weight) {
        weight = quarterWeight[quarter];
        uint256 from = _totalQuarter < quarter ? quarterStart : _totalTs;
        if (block.timestamp > from) weight += totalPrincipalPaid * (block.timestamp - from);
    }

    /// @notice What an account would be paid if the quarter closed on this block.
    function projectedPayout(address who) external view returns (uint256) {
        uint256 total = currentTotalWeight();
        if (total == 0) return 0;
        uint256 weight = who == owner
            ? currentWeightOf(who) + currentOwnerWeight()
            : currentWeightOf(who);
        return (pendingPool * weight) / total;
    }

    /// @notice What a closed quarter still owes an account.
    function claimable(uint256 q, address who) external view returns (uint256) {
        if (q >= quarter || swept[q] || claimed[q][who] || quarterWeight[q] == 0) return 0;
        return (quarterPool[q] * weightOf[q][who]) / quarterWeight[q];
    }

    /// @notice A renter's equity in basis points (10000 = 100% of the home).
    function equityBps(address renter) public view returns (uint256) {
        return (principalPaid[renter] * 10_000) / homePrice;
    }

    /// @notice Share of the home owned by renters so far, in basis points.
    function ownedBps() external view returns (uint256) {
        return (totalPrincipalPaid * 10_000) / homePrice;
    }

    /// @notice Principal still owed before the home is fully owned.
    function remainingPrincipal() external view returns (uint256) {
        return homePrice - totalPrincipalPaid;
    }

    /// @notice Share of all rent ever paid that stayed with the property
    ///         (renter equity + owner income), in basis points. The pooled fee is
    ///         owed back to the people who locked liquidity, so at 0% this is 100%.
    function toPropertyBps() external view returns (uint256) {
        if (totalRentPaid == 0) return 10_000 - platformFeeBps;
        return ((totalRentPaid - totalFees) * 10_000) / totalRentPaid;
    }

    function renterCount() external view returns (uint256) { return renters.length; }
    function fullyOwned() external view returns (bool) { return totalPrincipalPaid == homePrice; }
    function quarterReady() external view returns (bool) { return block.timestamp >= quarterStart + QUARTER; }
    function quarterEndsAt() external view returns (uint256) { return quarterStart + QUARTER; }

    // ──────────────────────────── Governance: the 2-of-2 ───

    /// @notice Open a 2-of-2 operation. The proposer's signature is this call; the
    ///         OTHER seat's `approve` executes it. Arguments are checked here so a
    ///         doomed proposal fails at the door, and checked again at execution so
    ///         a week of drift can't make a stale one land wrong.
    /// @param kind what to do — see OpKind
    /// @param addr the new treasury / vault / owner / bank, for address-shaped ops
    /// @param a    feeBps for SetTerms, the quarter index for Sweep
    /// @param b    creditBps for SetTerms
    function propose(OpKind kind, address addr, uint256 a, uint256 b)
        external onlySeat returns (uint256 id)
    {
        if (kind == OpKind.SetTerms) {
            require(a <= MAX_FEE_BPS, "OpenHouse: fee out of band");
            require(b <= 10_000, "OpenHouse: credit > 100%");
        } else if (kind == OpKind.SetTreasury || kind == OpKind.TransferOwner || kind == OpKind.SetBank) {
            require(addr != address(0), "OpenHouse: zero address");
            // The two seats must never collapse into one key. Checked again at
            // execution — a seat may have rotated while the proposal sat open.
            if (kind == OpKind.TransferOwner) require(addr != bank, "OpenHouse: owner would be the bank");
            if (kind == OpKind.SetBank) require(addr != owner, "OpenHouse: bank would be the owner");
        } else if (kind == OpKind.Unpause) {
            require(paused, "OpenHouse: not paused");
        }
        id = ops.length;
        ops.push(Op({
            kind: kind,
            addr: addr,
            a: a,
            b: b,
            proposedBy: msg.sender,
            proposedAt: uint64(block.timestamp),
            executed: false,
            cancelled: false
        }));
        emit OpProposed(id, kind, msg.sender);
    }

    /// @notice The second signature. Must come from the seat that did NOT propose,
    ///         inside the TTL — and executes the operation in the same breath.
    function approve(uint256 id) external onlySeat {
        Op storage op = _openOp(id);
        require(msg.sender != op.proposedBy, "OpenHouse: cannot self-approve");
        require(block.timestamp <= op.proposedAt + OP_TTL, "OpenHouse: proposal expired");
        op.executed = true;
        _execute(op);
        emit OpExecuted(id, op.kind, msg.sender);
    }

    /// @notice The undo. Either seat kills a pending operation — its own proposal
    ///         on second thought, or the other seat's on first sight.
    function cancel(uint256 id) external onlySeat {
        Op storage op = _openOp(id);
        op.cancelled = true;
        emit OpCancelled(id, msg.sender);
    }

    /// @notice The brake. One seat, alone, right now — a stolen key racing to move
    ///         money loses to the honest key freezing it. Unpausing is an Unpause
    ///         proposal, so nothing thaws until both seats agree the keys are safe.
    function pause() external onlySeat {
        paused = true;
        emit PausedBy(msg.sender);
    }

    function opCount() external view returns (uint256) { return ops.length; }

    function _openOp(uint256 id) internal view returns (Op storage op) {
        require(id < ops.length, "OpenHouse: no such op");
        op = ops[id];
        require(!op.executed, "OpenHouse: already executed");
        require(!op.cancelled, "OpenHouse: cancelled");
    }

    function _execute(Op storage op) internal {
        if (op.kind == OpKind.SetTerms) {
            _setTerms(op.a, op.b);
        } else if (op.kind == OpKind.SetTreasury) {
            treasury = op.addr;
            emit TreasurySet(op.addr);
        } else if (op.kind == OpKind.SetYieldVault) {
            yieldVault = op.addr;
            emit YieldVaultSet(op.addr);
        } else if (op.kind == OpKind.TransferOwner) {
            // The owner's bloctime is credited to whoever holds the seat at close,
            // so settle the current holder's accrual before the seat moves.
            require(op.addr != bank, "OpenHouse: owner would be the bank");
            _accrue(owner);
            _accrue(op.addr);
            emit OwnerTransferred(owner, op.addr);
            owner = op.addr;
        } else if (op.kind == OpKind.SetBank) {
            require(op.addr != owner, "OpenHouse: bank would be the owner");
            bank = op.addr;
            emit BankSet(op.addr);
        } else if (op.kind == OpKind.Sweep) {
            require(!paused, "OpenHouse: paused");
            _sweepUnclaimed(op.a);
        } else if (op.kind == OpKind.Unpause) {
            paused = false;
            emit Unpaused();
        }
    }

    // ─────────────────────────────────────────── Internal ──

    function _setTerms(uint256 feeBps, uint256 creditBps) internal {
        // MIN_FEE_BPS is 0, so only the ceiling needs checking — the floor is
        // documented in the constant and enforced by the type.
        require(feeBps <= MAX_FEE_BPS, "OpenHouse: fee out of band");
        require(creditBps <= 10_000, "OpenHouse: credit > 100%");
        platformFeeBps = feeBps;
        rentCreditBps = creditBps;
        emit TermsSet(feeBps, creditBps);
    }

    /// @dev Bank one account's bloctime up to now, splitting the stretch across any
    ///      quarter boundaries it crossed. Called before any balance change, so the
    ///      current balance is the right one for every interval it walks.
    function _accrue(address who) internal {
        Accrual storage a = _acc[who];
        if (a.lastTs == 0) {
            a.lastTs = block.timestamp;
            a.lastQuarter = quarter;
            return;
        }
        uint256 locked = principalPaid[who];
        uint256 ts = a.lastTs;
        uint256 q = a.lastQuarter;
        while (q < quarter) {
            uint256 end = quarterEnd[q];
            if (end > ts) {
                weightOf[q][who] += locked * (end - ts);
                ts = end;
            }
            unchecked { ++q; }
        }
        if (block.timestamp > ts) weightOf[quarter][who] += locked * (block.timestamp - ts);
        a.lastTs = block.timestamp;
        a.lastQuarter = quarter;
    }

    /// @dev The same walk for the renter total. Settled on every payment and at
    ///      every close, so `quarterWeight[q]` is exact the moment q closes — which
    ///      is what lets a late claimer's share be computed against a fixed
    ///      denominator instead of a moving one.
    function _accrueTotal() internal {
        uint256 locked = totalPrincipalPaid;
        uint256 ts = _totalTs;
        uint256 q = _totalQuarter;
        while (q < quarter) {
            uint256 end = quarterEnd[q];
            if (end > ts) {
                quarterWeight[q] += locked * (end - ts);
                ts = end;
            }
            unchecked { ++q; }
        }
        if (block.timestamp > ts) quarterWeight[quarter] += locked * (block.timestamp - ts);
        _totalTs = block.timestamp;
        _totalQuarter = quarter;
    }

    function _send(address to, uint256 amount) internal {
        (bool ok, ) = to.call{value: amount}("");
        require(ok, "OpenHouse: transfer failed");
    }

    function _track(address who) internal {
        if (!_known[who]) { _known[who] = true; renters.push(who); }
    }
}
