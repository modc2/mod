//! Minimal Starknet JSON-RPC client (blocking; call from spawn_blocking).
//!
//! Public endpoints only, tried in order. STARKNET_RPC overrides the list.
//! Blast/Lava/1rpc Starknet are dead; publicnode and zan 403 a default UA,
//! so every request carries one.

use num_bigint::BigUint;
use serde_json::{json, Value};
use sha3::{Digest, Keccak256};
use std::time::Duration;

pub const STRK: &str = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d";
pub const ETH: &str = "0x049d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7";
/// Starknet native staking contract (mainnet, v2)
pub const STAKING: &str = "0x00ca1702e64c81d9a07b86bd2c540188d92a2c73cf5cc0e508d949015e7e84a7";

pub struct Rpc {
    pub urls: Vec<String>,
}

impl Rpc {
    pub fn new(network: &str) -> Self {
        let urls = match std::env::var("STARKNET_RPC") {
            Ok(u) if !u.is_empty() => vec![u],
            _ if network == "sepolia" => vec![
                "https://starknet-sepolia-rpc.publicnode.com".into(),
                "https://api.zan.top/public/starknet-sepolia".into(),
            ],
            _ => vec![
                "https://starknet-rpc.publicnode.com".into(),
                "https://api.zan.top/public/starknet-mainnet".into(),
            ],
        };
        Rpc { urls }
    }

    pub fn rpc(&self, method: &str, params: Value) -> Result<Value, String> {
        let body = json!({"jsonrpc": "2.0", "id": 1, "method": method, "params": params});
        let mut last = String::from("no rpc endpoints");
        for url in &self.urls {
            let resp = ureq::post(url)
                .set("User-Agent", "mod-starktime/0.1")
                .set("Content-Type", "application/json")
                .timeout(Duration::from_secs(10))
                .send_json(body.clone());
            match resp {
                Ok(r) => match r.into_json::<Value>() {
                    Ok(v) => {
                        if let Some(e) = v.get("error") {
                            // a contract error is an answer, not a dead node
                            return Err(format!("rpc error: {e}"));
                        }
                        return Ok(v.get("result").cloned().unwrap_or(Value::Null));
                    }
                    Err(e) => last = format!("{url}: bad json: {e}"),
                },
                Err(e) => last = format!("{url}: {e}"),
            }
        }
        Err(last)
    }

    pub fn call(&self, contract: &str, entry: &str, calldata: &[String]) -> Result<Vec<String>, String> {
        let r = self.rpc(
            "starknet_call",
            json!([{"contract_address": contract, "entry_point_selector": selector(entry), "calldata": calldata}, "latest"]),
        )?;
        Ok(r.as_array()
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default())
    }

    pub fn block_number(&self) -> Result<u64, String> {
        self.rpc("starknet_blockNumber", json!([]))?.as_u64().ok_or("bad block number".into())
    }

    pub fn chain_id(&self) -> Result<String, String> {
        let hex = self.rpc("starknet_chainId", json!([]))?.as_str().unwrap_or("").to_string();
        // 0x534e5f4d41494e -> "SN_MAIN"
        let bytes = BigUint::parse_bytes(hex.trim_start_matches("0x").as_bytes(), 16)
            .map(|b| b.to_bytes_be())
            .unwrap_or_default();
        Ok(String::from_utf8(bytes).unwrap_or(hex))
    }

    /// ERC-20 balance in whole tokens (18 decimals) + raw decimal string.
    pub fn balance(&self, token: &str, owner: &str) -> Result<(f64, String), String> {
        let r = self.call(token, "balance_of", &[owner.to_string()])?;
        let raw = u256(&r, 0);
        Ok((units(&raw, 18), raw.to_string()))
    }
}

/// starknet_keccak: keccak256 masked to 250 bits.
pub fn selector(name: &str) -> String {
    let mut h: [u8; 32] = Keccak256::digest(name.as_bytes()).into();
    h[0] &= 0x03;
    format!("0x{}", BigUint::from_bytes_be(&h).to_str_radix(16))
}

pub fn felt(s: &str) -> BigUint {
    BigUint::parse_bytes(s.trim_start_matches("0x").as_bytes(), 16).unwrap_or_default()
}

/// u256 = (low, high) felts starting at index i
pub fn u256(r: &[String], i: usize) -> BigUint {
    let lo = r.get(i).map(|s| felt(s)).unwrap_or_default();
    let hi = r.get(i + 1).map(|s| felt(s)).unwrap_or_default();
    lo + (hi << 128)
}

pub fn units(raw: &BigUint, decimals: u32) -> f64 {
    raw.to_string().parse::<f64>().unwrap_or(0.0) / 10f64.powi(decimals as i32)
}

pub fn valid_address(a: &str) -> bool {
    let h = a.trim().strip_prefix("0x").unwrap_or("");
    !h.is_empty() && h.len() <= 64 && h.chars().all(|c| c.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selector_matches_starknet() {
        // well-known: selector!("transfer")
        assert_eq!(
            selector("transfer"),
            "0x83afd3f4caedc6eebf44246fe54e38c95e3179a5ec9ea81740eca5b482d12e"
        );
    }

    #[test]
    fn u256_combines_limbs() {
        let r = vec!["0x1".to_string(), "0x1".to_string()];
        assert_eq!(u256(&r, 0), BigUint::from(1u8) + (BigUint::from(1u8) << 128));
    }
}
