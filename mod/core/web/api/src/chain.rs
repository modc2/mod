//! Chain module client — the web front door's one dependency on another module.
//!
//! The mod protocol's source of truth for *what is registered* is the on-chain
//! Registry, which the `chain` module exposes over its hub API. Rather than
//! reach into web3/RPC from here, we depend on the chain module the protocol
//! way: an HTTP call to its hub (`/registry/all`). The result is cached behind
//! a short TTL and degrades gracefully — if chain is down or undeployed, the
//! catalog still serves, it just reports nothing as registered.

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::time::{Duration, Instant};

/// One mod as recorded in the on-chain Registry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegEntry {
    #[serde(default)]
    pub id: serde_json::Value,
    #[serde(default)]
    pub owner: String,
    pub name: String,
    #[serde(default)]
    pub data: String,
}

/// The registry view returned to the frontend: whether the chain module was
/// reachable, the network it answered for, and every registered mod.
#[derive(Debug, Clone, Serialize)]
pub struct RegistryView {
    /// True if the chain hub answered. When false the list is empty and the UI
    /// hides on-chain badges rather than implying nothing is registered.
    pub available: bool,
    pub source: String,
    pub network: String,
    pub mods: Vec<RegEntry>,
}

#[derive(Deserialize)]
struct RegistryAllResponse {
    #[serde(default)]
    network: String,
    #[serde(default)]
    mods: Vec<RegEntry>,
}

struct Cached {
    view: RegistryView,
    at: Instant,
}

/// TTL-cached client for the chain module's registry.
pub struct ChainClient {
    base_url: String,
    network: String,
    ttl: Duration,
    http: reqwest::Client,
    cache: RwLock<Option<Cached>>,
}

impl ChainClient {
    /// Build a client. The hub URL and target network come from the
    /// environment so deployment can point at testnet/mainnet without a
    /// rebuild; defaults match the chain module's local dev setup.
    pub fn from_env() -> Self {
        let base_url = std::env::var("CHAIN_API_URL")
            .unwrap_or_else(|_| "http://localhost:8800".to_string());
        let network = std::env::var("CHAIN_NETWORK").unwrap_or_else(|_| "testnet".to_string());
        // The chain hub fans out one RPC call per registered mod against a
        // public testnet, so the round trip can take several seconds. Allow
        // generous headroom; the result is cached for the TTL afterwards.
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(15))
            .build()
            .unwrap_or_default();
        Self {
            base_url,
            network,
            ttl: Duration::from_secs(30),
            http,
            cache: RwLock::new(None),
        }
    }

    /// Fetch the on-chain registry, serving a cached copy within the TTL.
    pub async fn registry(&self) -> RegistryView {
        if let Some(c) = self.cache.read().as_ref() {
            if c.at.elapsed() < self.ttl {
                return c.view.clone();
            }
        }
        let view = self.fetch().await;
        *self.cache.write() = Some(Cached {
            view: view.clone(),
            at: Instant::now(),
        });
        view
    }

    /// Just the set of registered module names (lowercased), for merging into
    /// the catalog. Empty when chain is unreachable.
    pub async fn registered_names(&self) -> HashSet<String> {
        self.registry()
            .await
            .mods
            .iter()
            .map(|m| m.name.to_lowercase())
            .collect()
    }

    async fn fetch(&self) -> RegistryView {
        let url = format!("{}/registry/all?network={}", self.base_url, self.network);
        let result: Result<RegistryAllResponse, String> = async {
            let resp = self
                .http
                .get(&url)
                .send()
                .await
                .map_err(|e| e.to_string())?;
            if !resp.status().is_success() {
                return Err(format!("chain hub returned {}", resp.status()));
            }
            resp.json::<RegistryAllResponse>()
                .await
                .map_err(|e| e.to_string())
        }
        .await;

        match result {
            Ok(body) => RegistryView {
                available: true,
                source: self.base_url.clone(),
                network: if body.network.is_empty() {
                    self.network.clone()
                } else {
                    body.network
                },
                mods: body.mods,
            },
            Err(e) => {
                tracing::warn!("chain registry unavailable at {url}: {e}");
                RegistryView {
                    available: false,
                    source: self.base_url.clone(),
                    network: self.network.clone(),
                    mods: Vec::new(),
                }
            }
        }
    }
}
