//! ztensor API — anonymous voting & consensus for miner/validator networks.
//!
//! Rust port of server.py/mod.py. One binary, one port (:51180): JSON API +
//! the static Next.js console (dist/, atomic-swapped by build.sh; falls back
//! to app/index.html when no dist exists).
//!
//! Design stance — PRIVATE BALLOTS, PUBLIC BOOKS:
//!   * votes are LSAG-signed: the tally learns a member voted, never which;
//!   * payouts are transparent and auditable on purpose;
//!   * secret keys never reach this service — it stores public keys, tags
//!     and choices only (keygen/sign run in the browser or the caller).
//!
//! Routing: the gateway sends the console `/ztensor/*` (prefix kept) and the
//! API either `/ztensor/_api/*` (via the app route) or with `/ztensor/api`
//! stripped entirely (via the api route), so every API route is mounted at
//! `/`, `/api`, `/_api`, `/ztensor/api` and `/ztensor/_api`.

mod lsag;

use axum::{
    extract::{Query, State},
    http::{header, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use num_bigint::BigUint;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};
use tower_http::cors::CorsLayer;

// ── persistent state (same JSON schema as the python implementation) ──

#[derive(Clone, Serialize, Deserialize, Default)]
struct TopicState {
    ring: Vec<String>,                 // participant set frozen at first vote (hex)
    votes: BTreeMap<String, String>,   // key-image tag (hex) -> choice
}

#[derive(Clone, Serialize, Deserialize, Default)]
struct Books {
    participants: Vec<String>, // public keys, hex
    topics: BTreeMap<String, TopicState>,
}

struct App {
    module_dir: PathBuf,
    state_file: PathBuf,
    books: Mutex<Books>,
    config: Value,
}

impl App {
    fn save(&self, books: &Books) {
        if let Some(dir) = self.state_file.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(&self.state_file, serde_json::to_string_pretty(books).unwrap());
    }
}

fn load_books(state_file: &Path) -> Books {
    std::fs::read_to_string(state_file)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

// ── helpers ──

fn hex_to_big(s: &str) -> Option<BigUint> {
    let s = s.trim().trim_start_matches("0x");
    BigUint::parse_bytes(s.as_bytes(), 16)
}

/// Accept a signature field as a decimal/0x-hex string or a raw JSON int of
/// any size (python's json.dumps emits the latter; arbitrary_precision keeps
/// the digits).
fn value_to_big(v: &Value) -> Option<BigUint> {
    match v {
        Value::Number(n) => BigUint::parse_bytes(n.to_string().as_bytes(), 10),
        Value::String(s) => {
            let t = s.trim();
            if let Some(h) = t.strip_prefix("0x") {
                BigUint::parse_bytes(h.as_bytes(), 16)
            } else {
                BigUint::parse_bytes(t.as_bytes(), 10)
            }
        }
        _ => None,
    }
}

fn parse_sig(v: &Value) -> Option<lsag::Signature> {
    // the python client may send sig as a JSON-encoded string
    let owned;
    let v = if let Value::String(s) = v {
        owned = serde_json::from_str::<Value>(s).ok()?;
        &owned
    } else {
        v
    };
    Some(lsag::Signature {
        c0: value_to_big(v.get("c0")?)?,
        s: v.get("s")?.as_array()?.iter().map(value_to_big).collect::<Option<Vec<_>>>()?,
        tag: value_to_big(v.get("tag")?)?,
    })
}

fn tally_counts(tp: &TopicState) -> Vec<(String, usize)> {
    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for choice in tp.votes.values() {
        *counts.entry(choice).or_insert(0) += 1;
    }
    let mut v: Vec<(String, usize)> = counts.into_iter().map(|(k, n)| (k.to_string(), n)).collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    v
}

fn round_to(x: f64, places: i32) -> f64 {
    let f = 10f64.powi(places);
    (x * f).round() / f
}

// ── API handlers ──

async fn health() -> Json<Value> {
    Json(json!({"ok": true}))
}

async fn info(State(app): State<Arc<App>>) -> Json<Value> {
    let port = app.config.get("port").and_then(|p| p.as_u64()).unwrap_or(51180);
    Json(json!({
        "name": app.config.get("name").and_then(|n| n.as_str()).unwrap_or("ztensor"),
        "description": "Anonymous voting & consensus: members vote without revealing which member they are (LSAG); private ballots, public auditable payouts",
        "port": port,
        "url": format!("http://localhost:{port}/ztensor/"),
        "scheme": "LSAG over RFC3526 MODP-2048 (order-q QR subgroup)",
        "guarantees": ["anonymity", "per-topic double-vote resistance", "public auditable payouts"],
        "stack": {"api": "rust/axum", "console": "next.js static export"},
        "fns": app.config.get("fns").cloned().unwrap_or(Value::Null),
    }))
}

async fn get_set(State(app): State<Arc<App>>) -> Json<Value> {
    let books = app.books.lock().unwrap();
    Json(json!({"size": books.participants.len(), "participants": books.participants}))
}

async fn status(State(app): State<Arc<App>>) -> Json<Value> {
    let books = app.books.lock().unwrap();
    Json(json!({
        "participants": books.participants.len(),
        "topics": books.topics.keys().collect::<Vec<_>>(),
    }))
}

async fn topics(State(app): State<Arc<App>>) -> Json<Value> {
    let books = app.books.lock().unwrap();
    let list: Vec<Value> = books
        .topics
        .iter()
        .map(|(name, tp)| json!({"topic": name, "ring_size": tp.ring.len(), "votes": tp.votes.len()}))
        .collect();
    Json(json!({"topics": list}))
}

#[derive(Deserialize)]
struct TopicQ {
    #[serde(default)]
    topic: String,
    #[serde(default)]
    pool: Option<f64>,
}

/// The ring a vote on `topic` must be signed against: frozen at the topic's
/// first vote, else the current participant set. Clients sign against THIS,
/// not /set, or votes on in-progress topics fail verification.
async fn ring_for(State(app): State<Arc<App>>, Query(q): Query<TopicQ>) -> Json<Value> {
    let books = app.books.lock().unwrap();
    let (ring, frozen) = match books.topics.get(&q.topic) {
        Some(tp) => (tp.ring.clone(), true),
        None => (books.participants.clone(), false),
    };
    Json(json!({"topic": q.topic, "frozen": frozen, "size": ring.len(), "ring": ring}))
}

async fn tally(State(app): State<Arc<App>>, Query(q): Query<TopicQ>) -> Json<Value> {
    let books = app.books.lock().unwrap();
    match books.topics.get(&q.topic) {
        None => Json(json!({"topic": q.topic, "counts": {}, "total": 0})),
        Some(tp) => {
            let counts: BTreeMap<String, usize> = tally_counts(tp).into_iter().collect();
            Json(json!({"topic": q.topic, "counts": counts, "total": tp.votes.len()}))
        }
    }
}

async fn payout(State(app): State<Arc<App>>, Query(q): Query<TopicQ>) -> Json<Value> {
    let pool = q.pool.unwrap_or(0.0);
    let books = app.books.lock().unwrap();
    let (total, sorted) = match books.topics.get(&q.topic) {
        None => (0usize, vec![]),
        Some(tp) => (tp.votes.len(), tally_counts(tp)),
    };
    let mut instructions = Vec::new();
    if total > 0 {
        for (recipient, votes) in sorted {
            let share = votes as f64 / total as f64;
            instructions.push(json!({
                "recipient": recipient,
                "votes": votes,
                "share": round_to(share, 6),
                "amount": round_to(pool * share, 8),
            }));
        }
    }
    Json(json!({"topic": q.topic, "pool": pool, "total_votes": total, "instructions": instructions}))
}

#[derive(Deserialize)]
struct RegisterBody {
    #[serde(rename = "pub")]
    pub_key: Value,
}

async fn register(State(app): State<Arc<App>>, Json(body): Json<RegisterBody>) -> Response {
    let y = match &body.pub_key {
        Value::String(s) => hex_to_big(s),
        Value::Number(n) => BigUint::parse_bytes(n.to_string().as_bytes(), 10),
        _ => None,
    };
    let Some(y) = y else {
        return (StatusCode::BAD_REQUEST, Json(json!({"error": "pub must be a hex public key"}))).into_response();
    };
    let hexy = y.to_str_radix(16);
    let mut books = app.books.lock().unwrap();
    if !books.participants.contains(&hexy) {
        books.participants.push(hexy.clone());
        app.save(&books);
    }
    let index = books.participants.iter().position(|p| *p == hexy).unwrap();
    Json(json!({"index": index, "size": books.participants.len()})).into_response()
}

#[derive(Deserialize)]
struct VoteBody {
    topic: String,
    choice: String,
    sig: Value,
}

async fn vote(State(app): State<Arc<App>>, Json(body): Json<VoteBody>) -> Response {
    let Some(sig) = parse_sig(&body.sig) else {
        return Json(json!({"accepted": false, "reason": "invalid signature"})).into_response();
    };
    let mut books = app.books.lock().unwrap();
    let participants = books.participants.clone();
    let tp = books
        .topics
        .entry(body.topic.clone())
        .or_insert_with(|| TopicState { ring: participants, votes: BTreeMap::new() });

    let ring: Option<Vec<BigUint>> = tp.ring.iter().map(|p| hex_to_big(p)).collect();
    let Some(ring) = ring else {
        return Json(json!({"accepted": false, "reason": "corrupt ring"})).into_response();
    };
    let msg = format!("{}|{}", body.topic, body.choice);
    if !lsag::verify(&ring, body.topic.as_bytes(), msg.as_bytes(), &sig) {
        return Json(json!({"accepted": false, "reason": "invalid signature"})).into_response();
    }
    let tag = sig.tag.to_str_radix(16);
    if tp.votes.contains_key(&tag) {
        return Json(json!({"accepted": false, "reason": "double vote (tag already used for topic)"})).into_response();
    }
    tp.votes.insert(tag, body.choice);
    app.save(&books);
    Json(json!({"accepted": true, "topic": body.topic})).into_response()
}

/// End-to-end self-check with ephemeral keys; touches no stored state.
async fn self_test() -> Json<Value> {
    Json(run_self_test(false))
}

async fn demo() -> Json<Value> {
    Json(run_self_test(true))
}

fn run_self_test(explain: bool) -> Value {
    let keys: Vec<_> = (0..5).map(|_| lsag::keygen()).collect();
    let ring: Vec<BigUint> = keys.iter().map(|(_, y)| y.clone()).collect();
    let topic = b"reward-epoch-1";
    let msg = b"reward-epoch-1|minerA";

    let sigs: Vec<_> = [0usize, 2, 4]
        .iter()
        .map(|&i| lsag::sign(&keys[i].0, &ring, topic, msg).unwrap())
        .collect();
    let ok_all = sigs.iter().all(|s| lsag::verify(&ring, topic, msg, s));
    let tags: std::collections::BTreeSet<String> = sigs.iter().map(|s| s.tag.to_str_radix(16)).collect();

    let dup = lsag::sign(&keys[0].0, &ring, topic, b"reward-epoch-1|minerB").unwrap();
    let dup_caught = tags.contains(&dup.tag.to_str_radix(16));

    let (outsider, _) = lsag::keygen();
    let outsider_blocked = lsag::sign(&outsider, &ring, topic, b"x").is_err();

    let passed = ok_all && tags.len() == 3 && dup_caught && outsider_blocked;
    let mut out = json!({
        "passed": passed,
        "signatures_verified": ok_all,
        "distinct_voters": tags.len(),
        "double_vote_caught": dup_caught,
        "outsider_blocked": outsider_blocked,
    });
    if explain {
        out["explanation"] = json!(
            "5 members registered; 3 cast anonymous votes for the same miner. \
             The tally sees 3 valid votes but cannot tell which members voted. \
             A repeat vote by member 0 is rejected via its reused tag."
        );
    }
    out
}

// ── static console (Next.js export in dist/, legacy fallback app/index.html) ──

fn content_type(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "js" | "mjs" => "application/javascript",
        "css" => "text/css",
        "json" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "ico" => "image/x-icon",
        "txt" => "text/plain; charset=utf-8",
        "woff2" => "font/woff2",
        "map" => "application/json",
        "webmanifest" => "application/manifest+json",
        _ => "application/octet-stream",
    }
}

async fn static_handler(State(app): State<Arc<App>>, uri: Uri) -> Response {
    let raw = uri.path();
    // normalize: "/ztensor", "/ztensor/..." and "/ztensor.txt" (Next RSC
    // payload for the root route lives outside the /ztensor/ prefix)
    let rel = if raw == "/ztensor" || raw == "/ztensor/" {
        ""
    } else if raw == "/ztensor.txt" {
        "index.txt"
    } else if let Some(r) = raw.strip_prefix("/ztensor/") {
        r
    } else {
        raw.trim_start_matches('/')
    };
    if rel.contains("..") {
        return (StatusCode::BAD_REQUEST, "bad path").into_response();
    }

    let dist = app.module_dir.join("dist");
    let rel = if rel.is_empty() { "index.html" } else { rel };
    let candidates = [
        dist.join(rel),
        dist.join(format!("{rel}.html")),
        dist.join(rel).join("index.html"),
    ];
    for cand in &candidates {
        if cand.is_file() {
            if let Ok(bytes) = std::fs::read(cand) {
                return ([(header::CONTENT_TYPE, content_type(cand))], bytes).into_response();
            }
        }
    }
    // legacy single-file console
    let legacy = app.module_dir.join("app").join("index.html");
    if legacy.is_file() {
        if let Ok(bytes) = std::fs::read(&legacy) {
            return ([(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes).into_response();
        }
    }
    (StatusCode::NOT_FOUND, Json(json!({"error": "not found"}))).into_response()
}

// ── wiring ──

fn api_router() -> Router<Arc<App>> {
    Router::new()
        .route("/health", get(health))
        .route("/info", get(info))
        .route("/set", get(get_set))
        .route("/status", get(status))
        .route("/topics", get(topics))
        .route("/ring", get(ring_for))
        .route("/tally", get(tally))
        .route("/payout", get(payout))
        .route("/test", get(self_test))
        .route("/demo", get(demo))
        .route("/register", post(register))
        .route("/vote", post(vote))
}

#[tokio::main]
async fn main() {
    let module_dir = std::env::var("ZTENSOR_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| std::env::current_dir().unwrap());
    let config: Value = std::fs::read_to_string(module_dir.join("config.json"))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| json!({}));
    let port: u16 = std::env::var("ZTENSOR_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .or_else(|| config.get("port").and_then(|p| p.as_u64()).map(|p| p as u16))
        .unwrap_or(51180);
    let data_dir = std::env::var("ZTENSOR_DATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| module_dir.join("state"));
    let state_file = data_dir.join("state.json");

    let app = Arc::new(App {
        books: Mutex::new(load_books(&state_file)),
        module_dir,
        state_file,
        config,
    });

    let api = api_router();
    let router = Router::new()
        .nest("/api", api.clone())
        .nest("/_api", api.clone())
        .nest("/ztensor/api", api.clone())
        .nest("/ztensor/_api", api.clone())
        .merge(api) // gateway api route strips the /ztensor/api prefix
        .fallback(static_handler)
        .layer(CorsLayer::permissive())
        .with_state(app);

    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    println!("ztensor on :{port} -> http://localhost:{port}/ztensor/");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, router).await.unwrap();
}
