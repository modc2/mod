pub mod auth;
pub mod infer;
pub mod jobs;
pub mod metrics;
pub mod models;
pub mod routes;

use std::sync::Arc;

use infer::InferPool;
use jobs::JobManager;
use metrics::Metrics;
use serde_json::Value;

#[derive(Clone)]
pub struct AppState {
    pub jobs: Arc<JobManager>,
    pub infer: Arc<InferPool>,
    pub metrics: Metrics,
    /// Cached model registry (from trainer/common.py).
    pub models: Arc<Value>,
    pub trainer_dir: String,
}

pub fn router() -> axum::Router<AppState> {
    routes::router()
}

/// The python interpreter used to run the trainer package. Override with
/// FREETUNE_PYTHON (e.g. a venv path).
pub fn trainer_python() -> String {
    std::env::var("FREETUNE_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

/// Directory containing the `trainer/` package (the module root). start.sh sets
/// FREETUNE_TRAINER_DIR; the fallback is the in-repo location.
pub fn trainer_dir() -> String {
    std::env::var("FREETUNE_TRAINER_DIR")
        .unwrap_or_else(|_| "/root/mod/mod/orbit/freetune".to_string())
}

/// Off-tree mutable state root (~/.mod/freetune). Mirrors trainer/common.py.
pub fn state_dir() -> String {
    std::env::var("FREETUNE_STATE_DIR").unwrap_or_else(|_| {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
        format!("{home}/.mod/freetune")
    })
}
