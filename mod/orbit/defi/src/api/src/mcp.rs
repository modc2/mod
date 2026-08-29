//! MCP server (Streamable HTTP, JSON-RPC 2.0).
//!
//! Every tool re-enters the same logic the REST routes use and carries the
//! caller's own Authorization header, so an agent gets exactly the surface a
//! browser would — no second permission model to keep in sync.

use crate::{agentlink, auth, graph, storage, Shared};
use axum::{extract::State, http::HeaderMap, Json};

fn composer_and_desk_tools() -> serde_json::Value {
    serde_json::json!([
        {
            "name": "defi_catalog",
            "description": "[public] The reusable DeFi block catalog: every block, its typed input ports, what it provides, and its params.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "defi_block",
            "description": "[public] One block in full, including its Solidity source and compiled ABI/bytecode.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string", "description": "block id, e.g. 'vault'" } },
                "required": ["id"]
            }
        },
        {
            "name": "defi_templates",
            "description": "[public] Starter compositions — a vault+strategy, an AMM with liquidity mining, a governed money market.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "defi_validate",
            "description": "[public] Type-check a protocol graph: port types, required wires, constructor cycles. Returns issues and the deployment order.",
            "inputSchema": {
                "type": "object",
                "properties": { "graph": { "type": "object", "description": "{name, nodes[], edges[]}" } },
                "required": ["graph"]
            }
        },
        {
            "name": "defi_plan",
            "description": "[public] Compile a graph and return the ordered deployment plan: each deploy step with ABI, bytecode and resolved constructor args, then the post-deploy wiring calls.",
            "inputSchema": {
                "type": "object",
                "properties": { "graph": { "type": "object" } },
                "required": ["graph"]
            }
        },
        {
            "name": "defi_protocols",
            "description": "[public] Saved protocol designs on this node.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "defi_protocol",
            "description": "[public] One saved protocol, with its graph and any recorded deployments.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }
        },
        {
            "name": "defi_save",
            "description": "Save a protocol design. Requires a signed-in wallet.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": { "type": "string" },
                    "name": { "type": "string" },
                    "graph": { "type": "object" }
                },
                "required": ["graph"]
            }
        },
        {
            "name": "defi_publish",
            "description": "Content-address a saved protocol and return its CID for sharing. Requires a signed-in wallet.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }
        },
        {
            "name": "defi_import",
            "description": "Import a protocol someone shared by CID. Requires a signed-in wallet.",
            "inputSchema": {
                "type": "object",
                "properties": { "cid": { "type": "string" } },
                "required": ["cid"]
            }
        },
        {
            "name": "defi_prompts",
            "description": "[public] Browse the agent protocol's shared prompt library (served by the agent mod).",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "defi_prompt",
            "description": "[public] Retrieve one prompt from the agent protocol by id or CID.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }
        },
        {
            "name": "defi_dex_venues",
            "description": "[public] Where this desk can trade and what is behind each venue: Uniswap V3 on Ethereum and Base (the eth mod signs), every Solana DEX through Jupiter (the solana mod signs), and the dTAO subnet pools on Bittensor (the bt mod signs). Pass check=true to find out which of those modules is actually up right now. Start here.",
            "inputSchema": {
                "type": "object",
                "properties": { "check": { "type": "boolean", "description": "ping each backing module instead of just listing them" } }
            }
        },
        {
            "name": "defi_dex_tokens",
            "description": "[public] The tokens each venue knows by name, so you can say USDC instead of an address. Anything not listed is still tradable — pass a contract address (EVM), a mint or symbol (Solana), or SN<netuid> (Bittensor).",
            "inputSchema": {
                "type": "object",
                "properties": { "chain": { "type": "string", "description": "ethereum | base | solana | tao — omit for all of them" } }
            }
        },
        {
            "name": "defi_dex_quote",
            "description": "[public] What a trade would actually get you, priced against live liquidity: the best Uniswap V3 fee tier (or a route through wrapped native) on Ethereum/Base, Jupiter's best route on Solana, the subnet pool's constant product on Bittensor. Returns the expected output, the minimum after slippage, price impact and the route. Free, signs nothing — read this before defi_dex_swap.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain": { "type": "string", "description": "ethereum | base | solana | tao (also sepolia, base-sepolia to rehearse)" },
                    "sell": { "type": "string", "description": "what you are giving up — a symbol, an address/mint, or TAO" },
                    "buy": { "type": "string", "description": "what you want — a symbol, an address/mint, or SN<netuid> on Bittensor" },
                    "amount": { "type": ["string", "number"], "description": "how much of `sell` to sell, in whole units (\"1.5\", not wei)" },
                    "slippageBps": { "type": "number", "description": "slippage tolerance in basis points (default 50 = 0.5%)" },
                    "auth": { "type": "string", "description": "bearer token for the chain module, if it differs from yours" }
                },
                "required": ["chain", "sell", "buy", "amount"]
            }
        },
        {
            "name": "defi_dex_swap",
            "description": "TRADE, for real, on the DEXes of Solana, Ethereum, Base and Bittensor. Quotes first, then executes through the module that holds the key: eth_write on Uniswap V3 SwapRouter02 (approving the token first if it has to), sol_swap through Jupiter, bt_buy/bt_sell into a dTAO subnet pool. This module holds no keys and adds no credential — your Authorization header, or auth=, is what reaches the chain module, and its own guards still apply. On a mainnet venue it returns needs_confirm with the quote until you pass confirm=true. dryRun=true prices the trade and stops.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain": { "type": "string", "description": "ethereum | base | solana | tao" },
                    "sell": { "type": "string", "description": "what you are giving up" },
                    "buy": { "type": "string", "description": "what you want" },
                    "amount": { "type": ["string", "number"], "description": "how much of `sell` to sell, in whole units" },
                    "account": { "type": "string", "description": "who signs: an eth-module account name (required on EVM), a solana keystore wallet, or a bittensor coldkey" },
                    "hotkey": { "type": "string", "description": "Bittensor hotkey, if not 'default'" },
                    "password": { "type": "string", "description": "EVM keystore password, if the account is not already unlocked with eth_unlock" },
                    "slippageBps": { "type": "number", "description": "slippage tolerance in basis points (default 50)" },
                    "confirm": { "type": "boolean", "description": "yes, actually trade it — required on every mainnet venue" },
                    "dryRun": { "type": "boolean", "description": "price it and stop" },
                    "auth": { "type": "string", "description": "bearer token for the chain module, if it differs from yours" }
                },
                "required": ["chain", "sell", "buy", "amount"]
            }
        },
        {
            "name": "defi_dex_balances",
            "description": "[public] What a wallet holds on one of these chains, read through that chain's own module — eth_portfolio, sol_portfolio, or bt_portfolio for staked subnet positions. The other half of a trade: what you have to trade with.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain": { "type": "string", "description": "ethereum | base | solana | tao" },
                    "address": { "type": "string", "description": "the address, or an account/wallet name the chain module knows" },
                    "hotkey": { "type": "string", "description": "Bittensor hotkey, if not 'default'" },
                    "auth": { "type": "string", "description": "bearer token for the chain module, if it differs from yours" }
                },
                "required": ["chain"]
            }
        },
        {
            "name": "defi_compose",
            "description": "Describe a protocol in words and get back a validated graph, composed by the agent mod out of catalog blocks.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": { "type": "string" },
                    "promptId": { "type": "string", "description": "optional agent-library prompt to frame the request" }
                },
                "required": ["prompt"]
            }
        }
    ])
}

/// The yields table and the treasury. Split out of the list above only because
/// one `json!` literal of thirty-one tools blows the macro's recursion limit —
/// the surface is one list to a client.
fn yield_and_treasury_tools() -> serde_json::Value {
    serde_json::json!([
        {
            "name": "defi_yields",
            "description": "[public] The live APR of every DeFi pool worth looking at, from DefiLlama's yields index (~17k pools, hourly). apy_base is the part that comes from fees and apy_reward the part that comes from token emissions, kept apart so a farm cannot pass for a yield; apy_mean_30d and apy_change_7d say how the rate has actually behaved. Filter by chain/project/symbol/q, min_tvl (default 100k), stable, organic. sort: apy | tvl | base | mean30d | score (depth-adjusted — the honest ranking for money that intends to sit somewhere). tradable_on names the chain this desk could trade the pool on, or null.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain": { "type": "string", "description": "e.g. Ethereum, Base, Solana — see defi_yields_facets" },
                    "project": { "type": "string", "description": "e.g. aave-v3, lido" },
                    "symbol": { "type": "string", "description": "substring of the pool symbol, e.g. USDC" },
                    "q": { "type": "string", "description": "free-text over project, chain, symbol and pool meta" },
                    "min_tvl": { "type": "number", "description": "USD floor, default 100000" },
                    "max_apy": { "type": "number", "description": "cap, for hiding rates that are too good to be a plan" },
                    "stable": { "type": "boolean" },
                    "organic": { "type": "boolean", "description": "only pools where most of the rate is fees, not emissions" },
                    "outliers": { "type": "boolean", "description": "include what the index itself flags as statistically odd" },
                    "sort": { "type": "string" },
                    "limit": { "type": "number" }
                }
            }
        },
        {
            "name": "defi_yield_protocols",
            "description": "[public] The same index collapsed to one row per protocol — the APR for each DeFi protocol rather than each pool. The headline apy is TVL-weighted across the protocol's pools, because its best pool is a marketing number and its mean is dragged down by dust. Takes the same filters as defi_yields.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain": { "type": "string" }, "symbol": { "type": "string" },
                    "q": { "type": "string" }, "min_tvl": { "type": "number" },
                    "stable": { "type": "boolean" }, "organic": { "type": "boolean" },
                    "sort": { "type": "string", "description": "tvl (default) | apy | best | pools" },
                    "limit": { "type": "number" }
                }
            }
        },
        {
            "name": "defi_yield_pool",
            "description": "[public] One pool in full, plus up to a year of its APY and TVL history — which is what turns a headline rate into a decision.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": { "type": "string", "description": "the pool id from defi_yields" },
                    "history": { "type": "boolean", "description": "default true" }
                },
                "required": ["id"]
            }
        },
        {
            "name": "defi_yields_facets",
            "description": "[public] Which chains and projects exist in the index right now, with pool counts and TVL, for building a filter without hard-coding a list that goes stale.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "defi_treasury",
            "description": "[public] The treasury desk: every allocation on this node, the next four payout windows, the local BLOC watch list, and — when a ModBlocTimeTreasury is bound — its live on-chain state. An allocation is a PLAN until its status is 'locked'; the response says so rather than blurring the two.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "defi_treasury_schedule",
            "description": "[public] The next N weekly windows — Friday 12:00 EST, BlocTime's clock, not one of ours — and what each would release from the ledger. Principal released is exact arithmetic; the yield line extrapolates the APY at the time of choosing and is labelled projected.",
            "inputSchema": {
                "type": "object",
                "properties": { "weeks": { "type": "number", "description": "default 8, max 52" } }
            }
        },
        {
            "name": "defi_treasury_holders",
            "description": "[public] Who the payout splits across and how much BLOC each holds, read live from the bloctime module. Shares are of the watched set, not of total supply — the same rule the contract's registered set follows — and the uncovered supply is reported so the difference is visible.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "defi_treasury_preview",
            "description": "[public] Next Friday in full: what goes into the pot, and what each BLOC holder would get out of it. Read this before locking anything.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "defi_treasury_choose",
            "description": "Record a choice off the yields table as an allocation: which pool, how much, for how many weeks, and whether the principal streams out weekly (return_principal false) or sits escrowed while only the yield is shared (true). The APY and TVL you pass are frozen into the row as the numbers the decision was made on. This writes the ledger only — it moves no money; call defi_treasury_lock to make it real. Requires a signed-in wallet.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pool": { "type": "string", "description": "the DefiLlama pool id from defi_yields" },
                    "project": { "type": "string" },
                    "chain": { "type": "string" },
                    "symbol": { "type": "string" },
                    "apy": { "type": "number", "description": "the APY at the moment of choosing" },
                    "apy_base": { "type": "number" },
                    "tvl_usd": { "type": "number" },
                    "amount": { "type": "string", "description": "whole units of the asset, as a decimal string" },
                    "asset": { "type": "string", "description": "symbol of what is actually being locked" },
                    "asset_address": { "type": "string", "description": "its ERC20 address, needed before it can be locked on chain" },
                    "term_weeks": { "type": "number", "description": "number of weekly payouts, default 4" },
                    "return_principal": { "type": "boolean", "description": "escrow the principal and share only the yield" },
                    "note": { "type": "string" },
                    "id": { "type": "string", "description": "pass an existing id to edit a plan; a locked one cannot be edited" }
                },
                "required": ["amount"]
            }
        },
        {
            "name": "defi_treasury_bind",
            "description": "Point this node at a deployed ModBlocTimeTreasury, so the desk reads the chain instead of only its own ledger. Deploy the 'treasury' block first (defi_plan, then your wallet signs). The first signed-in wallet may bind an unbound node; after that only the binder or the module owner can repoint it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": { "type": "string" },
                    "network": { "type": "string", "description": "an eth-module network, default base-sepolia" },
                    "asset": { "type": "string", "description": "the ERC20 the treasury distributes" },
                    "weight": { "type": "string", "description": "the BLOC token whose balances decide the split" },
                    "decimals": { "type": "number", "description": "decimals of the asset, default 18" }
                },
                "required": ["address"]
            }
        },
        {
            "name": "defi_treasury_participants",
            "description": "Add or remove an address from the local BLOC watch list — who the preview splits across. This is bookkeeping; the on-chain equivalent is defi_treasury_register, and only that one makes an address eligible for a real payout.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": { "type": "string" },
                    "remove": { "type": "boolean" }
                },
                "required": ["address"]
            }
        },
        {
            "name": "defi_treasury_lock",
            "description": "Lock an allocation into the bound treasury for real: approve the asset, then lock(amount, termWeeks, returnPrincipal). THIS MOVES MONEY AND YOU CANNOT RECALL IT before the term — a streaming lock pays its principal out to BLOC holders week by week, an escrowed one comes back only after the term. The eth module signs with the account you name and holds the key; this module has none. A non-testnet network needs confirm=true here, and the eth module's own confirm underneath.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": { "type": "string", "description": "the allocation to lock" },
                    "account": { "type": "string", "description": "name of an eth-module account (eth_accounts lists yours)" },
                    "confirm": { "type": "boolean" },
                    "password": { "type": "string", "description": "passed through to the eth module if its keystore is locked" },
                    "auth": { "type": "string", "description": "bearer for the eth module, if not yours" }
                },
                "required": ["id", "account"]
            }
        },
        {
            "name": "defi_treasury_distribute",
            "description": "Call distribute() on the bound treasury: sweep this week's payout to the registered holders pro-rata by BLOC. Permissionless on chain — anyone may call it once the window opens. Refuses early rather than letting a wallet pay gas for a revert it can see coming.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "account": { "type": "string" }, "confirm": { "type": "boolean" },
                    "password": { "type": "string" }, "auth": { "type": "string" }
                },
                "required": ["account"]
            }
        },
        {
            "name": "defi_treasury_claim",
            "description": "Pull whatever the weekly splits have already credited to this account out of the treasury.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "account": { "type": "string" }, "confirm": { "type": "boolean" },
                    "password": { "type": "string" }, "auth": { "type": "string" }
                },
                "required": ["account"]
            }
        },
        {
            "name": "defi_treasury_register",
            "description": "Put an address into the treasury's own registered set on chain, which is what makes it eligible for a payout. Registering someone else only ever dilutes you, so the contract lets anyone do it. Unregistered BLOC holders earn nothing here — the contract reads the token from outside and cannot checkpoint transfers, so an open accumulator would pay whoever bought BLOC after the fact.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "account": { "type": "string", "description": "the eth account that sends the transaction" },
                    "who": { "type": "string", "description": "address to register; omit to register the account itself" },
                    "confirm": { "type": "boolean" }, "password": { "type": "string" }, "auth": { "type": "string" }
                },
                "required": ["account"]
            }
        },
        {
            "name": "defi_treasury_onchain",
            "description": "[public] The bound treasury's live state read straight off the chain: balance, locked principal, what this week's payout would be, registered weight and holder count, and when the next window opens.",
            "inputSchema": { "type": "object", "properties": {} }
        },
    ])
}

/// Every tool this server offers, in one list.
fn tools() -> serde_json::Value {
    let mut all = composer_and_desk_tools();
    let rest = yield_and_treasury_tools();
    if let (Some(a), Some(b)) = (all.as_array_mut(), rest.as_array()) {
        a.extend(b.iter().cloned());
    }
    all
}

pub async fn describe(State(state): State<Shared>) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "protocol": "mcp/2025-06-18",
        "transport": "POST /mcp (Streamable HTTP, JSON-RPC 2.0); single messages or batches",
        "auth": "the same Bearer token the REST routes take; anonymous callers get the [public] tools",
        "module": "defi",
        "catalog": state.catalog.version,
        "tools": tools(),
    }))
}

pub async fn rpc(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    if let serde_json::Value::Array(batch) = body {
        let mut out = Vec::new();
        for message in batch {
            if let Some(response) = handle(&state, &headers, message).await {
                out.push(response);
            }
        }
        return Json(serde_json::Value::Array(out));
    }
    Json(handle(&state, &headers, body).await.unwrap_or(serde_json::Value::Null))
}

fn ok(id: serde_json::Value, result: serde_json::Value) -> serde_json::Value {
    serde_json::json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn err(id: serde_json::Value, code: i32, message: impl Into<String>) -> serde_json::Value {
    serde_json::json!({
        "jsonrpc": "2.0", "id": id,
        "error": { "code": code, "message": message.into() }
    })
}

fn text_result(value: serde_json::Value, is_error: bool) -> serde_json::Value {
    serde_json::json!({
        "content": [{ "type": "text", "text": serde_json::to_string_pretty(&value).unwrap_or_default() }],
        "isError": is_error
    })
}

async fn handle(
    state: &Shared,
    headers: &HeaderMap,
    message: serde_json::Value,
) -> Option<serde_json::Value> {
    let method = message.get("method")?.as_str()?.to_string();
    let id = message.get("id").cloned();
    let params = message.get("params").cloned().unwrap_or(serde_json::json!({}));

    // Notifications carry no id and expect no response.
    let Some(id) = id else {
        return None;
    };

    Some(match method.as_str() {
        "initialize" => ok(
            id,
            serde_json::json!({
                "protocolVersion": "2025-06-18",
                "capabilities": { "tools": {}, "resources": {}, "prompts": {} },
                "serverInfo": { "name": "defi", "version": state.version }
            }),
        ),
        "ping" => ok(id, serde_json::json!({})),
        "tools/list" => ok(id, serde_json::json!({ "tools": tools() })),
        "resources/list" => ok(
            id,
            serde_json::json!({
                "resources": state.catalog.blocks.iter().map(|b| serde_json::json!({
                    "uri": format!("defi://block/{}", b.id),
                    "name": b.name,
                    "description": b.summary,
                    "mimeType": "text/x-solidity"
                })).collect::<Vec<_>>()
            }),
        ),
        "resources/read" => {
            let uri = params.get("uri").and_then(|u| u.as_str()).unwrap_or("");
            let block_id = uri.strip_prefix("defi://block/").unwrap_or("");
            match state.catalog.block(block_id) {
                Some(block) => ok(
                    id,
                    serde_json::json!({
                        "contents": [{
                            "uri": uri,
                            "mimeType": "text/x-solidity",
                            "text": block.source.clone().unwrap_or_default()
                        }]
                    }),
                ),
                None => err(id, -32602, format!("no block resource '{block_id}'")),
            }
        }
        "prompts/list" => ok(id, serde_json::json!({ "prompts": [] })),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or(serde_json::json!({}));
            match call_tool(state, headers, name, args).await {
                Ok(value) => ok(id, text_result(value, false)),
                Err(message) => ok(
                    id,
                    text_result(serde_json::json!({ "error": message }), true),
                ),
            }
        }
        other => err(id, -32601, format!("unknown method '{other}'")),
    })
}

fn arg_str(args: &serde_json::Value, key: &str) -> Result<String, String> {
    args.get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| format!("'{key}' is required"))
}

/// Which token the chain module should see: an explicit one on the call wins,
/// otherwise the caller's own. Never a credential of this module's — it has
/// none, and inventing one here would make every anonymous visitor a trader.
fn peer_auth<'a>(args: &'a serde_json::Value, token: &'a Option<String>) -> Option<&'a str> {
    args.get("auth")
        .and_then(|v| v.as_str())
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .or_else(|| token.as_deref())
}

fn arg_graph(args: &serde_json::Value) -> Result<graph::Graph, String> {
    let raw = args.get("graph").cloned().ok_or("'graph' is required")?;
    serde_json::from_value(raw).map_err(|e| format!("bad graph: {e}"))
}

async fn call_tool(
    state: &Shared,
    headers: &HeaderMap,
    name: &str,
    args: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let token = crate::bearer(headers);
    let who = crate::caller(state, headers);

    match name {
        "defi_catalog" => Ok(state.catalog.summary()),
        "defi_templates" => Ok(serde_json::json!({ "templates": state.catalog.templates })),
        "defi_block" => {
            let block_id = arg_str(&args, "id")?;
            let block = state
                .catalog
                .block(&block_id)
                .ok_or_else(|| format!("no block '{block_id}'"))?;
            let compiled = state.compiled.read().await;
            Ok(serde_json::json!({
                "block": block,
                "artifact": compiled.as_ref().and_then(|c| c.artifacts.get(&block.contract).cloned()),
            }))
        }
        "defi_validate" => {
            let g = arg_graph(&args)?;
            Ok(serde_json::to_value(graph::validate(&state.catalog, &g)).unwrap())
        }
        "defi_plan" => {
            let g = arg_graph(&args)?;
            let report = graph::validate(&state.catalog, &g);
            if !report.ok {
                return Ok(serde_json::json!({ "ok": false, "report": report }));
            }
            let compiled = state.compiled.read().await;
            let compiled = compiled
                .as_ref()
                .ok_or("the catalog has not compiled yet — check /compile/status")?;
            let plan = graph::plan(&state.catalog, &g, &compiled.artifacts, &report)?;
            Ok(serde_json::json!({ "ok": true, "plan": plan }))
        }
        "defi_protocols" => Ok(serde_json::json!({ "protocols": state.store.list() })),
        "defi_protocol" => {
            let protocol_id = arg_str(&args, "id")?;
            state
                .store
                .get(&protocol_id)
                .map(|p| serde_json::json!({ "protocol": p }))
                .ok_or_else(|| format!("no protocol '{protocol_id}'"))
        }
        "defi_save" => {
            let who = who.ok_or("sign in with your wallet first")?;
            let g = arg_graph(&args)?;
            let now = auth::now();
            let protocol_id = args
                .get("id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| format!("p-{now}-mcp"));
            if let Some(prev) = state.store.get(&protocol_id) {
                if prev.owner != who && who != state.owner {
                    return Err("that protocol belongs to someone else".into());
                }
            }
            let protocol = storage::Protocol {
                id: protocol_id,
                name: args
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or(if g.name.is_empty() { "Untitled protocol" } else { &g.name })
                    .to_string(),
                description: g.description.clone(),
                owner: who,
                created: now,
                updated: now,
                graph: g,
                cid: None,
                deployments: vec![],
                imported_from: None,
            };
            state.store.save(&protocol)?;
            Ok(serde_json::json!({ "ok": true, "protocol": protocol }))
        }
        "defi_publish" => {
            let who = who.ok_or("sign in with your wallet first")?;
            let protocol_id = arg_str(&args, "id")?;
            let mut protocol = state
                .store
                .get(&protocol_id)
                .ok_or_else(|| format!("no protocol '{protocol_id}'"))?;
            if protocol.owner != who && who != state.owner {
                return Err("not yours to publish".into());
            }
            let payload = serde_json::json!({
                "kind": "defi/protocol",
                "version": 1,
                "name": protocol.name,
                "description": protocol.description,
                "graph": protocol.graph,
                "catalog": state.catalog.version,
            });
            let bytes = serde_json::to_vec(&payload).map_err(|e| e.to_string())?;
            let cid = state.store.put_object(&bytes)?;
            protocol.cid = Some(cid.clone());
            state.store.save(&protocol)?;
            Ok(serde_json::json!({ "ok": true, "cid": cid }))
        }
        "defi_import" => {
            let who = who.ok_or("sign in with your wallet first")?;
            let cid = arg_str(&args, "cid")?;
            let bytes = state
                .store
                .get_object(&cid)
                .ok_or_else(|| format!("{cid} is not in this node's object store"))?;
            let payload: serde_json::Value =
                serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            let g: graph::Graph =
                serde_json::from_value(payload.get("graph").cloned().unwrap_or_default())
                    .map_err(|e| format!("object is not a protocol: {e}"))?;
            let now = auth::now();
            let protocol = storage::Protocol {
                id: format!("p-{now}-import"),
                name: payload
                    .get("name")
                    .and_then(|n| n.as_str())
                    .unwrap_or("Imported protocol")
                    .to_string(),
                description: String::new(),
                owner: who,
                created: now,
                updated: now,
                graph: g,
                cid: Some(cid.clone()),
                deployments: vec![],
                imported_from: Some(cid),
            };
            state.store.save(&protocol)?;
            Ok(serde_json::json!({ "ok": true, "protocol": protocol }))
        }
        "defi_yields" => state.yields.pools(&crate::yields::Filter::from_query(&args)).await,
        "defi_yield_protocols" => state.yields.protocols(&crate::yields::Filter::from_query(&args)).await,
        "defi_yields_facets" => state.yields.facets().await,
        "defi_yield_pool" => {
            let pool_id = arg_str(&args, "id")?;
            let history = args.get("history").and_then(|v| v.as_bool()).unwrap_or(true);
            state.yields.pool(&pool_id, history).await
        }
        "defi_treasury" => Ok(state
            .treasury
            .desk(&state.dex, auth::now(), token.as_deref())
            .await),
        "defi_treasury_schedule" => {
            let weeks = args.get("weeks").and_then(|v| v.as_u64()).unwrap_or(8) as usize;
            Ok(state.treasury.schedule(weeks, auth::now()))
        }
        "defi_treasury_holders" => state.treasury.holders().await,
        "defi_treasury_preview" => state.treasury.preview(auth::now()).await,
        "defi_treasury_onchain" => state.treasury.onchain(&state.dex, token.as_deref()).await,
        "defi_treasury_choose" => {
            let who = who.ok_or("sign in with your wallet first")?;
            let now = auth::now();
            let allocation = state.treasury.choose(&args, &who, now)?;
            Ok(serde_json::json!({
                "ok": true,
                "allocation": allocation.view(now),
                "next": "nothing has moved yet — defi_treasury_lock turns this plan into a transaction",
            }))
        }
        "defi_treasury_bind" => {
            let who = who.ok_or("sign in with your wallet first")?;
            let existing = state.treasury.binding();
            if !existing.address.is_empty()
                && !existing.bound_by.eq_ignore_ascii_case(&who)
                && who != state.owner
            {
                return Err(format!(
                    "a treasury is already bound here by {} — only they or the module owner can repoint it",
                    existing.bound_by
                ));
            }
            Ok(serde_json::json!({
                "ok": true,
                "binding": state.treasury.bind(&args, &who, auth::now())?,
            }))
        }
        "defi_treasury_participants" => {
            who.ok_or("sign in with your wallet first")?;
            let address = arg_str(&args, "address")?;
            let list = if args.get("remove").and_then(|v| v.as_bool()).unwrap_or(false) {
                state.treasury.remove_participant(&address)?
            } else {
                state.treasury.add_participant(&address)?
            };
            Ok(serde_json::json!({ "ok": true, "watched": list }))
        }
        "defi_treasury_lock" => {
            let who = who.ok_or("sign in with your wallet first")?;
            let allocation_id = arg_str(&args, "id")?;
            let account = arg_str(&args, "account")?;
            let mut allocation = state
                .treasury
                .get(&allocation_id)
                .ok_or_else(|| format!("no allocation '{allocation_id}'"))?;
            if allocation.owner != who && who != state.owner {
                return Err("that allocation belongs to someone else".into());
            }
            if allocation.status == "locked" {
                return Err("that allocation is already locked".into());
            }
            let out = state
                .treasury
                .lock_onchain(
                    &state.dex,
                    &allocation,
                    &account,
                    args.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false),
                    args.get("password"),
                    peer_auth(&args, &token),
                )
                .await?;
            let binding = state.treasury.binding();
            allocation.status = "locked".into();
            allocation.updated = auth::now();
            allocation.treasury = Some(binding.address);
            allocation.network = Some(binding.network);
            allocation.tx = out
                .pointer("/result/hash")
                .or_else(|| out.pointer("/result/tx"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            state.treasury.save(&allocation)?;
            Ok(serde_json::json!({
                "ok": true,
                "allocation": allocation.view(auth::now()),
                "chain": out,
            }))
        }
        "defi_treasury_distribute" => {
            let account = arg_str(&args, "account")?;
            state
                .treasury
                .distribute_onchain(
                    &state.dex,
                    &account,
                    args.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false),
                    args.get("password"),
                    peer_auth(&args, &token),
                    auth::now(),
                )
                .await
        }
        "defi_treasury_claim" => {
            let account = arg_str(&args, "account")?;
            state
                .treasury
                .claim_onchain(
                    &state.dex,
                    &account,
                    args.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false),
                    args.get("password"),
                    peer_auth(&args, &token),
                )
                .await
        }
        "defi_treasury_register" => {
            let account = arg_str(&args, "account")?;
            state
                .treasury
                .register_onchain(
                    &state.dex,
                    &account,
                    args.get("who").and_then(|v| v.as_str()),
                    args.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false),
                    args.get("password"),
                    peer_auth(&args, &token),
                )
                .await
        }
        "defi_dex_venues" => {
            let check = args.get("check").and_then(|c| c.as_bool()).unwrap_or(false);
            Ok(state.dex.venues(check).await)
        }
        "defi_dex_tokens" => state
            .dex
            .tokens(args.get("chain").and_then(|c| c.as_str())),
        "defi_dex_quote" => state.dex.quote(&args, peer_auth(&args, &token)).await,
        "defi_dex_swap" => state.dex.swap(&args, peer_auth(&args, &token)).await,
        "defi_dex_balances" => state.dex.balances(&args, peer_auth(&args, &token)).await,
        "defi_prompts" => {
            let prompts = state.agent.prompts(token.as_deref()).await?;
            Ok(serde_json::json!({ "prompts": prompts, "source": state.agent.base }))
        }
        "defi_prompt" => {
            let prompt_id = arg_str(&args, "id")?;
            let prompt = state.agent.prompt(&prompt_id, token.as_deref()).await?;
            Ok(serde_json::json!({ "prompt": prompt }))
        }
        "defi_compose" => {
            let request = arg_str(&args, "prompt")?;
            let mut preamble = String::new();
            if let Some(prompt_id) = args.get("promptId").and_then(|v| v.as_str()) {
                let prompt = state.agent.prompt(prompt_id, token.as_deref()).await?;
                preamble = format!("{}\n\n", prompt.text);
            }
            let blocks: Vec<serde_json::Value> = state
                .catalog
                .blocks
                .iter()
                .map(|b| {
                    serde_json::json!({
                        "block": b.id,
                        "provides": b.provides,
                        "inputs": b.inputs.iter().map(|i| serde_json::json!({
                            "port": i.id, "type": i.port_type, "required": i.required
                        })).collect::<Vec<_>>(),
                    })
                })
                .collect();
            let ask = format!(
                "{preamble}Compose a DeFi protocol from this block catalog. Return ONLY JSON \
                 {{\"name\",\"description\",\"nodes\":[{{\"id\",\"block\",\"x\",\"y\",\"params\"}}],\"edges\":[{{\"from\",\"to\",\"port\"}}]}}.\n\
                 CATALOG:\n{}\n\nREQUEST: {request}",
                serde_json::to_string(&blocks).unwrap_or_default()
            );
            let reply = state.agent.ask(&ask, token.as_deref()).await?;
            let parsed = agentlink::extract_json(&reply)
                .ok_or("the agent did not return a protocol graph")?;
            let g: graph::Graph = serde_json::from_value(parsed)
                .map_err(|e| format!("agent returned an unusable graph: {e}"))?;
            let report = graph::validate(&state.catalog, &g);
            Ok(serde_json::json!({ "graph": g, "report": report }))
        }
        other => Err(format!("unknown tool '{other}'")),
    }
}
