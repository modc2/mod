pub mod routes;
pub mod access;
pub mod proxy;
pub mod pipeline;
pub mod cache;
pub mod categories;
pub mod types;
pub mod first_trade;
pub mod strats;
pub mod auth;
pub mod signer;
pub mod order_signing;
pub mod clob_auth;
pub mod deposit_wallet;
pub mod relayer;
pub mod order_place;
pub mod user_strats;
pub mod share;
pub mod live_engine;
pub mod sync;
pub mod copy;
pub mod copy_actions;

use std::sync::Arc;

pub use cache::ProxyCache;
pub use pipeline::PipelineState;
pub use strats::StratStore;
pub use signer::SignerStore;
pub use live_engine::EngineRegistry;
pub use user_strats::UserStratStore;
pub use share::ShareStore;
pub use access::AccessStore;
pub use sync::SyncSchedule;
pub use copy::CopyBookStore;

#[derive(Clone)]
pub struct AppState {
    pub http: reqwest::Client,
    pub proxy_cache: Arc<ProxyCache>,
    pub pipeline: Arc<PipelineState>,
    pub strat_store: Arc<StratStore>,
    pub signer_store: Arc<SignerStore>,
    pub engines: Arc<EngineRegistry>,
    pub user_strats: Arc<UserStratStore>,
    /// Content-addressable backend for sharing strats by CID.
    pub share: ShareStore,
    /// Cadence + status of the background trader-data sync (sync.rs).
    pub sync: Arc<SyncSchedule>,
    /// The COPY BOOK — which traders this deployment copies and with how much
    /// (copy.rs). Server-owned and plaintext so the console and an MCP agent
    /// read and write the SAME desk.
    pub copy_book: Arc<CopyBookStore>,
}

pub fn router() -> axum::Router<AppState> {
    routes::router()
}
