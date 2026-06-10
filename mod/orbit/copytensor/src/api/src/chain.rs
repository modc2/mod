//! Subtensor chain client — all on-chain reads via subxt dynamic mode.
//!
//! Connects to the Bittensor subtensor node (a Substrate chain) and queries
//! storage directly. No third-party APIs.

use anyhow::{Context, Result};
use subxt::dynamic::Value;
use subxt::{OnlineClient, SubstrateConfig};
use tracing::warn;

use crate::models::{AccountPositions, AlphaPosition, SubnetInfo};

const BLOCKS_PER_DAY: u64 = 7200;

const FINNEY_ENDPOINTS: &[&str] = &[
    "wss://entrypoint-finney.opentensor.ai:443",
    "wss://finney.opentensor.ai:443",
];

const TEST_ENDPOINTS: &[&str] = &["wss://test.finney.opentensor.ai:443"];

// ── Dynamic Value wrapper ────────────────────────────────────────

/// Wrapper around decoded subxt dynamic value for ergonomic field access.
/// `DecodedValueThunk::to_value()` returns `scale_value::Value<u32>`.
#[derive(Debug, Clone)]
pub struct DynVal {
    inner: scale_value::Value<u32>,
}

impl DynVal {
    pub fn new(val: scale_value::Value<u32>) -> Self {
        Self { inner: val }
    }

    /// Navigate to a named field in a composite/variant.
    pub fn at(&self, key: &str) -> Option<DynVal> {
        use scale_value::{Composite, ValueDef};
        let find_named = |fields: &[(String, scale_value::Value<u32>)]| -> Option<DynVal> {
            fields.iter().find(|(n, _)| n == key).map(|(_, v)| DynVal::new(v.clone()))
        };
        let find_indexed = |fields: &[scale_value::Value<u32>]| -> Option<DynVal> {
            key.parse::<usize>().ok().and_then(|i| fields.get(i).map(|v| DynVal::new(v.clone())))
        };
        match &self.inner.value {
            ValueDef::Composite(Composite::Named(f)) => find_named(f),
            ValueDef::Composite(Composite::Unnamed(f)) => find_indexed(f),
            ValueDef::Variant(v) => match &v.values {
                Composite::Named(f) => find_named(f),
                Composite::Unnamed(f) => find_indexed(f),
            },
            _ => None,
        }
    }

    /// Index into unnamed composite by position.
    pub fn at_index(&self, idx: usize) -> Option<DynVal> {
        use scale_value::{Composite, ValueDef};
        match &self.inner.value {
            ValueDef::Composite(Composite::Unnamed(f)) => f.get(idx).map(|v| DynVal::new(v.clone())),
            ValueDef::Composite(Composite::Named(f)) => f.get(idx).map(|(_, v)| DynVal::new(v.clone())),
            ValueDef::Variant(v) => match &v.values {
                Composite::Unnamed(f) => f.get(idx).map(|v| DynVal::new(v.clone())),
                Composite::Named(f) => f.get(idx).map(|(_, v)| DynVal::new(v.clone())),
            },
            _ => None,
        }
    }

    pub fn as_u128(&self) -> Option<u128> {
        // scale_value provides .as_u128() directly on the Value type
        self.inner.as_u128()
    }

    pub fn as_bool(&self) -> Option<bool> {
        self.inner.as_bool()
    }

    pub fn as_bytes(&self) -> Option<Vec<u8>> {
        use scale_value::{Composite, ValueDef};
        match &self.inner.value {
            ValueDef::Composite(Composite::Unnamed(fields)) => {
                let bytes: Vec<u8> = fields.iter().filter_map(|f| {
                    f.as_u128().map(|n| n as u8)
                }).collect();
                if bytes.len() == fields.len() && !bytes.is_empty() {
                    Some(bytes)
                } else {
                    None
                }
            }
            _ => None,
        }
    }

    pub fn as_string(&self) -> Option<String> {
        // Try as_str() first (scale_value provides this for string primitives)
        if let Some(s) = self.inner.as_str() {
            return Some(s.to_string());
        }
        self.as_bytes().and_then(|b| String::from_utf8(b).ok())
    }

    /// Iterate sequence/composite elements.
    pub fn iter_items(&self) -> Vec<DynVal> {
        use scale_value::{Composite, ValueDef};
        match &self.inner.value {
            ValueDef::Composite(Composite::Unnamed(fields)) => {
                fields.iter().map(|v: &scale_value::Value<u32>| DynVal::new(v.clone())).collect()
            }
            ValueDef::Composite(Composite::Named(fields)) => {
                fields.iter().map(|(_, v): &(String, scale_value::Value<u32>)| DynVal::new(v.clone())).collect()
            }
            _ => vec![],
        }
    }
}

// ── Subtensor Client ────────────────────────────────────────────

pub struct SubtensorClient {
    // Wrapped in RwLock so we can swap in a fresh OnlineClient on reconnect
    // without changing the SubtensorClient instance held by AppState.
    api: tokio::sync::RwLock<OnlineClient<SubstrateConfig>>,
    network: String,
    url: String,
}

impl SubtensorClient {
    pub async fn connect(network: &str, endpoint: Option<&str>) -> Result<Self> {
        let url = if let Some(ep) = endpoint {
            ep.to_string()
        } else {
            match network {
                "test" => TEST_ENDPOINTS[0].to_string(),
                _ => FINNEY_ENDPOINTS[0].to_string(),
            }
        };

        let api = OnlineClient::<SubstrateConfig>::from_url(&url)
            .await
            .context(format!("failed to connect to subtensor at {url}"))?;

        Ok(Self {
            api: tokio::sync::RwLock::new(api),
            network: network.to_string(),
            url,
        })
    }

    /// Rebuild the underlying OnlineClient. Call when the WS connection
    /// drops ("background task closed" — subxt 0.37 does not auto-reconnect).
    pub async fn reconnect(&self) -> Result<()> {
        tracing::info!("reconnecting subtensor at {}", self.url);
        let fresh = OnlineClient::<SubstrateConfig>::from_url(&self.url)
            .await
            .context(format!("reconnect to subtensor at {} failed", self.url))?;
        *self.api.write().await = fresh;
        tracing::info!("reconnected subtensor");
        Ok(())
    }

    pub async fn get_block(&self) -> Result<u64> {
        let block = self.api.read().await.blocks().at_latest().await?;
        Ok(block.number() as u64)
    }

    pub fn block_at_days_ago(&self, current_block: u64, days: u32) -> u64 {
        current_block.saturating_sub(days as u64 * BLOCKS_PER_DAY)
    }

    // ── subnets ──────────────────────────────────────────────────

    pub async fn get_all_netuids(&self) -> Result<Vec<u16>> {
        let total = self
            .fetch_u64("SubtensorModule", "TotalNetworks", vec![])
            .await
            .unwrap_or(64);

        let total = total as u16;
        let futs = (0..total).map(|netuid| async move {
            let exists = self
                .fetch_bool(
                    "SubtensorModule",
                    "NetworksAdded",
                    vec![Value::u128(netuid as u128)],
                )
                .await
                .unwrap_or(false);
            (netuid, exists)
        });
        let results = futures::future::join_all(futs).await;
        Ok(results.into_iter().filter_map(|(n, e)| e.then_some(n)).collect())
    }

    pub async fn get_subnet_info(&self, netuid: u16) -> Result<SubnetInfo> {
        let nk = vec![Value::u128(netuid as u128)];
        let name = self.get_subnet_name(netuid).await;
        let alpha_price = self.get_alpha_price(netuid, None).await.unwrap_or(0.0);
        let total_stake = self.get_subnet_tao(netuid, None).await.unwrap_or(0.0);
        let tempo = self
            .fetch_u64("SubtensorModule", "Tempo", nk.clone())
            .await
            .unwrap_or(360) as u16;
        let emission = self
            .fetch_u64("SubtensorModule", "SubnetEmission", nk)
            .await
            .unwrap_or(0) as f64
            / 1e9;

        Ok(SubnetInfo {
            netuid,
            name,
            alpha_price_tao: alpha_price,
            total_stake_tao: total_stake,
            tempo,
            emission,
        })
    }

    pub async fn get_all_subnet_info(&self) -> Result<Vec<SubnetInfo>> {
        let netuids = self.get_all_netuids().await?;
        // Fetch every subnet concurrently — sequential takes ~30-90s for
        // 60+ subnets (6 storage reads each), join_all drops it to ~5-10s.
        let futs = netuids.iter().map(|&n| self.get_subnet_info(n));
        let results = futures::future::join_all(futs).await;
        let mut out = Vec::with_capacity(netuids.len());
        for (netuid, r) in netuids.into_iter().zip(results) {
            match r {
                Ok(info) => out.push(info),
                Err(e) => warn!("skip subnet {netuid}: {e}"),
            }
        }
        Ok(out)
    }

    async fn get_subnet_name(&self, netuid: u16) -> String {
        let result = self
            .fetch_thunk(
                "SubtensorModule",
                "SubnetIdentity",
                vec![Value::u128(netuid as u128)],
            )
            .await;
        if let Ok(Some(thunk)) = result {
            if let Some(name) = thunk_field_to_string(&thunk, "subnet_name") {
                if !name.is_empty() {
                    return name;
                }
            }
        }
        format!("SN{netuid}")
    }

    // ── alpha prices ─────────────────────────────────────────────

    pub async fn get_alpha_price(&self, netuid: u16, _block: Option<u64>) -> Result<f64> {
        let subnet_tao = self.get_subnet_tao(netuid, _block).await?;
        let subnet_alpha = self.get_subnet_alpha_out(netuid, _block).await?;
        if subnet_alpha <= 0.0 {
            return Ok(0.0);
        }
        Ok(subnet_tao / subnet_alpha)
    }

    async fn get_subnet_tao(&self, netuid: u16, _block: Option<u64>) -> Result<f64> {
        let val = self
            .fetch_u64(
                "SubtensorModule",
                "SubnetTAO",
                vec![Value::u128(netuid as u128)],
            )
            .await?;
        Ok(val as f64 / 1e9)
    }

    async fn get_subnet_alpha_out(&self, netuid: u16, _block: Option<u64>) -> Result<f64> {
        let val = self
            .fetch_u64(
                "SubtensorModule",
                "SubnetAlphaOut",
                vec![Value::u128(netuid as u128)],
            )
            .await?;
        Ok(val as f64 / 1e9)
    }

    // ── account positions ────────────────────────────────────────

    pub async fn get_stake_for_coldkey(
        &self,
        ss58: &str,
        block: Option<u64>,
    ) -> Result<AccountPositions> {
        let current_block = match block {
            Some(b) => b,
            None => self.get_block().await?,
        };

        let netuids = self.get_all_netuids().await?;
        let hotkeys = self.get_hotkeys_for_coldkey(ss58).await?;

        let mut positions = Vec::new();
        let mut total_value = 0.0;

        for netuid in &netuids {
            let alpha_price = self.get_alpha_price(*netuid, block).await.unwrap_or(0.0);
            for hotkey in &hotkeys {
                let alpha = self
                    .get_alpha_stake(*netuid, hotkey, ss58)
                    .await
                    .unwrap_or(0.0);
                if alpha > 0.0 {
                    let value = alpha * alpha_price;
                    total_value += value;
                    positions.push(AlphaPosition {
                        netuid: *netuid,
                        hotkey: hotkey.clone(),
                        alpha_amount: alpha,
                        alpha_price_tao: alpha_price,
                        value_tao: value,
                    });
                }
            }
        }

        Ok(AccountPositions {
            ss58: ss58.to_string(),
            block: current_block,
            total_value_tao: total_value,
            positions,
        })
    }

    async fn get_hotkeys_for_coldkey(&self, coldkey_ss58: &str) -> Result<Vec<String>> {
        let account_id = ss58_to_value(coldkey_ss58)?;

        for storage_name in &["StakingHotkeys", "OwnedHotkeys"] {
            let result = self
                .fetch_thunk(
                    "SubtensorModule",
                    storage_name,
                    vec![account_id.clone()],
                )
                .await;

            if let Ok(Some(thunk)) = result {
                let hotkeys = thunk_to_account_id_list(&thunk);
                if !hotkeys.is_empty() {
                    return Ok(hotkeys);
                }
            }
        }

        Ok(vec![])
    }

    async fn get_alpha_stake(
        &self,
        netuid: u16,
        hotkey: &str,
        coldkey: &str,
    ) -> Result<f64> {
        let val = self
            .fetch_u64(
                "SubtensorModule",
                "Alpha",
                vec![
                    Value::u128(netuid as u128),
                    ss58_to_value(hotkey)?,
                    ss58_to_value(coldkey)?,
                ],
            )
            .await?;
        Ok(val as f64 / 1e9)
    }

    // ── balance ──────────────────────────────────────────────────

    pub async fn get_balance(&self, ss58: &str) -> Result<f64> {
        let result = self
            .fetch_thunk("System", "Account", vec![ss58_to_value(ss58)?])
            .await?;
        if let Some(thunk) = result {
            if let Some(free) = thunk_nested_u128(&thunk, &["data", "free"]) {
                return Ok(free as f64 / 1e9);
            }
        }
        Ok(0.0)
    }

    // ── staking operations ───────────────────────────────────────

    pub async fn stake(
        &self,
        signer: &subxt_signer::sr25519::Keypair,
        hotkey_ss58: &str,
        netuid: u16,
        amount_rao: u64,
    ) -> Result<String> {
        let hotkey_bytes = ss58_to_bytes(hotkey_ss58)?;
        let hotkey_id = subxt::utils::AccountId32::from(
            <[u8; 32]>::try_from(hotkey_bytes.as_slice())
                .context("invalid hotkey bytes")?,
        );

        let tx = subxt::dynamic::tx(
            "SubtensorModule",
            "add_stake",
            vec![
                Value::unnamed_variant("Id", [Value::from_bytes(hotkey_id.0)]),
                Value::u128(netuid as u128),
                Value::u128(amount_rao as u128),
            ],
        );

        let result = self
            .api
            .read()
            .await
            .tx()
            .sign_and_submit_then_watch_default(&tx, signer)
            .await?
            .wait_for_finalized_success()
            .await?;

        Ok(format!("{:?}", result.extrinsic_hash()))
    }

    pub async fn unstake(
        &self,
        signer: &subxt_signer::sr25519::Keypair,
        hotkey_ss58: &str,
        netuid: u16,
        amount_rao: u64,
    ) -> Result<String> {
        let hotkey_bytes = ss58_to_bytes(hotkey_ss58)?;
        let hotkey_id = subxt::utils::AccountId32::from(
            <[u8; 32]>::try_from(hotkey_bytes.as_slice())
                .context("invalid hotkey bytes")?,
        );

        let tx = subxt::dynamic::tx(
            "SubtensorModule",
            "remove_stake",
            vec![
                Value::unnamed_variant("Id", [Value::from_bytes(hotkey_id.0)]),
                Value::u128(netuid as u128),
                Value::u128(amount_rao as u128),
            ],
        );

        let result = self
            .api
            .read()
            .await
            .tx()
            .sign_and_submit_then_watch_default(&tx, signer)
            .await?
            .wait_for_finalized_success()
            .await?;

        Ok(format!("{:?}", result.extrinsic_hash()))
    }

    // ── health ───────────────────────────────────────────────────

    pub async fn health(&self) -> crate::models::HealthResponse {
        match self.get_block().await {
            Ok(block) => crate::models::HealthResponse {
                connected: true,
                network: self.network.clone(),
                block,
                endpoint: None,
                error: None,
            },
            Err(e) => crate::models::HealthResponse {
                connected: false,
                network: self.network.clone(),
                block: 0,
                endpoint: None,
                error: Some(e.to_string()),
            },
        }
    }

    // ── low-level storage ────────────────────────────────────────

    async fn fetch_thunk(
        &self,
        pallet: &str,
        entry: &str,
        keys: Vec<Value>,
    ) -> Result<Option<subxt::dynamic::DecodedValueThunk>> {
        let addr = subxt::dynamic::storage(pallet, entry, keys);
        let storage = self.api.read().await.storage().at_latest().await?;
        Ok(storage.fetch(&addr).await?)
    }

    async fn fetch_u64(
        &self,
        pallet: &str,
        entry: &str,
        keys: Vec<Value>,
    ) -> Result<u64> {
        let result = self.fetch_thunk(pallet, entry, keys).await?;
        Ok(result.and_then(|v| thunk_to_u64(&v)).unwrap_or(0))
    }

    async fn fetch_bool(
        &self,
        pallet: &str,
        entry: &str,
        keys: Vec<Value>,
    ) -> Result<bool> {
        let result = self.fetch_thunk(pallet, entry, keys).await?;
        Ok(result.and_then(|v| thunk_to_bool(&v)).unwrap_or(false))
    }
}

// ── value extraction helpers ─────────────────────────────────────
//
// These work with DecodedValueThunk by calling to_value() which returns
// a scale_value::Value with built-in .as_u128(), .as_bool(), .at() etc.

fn thunk_to_u64(thunk: &subxt::dynamic::DecodedValueThunk) -> Option<u64> {
    let val = DynVal::new(thunk.to_value().ok()?);
    val.as_u128().map(|n| n as u64)
}

fn thunk_to_bool(thunk: &subxt::dynamic::DecodedValueThunk) -> Option<bool> {
    let val = DynVal::new(thunk.to_value().ok()?);
    val.as_bool()
}

fn thunk_field_to_string(
    thunk: &subxt::dynamic::DecodedValueThunk,
    field: &str,
) -> Option<String> {
    let val = DynVal::new(thunk.to_value().ok()?);
    let field_val = val.at(field)?;

    // Try as string directly (byte sequence → UTF-8)
    if let Some(s) = field_val.as_string() {
        return Some(s);
    }

    // Try as bytes
    if let Some(bytes) = field_val.as_bytes() {
        return String::from_utf8(bytes).ok();
    }

    // Try iterating elements as bytes
    let items = field_val.iter_items();
    if !items.is_empty() {
        let bytes: Vec<u8> = items.iter()
            .filter_map(|item: &DynVal| item.as_u128().map(|n| n as u8))
            .collect();
        if !bytes.is_empty() {
            return String::from_utf8(bytes).ok();
        }
    }

    None
}

fn thunk_nested_u128(
    thunk: &subxt::dynamic::DecodedValueThunk,
    path: &[&str],
) -> Option<u128> {
    let val = DynVal::new(thunk.to_value().ok()?);
    let mut current = val;
    for key in path {
        current = current.at(key)?;
    }
    current.as_u128()
}

fn thunk_to_account_id_list(thunk: &subxt::dynamic::DecodedValueThunk) -> Vec<String> {
    use sp_core::crypto::Ss58Codec;

    let val = match thunk.to_value() {
        Ok(v) => DynVal::new(v),
        Err(_) => return vec![],
    };

    let mut result = Vec::new();
    for elem in val.iter_items() {
        // Each element is an AccountId32 — extract 32 bytes
        if let Some(bytes) = elem.as_bytes() {
            if bytes.len() == 32 {
                let arr: [u8; 32] = bytes.try_into().unwrap();
                let public = sp_core::sr25519::Public::from_raw(arr);
                result.push(public.to_ss58check());
            }
        } else {
            // Try iterating sub-elements as individual bytes
            let items = elem.iter_items();
            let bytes: Vec<u8> = items.iter()
                .filter_map(|item: &DynVal| item.as_u128().map(|n| n as u8))
                .collect();
            if bytes.len() == 32 {
                let arr: [u8; 32] = bytes.try_into().unwrap();
                let public = sp_core::sr25519::Public::from_raw(arr);
                result.push(public.to_ss58check());
            }
        }
    }
    result
}

// ── SS58 utilities ───────────────────────────────────────────────

fn ss58_to_value(ss58: &str) -> Result<Value> {
    let bytes = ss58_to_bytes(ss58)?;
    Ok(Value::from_bytes(bytes))
}

fn ss58_to_bytes(ss58: &str) -> Result<Vec<u8>> {
    use sp_core::crypto::Ss58Codec;
    let account = sp_core::sr25519::Public::from_ss58check(ss58)
        .map_err(|e| anyhow::anyhow!("invalid SS58 address: {e}"))?;
    Ok(account.0.to_vec())
}
