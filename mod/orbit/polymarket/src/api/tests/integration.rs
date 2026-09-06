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
    // `stale-disk` is a legitimate answer: a board too old to serve fresh but
    // present on disk is served labelled rather than withheld. This assert
    // predated that label and failed on any host with a warm cache dir.
    assert!(
        matches!(source, "fresh" | "memory" | "disk" | "stale-disk"),
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

/// The strat-sync write route must MATCH and actually persist.
///
/// It was registered as `/strats/{id}` — axum 0.8 syntax. This crate is on
/// axum 0.7, where `{id}` is a LITERAL segment, so `PUT /strats/<real-id>`
/// fell through to the proxy fallback (which answers GET/POST only, hence a
/// 405 rather than an obvious 404) and the browser client — which returns
/// `false` on any error — silently never saved a strat. Asserting on the
/// round-trip rather than on "not 404" is what makes this catch the bug.
#[tokio::test]
async fn strat_upsert_route_matches_and_persists() {
    let app = test_app();
    let token = format!("tok{}", std::process::id());
    let body = serde_json::json!({
        "token_id": token,
        "ciphertext": "Y2lwaGVydGV4dA==",
        "updated_at": 1_700_000_000u64,
    })
    .to_string();

    let resp = app
        .clone()
        .oneshot(
            Request::put("/strats/route-check-1")
                .header("content-type", "application/json")
                .body(Body::from(body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK, "PUT /strats/<id> did not reach the handler");
    let saved = resp_json(resp).await;
    assert_eq!(saved["ok"], true);
    assert_eq!(saved["id"], "route-check-1");

    let resp = app
        .oneshot(
            Request::get(format!("/strats?token_id={}", token))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let listed = resp_json(resp).await;
    assert!(
        listed["strats"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["id"] == "route-check-1"),
        "the strat was accepted but not stored: {}",
        listed
    );
}
