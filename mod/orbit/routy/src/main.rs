use axum::{
    extract::{Path, State},
    http::{HeaderMap, Method, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{any, get, post},
    Json, Router,
};
use std::sync::Arc;
use tracing::{error, info};

mod config;
mod proxy;
mod registry;
mod resources;

use config::Config;
use registry::WebsiteRegistry;
use resources::ResourceMonitor;

#[derive(Clone)]
struct AppState {
    registry: Arc<WebsiteRegistry>,
    monitor: Arc<ResourceMonitor>,
    config: Arc<Config>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "routy=info,tower_http=info".into()),
        )
        .init();

    let config = Arc::new(Config::load()?);
    let registry = Arc::new(WebsiteRegistry::new());
    let monitor = Arc::new(ResourceMonitor::new(config.clone()));

    monitor.start_monitoring();

    let addr = format!("{}:{}", config.host, config.port);

    let state = AppState {
        registry,
        monitor,
        config,
    };

    let app = Router::new()
        .route("/", get(root_handler))
        .route("/_api/register", post(register_website))
        .route("/_api/sync", post(sync_websites))
        .route("/_api/websites", get(list_websites))
        .route("/_api/stats", get(get_stats))
        // API proxy: /api/{name}/... → strip prefix, forward to API server
        .route("/api/:name/*path", any(api_proxy_handler))
        .route("/api/:name", any(api_proxy_root))
        .route("/api/:name/", any(api_proxy_root))
        // App proxy: /{name}/... → keep prefix, forward to app server
        .route("/:website/*path", any(app_proxy_handler))
        .route("/:website", any(app_proxy_root))
        .route("/:website/", any(app_proxy_root))
        .fallback(fallback_handler)
        .with_state(state)
        .layer(tower_http::trace::TraceLayer::new_for_http())
        .layer(tower_http::cors::CorsLayer::permissive());

    info!("Routy listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

// ── Dashboard ──

async fn root_handler(State(state): State<AppState>) -> impl IntoResponse {
    let apps = state.registry.list_apps().await;
    let apis = state.registry.list_apis().await;
    let cpu = state.monitor.get_cpu_usage().await;

    let app_rows = if apps.is_empty() {
        "<tr><td colspan=\"5\" style=\"color:#888\">No apps registered</td></tr>".to_string()
    } else {
        apps.iter()
            .map(|w| {
                let storage = w.storage_type.as_deref().unwrap_or("-");
                let cid_short = w.cid.as_deref().map(|c| if c.len() > 12 { &c[..12] } else { c }).unwrap_or("-");
                format!(
                    "<tr><td><a href=\"/{}/\">{}</a></td><td><code>{}</code></td><td>{}</td><td><code>{}</code></td><td><code>{}</code></td></tr>",
                    w.name, w.name, w.target_url,
                    w.description.as_deref().unwrap_or(""),
                    storage, cid_short
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    };

    let api_rows = if apis.is_empty() {
        "<tr><td colspan=\"5\" style=\"color:#888\">No APIs registered</td></tr>".to_string()
    } else {
        apis.iter()
            .map(|w| {
                let storage = w.storage_type.as_deref().unwrap_or("-");
                let cid_short = w.cid.as_deref().map(|c| if c.len() > 12 { &c[..12] } else { c }).unwrap_or("-");
                format!(
                    "<tr><td><a href=\"/api/{}/\">{}</a></td><td><code>{}</code></td><td>{}</td><td><code>{}</code></td><td><code>{}</code></td></tr>",
                    w.name, w.name, w.target_url,
                    w.description.as_deref().unwrap_or(""),
                    storage, cid_short
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    };

    let html = format!(
        r#"<!DOCTYPE html>
<html>
<head>
    <title>routy</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; background:#0a0a0a; color:#e0e0e0; padding:40px; }}
        h1 {{ font-size:1.5rem; margin-bottom:8px; color:#fff; }}
        .subtitle {{ color:#888; margin-bottom:32px; }}
        .stats {{ display:flex; gap:24px; margin-bottom:32px; }}
        .stat {{ background:#161616; border:1px solid #222; border-radius:8px; padding:16px 24px; }}
        .stat-value {{ font-size:1.8rem; font-weight:600; color:#fff; }}
        .stat-label {{ font-size:0.85rem; color:#888; margin-top:4px; }}
        h2 {{ font-size:1.1rem; margin:24px 0 12px; color:#ccc; }}
        table {{ width:100%; border-collapse:collapse; background:#161616; border:1px solid #222; border-radius:8px; overflow:hidden; }}
        th {{ text-align:left; padding:10px 16px; background:#1a1a1a; color:#888; font-weight:500; font-size:0.85rem; text-transform:uppercase; letter-spacing:0.05em; }}
        td {{ padding:10px 16px; border-top:1px solid #222; }}
        a {{ color:#60a5fa; text-decoration:none; }}
        a:hover {{ text-decoration:underline; }}
        code {{ font-size:0.85em; color:#888; }}
        .api-section {{ margin-top:32px; }}
    </style>
</head>
<body>
    <h1>routy</h1>
    <p class="subtitle">local gateway &middot; routing {} apps + {} apis</p>

    <div class="stats">
        <div class="stat">
            <div class="stat-value">{}</div>
            <div class="stat-label">apps</div>
        </div>
        <div class="stat">
            <div class="stat-value">{}</div>
            <div class="stat-label">apis</div>
        </div>
        <div class="stat">
            <div class="stat-value">{:.0}%</div>
            <div class="stat-label">cpu</div>
        </div>
    </div>

    <h2>Apps &mdash; /{{name}}/*</h2>
    <table>
        <tr><th>name</th><th>target</th><th>description</th><th>storage</th><th>cid</th></tr>
        {}
    </table>

    <div class="api-section">
        <h2>APIs &mdash; /api/{{name}}/*</h2>
        <table>
            <tr><th>name</th><th>target</th><th>description</th><th>storage</th><th>cid</th></tr>
            {}
        </table>
    </div>
</body>
</html>"#,
        apps.len(),
        apis.len(),
        apps.len(),
        apis.len(),
        cpu,
        app_rows,
        api_rows,
    );

    (StatusCode::OK, [("content-type", "text/html")], html)
}

// ── Management API ──

async fn register_website(
    State(state): State<AppState>,
    Json(payload): Json<registry::RegisterRequest>,
) -> Result<impl IntoResponse, AppError> {
    state
        .registry
        .register(payload)
        .await
        .map_err(|e| AppError::ProxyError(e))?;

    Ok(Json(serde_json::json!({
        "success": true,
        "message": "Registered"
    })))
}

async fn sync_websites(
    State(state): State<AppState>,
    Json(payload): Json<registry::SyncRequest>,
) -> Result<impl IntoResponse, AppError> {
    let count = state
        .registry
        .sync(payload)
        .await
        .map_err(|e| AppError::ProxyError(e))?;

    Ok(Json(serde_json::json!({
        "success": true,
        "synced": count
    })))
}

async fn list_websites(State(state): State<AppState>) -> impl IntoResponse {
    let apps = state.registry.list_apps().await;
    let apis = state.registry.list_apis().await;
    Json(serde_json::json!({ "apps": apps, "apis": apis }))
}

async fn get_stats(State(state): State<AppState>) -> impl IntoResponse {
    let cpu_usage = state.monitor.get_cpu_usage().await;
    let apps = state.registry.list_apps().await.len();
    let apis = state.registry.list_apis().await.len();

    Json(serde_json::json!({
        "cpu_usage_percent": cpu_usage,
        "apps": apps,
        "apis": apis,
        "total": apps + apis,
        "max_websites": state.config.max_websites,
    }))
}

// ── App proxy: /{name}/* → keep full path ──

async fn app_proxy_handler(
    State(state): State<AppState>,
    Path((website_name, path)): Path<(String, String)>,
    method: Method,
    headers: HeaderMap,
    uri: Uri,
    body: axum::body::Body,
) -> Result<Response, AppError> {
    let website = state
        .registry
        .get_app(&website_name)
        .await
        .ok_or(AppError::WebsiteNotFound)?;

    // Keep the full path including /{name}/... for basePath-aware apps
    let full_path = format!(
        "{}/{}",
        website_name,
        path.trim_start_matches('/')
    );
    proxy::proxy_request(website, &full_path, method, headers, uri, body).await
}

async fn app_proxy_root(
    State(state): State<AppState>,
    Path(website_name): Path<String>,
    method: Method,
    headers: HeaderMap,
    uri: Uri,
    body: axum::body::Body,
) -> Result<Response, AppError> {
    let website = state
        .registry
        .get_app(&website_name)
        .await
        .ok_or(AppError::WebsiteNotFound)?;

    // Keep /{name} as the path
    proxy::proxy_request(website, &website_name, method, headers, uri, body).await
}

// ── API proxy: /api/{name}/* → strip prefix ──

async fn api_proxy_handler(
    State(state): State<AppState>,
    Path((name, path)): Path<(String, String)>,
    method: Method,
    headers: HeaderMap,
    uri: Uri,
    body: axum::body::Body,
) -> Result<Response, AppError> {
    let website = state
        .registry
        .get_api(&name)
        .await
        .ok_or(AppError::WebsiteNotFound)?;

    // Strip /api/{name}/ prefix — just forward the remaining path
    proxy::proxy_request(website, &path, method, headers, uri, body).await
}

async fn api_proxy_root(
    State(state): State<AppState>,
    Path(name): Path<String>,
    method: Method,
    headers: HeaderMap,
    uri: Uri,
    body: axum::body::Body,
) -> Result<Response, AppError> {
    let website = state
        .registry
        .get_api(&name)
        .await
        .ok_or(AppError::WebsiteNotFound)?;

    // Root of the API — forward as /
    proxy::proxy_request(website, "", method, headers, uri, body).await
}

// ── Fallback ──

async fn fallback_handler(uri: Uri) -> impl IntoResponse {
    (
        StatusCode::NOT_FOUND,
        format!("Not found: {}", uri.path()),
    )
}

// ── Errors ──

#[derive(Debug)]
enum AppError {
    WebsiteNotFound,
    ProxyError(String),
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, message) = match self {
            AppError::WebsiteNotFound => (StatusCode::NOT_FOUND, "Not found".to_string()),
            AppError::ProxyError(msg) => {
                error!("Proxy error: {}", msg);
                (StatusCode::BAD_GATEWAY, "Bad gateway".to_string())
            }
        };
        (status, message).into_response()
    }
}
