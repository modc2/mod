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
// `live-prices-history` / `live-midpoint` rewrite to the same CLOB endpoints
// but under a DISTINCT name so they get a near-live TTL and no disk
// persistence. Sub-hour candle strats (BTC 5-min Up/Down) read through them:
// the whole market lives ~5 minutes, so the regular `prices-history` path —
// 24h disk persistence — would freeze the series at its first fetch.
const CLOB_PREFIXES: &[&str] = &[
    "prices-history", "book", "books", "midpoint", "midpoints", "price", "live-",
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
    match endpoint {
        "market-trades" | "user-trades" => "trades",
        "live-prices-history" => "prices-history",
        "live-midpoint" => "midpoint",
        _ => endpoint,
    }
}

/// Order-independent form of a query string, for cache keying only. Repeated
/// keys (gamma's `condition_ids`) are kept — sorting preserves every pair, so
/// two requests differing in which ids they ask for still get separate entries.
fn normalize_query(qs: &str) -> String {
    if qs.is_empty() {
        return String::new();
    }
    let mut pairs: Vec<&str> = qs.split('&').filter(|p| !p.is_empty()).collect();
    pairs.sort_unstable();
    pairs.join("&")
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

    // Cache key from the query string, NORMALIZED. Keying on it verbatim meant
    // parameter order decided the entry: the TS lib builds
    // `user&limit&offset&endpoint` while everything hand-rolled writes
    // `endpoint&user&limit&offset`, so one trader's page 0 sat in two entries
    // aging independently — observed ten minutes apart for the same request,
    // which is a fetch upstream that didn't have to happen and a caller that
    // can't tell how old its answer is. Sorting makes the key a function of
    // the request's meaning rather than of how the caller spelled it.
    let qs = req.uri().query().unwrap_or("");
    let cache_key = format!("proxy:{}", normalize_query(qs));

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

    // Fetch upstream. Cloudflare rate-limits data-api (HTTP 429, error 1015)
    // when the live engine + a browser page hit it from the same IP — for a
    // first-time trader there's no stale cache to fall back on, so a single
    // 429 used to surface as a 502 and the profile rendered "0 trades" over
    // 100 open positions. Ride out the burst with a couple of spaced retries
    // before giving up; only 429/5xx re-attempt (other 4xx are deterministic).
    let mut result = state.http.get(&url)
        .header("accept", "application/json")
        .send()
        .await;
    for delay_ms in [1200u64, 2600] {
        let retryable = match &result {
            Ok(resp) => {
                let s = resp.status();
                s.as_u16() == 429 || s.is_server_error()
            }
            Err(_) => true,
        };
        if !retryable {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
        result = state.http.get(&url)
            .header("accept", "application/json")
            .send()
            .await;
    }

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
            let out = downstream_status(status);
            // Upstream explains itself; a caller can only act on a reason it
            // can read, so forward the message rather than just the number.
            let detail = resp.text().await.ok()
                .and_then(|b| serde_json::from_str::<Value>(&b).ok())
                .and_then(|v| v.get("error").or_else(|| v.get("message"))
                    .and_then(Value::as_str).map(str::to_string))
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| format!("upstream {}", status));
            (out, Json(json!({"error": detail, "upstream_status": status}))).into_response()
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

/// What status the browser should see for a given upstream status.
///
/// Pass a client error through AS a client error. Flattening every upstream
/// 4xx into 502 cost a real bug: data-api answers `/activity?offset>5000` with
/// 400 "max historical activity offset of 5000 exceeded" — a permanent limit —
/// and the browser saw a 502, a status its own retry ladder treats as
/// transient. A deterministic no burned three retries and then rendered as
/// "TRADE SYNC FAILED (API 502)" behind a RETRY SYNC button that could never
/// work. (429 still arrives as 429 for the reason it always did: the frontend
/// backs off differently for a rate limit than for a blip.)
fn downstream_status(upstream: u16) -> StatusCode {
    StatusCode::from_u16(upstream)
        .ok()
        .filter(StatusCode::is_client_error)
        .unwrap_or(StatusCode::BAD_GATEWAY)
}

#[cfg(test)]
mod tests {
    use super::{downstream_status, normalize_query};
    use axum::http::StatusCode;

    /// The one that bit us: a permanent upstream refusal must not arrive
    /// wearing a transient status.
    #[test]
    fn client_errors_are_not_disguised_as_gateway_failures() {
        assert_eq!(downstream_status(400), StatusCode::BAD_REQUEST);
        assert_eq!(downstream_status(404), StatusCode::NOT_FOUND);
        assert_eq!(downstream_status(429), StatusCode::TOO_MANY_REQUESTS);
    }

    /// Everything that ISN'T the caller's fault stays a 502 — the frontend
    /// retries those, and a 500/503 genuinely is worth retrying.
    #[test]
    fn server_errors_and_nonsense_stay_502() {
        assert_eq!(downstream_status(500), StatusCode::BAD_GATEWAY);
        assert_eq!(downstream_status(503), StatusCode::BAD_GATEWAY);
        assert_eq!(downstream_status(999), StatusCode::BAD_GATEWAY);
    }

    /// The whole point: two spellings of one request must land on one entry.
    #[test]
    fn param_order_does_not_change_the_cache_key() {
        assert_eq!(
            normalize_query("user=0xabc&limit=500&offset=0&endpoint=activity"),
            normalize_query("endpoint=activity&user=0xabc&limit=500&offset=0"),
        );
    }

    #[test]
    fn different_requests_still_differ() {
        assert_ne!(
            normalize_query("endpoint=activity&user=0xabc&offset=0"),
            normalize_query("endpoint=activity&user=0xabc&offset=500"),
        );
        // Repeated keys are preserved, not collapsed — gamma's `condition_ids`
        // is passed once per id and the set is what makes the request distinct.
        assert_ne!(
            normalize_query("endpoint=markets&condition_ids=a&condition_ids=b"),
            normalize_query("endpoint=markets&condition_ids=a"),
        );
        assert_eq!(normalize_query(""), "");
    }
}
