//! Wallet-signed module backing — "stake BlocTime to a module" from a browser
//! wallet instead of a server-held key.
//!
//! The chain module already keeps a key-signed allocation ledger
//! (`~/.mod/chain/mod_stakes.json`): a named key on the host says how much of
//! its on-chain BlocTime backs each module. That path can't serve a visitor
//! with MetaMask — there is no key on the box for them, and there never should
//! be. So this module owns the *wallet* half of the same idea:
//!
//!   1. the browser signs a human-readable EIP-191 (`personal_sign`) message
//!      naming the module, the action, the amount and a timestamp,
//!   2. we recover the signer here (k256 + keccak — no RPC, no gas),
//!   3. we ask the bloctime module for that address's live BLOC balance and
//!      refuse any allocation the balance can't cover, counting what the
//!      address already backs on BOTH ledgers,
//!   4. we write our own ledger at `~/.mod/web/mod_backing.json`.
//!
//! Amounts are wei stored as *strings* so nothing rounds: JSON numbers are
//! f64 in Rust and 1 kBLOC already exceeds the exact integer range. Reads of
//! the chain module's ledger arrive over its hub API as JSON numbers, so those
//! are rounded UP when they count against an address's headroom — an epsilon
//! of caution rather than an epsilon of overdraft.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use tiny_keccak::{Hasher, Keccak};

/// wei per BLOC (the token is 18-decimals like ether).
const WEI: u128 = 1_000_000_000_000_000_000;
/// How stale a signed message may be before we reject it.
const MAX_AGE_SECS: u64 = 15 * 60;

/// `{network: {module: {address: wei-as-string}}}`
type Book = BTreeMap<String, BTreeMap<String, BTreeMap<String, String>>>;

/// One address's position in a module's book.
#[derive(Debug, Clone, Serialize)]
pub struct Staker {
    pub address: String,
    /// wei, as a string (exact).
    pub amount: String,
    /// Human BLOC, for display/sorting.
    pub bloc: f64,
    /// "wallet" = signed by a browser wallet here, "key" = the chain module's
    /// server-key ledger.
    pub via: &'static str,
}

/// The merged view of one module's backing.
#[derive(Debug, Clone, Serialize)]
pub struct ModBacking {
    pub name: String,
    pub network: String,
    pub total: String,
    pub total_bloc: f64,
    pub stakers: Vec<Staker>,
    /// Set when the request carried `?address=`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub address: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub my_stake: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub my_stake_bloc: Option<f64>,
    /// BLOC the address holds on-chain (from the bloctime module), wei string.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bloc_balance: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bloc_balance_bloc: Option<f64>,
    /// Balance minus everything already allocated across all modules.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub available: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub available_bloc: Option<f64>,
    /// False when the bloctime module couldn't be reached — the UI then says
    /// so instead of implying the address holds nothing.
    pub balance_available: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct BookTotals {
    pub network: String,
    /// Chain-shaped for the catalog grid: wei as a number is display-only.
    pub mods: BTreeMap<String, ModTotal>,
    pub total: f64,
    /// True when the chain module's key-signed ledger was reachable.
    pub chain_available: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct ModTotal {
    pub total: f64,
    pub stakers: usize,
    /// Wallet-signed share (wei string, exact).
    pub wallet: String,
}

/// What the browser signs and posts.
#[derive(Debug, Deserialize)]
pub struct BackRequest {
    pub name: String,
    /// "stake" | "unstake"
    pub action: String,
    /// Human BLOC as typed ("12.5"), or "all" to withdraw a whole position.
    pub amount: String,
    pub address: String,
    /// Unix seconds, as signed.
    pub time: u64,
    /// 0x… 65-byte `personal_sign` output.
    pub signature: String,
    #[serde(default)]
    pub network: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct BackResult {
    pub name: String,
    pub address: String,
    pub action: String,
    pub my_stake: String,
    pub my_stake_bloc: f64,
    pub total: String,
    pub total_bloc: f64,
    pub bloc_balance: String,
    pub available: String,
    pub available_bloc: f64,
}

pub struct Staking {
    ledger: PathBuf,
    network: String,
    chain_url: String,
    bloctime_url: String,
    http: reqwest::Client,
    /// Serializes read-modify-write of the ledger file.
    write_lock: Mutex<()>,
}

/// Exactly the message the wallet is asked to sign. Human-readable on purpose:
/// what MetaMask shows is what the server verifies, field for field.
pub fn backing_message(
    action: &str,
    module: &str,
    amount: &str,
    network: &str,
    address: &str,
    time: u64,
) -> String {
    format!(
        "mod protocol · back a module\n\
         action: {action}\n\
         module: {module}\n\
         amount: {amount} BLOC\n\
         network: {network}\n\
         address: {}\n\
         time: {time}",
        address.to_lowercase()
    )
}

fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut k = Keccak::v256();
    let mut out = [0u8; 32];
    k.update(data);
    k.finalize(&mut out);
    out
}

/// EIP-191 personal_sign digest.
fn personal_digest(msg: &str) -> [u8; 32] {
    let mut buf = format!("\x19Ethereum Signed Message:\n{}", msg.len()).into_bytes();
    buf.extend_from_slice(msg.as_bytes());
    keccak256(&buf)
}

/// Recover the lowercase 0x address that produced `sig_hex` over `msg`.
pub fn recover_signer(msg: &str, sig_hex: &str) -> Result<String, String> {
    let raw = hex::decode(sig_hex.trim().trim_start_matches("0x"))
        .map_err(|_| "signature is not hex".to_string())?;
    if raw.len() != 65 {
        return Err(format!("signature must be 65 bytes, got {}", raw.len()));
    }
    let sig = Signature::from_slice(&raw[..64]).map_err(|e| format!("bad signature: {e}"))?;
    // MetaMask signs with the legacy v = 27/28; some wallets emit 0/1.
    let v = match raw[64] {
        0 | 27 => 0u8,
        1 | 28 => 1u8,
        other => return Err(format!("unsupported recovery id {other}")),
    };
    let rec = RecoveryId::from_byte(v).ok_or_else(|| "bad recovery id".to_string())?;
    let digest = personal_digest(msg);
    let key = VerifyingKey::recover_from_prehash(&digest, &sig, rec)
        .map_err(|e| format!("could not recover signer: {e}"))?;
    let point = key.to_encoded_point(false);
    // Skip the 0x04 SEC1 tag; the address is the last 20 bytes of the hash.
    let hash = keccak256(&point.as_bytes()[1..]);
    Ok(format!("0x{}", hex::encode(&hash[12..])))
}

/// Parse a human decimal amount ("12.5") into wei without touching floats.
fn to_wei(amount: &str) -> Result<u128, String> {
    let s = amount.trim().replace('_', "");
    if s.is_empty() {
        return Err("amount required".into());
    }
    let (int_part, frac_part) = match s.split_once('.') {
        Some((a, b)) => (a, b),
        None => (s.as_str(), ""),
    };
    if int_part.chars().any(|c| !c.is_ascii_digit())
        || frac_part.chars().any(|c| !c.is_ascii_digit())
    {
        return Err(format!("'{amount}' is not a number"));
    }
    if frac_part.len() > 18 {
        return Err("more than 18 decimals".into());
    }
    let int_v: u128 = if int_part.is_empty() {
        0
    } else {
        int_part.parse().map_err(|_| "amount too large".to_string())?
    };
    let mut frac = frac_part.to_string();
    while frac.len() < 18 {
        frac.push('0');
    }
    let frac_v: u128 = frac.parse().map_err(|_| "amount too large".to_string())?;
    int_v
        .checked_mul(WEI)
        .and_then(|v| v.checked_add(frac_v))
        .ok_or_else(|| "amount too large".to_string())
}

fn bloc(wei: u128) -> f64 {
    wei as f64 / WEI as f64
}

fn parse_wei(s: &str) -> u128 {
    s.trim().parse::<u128>().unwrap_or(0)
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

impl Staking {
    pub fn from_env() -> Self {
        let ledger = std::env::var("MOD_BACKING_LEDGER")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
                PathBuf::from(home).join(".mod/web/mod_backing.json")
            });
        let chain_url =
            std::env::var("CHAIN_API_URL").unwrap_or_else(|_| "http://localhost:8800".to_string());
        let bloctime_url = std::env::var("BLOCTIME_API_URL")
            .unwrap_or_else(|_| "http://localhost:8851".to_string());
        let network = std::env::var("CHAIN_NETWORK").unwrap_or_else(|_| "testnet".to_string());
        Self {
            ledger,
            network,
            chain_url,
            bloctime_url,
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(20))
                .build()
                .unwrap_or_default(),
            write_lock: Mutex::new(()),
        }
    }

    pub fn network(&self) -> &str {
        &self.network
    }

    fn load(&self) -> Book {
        std::fs::read_to_string(&self.ledger)
            .ok()
            .and_then(|s| serde_json::from_str::<Book>(&s).ok())
            .unwrap_or_default()
    }

    fn save(&self, book: &Book) -> Result<(), String> {
        if let Some(dir) = self.ledger.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let tmp = self.ledger.with_extension("json.tmp");
        let body = serde_json::to_string_pretty(book).map_err(|e| e.to_string())?;
        std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, &self.ledger).map_err(|e| e.to_string())
    }

    /// The chain module's key-signed ledger, as `{module: {address: wei}}`.
    /// Values arrive as JSON numbers (f64) — see the module header.
    async fn key_book(&self) -> Option<BTreeMap<String, BTreeMap<String, u128>>> {
        #[derive(Deserialize)]
        struct One {
            #[serde(default)]
            address: String,
            #[serde(default)]
            amount: f64,
        }
        #[derive(Deserialize)]
        struct Detail {
            #[serde(default)]
            stakers: Vec<One>,
        }
        // The hub exposes per-mod detail; the "all" view drops addresses, and
        // we need them to know what an address has already allocated. One call
        // for the totals, then only the mods that have any.
        #[derive(Deserialize)]
        struct AllMods {
            #[serde(default)]
            mods: BTreeMap<String, serde_json::Value>,
        }
        let all: AllMods = self
            .http
            .get(format!(
                "{}/mods/stakes?network={}",
                self.chain_url, self.network
            ))
            .send()
            .await
            .ok()?
            .json()
            .await
            .ok()?;
        let mut out: BTreeMap<String, BTreeMap<String, u128>> = BTreeMap::new();
        for name in all.mods.keys() {
            let detail: Option<Detail> = self
                .http
                .get(format!(
                    "{}/mods/stakes/{}?network={}",
                    self.chain_url, name, self.network
                ))
                .send()
                .await
                .ok()?
                .json()
                .await
                .ok();
            if let Some(d) = detail {
                let entry = out.entry(name.clone()).or_default();
                for s in d.stakers {
                    if s.address.is_empty() || s.amount <= 0.0 {
                        continue;
                    }
                    // Round UP: this counts against headroom, never for it.
                    entry.insert(s.address.to_lowercase(), s.amount.ceil() as u128);
                }
            }
        }
        Some(out)
    }

    /// An address's live BLOC balance (wei) from the bloctime module.
    async fn bloc_balance(&self, address: &str) -> Option<u128> {
        #[derive(Deserialize)]
        struct Wrap {
            result: Inner,
        }
        #[derive(Deserialize)]
        struct Inner {
            #[serde(default)]
            #[serde(rename = "blocBalance")]
            bloc_balance: String,
            #[serde(default)]
            #[serde(rename = "totalBlocTime")]
            total_bloctime: String,
        }
        let wrap: Wrap = self
            .http
            .post(format!("{}/overview", self.bloctime_url))
            .json(&serde_json::json!({ "address": address }))
            .send()
            .await
            .ok()?
            .json()
            .await
            .ok()?;
        // BLOC is minted 1:1 with the staked position's weight; prefer the
        // ERC-20 balance and fall back to the summed position weight when the
        // deployed build predates `balanceOf` reporting.
        let bal = parse_wei(&wrap.result.bloc_balance);
        Some(if bal > 0 {
            bal
        } else {
            parse_wei(&wrap.result.total_bloctime)
        })
    }

    /// Total wei an address has allocated across every module, both ledgers.
    fn allocated(
        &self,
        wallet: &BTreeMap<String, BTreeMap<String, String>>,
        keys: Option<&BTreeMap<String, BTreeMap<String, u128>>>,
        address: &str,
    ) -> u128 {
        let a = address.to_lowercase();
        let mine: u128 = wallet
            .values()
            .map(|stakers| parse_wei(stakers.get(&a).map(|s| s.as_str()).unwrap_or("0")))
            .sum();
        let theirs: u128 = keys
            .map(|k| {
                k.values()
                    .map(|stakers| stakers.get(&a).copied().unwrap_or(0))
                    .sum()
            })
            .unwrap_or(0);
        mine.saturating_add(theirs)
    }

    /// Catalog-wide totals: the chain module's key ledger plus ours.
    pub async fn totals(&self) -> BookTotals {
        let book = self.load();
        let wallet = book.get(&self.network).cloned().unwrap_or_default();
        let keys = self.key_book().await;
        let mut mods: BTreeMap<String, ModTotal> = BTreeMap::new();
        for (name, stakers) in &wallet {
            let sum: u128 = stakers.values().map(|v| parse_wei(v)).sum();
            if sum == 0 {
                continue;
            }
            // Totals ride out in wei to match the chain module's shape (the
            // grid divides by 1e18 for display).
            mods.insert(
                name.clone(),
                ModTotal {
                    total: sum as f64,
                    stakers: stakers.values().filter(|v| parse_wei(v) > 0).count(),
                    wallet: sum.to_string(),
                },
            );
        }
        if let Some(keys) = &keys {
            for (name, stakers) in keys {
                let sum: u128 = stakers.values().copied().sum();
                if sum == 0 {
                    continue;
                }
                let entry = mods.entry(name.clone()).or_insert(ModTotal {
                    total: 0.0,
                    stakers: 0,
                    wallet: "0".into(),
                });
                entry.total += sum as f64;
                entry.stakers += stakers.values().filter(|v| **v > 0).count();
            }
        }
        let total = mods.values().map(|m| m.total).sum();
        BookTotals {
            network: self.network.clone(),
            mods,
            total,
            chain_available: keys.is_some(),
        }
    }

    /// One module's book, optionally with `address`'s position and headroom.
    pub async fn module(&self, name: &str, address: Option<&str>) -> ModBacking {
        let name = name.trim().to_lowercase();
        let book = self.load();
        let wallet = book.get(&self.network).cloned().unwrap_or_default();
        let keys = self.key_book().await;

        let mut stakers: Vec<Staker> = Vec::new();
        let mut total: u128 = 0;
        if let Some(entry) = wallet.get(&name) {
            for (addr, amt) in entry {
                let v = parse_wei(amt);
                if v == 0 {
                    continue;
                }
                total = total.saturating_add(v);
                stakers.push(Staker {
                    address: addr.clone(),
                    amount: v.to_string(),
                    bloc: bloc(v),
                    via: "wallet",
                });
            }
        }
        if let Some(k) = keys.as_ref().and_then(|k| k.get(&name)) {
            for (addr, v) in k {
                if *v == 0 {
                    continue;
                }
                total = total.saturating_add(*v);
                stakers.push(Staker {
                    address: addr.clone(),
                    amount: v.to_string(),
                    bloc: bloc(*v),
                    via: "key",
                });
            }
        }
        stakers.sort_by(|a, b| parse_wei(&b.amount).cmp(&parse_wei(&a.amount)));

        let mut out = ModBacking {
            name: name.clone(),
            network: self.network.clone(),
            total: total.to_string(),
            total_bloc: bloc(total),
            stakers,
            address: None,
            my_stake: None,
            my_stake_bloc: None,
            bloc_balance: None,
            bloc_balance_bloc: None,
            available: None,
            available_bloc: None,
            balance_available: false,
        };

        if let Some(addr) = address {
            let a = addr.trim().to_lowercase();
            if a.is_empty() {
                return out;
            }
            let mine = parse_wei(
                wallet
                    .get(&name)
                    .and_then(|e| e.get(&a))
                    .map(|s| s.as_str())
                    .unwrap_or("0"),
            );
            let balance = self.bloc_balance(&a).await;
            let allocated = self.allocated(&wallet, keys.as_ref(), &a);
            out.address = Some(a);
            out.my_stake = Some(mine.to_string());
            out.my_stake_bloc = Some(bloc(mine));
            if let Some(b) = balance {
                let free = b.saturating_sub(allocated);
                out.bloc_balance = Some(b.to_string());
                out.bloc_balance_bloc = Some(bloc(b));
                out.available = Some(free.to_string());
                out.available_bloc = Some(bloc(free));
                out.balance_available = true;
            }
        }
        out
    }

    /// Verify a signed stake/unstake and apply it to the wallet ledger.
    pub async fn apply(&self, req: &BackRequest) -> Result<BackResult, (u16, String)> {
        let bad = |m: String| (400u16, m);
        let name = req.name.trim().to_lowercase();
        if name.is_empty() {
            return Err(bad("module name required".into()));
        }
        let action = req.action.trim().to_lowercase();
        if action != "stake" && action != "unstake" {
            return Err(bad("action must be 'stake' or 'unstake'".into()));
        }
        let network = req.network.clone().unwrap_or_else(|| self.network.clone());
        if network != self.network {
            return Err(bad(format!(
                "this deployment tracks '{}', not '{network}'",
                self.network
            )));
        }
        let address = req.address.trim().to_lowercase();
        if !address.starts_with("0x") || address.len() != 42 {
            return Err(bad("address must be a 0x… EOA".into()));
        }
        let age = now().saturating_sub(req.time);
        if req.time > now() + 120 || age > MAX_AGE_SECS {
            return Err(bad(
                "signature expired — sign again (messages are good for 15 minutes)".into(),
            ));
        }

        let msg = backing_message(&action, &name, req.amount.trim(), &network, &address, req.time);
        let signer = recover_signer(&msg, &req.signature).map_err(|e| (400u16, e))?;
        if signer != address {
            return Err((
                401,
                format!("signature is from {signer}, not {address} — reconnect the wallet"),
            ));
        }

        let all = req.amount.trim().eq_ignore_ascii_case("all");
        let amount_wei = if all { 0 } else { to_wei(&req.amount).map_err(bad)? };
        if !all && amount_wei == 0 {
            return Err(bad("amount must be greater than zero".into()));
        }

        let balance = self.bloc_balance(&address).await;

        let _guard = self.write_lock.lock().await;
        let mut book = self.load();
        let wallet = book.entry(self.network.clone()).or_default();
        let current = parse_wei(
            wallet
                .get(&name)
                .and_then(|e| e.get(&address))
                .map(|s| s.as_str())
                .unwrap_or("0"),
        );

        let keys = self.key_book().await;
        let allocated = self.allocated(wallet, keys.as_ref(), &address);

        let new_amount = if action == "stake" {
            let balance = balance.ok_or((
                503u16,
                "can't reach the bloctime module to read your BLOC balance — try again in a moment"
                    .to_string(),
            ))?;
            let free = balance.saturating_sub(allocated);
            if amount_wei > free {
                return Err(bad(format!(
                    "not enough free BlocTime: you hold {:.4} BLOC and already back modules with {:.4} — {:.4} free. Stake NAT in the bloctime protocol to mint more.",
                    bloc(balance),
                    bloc(allocated),
                    bloc(free)
                )));
            }
            current.saturating_add(amount_wei)
        } else {
            if current == 0 {
                return Err(bad(format!("you have no wallet-signed stake on '{name}'")));
            }
            let take = if all { current } else { amount_wei.min(current) };
            current - take
        };

        let entry = wallet.entry(name.clone()).or_default();
        if new_amount > 0 {
            entry.insert(address.clone(), new_amount.to_string());
        } else {
            entry.remove(&address);
        }
        let is_empty = entry.is_empty();
        if is_empty {
            wallet.remove(&name);
        }
        self.save(&book).map_err(|e| (500u16, e))?;

        // Totals after the write: our ledger plus the key ledger.
        let wallet = book.get(&self.network).cloned().unwrap_or_default();
        let mut total: u128 = wallet
            .get(&name)
            .map(|e| e.values().map(|v| parse_wei(v)).sum())
            .unwrap_or(0);
        if let Some(k) = keys.as_ref().and_then(|k| k.get(&name)) {
            total = total.saturating_add(k.values().copied().sum());
        }
        let allocated = self.allocated(&wallet, keys.as_ref(), &address);
        let balance = balance.unwrap_or(0);
        let free = balance.saturating_sub(allocated);

        Ok(BackResult {
            name,
            address,
            action,
            my_stake: new_amount.to_string(),
            my_stake_bloc: bloc(new_amount),
            total: total.to_string(),
            total_bloc: bloc(total),
            bloc_balance: balance.to_string(),
            available: free.to_string(),
            available_bloc: bloc(free),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wei_parsing_is_exact() {
        assert_eq!(to_wei("1").unwrap(), WEI);
        assert_eq!(to_wei("0.5").unwrap(), WEI / 2);
        assert_eq!(to_wei("1625").unwrap(), 1625 * WEI);
        assert_eq!(to_wei("0.000000000000000001").unwrap(), 1);
        assert!(to_wei("nope").is_err());
        assert!(to_wei("0.0000000000000000001").is_err());
    }

    /// Fixture produced by `eth_account` (the same library the rest of the
    /// fleet verifies with) over the exact message `backing_message` builds —
    /// including the non-ASCII `·`, which must be counted in BYTES by the
    /// EIP-191 length prefix.
    const FIXTURE_ADDR: &str = "0x19e7e376e7c213b7e7e7e46cc70a5dd086daff2a";
    const FIXTURE_SIG: &str = "0x373be41ef911f34ec035c12ee22bf17b83dd46da3d3f864b8d2a2e28231dc5db07974415ad50c4b71f8712e65681c75aa088384957b7c77b2d4fa0b79f9cf82b1b";

    #[test]
    fn recovers_a_real_personal_sign() {
        let msg = backing_message("stake", "web", "12.5", "testnet", FIXTURE_ADDR, 1700000000);
        assert_eq!(recover_signer(&msg, FIXTURE_SIG).unwrap(), FIXTURE_ADDR);
    }

    #[test]
    fn rejects_a_tampered_message() {
        // Same signature, different amount → a different signer, never a panic.
        let msg = backing_message("stake", "web", "125", "testnet", FIXTURE_ADDR, 1700000000);
        assert_ne!(recover_signer(&msg, FIXTURE_SIG).unwrap(), FIXTURE_ADDR);
        assert!(recover_signer(&msg, "0xdeadbeef").is_err());
    }

    #[test]
    fn message_is_stable() {
        let m = backing_message("stake", "web", "12.5", "testnet", "0xABC", 1700000000);
        assert!(m.contains("action: stake"));
        assert!(m.contains("module: web"));
        assert!(m.contains("amount: 12.5 BLOC"));
        assert!(m.contains("address: 0xabc"));
        assert!(m.ends_with("time: 1700000000"));
    }
}
