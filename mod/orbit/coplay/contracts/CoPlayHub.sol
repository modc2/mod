// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title CoPlayHub - Organize games, charge entry fees in crypto
 * @notice Players join games by paying an entry fee. The organizer receives
 *         funds minus an admin fee (0-10%) when the game completes.
 *         Admin can set the fee and approve/reject games if moderation is on.
 */
contract CoPlayHub {

    // ── State ────────────────────────────────────────────────────────

    address public admin;
    uint256 public adminFeeBps;          // basis points, 0-1000 (0%-10%)
    bool    public requireApproval;      // if true, new games need admin approval
    uint256 public accumulatedFees;      // admin fees waiting to be withdrawn
    uint256 public nextGameId;

    struct Game {
        uint256 id;
        address organizer;
        uint256 entryFee;                // wei per player
        uint256 maxPlayers;
        uint256 playerCount;
        uint256 pool;                    // total ETH held for this game
        Status  status;
        uint256 createdAt;
    }

    enum Status {
        PendingApproval,  // 0 - waiting for admin approval
        Open,             // 1 - accepting players
        Full,             // 2 - max players reached, still active
        Completed,        // 3 - organizer settled, funds released
        Cancelled         // 4 - organizer or admin cancelled, refunds available
    }

    mapping(uint256 => Game) public games;
    mapping(uint256 => address[]) public gamePlayers;
    mapping(uint256 => mapping(address => bool)) public isPlayer;
    mapping(uint256 => mapping(address => bool)) public hasRefunded;

    // ── Events ───────────────────────────────────────────────────────

    event GameCreated(uint256 indexed gameId, address indexed organizer, uint256 entryFee, uint256 maxPlayers);
    event GameApproved(uint256 indexed gameId);
    event GameRejected(uint256 indexed gameId);
    event PlayerJoined(uint256 indexed gameId, address indexed player, uint256 amount);
    event GameCompleted(uint256 indexed gameId, uint256 organizerPayout, uint256 adminFee);
    event GameCancelled(uint256 indexed gameId);
    event PlayerRefunded(uint256 indexed gameId, address indexed player, uint256 amount);
    event AdminFeeUpdated(uint256 newFeeBps);
    event AdminTransferred(address indexed oldAdmin, address indexed newAdmin);
    event FeesWithdrawn(address indexed admin, uint256 amount);
    event ApprovalToggled(bool requireApproval);

    // ── Modifiers ────────────────────────────────────────────────────

    modifier onlyAdmin() {
        require(msg.sender == admin, "Only admin");
        _;
    }

    modifier gameExists(uint256 gameId) {
        require(gameId < nextGameId, "Game does not exist");
        _;
    }

    // ── Constructor ──────────────────────────────────────────────────

    constructor(uint256 _adminFeeBps) {
        require(_adminFeeBps <= 1000, "Fee cannot exceed 10%");
        admin = msg.sender;
        adminFeeBps = _adminFeeBps;
        requireApproval = false;
        nextGameId = 0;
    }

    // ── Game Lifecycle ───────────────────────────────────────────────

    /**
     * @notice Create a new game. Entry fee is in ETH (wei).
     * @param entryFee   Wei required per player to join
     * @param maxPlayers Maximum number of players (0 = unlimited)
     */
    function createGame(uint256 entryFee, uint256 maxPlayers) external returns (uint256 gameId) {
        gameId = nextGameId++;

        Status initialStatus = requireApproval ? Status.PendingApproval : Status.Open;

        games[gameId] = Game({
            id: gameId,
            organizer: msg.sender,
            entryFee: entryFee,
            maxPlayers: maxPlayers,
            playerCount: 0,
            pool: 0,
            status: initialStatus,
            createdAt: block.timestamp
        });

        emit GameCreated(gameId, msg.sender, entryFee, maxPlayers);
    }

    /**
     * @notice Join a game by paying the entry fee.
     */
    function joinGame(uint256 gameId) external payable gameExists(gameId) {
        Game storage game = games[gameId];
        require(game.status == Status.Open || game.status == Status.Full, "Game not open");
        require(game.status == Status.Open, "Game is full");
        require(!isPlayer[gameId][msg.sender], "Already joined");
        require(msg.value == game.entryFee, "Wrong entry fee");
        if (game.maxPlayers > 0) {
            require(game.playerCount < game.maxPlayers, "Game full");
        }

        isPlayer[gameId][msg.sender] = true;
        gamePlayers[gameId].push(msg.sender);
        game.playerCount++;
        game.pool += msg.value;

        // Auto-set to Full when max reached
        if (game.maxPlayers > 0 && game.playerCount >= game.maxPlayers) {
            game.status = Status.Full;
        }

        emit PlayerJoined(gameId, msg.sender, msg.value);
    }

    /**
     * @notice Organizer completes the game. Funds released minus admin fee.
     */
    function completeGame(uint256 gameId) external gameExists(gameId) {
        Game storage game = games[gameId];
        require(msg.sender == game.organizer, "Only organizer");
        require(game.status == Status.Open || game.status == Status.Full, "Cannot complete");

        uint256 pool = game.pool;
        uint256 fee = (pool * adminFeeBps) / 10000;
        uint256 payout = pool - fee;

        game.pool = 0;
        game.status = Status.Completed;
        accumulatedFees += fee;

        if (payout > 0) {
            (bool ok,) = payable(game.organizer).call{value: payout}("");
            require(ok, "Payout failed");
        }

        emit GameCompleted(gameId, payout, fee);
    }

    /**
     * @notice Organizer cancels the game. Players can then claim refunds.
     */
    function cancelGame(uint256 gameId) external gameExists(gameId) {
        Game storage game = games[gameId];
        require(
            msg.sender == game.organizer || msg.sender == admin,
            "Only organizer or admin"
        );
        require(
            game.status == Status.Open ||
            game.status == Status.Full ||
            game.status == Status.PendingApproval,
            "Cannot cancel"
        );

        game.status = Status.Cancelled;
        emit GameCancelled(gameId);
    }

    /**
     * @notice Player claims refund after game cancellation.
     */
    function claimRefund(uint256 gameId) external gameExists(gameId) {
        Game storage game = games[gameId];
        require(game.status == Status.Cancelled, "Game not cancelled");
        require(isPlayer[gameId][msg.sender], "Not a player");
        require(!hasRefunded[gameId][msg.sender], "Already refunded");

        hasRefunded[gameId][msg.sender] = true;
        uint256 refund = game.entryFee;
        game.pool -= refund;

        (bool ok,) = payable(msg.sender).call{value: refund}("");
        require(ok, "Refund failed");

        emit PlayerRefunded(gameId, msg.sender, refund);
    }

    // ── Admin: Moderation ────────────────────────────────────────────

    /**
     * @notice Approve a pending game (only when requireApproval is on).
     */
    function approveGame(uint256 gameId) external onlyAdmin gameExists(gameId) {
        Game storage game = games[gameId];
        require(game.status == Status.PendingApproval, "Not pending");
        game.status = Status.Open;
        emit GameApproved(gameId);
    }

    /**
     * @notice Reject a pending game.
     */
    function rejectGame(uint256 gameId) external onlyAdmin gameExists(gameId) {
        Game storage game = games[gameId];
        require(game.status == Status.PendingApproval, "Not pending");
        game.status = Status.Cancelled;
        emit GameRejected(gameId);
    }

    // ── Admin: Configuration ─────────────────────────────────────────

    /**
     * @notice Set admin fee in basis points. Max 1000 (10%), min 0.
     */
    function setAdminFee(uint256 _feeBps) external onlyAdmin {
        require(_feeBps <= 1000, "Fee cannot exceed 10%");
        adminFeeBps = _feeBps;
        emit AdminFeeUpdated(_feeBps);
    }

    /**
     * @notice Toggle whether new games require admin approval.
     */
    function setRequireApproval(bool _require) external onlyAdmin {
        requireApproval = _require;
        emit ApprovalToggled(_require);
    }

    /**
     * @notice Transfer admin role.
     */
    function transferAdmin(address newAdmin) external onlyAdmin {
        require(newAdmin != address(0), "Invalid address");
        address old = admin;
        admin = newAdmin;
        emit AdminTransferred(old, newAdmin);
    }

    /**
     * @notice Withdraw accumulated admin fees.
     */
    function withdrawFees() external onlyAdmin {
        uint256 amount = accumulatedFees;
        require(amount > 0, "No fees to withdraw");
        accumulatedFees = 0;

        (bool ok,) = payable(admin).call{value: amount}("");
        require(ok, "Withdraw failed");

        emit FeesWithdrawn(admin, amount);
    }

    // ── Views ────────────────────────────────────────────────────────

    function getGame(uint256 gameId) external view gameExists(gameId) returns (Game memory) {
        return games[gameId];
    }

    function getPlayers(uint256 gameId) external view gameExists(gameId) returns (address[] memory) {
        return gamePlayers[gameId];
    }

    function getGameCount() external view returns (uint256) {
        return nextGameId;
    }

    function getPlayerCount(uint256 gameId) external view gameExists(gameId) returns (uint256) {
        return games[gameId].playerCount;
    }
}
