//! MCP (Model Context Protocol) tool server — the mod-protocol fn surface,
//! spoken as JSON-RPC 2.0 so any MCP client (Claude, IDEs, agent frameworks)
//! can drive Hyperliquid without a bespoke SDK.
//!
//! Transports: Streamable HTTP at `POST /mcp` (one request → one JSON
//! response, no SSE) and stdio (`hyperliquid-api --stdio`) for clients that
//! only speak stdio.
//!
//! **One schema, two protocols.** Every tool in `TOOLS` names the mod-protocol
//! `fn` it fronts (the same names config.json lists and `mod.py::forward`
//! dispatches) plus the REST method/path that fn calls. `GET /mcp/schema`
//! publishes that mapping, and a unit test asserts every `mod_fn` really is in
//! config.json's `fns` — so the MCP tool list can't drift from the module's
//! protocol surface.
//!
//! **Auth by re-entry.** A tool call is executed as a loopback HTTP request
//! against this same server, forwarding the caller's `Authorization` header.
//! The auth guard therefore sees MCP traffic exactly like browser traffic:
//! public reads stay open, user-scoped routes need a mod protocol token, and
//! the eoa/follower/owner identity binding applies unchanged. The MCP layer
//! holds no authority of its own — it cannot become a way around the gate.

use serde_json::{json, Map, Value};
use std::sync::OnceLock;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const SERVER_NAME: &str = "hyperliquid";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Echo the client's protocol version when we implement it; otherwise pin the
/// oldest revision whose feature set (plain-JSON Streamable HTTP, tools) is
/// fully covered here.
pub const SUPPORTED_PROTOCOL_VERSIONS: [&str; 3] = ["2025-06-18", "2025-03-26", "2024-11-05"];
pub const DEFAULT_PROTOCOL_VERSION: &str = "2025-03-26";

const INSTRUCTIONS: &str = "\
Hyperliquid perps/spot: market data, trader analytics, copy-trade follows and \
baskets (indexes/strats), vaults, and full order + transfer execution. Market \
data, leaderboards, trader analysis, vaults and index reads are public. \
Anything wallet-scoped (follows, signals, signer, trading, transfers, live \
engine) needs a mod protocol token as `Authorization: Bearer <token>`, and any \
`eoa` you pass must be that token's own address. Orders are signed server-side \
by a per-wallet agent key the master wallet must `approveAgent` first — check \
hl_agent_status, and use hl_approve_agent_intent to get the payload to sign.";

// ─── Tool registry — the mod-protocol fn surface, as MCP tools ──────────

pub struct Tool {
    pub name: &'static str,
    /// mod-protocol fn this tool fronts (config.json `fns`, `mod.py::forward`).
    pub mod_fn: &'static str,
    pub method: &'static str,
    /// REST path; `{param}` segments are filled from the tool arguments.
    pub path: &'static str,
    /// True when the auth guard serves this route without a token.
    pub public: bool,
    pub description: &'static str,
    pub schema: Value,
}

fn p(kind: &str, desc: &str) -> Value {
    json!({ "type": kind, "description": desc })
}

fn arr(item_kind: &str, desc: &str) -> Value {
    json!({ "type": "array", "items": { "type": item_kind }, "description": desc })
}

fn eoa() -> Value {
    p("string", "your wallet address — must match the bearer token's address")
}

fn vault_opt() -> Value {
    p("string", "act as this vault instead of your own account (optional)")
}

fn tool(
    name: &'static str,
    mod_fn: &'static str,
    method: &'static str,
    path: &'static str,
    public: bool,
    description: &'static str,
    props: Vec<(&'static str, Value)>,
    required: &[&str],
) -> Tool {
    let mut map = Map::new();
    for (k, v) in props {
        map.insert(k.to_string(), v);
    }
    Tool {
        name,
        mod_fn,
        method,
        path,
        public,
        description,
        schema: json!({
            "type": "object",
            "properties": Value::Object(map),
            "required": required,
        }),
    }
}

pub fn tools() -> &'static [Tool] {
    static TOOLS: OnceLock<Vec<Tool>> = OnceLock::new();
    TOOLS.get_or_init(|| vec![
        // ── module / market data (public) ──
        tool("hl_status", "status", "GET", "/status", true,
            "Module status: mainnet/testnet, stored index and follow counts.",
            vec![], &[]),
        tool("hl_mids", "mids", "GET", "/mids", true,
            "Live mid price for every Hyperliquid market, keyed by coin \
             (spot keys look like `@1`, perp-dex keys carry `:`).",
            vec![], &[]),
        tool("hl_meta", "meta", "GET", "/market/meta", true,
            "Perp universe metadata plus per-asset context: size decimals, max \
             leverage, funding, open interest, mark/oracle price.",
            vec![], &[]),
        tool("hl_orderbook", "orderbook", "GET", "/orderbook/{coin}", true,
            "L2 order book snapshot (bids and asks with size and order count).",
            vec![("coin", p("string", "market symbol, e.g. BTC or ETH"))], &["coin"]),
        tool("hl_candles", "candles", "GET", "/candles/{coin}", true,
            "OHLCV candles for the last `hours` hours at `interval` resolution.",
            vec![
                ("coin", p("string", "market symbol, e.g. BTC")),
                ("interval", p("string", "candle size: 1m 5m 15m 1h 4h 1d (default 1h)")),
                ("hours", p("integer", "lookback window in hours (default 24)")),
            ], &["coin"]),
        tool("hl_wallet_config", "wallet_config", "GET", "/wallet/config", true,
            "Chain constants for wallet flows: chain id, USDC and bridge \
             addresses, RPC/explorer URLs, minimum deposit and withdrawal fee.",
            vec![], &[]),

        // ── account data (public reads on any address) ──
        tool("hl_user_state", "user_state", "GET", "/user/{address}/state", true,
            "Perp account state for an address: margin summary, account value, \
             withdrawable, and every open position with entry, size and unrealized PnL.",
            vec![("address", p("string", "0x wallet address"))], &["address"]),
        tool("hl_user_fills", "user_fills", "GET", "/user/{address}/fills", true,
            "Recent fills for an address: coin, side, price, size, fee, closed PnL.",
            vec![("address", p("string", "0x wallet address"))], &["address"]),
        tool("hl_user_pnl", "user_pnl", "GET", "/user/{address}/pnl", true,
            "Portfolio history for an address (PnL and account value series over \
             day/week/month/allTime windows).",
            vec![("address", p("string", "0x wallet address"))], &["address"]),
        tool("hl_user_orders", "user_orders", "GET", "/user/{address}/orders", true,
            "Open orders for an address: oid, coin, side, limit price, size.",
            vec![("address", p("string", "0x wallet address"))], &["address"]),
        tool("hl_user_funding", "user_funding", "GET", "/user/{address}/funding", true,
            "Funding payment history for an address.",
            vec![("address", p("string", "0x wallet address"))], &["address"]),

        // ── trader analytics (public) ──
        tool("hl_top_traders", "top_traders", "GET", "/traders/top", true,
            "Ranked board of the best-performing wallets over the window, from a \
             full leaderboard scrape: ROI, PnL, volume, and (for the top rows) \
             win rate, sharpe and trade count. Served from a cache refreshed in \
             the background; `updated_at` says how fresh it is. Standard windows \
             (days 1/7/30) return in milliseconds.",
            vec![
                ("days", p("integer", "window in days, 1-90 (default 7); 1/7/30 are precomputed")),
                ("pool", p("integer", "how many ranked traders to return, 1-1500 (default 150)")),
                ("min_per_day", p("number", "minimum trades per day to qualify (default 1)")),
                ("seed", arr("string", "extra wallet addresses to force into the board")),
            ], &[]),
        tool("hl_analyze_trader", "analyze_trader", "GET", "/trader/{address}/analyze", true,
            "Deep analysis of one wallet over `days`: PnL, ROI, win rate, sharpe, \
             volume, per-coin breakdown, open positions, raw fills and the \
             portfolio PnL history behind the chart.",
            vec![
                ("address", p("string", "0x wallet address")),
                ("days", p("integer", "lookback window in days, 1-90 (default 7)")),
            ], &["address"]),
        tool("hl_leaderboard", "leaderboard", "GET", "/leaderboard", true,
            "Raw Hyperliquid leaderboard scrape (~39k accounts) with per-window \
             ROI/PnL/volume. Large — prefer hl_top_traders unless you need the \
             whole set.",
            vec![], &[]),

        // ── indexes / strats ──
        tool("hl_list_indexes", "list_indexes", "GET", "/indexes", true,
            "All saved indexes (a.k.a. strats): weighted baskets of traders to \
             mirror, with legs, window and any linked vault.",
            vec![], &[]),
        tool("hl_get_index", "get_index", "GET", "/indexes/{id}", true,
            "One index by id.",
            vec![("id", p("string", "index id"))], &["id"]),
        tool("hl_index_perf", "index_perf", "GET", "/indexes/{id}/perf", true,
            "Weighted performance of an index: per-leg and blended PnL/ROI over \
             the window.",
            vec![
                ("id", p("string", "index id")),
                ("days", p("integer", "override the index's own window, in days")),
            ], &["id"]),
        tool("hl_create_index", "create_index", "POST", "/indexes", false,
            "Create an index: a named basket of trader legs with weights. \
             `owner` must be your own address.",
            vec![
                ("name", p("string", "display name")),
                ("owner", eoa()),
                ("description", p("string", "optional blurb")),
                ("legs", json!({
                    "type": "array",
                    "description": "basket legs: [{address, weight}] — weights are normalised across legs",
                    "items": {"type": "object", "properties": {
                        "address": {"type": "string"},
                        "weight": {"type": "number"}
                    }, "required": ["address", "weight"]}
                })),
                ("days_window", p("integer", "performance window in days, 1-90 (default 7)")),
                ("max_leverage", p("number", "leverage cap, 0 = uncapped")),
                ("notional_pct", p("number", "share of capital to deploy, 0-100 (default 50)")),
                ("vault_address", p("string", "vault backing this index (optional)")),
            ], &["name", "owner", "legs"]),
        tool("hl_update_index", "update_index", "PATCH", "/indexes/{id}", false,
            "Edit an index you own — send only the fields you want changed.",
            vec![
                ("id", p("string", "index id")),
                ("name", p("string", "new name")),
                ("description", p("string", "new description")),
                ("legs", json!({
                    "type": "array",
                    "description": "replacement legs: [{address, weight}]",
                    "items": {"type": "object", "properties": {
                        "address": {"type": "string"},
                        "weight": {"type": "number"}
                    }, "required": ["address", "weight"]}
                })),
                ("days_window", p("integer", "performance window in days, 1-90")),
                ("max_leverage", p("number", "leverage cap, 0 = uncapped")),
                ("notional_pct", p("number", "share of capital to deploy, 0-100")),
                ("vault_address", p("string", "vault backing this index")),
            ], &["id"]),
        tool("hl_delete_index", "delete_index", "DELETE", "/indexes/{id}", false,
            "Delete an index you own.",
            vec![("id", p("string", "index id"))], &["id"]),
        tool("hl_auto_index", "auto_index", "POST", "/indexes/auto", false,
            "Propose index legs automatically from the top traders of the \
             window — returns candidate wallets and normalised weights to review \
             before saving with hl_create_index.",
            vec![
                ("days", p("integer", "window in days (default 7)")),
                ("top", p("integer", "how many traders to include, 1-50 (default 10)")),
                ("pool", p("integer", "candidate pool to rank within (default 150)")),
                ("min_per_day", p("number", "minimum trades per day to qualify (default 1)")),
            ], &[]),

        // ── vaults ──
        tool("hl_list_vaults", "list_vaults", "GET", "/vaults", true,
            "Open Hyperliquid vaults ranked by APR (child vaults and dust filtered out).",
            vec![
                ("pool", p("integer", "how many vaults to return, 1-2000 (default 300)")),
                ("min_tvl", p("number", "minimum TVL in USD (default 10000)")),
            ], &[]),
        tool("hl_vault_details", "vault_details", "GET", "/vaults/{address}", true,
            "Vault profile: leader, TVL, APR, lockup, portfolio history, and — \
             when `user` is given — that follower's stake and max withdrawable.",
            vec![
                ("address", p("string", "vault address")),
                ("user", p("string", "look up this wallet's follower state (optional)")),
            ], &["address"]),
        tool("hl_vault_perf", "vault_perf", "GET", "/vaults/{address}/perf", true,
            "Vault historical PnL series.",
            vec![("address", p("string", "vault address"))], &["address"]),
        tool("hl_vault_intent", "vault_intent", "POST", "/indexes/{index_id}/vault/intent", false,
            "Build the unsigned `createVault` action for an index you own, ready \
             for the owner wallet to sign and relay.",
            vec![
                ("index_id", p("string", "index id")),
                ("initial_usd", p("number", "initial vault funding in USDC")),
                ("nonce", p("integer", "override the nonce (defaults to now, ms)")),
            ], &["index_id", "initial_usd"]),
        tool("hl_create_vault", "create_vault", "POST", "/create_vault", false,
            "Create a Hyperliquid vault, signed by your backend agent key. \
             Requires an approved agent and enough USDC for `initial_usd`.",
            vec![
                ("eoa", eoa()),
                ("name", p("string", "vault name")),
                ("initial_usd", p("number", "initial funding in USDC (HL minimum is 100)")),
                ("description", p("string", "vault description")),
            ], &["eoa", "name", "initial_usd"]),
        tool("hl_vault_transfer", "vault_transfer", "POST", "/vault_transfer", false,
            "Deposit into or withdraw from a vault (agent-signed). Withdrawals \
             are blocked while the vault's lockup is active.",
            vec![
                ("eoa", eoa()),
                ("vault", p("string", "vault address")),
                ("is_deposit", p("boolean", "true = deposit, false = withdraw")),
                ("amount_usd", p("number", "USDC amount")),
            ], &["eoa", "vault", "is_deposit", "amount_usd"]),

        // ── copy-trade follows / signals ──
        tool("hl_list_follows", "list_follows", "GET", "/follows", false,
            "Your copy-trade follows: leader, size %, caps, coin filters, paused \
             state. `follower` must be your own address.",
            vec![("follower", eoa())], &["follower"]),
        tool("hl_create_follow", "create_follow", "POST", "/follows", false,
            "Follow a leader wallet: mirror their fills at `size_pct` of your \
             account, optionally capped per trade and filtered by coin.",
            vec![
                ("follower", eoa()),
                ("leader", p("string", "wallet address to copy")),
                ("size_pct", p("number", "percent of your account per mirrored trade, 0-100 (default 10)")),
                ("max_per_trade_usd", p("number", "hard USD cap per trade, 0 = none")),
                ("coins_allow", arr("string", "only mirror these coins (empty = all)")),
                ("coins_deny", arr("string", "never mirror these coins")),
                ("vault_address", vault_opt()),
            ], &["follower", "leader"]),
        tool("hl_update_follow", "update_follow", "PATCH", "/follows/{id}", false,
            "Edit one of your follows — send only the fields you want changed.",
            vec![
                ("id", p("string", "follow id")),
                ("size_pct", p("number", "percent of your account per mirrored trade, 0-100")),
                ("max_per_trade_usd", p("number", "hard USD cap per trade, 0 = none")),
                ("coins_allow", arr("string", "only mirror these coins")),
                ("coins_deny", arr("string", "never mirror these coins")),
                ("paused", p("boolean", "pause or resume mirroring")),
                ("vault_address", vault_opt()),
            ], &["id"]),
        tool("hl_delete_follow", "delete_follow", "DELETE", "/follows/{id}", false,
            "Delete one of your follows.",
            vec![("id", p("string", "follow id"))], &["id"]),
        tool("hl_pause_follow", "pause_follow", "POST", "/follows/{id}/pause", false,
            "Pause mirroring for one of your follows (config is kept).",
            vec![("id", p("string", "follow id"))], &["id"]),
        tool("hl_resume_follow", "resume_follow", "POST", "/follows/{id}/resume", false,
            "Resume a paused follow.",
            vec![("id", p("string", "follow id"))], &["id"]),
        tool("hl_list_signals", "list_signals", "GET", "/signals", false,
            "Recent copy-trade signals generated for you: leader fill, mirrored \
             size, status. `follower` must be your own address.",
            vec![
                ("follower", eoa()),
                ("limit", p("integer", "how many signals, 1-500 (default 100)")),
            ], &["follower"]),

        // ── backend agent signer ──
        tool("hl_signer_address", "signer_address", "POST", "/signer/address", false,
            "Get (creating on first call) the backend agent address that signs \
             orders for your wallet. Your master wallet must approve it before \
             any trade can go through.",
            vec![("eoa", eoa())], &["eoa"]),
        tool("hl_agent_status", "agent_status", "GET", "/agent/status", false,
            "Is your backend agent approved on Hyperliquid? Returns the agent \
             address, an `approved` flag and the raw extraAgents list. Check \
             this before trading.",
            vec![("eoa", eoa())], &["eoa"]),
        tool("hl_approve_agent_intent", "approve_agent_intent", "POST", "/signer/approve_agent", false,
            "Build the `approveAgent` action + EIP-712 typed data your master \
             wallet must sign (on Arbitrum) to authorize the backend agent. \
             Sign it in a wallet, then relay the signature to /exchange/relay — \
             this server never holds your master key.",
            vec![
                ("eoa", eoa()),
                ("agent_name", p("string", "label for the agent (optional)")),
            ], &["eoa"]),

        // ── trading (agent-signed) ──
        tool("hl_trade", "trade", "POST", "/trade", false,
            "Place an order for your wallet, signed by your approved agent key. \
             Omit `price` for a slippage-padded IOC market order; include it for \
             a limit order. `size` is in base units (coin amount), not USD.",
            vec![
                ("eoa", eoa()),
                ("coin", p("string", "market symbol, e.g. BTC")),
                ("is_buy", p("boolean", "true = buy/long, false = sell/short")),
                ("size", p("number", "order size in base units (coin amount)")),
                ("price", p("number", "limit price; omit for a market order")),
                ("tif", p("string", "time in force for limit orders: Gtc, Ioc or Alo")),
                ("reduce_only", p("boolean", "only reduce an existing position")),
                ("slippage_bps", p("integer", "market-order slippage padding in bps (default 100)")),
                ("cloid", p("string", "client order id, 0x + 32 hex chars")),
                ("vault_address", vault_opt()),
            ], &["eoa", "coin", "is_buy", "size"]),
        tool("hl_cancel", "cancel", "POST", "/cancel", false,
            "Cancel resting orders by exchange order id.",
            vec![
                ("eoa", eoa()),
                ("cancels", json!({
                    "type": "array",
                    "description": "orders to cancel: [{coin, oid}]",
                    "items": {"type": "object", "properties": {
                        "coin": {"type": "string"},
                        "oid": {"type": "integer"}
                    }, "required": ["coin", "oid"]}
                })),
                ("vault_address", vault_opt()),
            ], &["eoa", "cancels"]),
        tool("hl_cancel_by_cloid", "cancel_by_cloid", "POST", "/cancel_by_cloid", false,
            "Cancel resting orders by client order id.",
            vec![
                ("eoa", eoa()),
                ("cancels", json!({
                    "type": "array",
                    "description": "orders to cancel: [{coin, cloid}]",
                    "items": {"type": "object", "properties": {
                        "coin": {"type": "string"},
                        "cloid": {"type": "string"}
                    }, "required": ["coin", "cloid"]}
                })),
                ("vault_address", vault_opt()),
            ], &["eoa", "cancels"]),
        tool("hl_modify", "modify", "POST", "/modify", false,
            "Replace a resting order in place with new price/size.",
            vec![
                ("eoa", eoa()),
                ("oid", p("integer", "order id to modify")),
                ("coin", p("string", "market symbol")),
                ("is_buy", p("boolean", "true = buy, false = sell")),
                ("price", p("number", "new limit price")),
                ("size", p("number", "new size in base units")),
                ("reduce_only", p("boolean", "only reduce an existing position")),
                ("tif", p("string", "Gtc, Ioc or Alo")),
                ("vault_address", vault_opt()),
            ], &["eoa", "oid", "coin", "is_buy", "price", "size"]),
        tool("hl_set_leverage", "set_leverage", "POST", "/leverage", false,
            "Set leverage for a coin, cross or isolated.",
            vec![
                ("eoa", eoa()),
                ("coin", p("string", "market symbol")),
                ("leverage", p("integer", "leverage multiple, within the asset's max")),
                ("is_cross", p("boolean", "true = cross margin (default), false = isolated")),
                ("vault_address", vault_opt()),
            ], &["eoa", "coin", "leverage"]),
        tool("hl_update_isolated_margin", "update_isolated_margin", "POST", "/isolated_margin", false,
            "Add (positive) or remove (negative) isolated margin on an open position.",
            vec![
                ("eoa", eoa()),
                ("coin", p("string", "market symbol")),
                ("is_buy", p("boolean", "true if the position is long")),
                ("amount_usd", p("number", "USDC to add (positive) or remove (negative)")),
                ("vault_address", vault_opt()),
            ], &["eoa", "coin", "is_buy", "amount_usd"]),
        tool("hl_schedule_cancel", "schedule_cancel", "POST", "/schedule_cancel", false,
            "Dead-man's switch: schedule cancellation of all your orders at \
             `time_ms` unless it is pushed back. Omit `time_ms` to clear it.",
            vec![
                ("eoa", eoa()),
                ("time_ms", p("integer", "unix ms at which to cancel everything")),
                ("vault_address", vault_opt()),
            ], &["eoa"]),
        tool("hl_action", "action", "POST", "/action", false,
            "Escape hatch: sign an arbitrary Hyperliquid L1 action with your \
             agent key and post it to /exchange. Key order in `action` matters — \
             HL hashes the msgpack encoding.",
            vec![
                ("eoa", eoa()),
                ("action", json!({"type": "object", "description": "raw L1 action JSON, e.g. {\"type\":\"approveBuilderFee\", ...}"})),
                ("nonce", p("integer", "override the nonce (defaults to now, ms)")),
                ("vault_address", vault_opt()),
            ], &["eoa", "action"]),

        // ── transfers ──
        tool("hl_usd_class_transfer", "usd_class_transfer", "POST", "/usd_class_transfer", false,
            "Move USDC between your perp and spot wallets. Hyperliquid requires \
             a master-wallet signature for this class of action — if the agent \
             signature is rejected, build the payload with the wallet flow instead.",
            vec![
                ("eoa", eoa()),
                ("amount", p("string", "USDC amount as a string, e.g. \"10.5\"")),
                ("to_perp", p("boolean", "true = spot → perp, false = perp → spot")),
            ], &["eoa", "amount", "to_perp"]),
        tool("hl_withdraw", "withdraw", "POST", "/withdraw", false,
            "Withdraw USDC from Hyperliquid to an Arbitrum address (master-wallet \
             signed class; $1 fee). Funds leave the exchange — confirm the \
             destination before calling.",
            vec![
                ("eoa", eoa()),
                ("destination", p("string", "0x address to receive USDC on Arbitrum")),
                ("amount", p("string", "USDC amount as a string")),
            ], &["eoa", "destination", "amount"]),
        tool("hl_usd_send", "usd_send", "POST", "/usd_send", false,
            "Send USDC to another Hyperliquid account (stays on HL).",
            vec![
                ("eoa", eoa()),
                ("destination", p("string", "recipient 0x address")),
                ("amount", p("string", "USDC amount as a string")),
            ], &["eoa", "destination", "amount"]),
        tool("hl_spot_send", "spot_send", "POST", "/spot_send", false,
            "Send a spot token to another Hyperliquid account.",
            vec![
                ("eoa", eoa()),
                ("destination", p("string", "recipient 0x address")),
                ("token", p("string", "token identifier, 'SYMBOL:0x<tokenId>'")),
                ("amount", p("string", "amount as a string")),
            ], &["eoa", "destination", "token", "amount"]),
        tool("hl_set_referrer", "set_referrer", "POST", "/set_referrer", false,
            "Set your referral code (one time, per account).",
            vec![
                ("eoa", eoa()),
                ("code", p("string", "referral code")),
            ], &["eoa", "code"]),

        // ── live copy-trade engine ──
        tool("hl_live_start", "live_start", "POST", "/live/start", false,
            "Start an autonomous copy-trade session: poll the given traders and \
             mirror their fills onto your account with your agent key. Trades \
             real money until hl_live_stop — confirm sizing first.",
            vec![
                ("eoa", eoa()),
                ("traders", json!({
                    "type": "array",
                    "description": "wallets to mirror: [{address, weight, enabled}] (weight defaults 1, enabled true)",
                    "items": {"type": "object", "properties": {
                        "address": {"type": "string"},
                        "weight": {"type": "number"},
                        "enabled": {"type": "boolean"}
                    }, "required": ["address"]}
                })),
                ("size_pct", p("number", "percent of capital per mirrored trade (default 10)")),
                ("capital", p("number", "capital base in USD; 0 = use live account value")),
                ("interval_ms", p("integer", "poll interval in ms, minimum 2000 (default 15000)")),
                ("min_order_size_usd", p("number", "skip mirrored orders below this notional (default 10)")),
                ("max_per_trade_usd", p("number", "hard USD cap per trade, 0 = none")),
                ("max_slippage_bps", p("integer", "market-order slippage cap in bps (default 100)")),
                ("coins_allow", arr("string", "only mirror these coins (empty = all)")),
                ("coins_deny", arr("string", "never mirror these coins")),
                ("strategy_id", p("string", "tag fills with this strategy id (optional)")),
                ("vault_address", vault_opt()),
            ], &["eoa", "traders"]),
        tool("hl_live_stop", "live_stop", "POST", "/live/stop", false,
            "Stop your live copy-trade session. Open positions are left as they \
             are — close them yourself if you want flat.",
            vec![("eoa", eoa())], &["eoa"]),
        tool("hl_live_status", "live_status", "GET", "/live/status", false,
            "Live session state for your wallet: config, running flag, last \
             poll, mirrored trade counts and errors.",
            vec![("eoa", eoa())], &["eoa"]),
    ])
}

fn find_tool(name: &str) -> Option<&'static Tool> {
    tools().iter().find(|t| t.name == name)
}

/// MCP `tools/list` payload.
pub fn tool_list() -> Value {
    Value::Array(tools().iter().map(|t| json!({
        "name": t.name,
        "description": t.description,
        "inputSchema": t.schema,
    })).collect())
}

/// `GET /mcp/schema` — the MCP tool surface *and* its mod-protocol mapping,
/// so a mod client can see which fn and REST route each tool speaks for.
pub fn schema_doc(testnet: bool) -> Value {
    json!({
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol": "mod",
        "testnet": testnet,
        "mcp": {
            "transport": "streamable-http",
            "endpoint": "/mcp",
            "stdio": "hyperliquid-api --stdio",
            "protocolVersion": DEFAULT_PROTOCOL_VERSION,
            "supportedVersions": SUPPORTED_PROTOCOL_VERSIONS,
            "auth": "Authorization: Bearer <mod protocol token> — required for non-public tools",
            "instructions": INSTRUCTIONS,
        },
        "tools": Value::Array(tools().iter().map(|t| json!({
            "name": t.name,
            "fn": t.mod_fn,
            "method": t.method,
            "path": t.path,
            "public": t.public,
            "description": t.description,
            "inputSchema": t.schema,
        })).collect()),
    })
}

// ─── Tool execution — loopback through this server's own REST surface ───

fn client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            // A cold /traders/top scan can run minutes behind HL's rate limits.
            .timeout(std::time::Duration::from_secs(300))
            .build()
            .expect("mcp loopback client")
    })
}

/// Render a query/path scalar. Arrays become comma-joined lists, which is what
/// the query-parsing routes (`seed`) expect.
fn scalar(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Array(items) => items.iter().map(scalar).collect::<Vec<_>>().join(","),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// Split arguments into the path substitutions the route needs and whatever
/// is left over (query for GET/DELETE, JSON body otherwise).
fn build_url(base: &str, t: &Tool, args: &Value) -> Result<(String, Map<String, Value>), String> {
    let mut rest: Map<String, Value> = match args {
        Value::Object(m) => m.clone(),
        Value::Null => Map::new(),
        _ => return Err("arguments must be an object".into()),
    };
    let mut path = String::new();
    for segment in t.path.split('/') {
        if segment.is_empty() {
            continue;
        }
        path.push('/');
        if let Some(key) = segment.strip_prefix('{').and_then(|s| s.strip_suffix('}')) {
            let v = rest
                .remove(key)
                .ok_or_else(|| format!("{} requires `{key}`", t.name))?;
            let s = scalar(&v);
            if s.is_empty() {
                return Err(format!("{} requires a non-empty `{key}`", t.name));
            }
            path.push_str(s.trim());
        } else {
            path.push_str(segment);
        }
    }

    // Enforce the schema's own required list before spending a round trip.
    if let Some(req) = t.schema.get("required").and_then(|r| r.as_array()) {
        for k in req.iter().filter_map(|k| k.as_str()) {
            let from_path = t.path.contains(&format!("{{{k}}}"));
            if !from_path && !rest.contains_key(k) {
                return Err(format!("{} requires `{k}`", t.name));
            }
        }
    }

    let mut url = format!("{}{}", base.trim_end_matches('/'), path);
    if matches!(t.method, "GET" | "DELETE") && !rest.is_empty() {
        let qs: Vec<String> = rest
            .iter()
            .filter(|(_, v)| !v.is_null())
            .map(|(k, v)| format!("{}={}", k, urlencode(&scalar(v))))
            .collect();
        if !qs.is_empty() {
            url.push('?');
            url.push_str(&qs.join("&"));
        }
        rest.clear();
    }
    Ok((url, rest))
}

/// Percent-encode everything outside the unreserved set — enough for the
/// addresses, symbols and numbers these routes take.
fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' | b',' => {
                (b as char).to_string()
            }
            _ => format!("%{b:02X}"),
        })
        .collect()
}

/// Execute one tool: a loopback HTTP call against this same API, carrying the
/// caller's Authorization header so the auth guard rules apply unchanged.
pub async fn call_tool(
    base: &str,
    name: &str,
    args: &Value,
    authorization: Option<&str>,
) -> Result<Value, String> {
    let t = find_tool(name).ok_or_else(|| format!("unknown tool: {name}"))?;
    let (url, body) = build_url(base, t, args)?;

    let mut req = match t.method {
        "GET" => client().get(&url),
        "POST" => client().post(&url).json(&Value::Object(body)),
        "PATCH" => client().patch(&url).json(&Value::Object(body)),
        "DELETE" => client().delete(&url),
        m => return Err(format!("unsupported method {m}")),
    };
    if let Some(a) = authorization {
        req = req.header(axum::http::header::AUTHORIZATION, a);
    }

    let resp = req.send().await.map_err(|e| format!("{name} failed: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    let value: Value = serde_json::from_str(&text).unwrap_or(Value::String(text));

    if status.is_success() {
        return Ok(value);
    }
    let detail = value
        .get("detail")
        .or_else(|| value.get("error"))
        .map(scalar)
        .unwrap_or_else(|| value.to_string());
    if status.as_u16() == 401 {
        // The guard's own wording already explains the token; just say where
        // an MCP client puts it.
        return Err(format!(
            "{name} failed (401): {detail} (POST /mcp forwards the same \
             Authorization header)"
        ));
    }
    Err(format!("{name} failed ({}): {detail}", status.as_u16()))
}

// ─── JSON-RPC 2.0 dispatch ─────────────────────────────────────────────

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

pub fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// Handle one JSON-RPC message. `None` = notification, nothing to reply with.
pub async fn handle_message(
    base: &str,
    msg: &Value,
    authorization: Option<&str>,
) -> Option<Value> {
    let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let params = msg.get("params").cloned().unwrap_or(json!({}));
    let id = match msg.get("id") {
        Some(id) if !id.is_null() => id.clone(),
        _ => return None,
    };

    Some(match method {
        "initialize" => {
            let asked = params.get("protocolVersion").and_then(|v| v.as_str()).unwrap_or("");
            let version = if SUPPORTED_PROTOCOL_VERSIONS.contains(&asked) {
                asked
            } else {
                DEFAULT_PROTOCOL_VERSION
            };
            rpc_result(id, json!({
                "protocolVersion": version,
                "capabilities": { "tools": {} },
                "serverInfo": { "name": SERVER_NAME, "version": SERVER_VERSION },
                "instructions": INSTRUCTIONS,
            }))
        }
        "ping" => rpc_result(id, json!({})),
        "tools/list" => rpc_result(id, json!({ "tools": tool_list() })),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or(json!({}));
            // Tool failures are *successful* JSON-RPC responses carrying
            // isError, per spec — the client model reads the text and retries.
            match call_tool(base, name, &args, authorization).await {
                Ok(v) => rpc_result(id, json!({
                    "content": [{ "type": "text", "text": serde_json::to_string_pretty(&v).unwrap_or_default() }],
                    "structuredContent": if v.is_object() { v } else { json!({ "result": v }) },
                    "isError": false,
                })),
                Err(e) => rpc_result(id, json!({
                    "content": [{ "type": "text", "text": e }],
                    "isError": true,
                })),
            }
        }
        "resources/list" => rpc_result(id, json!({ "resources": [] })),
        "prompts/list" => rpc_result(id, json!({ "prompts": [] })),
        _ => rpc_error(id, -32601, &format!("method not found: {method}")),
    })
}

/// stdio transport: newline-delimited JSON-RPC on stdin/stdout, proxying to a
/// running API (`HL_API_URL`, default the local port). Register with e.g.
/// `claude mcp add hyperliquid -- /path/to/hyperliquid-api --stdio`.
/// Set `HYPERLIQUID_TOKEN` to authorize wallet-scoped tools.
pub async fn run_stdio(base: String) {
    let auth = std::env::var("HYPERLIQUID_TOKEN")
        .ok()
        .filter(|t| !t.is_empty())
        .map(|t| if t.starts_with("Bearer ") { t } else { format!("Bearer {t}") });

    let stdin = BufReader::new(tokio::io::stdin());
    let mut stdout = tokio::io::stdout();
    let mut lines = stdin.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                let err = rpc_error(Value::Null, -32700, &format!("parse error: {e}"));
                let _ = stdout.write_all(format!("{err}\n").as_bytes()).await;
                let _ = stdout.flush().await;
                continue;
            }
        };
        if let Some(resp) = handle_message(&base, &msg, auth.as_deref()).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// config.json is the module's mod-protocol schema; the MCP tool table
    /// must front fns that actually exist there.
    const CONFIG_JSON: &str = include_str!("../../../config.json");

    #[test]
    fn every_tool_fronts_a_mod_protocol_fn() {
        let cfg: Value = serde_json::from_str(CONFIG_JSON).unwrap();
        let fns: Vec<&str> = cfg["fns"].as_array().unwrap()
            .iter().filter_map(|f| f.as_str()).collect();
        for t in tools() {
            assert!(fns.contains(&t.mod_fn),
                "tool {} fronts fn `{}` which config.json does not declare", t.name, t.mod_fn);
        }
    }

    #[test]
    fn tool_names_are_unique_and_prefixed() {
        let mut seen = std::collections::HashSet::new();
        for t in tools() {
            assert!(t.name.starts_with("hl_"), "{} is missing the hl_ prefix", t.name);
            assert!(seen.insert(t.name), "duplicate tool name {}", t.name);
        }
    }

    #[test]
    fn path_params_are_declared_and_required() {
        for t in tools() {
            for seg in t.path.split('/') {
                if let Some(key) = seg.strip_prefix('{').and_then(|s| s.strip_suffix('}')) {
                    let props = &t.schema["properties"];
                    assert!(props.get(key).is_some(),
                        "{}: path param `{key}` is not in the input schema", t.name);
                    let req = t.schema["required"].as_array().unwrap();
                    assert!(req.iter().any(|r| r.as_str() == Some(key)),
                        "{}: path param `{key}` must be required", t.name);
                }
            }
        }
    }

    #[test]
    fn schemas_describe_every_property() {
        for t in tools() {
            let props = t.schema["properties"].as_object().unwrap();
            for (k, v) in props {
                assert!(v.get("type").is_some(), "{}.{k} has no type", t.name);
                assert!(v.get("description").is_some() || v.get("items").is_some(),
                    "{}.{k} has no description", t.name);
            }
            for r in t.schema["required"].as_array().unwrap() {
                let k = r.as_str().unwrap();
                assert!(props.contains_key(k), "{}: required `{k}` is not a property", t.name);
            }
        }
    }

    #[test]
    fn urls_substitute_path_params_and_route_the_rest() {
        // GET: leftovers become the query string.
        let args = json!({ "coin": "BTC", "interval": "1h", "hours": 6 });
        let t = find_tool("hl_candles").unwrap();
        let (url, body) = build_url("http://127.0.0.1:8919", t, &args).unwrap();
        assert!(url.starts_with("http://127.0.0.1:8919/candles/BTC?"), "{url}");
        assert!(url.contains("interval=1h") && url.contains("hours=6"), "{url}");
        assert!(body.is_empty());

        // POST: leftovers become the JSON body.
        let t = find_tool("hl_vault_intent").unwrap();
        let (url, body) = build_url("http://127.0.0.1:8919", t,
            &json!({ "index_id": "abc", "initial_usd": 100.0 })).unwrap();
        assert_eq!(url, "http://127.0.0.1:8919/indexes/abc/vault/intent");
        assert_eq!(body["initial_usd"], json!(100.0));

        // Arrays flatten to the comma-separated form the routes parse.
        let t = find_tool("hl_top_traders").unwrap();
        let (url, _) = build_url("http://x", t, &json!({ "seed": ["0xa", "0xb"] })).unwrap();
        assert!(url.ends_with("/traders/top?seed=0xa,0xb"), "{url}");

        // Missing path param fails before any network call.
        let t = find_tool("hl_orderbook").unwrap();
        assert!(build_url("http://x", t, &json!({})).is_err());
    }

    #[test]
    fn public_flag_matches_the_auth_guard() {
        // The guard is the authority; this catches a tool advertising open
        // access that the gate would in fact refuse (and vice versa).
        for t in tools() {
            let probe = t.path.replace("{coin}", "BTC")
                .replace("{address}", "0x0000000000000000000000000000000000000000")
                .replace("{id}", "x").replace("{index_id}", "x");
            let method = axum::http::Method::from_bytes(t.method.as_bytes()).unwrap();
            assert_eq!(crate::auth::is_public(&method, &probe), t.public,
                "{}: public={} disagrees with the auth guard for {} {}",
                t.name, t.public, t.method, probe);
        }
    }
}
