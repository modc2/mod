//! Content-addressable share backend for user strats.
//!
//! A strat is shared by serializing it into a self-describing JSON bundle
//! (`StratBundle`, defined in `user_strats.rs`) and storing those exact bytes
//! in a content-addressable store. The store returns an IPFS-compatible CID;
//! that CID is the share link — anyone can import the strat by CID.
//!
//! ## Adaptable to other systems
//!
//! The backend is just an HTTP endpoint that speaks a tiny two-call contract:
//!
//!   POST {base}/put         (raw body)  → {"cid": "<cid>"}
//!   GET  {base}/get/{cid}               → the original bytes
//!
//! The orbit `localfs` module implements exactly this on `:8860`, so it is the
//! default. Point `POLYMARKET_SHARE_URL` (or `LOCALFS_URL`) at any other
//! service that speaks the same contract — a *remote* localfs shared by several
//! deploys, the `store` module, or a thin adapter in front of a real IPFS node
//! / pinning service — and sharing works across systems unchanged. Because the
//! bundle is plain bytes and the CID is computed the IPFS way (UnixFS DAG-PB),
//! the *same* bundle re-pinned to *any* IPFS-compatible store resolves to the
//! *same* CID: the share link is portable even when the backend is not.

use anyhow::{anyhow, Context, Result};
use serde::Deserialize;

/// Default backend: the local `localfs` module's HTTP API.
const DEFAULT_SHARE_URL: &str = "http://localhost:8860";

/// Thin client over a content-addressable store. Cheap to clone (just a URL);
/// reuses the shared `reqwest::Client` passed in by callers.
#[derive(Clone, Debug)]
pub struct ShareStore {
    base_url: String,
}

impl ShareStore {
    /// Resolve the backend URL from the environment, falling back to a local
    /// `localfs`. `POLYMARKET_SHARE_URL` wins so a deploy can point sharing at
    /// a shared/remote store without disturbing the module's own `LOCALFS_URL`.
    pub fn from_env() -> Self {
        let base_url = std::env::var("POLYMARKET_SHARE_URL")
            .ok()
            .filter(|s| !s.trim().is_empty())
            .or_else(|| std::env::var("LOCALFS_URL").ok().filter(|s| !s.trim().is_empty()))
            .unwrap_or_else(|| DEFAULT_SHARE_URL.to_string());
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
        }
    }

    /// Backend URL — surfaced to the UI so a user knows where their strat went.
    pub fn label(&self) -> &str {
        &self.base_url
    }

    /// Store `bytes`, return the content CID, and best-effort pin so a GC sweep
    /// on the backend doesn't drop a freshly-shared strat before anyone imports
    /// it. A failed pin is not fatal — the bytes are stored either way.
    pub async fn put_and_pin(&self, client: &reqwest::Client, bytes: Vec<u8>) -> Result<String> {
        let cid = self.put(client, bytes).await?;
        let _ = self.pin(client, &cid).await;
        Ok(cid)
    }

    /// Store raw bytes. Sent as `application/octet-stream` (not JSON) so the
    /// backend hashes exactly the bytes we produced — keeping the CID
    /// deterministic and portable rather than re-serializing the payload.
    pub async fn put(&self, client: &reqwest::Client, bytes: Vec<u8>) -> Result<String> {
        let url = format!("{}/put", self.base_url);
        let resp = client
            .post(&url)
            .header("content-type", "application/octet-stream")
            .body(bytes)
            .send()
            .await
            .with_context(|| format!("contact share store at {}", self.base_url))?;
        let status = resp.status();
        if !status.is_success() {
            return Err(anyhow!("share store PUT {} → HTTP {}", url, status));
        }
        let parsed: PutResp = resp.json().await.context("parse share store /put response")?;
        if parsed.cid.trim().is_empty() {
            return Err(anyhow!("share store returned an empty cid"));
        }
        Ok(parsed.cid)
    }

    /// Fetch the bytes previously stored under `cid`.
    pub async fn get(&self, client: &reqwest::Client, cid: &str) -> Result<Vec<u8>> {
        validate_cid(cid)?;
        let url = format!("{}/get/{}", self.base_url, cid);
        let resp = client
            .get(&url)
            .send()
            .await
            .with_context(|| format!("contact share store at {}", self.base_url))?;
        let status = resp.status();
        if status.as_u16() == 404 {
            return Err(anyhow!("nothing stored at {} on the share store", cid));
        }
        if !status.is_success() {
            return Err(anyhow!("share store GET {} → HTTP {}", url, status));
        }
        Ok(resp.bytes().await.context("read share store body")?.to_vec())
    }

    async fn pin(&self, client: &reqwest::Client, cid: &str) -> Result<()> {
        let url = format!("{}/pin/{}", self.base_url, cid);
        client.post(&url).send().await?;
        Ok(())
    }
}

#[derive(Deserialize)]
struct PutResp {
    cid: String,
}

/// CIDs flow into a URL path, so reject anything that could alter the path
/// (slashes, dots, whitespace). CIDv0 (`Qm…`, base58btc) and CIDv1 (`b…`,
/// base32) are both within the ASCII-alphanumeric set, so this is permissive
/// enough for real CIDs while blocking traversal.
fn validate_cid(cid: &str) -> Result<()> {
    let cid = cid.trim();
    if cid.is_empty() || cid.len() > 128 {
        return Err(anyhow!("cid length must be 1–128"));
    }
    if !cid.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err(anyhow!("cid may only contain alphanumeric characters"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_cid_blocks_traversal() {
        assert!(validate_cid("QmZ1DErg6Agaf4i1FpUU8MtPkWyyJ4TFDHDznm5PuRTaQz").is_ok());
        assert!(validate_cid("bafybeigdyrztxyz234").is_ok());
        assert!(validate_cid("../etc/passwd").is_err());
        assert!(validate_cid("Qm/../x").is_err());
        assert!(validate_cid("").is_err());
    }

    #[test]
    fn from_env_trims_trailing_slash() {
        // No env set in test → default localfs, no trailing slash.
        let s = ShareStore::from_env();
        assert!(!s.label().ends_with('/'));
    }
}
