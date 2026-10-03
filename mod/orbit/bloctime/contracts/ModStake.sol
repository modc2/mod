// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

interface IBlocTimeCurve {
    function getMultiplier(uint256 blockCount) external view returns (uint256);
    function fundPot(uint256 amount) external;
}

/**
 * @title ModStake
 * @dev Registry priority bought with locked BLOC, and a court that can take it away.
 *
 *      BACKING. Anyone can back a module by locking BLOC against its name. The
 *      lock length runs through BlocTime's own multiplier curve, so a long
 *      commitment counts for more than a large one — a module's priority on the
 *      registry is the sum of the time-weighted conviction behind it. Backers
 *      get their principal back when the lock expires, minus anything the court
 *      has taken in the meantime. That is the whole point: to lift a module you
 *      must put capital behind it that a slash can reach.
 *
 *      THE COURT. A module that turns malicious can be slashed, but only the
 *      slow way. A juror must escrow BLOC here BEFORE a case opens to vote on
 *      it, so nobody borrows a verdict. A case needs a proposer with standing,
 *      a bond that is burnt to the reward pot if the case fails, a third of the
 *      circulating supply to show up, a two-thirds majority, and then a
 *      timelock before the slash lands. Each of those is survivable alone;
 *      together they make an executed slash a rare event, which is what a
 *      slash should be. The ones that do land are kept forever in `slashEvents`.
 *
 *      Slashing is O(1) for any number of backers: each module carries a
 *      `slashIndex` and every backing is denominated in shares of it, so a
 *      verdict re-prices the whole pool in one multiplication.
 */
contract ModStake is ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    uint256 public constant ONE = 1e18;
    uint256 public constant BPS = 10000;
    address public constant BURN = 0x000000000000000000000000000000000000dEaD;

    // ── Modules ─────────────────────────────────────────────────

    struct ModRecord {
        bytes32 id;
        string name;
        address maintainer;
        uint64 registeredAt;
        uint256 stakeShares;   // shares of the backing pool
        uint256 weightShares;  // shares of the time-weighted conviction
        uint256 slashIndex;    // ONE = never slashed; scales every share down
        uint256 totalSlashed;  // BLOC taken by the court, all time
        uint32 backings;       // open backings
        uint32 slashCount;
        bool banned;           // fully slashed — no new backing, no priority
    }

    struct Backing {
        uint256 id;
        bytes32 modId;
        address backer;
        uint256 principal;     // BLOC in at open, before any slash
        uint256 multiplier;    // bps, from BlocTime's curve at `lockBlocks`
        uint256 stakeShares;
        uint256 weightShares;
        uint256 startBlock;
        uint256 lockBlocks;
        bool closed;
    }

    IERC20 public immutable bloc;
    IBlocTimeCurve public immutable blocTime;

    bytes32[] public modIds;
    mapping(bytes32 => ModRecord) public mods;
    mapping(bytes32 => bool) public modExists;

    Backing[] public backings;
    mapping(address => uint256[]) public backerBackings;
    mapping(bytes32 => uint256[]) public modBackings;

    /// @dev Backing pool + jury escrow, tracked apart so neither can spend the
    ///      other. The court only ever moves BLOC it can name.
    uint256 public totalBackedPool;
    uint256 public totalJuryPool;

    // ── The court ───────────────────────────────────────────────

    enum State { None, Active, Defeated, Queued, Executed, Expired }

    struct Proposal {
        uint256 id;
        bytes32 modId;
        address proposer;
        uint256 slashBps;
        uint256 bond;
        uint64 start;
        uint64 voteEnd;
        uint64 eta;             // earliest execution, set when queued
        uint256 forVotes;
        uint256 againstVotes;
        uint256 eligibleSupply; // quorum denominator, snapshotted at open
        State state;
        string evidence;
    }

    struct SlashEvent {
        uint256 proposalId;
        bytes32 modId;
        uint256 slashBps;
        uint256 amount;
        uint256 forVotes;
        uint256 againstVotes;
        address proposer;
        uint64 time;
    }

    Proposal[] public proposals;
    SlashEvent[] public slashEvents;
    mapping(bytes32 => uint256) public openProposal;   // modId => proposalId + 1
    mapping(bytes32 => uint64) public cooldownUntil;   // modId => no new cases before
    mapping(uint256 => mapping(address => uint8)) public voteOf; // 0 none, 1 for, 2 against
    mapping(uint256 => mapping(address => uint256)) public voteWeight;

    mapping(address => uint256) public juryDeposit;
    mapping(address => uint64) public jurySince;       // last time the deposit grew
    mapping(address => uint64) public juryLockedUntil;

    uint256 public defeatedCount;

    struct Court {
        uint256 votingPeriod;      // seconds a case stays open
        uint256 executionDelay;    // timelock between verdict and slash
        uint256 gracePeriod;       // window to execute before the verdict lapses
        uint256 cooldown;          // per-module quiet period after any verdict
        uint256 quorumBps;         // share of circulating BLOC that must vote
        uint256 supermajorityBps;  // share of votes cast that must be FOR
        uint256 proposalBond;      // BLOC staked on the accusation
        uint256 proposalThreshold; // jury deposit a proposer must already hold
        uint256 maxSlashBps;       // ceiling on a single verdict
        uint256 bountyBps;         // slice of a slash paid to the proposer
        uint256 burnBps;           // slice burnt; the rest goes to the pot
    }

    Court public court;

    // ── Events ──────────────────────────────────────────────────

    event ModRegistered(bytes32 indexed modId, string name, address indexed maintainer);
    event MaintainerTransferred(bytes32 indexed modId, address indexed from, address indexed to);
    event Backed(bytes32 indexed modId, address indexed backer, uint256 backingId, uint256 amount, uint256 lockBlocks, uint256 weight);
    event Unbacked(bytes32 indexed modId, address indexed backer, uint256 backingId, uint256 returned, uint256 lost);
    event JuryJoined(address indexed juror, uint256 amount, uint256 deposit);
    event JuryLeft(address indexed juror, uint256 amount, uint256 deposit);
    event SlashProposed(uint256 indexed proposalId, bytes32 indexed modId, address indexed proposer, uint256 slashBps, string evidence);
    event VoteCast(uint256 indexed proposalId, address indexed juror, bool support, uint256 weight);
    event ProposalQueued(uint256 indexed proposalId, uint64 eta);
    event ProposalDefeated(uint256 indexed proposalId, bytes32 indexed modId, uint256 forVotes, uint256 againstVotes, uint256 bondForfeited);
    event SlashExecuted(uint256 indexed proposalId, bytes32 indexed modId, uint256 amount, uint256 slashBps, uint256 bounty, uint256 burned, uint256 toPot);
    event ModBanned(bytes32 indexed modId);
    event CourtParamsUpdated();

    constructor(address _bloc, Court memory _court) Ownable(msg.sender) {
        require(_bloc != address(0), "Zero token");
        bloc = IERC20(_bloc);
        blocTime = IBlocTimeCurve(_bloc);
        _setCourt(_court);
    }

    // ── Module registry ─────────────────────────────────────────

    /// @notice Canonical id for a module name. Names are lowercase so that one
    ///         module cannot be split across two spellings of its own name.
    function modId(string memory name) public pure returns (bytes32) {
        bytes memory b = bytes(name);
        require(b.length > 0 && b.length <= 64, "Name 1-64 chars");
        for (uint256 i = 0; i < b.length; i++) {
            uint8 c = uint8(b[i]);
            bool ok = (c >= 0x61 && c <= 0x7A)   // a-z
                || (c >= 0x30 && c <= 0x39)      // 0-9
                || c == 0x2D || c == 0x5F || c == 0x2E; // - _ .
            require(ok, "Name must be [a-z0-9._-]");
        }
        return keccak256(b);
    }

    function registerMod(string calldata name) public returns (bytes32 id) {
        id = modId(name);
        if (modExists[id]) return id;
        modExists[id] = true;
        modIds.push(id);
        ModRecord storage mr = mods[id];
        mr.id = id;
        mr.name = name;
        mr.maintainer = msg.sender;
        mr.registeredAt = uint64(block.timestamp);
        mr.slashIndex = ONE;
        emit ModRegistered(id, name, msg.sender);
    }

    function transferMaintainer(bytes32 id, address to) external {
        ModRecord storage mr = mods[id];
        require(mr.maintainer == msg.sender, "Not maintainer");
        require(to != address(0), "Zero address");
        emit MaintainerTransferred(id, msg.sender, to);
        mr.maintainer = to;
    }

    // ── Backing ─────────────────────────────────────────────────

    /// @notice Lock BLOC behind a module name, registering it if it is new.
    function backNamed(string calldata name, uint256 amount, uint256 lockBlocks)
        external nonReentrant returns (uint256)
    {
        return _back(registerMod(name), amount, lockBlocks);
    }

    function back(bytes32 id, uint256 amount, uint256 lockBlocks)
        external nonReentrant returns (uint256)
    {
        require(modExists[id], "Unknown module");
        return _back(id, amount, lockBlocks);
    }

    function _back(bytes32 id, uint256 amount, uint256 lockBlocks) internal returns (uint256 backingId) {
        require(amount > 0, "Amount > 0");
        ModRecord storage mr = mods[id];
        require(!mr.banned, "Module banned");

        bloc.safeTransferFrom(msg.sender, address(this), amount);
        totalBackedPool += amount;

        uint256 mult = _multiplier(lockBlocks);
        uint256 weight = (amount * mult) / BPS;
        uint256 sShares = (amount * ONE) / mr.slashIndex;
        uint256 wShares = (weight * ONE) / mr.slashIndex;

        mr.stakeShares += sShares;
        mr.weightShares += wShares;
        mr.backings += 1;

        backingId = backings.length;
        backings.push(Backing({
            id: backingId,
            modId: id,
            backer: msg.sender,
            principal: amount,
            multiplier: mult,
            stakeShares: sShares,
            weightShares: wShares,
            startBlock: block.number,
            lockBlocks: lockBlocks,
            closed: false
        }));
        backerBackings[msg.sender].push(backingId);
        modBackings[id].push(backingId);

        emit Backed(id, msg.sender, backingId, amount, lockBlocks, weight);
    }

    /// @dev BlocTime's curve is the one source of truth for what a lock is
    ///      worth. An instance that predates `getMultiplier` scores everything
    ///      at 1x rather than reverting the stake.
    function _multiplier(uint256 lockBlocks) internal view returns (uint256) {
        try blocTime.getMultiplier(lockBlocks) returns (uint256 mult) {
            return mult < BPS ? BPS : mult;
        } catch {
            return BPS;
        }
    }

    /// @notice Withdraw a backing once its lock has elapsed. What comes back is
    ///         the principal re-priced by every slash the module took while the
    ///         BLOC was committed.
    function unback(uint256 backingId) external nonReentrant {
        Backing storage b = backings[backingId];
        require(b.backer == msg.sender, "Not yours");
        require(!b.closed, "Closed");
        require(block.number >= b.startBlock + b.lockBlocks, "Still locked");

        ModRecord storage mr = mods[b.modId];
        uint256 payout = (b.stakeShares * mr.slashIndex) / ONE;
        uint256 lost = b.principal > payout ? b.principal - payout : 0;

        b.closed = true;
        mr.stakeShares -= b.stakeShares;
        mr.weightShares -= b.weightShares;
        mr.backings -= 1;

        if (payout > 0) {
            totalBackedPool -= payout;
            bloc.safeTransfer(msg.sender, payout);
        }
        emit Unbacked(b.modId, msg.sender, backingId, payout, lost);
    }

    // ── Jury ────────────────────────────────────────────────────

    /// @notice Escrow BLOC to sit on the jury. Deposits only count for cases
    ///         opened after they land, so a verdict cannot be bought at the
    ///         last second or borrowed for a block.
    function joinJury(uint256 amount) external nonReentrant {
        require(amount > 0, "Amount > 0");
        bloc.safeTransferFrom(msg.sender, address(this), amount);
        juryDeposit[msg.sender] += amount;
        totalJuryPool += amount;
        jurySince[msg.sender] = uint64(block.timestamp);
        emit JuryJoined(msg.sender, amount, juryDeposit[msg.sender]);
    }

    function leaveJury(uint256 amount) external nonReentrant {
        require(amount > 0 && amount <= juryDeposit[msg.sender], "Bad amount");
        require(block.timestamp >= juryLockedUntil[msg.sender], "Voted, still locked");
        juryDeposit[msg.sender] -= amount;
        totalJuryPool -= amount;
        bloc.safeTransfer(msg.sender, amount);
        emit JuryLeft(msg.sender, amount, juryDeposit[msg.sender]);
    }

    /// @notice A juror's weight in a case: their escrow, if it predates the case.
    function votingPower(address juror, uint256 proposalId) public view returns (uint256) {
        Proposal storage p = proposals[proposalId];
        if (jurySince[juror] > p.start) return 0;
        return juryDeposit[juror];
    }

    /// @notice BLOC the quorum is measured against — everything circulating,
    ///         which excludes the reward pot BlocTime custodies for itself.
    function eligibleSupply() public view returns (uint256) {
        uint256 supply = bloc.totalSupply();
        uint256 held = bloc.balanceOf(address(bloc));
        return supply > held ? supply - held : 0;
    }

    // ── Cases ───────────────────────────────────────────────────

    function propose(bytes32 id, uint256 slashBps, string calldata evidence)
        external nonReentrant returns (uint256 proposalId)
    {
        require(modExists[id], "Unknown module");
        ModRecord storage mr = mods[id];
        require(!mr.banned, "Already banned");
        require(slashBps > 0 && slashBps <= court.maxSlashBps, "Bad slash");
        require(openProposal[id] == 0, "Case already open");
        require(block.timestamp >= cooldownUntil[id], "Module in cooldown");
        require(bytes(evidence).length > 0, "Evidence required");
        require(juryDeposit[msg.sender] >= court.proposalThreshold, "Below threshold");

        // The bond rides on top of the jury escrow: an accusation costs BLOC
        // that a failed case does not give back.
        bloc.safeTransferFrom(msg.sender, address(this), court.proposalBond);

        proposalId = proposals.length;
        proposals.push(Proposal({
            id: proposalId,
            modId: id,
            proposer: msg.sender,
            slashBps: slashBps,
            bond: court.proposalBond,
            start: uint64(block.timestamp),
            voteEnd: uint64(block.timestamp + court.votingPeriod),
            eta: 0,
            forVotes: 0,
            againstVotes: 0,
            eligibleSupply: eligibleSupply(),
            state: State.Active,
            evidence: evidence
        }));
        openProposal[id] = proposalId + 1;
        emit SlashProposed(proposalId, id, msg.sender, slashBps, evidence);
    }

    function castVote(uint256 proposalId, bool support) external {
        Proposal storage p = proposals[proposalId];
        require(p.state == State.Active, "Not active");
        require(block.timestamp < p.voteEnd, "Voting closed");
        require(voteOf[proposalId][msg.sender] == 0, "Already voted");

        uint256 weight = votingPower(msg.sender, proposalId);
        require(weight > 0, "No standing");

        voteOf[proposalId][msg.sender] = support ? 1 : 2;
        voteWeight[proposalId][msg.sender] = weight;
        if (support) p.forVotes += weight;
        else p.againstVotes += weight;

        // Escrow stays put until the case closes, so one stack of BLOC cannot
        // be walked to a second address and voted twice.
        if (p.voteEnd > juryLockedUntil[msg.sender]) {
            juryLockedUntil[msg.sender] = p.voteEnd;
        }
        emit VoteCast(proposalId, msg.sender, support, weight);
    }

    function quorumVotes(uint256 proposalId) public view returns (uint256) {
        return (proposals[proposalId].eligibleSupply * court.quorumBps) / BPS;
    }

    function quorumReached(uint256 proposalId) public view returns (bool) {
        Proposal storage p = proposals[proposalId];
        return p.forVotes + p.againstVotes >= quorumVotes(proposalId);
    }

    function supermajorityReached(uint256 proposalId) public view returns (bool) {
        Proposal storage p = proposals[proposalId];
        uint256 cast = p.forVotes + p.againstVotes;
        if (cast == 0) return false;
        return p.forVotes * BPS >= cast * court.supermajorityBps;
    }

    /// @notice Close a case whose voting window has ended. A case that cleared
    ///         quorum and the supermajority is queued behind the timelock; any
    ///         other outcome is a defeat and the bond goes to the reward pot.
    function finalize(uint256 proposalId) external nonReentrant {
        Proposal storage p = proposals[proposalId];
        require(p.state == State.Active, "Not active");
        require(block.timestamp >= p.voteEnd, "Voting open");

        if (quorumReached(proposalId) && supermajorityReached(proposalId)) {
            p.state = State.Queued;
            p.eta = uint64(block.timestamp + court.executionDelay);
            emit ProposalQueued(proposalId, p.eta);
        } else {
            p.state = State.Defeated;
            openProposal[p.modId] = 0;
            cooldownUntil[p.modId] = uint64(block.timestamp + court.cooldown);
            defeatedCount += 1;
            uint256 bond = p.bond;
            p.bond = 0;
            _disburse(bond, 0, BPS); // the whole bond, burnt or potted
            emit ProposalDefeated(proposalId, p.modId, p.forVotes, p.againstVotes, bond);
        }
    }

    /// @notice Carry out a queued verdict. Re-prices every backing behind the
    ///         module in one multiplication and writes the event down forever.
    function execute(uint256 proposalId) external nonReentrant {
        Proposal storage p = proposals[proposalId];
        require(p.state == State.Queued, "Not queued");
        require(block.timestamp >= p.eta, "Timelocked");

        if (block.timestamp > p.eta + court.gracePeriod) {
            p.state = State.Expired;
            openProposal[p.modId] = 0;
            uint256 stale = p.bond;
            p.bond = 0;
            if (stale > 0) bloc.safeTransfer(p.proposer, stale);
            return;
        }

        ModRecord storage mr = mods[p.modId];
        uint256 pool = (mr.stakeShares * mr.slashIndex) / ONE;
        uint256 amount = (pool * p.slashBps) / BPS;

        mr.slashIndex = (mr.slashIndex * (BPS - p.slashBps)) / BPS;
        mr.slashCount += 1;
        mr.totalSlashed += amount;
        if (p.slashBps == BPS) {
            mr.banned = true;
            emit ModBanned(p.modId);
        }

        p.state = State.Executed;
        openProposal[p.modId] = 0;
        cooldownUntil[p.modId] = uint64(block.timestamp + court.cooldown);

        uint256 bounty;
        uint256 burned;
        uint256 toPot;
        if (amount > 0) {
            totalBackedPool -= amount;
            (bounty, burned, toPot) = _disburse(amount, court.bountyBps, court.burnBps);
            if (bounty > 0) bloc.safeTransfer(p.proposer, bounty);
        }

        uint256 bond = p.bond;
        p.bond = 0;
        if (bond > 0) bloc.safeTransfer(p.proposer, bond); // an upheld case gets its bond back

        slashEvents.push(SlashEvent({
            proposalId: proposalId,
            modId: p.modId,
            slashBps: p.slashBps,
            amount: amount,
            forVotes: p.forVotes,
            againstVotes: p.againstVotes,
            proposer: p.proposer,
            time: uint64(block.timestamp)
        }));
        emit SlashExecuted(proposalId, p.modId, amount, p.slashBps, bounty, burned, toPot);
    }

    /// @dev Split BLOC the court has taken. `bountyBps` is held back for the
    ///      caller to pay out; the rest is burnt or pushed into BlocTime's
    ///      weekly pot, where it reaches every holder. An instance without a
    ///      pot burns that share instead of stranding it here.
    function _disburse(uint256 amount, uint256 bountyBps, uint256 burnBps)
        internal returns (uint256 bounty, uint256 burned, uint256 toPot)
    {
        if (amount == 0) return (0, 0, 0);
        bounty = (amount * bountyBps) / BPS;
        burned = (amount * burnBps) / BPS;
        toPot = amount - bounty - burned;
        if (burned > 0) bloc.safeTransfer(BURN, burned);
        if (toPot > 0) {
            try blocTime.fundPot(toPot) {
                // fundPot pulls the BLOC itself via _transfer
            } catch {
                bloc.safeTransfer(BURN, toPot);
            }
        }
    }

    // ── Views ───────────────────────────────────────────────────

    /// @notice What the registry sorts on: time-weighted conviction, after slashes.
    function priorityOf(bytes32 id) public view returns (uint256) {
        ModRecord storage mr = mods[id];
        if (mr.banned) return 0;
        return (mr.weightShares * mr.slashIndex) / ONE;
    }

    function stakedOf(bytes32 id) public view returns (uint256) {
        ModRecord storage mr = mods[id];
        return (mr.stakeShares * mr.slashIndex) / ONE;
    }

    struct ModView {
        bytes32 id;
        string name;
        address maintainer;
        uint64 registeredAt;
        uint256 staked;
        uint256 priority;
        uint256 slashIndex;
        uint256 totalSlashed;
        uint32 backings;
        uint32 slashCount;
        bool banned;
        uint256 openCase;   // proposalId + 1, or 0
        uint64 cooldownUntil;
    }

    function modCount() external view returns (uint256) { return modIds.length; }
    function proposalCount() external view returns (uint256) { return proposals.length; }
    function slashEventCount() external view returns (uint256) { return slashEvents.length; }
    function backingCount() external view returns (uint256) { return backings.length; }

    function getMod(bytes32 id) public view returns (ModView memory) {
        ModRecord storage mr = mods[id];
        return ModView({
            id: mr.id,
            name: mr.name,
            maintainer: mr.maintainer,
            registeredAt: mr.registeredAt,
            staked: stakedOf(id),
            priority: priorityOf(id),
            slashIndex: mr.slashIndex,
            totalSlashed: mr.totalSlashed,
            backings: mr.backings,
            slashCount: mr.slashCount,
            banned: mr.banned,
            openCase: openProposal[id],
            cooldownUntil: cooldownUntil[id]
        });
    }

    function getMods(uint256 offset, uint256 limit) external view returns (ModView[] memory out) {
        uint256 n = modIds.length;
        if (offset >= n) return new ModView[](0);
        uint256 end = offset + limit;
        if (end > n || limit == 0) end = n;
        out = new ModView[](end - offset);
        for (uint256 i = offset; i < end; i++) {
            out[i - offset] = getMod(modIds[i]);
        }
    }

    /// @notice Ranking in two arrays — cheap enough to poll on every page load.
    function priorities() external view returns (bytes32[] memory ids, uint256[] memory ranks) {
        uint256 n = modIds.length;
        ids = new bytes32[](n);
        ranks = new uint256[](n);
        for (uint256 i = 0; i < n; i++) {
            ids[i] = modIds[i];
            ranks[i] = priorityOf(modIds[i]);
        }
    }

    function getBackings(address backer) external view returns (Backing[] memory out) {
        uint256[] storage ids = backerBackings[backer];
        out = new Backing[](ids.length);
        for (uint256 i = 0; i < ids.length; i++) out[i] = backings[ids[i]];
    }

    function getModBackings(bytes32 id) external view returns (Backing[] memory out) {
        uint256[] storage ids = modBackings[id];
        out = new Backing[](ids.length);
        for (uint256 i = 0; i < ids.length; i++) out[i] = backings[ids[i]];
    }

    function getProposals(uint256 offset, uint256 limit) external view returns (Proposal[] memory out) {
        uint256 n = proposals.length;
        if (offset >= n) return new Proposal[](0);
        uint256 end = offset + limit;
        if (end > n || limit == 0) end = n;
        out = new Proposal[](end - offset);
        for (uint256 i = offset; i < end; i++) out[i - offset] = proposals[i];
    }

    function getSlashEvents(uint256 offset, uint256 limit) external view returns (SlashEvent[] memory out) {
        uint256 n = slashEvents.length;
        if (offset >= n) return new SlashEvent[](0);
        uint256 end = offset + limit;
        if (end > n || limit == 0) end = n;
        out = new SlashEvent[](end - offset);
        for (uint256 i = offset; i < end; i++) out[i - offset] = slashEvents[i];
    }

    /// @notice How rare a slash actually is here: cases opened, cases upheld,
    ///         modules touched. The console reads its headline off this.
    function rarity() external view returns (
        uint256 modsRegistered, uint256 modsSlashed, uint256 casesOpened,
        uint256 casesUpheld, uint256 casesDefeated, uint256 blocSlashed, uint256 blocBacked
    ) {
        modsRegistered = modIds.length;
        for (uint256 i = 0; i < modIds.length; i++) {
            if (mods[modIds[i]].slashCount > 0) modsSlashed++;
        }
        casesOpened = proposals.length;
        casesUpheld = slashEvents.length;
        casesDefeated = defeatedCount;
        for (uint256 i = 0; i < slashEvents.length; i++) blocSlashed += slashEvents[i].amount;
        blocBacked = totalBackedPool;
    }

    /// @notice Live view of one case, including whether it would pass right now.
    function caseStatus(uint256 proposalId) external view returns (
        State state, uint256 forVotes, uint256 againstVotes, uint256 quorum,
        bool hasQuorum, bool hasMajority, uint64 voteEnd, uint64 eta, bool executable
    ) {
        Proposal storage p = proposals[proposalId];
        state = p.state;
        forVotes = p.forVotes;
        againstVotes = p.againstVotes;
        quorum = quorumVotes(proposalId);
        hasQuorum = quorumReached(proposalId);
        hasMajority = supermajorityReached(proposalId);
        voteEnd = p.voteEnd;
        eta = p.eta;
        executable = p.state == State.Queued
            && block.timestamp >= p.eta
            && block.timestamp <= p.eta + court.gracePeriod;
    }

    function getCourt() external view returns (Court memory) { return court; }

    // ── Owner ───────────────────────────────────────────────────

    function setCourt(Court calldata c) external onlyOwner { _setCourt(c); }

    function _setCourt(Court memory c) internal {
        require(c.votingPeriod > 0, "Voting period > 0");
        require(c.quorumBps > 0 && c.quorumBps <= BPS, "Bad quorum");
        require(c.supermajorityBps > 5000 && c.supermajorityBps <= BPS, "Majority > 50%");
        require(c.maxSlashBps > 0 && c.maxSlashBps <= BPS, "Bad max slash");
        require(c.bountyBps + c.burnBps <= BPS, "Split > 100%");
        court = c;
        emit CourtParamsUpdated();
    }
}
