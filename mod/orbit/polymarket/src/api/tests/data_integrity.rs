/// Data integrity tests for the Polymarket API.
/// These tests hit the live API to verify data completeness and correctness.
/// Run with: cargo test --test data_integrity -- --nocapture
use axum::body::Body;
use axum::http::{Request, StatusCode};
use serde_json::Value;
use tower::ServiceExt;

mod helpers;
use helpers::test_app;

// ── helpers ──

async fn resp_json(resp: axum::http::Response<Body>) -> Value {
    let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

fn parse_volume(m: &Value) -> f64 {
    m.get("volume")
        .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
        .unwrap_or(0.0)
}

// ── Market data integrity ──

#[tokio::test]
async fn markets_return_active_only() {
    let app = test_app();
    let today = chrono::Utc::now().format("%Y-%m-%dT00:00:00.000Z").to_string();
    let url = format!(
        "/?endpoint=markets&_limit=50&active=true&closed=false&order=volume&ascending=false&end_date_min={}",
        today
    );
    let resp = app
        .oneshot(Request::get(&url).body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    let markets = body.as_array().expect("markets should be an array");
    assert!(!markets.is_empty(), "should have active markets");

    let now_iso = chrono::Utc::now().format("%Y-%m-%d").to_string();
    for m in markets {
        let active = m.get("active").and_then(|v| v.as_bool()).unwrap_or(true);
        assert!(active, "market should be active: {:?}", m.get("question"));

        let closed = m.get("closed").and_then(|v| v.as_bool()).unwrap_or(false);
        assert!(!closed, "market should not be closed: {:?}", m.get("question"));

        if let Some(end_date) = m.get("end_date_iso").or(m.get("endDate")).and_then(|v| v.as_str()) {
            if end_date.len() >= 10 {
                let end_day = &end_date[..10];
                assert!(
                    end_day >= now_iso.as_str(),
                    "market end_date {} is before today {}: {:?}",
                    end_day, now_iso, m.get("question")
                );
            }
        }

        let question = m.get("question").and_then(|v| v.as_str()).unwrap_or("");
        assert!(!question.is_empty(), "market should have a question");
    }
    println!("PASS: {} active markets verified", markets.len());
}

#[tokio::test]
async fn markets_have_required_fields() {
    let app = test_app();
    let resp = app
        .oneshot(
            Request::get("/?endpoint=markets&_limit=20&active=true")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    let markets = body.as_array().expect("markets should be an array");

    let mut with_prices = 0;
    for m in markets {
        assert!(
            m.get("condition_id").or(m.get("conditionId")).is_some(),
            "market missing condition_id"
        );
        assert!(m.get("question").is_some(), "market missing question");

        // Outcome prices may be a JSON string "[\"0.5\",\"0.5\"]" or missing
        if let Some(prices_val) = m.get("outcomePrices") {
            let prices_str = prices_val.as_str().unwrap_or("[]");
            let prices: Vec<f64> = serde_json::from_str::<Vec<Value>>(prices_str)
                .unwrap_or_default()
                .iter()
                .filter_map(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
                .collect();
            if !prices.is_empty() {
                with_prices += 1;
                for p in &prices {
                    assert!(*p >= 0.0 && *p <= 1.0, "price {} out of range [0,1]", p);
                }
            }
        }
    }
    assert!(with_prices > 0, "at least some markets should have valid outcome prices");
    println!("PASS: {} markets checked, {} with valid prices", markets.len(), with_prices);
}

#[tokio::test]
async fn markets_have_volume_data() {
    let app = test_app();
    let resp = app
        .oneshot(
            Request::get("/?endpoint=markets&_limit=20&active=true&order=volume&ascending=false")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    let markets = body.as_array().expect("markets should be an array");
    assert!(markets.len() >= 2, "need at least 2 markets");

    // Gamma API returns volume as string and sorts lexicographically,
    // so we just verify volumes are parseable and positive
    let mut has_volume = 0;
    for m in markets {
        let vol = parse_volume(m);
        if vol > 0.0 {
            has_volume += 1;
        }
    }
    assert!(has_volume > 0, "at least some markets should have volume > 0");
    println!("PASS: {}/{} markets have positive volume", has_volume, markets.len());
}

// ── Category/event data integrity ──

#[tokio::test]
async fn category_events_return_markets() {
    let app = test_app();
    let categories = ["politics", "crypto"];

    let mut total_events = 0;
    let mut total_markets = 0;
    for cat in &categories {
        let url = format!("/?endpoint=events&tag_slug={}&_limit=5&active=true", cat);
        let resp = app
            .clone()
            .oneshot(Request::get(&url).body(Body::empty()).unwrap())
            .await
            .unwrap();
        // Tolerate upstream errors — some categories may 502
        if resp.status() != StatusCode::OK {
            println!("  {} category: upstream returned {} (skipping)", cat, resp.status());
            continue;
        }
        let body = resp_json(resp).await;
        let empty = vec![];
        let events = body.as_array().unwrap_or(&empty);
        let market_count: usize = events
            .iter()
            .filter_map(|e| e.get("markets").and_then(|m| m.as_array()))
            .map(|m| m.len())
            .sum();
        total_events += events.len();
        total_markets += market_count;
        println!("  {} category: {} events, {} embedded markets", cat, events.len(), market_count);
    }
    assert!(total_events > 0, "should get events from at least one category");
    println!("PASS: {} total events, {} total markets", total_events, total_markets);
}

// ── Trader pipeline data integrity ──

#[tokio::test]
async fn trader_pipeline_30d_data_complete() {
    let app = test_app();
    let resp = app
        .oneshot(
            Request::get("/active-traders?days=30&pool=100&minPerDay=0")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;

    let count = body["count"].as_u64().unwrap_or(0);
    let candidate_pool = body["candidatePool"].as_u64().unwrap_or(0);
    let days_window = body["daysWindow"].as_u64().unwrap_or(0);
    let traders = body["traders"].as_array().expect("traders should be an array");

    assert_eq!(days_window, 30, "should be 30-day window");
    assert!(count > 0, "should find traders in 30d window");
    assert!(candidate_pool > 0, "should have candidates");
    assert_eq!(traders.len(), count as usize, "count should match traders length");

    println!("30D pipeline: {} candidates -> {} traders", candidate_pool, count);

    for (i, t) in traders.iter().enumerate() {
        let addr = t["address"].as_str().unwrap_or("");
        assert!(!addr.is_empty(), "trader {} missing address", i);
        assert!(addr.starts_with("0x"), "trader {} address doesn't start with 0x: {}", i, addr);
        assert!(addr.len() == 42, "trader {} address wrong length: {}", i, addr);

        let volume = t["volume"].as_f64().unwrap_or(-1.0);
        assert!(volume >= 0.0, "trader {} has negative volume: {}", i, volume);

        let buy_vol = t["buyVolume"].as_f64().unwrap_or(-1.0);
        let sell_vol = t["sellVolume"].as_f64().unwrap_or(-1.0);
        assert!(buy_vol >= 0.0, "trader {} has negative buyVolume", i);
        assert!(sell_vol >= 0.0, "trader {} has negative sellVolume", i);

        // buy + sell should roughly equal total volume
        let vol_sum = buy_vol + sell_vol;
        let vol_diff = (volume - vol_sum).abs();
        assert!(
            vol_diff < 1.0 || vol_diff / volume.max(1.0) < 0.01,
            "trader {} volume mismatch: total={} but buy+sell={}",
            i, volume, vol_sum
        );

        let recent_trades = t["recentTrades"].as_u64().unwrap_or(0);
        // With minPerDay=0, some traders from the leaderboard may have zero
        // in-window trades (their activity was outside the exact window).
        // This is valid data — the pipeline still includes them.

        if let Some(curve) = t.get("pnlCurve").and_then(|v| v.as_array()) {
            // Traders with zero in-window trades may have an empty curve
            if !curve.is_empty() {
                assert_eq!(curve.len(), 12, "trader {} pnlCurve should have 12 buckets, got {}", i, curve.len());
                for (j, val) in curve.iter().enumerate() {
                    assert!(val.is_number(), "trader {} pnlCurve[{}] is not a number", i, j);
                }
            }
        }

        let titles = t.get("marketTitles").and_then(|v| v.as_array());
        assert!(titles.is_some(), "trader {} missing marketTitles", i);

        if i < 5 {
            let pnl = t["pnl"].as_f64().unwrap_or(0.0);
            println!(
                "  #{}: {} vol={:.0} pnl={:.2} trades={} markets={}",
                i + 1, &addr[..8], volume, pnl, recent_trades,
                titles.map(|t| t.len()).unwrap_or(0)
            );
        }
    }
    println!("PASS: {} traders have complete 30D data", traders.len());
}

#[tokio::test]
async fn trader_pipeline_paged_filters_work() {
    let app = test_app();
    let resp = app
        .clone()
        .oneshot(
            Request::get("/active-traders?days=7&pool=100&minPerDay=0")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    let all_count = body["count"].as_u64().unwrap_or(0);
    assert!(all_count > 0, "need traders in cache to test pagination");

    // Test pagination
    let resp = app
        .clone()
        .oneshot(
            Request::get("/active-traders?paged=1&days=7&pool=100&page=0&pageSize=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    let traders = body["traders"].as_array().unwrap();
    let total = body["total"].as_u64().unwrap_or(0);
    assert!(traders.len() <= 10, "page size should be respected");
    assert_eq!(total, all_count, "total should match full count");

    // Test minVolume filter
    let resp = app
        .clone()
        .oneshot(
            Request::get("/active-traders?paged=1&days=7&pool=100&minVolume=10000")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    let filtered_total = body["total"].as_u64().unwrap_or(0);
    assert!(filtered_total <= all_count, "minVolume filter should reduce count");
    let traders = body["traders"].as_array().unwrap();
    for t in traders {
        let vol = t["volume"].as_f64().unwrap_or(0.0);
        assert!(vol >= 10000.0, "trader volume {} is below minVolume 10000", vol);
    }

    // Test sort order
    let resp = app
        .oneshot(
            Request::get("/active-traders?paged=1&days=7&pool=100&sort=volume&order=desc")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = resp_json(resp).await;
    let traders = body["traders"].as_array().unwrap();
    if traders.len() >= 2 {
        let vols: Vec<f64> = traders
            .iter()
            .map(|t| t["volume"].as_f64().unwrap_or(0.0))
            .collect();
        for i in 0..vols.len() - 1 {
            assert!(
                vols[i] >= vols[i + 1],
                "traders not sorted by volume desc: {} < {}",
                vols[i], vols[i + 1]
            );
        }
    }

    println!("PASS: pagination and filters work correctly");
}

// ── Cache integrity ──

#[tokio::test]
async fn proxy_cache_returns_consistent_data() {
    let app = test_app();
    let url = "/?endpoint=markets&_limit=5&active=true&order=volume&ascending=false";

    let resp1 = app
        .clone()
        .oneshot(Request::get(url).body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp1.status(), StatusCode::OK);
    let data1 = resp_json(resp1).await;

    let resp2 = app
        .oneshot(Request::get(url).body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp2.status(), StatusCode::OK);
    let cache_header = resp2.headers().get("x-cache").unwrap().to_str().unwrap();
    assert_eq!(cache_header, "HIT");
    let data2 = resp_json(resp2).await;

    assert_eq!(data1, data2, "cached response should match original");
    println!("PASS: cache returns consistent data");
}

#[tokio::test]
async fn pipeline_cache_preserves_trader_data() {
    let app = test_app();

    let resp1 = app
        .clone()
        .oneshot(
            Request::get("/active-traders?days=1&pool=50")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp1.status(), StatusCode::OK);
    let body1 = resp_json(resp1).await;
    let source1 = body1["source"].as_str().unwrap_or("");

    let resp2 = app
        .oneshot(
            Request::get("/active-traders?days=1&pool=50")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp2.status(), StatusCode::OK);
    let body2 = resp_json(resp2).await;
    let source2 = body2["source"].as_str().unwrap_or("");

    if source1 == "fresh" {
        assert!(source2 == "memory", "expected memory cache hit, got {}", source2);
    }

    assert_eq!(body1["count"], body2["count"]);
    assert_eq!(body1["traders"], body2["traders"]);
    println!("PASS: pipeline cache preserves trader data (source: {} -> {})", source1, source2);
}

// ── Search integrity ──

#[tokio::test]
async fn search_returns_valid_markets() {
    let app = test_app();
    let resp = app
        .oneshot(
            Request::get("/?endpoint=public-search&q=bitcoin&_limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    // Upstream may occasionally 502
    if resp.status() != StatusCode::OK {
        println!("PASS: search upstream returned {} (tolerated)", resp.status());
        return;
    }
    let body = resp_json(resp).await;

    if let Some(events) = body.as_array().or(body.get("events").and_then(|v| v.as_array())) {
        for evt in events {
            if let Some(markets) = evt.get("markets").and_then(|v| v.as_array()) {
                for m in markets {
                    let q = m.get("question").and_then(|v| v.as_str()).unwrap_or("");
                    assert!(!q.is_empty(), "search result market should have question");
                }
            }
        }
        println!("PASS: search returned {} results", events.len());
    } else {
        println!("PASS: search returned non-array response (acceptable)");
    }
}
