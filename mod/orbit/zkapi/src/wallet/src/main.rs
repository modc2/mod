//! zkapi-wallet: a persistent driver around the upstream zkAPI v2 wallet core.
//!
//! The cryptography is entirely `zkapi_browser` / `zkapi_proof` from the pinned
//! vbuterin/zkapi checkout (the same code the browser SDK compiles to WASM).
//! This binary only adds what the browser host normally provides: storage and
//! a write-ahead journal. Every mutation is written to disk (atomic rename,
//! mode 0600) before the reply leaves the process, so a crash between "prepare"
//! and "send" can always be recovered with the exact saved request.
//!
//! Protocol: one JSON command per stdin line, one JSON reply per stdout line:
//!   {"id": 1, "command": "status", "wallet": "alice"}
//!   {"id": 1, "ok": true, "result": {...}}
//!
//! Usage: zkapi-wallet SETUP_DIR WALLETS_DIR

use anyhow::{bail, ensure, Context, Result};
use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    io::{self, BufRead, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use zkapi_browser::{
    BrowserNoteState, BrowserWalletConfig, CompleteResponseArgs, ConfirmDepositArgs,
    PendingRequestJournal, PrepareRequestArgs, PrepareWithdrawalArgs, TreeSnapshot,
};
use zkapi_proof::compact::{CompactSigner, RequestProver, CIRCUIT_ID};
use zkapi_types::wire::RequestResponseV2;
use zkapi_types::Felt252;

const FILE_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct PendingDeposit {
    secret: Felt252,
    registration_commitment: Felt252,
    created_at_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct HistoryEntry {
    /// The signed state that was consumed by `client_request_id`.
    state: BrowserNoteState,
    client_request_id: String,
    charge_applied: u128,
    settled_at_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Closed {
    mode: String,
    transaction: Option<String>,
    at_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct WalletFile {
    version: u32,
    name: String,
    created_at_ms: u64,
    config: BrowserWalletConfig,
    #[serde(default)]
    pending_deposit: Option<PendingDeposit>,
    #[serde(default)]
    state: Option<BrowserNoteState>,
    #[serde(default)]
    journal: Option<PendingRequestJournal>,
    #[serde(default)]
    history: Vec<HistoryEntry>,
    #[serde(default)]
    closed: Option<Closed>,
    #[serde(default)]
    labels: Value,
}

#[derive(Deserialize)]
struct ConfirmArgs {
    note_id: u32,
    amount: u128,
    expiry_ts: u64,
}

#[derive(Deserialize)]
struct PathArgs {
    snapshot: TreeSnapshot,
    note_id: u32,
    require_existing: bool,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn valid_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 64
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'.')
        && !name.starts_with('.')
}

struct Driver {
    setup: PathBuf,
    dir: PathBuf,
    request_prover: Option<RequestProver>,
    withdrawal_key: Option<Vec<u8>>,
}

impl Driver {
    fn path(&self, name: &str) -> Result<PathBuf> {
        ensure!(valid_name(name), "invalid wallet name (use a-z 0-9 - _ .)");
        Ok(self.dir.join(format!("{name}.json")))
    }

    fn load(&self, name: &str) -> Result<WalletFile> {
        let path = self.path(name)?;
        let raw = std::fs::read(&path).with_context(|| format!("unknown wallet {name}"))?;
        let file: WalletFile = serde_json::from_slice(&raw).context("corrupt wallet file")?;
        ensure!(file.version == FILE_VERSION, "unsupported wallet file version");
        Ok(file)
    }

    fn save(&self, file: &WalletFile) -> Result<()> {
        let path = self.path(&file.name)?;
        let tmp = path.with_extension("json.tmp");
        let bytes = serde_json::to_vec_pretty(file)?;
        write_private(&tmp, &bytes)?;
        std::fs::rename(&tmp, &path)?;
        Ok(())
    }

    fn summary(file: &WalletFile) -> Value {
        let state = zkapi_browser::wallet_status(file.state.as_ref(), file.journal.as_ref());
        json!({
            "wallet": file.name,
            "chain_id": file.config.chain_id,
            "contract_address": file.config.contract_address,
            "created_at_ms": file.created_at_ms,
            "state": state,
            "pending_deposit": file.pending_deposit.as_ref().map(|d| json!({
                "registration_commitment": d.registration_commitment,
                "created_at_ms": d.created_at_ms,
            })),
            "pending_request": file.journal.as_ref().map(|j| json!({
                "client_request_id": j.client_request_id,
                "nullifier": j.nullifier,
                "created_at_ms": j.created_at_ms,
            })),
            "withdrawal_nullifier": file.state.as_ref().map(zkapi_browser::withdrawal_nullifier),
            "settlements": file.history.iter().map(|h| json!({
                "client_request_id": h.client_request_id,
                "charge_applied": h.charge_applied,
                "settled_at_ms": h.settled_at_ms,
                "balance_before": h.state.current_balance,
            })).collect::<Vec<_>>(),
            "closed": file.closed,
            "labels": file.labels,
        })
    }

    fn handle(&mut self, cmd: &Value) -> Result<Value> {
        let command = cmd["command"].as_str().context("command must be a string")?;
        let wallet = || cmd["wallet"].as_str().context("wallet name is required");
        match command {
            "ping" => Ok(json!({"circuit_id": CIRCUIT_ID})),
            "public_keys" => {
                let seed = |key: &str, default: u64| -> Result<Felt252> {
                    match cmd.get(key).and_then(Value::as_str) {
                        Some(hex) => Felt252::from_hex(hex).map_err(|e| anyhow::anyhow!("{key}: {e}")),
                        None => Ok(Felt252::from_u64(default)),
                    }
                };
                Ok(json!({
                    "state_signing_key": CompactSigner::from_seed(&seed("state_seed", 1)?).public_key(),
                    "clearance_signing_key": CompactSigner::from_seed(&seed("clear_seed", 2)?).public_key(),
                    "circuit_id": CIRCUIT_ID,
                }))
            }
            "list" => {
                let mut out = Vec::new();
                if let Ok(entries) = std::fs::read_dir(&self.dir) {
                    for entry in entries.flatten() {
                        let name = entry.file_name().to_string_lossy().to_string();
                        if let Some(stem) = name.strip_suffix(".json") {
                            if let Ok(file) = self.load(stem) {
                                out.push(Self::summary(&file));
                            }
                        }
                    }
                }
                out.sort_by_key(|v| v["created_at_ms"].as_u64().unwrap_or(0));
                Ok(json!(out))
            }
            "create" => {
                let name = wallet()?;
                let path = self.path(name)?;
                ensure!(!path.exists(), "wallet {name} already exists");
                let config: BrowserWalletConfig = serde_json::from_value(cmd["config"].clone())?;
                ensure!(config.protocol_version == 2, "only protocol v2 is supported");
                ensure!(!config.contract_address.is_zero(), "vault address is required");
                let file = WalletFile {
                    version: FILE_VERSION,
                    name: name.to_string(),
                    created_at_ms: now_ms(),
                    config,
                    pending_deposit: None,
                    state: None,
                    journal: None,
                    history: Vec::new(),
                    closed: None,
                    labels: cmd.get("labels").cloned().unwrap_or(json!({})),
                };
                self.save(&file)?;
                Ok(Self::summary(&file))
            }
            "status" => Ok(Self::summary(&self.load(wallet()?)?)),
            "config" => Ok(serde_json::to_value(self.load(wallet()?)?.config)?),
            "delete" => {
                let name = wallet()?;
                let file = self.load(name)?;
                let force = cmd["force"].as_bool().unwrap_or(false);
                let holds_value = file.closed.is_none()
                    && (file.state.as_ref().is_some_and(|s| s.current_balance > 0)
                        || file.pending_deposit.is_some());
                ensure!(
                    force || !holds_value,
                    "wallet still controls an open note or pending deposit; withdraw first (or force)"
                );
                std::fs::remove_file(self.path(name)?)?;
                Ok(json!({"deleted": name}))
            }
            "deposit_params" => {
                let mut file = self.load(wallet()?)?;
                ensure!(file.state.is_none(), "wallet already has a note");
                // Idempotent: a crash after this reply but before the deposit
                // transaction must not lose the secret behind a funded commitment.
                if file.pending_deposit.is_none() {
                    let params = zkapi_browser::generate_deposit_params();
                    file.pending_deposit = Some(PendingDeposit {
                        secret: params.secret,
                        registration_commitment: params.registration_commitment,
                        created_at_ms: now_ms(),
                    });
                    self.save(&file)?;
                }
                let pending = file.pending_deposit.as_ref().unwrap();
                Ok(json!({"registration_commitment": pending.registration_commitment}))
            }
            "confirm_deposit" => {
                let mut file = self.load(wallet()?)?;
                ensure!(file.state.is_none(), "wallet already has a note");
                let args: ConfirmArgs = serde_json::from_value(cmd["args"].clone())?;
                let pending = file.pending_deposit.clone().context("prepare a deposit first")?;
                let state = zkapi_browser::confirm_deposit(
                    &file.config,
                    ConfirmDepositArgs {
                        secret: pending.secret,
                        note_id: args.note_id,
                        amount: args.amount,
                        expiry_ts: args.expiry_ts,
                    },
                )?;
                file.state = Some(state);
                file.pending_deposit = None;
                self.save(&file)?;
                Ok(Self::summary(&file))
            }
            "pending_request" => {
                let file = self.load(wallet()?)?;
                Ok(match &file.journal {
                    Some(journal) => json!({"request": journal.prepared_request, "created_at_ms": journal.created_at_ms}),
                    None => Value::Null,
                })
            }
            "prepare_request" => {
                let mut file = self.load(wallet()?)?;
                ensure!(file.closed.is_none(), "wallet note is closed");
                ensure!(file.journal.is_none(), "a request is already pending; settle or recover it first");
                let args: PrepareRequestArgs = serde_json::from_value(cmd["args"].clone())?;
                if self.request_prover.is_none() {
                    self.request_prover = Some(RequestProver::load(&self.setup)?);
                }
                let state = file.state.as_ref().context("confirm a deposit first")?;
                let prepared = zkapi_browser::prepare_request_with_prover(
                    &file.config,
                    state,
                    args,
                    self.request_prover.as_ref().unwrap(),
                )?;
                // Write-ahead: the journal is durable before the caller can send it.
                file.journal = Some(prepared.journal);
                self.save(&file)?;
                Ok(json!({"request": prepared.request}))
            }
            "complete_response" => {
                let mut file = self.load(wallet()?)?;
                let journal = file.journal.clone().context("no pending request")?;
                let state = file.state.clone().context("confirm a deposit first")?;
                let response: RequestResponseV2 = serde_json::from_value(cmd["response"].clone())?;
                let charge = response.charge_applied;
                let next = zkapi_browser::complete_response(
                    &file.config,
                    CompleteResponseArgs { state: state.clone(), journal: journal.clone(), response },
                )?;
                file.history.push(HistoryEntry {
                    state,
                    client_request_id: journal.client_request_id,
                    charge_applied: charge,
                    settled_at_ms: now_ms(),
                });
                file.state = Some(next);
                file.journal = None;
                self.save(&file)?;
                Ok(Self::summary(&file))
            }
            "discard_request" => {
                // Only after the server's authoritative expiry handshake says the
                // exact request was never accepted (expired/superseded quote).
                let mut file = self.load(wallet()?)?;
                let journal = file.journal.clone().context("no pending request")?;
                let evidence = &cmd["evidence"];
                let status = evidence["status"].as_str().unwrap_or("");
                ensure!(
                    status == "expired_unaccepted" || status == "superseded_unaccepted",
                    "server did not confirm the request as unaccepted"
                );
                ensure!(
                    evidence["client_request_id"].as_str() == Some(journal.client_request_id.as_str()),
                    "expiry evidence is for a different request"
                );
                let nullifier: Felt252 = serde_json::from_value(evidence["request_nullifier"].clone())?;
                ensure!(nullifier == journal.nullifier, "expiry evidence nullifier mismatch");
                file.journal = None;
                self.save(&file)?;
                Ok(Self::summary(&file))
            }
            "prepare_withdrawal" => {
                let file = self.load(wallet()?)?;
                ensure!(file.closed.is_none(), "wallet note is closed");
                let args: PrepareWithdrawalArgs = serde_json::from_value(cmd["args"].clone())?;
                let state = match cmd.get("history_index").and_then(Value::as_u64) {
                    Some(index) => {
                        // A consumed earlier state: only meaningful as an escape the
                        // challenger is expected to defeat (fraud demonstration).
                        ensure!(args.mode == "escape", "historical states can only be escaped");
                        let entry = file.history.get(index as usize).context("unknown history index")?;
                        ensure!(
                            !entry.state.is_genesis || index == 0,
                            "invalid historical state"
                        );
                        entry.state.clone()
                    }
                    None => {
                        ensure!(
                            file.journal.is_none(),
                            "settle the pending request before an honest withdrawal"
                        );
                        file.state.clone().context("confirm a deposit first")?
                    }
                };
                if self.withdrawal_key.is_none() {
                    self.withdrawal_key = Some(std::fs::read(self.setup.join("withdrawal.pk"))?);
                }
                let plan = zkapi_browser::prepare_withdrawal(
                    &file.config,
                    &state,
                    args,
                    self.withdrawal_key.as_deref().unwrap(),
                )?;
                let inputs_abi = format!(
                    "0x{}",
                    plan.public_inputs
                        .to_field_elements()
                        .iter()
                        .map(|value| hex::encode(value.as_bytes()))
                        .collect::<String>()
                );
                let proof_hex = format!(
                    "0x{}",
                    hex::encode(base64::engine::general_purpose::STANDARD.decode(&plan.proof.proof)?)
                );
                let mut result = serde_json::to_value(plan)?;
                result["inputs_abi"] = json!(inputs_abi);
                result["proof_hex"] = json!(proof_hex);
                Ok(result)
            }
            "mark_closed" => {
                let mut file = self.load(wallet()?)?;
                file.closed = Some(Closed {
                    mode: cmd["mode"].as_str().unwrap_or("mutual").to_string(),
                    transaction: cmd["transaction"].as_str().map(str::to_string),
                    at_ms: now_ms(),
                });
                self.save(&file)?;
                Ok(Self::summary(&file))
            }
            "reopen" => {
                // A challenged escape restores the note on-chain.
                let mut file = self.load(wallet()?)?;
                file.closed = None;
                self.save(&file)?;
                Ok(Self::summary(&file))
            }
            "tree_path" => {
                let args: PathArgs = serde_json::from_value(cmd["args"].clone())?;
                Ok(serde_json::to_value(zkapi_browser::tree_path(
                    args.snapshot,
                    args.note_id,
                    args.require_existing,
                )?)?)
            }
            _ => bail!("unsupported command {command}"),
        }
    }
}

#[cfg(unix)]
fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)?;
    f.write_all(bytes)?;
    f.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    std::fs::write(path, bytes)?;
    Ok(())
}

fn main() -> Result<()> {
    let mut args = std::env::args_os().skip(1);
    let usage = "usage: zkapi-wallet SETUP_DIR WALLETS_DIR";
    let setup = PathBuf::from(args.next().context(usage)?);
    let dir = PathBuf::from(args.next().context(usage)?);
    ensure!(args.next().is_none(), "{usage}");
    std::fs::create_dir_all(&dir)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
    }
    let mut driver = Driver { setup, dir, request_prover: None, withdrawal_key: None };
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let (id, result) = match serde_json::from_str::<Value>(&line) {
            Ok(command) => (command.get("id").cloned().unwrap_or(Value::Null), driver.handle(&command)),
            Err(error) => (Value::Null, Err(error.into())),
        };
        let reply = match result {
            Ok(result) => json!({"id": id, "ok": true, "result": result}),
            Err(error) => json!({"id": id, "ok": false, "error": format!("{error:#}")}),
        };
        serde_json::to_writer(&mut stdout, &reply)?;
        writeln!(stdout)?;
        stdout.flush()?;
    }
    Ok(())
}
