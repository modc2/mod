//! Model Context Protocol server.
//!
//! Streamable-HTTP MCP on `POST /mcp`, sharing the exact code path the REST
//! routes use (`pipeline::collect`), so a tool call and the equivalent GET
//! cannot drift apart. `GET /mcp` returns the same tool registry as plain
//! JSON, which is what a human or a health check wants from that URL.

use axum::{
    extract::State,
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::sync::Arc;

use crate::config;
use crate::models::chain::Chain;
use crate::models::trader::TraderResult;
use crate::pipeline::{self, meta};
use crate::state::AppState;

/// Protocol revision this server implements. The two older revisions are
/// accepted on initialize so clients pinned to them still connect.
const PROTOCOL_VERSION: &str = "2025-06-18";
const SUPPORTED_VERSIONS: &[&str] = &["2025-06-18", "2025-03-26", "2024-11-05"];

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/mcp", post(mcp_post).get(mcp_get))
        .route("/mcp/tools", get(mcp_tools_json))
}

// ---------------------------------------------------------------- schema

fn chain_prop(default: &str) -> Value {
    json!({
        "type": "string",
        "enum": Chain::all().iter().map(|c| c.name()).collect::<Vec<_>>(),
        "default": default,
        "description": "Which chain to read.",
    })
}

fn days_prop() -> Value {
    json!({
        "type": "integer",
        "minimum": 1,
        "maximum": 30,
        "default": 7,
        "description": "Length of the lookback window in days.",
    })
}

fn pool_prop() -> Value {
    json!({
        "type": "integer",
        "minimum": 100,
        "maximum": 20000,
        "default": 2000,
        "description": "Swap sample budget. Larger reads more of the window and takes longer.",
    })
}

/// The tool registry. One list, used by tools/list, GET /mcp and dispatch, so
/// a tool cannot be advertised without being callable.
pub fn tools() -> Vec<Value> {
    vec![
        json!({
            "name": "uniswap_chains",
            "title": "List chains",
            "description": "The chains this module scrapes, with how many Uniswap V3 pools are configured on each and whether its RPC endpoints are answering.",
            "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false },
        }),
        json!({
            "name": "uniswap_pools",
            "title": "List pools",
            "description": "The Uniswap V3 pools sampled on one chain, resolved on chain: both token addresses, their symbols and decimals, and the fee tier. This is the scrape's universe — a trader who never touches these pools will not appear in any leaderboard.",
            "inputSchema": {
                "type": "object",
                "properties": { "chain": chain_prop("base") },
                "additionalProperties": false,
            },
        }),
        json!({
            "name": "uniswap_traders",
            "title": "Trader leaderboard",
            "description": "Rank traders on one chain over a lookback window. Returns volume, FIFO realized PnL, win rate, pool and token breakdowns, MEV-bot classification and a composite score per trader, plus how much of the window the sample actually read.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain": chain_prop("base"),
                    "days": days_prop(),
                    "limit": { "type": "integer", "minimum": 1, "maximum": 200, "default": 20, "description": "How many traders to return." },
                    "sort": { "type": "string", "enum": pipeline::SORT_KEYS, "default": "score", "description": "Ranking key." },
                    "min_swaps": { "type": "integer", "minimum": 1, "default": 5, "description": "Drop traders with fewer swaps than this in the sample." },
                    "exclude_mev": { "type": "boolean", "default": false, "description": "Drop addresses classified as MEV bots." },
                    "pool": pool_prop(),
                    "refresh": { "type": "boolean", "default": false, "description": "Ignore the cached window and scrape again." },
                },
                "additionalProperties": false,
            },
        }),
        json!({
            "name": "uniswap_trader",
            "title": "Trader profile",
            "description": "The full metric set for one address over a window: volume split, FIFO realized PnL and its 12-point curve, win rate, per-pool and per-token breakdowns, and MEV indicators. Says so plainly when the address is not in the sample rather than reporting zeros.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": { "type": "string", "description": "The trader's 0x address (the swap sender)." },
                    "chain": chain_prop("base"),
                    "days": days_prop(),
                    "pool": pool_prop(),
                },
                "required": ["address"],
                "additionalProperties": false,
            },
        }),
        json!({
            "name": "uniswap_compare",
            "title": "Compare traders",
            "description": "Put several addresses side by side over the same window and chain — one scrape, so the comparison is apples to apples. Addresses missing from the sample are reported as missing.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "addresses": {
                        "type": "array",
                        "items": { "type": "string" },
                        "minItems": 1,
                        "maxItems": 25,
                        "description": "The 0x addresses to compare.",
                    },
                    "chain": chain_prop("base"),
                    "days": days_prop(),
                    "pool": pool_prop(),
                },
                "required": ["addresses"],
                "additionalProperties": false,
            },
        }),
        json!({
            "name": "uniswap_market",
            "title": "Market summary",
            "description": "Aggregate the whole sample for a chain and window: total sampled volume and swaps, trader counts, how many are MEV bots, the profitable share, and the busiest pools. The shape of the activity rather than a ranked list.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain": chain_prop("base"),
                    "days": days_prop(),
                    "pool": pool_prop(),
                },
                "additionalProperties": false,
            },
        }),
        json!({
            "name": "uniswap_scrape",
            "title": "Force a fresh scrape",
            "description": "Discard the cached window and read it again, then report what the new sample contains. Use when the cache is stale; an ordinary uniswap_traders call already scrapes when nothing is cached.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain": chain_prop("base"),
                    "days": days_prop(),
                    "pool": pool_prop(),
                },
                "additionalProperties": false,
            },
        }),
        json!({
            "name": "uniswap_health",
            "title": "Service health",
            "description": "Whether the API is up, which chain/window combinations are cached, how many pools have been resolved, and the ETH price each chain is pricing swaps at.",
            "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false },
        }),
    ]
}

// ---------------------------------------------------------------- transport

async fn mcp_get(State(state): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({
        "server": "uniswap",
        "protocol": PROTOCOL_VERSION,
        "supported_protocols": SUPPORTED_VERSIONS,
        "transport": {
            "http": "POST /mcp — JSON-RPC 2.0, one request per call",
            "stdio": "python3 mcp.py — bridges stdin/stdout to this endpoint",
        },
        "auth": "none — every tool reads public chain data",
        "tools": tools(),
        "chains": Chain::all().iter().map(|c| c.name()).collect::<Vec<_>>(),
        "pools_resolved": state.pool_meta.len(),
    }))
}

async fn mcp_tools_json() -> Json<Value> {
    Json(json!({ "tools": tools() }))
}

fn rpc_error(id: Value, code: i64, message: &str) -> Json<Value> {
    Json(json!({
        "jsonrpc": "2.0",
        "id": id,
        "error": { "code": code, "message": message },
    }))
}

fn rpc_ok(id: Value, result: Value) -> Json<Value> {
    Json(json!({ "jsonrpc": "2.0", "id": id, "result": result }))
}

async fn mcp_post(
    State(state): State<Arc<AppState>>,
    body: axum::body::Bytes,
) -> axum::response::Response {
    let req: Value = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(e) => {
            return rpc_error(Value::Null, -32700, &format!("parse error: {e}")).into_response()
        }
    };

    // A batch is a JSON array of requests; answer each in order.
    if let Some(batch) = req.as_array() {
        let mut out = Vec::new();
        for one in batch {
            if let Some(resp) = handle_one(&state, one).await {
                out.push(resp);
            }
        }
        if out.is_empty() {
            return StatusCode::ACCEPTED.into_response();
        }
        return Json(Value::Array(out)).into_response();
    }

    match handle_one(&state, &req).await {
        Some(resp) => Json(resp).into_response(),
        // Notifications get no body — the spec wants 202, and a client that
        // sends `notifications/initialized` will hang if it gets a result it
        // did not ask for.
        None => StatusCode::ACCEPTED.into_response(),
    }
}

/// Handle one JSON-RPC message. `None` means "notification, say nothing".
async fn handle_one(state: &Arc<AppState>, req: &Value) -> Option<Value> {
    let method = req.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let id = req.get("id").cloned();
    let params = req.get("params").cloned().unwrap_or_else(|| json!({}));

    // No id: a notification. Nothing may be returned for one.
    let Some(id) = id else {
        return None;
    };

    let unwrap = |j: Json<Value>| j.0;

    Some(match method {
        "initialize" => {
            let asked = params
                .get("protocolVersion")
                .and_then(|v| v.as_str())
                .unwrap_or(PROTOCOL_VERSION);
            let version = if SUPPORTED_VERSIONS.contains(&asked) {
                asked
            } else {
                PROTOCOL_VERSION
            };
            unwrap(rpc_ok(
                id,
                json!({
                    "protocolVersion": version,
                    "capabilities": { "tools": { "listChanged": false } },
                    "serverInfo": {
                        "name": "uniswap",
                        "title": "Uniswap V3 trader scraper",
                        "version": env!("CARGO_PKG_VERSION"),
                    },
                    "instructions": "Uniswap V3 trader analytics across Ethereum, Arbitrum, Base, \
Polygon and Optimism. Start with uniswap_traders for a ranked leaderboard, then uniswap_trader for \
one address. Every figure comes from a sample of the top pools on that chain, not from every swap: \
each result carries a `coverage` block saying what fraction of the window was actually read, and \
uniswap_pools shows which pools are in scope. Realized PnL is FIFO cost-basis over the sampled \
swaps only, so a position opened outside the sample contributes nothing to it.",
                }),
            ))
        }
        "ping" => unwrap(rpc_ok(id, json!({}))),
        "tools/list" => unwrap(rpc_ok(id, json!({ "tools": tools() }))),
        "resources/list" => unwrap(rpc_ok(id, json!({ "resources": [] }))),
        "prompts/list" => unwrap(rpc_ok(id, json!({ "prompts": [] }))),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params
                .get("arguments")
                .cloned()
                .unwrap_or_else(|| json!({}));

            if !tools()
                .iter()
                .any(|t| t.get("name").and_then(|n| n.as_str()) == Some(name))
            {
                return Some(unwrap(rpc_error(
                    id,
                    -32602,
                    &format!("unknown tool '{name}'"),
                )));
            }

            let (payload, is_error) = dispatch(state, name, &args).await;
            unwrap(rpc_ok(
                id,
                json!({
                    "content": [{
                        "type": "text",
                        "text": serde_json::to_string_pretty(&payload).unwrap_or_default(),
                    }],
                    "structuredContent": payload,
                    "isError": is_error,
                }),
            ))
        }
        other => unwrap(rpc_error(id, -32601, &format!("unknown method '{other}'"))),
    })
}

// ---------------------------------------------------------------- dispatch

fn arg_str(args: &Value, key: &str) -> Option<String> {
    args.get(key)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

fn arg_u32(args: &Value, key: &str, default: u32, lo: u32, hi: u32) -> u32 {
    args.get(key)
        .and_then(|v| v.as_u64())
        .map(|v| v as u32)
        .unwrap_or(default)
        .clamp(lo, hi)
}

fn arg_bool(args: &Value, key: &str) -> bool {
    args.get(key).and_then(|v| v.as_bool()).unwrap_or(false)
}

fn resolve_chain(args: &Value) -> Result<Chain, Value> {
    let name = arg_str(args, "chain").unwrap_or_else(|| "base".to_string());
    Chain::from_str(&name).ok_or_else(|| {
        json!({
            "error": format!("unknown chain '{name}'"),
            "chains": Chain::all().iter().map(|c| c.name()).collect::<Vec<_>>(),
        })
    })
}

/// A trader trimmed to the fields that matter in a list, so a 200-row
/// leaderboard does not arrive as a megabyte of curves.
fn brief(t: &TraderResult) -> Value {
    json!({
        "address": t.address,
        "chain": t.chain,
        "score": (t.composite_score * 100.0).round() / 100.0,
        "volume_usd": t.total_volume_usd.round(),
        "realized_pnl_usd": (t.realized_pnl_usd * 100.0).round() / 100.0,
        "win_rate_pct": (t.win_rate * 10.0).round() / 10.0,
        "swaps": t.swap_count,
        "active_days": t.active_days,
        "avg_trade_usd": t.avg_trade_size.round(),
        "unique_pools": t.unique_pools,
        "top_tokens": t.top_tokens.iter().take(3).map(|k| k.symbol.clone()).collect::<Vec<_>>(),
        "is_mev_bot": t.is_mev_bot,
    })
}

async fn dispatch(state: &Arc<AppState>, name: &str, args: &Value) -> (Value, bool) {
    match name {
        "uniswap_chains" => {
            let chains: Vec<Value> = Chain::all()
                .iter()
                .map(|c| {
                    json!({
                        "name": c.name(),
                        "aliases": alias_list(c),
                        "tokens_scanned": config::tokens(c).len(),
                        "pools_sampled": state.chain_pools.get(c.name()).map(|p| p.len()).unwrap_or(0),
                        "rpc_endpoints": config::rpc_endpoints(c).len(),
                        "eth_price_usd": state.get_eth_price(c.name()),
                    })
                })
                .collect();
            (json!({ "chains": chains }), false)
        }

        "uniswap_pools" => {
            let chain = match resolve_chain(args) {
                Ok(c) => c,
                Err(e) => return (e, true),
            };
            let pools = meta::resolve_chain_pools(state, &chain).await;
            let mut list: Vec<Value> = pools
                .values()
                .map(|m| {
                    json!({
                        "pool": m.pool,
                        "pair": m.label(),
                        "token0": { "address": m.token0, "symbol": m.symbol0, "decimals": m.decimals0 },
                        "token1": { "address": m.token1, "symbol": m.symbol1, "decimals": m.decimals1 },
                        "fee_tier": m.fee,
                        "fee_pct": m.fee as f64 / 10_000.0,
                        "depth": { "amount": m.quote_balance.round(), "token": m.quote_symbol },
                    })
                })
                .collect();
            list.sort_by(|a, b| a["pair"].as_str().cmp(&b["pair"].as_str()));

            (
                json!({
                    "chain": chain.name(),
                    "pools": list,
                    "tokens_scanned": config::tokens(&chain).len(),
                    "fee_tiers": config::FEE_TIERS,
                    "sampled": pools.len(),
                    "note": "Discovered from the Uniswap V3 factory over these tokens and fee tiers, \
ranked by quote-token depth. They are the entire scrape universe on this chain.",
                }),
                false,
            )
        }

        "uniswap_traders" => {
            let chain = match resolve_chain(args) {
                Ok(c) => c,
                Err(e) => return (e, true),
            };
            let days = arg_u32(args, "days", 7, 1, 30);
            let pool = arg_u32(args, "pool", 2000, 100, 20_000);
            let limit = arg_u32(args, "limit", 20, 1, 200) as usize;
            let min_swaps = arg_u32(args, "min_swaps", 5, 1, 10_000);
            let sort = arg_str(args, "sort").unwrap_or_else(|| "score".to_string());
            let exclude_mev = arg_bool(args, "exclude_mev");

            let scrape = pipeline::collect(
                state.clone(),
                chain,
                days,
                pool,
                min_swaps,
                arg_bool(args, "refresh"),
            )
            .await;

            if let Some(err) = scrape.error {
                return (json!({ "error": err, "chain": chain.name(), "days": days }), true);
            }

            let mut traders = scrape.traders;
            if exclude_mev {
                traders.retain(|t| !t.is_mev_bot);
            }
            pipeline::sort_traders(&mut traders, &sort);
            let total = traders.len();
            traders.truncate(limit);

            (
                json!({
                    "chain": chain.name(),
                    "days": days,
                    "sort": sort,
                    "matched": total,
                    "returned": traders.len(),
                    "source": scrape.source,
                    "coverage": scrape.coverage,
                    "traders": traders.iter().map(brief).collect::<Vec<_>>(),
                }),
                false,
            )
        }

        "uniswap_trader" => {
            let Some(address) = arg_str(args, "address") else {
                return (json!({ "error": "address is required" }), true);
            };
            let chain = match resolve_chain(args) {
                Ok(c) => c,
                Err(e) => return (e, true),
            };
            let days = arg_u32(args, "days", 7, 1, 30);
            let pool = arg_u32(args, "pool", 2000, 100, 20_000);
            let addr = address.to_lowercase();

            let scrape = pipeline::collect(state.clone(), chain, days, pool, 1, false).await;
            if let Some(err) = scrape.error {
                return (json!({ "error": err, "address": addr }), true);
            }

            match scrape.traders.iter().find(|t| t.address == addr) {
                Some(t) => (
                    json!({
                        "chain": chain.name(),
                        "days": days,
                        "source": scrape.source,
                        "coverage": scrape.coverage,
                        "trader": t,
                    }),
                    false,
                ),
                None => (
                    json!({
                        "error": "trader not in this sample",
                        "detail": format!(
                            "{} made no swaps in the {}d {} sample ({} addresses seen). The scrape \
                             reads a sample of the top pools, so an address can be active on chain \
                             and still be absent here — widen `pool` or check uniswap_pools.",
                            addr, days, chain.name(), scrape.traders.len()),
                        "address": addr,
                        "chain": chain.name(),
                        "days": days,
                        "sampled_traders": scrape.traders.len(),
                        "coverage": scrape.coverage,
                    }),
                    true,
                ),
            }
        }

        "uniswap_compare" => {
            let addresses: Vec<String> = args
                .get("addresses")
                .and_then(|v| v.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|v| v.as_str())
                        .map(|s| s.to_lowercase())
                        .collect()
                })
                .unwrap_or_default();

            if addresses.is_empty() {
                return (json!({ "error": "addresses must be a non-empty array" }), true);
            }

            let chain = match resolve_chain(args) {
                Ok(c) => c,
                Err(e) => return (e, true),
            };
            let days = arg_u32(args, "days", 7, 1, 30);
            let pool = arg_u32(args, "pool", 2000, 100, 20_000);

            let scrape = pipeline::collect(state.clone(), chain, days, pool, 1, false).await;
            if let Some(err) = scrape.error {
                return (json!({ "error": err, "chain": chain.name() }), true);
            }

            let mut found = Vec::new();
            let mut missing = Vec::new();
            for addr in &addresses {
                match scrape.traders.iter().find(|t| &t.address == addr) {
                    Some(t) => found.push(brief(t)),
                    None => missing.push(addr.clone()),
                }
            }

            (
                json!({
                    "chain": chain.name(),
                    "days": days,
                    "source": scrape.source,
                    "coverage": scrape.coverage,
                    "found": found,
                    "missing": missing,
                    "note": if missing.is_empty() { Value::Null } else {
                        json!("Missing addresses made no swaps in this sample of the top pools.")
                    },
                }),
                false,
            )
        }

        "uniswap_market" => {
            let chain = match resolve_chain(args) {
                Ok(c) => c,
                Err(e) => return (e, true),
            };
            let days = arg_u32(args, "days", 7, 1, 30);
            let pool = arg_u32(args, "pool", 2000, 100, 20_000);

            let scrape = pipeline::collect(state.clone(), chain, days, pool, 1, false).await;
            if let Some(err) = scrape.error {
                return (json!({ "error": err, "chain": chain.name() }), true);
            }

            let t = &scrape.traders;
            let volume: f64 = t.iter().map(|x| x.total_volume_usd).sum();
            let swaps: u64 = t.iter().map(|x| x.swap_count as u64).sum();
            let pnl: f64 = t.iter().map(|x| x.realized_pnl_usd).sum();
            let bots = t.iter().filter(|x| x.is_mev_bot).count();
            let winners = t.iter().filter(|x| x.realized_pnl_usd > 0.0).count();

            // Busiest pools across every sampled trader.
            let mut by_pool: std::collections::HashMap<String, (String, f64, u64)> =
                std::collections::HashMap::new();
            for trader in t {
                for p in &trader.pools_traded {
                    let e = by_pool
                        .entry(p.pool_id.clone())
                        .or_insert_with(|| (format!("{}/{}", p.token0, p.token1), 0.0, 0));
                    e.1 += p.volume_usd;
                    e.2 += p.swap_count as u64;
                }
            }
            let mut pools: Vec<Value> = by_pool
                .into_iter()
                .map(|(id, (pair, vol, n))| {
                    json!({ "pool": id, "pair": pair, "volume_usd": vol.round(), "swaps": n })
                })
                .collect();
            pools.sort_by(|a, b| {
                b["volume_usd"]
                    .as_f64()
                    .unwrap_or(0.0)
                    .total_cmp(&a["volume_usd"].as_f64().unwrap_or(0.0))
            });
            pools.truncate(10);

            (
                json!({
                    "chain": chain.name(),
                    "days": days,
                    "source": scrape.source,
                    "coverage": scrape.coverage,
                    "eth_price_usd": state.get_eth_price(chain.name()),
                    "sampled": {
                        "traders": t.len(),
                        "swaps": swaps,
                        "volume_usd": volume.round(),
                        "net_realized_pnl_usd": (pnl * 100.0).round() / 100.0,
                        "mev_bots": bots,
                        "mev_bot_pct": if t.is_empty() { 0.0 } else {
                            (bots as f64 / t.len() as f64 * 1000.0).round() / 10.0 },
                        "profitable_traders": winners,
                        "profitable_pct": if t.is_empty() { 0.0 } else {
                            (winners as f64 / t.len() as f64 * 1000.0).round() / 10.0 },
                    },
                    "top_pools": pools,
                }),
                false,
            )
        }

        "uniswap_scrape" => {
            let chain = match resolve_chain(args) {
                Ok(c) => c,
                Err(e) => return (e, true),
            };
            let days = arg_u32(args, "days", 7, 1, 30);
            let pool = arg_u32(args, "pool", 2000, 100, 20_000);

            let started = std::time::Instant::now();
            let scrape = pipeline::collect(state.clone(), chain, days, pool, 1, true).await;
            let elapsed = started.elapsed().as_secs_f64();

            if let Some(err) = scrape.error {
                return (
                    json!({ "error": err, "chain": chain.name(), "days": days, "seconds": elapsed }),
                    true,
                );
            }

            let swaps: u64 = scrape.traders.iter().map(|t| t.swap_count as u64).sum();
            (
                json!({
                    "chain": chain.name(),
                    "days": days,
                    "source": scrape.source,
                    "seconds": (elapsed * 10.0).round() / 10.0,
                    "traders": scrape.traders.len(),
                    "swaps": swaps,
                    "coverage": scrape.coverage,
                }),
                false,
            )
        }

        "uniswap_health" => {
            let cached: Vec<Value> = state
                .memory_cache
                .iter()
                .map(|e| {
                    json!({
                        "key": e.key(),
                        "traders": e.value().data.len(),
                        "age_seconds": chrono::Utc::now().timestamp() - e.value().created_at,
                    })
                })
                .collect();

            (
                json!({
                    "status": "ok",
                    "service": "uniswap",
                    "version": env!("CARGO_PKG_VERSION"),
                    "uptime_seconds": chrono::Utc::now().timestamp() - state.started_at,
                    "pools_resolved": state.pool_meta.len(),
                    "cached_windows": cached,
                    "eth_price_usd": Chain::all()
                        .iter()
                        .filter_map(|c| state.get_eth_price(c.name()).map(|p| (c.name().to_string(), p)))
                        .collect::<std::collections::HashMap<_, _>>(),
                }),
                false,
            )
        }

        other => (json!({ "error": format!("unknown tool '{other}'") }), true),
    }
}

fn alias_list(chain: &Chain) -> Vec<&'static str> {
    match chain {
        Chain::Ethereum => vec!["eth"],
        Chain::Arbitrum => vec!["arb"],
        Chain::Polygon => vec!["matic"],
        Chain::Optimism => vec!["op"],
        Chain::Base => vec![],
    }
}
