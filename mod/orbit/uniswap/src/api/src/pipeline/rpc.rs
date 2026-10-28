use anyhow::{anyhow, Result};
use std::sync::atomic::{AtomicUsize, Ordering};

use crate::config;
use crate::models::chain::Chain;

/// Round-robin counter for RPC endpoint selection
static RPC_INDEX: AtomicUsize = AtomicUsize::new(0);

/// Next RPC endpoint for a chain (round-robin across the configured set).
pub fn next_rpc(chain: &Chain) -> &'static str {
    let endpoints = config::rpc_endpoints(chain);
    let idx = RPC_INDEX.fetch_add(1, Ordering::Relaxed) % endpoints.len();
    endpoints[idx]
}

/// One JSON-RPC call, retried across the chain's endpoints.
///
/// A JSON-RPC `error` object is a failure like any transport failure — the
/// previous version of this pipeline read `json["result"]` and treated a
/// missing result as "no logs here", which is how five dead endpoints turned
/// into an empty leaderboard instead of an error. Every endpoint is tried
/// before giving up, and the last error is what the caller sees.
pub async fn call(
    http: &reqwest::Client,
    chain: &Chain,
    method: &str,
    params: serde_json::Value,
) -> Result<serde_json::Value> {
    let endpoints = config::rpc_endpoints(chain);
    let mut last_err = anyhow!("no RPC endpoints configured for {}", chain.name());

    // Several passes over the endpoint set. Free public nodes answer a burst
    // and then start refusing: a single pass turns a momentary rate limit into
    // a permanently missing slice of the window, which the sample then reports
    // as a quiet hour on the chain rather than as data it failed to read.
    let attempts = endpoints.len() * config::RPC_ROUNDS;

    for attempt in 0..attempts {
        if attempt >= endpoints.len() {
            // Later passes: give the endpoints time to refill before asking again.
            let round = (attempt / endpoints.len()) as u64;
            tokio::time::sleep(std::time::Duration::from_millis(
                config::RPC_BACKOFF_MS * round,
            ))
            .await;
        }

        let rpc = next_rpc(chain);
        let body = serde_json::json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        });

        let resp = match http.post(rpc).json(&body).send().await {
            Ok(r) => r,
            Err(e) => {
                last_err = anyhow!("{rpc}: {e}");
                continue;
            }
        };

        let json: serde_json::Value = match resp.json().await {
            Ok(j) => j,
            Err(e) => {
                last_err = anyhow!("{rpc}: bad JSON: {e}");
                continue;
            }
        };

        // A JSON-RPC `error` object is a failure like any transport failure.
        // Reading `json["result"]` and treating a missing result as "no logs
        // here" is how five dead endpoints became an empty leaderboard
        // instead of an error.
        if let Some(err) = json.get("error") {
            last_err = anyhow!("{rpc}: {err}");
            continue;
        }

        match json.get("result") {
            Some(serde_json::Value::Null) | None => {
                last_err = anyhow!("{rpc}: null result for {method}");
                continue;
            }
            Some(result) => return Ok(result.clone()),
        }
    }

    Err(last_err)
}

/// Parse a `0x`-prefixed quantity.
pub fn hex_u64(v: &serde_json::Value) -> Option<u64> {
    u64::from_str_radix(v.as_str()?.trim_start_matches("0x"), 16).ok()
}

/// `eth_call` against a contract, returning the raw hex return data.
pub async fn eth_call(
    http: &reqwest::Client,
    chain: &Chain,
    to: &str,
    data: &str,
) -> Result<String> {
    let result = call(
        http,
        chain,
        "eth_call",
        serde_json::json!([{ "to": to, "data": data }, "latest"]),
    )
    .await?;

    Ok(result
        .as_str()
        .ok_or_else(|| anyhow!("eth_call returned non-string"))?
        .trim_start_matches("0x")
        .to_string())
}
