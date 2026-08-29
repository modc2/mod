//! mod-api — the Rust (axum) front door to the mod protocol.
//!
//! Serves protocol identity and a live catalog of the mod ecosystem (every
//! module under `mod/orbit/`). Designed to sit behind the modc2.com gateway at
//! `/api/web` and back the Next.js app mounted at `/web`.

mod catalog;
mod chain;
mod embed;
mod owner;
mod routes;
mod staking;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use catalog::Catalog;
use chain::ChainClient;
use owner::OwnerAuth;
use routes::AppState;
use tower_http::cors::{Any, CorsLayer};

const VERSION: &str = env!("CARGO_PKG_VERSION");

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "mod_api=info,tower_http=warn".into()),
        )
        .init();

    let orbit_dir = resolve_orbit_dir();
    tracing::info!("orbit dir: {}", orbit_dir.display());

    let catalog = Arc::new(Catalog::new(orbit_dir));
    // Warm the cache once at boot so the first request is instant.
    let n = catalog.modules().len();
    tracing::info!("catalog warm: {n} modules");

    let chain = Arc::new(ChainClient::from_env());
    tracing::info!("chain hub: registry source configured");

    let semantic = Arc::new(embed::Semantic::from_env());
    tracing::info!(
        "semantic search: {}",
        if semantic.available() { "enabled (embeddings provider configured)" } else { "disabled (no provider — falling back to substring)" }
    );

    let owner = Arc::new(OwnerAuth::from_env());

    // Wallet-signed module backing: verifies personal_sign locally and checks
    // balances against the bloctime module.
    let staking = Arc::new(staking::Staking::from_env());
    tracing::info!("module backing: wallet-signed staking on {}", staking.network());

    let state = AppState {
        catalog,
        chain,
        semantic,
        owner,
        staking,
        version: VERSION,
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = routes::router(state).layer(cors);

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(50420);
    let addr = SocketAddr::from(([0, 0, 0, 0], port));

    let listener = tokio::net::TcpListener::bind(addr).await?;
    tracing::info!("mod-api v{VERSION} listening on http://{addr}");
    axum::serve(listener, app).await?;
    Ok(())
}

/// Locate the orbit directory. Prefers `MOD_ORBIT_DIR`, then a set of
/// candidates relative to the binary's working directory, then the path baked
/// in at compile time (relative to this crate).
fn resolve_orbit_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("MOD_ORBIT_DIR") {
        let p = PathBuf::from(dir);
        if p.is_dir() {
            return p;
        }
    }

    // Candidates relative to cwd (pm2 launches with cwd = api dir).
    let cwd_candidates = [
        "../../orbit",       // mod/core/web/api -> mod/orbit
        "../../../orbit",
        "mod/orbit",         // repo root
        "orbit",
    ];
    for c in cwd_candidates {
        let p = PathBuf::from(c);
        if p.is_dir() {
            if let Ok(abs) = std::fs::canonicalize(&p) {
                return abs;
            }
            return p;
        }
    }

    // Compile-time fallback: this crate lives at mod/core/web/api.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../orbit")
}
