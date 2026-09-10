mod bridge;
mod routes;

use axum::{
    routing::{delete, get, post},
    Router,
};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use tower_http::cors::{Any, CorsLayer};

use bridge::Bridge;
use routes::{AppStateInner, AppState};

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            std::env::var("RUST_LOG")
                .unwrap_or_else(|_| "wingman_api=info,tower_http=warn".to_string()),
        )
        .init();

    let port: u16 = std::env::args()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or_else(|| {
            std::env::var("WINGMAN_API_PORT")
                .ok()
                .and_then(|s| s.parse().ok())
                .unwrap_or(50830)
        });

    let bind_addr = std::env::var("WINGMAN_BIND").unwrap_or_else(|_| "0.0.0.0".to_string());

    // Mod dir: where mod.py lives
    let mod_dir: PathBuf = std::env::var("WINGMAN_MOD_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            std::env::current_exe()
                .ok()
                .and_then(|p| {
                    // target/release/wingman-api → ../../.. → src/api/../.. → wingman/
                    p.ancestors().nth(4).map(PathBuf::from)
                })
                .unwrap_or_else(|| PathBuf::from("."))
        });

    // Data dir: ~/.mod/wingman
    let data_dir: PathBuf = std::env::var("WINGMAN_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            dirs::home_dir()
                .unwrap_or_else(|| PathBuf::from("/root"))
                .join(".mod/wingman")
        });

    let bridge = Bridge::new(mod_dir.clone());

    let state: AppState = Arc::new(AppStateInner {
        bridge,
        mod_dir,
        data_dir,
    });

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        // Info / health
        .route("/", get(routes::get_info))
        .route("/health", get(routes::get_health))
        .route("/guide", get(routes::get_guide))
        .route("/presets", get(routes::get_presets))
        .route("/tools", get(routes::get_tools))
        .route("/token", get(routes::get_token))
        // Sets
        .route("/sets", get(routes::get_sets).post(routes::post_sets))
        .route("/sets/:id", get(routes::get_set).delete(routes::delete_set))
        // Photos
        .route("/photos", post(routes::post_photos))
        .route("/photos/:set/:photo", delete(routes::delete_photo))
        // Work
        .route("/audit", get(routes::get_audit))
        .route("/faces", get(routes::get_faces))
        .route("/lineup", get(routes::get_lineup))
        .route("/render", post(routes::post_render))
        .route("/export", post(routes::post_export))
        // Image serving
        .route("/img/:set/:photo", get(routes::get_thumb))
        .route("/img/:set/:photo/:preset", get(routes::get_rendered))
        .route("/download/:set/:preset", get(routes::get_download))
        // MCP
        .route("/mcp", post(routes::post_mcp))
        // Read / Venice
        .route("/read", get(routes::get_read).post(routes::post_read))
        .route("/venice", get(routes::get_venice).post(routes::post_venice))
        .route("/venice/models", get(routes::get_venice_models))
        .route(
            "/venice/key",
            post(routes::post_venice_key).delete(routes::delete_venice_key),
        )
        .layer(cors)
        .with_state(state);

    let addr: SocketAddr = format!("{bind_addr}:{port}")
        .parse()
        .expect("invalid bind address");

    tracing::info!("wingman-api listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
