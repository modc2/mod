//! HTTP API and routing

use crate::auth::{recover_address, sign_address, verify_token};
use crate::markets::MarketManager;
use axum::{
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tower_http::cors::{Any, CorsLayer};

type AppState = Arc<MarketManager>;

pub async fn serve(manager: Arc<MarketManager>, port: u16) {
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/health", get(health))
        .route("/auth/challenge", post(auth_challenge))
        .route("/auth/verify", post(auth_verify))
        .route("/markets", get(list_markets).post(create_market))
        .route("/markets/:id", get(get_market))
        .route("/markets/:id/resolve", post(resolve_market))
        .route("/positions", post(create_position))
        .route("/positions/user/:address", get(get_user_positions))
        .layer(cors)
        .with_state(manager);

    let addr = format!("0.0.0.0:{}", port);
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    println!("Listening on http://{}", addr);
    axum::serve(listener, app).await.unwrap();
}

async fn health() -> &'static str {
    "OK"
}

// ──── Auth ────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct AuthChallengeRequest {
    address: String,
}

#[derive(Serialize)]
struct AuthChallengeResponse {
    message: String,
}

async fn auth_challenge(
    Json(req): Json<AuthChallengeRequest>,
) -> Result<Json<AuthChallengeResponse>, AppError> {
    let message = format!("Sign this message to authenticate with PreFi: {}", req.address);
    Ok(Json(AuthChallengeResponse { message }))
}

#[derive(Deserialize)]
struct AuthVerifyRequest {
    address: String,
    signature: String,
    message: String,
}

#[derive(Serialize)]
struct AuthVerifyResponse {
    token: String,
    address: String,
}

async fn auth_verify(
    Json(req): Json<AuthVerifyRequest>,
) -> Result<Json<AuthVerifyResponse>, AppError> {
    let recovered = recover_address(&req.message, &req.signature)?;

    if recovered.to_lowercase() != req.address.to_lowercase() {
        return Err(AppError::Unauthorized("Address mismatch".to_string()));
    }

    let token = sign_address(&req.address);
    Ok(Json(AuthVerifyResponse {
        token,
        address: req.address,
    }))
}

// ──── Markets ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct ListMarketsQuery {
    status: Option<String>,
}

async fn list_markets(
    State(manager): State<AppState>,
    Query(params): Query<ListMarketsQuery>,
) -> Result<Json<Vec<crate::db::Market>>, AppError> {
    let markets = manager.list_markets(params.status)?;
    Ok(Json(markets))
}

#[derive(Deserialize)]
struct CreateMarketRequest {
    title: String,
    description: String,
    creator: String,
}

async fn create_market(
    State(manager): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<CreateMarketRequest>,
) -> Result<Json<crate::db::Market>, AppError> {
    verify_auth(&headers, &req.creator)?;
    let market = manager.create_market(req.title, req.description, req.creator)?;
    Ok(Json(market))
}

async fn get_market(
    State(manager): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<crate::db::Market>, AppError> {
    let market = manager.get_market(&id)?;
    Ok(Json(market))
}

#[derive(Deserialize)]
struct ResolveMarketRequest {
    resolution: bool,
}

async fn resolve_market(
    State(manager): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Json(req): Json<ResolveMarketRequest>,
) -> Result<Json<crate::db::Market>, AppError> {
    // Get market to check creator
    let market = manager.get_market(&id)?;
    verify_auth(&headers, &market.creator)?;

    let resolved = manager.resolve_market(&id, req.resolution)?;
    Ok(Json(resolved))
}

// ──── Positions ───────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct CreatePositionRequest {
    market_id: String,
    user_address: String,
    position_type: String,
    amount: f64,
}

async fn create_position(
    State(manager): State<AppState>,
    headers: HeaderMap,
    Json(req): Json<CreatePositionRequest>,
) -> Result<Json<crate::db::Position>, AppError> {
    verify_auth(&headers, &req.user_address)?;
    let position = manager.create_position(
        req.market_id,
        req.user_address,
        req.position_type,
        req.amount,
    )?;
    Ok(Json(position))
}

async fn get_user_positions(
    State(manager): State<AppState>,
    Path(address): Path<String>,
) -> Result<Json<Vec<crate::db::Position>>, AppError> {
    let positions = manager.get_user_positions(&address)?;
    Ok(Json(positions))
}

// ──── Auth helpers ────────────────────────────────────────────────────────

fn verify_auth(headers: &HeaderMap, expected_address: &str) -> Result<(), AppError> {
    let auth_header = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| AppError::Unauthorized("Missing authorization header".to_string()))?;

    let token = auth_header
        .strip_prefix("Bearer ")
        .ok_or_else(|| AppError::Unauthorized("Invalid authorization format".to_string()))?;

    if !verify_token(expected_address, token) {
        return Err(AppError::Unauthorized("Invalid token".to_string()));
    }

    Ok(())
}

// ──── Error handling ──────────────────────────────────────────────────────

#[derive(Debug)]
enum AppError {
    Internal(String),
    NotFound(String),
    Unauthorized(String),
    BadRequest(String),
}

impl From<String> for AppError {
    fn from(s: String) -> Self {
        AppError::Internal(s)
    }
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, message) = match self {
            AppError::Internal(msg) => (StatusCode::INTERNAL_SERVER_ERROR, msg),
            AppError::NotFound(msg) => (StatusCode::NOT_FOUND, msg),
            AppError::Unauthorized(msg) => (StatusCode::UNAUTHORIZED, msg),
            AppError::BadRequest(msg) => (StatusCode::BAD_REQUEST, msg),
        };

        let body = serde_json::json!({
            "error": message
        });

        (status, Json(body)).into_response()
    }
}
