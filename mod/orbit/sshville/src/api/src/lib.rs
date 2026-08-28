pub mod auth;
pub mod routes;
pub mod ssh;
pub mod store;

use std::sync::Arc;

#[derive(Clone)]
pub struct AppState {
    pub store: Arc<store::Store>,
    pub keys: Arc<store::KeyStore>,
    pub challenge: String,
}
