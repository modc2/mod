//! Upstream client for the Lium platform API (https://lium.io/api) — the GPU
//! marketplace of Bittensor subnet 51. Providers list nodes, validators score
//! them and set weights on-chain, renters rent pods by the hour.
//!
//! Every call here is one HTTP hop; auth is the caller's own `X-API-Key`
//! (Settings → API Keys on lium.io). Public reads (nodes, templates, stats,
//! subnet weights) work with no key at all.

use serde_json::{json, Value};
use std::sync::OnceLock;
use std::time::Duration;
use tokio::sync::OnceCell;

/// The Bittensor netuid the Lium compute subnet runs on.
pub const NETUID: u64 = 51;

fn client() -> &'static reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(120))
            // lium.io sits behind CloudFront, which blocks agent-less clients.
            .user_agent(concat!("lium-mod/", env!("CARGO_PKG_VERSION"), " (mod protocol)"))
            .build()
            .expect("reqwest client")
    })
}

pub fn base_url() -> String {
    std::env::var("LIUM_BASE_URL").unwrap_or_else(|_| "https://lium.io/api".into())
}

/// Key precedence: explicit (per request) > LIUM_API_KEY > ~/.mod/lium/api_key.
/// Never a committed file — the key file lives off-tree with 0600.
pub fn resolve_key(explicit: Option<&str>) -> String {
    if let Some(k) = explicit {
        if !k.trim().is_empty() {
            return k.trim().to_string();
        }
    }
    if let Ok(k) = std::env::var("LIUM_API_KEY") {
        if !k.trim().is_empty() {
            return k.trim().to_string();
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        if let Ok(k) = std::fs::read_to_string(format!("{home}/.mod/lium/api_key")) {
            if !k.trim().is_empty() {
                return k.trim().to_string();
            }
        }
    }
    String::new()
}

#[derive(Debug)]
pub struct ApiError {
    pub status: u16,
    pub message: String,
    /// True when lium.io said no, false when we refused before asking.
    pub upstream: bool,
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.upstream {
            write!(f, "upstream {}: {}", self.status, self.message)
        } else {
            write!(f, "{}", self.message)
        }
    }
}

impl ApiError {
    pub fn local(status: u16, message: impl Into<String>) -> Self {
        ApiError { status, message: message.into(), upstream: false }
    }
    fn net(e: reqwest::Error) -> Self {
        ApiError { status: 502, message: e.to_string(), upstream: true }
    }
    pub fn needs_key(what: &str) -> Self {
        ApiError::local(
            401,
            format!("{what} needs a Lium API key (x-api-key header, LIUM_API_KEY, or ~/.mod/lium/api_key)"),
        )
    }
}

pub async fn request(
    method: reqwest::Method,
    path: &str,
    key: &str,
    body: Option<&Value>,
    query: &[(String, String)],
) -> Result<Value, ApiError> {
    let url = format!("{}{}", base_url(), path);
    let mut req = client().request(method, &url);
    if !key.is_empty() {
        req = req.header("X-API-Key", key);
    }
    if let Some(b) = body {
        req = req.json(b);
    }
    if !query.is_empty() {
        req = req.query(query);
    }
    let resp = req.send().await.map_err(ApiError::net)?;
    let status = resp.status().as_u16();
    let text = resp.text().await.unwrap_or_default();
    // Some endpoints (pod logs) answer in plain text — keep it, don't lose it.
    let value = serde_json::from_str::<Value>(&text).unwrap_or_else(|_| json!({ "text": text }));
    if status >= 400 {
        let message = value
            .get("message")
            .and_then(|m| m.as_str())
            .map(String::from)
            .unwrap_or_else(|| value.to_string());
        return Err(ApiError { status, message, upstream: true });
    }
    Ok(value)
}

pub async fn get(path: &str, key: &str, query: &[(String, String)]) -> Result<Value, ApiError> {
    request(reqwest::Method::GET, path, key, None, query).await
}

pub async fn post(path: &str, key: &str, body: &Value) -> Result<Value, ApiError> {
    request(reqwest::Method::POST, path, key, Some(body), &[]).await
}

pub async fn del(path: &str, key: &str) -> Result<Value, ApiError> {
    request(reqwest::Method::DELETE, path, key, None, &[]).await
}

/// The live OpenAPI 3.1 spec, fetched once and kept — it is what the API
/// explorer lists, so the console can never drift from the real platform.
pub async fn openapi() -> Result<Value, ApiError> {
    static SPEC: OnceCell<Value> = OnceCell::const_new();
    if let Some(v) = SPEC.get() {
        return Ok(v.clone());
    }
    let v = get("/openapi.json", "", &[]).await?;
    Ok(SPEC.get_or_init(|| async { v }).await.clone())
}

/// Flat {method, path, summary, auth} view of the spec — one row per operation.
pub async fn endpoints() -> Result<Value, ApiError> {
    let spec = openapi().await?;
    let mut rows = Vec::new();
    if let Some(paths) = spec.get("paths").and_then(|p| p.as_object()) {
        for (path, ops) in paths {
            let ops = match ops.as_object() {
                Some(o) => o,
                None => continue,
            };
            for (method, op) in ops {
                if !matches!(method.as_str(), "get" | "post" | "put" | "patch" | "delete") {
                    continue;
                }
                let tags = op.get("tags").and_then(|t| t.as_array()).cloned().unwrap_or_default();
                let tag = tags.first().and_then(|t| t.as_str()).unwrap_or("").to_string();
                rows.push(json!({
                    "method": method.to_uppercase(),
                    "path": path,
                    "summary": op.get("summary").and_then(|s| s.as_str()).unwrap_or(""),
                    "operation_id": op.get("operationId").and_then(|s| s.as_str()).unwrap_or(""),
                    "tag": tag,
                    "auth": op.get("security").is_some(),
                }));
            }
        }
    }
    rows.sort_by(|a, b| {
        let pa = a["path"].as_str().unwrap_or("");
        let pb = b["path"].as_str().unwrap_or("");
        pa.cmp(pb).then(a["method"].as_str().cmp(&b["method"].as_str()))
    });
    let version = spec
        .get("info")
        .and_then(|i| i.get("version"))
        .cloned()
        .unwrap_or(json!("?"));
    Ok(json!({ "base_url": base_url(), "version": version, "count": rows.len(), "endpoints": rows }))
}

// ── shaping ──────────────────────────────────────────────────────

fn f(v: &Value, key: &str) -> f64 {
    v.get(key).and_then(|x| x.as_f64()).unwrap_or(0.0)
}

fn round(x: f64, places: u32) -> f64 {
    let m = 10f64.powi(places as i32);
    (x * m).round() / m
}

/// Kilobytes (how lium reports RAM/disk) → gigabytes.
fn kb_gb(kb: f64) -> f64 {
    round(kb / 1_048_576.0, 1)
}

/// One marketplace row: what you need to pick a node, without the 40 KB of specs.
pub fn compact_executor(e: &Value) -> Value {
    let specs = e.get("specs").cloned().unwrap_or(json!({}));
    let gpu = specs.get("gpu").cloned().unwrap_or(json!({}));
    let first = gpu
        .get("details")
        .and_then(|d| d.as_array())
        .and_then(|d| d.first())
        .cloned()
        .unwrap_or(json!({}));
    let count = e.get("gpu_count").and_then(|c| c.as_u64()).unwrap_or(0);
    let per_gpu = f(e, "price_per_gpu");
    let loc = e.get("location").cloned().unwrap_or(json!({}));
    let city = loc.get("city").and_then(|c| c.as_str()).unwrap_or("");
    let cc = loc.get("country_code").and_then(|c| c.as_str()).unwrap_or("");
    json!({
        "id": e.get("id").cloned().unwrap_or(Value::Null),
        "gpu": e.get("machine_name").cloned().unwrap_or(Value::Null),
        "gpu_count": count,
        "available_gpu_count": e.get("available_gpu_count").cloned().unwrap_or(Value::Null),
        "min_rental_gpus": e.get("min_gpu_count_for_rental").cloned().unwrap_or(Value::Null),
        "price_per_gpu_hr": per_gpu,
        "price_per_hr": round(per_gpu * count as f64, 3),
        "vram_gb_per_gpu": round(f(&first, "capacity") / 1024.0, 0),
        "cuda_max": e.get("max_cuda_version").cloned().unwrap_or(Value::Null),
        "driver": gpu.get("driver").cloned().unwrap_or(Value::Null),
        "cpu": specs.get("cpu").and_then(|c| c.get("model")).cloned().unwrap_or(Value::Null),
        "cpu_count": specs.get("cpu").and_then(|c| c.get("count")).cloned().unwrap_or(Value::Null),
        "ram_gb": kb_gb(specs.get("ram").map(|r| f(r, "total")).unwrap_or(0.0)),
        "disk_gb": kb_gb(specs.get("hard_disk").map(|d| f(d, "total")).unwrap_or(0.0)),
        "tier": e.get("tier").cloned().unwrap_or(Value::Null),
        "reliability": e.get("reliability_score").cloned().unwrap_or(Value::Null),
        "uptime_hours": round(e.get("uptime_in_minutes").and_then(|u| u.as_f64()).unwrap_or(0.0) / 60.0, 1),
        "up_mbps": round(f(e, "effective_upload_speed_mbps"), 1),
        "down_mbps": round(f(e, "effective_download_speed_mbps"), 1),
        "location": if city.is_empty() { cc.to_string() } else { format!("{city}, {cc}") },
        "country": loc.get("country").cloned().unwrap_or(Value::Null),
        // The Bittensor side of a node: which provider (miner) runs it and
        // which validator scored it.
        "miner_hotkey": e.get("miner_hotkey").cloned().unwrap_or(Value::Null),
        "validator_hotkey": e.get("validator_hotkey").cloned().unwrap_or(Value::Null),
        "collateral_deposited": e.get("collateral_deposited").cloned().unwrap_or(Value::Null),
        "ncu_profiling": e.get("ncu_profiling_enabled").cloned().unwrap_or(Value::Null),
        "ip": e.get("executor_ip_address").cloned().unwrap_or(Value::Null),
    })
}

/// Client-side narrowing the upstream query params don't cover: GPU-name
/// substring, country, tier, "has a free GPU right now", sort, limit.
pub fn filter_sort(rows: Vec<Value>, args: &Value) -> Vec<Value> {
    let want = |k: &str| args.get(k).and_then(|v| v.as_str()).map(|s| s.to_lowercase());
    let gpu = want("gpu_type").or_else(|| want("gpu"));
    let country = want("country");
    let tier = want("tier");
    let available_only = args.get("available_only").and_then(|v| v.as_bool()).unwrap_or(false);
    let max_price = args.get("max_price").and_then(|v| v.as_f64());
    let min_gpus = args.get("min_gpus").and_then(|v| v.as_u64());

    let mut out: Vec<Value> = rows
        .into_iter()
        .filter(|r| {
            let name = r["gpu"].as_str().unwrap_or("").to_lowercase();
            if let Some(g) = &gpu {
                if !name.contains(g.as_str()) {
                    return false;
                }
            }
            if let Some(c) = &country {
                let loc = format!(
                    "{} {}",
                    r["location"].as_str().unwrap_or(""),
                    r["country"].as_str().unwrap_or("")
                )
                .to_lowercase();
                if !loc.contains(c.as_str()) {
                    return false;
                }
            }
            if let Some(t) = &tier {
                if r["tier"].as_str().unwrap_or("").to_lowercase() != *t {
                    return false;
                }
            }
            if available_only && r["available_gpu_count"].as_u64().unwrap_or(0) == 0 {
                return false;
            }
            if let Some(p) = max_price {
                if r["price_per_gpu_hr"].as_f64().unwrap_or(f64::MAX) > p {
                    return false;
                }
            }
            if let Some(n) = min_gpus {
                if r["gpu_count"].as_u64().unwrap_or(0) < n {
                    return false;
                }
            }
            true
        })
        .collect();

    let sort = args.get("sort").and_then(|v| v.as_str()).unwrap_or("price");
    let num = |r: &Value, k: &str| r[k].as_f64().unwrap_or(0.0);
    match sort {
        "reliability" => out.sort_by(|a, b| num(b, "reliability").total_cmp(&num(a, "reliability"))),
        "gpu_count" => out.sort_by(|a, b| num(b, "gpu_count").total_cmp(&num(a, "gpu_count"))),
        "uptime" => out.sort_by(|a, b| num(b, "uptime_hours").total_cmp(&num(a, "uptime_hours"))),
        "vram" => out.sort_by(|a, b| num(b, "vram_gb_per_gpu").total_cmp(&num(a, "vram_gb_per_gpu"))),
        _ => out.sort_by(|a, b| num(a, "price_per_gpu_hr").total_cmp(&num(b, "price_per_gpu_hr"))),
    }
    let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(50) as usize;
    out.truncate(limit);
    out
}

/// Fetch the marketplace (one page big enough to hold it) as compact rows.
pub async fn executor_rows(key: &str, size: u64) -> Result<Vec<Value>, ApiError> {
    let v = get("/executors", key, &[("size".into(), size.to_string())]).await?;
    let list = v
        .as_array()
        .cloned()
        .or_else(|| v.get("items").and_then(|i| i.as_array()).cloned())
        .unwrap_or_default();
    Ok(list.iter().map(compact_executor).collect())
}

/// The subnet 51 view: supply, price, and the on-chain scoring that decides
/// which providers get paid — one call, because that is the question people
/// actually ask ("what is SN51 doing right now?").
pub async fn subnet(key: &str) -> Result<Value, ApiError> {
    let (stats, capacity, total, weights, rows) = tokio::join!(
        get("/executors/stats", key, &[]),
        get("/machines/capacity", key, &[]),
        get("/executors/total-count", key, &[]),
        get("/latest-set-weights", key, &[]),
        executor_rows(key, 500),
    );
    let rows = rows.unwrap_or_default();

    let mut miners = std::collections::BTreeSet::new();
    let mut validators = std::collections::BTreeSet::new();
    let mut gpus = 0u64;
    let mut free = 0u64;
    let mut spend = 0.0;
    for r in &rows {
        if let Some(m) = r["miner_hotkey"].as_str() {
            miners.insert(m.to_string());
        }
        if let Some(v) = r["validator_hotkey"].as_str() {
            validators.insert(v.to_string());
        }
        gpus += r["gpu_count"].as_u64().unwrap_or(0);
        free += r["available_gpu_count"].as_u64().unwrap_or(0);
        spend += r["price_per_hr"].as_f64().unwrap_or(0.0);
    }

    let weights = weights.unwrap_or(json!({}));
    let uids = weights.get("uids").and_then(|u| u.as_array()).cloned().unwrap_or_default();
    let ws = weights.get("weights").and_then(|w| w.as_array()).cloned().unwrap_or_default();
    let total_w: f64 = ws.iter().filter_map(|w| w.as_f64()).sum();
    let mut scored: Vec<Value> = uids
        .iter()
        .zip(ws.iter())
        .map(|(u, w)| {
            let wv = w.as_f64().unwrap_or(0.0);
            json!({
                "uid": u,
                "weight": w,
                "share": if total_w > 0.0 { round(wv / total_w * 100.0, 2) } else { 0.0 }
            })
        })
        .collect();
    scored.sort_by(|a, b| b["weight"].as_f64().unwrap_or(0.0).total_cmp(&a["weight"].as_f64().unwrap_or(0.0)));
    scored.truncate(10);

    Ok(json!({
        "netuid": NETUID,
        "name": "Lium (compute)",
        "chain": "bittensor",
        "marketplace": {
            "nodes_rentable": rows.len(),
            "nodes_total": total.ok().and_then(|t| t.get("total_count").cloned()).unwrap_or(Value::Null),
            "gpus": gpus,
            "gpus_available": free,
            "providers": miners.len(),
            "validators": validators.len(),
            "listed_value_per_hr_usd": round(spend, 2),
        },
        "utilization_by_gpu": stats.unwrap_or(json!([])),
        "capacity": capacity.unwrap_or(json!([])),
        "weights": {
            "validator_key": weights.get("validator_key").cloned().unwrap_or(Value::Null),
            "netuid": weights.get("netuid").cloned().unwrap_or(json!(NETUID)),
            "version_key": weights.get("version_key").cloned().unwrap_or(Value::Null),
            "updated_at": weights.get("current_time").or_else(|| weights.get("updated_at")).cloned().unwrap_or(Value::Null),
            "scored_uids": uids.len(),
            "top_uids": scored,
        },
    }))
}
