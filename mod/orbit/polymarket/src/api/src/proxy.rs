use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::Json;
use serde_json::{json, Value};

use crate::AppState;
use crate::cache::ProxyCache;
use crate::types::ProxyQuery;

const GAMMA_API: &str = "https://gamma-api.polymarket.com";
const CLOB_API: &str = "https://clob.polymarket.com";
const DATA_API: &str = "https://data-api.polymarket.com";

// `market-trades` rewrites to data-api's public `/trades?market=<conditionId>`
// — it must NOT resolve to the CLOB API's `/trades`, which is an
// authenticated (API-key-gated) endpoint for the caller's own fills and
// returns 401 for anyone else's market history.
// `user-trades` likewise rewrites to data-api `/trades?user=<wallet>` but is a
// DISTINCT endpoint name so it gets its own near-live TTL — the signed-in
// user's own fill tape must not sit behind the global tape's 1h freshness.
const DATA_PREFIXES: &[&str] = &[
    "positions", "closed-positions", "trades", "activity", "value", "holders", "users/", "v1/", "market-trades",
    "user-trades",
];
const CLOB_PREFIXES: &[&str] = &[
    "prices-history", "book", "books", "midpoint", "midpoints", "price",
];

fn select_upstream(endpoint: &str) -> &'static str {
    let ep = endpoint.to_lowercase();
    if DATA_PREFIXES.iter().any(|p| ep.starts_with(p)) {
        DATA_API
    } else if CLOB_PREFIXES.iter().any(|p| ep.starts_with(p)) {
        CLOB_API
    } else {
        GAMMA_API
    }
}

fn rewrite_endpoint(endpoint: &str) -> &str {
    if endpoint == "market-trades" || endpoint == "user-trades" {
        "trades"
    } else {
        endpoint
    }
}

pub async fn proxy_handler(
    State(state): State<AppState>,
    Query(params): Query<ProxyQuery>,
    req: axum::http::Request<axum::body::Body>,
) -> impl IntoResponse {
    let endpoint = match &params.endpoint {
        Some(ep) => ep.clone(),
        None => return (StatusCode::BAD_REQUEST, Json(json!({"error": "missing endpoint param"}))).into_response(),
    };

    // Build cache key from full query string
    let qs = req.uri().query().unwrap_or("");
    let cache_key = format!("proxy:{}", qs);

    // Check cache (memory + disk for persistent endpoints). Only a FRESH
    // entry short-circuits — a stale one falls through to a live upstream
    // fetch (and is still served by the stale-on-error branches below if
    // that fetch fails). Serving stale here unconditionally meant a cached
    // /value or /positions could outlive the actual holdings by a day and
    // the portfolio panel kept rendering positions that were long gone.
    if let Some((data, fresh)) = state.proxy_cache.get(&cache_key, &endpoint) {
        if fresh {
            let mut headers = HeaderMap::new();
            headers.insert("x-cache", "HIT".parse().unwrap());
            return (StatusCode::OK, headers, Json(data)).into_response();
        }
    }

    // Build upstream URL
    let upstream = select_upstream(&endpoint);
    let rewritten = rewrite_endpoint(&endpoint);

    // Strip `endpoint` param from query, pass everything else
    let upstream_qs: String = req.uri().query().unwrap_or("").split('&')
        .filter(|p| !p.starts_with("endpoint="))
        .collect::<Vec<_>>()
        .join("&");

    let url = if upstream_qs.is_empty() {
        format!("{}/{}", upstream, rewritten)
    } else {
        format!("{}/{}?{}", upstream, rewritten, upstream_qs)
    };

    // Fetch upstream
    let result = state.http.get(&url)
        .header("accept", "application/json")
        .send()
        .await;

    match result {
        Ok(resp) if resp.status().is_success() => {
            match resp.json::<Value>().await {
                Ok(data) => {
                    let ttl = ProxyCache::ttl_for_endpoint(&endpoint);
                    // Don't cache empty-array responses. The data-api
                    // intermittently returns [] for positions/activity/trades
                    // under load, and caching that for the full TTL renders an
                    // active wallet as empty (the phantom-$0 bug) until it
                    // expires. Skipping the cache for empties means the next
                    // request retries upstream, while the stale-on-error
                    // branches below still serve the last NON-empty result.
                    let is_empty_array = data.as_array().map(|a| a.is_empty()).unwrap_or(false);
                    if !is_empty_array {
                        state.proxy_cache.set(cache_key, data.clone(), ttl, &endpoint);
                    }
                    let mut headers = HeaderMap::new();
                    headers.insert("x-cache", if is_empty_array { "MISS-NOCACHE" } else { "MISS" }.parse().unwrap());
                    let max_age = ttl.as_secs();
                    headers.insert(
                        "cache-control",
                        format!("public, s-maxage={}, stale-while-revalidate={}", max_age, max_age / 5)
                            .parse()
                            .unwrap(),
                    );
                    (StatusCode::OK, headers, Json(data)).into_response()
                }
                Err(e) => {
                    // Try stale cache
                    if let Some((data, _)) = state.proxy_cache.get(&cache_key, &endpoint) {
                        let mut headers = HeaderMap::new();
                        headers.insert("x-cache", "STALE".parse().unwrap());
                        return (StatusCode::OK, headers, Json(data)).into_response();
                    }
                    (StatusCode::BAD_GATEWAY, Json(json!({"error": format!("parse: {}", e)}))).into_response()
                }
            }
        }
        Ok(resp) => {
            let status = resp.status().as_u16();
            // Serve stale on upstream error
            if let Some((data, _)) = state.proxy_cache.get(&cache_key, &endpoint) {
                let mut headers = HeaderMap::new();
                headers.insert("x-cache", "STALE".parse().unwrap());
                return (StatusCode::OK, headers, Json(data)).into_response();
            }
            (StatusCode::BAD_GATEWAY, Json(json!({"error": format!("upstream {}", status)}))).into_response()
        }
        Err(e) => {
            // Serve stale on network error
            if let Some((data, _)) = state.proxy_cache.get(&cache_key, &endpoint) {
                let mut headers = HeaderMap::new();
                headers.insert("x-cache", "STALE".parse().unwrap());
                return (StatusCode::OK, headers, Json(data)).into_response();
            }
            (StatusCode::BAD_GATEWAY, Json(json!({"error": format!("network: {}", e)}))).into_response()
        }
    }
}
