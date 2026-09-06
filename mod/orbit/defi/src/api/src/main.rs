//! defi — modular DeFi composer.
//!
//! Drag reusable Solidity modules onto a canvas, wire their typed ports, and the
//! server validates the composition, compiles it with solc, and hands back an
//! ordered deployment plan. Signing and broadcasting stay in the browser wallet:
//! this service never holds a private key, so the worst a compromise of it can
//! do is propose a transaction you still have to approve.
//!
//! Layout:
//!   catalog.rs   the block library (data-driven, blocks/catalog.json)
//!   graph.rs     validation + deployment planning
//!   compile.rs   solc standard-json driver
//!   auth.rs      wallet sign-in, HMAC bearer tokens
//!   storage.rs   saved protocols + CID publishing
//!   agentlink.rs the agent mod's prompt library and AI compose
//!   dex.rs       the trading desk — Solana, Ethereum, Base and Bittensor,
//!                each through the module that already owns that chain
//!   yields.rs    the live APR of every DeFi protocol, from DefiLlama's index
//!   treasury.rs  what you chose out of that table, locked, and paid out weekly
//!                on BlocTime's clock — Friday 12:00 EST, split by BLOC
//!   mcp.rs       MCP server over the same surface

mod agentlink;
mod auth;
mod catalog;
mod compile;
mod dex;
mod finance;
mod graph;
mod hub;
mod mcp;
mod storage;
mod treasury;
mod yields;

use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    routing::{delete, get, post},
    Json, Router,
};
use serde::Deserialize;
use std::sync::Arc;
use tokio::sync::RwLock;
use tower_http::cors::{Any, CorsLayer};

pub struct AppState {
    pub catalog: catalog::Catalog,
    pub compiled: RwLock<Option<Arc<compile::CompileResult>>>,
    pub compile_error: RwLock<Option<String>>,
    pub store: storage::Store,
    pub agent: agentlink::AgentLink,
    pub dex: dex::Dex,
    pub yields: yields::Yields,
    pub treasury: treasury::Treasury,
    pub finance: finance::Finance,
    pub hub: hub::Hub,
    pub secret: Vec<u8>,
    pub challenges: auth::Challenges,
    pub module_dir: std::path::PathBuf,
    pub owner: String,
    pub version: String,
}

pub type Shared = Arc<AppState>;

#[tokio::main]
async fn main() {
    let port: u16 = std::env::args()
        .nth(1)
        .or_else(|| std::env::var("DEFI_PORT").ok())
        .and_then(|p| p.parse().ok())
        .unwrap_or(50500);

    let module_dir = module_dir();
    let blocks_dir = std::env::var("DEFI_BLOCKS_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| module_dir.join("src/api/blocks"));

    let catalog = match catalog::Catalog::load(&blocks_dir) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[defi] fatal: {e}");
            std::process::exit(1);
        }
    };
    println!(
        "[defi] catalog {} — {} blocks, {} templates from {}",
        catalog.version,
        catalog.blocks.len(),
        catalog.templates.len(),
        blocks_dir.display()
    );

    let data_dir = dirs::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(".mod/defi");
    let _ = std::fs::create_dir_all(&data_dir);

    let config = read_config(&module_dir);
    let owner = config
        .get("owner")
        .and_then(|o| o.as_str())
        .unwrap_or("")
        .to_lowercase();
    let version = config
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("1.0.0")
        .to_string();

    let state: Shared = Arc::new(AppState {
        catalog,
        compiled: RwLock::new(None),
        compile_error: RwLock::new(None),
        store: storage::Store::new(&data_dir),
        agent: agentlink::AgentLink::new(
            std::env::var("DEFI_AGENT_URL").unwrap_or_else(|_| "http://localhost:50117".into()),
        ),
        dex: dex::Dex::from_env(),
        yields: yields::Yields::new(),
        treasury: treasury::Treasury::new(&data_dir),
        finance: finance::Finance::new(
            &data_dir,
            &std::env::var("DEFI_ADAPTERS")
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|_| blocks_dir.parent().map(|p| p.join("adapters.json")).unwrap_or_else(|| module_dir.join("src/api/adapters.json"))),
        ),
        hub: hub::Hub::load(
            &std::env::var("DEFI_HUB")
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|_| blocks_dir.parent().map(|p| p.join("hub.json")).unwrap_or_else(|| module_dir.join("src/api/hub.json"))),
        ),
        secret: auth::load_secret(&data_dir),
        challenges: auth::Challenges::default(),
        module_dir,
        owner,
        version,
    });

    // Compile the whole catalog once, off the request path.
    {
        let warm = state.clone();
        tokio::spawn(async move {
            warm_compile(warm).await;
        });
    }

    let app = Router::new()
        .route("/health", get(health))
        .route("/config", get(config_handler))
        .route("/owner", get(owner_handler))
        .route("/catalog", get(get_catalog))
        .route("/catalog/:id", get(get_block))
        .route("/catalog/:id/audit", get(get_block_audit))
        .route("/audits", get(get_audits))
        .route("/templates", get(get_templates))
        .route("/compile/status", get(compile_status))
        .route("/validate", post(post_validate))
        .route("/plan", post(post_plan))
        .route("/protocols", get(list_protocols).post(save_protocol))
        .route("/protocols/import", post(import_protocol))
        .route("/protocols/:id", get(get_protocol).delete(delete_protocol))
        .route("/protocols/:id/publish", post(publish_protocol))
        .route("/protocols/:id/deployments", post(record_deployment))
        .route("/objects/:cid", get(get_object))
        .route("/auth/challenge", get(auth_challenge))
        .route("/auth/verify", post(auth_verify))
        .route("/auth/whoami", get(whoami))
        .route("/agent/status", get(agent_status))
        .route("/agent/prompts", get(agent_prompts))
        .route("/agent/prompts/:id", get(agent_prompt))
        .route("/agent/prompts/import", post(agent_import_prompt))
        .route("/agent/compose", post(agent_compose))
        .route("/dex/venues", get(dex_venues))
        .route("/dex/tokens", get(dex_tokens))
        .route("/dex/quote", post(dex_quote))
        .route("/dex/swap", post(dex_swap))
        .route("/dex/balances", get(dex_balances))
        .route("/yields", get(get_yields))
        .route("/yields/protocols", get(get_yield_protocols))
        .route("/yields/facets", get(get_yield_facets))
        .route("/yields/pool/:id", get(get_yield_pool))
        .route("/treasury", get(get_treasury))
        .route("/treasury/schedule", get(get_treasury_schedule))
        .route("/treasury/holders", get(get_treasury_holders))
        .route("/treasury/preview", get(get_treasury_preview))
        .route("/treasury/onchain", get(get_treasury_onchain))
        .route("/treasury/allocations", post(post_allocation))
        .route("/treasury/allocations/:id", delete(delete_allocation))
        .route("/treasury/participants", post(post_participant))
        .route("/treasury/bind", post(post_bind))
        .route("/treasury/lock", post(post_lock))
        .route("/treasury/distribute", post(post_distribute))
        .route("/treasury/claim", post(post_claim))
        .route("/treasury/register", post(post_register))
        .route("/hub", get(get_hub))
        .route("/hub/:id", get(get_hub_protocol))
        .route("/modules", get(get_modules))
        .route("/modules/facets", get(get_module_facets))
        .route("/modules/:id", get(get_module))
        .route("/modules/:id/quote", post(post_module_quote))
        .route("/modules/:id/enter", post(post_module_enter))
        .route("/positions", get(get_positions).post(post_position))
        .route("/positions/:id", get(get_position).delete(delete_position))
        .route("/positions/:id/exit", post(post_position_exit))
        .route("/positions/:id/value", get(get_position_value))
        .route("/mcp", get(mcp::describe).post(mcp::rpc))
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .with_state(state);

    let bind = std::env::var("BIND_HOST").unwrap_or_else(|_| "0.0.0.0".into());
    let listener = tokio::net::TcpListener::bind(format!("{bind}:{port}"))
        .await
        .unwrap_or_else(|e| {
            eprintln!("[defi] cannot bind {bind}:{port}: {e}");
            std::process::exit(1);
        });
    println!("[defi] api listening on {bind}:{port}");
    axum::serve(listener, app).await.unwrap();
}

async fn warm_compile(state: Shared) {
    let sources = state.catalog.sources.clone();
    let result = tokio::task::spawn_blocking(move || match compile::find_solc() {
        Some(solc) => compile::compile_sources(&solc, &sources),
        None => Err("no solc found — set DEFI_SOLC, or install one with `svm install 0.8.24`".into()),
    })
    .await;

    match result {
        Ok(Ok(out)) => {
            println!(
                "[defi] compiled {} contracts with {}",
                out.artifacts.len(),
                out.version
            );
            *state.compiled.write().await = Some(Arc::new(out));
        }
        Ok(Err(e)) => {
            eprintln!("[defi] catalog did not compile: {e}");
            *state.compile_error.write().await = Some(e);
        }
        Err(e) => {
            eprintln!("[defi] compile task failed: {e}");
            *state.compile_error.write().await = Some(e.to_string());
        }
    }
}

fn module_dir() -> std::path::PathBuf {
    if let Ok(explicit) = std::env::var("DEFI_MODULE_DIR") {
        return std::path::PathBuf::from(explicit);
    }
    // Walk up from the working directory looking for our config.json.
    let mut dir = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    for _ in 0..5 {
        if dir.join("config.json").is_file() && dir.join("src/api/blocks").is_dir() {
            return dir;
        }
        match dir.parent() {
            Some(p) => dir = p.to_path_buf(),
            None => break,
        }
    }
    std::path::PathBuf::from(".")
}

fn read_config(module_dir: &std::path::Path) -> serde_json::Value {
    std::fs::read_to_string(module_dir.join("config.json"))
        .ok()
        .and_then(|b| serde_json::from_str(&b).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

// ── auth plumbing ──────────────────────────────────────────────────────────

pub fn bearer(headers: &HeaderMap) -> Option<String> {
    headers
        .get("authorization")?
        .to_str()
        .ok()?
        .strip_prefix("Bearer ")
        .map(|t| t.trim().to_string())
}

/// The caller's address, or None for an anonymous visitor. Reading is public
/// here on purpose — a protocol design is a diagram, and the deploy button is
/// gated by the wallet, not by us.
pub fn caller(state: &AppState, headers: &HeaderMap) -> Option<String> {
    let token = bearer(headers)?;
    auth::verify_token(&state.secret, &token)
}

fn require_caller(state: &AppState, headers: &HeaderMap) -> Result<String, (StatusCode, Json<serde_json::Value>)> {
    caller(state, headers).ok_or_else(|| {
        (
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({ "error": "sign in with your wallet first" })),
        )
    })
}

fn bad(msg: impl Into<String>) -> (StatusCode, Json<serde_json::Value>) {
    (
        StatusCode::BAD_REQUEST,
        Json(serde_json::json!({ "error": msg.into() })),
    )
}

// ── basic surface ──────────────────────────────────────────────────────────

async fn health(State(state): State<Shared>) -> Json<serde_json::Value> {
    let compiled = state.compiled.read().await;
    Json(serde_json::json!({
        "status": "ok",
        "module": "defi",
        "version": state.version,
        "protocol": state.catalog.protocol,
        "blocks": state.catalog.blocks.len(),
        "compiled": compiled.is_some(),
    }))
}

async fn config_handler(State(state): State<Shared>) -> Json<serde_json::Value> {
    Json(read_config(&state.module_dir))
}

async fn owner_handler(State(state): State<Shared>, headers: HeaderMap) -> Json<serde_json::Value> {
    let who = caller(&state, &headers);
    Json(serde_json::json!({
        "owner": state.owner,
        "caller": who,
        "is_owner": who.map(|w| w == state.owner).unwrap_or(false),
    }))
}

async fn get_catalog(State(state): State<Shared>) -> Json<serde_json::Value> {
    Json(state.catalog.summary())
}

async fn get_block(
    State(state): State<Shared>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let block = state
        .catalog
        .block(&id)
        .ok_or_else(|| bad(format!("no block '{id}'")))?;
    let compiled = state.compiled.read().await;
    let artifact = compiled
        .as_ref()
        .and_then(|c| c.artifacts.get(&block.contract).cloned());
    Ok(Json(serde_json::json!({
        "block": block,
        "artifact": artifact,
        "audit": state.catalog.audit(&id),
    })))
}

async fn get_block_audit(
    State(state): State<Shared>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    if state.catalog.block(&id).is_none() && id != "common" {
        return Err(bad(format!("no block '{id}'")));
    }
    let audit = state.catalog.audit(&id).ok_or_else(|| {
        (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": format!("block '{id}' has no audit yet") })),
        )
    })?;
    Ok(Json(audit.clone()))
}

async fn get_audits(State(state): State<Shared>) -> Json<serde_json::Value> {
    Json(state.catalog.audits_overview())
}

async fn get_templates(State(state): State<Shared>) -> Json<serde_json::Value> {
    Json(serde_json::json!({ "templates": state.catalog.templates }))
}

async fn compile_status(State(state): State<Shared>) -> Json<serde_json::Value> {
    let compiled = state.compiled.read().await;
    let error = state.compile_error.read().await;
    Json(serde_json::json!({
        "ready": compiled.is_some(),
        "solc": compiled.as_ref().map(|c| c.solc.clone()),
        "version": compiled.as_ref().map(|c| c.version.clone()),
        "contracts": compiled.as_ref().map(|c| c.artifacts.len()).unwrap_or(0),
        "warnings": compiled.as_ref().map(|c| c.warnings.clone()).unwrap_or_default(),
        "error": error.clone(),
    }))
}

// ── design surface ─────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct GraphBody {
    #[serde(default)]
    graph: Option<graph::Graph>,
    #[serde(flatten)]
    inline: serde_json::Value,
}

fn take_graph(body: GraphBody) -> Result<graph::Graph, (StatusCode, Json<serde_json::Value>)> {
    if let Some(g) = body.graph {
        return Ok(g);
    }
    serde_json::from_value(body.inline).map_err(|e| bad(format!("bad graph: {e}")))
}

async fn post_validate(
    State(state): State<Shared>,
    Json(body): Json<GraphBody>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let g = take_graph(body)?;
    Ok(Json(serde_json::to_value(graph::validate(&state.catalog, &g)).unwrap()))
}

async fn post_plan(
    State(state): State<Shared>,
    Json(body): Json<GraphBody>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let g = take_graph(body)?;
    let report = graph::validate(&state.catalog, &g);
    if !report.ok {
        return Ok(Json(serde_json::json!({
            "ok": false,
            "report": report,
            "error": "fix the wiring before deploying",
        })));
    }
    let compiled = state.compiled.read().await;
    let Some(compiled) = compiled.as_ref() else {
        let error = state.compile_error.read().await.clone();
        return Err((
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": error.unwrap_or_else(|| "still compiling the catalog — retry in a moment".into())
            })),
        ));
    };
    let plan = graph::plan(&state.catalog, &g, &compiled.artifacts, &report)
        .map_err(|e| bad(e))?;
    Ok(Json(serde_json::json!({ "ok": true, "report": report, "plan": plan })))
}

// ── saved protocols ────────────────────────────────────────────────────────

async fn list_protocols(State(state): State<Shared>) -> Json<serde_json::Value> {
    Json(serde_json::json!({ "protocols": state.store.list() }))
}

#[derive(Deserialize)]
struct SaveBody {
    #[serde(default)]
    id: Option<String>,
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    description: Option<String>,
    graph: graph::Graph,
}

async fn save_protocol(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<SaveBody>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    let now = auth::now();
    let id = body.id.clone().unwrap_or_else(|| format!("p-{now}-{}", &who[2..8.min(who.len())]));

    let existing = state.store.get(&id);
    if let Some(prev) = &existing {
        if prev.owner != who && who != state.owner {
            return Err((
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({ "error": "that protocol belongs to someone else" })),
            ));
        }
    }

    let protocol = storage::Protocol {
        id: id.clone(),
        name: body
            .name
            .clone()
            .or_else(|| existing.as_ref().map(|p| p.name.clone()))
            .unwrap_or_else(|| {
                if body.graph.name.is_empty() { "Untitled protocol".into() } else { body.graph.name.clone() }
            }),
        description: body
            .description
            .clone()
            .unwrap_or_else(|| existing.as_ref().map(|p| p.description.clone()).unwrap_or_default()),
        owner: existing.as_ref().map(|p| p.owner.clone()).unwrap_or(who),
        created: existing.as_ref().map(|p| p.created).unwrap_or(now),
        updated: now,
        graph: body.graph,
        cid: None, // content changed; a stale CID would be a lie
        deployments: existing.map(|p| p.deployments).unwrap_or_default(),
        imported_from: None,
    };
    state.store.save(&protocol).map_err(|e| bad(e))?;
    Ok(Json(serde_json::json!({ "ok": true, "protocol": protocol })))
}

async fn get_protocol(
    State(state): State<Shared>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    state
        .store
        .get(&id)
        .map(|p| Json(serde_json::json!({ "protocol": p })))
        .ok_or_else(|| {
            (
                StatusCode::NOT_FOUND,
                Json(serde_json::json!({ "error": format!("no protocol '{id}'") })),
            )
        })
}

async fn delete_protocol(
    State(state): State<Shared>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    let Some(protocol) = state.store.get(&id) else {
        return Err(bad("no such protocol"));
    };
    if protocol.owner != who && who != state.owner {
        return Err((
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({ "error": "not yours to delete" })),
        ));
    }
    state.store.delete(&id).map_err(|e| bad(e))?;
    Ok(Json(serde_json::json!({ "ok": true })))
}

async fn publish_protocol(
    State(state): State<Shared>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    let Some(mut protocol) = state.store.get(&id) else {
        return Err(bad("no such protocol"));
    };
    if protocol.owner != who && who != state.owner {
        return Err((
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({ "error": "not yours to publish" })),
        ));
    }
    // Publish the DESIGN, not the local bookkeeping — a shared CID should
    // reproduce the same diagram for whoever imports it, and nothing else.
    let payload = serde_json::json!({
        "kind": "defi/protocol",
        "version": 1,
        "name": protocol.name,
        "description": protocol.description,
        "graph": protocol.graph,
        "catalog": state.catalog.version,
    });
    let bytes = serde_json::to_vec(&payload).map_err(|e| bad(e.to_string()))?;
    let cid = state.store.put_object(&bytes).map_err(|e| bad(e))?;
    protocol.cid = Some(cid.clone());
    state.store.save(&protocol).map_err(|e| bad(e))?;
    Ok(Json(serde_json::json!({
        "ok": true,
        "cid": cid,
        "share": format!("/defi?import={cid}"),
        "bytes": bytes.len(),
    })))
}

#[derive(Deserialize)]
struct ImportBody {
    cid: String,
}

async fn import_protocol(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<ImportBody>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    let Some(bytes) = state.store.get_object(&body.cid) else {
        return Err((
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({
                "error": format!("{} is not in this node's object store", body.cid)
            })),
        ));
    };
    let payload: serde_json::Value = serde_json::from_slice(&bytes).map_err(|e| bad(e.to_string()))?;
    let g: graph::Graph =
        serde_json::from_value(payload.get("graph").cloned().unwrap_or_default())
            .map_err(|e| bad(format!("object is not a protocol: {e}")))?;
    let now = auth::now();
    let protocol = storage::Protocol {
        id: format!("p-{now}-import"),
        name: payload
            .get("name")
            .and_then(|n| n.as_str())
            .unwrap_or("Imported protocol")
            .to_string(),
        description: payload
            .get("description")
            .and_then(|d| d.as_str())
            .unwrap_or("")
            .to_string(),
        owner: who,
        created: now,
        updated: now,
        graph: g,
        cid: Some(body.cid.clone()),
        deployments: vec![],
        imported_from: Some(body.cid),
    };
    state.store.save(&protocol).map_err(|e| bad(e))?;
    Ok(Json(serde_json::json!({ "ok": true, "protocol": protocol })))
}

async fn get_object(
    State(state): State<Shared>,
    Path(cid): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let Some(bytes) = state.store.get_object(&cid) else {
        return Err((
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": "unknown cid" })),
        ));
    };
    let value: serde_json::Value = serde_json::from_slice(&bytes).map_err(|e| bad(e.to_string()))?;
    Ok(Json(value))
}

#[derive(Deserialize)]
struct DeploymentBody {
    #[serde(rename = "chainId")]
    chain_id: u64,
    #[serde(default)]
    network: String,
    addresses: serde_json::Map<String, serde_json::Value>,
    #[serde(default)]
    txs: Vec<String>,
}

async fn record_deployment(
    State(state): State<Shared>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(body): Json<DeploymentBody>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    let Some(mut protocol) = state.store.get(&id) else {
        return Err(bad("no such protocol"));
    };
    protocol.deployments.push(storage::Deployment {
        chain_id: body.chain_id,
        network: body.network,
        at: auth::now(),
        deployer: who,
        addresses: body.addresses,
        txs: body.txs,
    });
    protocol.updated = auth::now();
    state.store.save(&protocol).map_err(|e| bad(e))?;
    Ok(Json(serde_json::json!({ "ok": true, "protocol": protocol })))
}

// ── wallet auth ────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct ChallengeQuery {
    address: String,
}

async fn auth_challenge(
    State(state): State<Shared>,
    Query(q): Query<ChallengeQuery>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    if !q.address.starts_with("0x") || q.address.len() != 42 {
        return Err(bad("address must be a 0x-prefixed 20-byte hex string"));
    }
    Ok(Json(serde_json::json!({ "message": state.challenges.issue(&q.address) })))
}

#[derive(Deserialize)]
struct VerifyBody {
    address: String,
    signature: String,
}

async fn auth_verify(
    State(state): State<Shared>,
    Json(body): Json<VerifyBody>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let Some(message) = state.challenges.take(&body.address) else {
        return Err(bad("no live challenge for that address — request a new one"));
    };
    let recovered = auth::recover(&message, &body.signature).map_err(|e| bad(e))?;
    if recovered != body.address.to_lowercase() {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({ "error": "signature does not match that address" })),
        ));
    }
    Ok(Json(serde_json::json!({
        "token": auth::mint_token(&state.secret, &recovered),
        "address": recovered,
        "expires_in": auth::TOKEN_TTL,
    })))
}

async fn whoami(State(state): State<Shared>, headers: HeaderMap) -> Json<serde_json::Value> {
    let who = caller(&state, &headers);
    Json(serde_json::json!({
        "address": who,
        "is_owner": who.as_deref().map(|w| w == state.owner).unwrap_or(false),
    }))
}

// ── agent protocol ─────────────────────────────────────────────────────────

async fn agent_status(State(state): State<Shared>) -> Json<serde_json::Value> {
    Json(state.agent.status().await)
}

async fn agent_prompts(
    State(state): State<Shared>,
    headers: HeaderMap,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = bearer(&headers);
    match state.agent.prompts(token.as_deref()).await {
        Ok(prompts) => Ok(Json(serde_json::json!({
            "prompts": prompts,
            "source": state.agent.base,
        }))),
        Err(e) => Err((
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": e, "source": state.agent.base })),
        )),
    }
}

async fn agent_prompt(
    State(state): State<Shared>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = bearer(&headers);
    match state.agent.prompt(&id, token.as_deref()).await {
        Ok(p) => Ok(Json(serde_json::json!({ "prompt": p }))),
        Err(e) => Err((StatusCode::NOT_FOUND, Json(serde_json::json!({ "error": e })))),
    }
}

async fn agent_import_prompt(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<ImportBody>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    require_caller(&state, &headers)?;
    let token = bearer(&headers);
    match state.agent.import(&body.cid, token.as_deref()).await {
        Ok(v) => Ok(Json(v)),
        Err(e) => Err((StatusCode::BAD_GATEWAY, Json(serde_json::json!({ "error": e })))),
    }
}

#[derive(Deserialize)]
struct ComposeBody {
    #[serde(default)]
    prompt: String,
    /// Optional prompt from the agent library, prepended as the system framing.
    #[serde(default, rename = "promptId")]
    prompt_id: Option<String>,
    /// Existing graph to modify rather than replace.
    #[serde(default)]
    graph: Option<graph::Graph>,
}

/// Turn a sentence into a protocol graph, using the agent module as the brain.
/// The catalog is described inline so the agent can only reach for blocks that
/// actually exist — and whatever comes back is run through the same validator
/// as a hand-drawn graph before it is offered to the user.
async fn agent_compose(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<ComposeBody>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    if body.prompt.trim().is_empty() && body.prompt_id.is_none() {
        return Err(bad("say what you want to build"));
    }
    let token = bearer(&headers);

    let mut preamble = String::new();
    if let Some(id) = &body.prompt_id {
        match state.agent.prompt(id, token.as_deref()).await {
            Ok(p) => {
                preamble.push_str(&p.text);
                preamble.push_str("\n\n");
            }
            Err(e) => return Err(bad(format!("could not load prompt '{id}': {e}"))),
        }
    }

    let blocks: Vec<serde_json::Value> = state
        .catalog
        .blocks
        .iter()
        .map(|b| {
            serde_json::json!({
                "block": b.id,
                "name": b.name,
                "summary": b.summary,
                "provides": b.provides,
                "inputs": b.inputs.iter().map(|i| serde_json::json!({
                    "port": i.id, "type": i.port_type, "required": i.required
                })).collect::<Vec<_>>(),
                "params": b.params.iter().map(|p| serde_json::json!({
                    "name": p.name, "type": p.param_type, "default": p.default
                })).collect::<Vec<_>>(),
            })
        })
        .collect();

    let existing = body
        .graph
        .as_ref()
        .map(|g| serde_json::to_string(g).unwrap_or_default())
        .unwrap_or_else(|| "null".into());

    let ask = format!(
        "{preamble}You compose DeFi protocols out of a fixed catalog of Solidity blocks.\n\
         Return ONLY a JSON object: {{\"name\":string,\"description\":string,\"nodes\":[{{\"id\":string,\"block\":string,\"x\":number,\"y\":number,\"params\":object}}],\"edges\":[{{\"from\":nodeId,\"to\":nodeId,\"port\":portId}}]}}\n\
         Rules: `block` must be one of the catalog ids. An edge's `port` must be an input port on the `to` node, and the `from` node must PROVIDE that port's type. Fill every required port. Lay nodes out left to right, x in steps of 320 and y in steps of 200, starting at 60,60.\n\n\
         CATALOG:\n{}\n\n\
         EXISTING GRAPH (modify it if it is not null):\n{existing}\n\n\
         REQUEST: {}",
        serde_json::to_string_pretty(&blocks).unwrap_or_default(),
        body.prompt
    );

    let reply = state
        .agent
        .ask(&ask, token.as_deref())
        .await
        .map_err(|e| (StatusCode::BAD_GATEWAY, Json(serde_json::json!({ "error": e }))))?;

    let Some(parsed) = agentlink::extract_json(&reply) else {
        return Err((
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({
                "error": "the agent did not return a protocol graph",
                "reply": reply,
            })),
        ));
    };
    let g: graph::Graph = serde_json::from_value(parsed.clone())
        .map_err(|e| bad(format!("agent returned an unusable graph: {e}")))?;

    // Never hand back something we would refuse to deploy without saying so.
    let report = graph::validate(&state.catalog, &g);
    Ok(Json(serde_json::json!({
        "graph": g,
        "report": report,
        "raw": parsed,
    })))
}

// ── the trading desk ───────────────────────────────────────────────────────
//
// Reads are open, exactly like the rest of this module: a price is not a
// secret. Trading is gated by the chain module that holds the key, and the
// caller's own Authorization header is what reaches it — this service adds no
// credential of its own, because it has none.

/// The peer's token: an explicit `auth` on the call, else whatever the caller
/// sent us. Passing it per-call matters because eth, solana and bt each have
/// their own door, and one bearer rarely opens all three.
fn peer_token(headers: &HeaderMap, body: &serde_json::Value) -> Option<String> {
    body.get("auth")
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| bearer(headers))
}

fn dex_err(e: String) -> (StatusCode, Json<serde_json::Value>) {
    // 400, not 502: an unreachable venue or a locked account is something the
    // caller can act on, and a 5xx body gets eaten before it reaches them.
    (
        StatusCode::BAD_REQUEST,
        Json(serde_json::json!({ "error": e })),
    )
}

#[derive(Deserialize)]
struct VenuesQuery {
    #[serde(default)]
    check: Option<String>,
}

async fn dex_venues(
    State(state): State<Shared>,
    Query(q): Query<VenuesQuery>,
) -> Json<serde_json::Value> {
    let check = q
        .check
        .map(|c| !matches!(c.as_str(), "" | "0" | "false" | "no"))
        .unwrap_or(false);
    Json(state.dex.venues(check).await)
}

#[derive(Deserialize)]
struct ChainQuery {
    #[serde(default)]
    chain: Option<String>,
    #[serde(flatten)]
    rest: serde_json::Value,
}

async fn dex_tokens(
    State(state): State<Shared>,
    Query(q): Query<ChainQuery>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    state
        .dex
        .tokens(q.chain.as_deref())
        .map(Json)
        .map_err(dex_err)
}

async fn dex_quote(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &body);
    state
        .dex
        .quote(&body, token.as_deref())
        .await
        .map(Json)
        .map_err(dex_err)
}

async fn dex_swap(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &body);
    state
        .dex
        .swap(&body, token.as_deref())
        .await
        .map(Json)
        .map_err(dex_err)
}

async fn dex_balances(
    State(state): State<Shared>,
    headers: HeaderMap,
    Query(q): Query<ChainQuery>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let mut args = q.rest.clone();
    if !args.is_object() {
        args = serde_json::json!({});
    }
    args["chain"] = serde_json::json!(q.chain.unwrap_or_default());
    let token = peer_token(&headers, &args);
    state
        .dex
        .balances(&args, token.as_deref())
        .await
        .map(Json)
        .map_err(dex_err)
}

// ── the yields table ───────────────────────────────────────────────────────
//
// Read-only and unauthenticated. An APR is a public fact about a public market,
// and putting a sign-in in front of it would only make the number harder to
// check.

fn yields_err(message: String) -> (StatusCode, Json<serde_json::Value>) {
    (
        StatusCode::BAD_GATEWAY,
        Json(serde_json::json!({ "error": message })),
    )
}

/// Query strings arrive as a flat map; the filter reads them out of a Value so
/// the same code serves the REST route and the MCP tool.
fn query_value(raw: &str) -> serde_json::Value {
    let mut map = serde_json::Map::new();
    for pair in raw.split('&').filter(|p| !p.is_empty()) {
        let (key, value) = match pair.split_once('=') {
            Some((k, v)) => (k, v),
            None => (pair, ""),
        };
        map.insert(
            urldecode(key),
            serde_json::Value::String(urldecode(value)),
        );
    }
    serde_json::Value::Object(map)
}

fn urldecode(text: &str) -> String {
    let bytes = text.replace('+', " ").into_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            if let Ok(byte) = u8::from_str_radix(&String::from_utf8_lossy(&bytes[i + 1..i + 3]), 16) {
                out.push(byte);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).to_string()
}

async fn get_yields(
    State(state): State<Shared>,
    raw: axum::extract::RawQuery,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let filter = yields::Filter::from_query(&query_value(&raw.0.unwrap_or_default()));
    state.yields.pools(&filter).await.map(Json).map_err(yields_err)
}

async fn get_yield_protocols(
    State(state): State<Shared>,
    raw: axum::extract::RawQuery,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let filter = yields::Filter::from_query(&query_value(&raw.0.unwrap_or_default()));
    state.yields.protocols(&filter).await.map(Json).map_err(yields_err)
}

async fn get_yield_facets(
    State(state): State<Shared>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    state.yields.facets().await.map(Json).map_err(yields_err)
}

async fn get_yield_pool(
    State(state): State<Shared>,
    Path(id): Path<String>,
    raw: axum::extract::RawQuery,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let query = query_value(&raw.0.unwrap_or_default());
    let history = query.get("history").and_then(|v| v.as_str()) != Some("0");
    state.yields.pool(&id, history).await.map(Json).map_err(yields_err)
}

// ── the hub ────────────────────────────────────────────────────────────────
//
// The curated front door: hand-vetted protocols joined live with the index.
// Read-only and unauthenticated, like the rest of the tables.

async fn get_hub(
    State(state): State<Shared>,
    raw: axum::extract::RawQuery,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let query = query_value(&raw.0.unwrap_or_default());
    let chain = query.get("chain").and_then(|v| v.as_str()).filter(|c| !c.is_empty()).map(|c| c.to_lowercase());
    let min_tvl = query
        .get("min_tvl")
        .and_then(|v| v.as_str())
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(1_000_000.0);
    let (pools, fetched) = state.yields.all().await.map_err(yields_err)?;
    Ok(Json(state.hub.assemble(&pools, &state.finance.registry, fetched, chain.as_deref(), min_tvl)))
}

async fn get_hub_protocol(
    State(state): State<Shared>,
    Path(id): Path<String>,
    raw: axum::extract::RawQuery,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let query = query_value(&raw.0.unwrap_or_default());
    let min_tvl = query
        .get("min_tvl")
        .and_then(|v| v.as_str())
        .and_then(|v| v.parse::<f64>().ok())
        .unwrap_or(1_000_000.0);
    let (pools, fetched) = state.yields.all().await.map_err(yields_err)?;
    state
        .hub
        .protocol(&id, &pools, &state.finance.registry, fetched, min_tvl)
        .map(Json)
        .map_err(|e| (StatusCode::NOT_FOUND, Json(serde_json::json!({ "error": e }))))
}

// ── the treasury ───────────────────────────────────────────────────────────
//
// Reads are open, like the rest of this module: the schedule and the split are
// facts about a shared pot, and hiding them would only make the payout harder
// to audit. Writes need a signed-in wallet, and the ones that touch a chain
// carry the CALLER'S eth token — never one of ours, because there is none.

async fn get_treasury(
    State(state): State<Shared>,
    headers: HeaderMap,
) -> Json<serde_json::Value> {
    let token = peer_token(&headers, &serde_json::Value::Null);
    Json(state.treasury.desk(&state.dex, auth::now(), token.as_deref()).await)
}

async fn get_treasury_schedule(
    State(state): State<Shared>,
    raw: axum::extract::RawQuery,
) -> Json<serde_json::Value> {
    let weeks = query_value(&raw.0.unwrap_or_default())
        .get("weeks")
        .and_then(|v| v.as_str())
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(8);
    Json(state.treasury.schedule(weeks, auth::now()))
}

async fn get_treasury_holders(
    State(state): State<Shared>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    state.treasury.holders().await.map(Json).map_err(yields_err)
}

async fn get_treasury_preview(
    State(state): State<Shared>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    state
        .treasury
        .preview(auth::now())
        .await
        .map(Json)
        .map_err(yields_err)
}

async fn get_treasury_onchain(
    State(state): State<Shared>,
    headers: HeaderMap,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &serde_json::Value::Null);
    state
        .treasury
        .onchain(&state.dex, token.as_deref())
        .await
        .map(Json)
        .map_err(dex_err)
}

async fn post_allocation(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    state
        .treasury
        .choose(&body, &who, auth::now())
        .map(|a| Json(serde_json::json!({ "ok": true, "allocation": a.view(auth::now()) })))
        .map_err(bad)
}

async fn delete_allocation(
    State(state): State<Shared>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    let allocation = state
        .treasury
        .get(&id)
        .ok_or_else(|| bad(format!("no allocation '{id}'")))?;
    if allocation.owner != who && who != state.owner {
        return Err(bad("that allocation belongs to someone else"));
    }
    // A locked allocation is not a row you can delete — the principal is in a
    // contract that will not give it back early, and a ledger that forgot it
    // would be lying about where the money is.
    if allocation.status == "locked" {
        return Err(bad(
            "this one is locked on chain — the ledger cannot un-lock it, and deleting the row \
             would only hide it. Wait out the term and withdraw, or let it stream.",
        ));
    }
    state.treasury.delete(&id).map_err(bad)?;
    Ok(Json(serde_json::json!({ "ok": true, "deleted": id })))
}

async fn post_participant(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    require_caller(&state, &headers)?;
    let address = body
        .get("address")
        .and_then(|v| v.as_str())
        .ok_or_else(|| bad("'address' is required"))?;
    let remove = body.get("remove").and_then(|v| v.as_bool()).unwrap_or(false);
    let list = if remove {
        state.treasury.remove_participant(address)
    } else {
        state.treasury.add_participant(address)
    }
    .map_err(bad)?;
    Ok(Json(serde_json::json!({ "ok": true, "watched": list })))
}

async fn post_bind(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    let existing = state.treasury.binding();
    // The first signed-in wallet may bind an empty node; after that it is
    // whoever bound it, or the module owner. A treasury address is the one
    // piece of node config that decides where money goes.
    if !existing.address.is_empty()
        && !existing.bound_by.eq_ignore_ascii_case(&who)
        && who != state.owner
    {
        return Err(bad(format!(
            "a treasury is already bound here by {} — only they or the module owner can repoint it",
            existing.bound_by
        )));
    }
    state
        .treasury
        .bind(&body, &who, auth::now())
        .map(|b| Json(serde_json::json!({ "ok": true, "binding": b })))
        .map_err(bad)
}

/// The chain-touching writes. Each one needs the name of an `eth` account and
/// carries the caller's eth bearer; this module has no key and mints no token.
async fn post_lock(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    let token = peer_token(&headers, &body);
    let id = body
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| bad("'id' is required — the allocation to lock"))?;
    let account = body
        .get("account")
        .and_then(|v| v.as_str())
        .ok_or_else(|| bad("'account' is required — the name of an eth-module account"))?;
    let mut allocation = state
        .treasury
        .get(id)
        .ok_or_else(|| bad(format!("no allocation '{id}'")))?;
    if allocation.owner != who && who != state.owner {
        return Err(bad("that allocation belongs to someone else"));
    }
    if allocation.status == "locked" {
        return Err(bad("that allocation is already locked"));
    }
    let confirm = body.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false);
    let out = state
        .treasury
        .lock_onchain(
            &state.dex,
            &allocation,
            account,
            confirm,
            body.get("password"),
            token.as_deref(),
        )
        .await
        .map_err(dex_err)?;

    let binding = state.treasury.binding();
    allocation.status = "locked".into();
    allocation.updated = auth::now();
    allocation.treasury = Some(binding.address.clone());
    allocation.network = Some(binding.network.clone());
    allocation.tx = out
        .pointer("/result/hash")
        .or_else(|| out.pointer("/result/tx"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    state.treasury.save(&allocation).map_err(bad)?;

    Ok(Json(serde_json::json!({
        "ok": true,
        "allocation": allocation.view(auth::now()),
        "chain": out,
    })))
}

async fn post_distribute(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &body);
    let account = body
        .get("account")
        .and_then(|v| v.as_str())
        .ok_or_else(|| bad("'account' is required — the name of an eth-module account"))?;
    state
        .treasury
        .distribute_onchain(
            &state.dex,
            account,
            body.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false),
            body.get("password"),
            token.as_deref(),
            auth::now(),
        )
        .await
        .map(Json)
        .map_err(dex_err)
}

async fn post_claim(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &body);
    let account = body
        .get("account")
        .and_then(|v| v.as_str())
        .ok_or_else(|| bad("'account' is required — the name of an eth-module account"))?;
    state
        .treasury
        .claim_onchain(
            &state.dex,
            account,
            body.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false),
            body.get("password"),
            token.as_deref(),
        )
        .await
        .map(Json)
        .map_err(dex_err)
}

async fn post_register(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &body);
    let account = body
        .get("account")
        .and_then(|v| v.as_str())
        .ok_or_else(|| bad("'account' is required — the name of an eth-module account"))?;
    state
        .treasury
        .register_onchain(
            &state.dex,
            account,
            body.get("who").and_then(|v| v.as_str()),
            body.get("confirm").and_then(|v| v.as_bool()).unwrap_or(false),
            body.get("password"),
            token.as_deref(),
        )
        .await
        .map(Json)
        .map_err(dex_err)
}

// ── modular finance ────────────────────────────────────────────────────────
//
// Every place money can go, as a module with its own returns, liquidity and
// conditions; and the positions that went in through here. Reads are open.
// Entering and leaving forward the CALLER'S chain-module token, like the desk.

async fn get_modules(
    State(state): State<Shared>,
    raw: axum::extract::RawQuery,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let filter = finance::Filter::from_query(&query_value(&raw.0.unwrap_or_default()));
    state
        .finance
        .modules(&filter, &state.yields, &state.dex, &state.store, &state.catalog, &state.treasury)
        .await
        .map(Json)
        .map_err(yields_err)
}

async fn get_module_facets(
    State(state): State<Shared>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    state
        .finance
        .facets(&state.yields, &state.dex, &state.store, &state.catalog, &state.treasury)
        .await
        .map(Json)
        .map_err(yields_err)
}

async fn get_module(
    State(state): State<Shared>,
    Path(id): Path<String>,
    raw: axum::extract::RawQuery,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let query = query_value(&raw.0.unwrap_or_default());
    let history = query.get("history").and_then(|v| v.as_str()) != Some("0");
    state
        .finance
        .module(&id, &state.yields, &state.dex, &state.store, &state.catalog, &state.treasury, history)
        .await
        .map(Json)
        .map_err(|e| (StatusCode::NOT_FOUND, Json(serde_json::json!({ "error": e }))))
}

async fn post_module_quote(
    State(state): State<Shared>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &body);
    let module = state
        .finance
        .module(&id, &state.yields, &state.dex, &state.store, &state.catalog, &state.treasury, false)
        .await
        .map_err(bad)?;
    state
        .finance
        .quote(&module, &body, &state.dex, token.as_deref())
        .await
        .map(Json)
        .map_err(dex_err)
}

async fn post_module_enter(
    State(state): State<Shared>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let mut body = body;
    body["module"] = serde_json::json!(id);
    post_position(State(state), headers, Json(body)).await
}

async fn post_position(
    State(state): State<Shared>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &body);
    let who = caller(&state, &headers);
    let id = body
        .get("module")
        .and_then(|v| v.as_str())
        .ok_or_else(|| bad("'module' is required — an id from /modules"))?;
    let module = state
        .finance
        .module(id, &state.yields, &state.dex, &state.store, &state.catalog, &state.treasury, false)
        .await
        .map_err(bad)?;
    state
        .finance
        .enter(&module, &body, who.as_deref(), &state.dex, &state.treasury, token.as_deref())
        .await
        .map(Json)
        .map_err(dex_err)
}

async fn get_positions(
    State(state): State<Shared>,
    headers: HeaderMap,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = caller(&state, &headers);
    state.finance.positions(&state.yields, who.as_deref()).await.map(Json).map_err(yields_err)
}

async fn get_position(
    State(state): State<Shared>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    state
        .finance
        .get(&id)
        .map(|p| Json(serde_json::json!({ "position": p })))
        .ok_or_else(|| (StatusCode::NOT_FOUND, Json(serde_json::json!({ "error": format!("no position '{id}'") }))))
}

async fn delete_position(
    State(state): State<Shared>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let who = require_caller(&state, &headers)?;
    state
        .finance
        .forget(&id, Some(&who), &state.owner)
        .map(|_| Json(serde_json::json!({ "ok": true, "forgotten": id, "note": "the ledger row is gone; whatever is on chain is still there" })))
        .map_err(bad)
}

async fn post_position_exit(
    State(state): State<Shared>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = peer_token(&headers, &body);
    state.finance.exit(&id, &body, &state.dex, token.as_deref()).await.map(Json).map_err(dex_err)
}

async fn get_position_value(
    State(state): State<Shared>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let token = bearer(&headers);
    state.finance.value(&id, &state.dex, token.as_deref()).await.map(Json).map_err(dex_err)
}
