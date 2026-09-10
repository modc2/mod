pub mod bridges;
pub mod config;
pub mod keys;
pub mod rates;
pub mod routes;
pub mod store;
pub mod types;

use std::sync::Arc;

pub use config::Config;
pub use rates::RatesCache;
pub use store::OrderStore;

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub http: reqwest::Client,
    pub orders: Arc<OrderStore>,
    pub rates: Arc<RatesCache>,
}

pub fn router() -> axum::Router<AppState> {
    routes::router()
}
