//! Credit system — per-key deposit wallets that auto-forward to the owner.
//!
//! Every authenticated key (address) gets its own deposit wallet, generated
//! on the backend from a master seed kept off-tree in ~/.mod/{module}/credits/.
//! When native coin lands on a deposit wallet it is swept to the module
//! owner's address and the swept amount is credited to the key's ledger —
//! the balance backs future subscriptions / pay-per-use.
//!
//! Module-agnostic by design: the module name comes from CREDITS_MODULE /
//! MOD_NAME (default "build"), all state lives under ~/.mod/{module}/credits/,
//! and the chain + payout destination are read from that module's config.json
//! (`credits` section + `owner` field). A fork of this server (codex, …) gets
//! the whole system by setting the env var — no code changes.
//!
//! State files (off-repo, like whitelist.json / grants.json):
//!   ~/.mod/{module}/credits/seed.hex    — 32-byte master seed (hex)
//!   ~/.mod/{module}/credits/ledger.json — accounts, deposits, spends

use k256::ecdsa::SigningKey;
use serde::{Deserialize, Serialize};
use sha3::Digest;
use std::collections::HashMap;
use std::sync::Mutex;

/// Serializes ledger read-modify-write cycles within this process.
static LEDGER_LOCK: Mutex<()> = Mutex::new(());

// ── Module identity + paths ──────────────────────────────────────────

/// Which module this credit system serves. Forks adapt by setting
/// CREDITS_MODULE (or MOD_NAME) — state dir, config and owner all follow.
pub fn module_name() -> String {
    std::env::var("CREDITS_MODULE")
        .or_else(|_| std::env::var("MOD_NAME"))
        .ok()
        .map(|s| s.trim().to_lowercase())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "build".to_string())
}

fn credits_dir() -> Option<std::path::PathBuf> {
    Some(dirs::home_dir()?.join(".mod").join(module_name()).join("credits"))
}

/// Locate the module's config.json: walk up from the binary (works for any
/// fork running its own build), then the standard orbit/ and core/ locations.
fn module_config_path() -> Option<std::path::PathBuf> {
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(|d| d.to_path_buf());
        for _ in 0..6 {
            match dir {
                Some(d) => {
                    let candidate = d.join("config.json");
                    if candidate.exists() {
                        return Some(candidate);
                    }
                    dir = d.parent().map(|p| p.to_path_buf());
                }
                None => break,
            }
        }
    }
    let home = dirs::home_dir()?;
    let name = module_name();
    for base in ["mod/mod/orbit", "mod/mod/core"] {
        let candidate = home.join(base).join(&name).join("config.json");
        if candidate.exists() {
            return Some(candidate);
        }
    }
    None
}

fn read_module_config() -> serde_json::Value {
    module_config_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|c| serde_json::from_str(&c).ok())
        .unwrap_or_else(|| serde_json::json!({}))
}

// ── Chain config ─────────────────────────────────────────────────────

#[derive(Serialize, Clone)]
pub struct ChainCfg {
    pub enabled: bool,
    pub rpc: String,
    pub chain_id: u64,
    pub symbol: String,
    /// Don't sweep below this (wei); 0 ⇒ sweep whenever the balance clears gas.
    pub min_sweep_wei: u128,
}

/// Chain settings — module config.json `credits` section, env overrides on top.
/// Defaults match the wallet UI: Polygon mainnet / MATIC.
pub fn chain_cfg() -> ChainCfg {
    let cfg = read_module_config();
    let c = cfg.get("credits").cloned().unwrap_or_else(|| serde_json::json!({}));
    let mut out = ChainCfg {
        enabled: c.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
        rpc: c
            .get("rpc")
            .and_then(|v| v.as_str())
            .unwrap_or("https://polygon-rpc.com")
            .to_string(),
        chain_id: c.get("chain_id").and_then(|v| v.as_u64()).unwrap_or(137),
        symbol: c
            .get("symbol")
            .and_then(|v| v.as_str())
            .unwrap_or("MATIC")
            .to_string(),
        min_sweep_wei: c
            .get("min_sweep_wei")
            .and_then(|v| v.as_str())
            .and_then(|s| s.parse::<u128>().ok())
            .unwrap_or(0),
    };
    if let Ok(rpc) = std::env::var("CREDITS_RPC") {
        if !rpc.trim().is_empty() {
            out.rpc = rpc.trim().to_string();
        }
    }
    if let Some(id) = std::env::var("CREDITS_CHAIN_ID").ok().and_then(|v| v.parse().ok()) {
        out.chain_id = id;
    }
    out
}

/// Where sweeps land: `credits.payout` override, else the module `owner`,
/// else the runtime owner (~/.mod/{module}/owner.json).
pub fn payout_address() -> Option<String> {
    let cfg = read_module_config();
    let payout = cfg
        .get("credits")
        .and_then(|c| c.get("payout"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_lowercase())
        .filter(|s| s.starts_with("0x") && s.len() == 42);
    if payout.is_some() {
        return payout;
    }
    if let Some(owner) = cfg
        .get("owner")
        .and_then(|v| v.as_str())
        .map(|s| s.to_lowercase())
        .filter(|s| s.starts_with("0x") && s.len() == 42)
    {
        return Some(owner);
    }
    // Runtime owner file (first sign-in claims ownership)
    let path = dirs::home_dir()?
        .join(".mod")
        .join(module_name())
        .join("owner.json");
    let content = std::fs::read_to_string(path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&content).ok()?;
    data.get("owner")
        .and_then(|v| v.as_str())
        .map(|s| s.to_lowercase())
}

// ── Deposit wallet derivation ────────────────────────────────────────

fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut h = sha3::Keccak256::new();
    h.update(data);
    h.finalize().into()
}

/// Load (or create once) the 32-byte master seed. Owner-readable only.
fn master_seed() -> Result<[u8; 32], String> {
    let dir = credits_dir().ok_or("no home dir")?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("mkdir: {}", e))?;
    let path = dir.join("seed.hex");
    if let Ok(content) = std::fs::read_to_string(&path) {
        let bytes = hex::decode(content.trim()).map_err(|e| format!("seed decode: {}", e))?;
        let arr: [u8; 32] = bytes
            .try_into()
            .map_err(|_| "seed must be 32 bytes".to_string())?;
        return Ok(arr);
    }
    use rand::RngCore;
    let mut seed = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut seed);
    std::fs::write(&path, hex::encode(seed)).map_err(|e| format!("seed write: {}", e))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(seed)
}

/// Deterministic per-key deposit wallet: privkey = keccak(domain|module|identity|seed).
/// Deterministic ⇒ recoverable from the seed alone; nothing per-wallet to store.
fn deposit_key(identity: &str) -> Result<SigningKey, String> {
    let seed = master_seed()?;
    let ident = identity.trim().to_lowercase();
    let mut counter: u8 = 0;
    loop {
        let mut material = Vec::new();
        material.extend_from_slice(b"mod-credits-v1|");
        material.extend_from_slice(module_name().as_bytes());
        material.extend_from_slice(b"|");
        material.extend_from_slice(ident.as_bytes());
        material.extend_from_slice(b"|");
        material.extend_from_slice(&seed);
        material.push(counter);
        let candidate = keccak256(&material);
        if let Ok(key) = SigningKey::from_slice(&candidate) {
            return Ok(key);
        }
        counter = counter.checked_add(1).ok_or("key derivation failed")?;
    }
}

fn eth_address(key: &SigningKey) -> String {
    let point = key.verifying_key().to_encoded_point(false);
    let hash = keccak256(&point.as_bytes()[1..]);
    format!("0x{}", hex::encode(&hash[12..]))
}

/// Public deposit address for an identity (no key material leaves this module).
pub fn deposit_address(identity: &str) -> Result<String, String> {
    Ok(eth_address(&deposit_key(identity)?))
}

// ── Ledger ───────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone)]
pub struct DepositEntry {
    /// Amount credited (wei, decimal string — u128 doesn't survive JS numbers).
    pub wei: String,
    /// Sweep transaction hash on the configured chain.
    pub tx: String,
    pub ts: i64,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct SpendEntry {
    pub wei: String,
    pub reason: String,
    pub ts: i64,
}

#[derive(Serialize, Deserialize, Clone, Default)]
pub struct Account {
    #[serde(default)]
    pub deposit_address: String,
    #[serde(default)]
    pub deposits: Vec<DepositEntry>,
    #[serde(default)]
    pub spends: Vec<SpendEntry>,
}

impl Account {
    pub fn credited_wei(&self) -> u128 {
        self.deposits
            .iter()
            .filter_map(|d| d.wei.parse::<u128>().ok())
            .sum()
    }
    pub fn spent_wei(&self) -> u128 {
        self.spends
            .iter()
            .filter_map(|s| s.wei.parse::<u128>().ok())
            .sum()
    }
    pub fn balance_wei(&self) -> u128 {
        self.credited_wei().saturating_sub(self.spent_wei())
    }
}

#[derive(Serialize, Deserialize, Default)]
pub struct Ledger {
    #[serde(default)]
    pub accounts: HashMap<String, Account>,
}

fn ledger_path() -> Option<std::path::PathBuf> {
    Some(credits_dir()?.join("ledger.json"))
}

pub fn read_ledger() -> Ledger {
    ledger_path()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|c| serde_json::from_str(&c).ok())
        .unwrap_or_default()
}

fn write_ledger(ledger: &Ledger) -> Result<(), String> {
    let path = ledger_path().ok_or("no home dir")?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir: {}", e))?;
    }
    let json = serde_json::to_string_pretty(ledger).map_err(|e| format!("encode: {}", e))?;
    std::fs::write(&path, json).map_err(|e| format!("write: {}", e))
}

/// Ensure an account exists for `identity` (registers its deposit address so
/// the background watcher starts polling it). Returns the account.
pub fn ensure_account(identity: &str) -> Result<Account, String> {
    let ident = identity.trim().to_lowercase();
    let addr = deposit_address(&ident)?;
    let _guard = LEDGER_LOCK.lock().unwrap();
    let mut ledger = read_ledger();
    let account = ledger.accounts.entry(ident).or_default();
    if account.deposit_address != addr {
        account.deposit_address = addr;
    }
    let out = account.clone();
    write_ledger(&ledger)?;
    Ok(out)
}

fn credit(identity: &str, wei: u128, tx: &str) -> Result<Account, String> {
    let ident = identity.trim().to_lowercase();
    let _guard = LEDGER_LOCK.lock().unwrap();
    let mut ledger = read_ledger();
    let account = ledger.accounts.entry(ident.clone()).or_default();
    account.deposits.push(DepositEntry {
        wei: wei.to_string(),
        tx: tx.to_string(),
        ts: chrono::Utc::now().timestamp(),
    });
    let out = account.clone();
    write_ledger(&ledger)?;
    println!("✓ credits: {} credited {} wei (sweep {})", ident, wei, tx);
    Ok(out)
}

/// Spend credits (subscription / pay-per-use hook). Errors if the balance is short.
pub fn debit(identity: &str, wei: u128, reason: &str) -> Result<Account, String> {
    let ident = identity.trim().to_lowercase();
    let _guard = LEDGER_LOCK.lock().unwrap();
    let mut ledger = read_ledger();
    let account = ledger.accounts.entry(ident.clone()).or_default();
    let balance = account.balance_wei();
    if balance < wei {
        return Err(format!(
            "insufficient credits: balance {} wei, debit {} wei",
            balance, wei
        ));
    }
    account.spends.push(SpendEntry {
        wei: wei.to_string(),
        reason: reason.to_string(),
        ts: chrono::Utc::now().timestamp(),
    });
    let out = account.clone();
    write_ledger(&ledger)?;
    Ok(out)
}

// ── JSON-RPC ─────────────────────────────────────────────────────────

async fn rpc_call(
    rpc: &str,
    method: &str,
    params: serde_json::Value,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let body = serde_json::json!({ "jsonrpc": "2.0", "id": 1, "method": method, "params": params });
    let resp = client
        .post(rpc)
        .json(&body)
        .timeout(std::time::Duration::from_secs(15))
        .send()
        .await
        .map_err(|e| format!("rpc {}: {}", method, e))?;
    let data: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("rpc {} decode: {}", method, e))?;
    if let Some(err) = data.get("error") {
        return Err(format!("rpc {}: {}", method, err));
    }
    data.get("result")
        .cloned()
        .ok_or_else(|| format!("rpc {}: empty result", method))
}

fn parse_hex_u128(v: &serde_json::Value) -> Result<u128, String> {
    let s = v.as_str().ok_or("expected hex string")?;
    u128::from_str_radix(s.trim_start_matches("0x"), 16).map_err(|e| format!("hex: {}", e))
}

async fn get_balance(rpc: &str, address: &str) -> Result<u128, String> {
    let result = rpc_call(rpc, "eth_getBalance", serde_json::json!([address, "latest"])).await?;
    parse_hex_u128(&result)
}

// ── Legacy tx signing (EIP-155) ──────────────────────────────────────

fn rlp_encode_bytes(data: &[u8]) -> Vec<u8> {
    if data.len() == 1 && data[0] < 0x80 {
        return data.to_vec();
    }
    let mut out = Vec::new();
    if data.len() <= 55 {
        out.push(0x80 + data.len() as u8);
    } else {
        let len_bytes = int_to_be(data.len() as u128);
        out.push(0xb7 + len_bytes.len() as u8);
        out.extend_from_slice(&len_bytes);
    }
    out.extend_from_slice(data);
    out
}

fn rlp_encode_list(items: &[Vec<u8>]) -> Vec<u8> {
    let payload: Vec<u8> = items.iter().flatten().copied().collect();
    let mut out = Vec::new();
    if payload.len() <= 55 {
        out.push(0xc0 + payload.len() as u8);
    } else {
        let len_bytes = int_to_be(payload.len() as u128);
        out.push(0xf7 + len_bytes.len() as u8);
        out.extend_from_slice(&len_bytes);
    }
    out.extend_from_slice(&payload);
    out
}

/// Minimal big-endian bytes — empty for zero (RLP integer convention).
fn int_to_be(value: u128) -> Vec<u8> {
    let bytes = value.to_be_bytes();
    let first = bytes.iter().position(|b| *b != 0).unwrap_or(bytes.len());
    bytes[first..].to_vec()
}

fn rlp_int(value: u128) -> Vec<u8> {
    rlp_encode_bytes(&int_to_be(value))
}

/// Build + sign a 21000-gas native transfer, EIP-155 replay-protected.
fn sign_transfer(
    key: &SigningKey,
    nonce: u128,
    gas_price: u128,
    to: &str,
    value: u128,
    chain_id: u64,
) -> Result<String, String> {
    let to_bytes = hex::decode(to.trim_start_matches("0x")).map_err(|e| format!("to: {}", e))?;
    if to_bytes.len() != 20 {
        return Err("to must be a 20-byte address".to_string());
    }
    let gas_limit: u128 = 21000;

    let unsigned = rlp_encode_list(&[
        rlp_int(nonce),
        rlp_int(gas_price),
        rlp_int(gas_limit),
        rlp_encode_bytes(&to_bytes),
        rlp_int(value),
        rlp_encode_bytes(&[]),
        rlp_int(chain_id as u128),
        rlp_int(0),
        rlp_int(0),
    ]);
    let hash = keccak256(&unsigned);

    let (sig, recid) = key
        .sign_prehash_recoverable(&hash)
        .map_err(|e| format!("sign: {}", e))?;
    let sig_bytes = sig.to_bytes();
    let r = &sig_bytes[..32];
    let s = &sig_bytes[32..];
    let v = chain_id as u128 * 2 + 35 + recid.to_byte() as u128;

    let signed = rlp_encode_list(&[
        rlp_int(nonce),
        rlp_int(gas_price),
        rlp_int(gas_limit),
        rlp_encode_bytes(&to_bytes),
        rlp_int(value),
        rlp_encode_bytes(&[]),
        rlp_int(v),
        rlp_encode_bytes(&trim_leading_zeros(r)),
        rlp_encode_bytes(&trim_leading_zeros(s)),
    ]);
    Ok(format!("0x{}", hex::encode(signed)))
}

fn trim_leading_zeros(data: &[u8]) -> Vec<u8> {
    let first = data.iter().position(|b| *b != 0).unwrap_or(data.len());
    data[first..].to_vec()
}

// ── Sweep ────────────────────────────────────────────────────────────

#[derive(Serialize)]
pub struct SweepResult {
    pub swept: bool,
    /// Live deposit-wallet balance after the attempt (wei, decimal string).
    pub pending_wei: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub swept_wei: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tx: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

/// Check `identity`'s deposit wallet and, if funded past gas + threshold,
/// forward everything (minus gas) to the owner and credit the ledger.
pub async fn sweep(identity: &str) -> Result<SweepResult, String> {
    let cfg = chain_cfg();
    if !cfg.enabled {
        return Err("credits disabled in config".to_string());
    }
    let owner = payout_address().ok_or("no owner configured — nowhere to forward deposits")?;
    let key = deposit_key(identity)?;
    let from = eth_address(&key);

    let balance = get_balance(&cfg.rpc, &from).await?;
    if balance == 0 {
        return Ok(SweepResult {
            swept: false,
            pending_wei: "0".to_string(),
            swept_wei: None,
            tx: None,
            note: None,
        });
    }

    // 20% gas-price headroom so the sweep lands promptly.
    let gas_price = parse_hex_u128(&rpc_call(&cfg.rpc, "eth_gasPrice", serde_json::json!([])).await?)?
        .saturating_mul(120)
        / 100;
    let fee = gas_price.saturating_mul(21000);
    let threshold = fee.saturating_mul(2).max(cfg.min_sweep_wei);
    if balance <= threshold {
        return Ok(SweepResult {
            swept: false,
            pending_wei: balance.to_string(),
            swept_wei: None,
            tx: None,
            note: Some(format!(
                "deposit below sweep threshold ({} wei) — send more to credit",
                threshold
            )),
        });
    }

    let nonce = parse_hex_u128(
        &rpc_call(&cfg.rpc, "eth_getTransactionCount", serde_json::json!([from, "pending"])).await?,
    )?;
    let value = balance - fee;
    let raw = sign_transfer(&key, nonce, gas_price, &owner, value, cfg.chain_id)?;
    let tx = rpc_call(&cfg.rpc, "eth_sendRawTransaction", serde_json::json!([raw]))
        .await?
        .as_str()
        .unwrap_or_default()
        .to_string();
    if tx.is_empty() {
        return Err("sweep broadcast returned no tx hash".to_string());
    }

    credit(identity, value, &tx)?;
    let remaining = get_balance(&cfg.rpc, &from).await.unwrap_or(0);
    Ok(SweepResult {
        swept: true,
        pending_wei: remaining.to_string(),
        swept_wei: Some(value.to_string()),
        tx: Some(tx),
        note: None,
    })
}

/// Live deposit-wallet balance (wei) — what's arrived but not yet swept.
pub async fn pending_wei(identity: &str) -> Result<u128, String> {
    let cfg = chain_cfg();
    if !cfg.enabled {
        return Ok(0);
    }
    let addr = deposit_address(identity)?;
    get_balance(&cfg.rpc, &addr).await
}

/// Background watcher: every 60s, sweep any registered deposit wallet that
/// has received funds. Accounts register on first GET /credits.
pub fn spawn_watcher() {
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(60)).await;
            let cfg = chain_cfg();
            if !cfg.enabled || payout_address().is_none() {
                continue;
            }
            let identities: Vec<String> = read_ledger().accounts.keys().cloned().collect();
            for identity in identities {
                match sweep(&identity).await {
                    Ok(r) if r.swept => {
                        println!(
                            "✓ credits watcher: swept {} wei for {} (tx {})",
                            r.swept_wei.unwrap_or_default(),
                            identity,
                            r.tx.unwrap_or_default()
                        );
                    }
                    Ok(_) => {}
                    Err(e) => eprintln!("credits watcher: {} — {}", identity, e),
                }
            }
        }
    });
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rlp_int_zero_is_empty_string() {
        assert_eq!(rlp_int(0), vec![0x80]);
    }

    #[test]
    fn test_rlp_single_low_byte_is_itself() {
        assert_eq!(rlp_int(0x7f), vec![0x7f]);
        assert_eq!(rlp_int(0x80), vec![0x81, 0x80]);
    }

    #[test]
    fn test_rlp_known_vectors() {
        // "dog" → [0x83, 'd', 'o', 'g']
        assert_eq!(rlp_encode_bytes(b"dog"), vec![0x83, b'd', b'o', b'g']);
        // empty list → 0xc0
        assert_eq!(rlp_encode_list(&[]), vec![0xc0]);
        // ["cat","dog"] → [0xc8, 0x83, c,a,t, 0x83, d,o,g]
        let list = rlp_encode_list(&[rlp_encode_bytes(b"cat"), rlp_encode_bytes(b"dog")]);
        assert_eq!(list, vec![0xc8, 0x83, b'c', b'a', b't', 0x83, b'd', b'o', b'g']);
        // 56-byte string gets the long-form 0xb8 prefix
        let long = vec![0xaa; 56];
        let enc = rlp_encode_bytes(&long);
        assert_eq!(&enc[..2], &[0xb8, 56]);
        assert_eq!(enc.len(), 58);
    }

    #[test]
    fn test_signed_transfer_parses_as_65_plus_payload() {
        let key = SigningKey::from_slice(&[7u8; 32]).unwrap();
        let raw = sign_transfer(
            &key,
            0,
            30_000_000_000,
            "0xd779eb61ced815570f74ab15a52ee8378a66996f",
            1_000_000_000_000_000_000,
            137,
        )
        .unwrap();
        assert!(raw.starts_with("0x"));
        let bytes = hex::decode(raw.trim_start_matches("0x")).unwrap();
        // Legacy signed tx is an RLP list
        assert!(bytes[0] >= 0xc0);
    }

    #[test]
    fn test_deposit_derivation_deterministic_and_distinct() {
        // Point HOME at a temp dir so the test seeds its own store. HOME is
        // process-global — take the shared lock so sudo's tests don't race us.
        let _g = crate::home_test_lock();
        let tmp = std::env::temp_dir().join(format!("credits-test-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let prev = std::env::var("HOME").ok();
        std::env::set_var("HOME", &tmp);

        let a1 = deposit_address("0xAAAA000000000000000000000000000000000001").unwrap();
        let a2 = deposit_address("0xaaaa000000000000000000000000000000000001").unwrap();
        let b = deposit_address("0xbbbb000000000000000000000000000000000002").unwrap();
        assert_eq!(a1, a2, "derivation must be case-insensitive deterministic");
        assert_ne!(a1, b, "different identities get different wallets");
        assert!(a1.starts_with("0x") && a1.len() == 42);

        if let Some(h) = prev {
            std::env::set_var("HOME", h);
        }
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_ledger_credit_debit_math() {
        let mut account = Account::default();
        account.deposits.push(DepositEntry {
            wei: "1000".to_string(),
            tx: "0x1".to_string(),
            ts: 0,
        });
        account.deposits.push(DepositEntry {
            wei: "500".to_string(),
            tx: "0x2".to_string(),
            ts: 0,
        });
        account.spends.push(SpendEntry {
            wei: "300".to_string(),
            reason: "job".to_string(),
            ts: 0,
        });
        assert_eq!(account.credited_wei(), 1500);
        assert_eq!(account.spent_wei(), 300);
        assert_eq!(account.balance_wei(), 1200);
    }
}
