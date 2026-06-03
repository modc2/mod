pub mod routes;
pub mod proxy;
pub mod pipeline;
pub mod cache;
pub mod categories;
pub mod types;
pub mod strats;
pub mod auth;
pub mod signer;
pub mod order_signing;
pub mod clob_auth;
pub mod deposit_wallet;
pub mod relayer;
pub mod order_place;
pub mod user_strats;
pub mod live_engine;

use std::sync::Arc;

pub use cache::ProxyCache;
pub use pipeline::PipelineState;
pub use strats::StratStore;
pub use signer::SignerStore;
pub use live_engine::EngineRegistry;
pub use user_strats::UserStratStore;

#[derive(Clone)]
pub struct AppState {
    pub http: reqwest::Client,
    pub proxy_cache: Arc<ProxyCache>,
    pub pipeline: Arc<PipelineState>,
    pub strat_store: Arc<StratStore>,
    pub signer_store: Arc<SignerStore>,
    pub engines: Arc<EngineRegistry>,
    pub user_strats: Arc<UserStratStore>,
}

pub fn router() -> axum::Router<AppState> {
    routes::router()
}
