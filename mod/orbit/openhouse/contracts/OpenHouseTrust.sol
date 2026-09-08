// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IMortgageOracle} from "./MortgageOracle.sol";

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address who) external view returns (uint256);
    function decimals() external view returns (uint8);
}

/// @title OpenHouseTrust — one house, one mortgage, many owners
/// @notice A tokenized single-property trust. Any set of individuals can put
///         USD against the same mortgage; each of them holds a share token, and
///         the share token is nothing but a receipt for dollars that reached
///         the loan. Equity is pro-rata by definition, not by policy: shares
///         are minted at a fixed rate against the money the servicer confirms
///         it posted, so `balanceOf(you) / totalSupply()` IS your fraction of
///         the house, and no function anywhere can move that ratio except
///         somebody else paying more or you paying less.
///
///         THE BANK CONTROLS THIS CONTRACT. It is the lender: it fronted the
///         principal, it carries the loan on its books, and the deed answers to
///         it. So it holds every operational lever here — it appoints the
///         oracle, admits the members, names the account payments are wired to,
///         can freeze the contract on its own, can call the default and can
///         foreclose. Control and liability are the same seat, deliberately.
///         There is no co-signer, no timelock and no member vote standing
///         between the bank and its collateral.
///
///         What the bank cannot do is take a member's stake, because the
///         functions to do it were never written. There is no burn. There is no
///         seize. Escrowed contributions leave this contract by exactly two
///         doors — to the servicer, or back to the member who sent them. The
///         bank's own money in ranks senior and is repaid in cash, never
///         converted to shares, so members are never diluted by the rescue.
///
///         INTEREST BUYS NOBODY A HOUSE. Set `basis = Principal` and only the
///         amortized principal in a payment mints equity; the interest is what
///         the bank's liquidity costs, and it is priced as a cost. `basis =
///         Contribution` is the softer deal, where every dollar you send toward
///         the mortgage counts. The owner picks one at formation and it is
///         immutable afterwards — it is the deal, not a setting.
contract OpenHouseTrust {
    // ═══════════════════════════════════════════ The house ══

    string  public property;          // street address / description
    bytes32 public deedRef;           // hash of the recorded deed, not the deed
    uint256 public purchasePrice;     // what the house cost, in asset units

    IERC20  public immutable asset;         // the payment asset — a USD stablecoin
    uint8   public immutable assetDecimals;
    /// Shares carry 18 decimals so 1e18 shares is exactly one dollar in.
    uint256 public immutable shareScale;

    // ═════════════════════════════════════════════ The bank ══

    address public bank;              // the lender: liquidity, control, liability
    address public pendingBank;       // two-step handover
    address public sponsor;           // the group's organiser — convenience, no power
    address public servicer;          // the only address mortgage money may be wired to
    IMortgageOracle public oracle;    // the bank's feed; see MortgageOracle.sol

    enum Status { Forming, Active, Default, Foreclosed, Discharged }
    Status public status;
    bool   public paused;

    /// How a dollar becomes equity.
    enum Basis { Contribution, Principal }
    Basis public immutable basis;

    /// Who may hold shares after the mint. The bank carries the compliance
    /// obligation for this cap table, so the bank sets the policy.
    enum TransferPolicy { Locked, MembersOnly, Open }
    TransferPolicy public transferPolicy;

    // ══════════════════════════════════════════════ Terms ═══

    /// The protocol take, in basis points of a contribution — hard-capped in
    /// code at 5%, and zero is inside the band. Whatever it collects is not
    /// kept: it pools and is paid straight back out to shareholders pro-rata
    /// when the period settles, so it lands back with the people who put the
    /// money in, in the same proportion they put it in.
    uint256 public constant MAX_FEE_BPS = 500;
    uint256 public feeBps;
    uint256 public rebatePool;        // fees waiting for the period to confirm

    /// Interest the bank charges on money it advances to cover a member
    /// shortfall, per annum in basis points. Capped so a rescue can never
    /// become a second mortgage.
    uint256 public constant MAX_ADVANCE_RATE_BPS = 2000;  // 20% APR
    uint256 public advanceRateBps;

    // ═══════════════════════════════════════════ Membership ══

    struct Member {
        bool    admitted;      // the bank has cleared them to hold shares
        bool    frozen;        // cleared, then stopped — cannot pay in or move shares
        uint64  joinedAt;
        uint256 contributed;   // gross USD sent, lifetime
        uint256 applied;       // USD of theirs that actually reached the loan
    }
    mapping(address => Member) public members;
    address[] public roster;
    mapping(address => bool) private _onRoster;

    /// When true anyone may `join()` — a public offering. When false the bank
    /// admits members one at a time. Either way the bank decides.
    bool public openMembership;

    // ═════════════════════════════════════════════ Periods ═══

    /// A billing period mirrored from the oracle. Money is raised against the
    /// statement, wired in one transfer, and only turns into equity once the
    /// servicer confirms it posted.
    struct Period {
        uint256 due;               // the bill, snapshotted from the oracle at open
        uint256 raised;            // gross contributions
        uint256 fees;              // protocol take inside `raised`
        uint256 advance;           // bank money covering a shortfall
        uint256 wired;             // sent to the servicer
        uint256 principalApplied;  // from the oracle's settlement
        uint256 netApplied;        // the denominator equity is split against
        uint64  feeBps;            // pinned at open, so a mid-period change is not retroactive
        bool    open;
        bool    wiredOut;
        bool    confirmed;
        bool    aborted;
    }
    mapping(uint64 => Period) public periods;
    mapping(uint64 => mapping(address => uint256)) public contributionOf;  // gross
    mapping(uint64 => mapping(address => uint256)) public feeOf;
    mapping(uint64 => mapping(address => bool))    public settled;
    uint64[] public periodIndex;

    uint256 public totalApplied;      // every dollar of member money that reached the loan
    uint256 public totalInterestPaid; // the running cost of the bank's capital

    /// Bank money advanced and not yet repaid. Senior to every distribution:
    /// rent pays the lender back before it pays an owner.
    uint256 public lienPrincipal;
    uint256 public lienAccrued;
    uint64  public lienTouchedAt;

    // ══════════════════════════════════════ Shares (ERC-20) ══

    string  public name;
    string  public symbol;
    uint8   public constant decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    // ═════════════════════════════ Pro-rata distributions ════

    uint256 internal constant MAGNITUDE = 2 ** 128;
    uint256 public magnifiedPerShare;
    mapping(address => int256)  internal _correction;
    mapping(address => uint256) public withdrawnOf;
    uint256 public totalDistributed;
    /// Money that arrived while nobody held a share yet. It is not lost — it
    /// joins the next distribution.
    uint256 public undistributed;

    // ═════════════════════════════════════════════ Events ════

    event Formed(address indexed bank, address indexed sponsor, string property);
    event Activated(address indexed oracle, address indexed servicer, uint256 purchasePrice);
    event MemberAdmitted(address indexed who);
    event MemberFrozen(address indexed who, bool frozen);
    event MemberNominated(address indexed who, address indexed by);
    event PeriodOpened(uint64 indexed period, uint256 due, uint64 feeBps);
    event Contributed(uint64 indexed period, address indexed who, uint256 gross, uint256 fee, uint256 net);
    event Refunded(uint64 indexed period, address indexed who, uint256 amount);
    event Wired(uint64 indexed period, address indexed servicer, uint256 amount);
    event PeriodConfirmed(uint64 indexed period, uint256 principalApplied, uint256 netApplied, uint256 rebate);
    event PeriodAborted(uint64 indexed period);
    event EquityMinted(uint64 indexed period, address indexed who, uint256 shares, uint256 usd);
    event AdvanceMade(uint64 indexed period, uint256 amount, uint256 lien);
    event AdvanceRepaid(uint256 amount, uint256 lienRemaining);
    event Distributed(uint256 amount, uint256 perShare);
    event Withdrawn(address indexed who, uint256 amount);
    event StatusChanged(Status from, Status to);
    event PausedSet(bool paused);
    event TermsSet(uint256 feeBps, uint256 advanceRateBps);
    event TransferPolicySet(TransferPolicy policy);
    event OracleSet(address indexed oracle);
    event ServicerSet(address indexed servicer);
    event OpenMembershipSet(bool open);
    event BankNominated(address indexed to);
    event BankChanged(address indexed from, address indexed to);
    event SponsorSet(address indexed sponsor);
    event Reissued(address indexed from, address indexed to, uint256 shares);
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    // ══════════════════════════════════════════ Modifiers ════

    modifier onlyBank() {
        require(msg.sender == bank, "Trust: not the bank");
        _;
    }

    modifier live() {
        require(status == Status.Active, "Trust: not active");
        require(!paused, "Trust: paused");
        _;
    }

    uint256 private _lock = 1;
    modifier nonReentrant() {
        require(_lock == 1, "Trust: reentrant");
        _lock = 2;
        _;
        _lock = 1;
    }

    // ══════════════════════════════════════ Construction ═════

    /// Everything a trust is born with. One struct because a deal has a lot of
    /// parts and none of them should be positional guesswork at the call site.
    struct Init {
        string  name;             // share token name
        string  symbol;
        string  property;
        bytes32 deedRef;
        address bank;             // the lender. Holds every lever in this contract.
        address sponsor;          // whoever organised the group. Nominates members; that is all.
        address asset;            // the USD stablecoin every number here is denominated in
        Basis   basis;            // Contribution or Principal. Immutable — it is the deal.
        uint256 feeBps;
        uint256 advanceRateBps;
        address oracle;           // the bank's feed; zero leaves the trust Forming
        address servicer;         // where mortgage money is wired; zero leaves it Forming
        uint256 purchasePrice;
        address[] founders;       // admitted at birth — the list the bank underwrote
    }

    constructor(Init memory init) {
        require(init.bank != address(0), "Trust: zero bank");
        require(init.asset != address(0), "Trust: zero asset");
        require(init.feeBps <= MAX_FEE_BPS, "Trust: fee out of band");
        require(init.advanceRateBps <= MAX_ADVANCE_RATE_BPS, "Trust: advance rate out of band");

        name = init.name;
        symbol = init.symbol;
        property = init.property;
        deedRef = init.deedRef;
        bank = init.bank;
        sponsor = init.sponsor;
        asset = IERC20(init.asset);
        basis = init.basis;
        feeBps = init.feeBps;
        advanceRateBps = init.advanceRateBps;

        uint8 d = IERC20(init.asset).decimals();
        require(d <= 18, "Trust: asset decimals > 18");
        assetDecimals = d;
        shareScale = 10 ** (18 - d);

        transferPolicy = TransferPolicy.MembersOnly;
        status = Status.Forming;
        lienTouchedAt = uint64(block.timestamp);
        emit Formed(init.bank, init.sponsor, init.property);

        for (uint256 i = 0; i < init.founders.length; i++) {
            if (!members[init.founders[i]].admitted) _admit(init.founders[i]);
        }

        // A deployer that already has the bank's feed and account can hand over
        // a live trust; otherwise the bank switches it on itself with activate().
        if (init.oracle != address(0) && init.servicer != address(0)) {
            _activate(init.oracle, init.servicer, init.purchasePrice);
        }
    }

    /// @notice The bank turns the deal on. Until this lands the trust cannot
    ///         take a cent: no oracle means no bill, and no servicer means
    ///         nowhere for money to go. A group can organise itself all it
    ///         likes; it becomes a financed structure when the lender says so.
    function activate(address _oracle, address _servicer, uint256 _purchasePrice) external onlyBank {
        _activate(_oracle, _servicer, _purchasePrice);
    }

    function _activate(address _oracle, address _servicer, uint256 _purchasePrice) internal {
        require(status == Status.Forming, "Trust: already activated");
        require(_oracle != address(0) && _servicer != address(0), "Trust: zero address");
        require(IMortgageOracle(_oracle).bank() == bank, "Trust: oracle answers to another bank");
        oracle = IMortgageOracle(_oracle);
        servicer = _servicer;
        purchasePrice = _purchasePrice;
        _setStatus(Status.Active);
        emit Activated(_oracle, _servicer, _purchasePrice);
    }

    // ═════════════════════════════════════════ Membership ════

    /// @notice Join an open offering. The bank decides whether the door is open
    ///         at all; while it is, membership is permissionless.
    function join() external {
        require(openMembership, "Trust: membership is by admission");
        require(status == Status.Forming || status == Status.Active, "Trust: closed");
        require(!members[msg.sender].admitted, "Trust: already a member");
        _admit(msg.sender);
    }

    /// @notice The sponsor puts a name forward. It does nothing on its own —
    ///         it is an event the bank's compliance desk reads.
    function nominateMember(address who) external {
        require(msg.sender == sponsor || msg.sender == bank, "Trust: not the sponsor");
        emit MemberNominated(who, msg.sender);
    }

    /// @notice The bank admits a member. This is the KYC gate, and it is the
    ///         bank's because the reporting obligation is the bank's.
    function admit(address who) external onlyBank {
        require(!members[who].admitted, "Trust: already a member");
        _admit(who);
    }

    function admitMany(address[] calldata who) external onlyBank {
        for (uint256 i = 0; i < who.length; i++) {
            if (!members[who[i]].admitted) _admit(who[i]);
        }
    }

    /// @notice Stop a member paying in or moving shares. It does NOT touch what
    ///         they already own — a frozen member still accrues distributions
    ///         and can still withdraw them. Freezing is a compliance brake, not
    ///         a confiscation, and there is no function here that makes it one.
    function setFrozen(address who, bool f) external onlyBank {
        require(members[who].admitted, "Trust: not a member");
        members[who].frozen = f;
        emit MemberFrozen(who, f);
    }

    function _admit(address who) internal {
        require(who != address(0), "Trust: zero member");
        members[who].admitted = true;
        members[who].joinedAt = uint64(block.timestamp);
        if (!_onRoster[who]) { _onRoster[who] = true; roster.push(who); }
        emit MemberAdmitted(who);
    }

    function memberCount() external view returns (uint256) { return roster.length; }
    function isMember(address who) external view returns (bool) { return members[who].admitted; }
    function isFrozen(address who) external view returns (bool) { return members[who].frozen; }

    // ══════════════════════════════════════════ Periods ══════

    /// @notice Mirror the oracle's newest statement into a period this trust can
    ///         raise against. Permissionless — the numbers come from the bank's
    ///         feed either way, and somebody has to pay the gas.
    function openPeriod() external live returns (uint64 period) {
        require(oracle.isFresh(), "Trust: oracle stale");
        period = oracle.latestPeriod();
        Period storage p = periods[period];
        require(!p.open, "Trust: already open");

        uint256 due = oracle.totalDue(period);
        require(due > 0, "Trust: nothing due");

        p.due = due;
        p.feeBps = uint64(feeBps);
        p.open = true;
        periodIndex.push(period);
        emit PeriodOpened(period, due, p.feeBps);
    }

    /// @notice Put money toward the mortgage. Takes at most what the period
    ///         still needs — send more and only the shortfall is pulled, so the
    ///         last member in is never overcharged and never front-run into a
    ///         revert. Nothing is equity yet: it is escrow here until the
    ///         servicer confirms it posted.
    function contribute(uint64 period, uint256 amount) external live nonReentrant returns (uint256 gross) {
        Member storage me = members[msg.sender];
        require(me.admitted, "Trust: not a member");
        require(!me.frozen, "Trust: member frozen");
        require(oracle.isFresh(), "Trust: oracle stale");

        Period storage p = periods[period];
        require(p.open && !p.wiredOut && !p.aborted, "Trust: period not taking money");

        uint256 net_ = p.raised - p.fees;
        require(net_ < p.due, "Trust: period funded");
        uint256 room = p.due - net_;

        // Cap the pull in GROSS terms so the fee is charged on money that is
        // actually used, never on an overpayment we hand straight back.
        uint256 bps = p.feeBps;
        uint256 maxGross = bps == 0
            ? room
            : (room * 10_000 + (10_000 - bps) - 1) / (10_000 - bps);
        gross = amount < maxGross ? amount : maxGross;
        require(gross > 0, "Trust: zero contribution");

        uint256 fee = (gross * bps) / 10_000;
        uint256 net = gross - fee;
        if (net > room) { fee += net - room; net = room; }

        p.raised += gross;
        p.fees += fee;
        contributionOf[period][msg.sender] += gross;
        feeOf[period][msg.sender] += fee;
        me.contributed += gross;
        rebatePool += fee;

        require(asset.transferFrom(msg.sender, address(this), gross), "Trust: transfer failed");
        emit Contributed(period, msg.sender, gross, fee, net);
    }

    /// @notice Wire the period's bill to the servicer, in one transfer, to the
    ///         one address the bank named. Permissionless once the money is
    ///         there — holding a funded period hostage should not be a power
    ///         anybody has.
    function wire(uint64 period) external live nonReentrant {
        Period storage p = periods[period];
        require(p.open && !p.wiredOut && !p.aborted, "Trust: nothing to wire");
        uint256 net = p.raised - p.fees;
        require(net + p.advance >= p.due, "Trust: period underfunded");

        p.wiredOut = true;
        p.wired = p.due;
        require(asset.transfer(servicer, p.due), "Trust: transfer failed");
        emit Wired(period, servicer, p.due);
    }

    /// @notice Read the servicer's posting back off the oracle and turn the
    ///         period into equity. This is the moment a contribution stops
    ///         being escrow: not when it was sent, not when it was wired, but
    ///         when the bank's own feed confirms the loan was credited.
    function confirmPeriod(uint64 period) external live {
        Period storage p = periods[period];
        require(p.wiredOut && !p.confirmed, "Trust: not wired or already confirmed");

        IMortgageOracle.Settlement memory s = oracle.settlementOf(period);
        require(s.confirmed, "Trust: servicer has not posted");

        p.principalApplied = s.principalApplied;
        // Members and the bank funded the wire together; equity only ever
        // splits the members' part, and only ever against the same denominator
        // the shares are minted from.
        uint256 memberNet = p.raised - p.fees;
        p.netApplied = basis == Basis.Principal
            ? (s.principalApplied * memberNet) / (memberNet + p.advance)
            : memberNet;
        p.confirmed = true;

        totalInterestPaid += s.interestApplied;

        // The take comes back the instant the period lands, split by shares —
        // which is to say, split by the dollars people already have in. It is
        // THIS period's fees, so a period still taking money is untouched, and
        // it lands on the holders who were already in at confirmation. The very
        // first period has no holders yet: that fee waits in `undistributed`
        // and joins the next split rather than falling to whoever settles first.
        uint256 rebate = p.fees;
        if (rebate > 0) { rebatePool -= rebate; _distribute(rebate); }

        emit PeriodConfirmed(period, s.principalApplied, p.netApplied, rebate);
    }

    /// @notice Mint a contributor's shares for a confirmed period. Permissionless
    ///         and idempotent — anyone can settle anyone, so a member who has
    ///         gone quiet still gets their equity.
    /// @dev    Deliberately NOT gated on `live`: once the servicer has posted
    ///         your money, nothing — not a pause, not a default, not a
    ///         foreclosure — stands between you and the shares it bought. A
    ///         mint that could be withheld would be a seizure with extra steps.
    function settle(uint64 period, address who) public returns (uint256 shares) {
        Period storage p = periods[period];
        require(p.confirmed, "Trust: period not confirmed");
        require(!settled[period][who], "Trust: already settled");

        uint256 gross = contributionOf[period][who];
        require(gross > 0, "Trust: nothing contributed");
        settled[period][who] = true;

        uint256 net = gross - feeOf[period][who];
        uint256 memberNet = p.raised - p.fees;
        // Pro-rata by construction: your dollars over the members' dollars,
        // times whatever the period is worth in equity.
        uint256 usd = (net * p.netApplied) / memberNet;
        if (usd == 0) return 0;

        members[who].applied += usd;
        totalApplied += usd;
        shares = usd * shareScale;
        _mint(who, shares);
        emit EquityMinted(period, who, shares, usd);
    }

    function settleMany(uint64 period, address[] calldata who) external {
        for (uint256 i = 0; i < who.length; i++) {
            if (!settled[period][who[i]] && contributionOf[period][who[i]] > 0) settle(period, who[i]);
        }
    }

    /// @notice The bank kills a period that will not fund. Contributions become
    ///         refundable; nothing else about the trust changes.
    function abortPeriod(uint64 period) external onlyBank {
        Period storage p = periods[period];
        require(p.open && !p.wiredOut, "Trust: cannot abort");
        p.aborted = true;
        emit PeriodAborted(period);
    }

    /// @notice Take your money back out of an aborted period — in full, fee
    ///         included. This is the second and last door out of escrow.
    function refund(uint64 period) external nonReentrant returns (uint256 amount) {
        Period storage p = periods[period];
        require(p.aborted, "Trust: period not aborted");
        amount = contributionOf[period][msg.sender];
        require(amount > 0, "Trust: nothing to refund");

        uint256 fee = feeOf[period][msg.sender];
        contributionOf[period][msg.sender] = 0;
        feeOf[period][msg.sender] = 0;
        p.raised -= amount;
        p.fees -= fee;
        rebatePool -= fee;
        members[msg.sender].contributed -= amount;

        require(asset.transfer(msg.sender, amount), "Trust: transfer failed");
        emit Refunded(period, msg.sender, amount);
    }

    // ════════════════════════════ The bank's own liquidity ═══

    /// @notice The lender covers a shortfall so the loan stays current. This is
    ///         a loan to the trust, not a purchase of it: it accrues at
    ///         `advanceRateBps`, ranks ahead of every distribution, and is
    ///         repaid in cash. It never mints the bank a share, so a member's
    ///         fraction of the house is the same after a rescue as before it.
    function advance(uint64 period, uint256 amount) external onlyBank nonReentrant {
        Period storage p = periods[period];
        require(p.open && !p.wiredOut && !p.aborted, "Trust: period not taking money");
        require(amount > 0, "Trust: zero advance");

        _accrueLien();
        p.advance += amount;
        lienPrincipal += amount;

        require(asset.transferFrom(msg.sender, address(this), amount), "Trust: transfer failed");
        emit AdvanceMade(period, amount, lienPrincipal + lienAccrued);
    }

    /// @notice Pay the lender back. Anyone may — the sponsor, a member, the
    ///         property's rent. Interest first, then principal.
    function repayAdvance(uint256 amount) public nonReentrant returns (uint256 paid) {
        paid = _repayFrom(msg.sender, amount, true);
    }

    function _repayFrom(address from, uint256 amount, bool pull) internal returns (uint256 paid) {
        _accrueLien();
        uint256 owed = lienPrincipal + lienAccrued;
        if (owed == 0 || amount == 0) return 0;
        paid = amount < owed ? amount : owed;

        uint256 toInterest = paid < lienAccrued ? paid : lienAccrued;
        lienAccrued -= toInterest;
        lienPrincipal -= (paid - toInterest);

        if (pull) require(asset.transferFrom(from, address(this), paid), "Trust: transfer failed");
        require(asset.transfer(bank, paid), "Trust: transfer failed");
        emit AdvanceRepaid(paid, lienPrincipal + lienAccrued);
    }

    /// @dev Simple interest on the outstanding advance, banked on every touch.
    function _accrueLien() internal {
        uint64 nowTs = uint64(block.timestamp);
        if (lienPrincipal > 0 && advanceRateBps > 0 && nowTs > lienTouchedAt) {
            lienAccrued += (lienPrincipal * advanceRateBps * (nowTs - lienTouchedAt)) / (10_000 * 365 days);
        }
        lienTouchedAt = nowTs;
    }

    function lienOutstanding() public view returns (uint256) {
        uint256 accrued = lienAccrued;
        if (lienPrincipal > 0 && advanceRateBps > 0 && block.timestamp > lienTouchedAt) {
            accrued += (lienPrincipal * advanceRateBps * (block.timestamp - lienTouchedAt)) / (10_000 * 365 days);
        }
        return lienPrincipal + accrued;
    }

    // ═══════════════════════════════════════ Distributions ═══

    /// @notice Send income — rent, a refund, sale proceeds — to the owners. The
    ///         lender's advance is senior and is settled out of this first;
    ///         what is left is split by shares, which is to say by the dollars
    ///         each owner put into the house.
    function distributeIncome(uint256 amount) external nonReentrant returns (uint256 toOwners) {
        require(amount > 0, "Trust: zero distribution");
        require(asset.transferFrom(msg.sender, address(this), amount), "Trust: transfer failed");

        _accrueLien();
        uint256 owed = lienPrincipal + lienAccrued;
        uint256 toLender = amount < owed ? amount : owed;
        if (toLender > 0) {
            uint256 toInterest = toLender < lienAccrued ? toLender : lienAccrued;
            lienAccrued -= toInterest;
            lienPrincipal -= (toLender - toInterest);
            require(asset.transfer(bank, toLender), "Trust: transfer failed");
            emit AdvanceRepaid(toLender, lienPrincipal + lienAccrued);
        }
        toOwners = amount - toLender;
        if (toOwners > 0) _distribute(toOwners);
    }

    /// @notice Take your accrued distributions.
    function withdraw() external nonReentrant returns (uint256 amount) {
        amount = withdrawableOf(msg.sender);
        require(amount > 0, "Trust: nothing to withdraw");
        withdrawnOf[msg.sender] += amount;
        require(asset.transfer(msg.sender, amount), "Trust: transfer failed");
        emit Withdrawn(msg.sender, amount);
    }

    /// @notice Push money that arrived before there were any shareholders out
    ///         to the shareholders there are now. Permissionless — nothing
    ///         should be able to strand a distribution.
    function flushUndistributed() external {
        require(undistributed > 0, "Trust: nothing waiting");
        require(totalSupply > 0, "Trust: no shares yet");
        _distribute(0);
    }

    function _distribute(uint256 amount) internal {
        uint256 total = amount + undistributed;
        if (total == 0) return;
        if (totalSupply == 0) { undistributed = total; return; }
        undistributed = 0;
        magnifiedPerShare += (total * MAGNITUDE) / totalSupply;
        totalDistributed += total;
        emit Distributed(total, magnifiedPerShare);
    }

    function accumulativeOf(address who) public view returns (uint256) {
        return uint256(int256(magnifiedPerShare * balanceOf[who]) + _correction[who]) / MAGNITUDE;
    }

    function withdrawableOf(address who) public view returns (uint256) {
        return accumulativeOf(who) - withdrawnOf[who];
    }

    // ══════════════════════════════════════════ Equity views ═

    /// @notice A member's share of the house, in basis points. This is the
    ///         whole model in one line: dollars in over dollars in.
    function equityBps(address who) external view returns (uint256) {
        if (totalSupply == 0) return 0;
        return (balanceOf[who] * 10_000) / totalSupply;
    }

    /// @notice How much of the purchase price the members have bought so far,
    ///         in basis points. The rest is still the lender's.
    function ownedBps() external view returns (uint256) {
        if (purchasePrice == 0) return 0;
        uint256 bpsOwned = (totalApplied * 10_000) / purchasePrice;
        return bpsOwned > 10_000 ? 10_000 : bpsOwned;
    }

    /// @notice Principal still owed on the mortgage, straight from the feed.
    function mortgageBalance() external view returns (uint256) {
        return address(oracle) == address(0) ? 0 : oracle.principalBalance();
    }

    /// @notice Every dollar a member has sent, and the part of it that bought
    ///         house rather than paying for the bank's capital.
    function positionOf(address who)
        external view
        returns (uint256 contributed, uint256 applied, uint256 shares, uint256 bps, uint256 claimable)
    {
        Member storage m = members[who];
        contributed = m.contributed;
        applied = m.applied;
        shares = balanceOf[who];
        bps = totalSupply == 0 ? 0 : (shares * 10_000) / totalSupply;
        claimable = withdrawableOf(who);
    }

    function periodCount() external view returns (uint256) { return periodIndex.length; }

    /// @notice What a period still needs, in gross terms — what to approve.
    function roomFor(uint64 period) external view returns (uint256) {
        Period storage p = periods[period];
        if (!p.open || p.wiredOut || p.aborted) return 0;
        uint256 net = p.raised - p.fees;
        if (net >= p.due) return 0;
        uint256 room = p.due - net;
        uint256 bps = p.feeBps;
        return bps == 0 ? room : (room * 10_000 + (10_000 - bps) - 1) / (10_000 - bps);
    }

    /// @notice Split a contribution the way `contribute` would, without paying.
    ///         `shares` is an estimate: under the Principal basis only the
    ///         amortized slice of the payment buys house, and the exact slice is
    ///         whatever the servicer posts, not whatever it billed.
    function quote(uint64 period, uint256 amount)
        external view
        returns (uint256 gross, uint256 fee, uint256 net, uint256 shares)
    {
        Period storage p = periods[period];
        uint256 bps = p.open ? p.feeBps : feeBps;
        gross = amount;
        fee = (gross * bps) / 10_000;
        net = gross - fee;
        shares = net * shareScale;
        if (basis == Basis.Principal && p.open) {
            IMortgageOracle.Statement memory s = oracle.statementOf(period);
            uint256 total = s.principalDue + s.interestDue + s.escrowDue + s.feesDue;
            shares = total == 0 ? 0 : (net * s.principalDue * shareScale) / total;
        }
    }

    // ═══════════════════════════════ The bank's control room ═

    /// @notice Freeze the contract. One call, one seat, no delay — the lender
    ///         does not have to ask anyone before protecting its collateral.
    ///         Pausing stops money moving; it does not move any.
    function setPaused(bool p) external onlyBank {
        paused = p;
        emit PausedSet(p);
    }

    /// @notice Call the loan. Gated on the oracle, not on the bank's mood: the
    ///         feed has to say the period actually went unpaid.
    function declareDefault(uint64 period) external onlyBank {
        require(status == Status.Active, "Trust: not active");
        require(oracle.isDelinquent(period), "Trust: loan is current");
        _setStatus(Status.Default);
    }

    /// @notice Take the house. The lender's remedy, and the reason it holds the
    ///         keys to this contract at all. On-chain it stops the mint and
    ///         turns the trust into a claim on whatever the sale returns.
    function foreclose() external onlyBank {
        require(status == Status.Default, "Trust: not in default");
        _setStatus(Status.Foreclosed);
    }

    /// @notice The loan came back current. Only out of Default, and never out
    ///         of Foreclosed — that one does not unwind on-chain.
    function cure() external onlyBank {
        require(status == Status.Default, "Trust: not in default");
        _setStatus(Status.Active);
    }

    /// @notice The mortgage is paid off. Permissionless and checked against the
    ///         feed — the bank cannot refuse to admit it was repaid.
    function dischargeMortgage() external {
        require(status == Status.Active, "Trust: not active");
        require(address(oracle) != address(0), "Trust: no oracle");
        require(oracle.principalBalance() == 0, "Trust: balance outstanding");
        _setStatus(Status.Discharged);
    }

    /// @notice Sale or insurance proceeds after a foreclosure. The lender is
    ///         made whole first; the surplus belongs to the members, pro-rata,
    ///         and the contract has no other place to send it.
    function distributeRecovery(uint256 amount) external onlyBank nonReentrant {
        require(status == Status.Foreclosed, "Trust: not foreclosed");
        require(amount > 0, "Trust: zero recovery");
        require(asset.transferFrom(msg.sender, address(this), amount), "Trust: transfer failed");
        _accrueLien();
        uint256 owed = lienPrincipal + lienAccrued;
        uint256 toLender = amount < owed ? amount : owed;
        if (toLender > 0) {
            uint256 toInterest = toLender < lienAccrued ? toLender : lienAccrued;
            lienAccrued -= toInterest;
            lienPrincipal -= (toLender - toInterest);
            require(asset.transfer(bank, toLender), "Trust: transfer failed");
            emit AdvanceRepaid(toLender, lienPrincipal + lienAccrued);
        }
        if (amount > toLender) _distribute(amount - toLender);
    }

    function setTerms(uint256 _feeBps, uint256 _advanceRateBps) external onlyBank {
        require(_feeBps <= MAX_FEE_BPS, "Trust: fee out of band");
        require(_advanceRateBps <= MAX_ADVANCE_RATE_BPS, "Trust: advance rate out of band");
        _accrueLien();   // the old rate earned up to this instant, not past it
        feeBps = _feeBps;
        advanceRateBps = _advanceRateBps;
        emit TermsSet(_feeBps, _advanceRateBps);
    }

    function setTransferPolicy(TransferPolicy p) external onlyBank {
        transferPolicy = p;
        emit TransferPolicySet(p);
    }

    function setOpenMembership(bool o) external onlyBank {
        openMembership = o;
        emit OpenMembershipSet(o);
    }

    /// @notice Point at a new feed — a servicing transfer, a reporter set
    ///         rotation. The new oracle must answer to this same bank, so a
    ///         swap can never quietly hand the numbers to somebody else.
    function setOracle(address _oracle) external onlyBank {
        require(_oracle != address(0), "Trust: zero oracle");
        require(IMortgageOracle(_oracle).bank() == bank, "Trust: oracle answers to another bank");
        oracle = IMortgageOracle(_oracle);
        emit OracleSet(_oracle);
    }

    function setServicer(address _servicer) external onlyBank {
        require(_servicer != address(0), "Trust: zero servicer");
        servicer = _servicer;
        emit ServicerSet(_servicer);
    }

    function setSponsor(address _sponsor) external onlyBank {
        sponsor = _sponsor;
        emit SponsorSet(_sponsor);
    }

    /// @notice Move a member's ENTIRE position to a new address of theirs — a
    ///         lost key, a death, a court order. It is the one bank power that
    ///         touches shares, and it is deliberately shaped so it cannot be a
    ///         confiscation: all or nothing, and the destination must be an
    ///         admitted member. The size of the stake is identical afterwards.
    function reissue(address from, address to) external onlyBank {
        require(from != to && to != address(0), "Trust: bad reissue");
        require(members[to].admitted, "Trust: destination not a member");
        uint256 bal = balanceOf[from];
        require(bal > 0, "Trust: nothing to reissue");

        // Carry the unwithdrawn distributions across with the shares.
        uint256 owed = withdrawableOf(from);
        withdrawnOf[from] = accumulativeOf(from);
        _move(from, to, bal);
        // Grant the moved-from position's unwithdrawn income to the destination:
        // a POSITIVE correction, the mirror of the negative one `_mint` applies.
        if (owed > 0) {
            _correction[to] += int256(owed * MAGNITUDE);
        }
        members[to].applied += members[from].applied;
        members[from].applied = 0;
        emit Reissued(from, to, bal);
    }

    function nominateBank(address to) external onlyBank {
        require(to != address(0), "Trust: zero bank");
        pendingBank = to;
        emit BankNominated(to);
    }

    /// @notice The incoming lender takes the seat — and with it the liability.
    function acceptBank() external {
        require(msg.sender == pendingBank, "Trust: not nominated");
        emit BankChanged(bank, msg.sender);
        bank = msg.sender;
        pendingBank = address(0);
    }

    function _setStatus(Status to) internal {
        emit StatusChanged(status, to);
        status = to;
    }

    // ═══════════════════════════════════════════ ERC-20 ══════

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
        uint256 a = allowance[from][msg.sender];
        if (a != type(uint256).max) {
            require(a >= amount, "Trust: allowance");
            allowance[from][msg.sender] = a - amount;
        }
        _transfer(from, to, amount);
        return true;
    }

    /// @dev The compliance hook. Shares are a claim on a financed house, so who
    ///      may hold one is the bank's call — but the check is on the transfer,
    ///      never on the balance.
    function _transfer(address from, address to, uint256 amount) internal {
        require(!paused, "Trust: paused");
        require(to != address(0), "Trust: zero recipient");
        require(transferPolicy != TransferPolicy.Locked, "Trust: shares are locked");
        require(!members[from].frozen && !members[to].frozen, "Trust: frozen");
        if (transferPolicy == TransferPolicy.MembersOnly) {
            require(members[to].admitted, "Trust: recipient not a member");
        }
        _move(from, to, amount);
    }

    function _move(address from, address to, uint256 amount) internal {
        uint256 bal = balanceOf[from];
        require(bal >= amount, "Trust: balance");
        balanceOf[from] = bal - amount;
        balanceOf[to] += amount;
        // Distributions already earned stay with the sender.
        int256 shift = int256(magnifiedPerShare * amount);
        _correction[from] += shift;
        _correction[to]   -= shift;
        emit Transfer(from, to, amount);
    }

    /// @dev The only mint. It is reachable from exactly one place — `settle`,
    ///      against a servicer-confirmed payment — and there is no burn at all.
    function _mint(address to, uint256 amount) internal {
        totalSupply += amount;
        balanceOf[to] += amount;
        _correction[to] -= int256(magnifiedPerShare * amount);
        emit Transfer(address(0), to, amount);
        // Deliberately does NOT flush `undistributed`: minting inside a loop of
        // settlements would hand the whole waiting pool to whoever settled
        // first. It waits for the next split, when every share exists.
    }
}
