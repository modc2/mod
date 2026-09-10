//! freewash-fork-server — the Rust backend for the freewash-fork WebGL game.
//!
//! One axum server on a single port does two jobs:
//!
//!   1. Serves the static game (`web/index.html` and friends).
//!   2. Hosts the multiplayer relay at `GET /ws` — a pure fan-out: every
//!      client's state is rebroadcast to all the others, so real people show
//!      up in each other's parks.
//!
//! On top of the relay it keeps a **live player roster** (id → name). Joins,
//! leaves and the full roster are pushed to every client so the browser can
//! *show the players* who are currently in the park.
//!
//! Config via env (set by `mod.py`):
//!   FW_WEB_DIR   path to the web/ directory   (default: "web")
//!   FW_PORT      port to bind                 (default: 8799)
//!   FW_HOST      host/interface to bind       (default: 127.0.0.1)

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use axum::{
    extract::ws::{Message, WebSocket, WebSocketUpgrade},
    extract::State,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::{broadcast, RwLock};
use tower_http::{cors::CorsLayer, services::ServeDir};

/// A relayed message: `(sender_id, json)`. `sender_id == 0` is a system
/// message (join/leave/roster) and is delivered to *everyone*; any other id is
/// a player's own state echo and is skipped for that player.
type Relay = (u64, String);

#[derive(Clone)]
struct AppState {
    tx: broadcast::Sender<Relay>,
    /// id → display name of everyone currently connected.
    players: Arc<RwLock<HashMap<u64, String>>>,
    counter: Arc<AtomicU64>,
}

// Kensington street names, so the roster reads like the market.
const ADJS: &[&str] = &[
    "Augusta", "Baldwin", "Kensington", "Bellevue", "Nassau", "Oxford",
    "Denison", "Spadina", "StAndrew", "College", "Wales", "Fitzroy",
];
const NOUNS: &[&str] = &[
    "Skater", "Shopper", "Wanderer", "Drummer", "Local", "Busker",
    "Vendor", "Dreamer", "Rambler", "Cruiser", "Dancer", "Pigeon",
];

fn gen_name(id: u64) -> String {
    let a = ADJS[(id as usize) % ADJS.len()];
    let n = NOUNS[((id as usize) / ADJS.len()) % NOUNS.len()];
    format!("{a} {n}")
}

#[tokio::main]
async fn main() {
    let web_dir = std::env::var("FW_WEB_DIR").unwrap_or_else(|_| "web".into());
    let port: u16 = std::env::var("FW_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8799);
    let host = std::env::var("FW_HOST").unwrap_or_else(|_| "127.0.0.1".into());

    let (tx, _) = broadcast::channel::<Relay>(1024);
    let state = AppState {
        tx,
        players: Arc::new(RwLock::new(HashMap::new())),
        counter: Arc::new(AtomicU64::new(0)),
    };

    // The game routes + static file serving. `with_state` bakes in the shared
    // relay/roster state and turns this into a `Router<()>`.
    let game = Router::new()
        .route("/ws", get(ws_handler))
        .route("/mp.json", get(mp_json))
        .route("/players.json", get(players_json))
        .fallback_service(ServeDir::new(&web_dir).append_index_html_on_directories(true))
        .with_state(state);

    // Serve the same app at the root (local play) AND under `/freewash-fork`
    // (so it works behind the gateway, which proxies `modc2.com/freewash-fork/*`).
    // `nest` strips the `/freewash-fork` prefix *before* routing, which a plain
    // `layer` middleware cannot do — layers run after the route is matched.
    let app = Router::new()
        .nest("/freewash-fork", game.clone())
        .merge(game)
        .layer(CorsLayer::permissive());

    let addr: SocketAddr = format!("{host}:{port}")
        .parse()
        .expect("invalid FW_HOST/FW_PORT");
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .unwrap_or_else(|e| panic!("freewash-fork-server: cannot bind {addr}: {e}"));
    println!("freewash-fork-server listening on http://{addr}  (multiplayer relay at /ws)");
    axum::serve(listener, app).await.unwrap();
}

/// Multiplayer discovery: tells the browser to open a same-origin websocket at
/// `/ws` (no separate relay port — it's all one server now).
async fn mp_json() -> impl IntoResponse {
    Json(json!({ "ws_path": "/ws", "backend": "rust" }))
}

/// Plain HTTP view of who's in the park (handy for debugging / health checks).
async fn players_json(State(st): State<AppState>) -> impl IntoResponse {
    Json(json!({ "count": st.players.read().await.len(), "players": roster(&st).await }))
}

async fn ws_handler(ws: WebSocketUpgrade, State(st): State<AppState>) -> Response {
    ws.on_upgrade(move |socket| client(socket, st))
}

/// Snapshot of the roster as `[{id, name}, …]`, sorted by join order.
async fn roster(st: &AppState) -> Vec<Value> {
    let players = st.players.read().await;
    let mut v: Vec<Value> = players
        .iter()
        .map(|(id, name)| json!({ "id": id, "name": name }))
        .collect();
    v.sort_by_key(|x| x["id"].as_u64().unwrap_or(0));
    v
}

/// Push the full roster to every connected client.
async fn broadcast_roster(st: &AppState) {
    let players = roster(st).await;
    let msg = json!({ "type": "roster", "count": players.len(), "players": players });
    let _ = st.tx.send((0, msg.to_string()));
}

/// One connected player: assign an id + name, welcome them with the current
/// roster, fan their state out to everyone else, and announce join/leave.
async fn client(socket: WebSocket, st: AppState) {
    let id = st.counter.fetch_add(1, Ordering::Relaxed) + 1;
    let name = gen_name(id);
    st.players.write().await.insert(id, name.clone());

    let (mut sink, mut stream) = socket.split();
    let mut rx = st.tx.subscribe();

    // Welcome packet: your id/name + everyone already here.
    let welcome = json!({
        "type": "welcome",
        "id": id,
        "name": name,
        "players": roster(&st).await,
    });
    if sink.send(Message::Text(welcome.to_string())).await.is_err() {
        st.players.write().await.remove(&id);
        return;
    }

    // Tell the others someone arrived, then refresh everyone's roster.
    let _ = st
        .tx
        .send((id, json!({ "type": "join", "id": id, "name": name }).to_string()));
    broadcast_roster(&st).await;

    // Forward task: pump relayed messages out to this socket (skipping our own
    // state echoes). Lagged receivers just drop frames and carry on.
    let send_task = tokio::spawn(async move {
        loop {
            match rx.recv().await {
                Ok((from, msg)) => {
                    if from == id {
                        continue;
                    }
                    if sink.send(Message::Text(msg)).await.is_err() {
                        break;
                    }
                }
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
                Err(broadcast::error::RecvError::Closed) => break,
            }
        }
    });

    // Read loop: tag each incoming state with our id + name and relay it.
    while let Some(Ok(msg)) = stream.next().await {
        match msg {
            Message::Text(t) => {
                if let Ok(mut v) = serde_json::from_str::<Value>(&t) {
                    v["type"] = json!("state");
                    v["id"] = json!(id);
                    v["name"] = json!(name);
                    let _ = st.tx.send((id, v.to_string()));
                }
            }
            Message::Close(_) => break,
            _ => {}
        }
    }

    // Cleanup: drop the player and let everyone know.
    send_task.abort();
    st.players.write().await.remove(&id);
    let _ = st
        .tx
        .send((0, json!({ "type": "leave", "id": id }).to_string()));
    broadcast_roster(&st).await;
}
