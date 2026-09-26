// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IMortgageOracle — what a trust needs to know about a real mortgage
/// @notice The seam between a loan that lives in a bank's servicing system and a
///         trust that lives on-chain. Everything a member's equity depends on —
///         what is owed this month, how much of a payment went to principal,
///         whether the loan is current — enters the chain through here and
///         nowhere else. The trust never guesses at a balance.
interface IMortgageOracle {
    /// The month's bill, as the servicer computed it.
    struct Statement {
        uint64  period;             // billing period index, monotonic
        uint64  asOf;               // when the servicer cut this statement
        uint64  dueBy;              // payment deadline
        uint256 principalBalance;   // loan principal still outstanding, after the last posting
        uint256 principalDue;       // amortized principal in this month's payment
        uint256 interestDue;        // the bank's coupon on its own liquidity
        uint256 escrowDue;          // taxes + insurance held by the servicer
        uint256 feesDue;            // late fees, servicing charges
        bool    published;
    }

    /// What the servicer says it actually received and how it applied it. A
    /// payment leaving the trust is not a payment: only a posting is.
    struct Settlement {
        uint64  confirmedAt;
        uint256 amount;             // total posted to the loan
        uint256 principalApplied;   // the part that reduced the balance — the equity part
        uint256 interestApplied;    // the bank's earnings, buys nobody any house
        uint256 escrowApplied;
        uint256 feesApplied;
        bool    confirmed;
        bool    delinquent;         // the period closed short
    }

    function bank() external view returns (address);
    function loanRef() external view returns (bytes32);
    function latestPeriod() external view returns (uint64);
    function statementOf(uint64 period) external view returns (Statement memory);
    function settlementOf(uint64 period) external view returns (Settlement memory);
    function totalDue(uint64 period) external view returns (uint256);
    function isFresh() external view returns (bool);
    function isDelinquent(uint64 period) external view returns (bool);
    function principalBalance() external view returns (uint256);
}

/// @title MortgageOracle — the bank's feed, attested by the bank's reporters
/// @notice A loan is a fact held by a servicer. This contract is how that fact
///         is put on-chain without any single machine being trusted to say it:
///         `quorum` of the bank's reporters must submit byte-identical data
///         before it publishes. The bank appoints and removes the reporters and
///         can freeze the feed; it cannot rewrite a period that already
///         published, because equity has already been minted against it.
///
///         Amounts are denominated in the trust's payment asset (USDC, 6dp) so
///         no unit conversion ever happens between the two contracts.
contract MortgageOracle is IMortgageOracle {
    // ─────────────────────────────────────── The lender ────
    address public bank;             // the liquidity provider — owns this feed
    address public pendingBank;      // two-step handover, so a typo isn't terminal
    string  public servicer;         // human name of the servicing institution
    bytes32 public override loanRef; // hash of the loan account number, never the number

    // ────────────────────────────────────── Reporters ──────
    /// The bank's own signers. One is allowed and is honest-but-single; the
    /// point of more is that a compromised reporting box cannot mint equity out
    /// of a made-up principal split by itself.
    mapping(address => bool) public isReporter;
    address[] public reporters;
    uint8 public quorum;

    /// A statement older than this is not usable — the trust refuses to take
    /// money against a stale bill. Oracle liveness is a bank obligation.
    uint64 public maxAge;

    bool public frozen;              // bank stops the feed; nothing publishes

    uint64 public override latestPeriod;
    mapping(uint64 => Statement)  private _statements;
    mapping(uint64 => Settlement) private _settlements;

    /// digest → how many distinct reporters have signed exactly this payload.
    mapping(bytes32 => uint256) public attestations;
    mapping(bytes32 => mapping(address => bool)) public attested;

    event BankNominated(address indexed to);
    event BankChanged(address indexed from, address indexed to);
    event ReporterSet(address indexed reporter, bool active);
    event QuorumSet(uint8 quorum);
    event MaxAgeSet(uint64 maxAge);
    event FrozenSet(bool frozen);
    event ServicerSet(string servicer, bytes32 loanRef);
    event Attested(bytes32 indexed digest, address indexed reporter, uint256 count);
    event StatementPublished(uint64 indexed period, uint256 totalDue, uint256 principalBalance);
    event SettlementPublished(uint64 indexed period, uint256 amount, uint256 principalApplied, bool delinquent);

    modifier onlyBank() {
        require(msg.sender == bank, "Oracle: not the bank");
        _;
    }

    modifier onlyReporter() {
        require(isReporter[msg.sender], "Oracle: not a reporter");
        require(!frozen, "Oracle: frozen");
        _;
    }

    constructor(
        address _bank,
        string memory _servicer,
        bytes32 _loanRef,
        address[] memory _reporters,
        uint8 _quorum,
        uint64 _maxAge
    ) {
        require(_bank != address(0), "Oracle: zero bank");
        require(_reporters.length > 0, "Oracle: no reporters");
        require(_quorum > 0 && _quorum <= _reporters.length, "Oracle: bad quorum");
        require(_maxAge > 0, "Oracle: zero max age");
        bank = _bank;
        servicer = _servicer;
        loanRef = _loanRef;
        for (uint256 i = 0; i < _reporters.length; i++) {
            address r = _reporters[i];
            require(r != address(0), "Oracle: zero reporter");
            require(!isReporter[r], "Oracle: duplicate reporter");
            isReporter[r] = true;
            reporters.push(r);
            emit ReporterSet(r, true);
        }
        quorum = _quorum;
        maxAge = _maxAge;
    }

    // ────────────────────────────────────── Publishing ─────

    /// @notice Attest this month's bill. The call publishes on the attestation
    ///         that reaches quorum; earlier ones only count. Reporters must
    ///         agree on every field — a single differing wei is a different
    ///         payload and starts its own tally.
    function submitStatement(
        uint64 period,
        uint64 asOf,
        uint64 dueBy,
        uint256 principalBalance_,
        uint256 principalDue,
        uint256 interestDue,
        uint256 escrowDue,
        uint256 feesDue
    ) external onlyReporter {
        require(!_statements[period].published, "Oracle: period published");
        require(period >= latestPeriod, "Oracle: period behind");
        require(asOf <= block.timestamp, "Oracle: statement from the future");
        require(dueBy > asOf, "Oracle: due before issued");

        bytes32 digest = keccak256(abi.encode(
            "STATEMENT", period, asOf, dueBy,
            principalBalance_, principalDue, interestDue, escrowDue, feesDue
        ));
        if (_count(digest) < quorum) return;

        _statements[period] = Statement({
            period: period,
            asOf: asOf,
            dueBy: dueBy,
            principalBalance: principalBalance_,
            principalDue: principalDue,
            interestDue: interestDue,
            escrowDue: escrowDue,
            feesDue: feesDue,
            published: true
        });
        latestPeriod = period;
        emit StatementPublished(
            period, principalDue + interestDue + escrowDue + feesDue, principalBalance_
        );
    }

    /// @notice Attest that the servicer posted a payment — the only event that
    ///         mints equity in the trust. `principalApplied` is the number that
    ///         matters: it is the part of the money that bought house rather
    ///         than renting the bank's capital.
    function submitSettlement(
        uint64 period,
        uint256 amount,
        uint256 principalApplied,
        uint256 interestApplied,
        uint256 escrowApplied,
        uint256 feesApplied,
        bool delinquent
    ) external onlyReporter {
        require(_statements[period].published, "Oracle: no statement");
        require(!_settlements[period].confirmed, "Oracle: already settled");
        require(
            principalApplied + interestApplied + escrowApplied + feesApplied <= amount,
            "Oracle: applied exceeds amount"
        );

        bytes32 digest = keccak256(abi.encode(
            "SETTLEMENT", period, amount,
            principalApplied, interestApplied, escrowApplied, feesApplied, delinquent
        ));
        if (_count(digest) < quorum) return;

        _settlements[period] = Settlement({
            confirmedAt: uint64(block.timestamp),
            amount: amount,
            principalApplied: principalApplied,
            interestApplied: interestApplied,
            escrowApplied: escrowApplied,
            feesApplied: feesApplied,
            confirmed: true,
            delinquent: delinquent
        });
        emit SettlementPublished(period, amount, principalApplied, delinquent);
    }

    /// @dev Tally one reporter's signature on a payload. Returns the count
    ///      including this one; a reporter can only be counted once.
    function _count(bytes32 digest) internal returns (uint256 n) {
        require(!attested[digest][msg.sender], "Oracle: already attested");
        attested[digest][msg.sender] = true;
        n = attestations[digest] + 1;
        attestations[digest] = n;
        emit Attested(digest, msg.sender, n);
    }

    // ─────────────────────────────────────────── Views ─────

    function statementOf(uint64 period) external view override returns (Statement memory) {
        return _statements[period];
    }

    function settlementOf(uint64 period) external view override returns (Settlement memory) {
        return _settlements[period];
    }

    /// @notice The whole bill for a period: principal + interest + escrow + fees.
    function totalDue(uint64 period) public view override returns (uint256) {
        Statement storage s = _statements[period];
        require(s.published, "Oracle: no statement");
        return s.principalDue + s.interestDue + s.escrowDue + s.feesDue;
    }

    /// @notice Whether the newest statement is recent enough to act on. A trust
    ///         that cannot see a fresh bill will not take a member's money.
    function isFresh() public view override returns (bool) {
        Statement storage s = _statements[latestPeriod];
        if (!s.published || frozen) return false;
        return block.timestamp <= uint256(s.asOf) + maxAge;
    }

    function isDelinquent(uint64 period) external view override returns (bool) {
        Settlement storage s = _settlements[period];
        if (s.confirmed) return s.delinquent;
        // Never settled and past due is delinquency by silence.
        Statement storage st = _statements[period];
        return st.published && block.timestamp > st.dueBy;
    }

    /// @notice Principal still owed on the loan, as of the last posting.
    function principalBalance() external view override returns (uint256) {
        return _statements[latestPeriod].principalBalance;
    }

    function reporterCount() external view returns (uint256) { return reporters.length; }

    // ──────────────────────────────── Bank administration ──

    function setReporter(address reporter, bool active) external onlyBank {
        require(reporter != address(0), "Oracle: zero reporter");
        if (active) {
            require(!isReporter[reporter], "Oracle: already a reporter");
            isReporter[reporter] = true;
            reporters.push(reporter);
        } else {
            require(isReporter[reporter], "Oracle: not a reporter");
            isReporter[reporter] = false;
            for (uint256 i = 0; i < reporters.length; i++) {
                if (reporters[i] == reporter) {
                    reporters[i] = reporters[reporters.length - 1];
                    reporters.pop();
                    break;
                }
            }
            require(reporters.length >= quorum, "Oracle: quorum unreachable");
        }
        emit ReporterSet(reporter, active);
    }

    function setQuorum(uint8 q) external onlyBank {
        require(q > 0 && q <= reporters.length, "Oracle: bad quorum");
        quorum = q;
        emit QuorumSet(q);
    }

    function setMaxAge(uint64 age) external onlyBank {
        require(age > 0, "Oracle: zero max age");
        maxAge = age;
        emit MaxAgeSet(age);
    }

    /// @notice Stop the feed. Published periods stand — equity already minted
    ///         against them is not in question — but nothing new lands, and
    ///         `isFresh` goes false, which halts contributions in the trust.
    function setFrozen(bool f) external onlyBank {
        frozen = f;
        emit FrozenSet(f);
    }

    function setServicer(string calldata name, bytes32 ref) external onlyBank {
        servicer = name;
        loanRef = ref;
        emit ServicerSet(name, ref);
    }

    function nominateBank(address to) external onlyBank {
        require(to != address(0), "Oracle: zero bank");
        pendingBank = to;
        emit BankNominated(to);
    }

    function acceptBank() external {
        require(msg.sender == pendingBank, "Oracle: not nominated");
        emit BankChanged(bank, msg.sender);
        bank = msg.sender;
        pendingBank = address(0);
    }
}
