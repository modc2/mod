use axum::body::Body;
use axum::http::{Request, StatusCode};
use serde_json::Value;
use tower::ServiceExt;

mod helpers;
use helpers::test_app;

#[tokio::test]
async fn health_returns_ok() {
    let app = test_app();
    let resp = app
        .oneshot(Request::get("/health").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    assert_eq!(body["status"], "ok");
    assert_eq!(body["service"], "polymarket-api");
}

#[tokio::test]
async fn active_traders_status_probe() {
    let app = test_app();
    let resp = app
        .oneshot(
            Request::get("/active-traders?status=1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    assert_eq!(body["ok"], true);
}

#[tokio::test]
async fn active_traders_paged_returns_valid_response() {
    let app = test_app();
    let resp = app
        .oneshot(
            Request::get("/active-traders?paged=1&days=7&pool=100")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    // Response is either cold (empty) or warm (has traders)
    if body["cold"] == true {
        assert_eq!(body["traders"], Value::Array(vec![]));
        assert_eq!(body["total"], 0);
    } else {
        assert!(body["traders"].is_array());
        assert!(body["total"].as_u64().unwrap_or(0) > 0);
    }
}

#[tokio::test]
async fn proxy_requires_endpoint_param() {
    let app = test_app();
    // Fallback without endpoint= should return 400
    let resp = app
        .oneshot(
            Request::get("/nonexistent")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = resp_json(resp).await;
    assert!(body["error"].as_str().unwrap().contains("endpoint"));
}

#[tokio::test]
async fn proxy_markets_endpoint() {
    let app = test_app();
    let resp = app
        .oneshot(
            Request::get("/?endpoint=markets&_limit=2&active=true")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    assert!(body.is_array(), "markets should return array");
    // Should have at least 1 market
    assert!(!body.as_array().unwrap().is_empty());
}

#[tokio::test]
async fn proxy_cache_hit() {
    let app = test_app();

    // First request - MISS
    let resp = app
        .clone()
        .oneshot(
            Request::get("/?endpoint=markets&_limit=1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let cache_header = resp.headers().get("x-cache").unwrap().to_str().unwrap();
    assert_eq!(cache_header, "MISS");

    // Second request - HIT
    let resp = app
        .oneshot(
            Request::get("/?endpoint=markets&_limit=1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let cache_header = resp.headers().get("x-cache").unwrap().to_str().unwrap();
    assert_eq!(cache_header, "HIT");
}

#[tokio::test]
async fn active_traders_pipeline_small_pool() {
    // Run the actual pipeline with a tiny pool to verify end-to-end
    let app = test_app();
    let resp = app
        .oneshot(
            Request::get("/active-traders?days=1&pool=50")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    assert!(body["count"].as_u64().unwrap() > 0, "should find traders");
    let source = body["source"].as_str().unwrap();
    assert!(
        source == "fresh" || source == "memory" || source == "disk",
        "unexpected source: {}",
        source
    );
    let traders = body["traders"].as_array().unwrap();
    assert!(!traders.is_empty());
    // Verify trader fields
    let t = &traders[0];
    assert!(t["address"].is_string());
    assert!(t["volume"].is_number());
    assert!(t["pnl"].is_number());
    assert!(t["recentTrades"].is_number());
}

// ── helpers ──

async fn resp_json(resp: axum::http::Response<Body>) -> Value {
    let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}
