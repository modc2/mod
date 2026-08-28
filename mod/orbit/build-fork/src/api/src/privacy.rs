//! Private repos — server-held passwords + encrypted-only publishing.
//!
//! Every module is PUBLIC by default: it shows on the hub for anyone, signed
//! in or not. Privacy is opt-in, per module, and reversible.
//!
//! Marking a module private does two things:
//!   1. No plaintext leaves this machine anymore. Snapshots become a single
//!      encrypted bundle blob (manifest + every file body), the mod-protocol
//!      registry push is blocked at its choke point (`mod_protocol_register`
//!      refuses private modules — the api module would re-read the plaintext
//!      tree), and the read endpoints hide the module from non-owners: it is
//!      gone from /modules (the hub's card wall), from /files, and its jobs
//!      drop out of the public task ledger.
//!      An already-registered on-chain entry simply goes stale: whoever holds
//!      the old CID has what was already public, but every update from now on
//!      exists only as ciphertext.
//!   2. A password is generated and held server-side in
//!      `~/.mod/build-fork/private/<module>.json` (0600). The owner can copy it
//!      through the API and delete the server copy at any time — after that
//!      only the salt + verifier remain, so the server can still *verify* a
//!      supplied password but can never recover it. Losing the password then
//!      means losing the encrypted history; there is no back door.
//!
//! Who can see through it: the address that turned privacy on, plus the host
//! owners (it is their filesystem — pretending otherwise would be theatre).
//! Everyone else, whitelisted editors included, sees nothing at all.
//!
//! Crypto uses only crates already vendored for this offline build
//! (sha2 / hmac / rand / rand_chacha): PBKDF2-HMAC-SHA256 stretches the
//! password, the ChaCha20 keystream (ChaCha20Rng seeded per-bundle) encrypts,
//! and an HMAC-SHA256 tag over header + ciphertext authenticates
//! (encrypt-then-MAC with independent enc/mac keys). A wrong password fails
//! the MAC — that is also how password verification works when only the
//! blob is at hand.

use hmac::{Hmac, Mac};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::path::{Path, PathBuf};

use crate::snapshots::{bundle_dir, default_store, module_root_for, write_manifest_files, Manifest};

/// Blob header magic — how restore paths recognize an encrypted bundle.
pub const MAGIC: &[u8; 8] = b"BLDPRIV1";
const SALT_LEN: usize = 16;
const NONCE_LEN: usize = 16;
const TAG_LEN: usize = 32;
const HEADER_LEN: usize = 8 + SALT_LEN + NONCE_LEN;
const PBKDF2_ITERS: u32 = 120_000;
/// Bundles are built fully in memory (bytes + base64 JSON), so cap the tree.
pub const MAX_BUNDLE_BYTES: u64 = 128 * 1024 * 1024;

// ── Per-module privacy record ────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrivacyRecord {
    pub module: String,
    pub owner: String,
    /// Disabling keeps the key material (old encrypted bundles still need
    /// it); only `enabled` flips.
    pub enabled: bool,
    /// Hex, 16 bytes — PBKDF2 salt, fixed per module.
    pub salt: String,
    /// Hex — HMAC(master, "verifier"). Survives password deletion so the
    /// server can always check a supplied password without holding it.
    pub verifier: String,
    /// The server-held copy the owner can read back; deletable at any time.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub password: Option<String>,
    pub created: u64,
    pub updated: u64,
}

fn now_ts() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".mod").join("build-fork").join("private")
}

/// Same sanitization as versions_path so nested names (portal/x/y) map to
/// one flat file and nothing can traverse out of the dir.
fn record_path(module: &str) -> PathBuf {
    let safe: String = module
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '/'))
        .collect();
    dir().join(format!("{}.json", safe.replace('/', "__")))
}

pub fn record(module: &str) -> Option<PrivacyRecord> {
    let raw = std::fs::read_to_string(record_path(module)).ok()?;
    serde_json::from_str(&raw).ok()
}

fn save(rec: &PrivacyRecord) -> Result<(), String> {
    let d = dir();
    std::fs::create_dir_all(&d).map_err(|e| format!("mkdir private: {e}"))?;
    let path = record_path(&rec.module);
    let json = serde_json::to_vec_pretty(rec).map_err(|e| format!("record serialize: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("record write: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o700));
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

pub fn is_private(module: &str) -> bool {
    record(module).map(|r| r.enabled).unwrap_or(false)
}

/// Every record on disk, read in one pass. `record()` costs a file read per
/// module — listing a 200-module fleet wants them all at once.
pub fn records() -> Vec<PrivacyRecord> {
    let Ok(rd) = std::fs::read_dir(dir()) else { return vec![] };
    rd.flatten()
        .filter_map(|e| std::fs::read_to_string(e.path()).ok())
        .filter_map(|raw| serde_json::from_str::<PrivacyRecord>(&raw).ok())
        .collect()
}

// ── Who sees what ────────────────────────────────────────────────────

/// Callers who see through every private module: local mode (no auth at all)
/// and the host owners, whose shell can read the tree regardless.
fn sees_everything(caller: &str) -> bool {
    std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1" || crate::auth::is_owner(caller)
}

fn owns(rec: &PrivacyRecord, caller: &str) -> bool {
    !caller.is_empty() && rec.owner.eq_ignore_ascii_case(caller)
}

/// May `caller` read this module's plaintext? True for every public module —
/// privacy is opt-in, so anything without an enabled record is everyone's.
pub fn can_access(caller: &str, module: &str) -> bool {
    match record(module) {
        Some(rec) if rec.enabled => sees_everything(caller) || owns(&rec, caller),
        _ => true,
    }
}

/// May `caller` flip privacy, read the key, or delete the server copy?
/// An existing record belongs to whoever created it; a module with no record
/// yet needs edit rights over that module (owner or scoped whitelist editor).
pub fn may_manage(caller: &str, module: &str) -> bool {
    match record(module) {
        Some(rec) => sees_everything(caller) || owns(&rec, caller),
        None => sees_everything(caller) || crate::auth::can_edit_module(caller, module),
    }
}

/// Lowercased names of the private modules `caller` may not see. One dir
/// read, so callers can filter a whole listing against it.
pub fn hidden_names(caller: &str) -> std::collections::HashSet<String> {
    if sees_everything(caller) {
        return std::collections::HashSet::new();
    }
    hidden_in(records(), caller)
}

/// The filter itself, disk-free: enabled records that aren't the caller's.
fn hidden_in(recs: Vec<PrivacyRecord>, caller: &str) -> std::collections::HashSet<String> {
    recs.into_iter()
        .filter(|r| r.enabled && !owns(r, caller))
        .map(|r| r.module.to_lowercase())
        .collect()
}

/// The module a job worked in, when it was a fleet module at all. Job rows
/// name their module only through `work_dir`.
pub fn module_of_work_dir(work_dir: &str) -> Option<String> {
    crate::snapshots::module_for_work_dir(work_dir).map(|(name, _)| name)
}

/// Human-copyable password: 24 random bytes, base64url (32 chars).
fn generate_password() -> String {
    use base64::Engine;
    let mut bytes = [0u8; 24];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
}

/// Turn privacy on. Re-enabling a module whose key material already exists
/// keeps it — old bundles stay decryptable, and a deleted server-side
/// password stays deleted (re-enabling must not resurrect a secret the
/// owner chose to hold exclusively).
pub fn enable(module: &str, owner: &str) -> Result<PrivacyRecord, String> {
    if let Some(mut rec) = record(module) {
        rec.enabled = true;
        rec.updated = now_ts();
        save(&rec)?;
        return Ok(rec);
    }
    let mut salt = [0u8; SALT_LEN];
    rand::rngs::OsRng.fill_bytes(&mut salt);
    let password = generate_password();
    let master = master_key(&password, &salt);
    let ts = now_ts();
    let rec = PrivacyRecord {
        module: module.to_string(),
        owner: owner.to_lowercase(),
        enabled: true,
        salt: hex::encode(salt),
        verifier: hex::encode(hmac_sha256(&master, &[b"build-privacy-verifier"])),
        password: Some(password),
        created: ts,
        updated: ts,
    };
    save(&rec)?;
    Ok(rec)
}

pub fn disable(module: &str) -> Result<PrivacyRecord, String> {
    let mut rec = record(module).ok_or_else(|| format!("module '{module}' is not private"))?;
    rec.enabled = false;
    rec.updated = now_ts();
    save(&rec)?;
    Ok(rec)
}

/// Drop the server-held password copy; salt + verifier stay so supplied
/// passwords can still be verified. Irreversible server-side.
pub fn delete_password(module: &str) -> Result<PrivacyRecord, String> {
    let mut rec = record(module).ok_or_else(|| format!("module '{module}' is not private"))?;
    rec.password = None;
    rec.updated = now_ts();
    save(&rec)?;
    Ok(rec)
}

pub fn server_password(module: &str) -> Option<String> {
    record(module)?.password
}

/// Check a password against the stored verifier.
pub fn verify(module: &str, password: &str) -> Result<bool, String> {
    let rec = record(module).ok_or_else(|| format!("module '{module}' is not private"))?;
    let salt = hex::decode(&rec.salt).map_err(|e| format!("bad salt in record: {e}"))?;
    let master = master_key(password, &salt);
    let derived = hex::encode(hmac_sha256(&master, &[b"build-privacy-verifier"]));
    Ok(ct_eq(derived.as_bytes(), rec.verifier.as_bytes()))
}

/// The password an operation should use: a supplied one (verified) wins,
/// else the server-held copy. Errors spell out the recovery path.
pub fn resolve_password(module: &str, supplied: Option<&str>) -> Result<String, String> {
    if let Some(p) = supplied.filter(|p| !p.is_empty()) {
        return if verify(module, p)? {
            Ok(p.to_string())
        } else {
            Err("invalid password for this module".to_string())
        };
    }
    server_password(module).ok_or_else(|| {
        "password required: the server-side copy was deleted — supply `password` in the request"
            .to_string()
    })
}

// ── Crypto primitives (vendored crates only) ─────────────────────────

fn hmac_sha256(key: &[u8], parts: &[&[u8]]) -> [u8; 32] {
    let mut mac = Hmac::<Sha256>::new_from_slice(key).expect("hmac accepts any key length");
    for p in parts {
        mac.update(p);
    }
    mac.finalize().into_bytes().into()
}

fn pbkdf2_sha256(password: &[u8], salt: &[u8], iters: u32, out: &mut [u8]) {
    let mut block: u32 = 1;
    for chunk in out.chunks_mut(32) {
        let mut u = hmac_sha256(password, &[salt, &block.to_be_bytes()]);
        let mut t = u;
        for _ in 1..iters {
            u = hmac_sha256(password, &[&u]);
            for (ti, ui) in t.iter_mut().zip(u.iter()) {
                *ti ^= ui;
            }
        }
        chunk.copy_from_slice(&t[..chunk.len()]);
        block += 1;
    }
}

fn master_key(password: &str, salt: &[u8]) -> [u8; 32] {
    let mut out = [0u8; 32];
    pbkdf2_sha256(password.as_bytes(), salt, PBKDF2_ITERS, &mut out);
    out
}

/// XOR `buf` with the ChaCha20 keystream for `seed` (the RNG's output IS the
/// ChaCha20 block stream). Chunked so a 100MB bundle doesn't double RAM.
fn xor_keystream(seed: &[u8; 32], buf: &mut [u8]) {
    use rand::SeedableRng;
    let mut rng = rand_chacha::ChaCha20Rng::from_seed(*seed);
    let mut ks = [0u8; 64 * 1024];
    for chunk in buf.chunks_mut(ks.len()) {
        rng.fill_bytes(&mut ks[..chunk.len()]);
        for (b, k) in chunk.iter_mut().zip(ks.iter()) {
            *b ^= k;
        }
    }
}

fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

/// MAGIC | salt | nonce | ciphertext | tag. Fresh nonce per call; per-bundle
/// enc key is bound to the nonce so keystreams never repeat under one salt.
pub fn encrypt(password: &str, salt: &[u8], plaintext: &[u8]) -> Vec<u8> {
    let master = master_key(password, salt);
    let mut nonce = [0u8; NONCE_LEN];
    rand::rngs::OsRng.fill_bytes(&mut nonce);
    let enc_key = hmac_sha256(&master, &[b"build-privacy-enc", &nonce]);
    let mac_key = hmac_sha256(&master, &[b"build-privacy-mac"]);
    let mut ct = plaintext.to_vec();
    xor_keystream(&enc_key, &mut ct);
    let tag = hmac_sha256(&mac_key, &[MAGIC, salt, &nonce, &ct]);
    let mut out = Vec::with_capacity(HEADER_LEN + ct.len() + TAG_LEN);
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(salt);
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&ct);
    out.extend_from_slice(&tag);
    out
}

pub fn is_encrypted_blob(blob: &[u8]) -> bool {
    blob.len() >= HEADER_LEN + TAG_LEN && &blob[..8] == MAGIC
}

// ── Key-based sealing (no PBKDF2 per call) ───────────────────────────
//
// `encrypt`/`decrypt` stretch a password on every call — right for a bundle
// written once, far too slow for the task vault, which seals every line of
// job output under a key it already holds. `seal`/`open` are the same
// construction with the 32-byte key supplied directly:
//   SEAL_MAGIC | nonce | ciphertext | tag
// Distinct magic and distinct HMAC labels, so a blob from one scheme can
// never be opened by the other.

pub const SEAL_MAGIC: &[u8; 8] = b"BLDSEAL1";
const SEAL_HEADER_LEN: usize = 8 + NONCE_LEN;

pub fn seal(key: &[u8; 32], plaintext: &[u8]) -> Vec<u8> {
    let mut nonce = [0u8; NONCE_LEN];
    rand::rngs::OsRng.fill_bytes(&mut nonce);
    let enc_key = hmac_sha256(key, &[b"build-seal-enc", &nonce]);
    let mac_key = hmac_sha256(key, &[b"build-seal-mac"]);
    let mut ct = plaintext.to_vec();
    xor_keystream(&enc_key, &mut ct);
    let tag = hmac_sha256(&mac_key, &[SEAL_MAGIC, &nonce, &ct]);
    let mut out = Vec::with_capacity(SEAL_HEADER_LEN + ct.len() + TAG_LEN);
    out.extend_from_slice(SEAL_MAGIC);
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&ct);
    out.extend_from_slice(&tag);
    out
}

pub fn open(key: &[u8; 32], blob: &[u8]) -> Result<Vec<u8>, String> {
    if blob.len() < SEAL_HEADER_LEN + TAG_LEN || &blob[..8] != SEAL_MAGIC {
        return Err("not a sealed blob".to_string());
    }
    let nonce = &blob[8..SEAL_HEADER_LEN];
    let ct = &blob[SEAL_HEADER_LEN..blob.len() - TAG_LEN];
    let tag = &blob[blob.len() - TAG_LEN..];
    let mac_key = hmac_sha256(key, &[b"build-seal-mac"]);
    if !ct_eq(&hmac_sha256(&mac_key, &[SEAL_MAGIC, nonce, ct]), tag) {
        return Err("wrong key (or corrupted blob)".to_string());
    }
    let enc_key = hmac_sha256(key, &[b"build-seal-enc", nonce]);
    let mut pt = ct.to_vec();
    xor_keystream(&enc_key, &mut pt);
    Ok(pt)
}

pub fn decrypt(password: &str, blob: &[u8]) -> Result<Vec<u8>, String> {
    if !is_encrypted_blob(blob) {
        return Err("not an encrypted bundle".to_string());
    }
    let salt = &blob[8..8 + SALT_LEN];
    let nonce = &blob[8 + SALT_LEN..HEADER_LEN];
    let ct = &blob[HEADER_LEN..blob.len() - TAG_LEN];
    let tag = &blob[blob.len() - TAG_LEN..];
    let master = master_key(password, salt);
    let mac_key = hmac_sha256(&master, &[b"build-privacy-mac"]);
    let expect = hmac_sha256(&mac_key, &[MAGIC, salt, nonce, ct]);
    if !ct_eq(&expect, tag) {
        return Err("wrong password (or corrupted bundle)".to_string());
    }
    let enc_key = hmac_sha256(&master, &[b"build-privacy-enc", nonce]);
    let mut pt = ct.to_vec();
    xor_keystream(&enc_key, &mut pt);
    Ok(pt)
}

// ── Encrypted snapshot / restore ─────────────────────────────────────

/// The plaintext inside an encrypted blob: the manifest plus every file body.
#[derive(Serialize, Deserialize)]
struct Bundle {
    v: u32,
    module: String,
    plain_cid: String,
    manifest: Manifest,
    /// cid → base64 file body
    blobs: std::collections::HashMap<String, String>,
}

pub struct EncryptedSnap {
    pub cipher_cid: String,
    pub plain_cid: String,
    pub file_count: usize,
}

/// Snapshot `root` as one encrypted bundle blob in the shared store. The
/// only thing that store (and anything downstream) ever sees is ciphertext.
pub fn snapshot_encrypted(module: &str, root: &Path, password: &str) -> Result<EncryptedSnap, String> {
    let rec = record(module).ok_or_else(|| format!("module '{module}' is not private"))?;
    let salt = hex::decode(&rec.salt).map_err(|e| format!("bad salt in record: {e}"))?;
    let (plain_cid, manifest, blobs) = bundle_dir(root, MAX_BUNDLE_BYTES)?;
    let file_count = manifest.files.len();
    use base64::Engine;
    let b64 = base64::engine::general_purpose::STANDARD;
    let bundle = Bundle {
        v: 1,
        module: module.to_string(),
        plain_cid: plain_cid.clone(),
        manifest,
        blobs: blobs.into_iter().map(|(cid, bytes)| (cid, b64.encode(bytes))).collect(),
    };
    let plaintext = serde_json::to_vec(&bundle).map_err(|e| format!("bundle serialize: {e}"))?;
    let cipher = encrypt(password, &salt, &plaintext);
    let cipher_cid = default_store().put(&cipher)?;
    Ok(EncryptedSnap { cipher_cid, plain_cid, file_count })
}

/// Decrypt a bundle blob and write its files into `target` (same escape
/// guards as plaintext restores).
pub fn restore_encrypted(target: &Path, blob: &[u8], password: &str) -> Result<usize, String> {
    let plaintext = decrypt(password, blob)?;
    let bundle: Bundle =
        serde_json::from_slice(&plaintext).map_err(|e| format!("bundle parse: {e}"))?;
    use base64::Engine;
    let b64 = base64::engine::general_purpose::STANDARD;
    write_manifest_files(target, &bundle.manifest, |cid| {
        let enc = bundle
            .blobs
            .get(cid)
            .ok_or_else(|| format!("bundle missing blob {cid}"))?;
        b64.decode(enc).map_err(|e| format!("bundle blob decode: {e}"))
    })
}

// ── Read guards for the /files endpoints ─────────────────────────────

/// Private-module roots this caller may NOT read. Empty in local mode and
/// for the host owners; otherwise every private tree except the caller's
/// own — whitelisted editors included.
pub fn denied_roots(caller: &str) -> Vec<PathBuf> {
    if sees_everything(caller) {
        return vec![];
    }
    records()
        .into_iter()
        .filter(|r| r.enabled && !owns(r, caller))
        .filter_map(|r| module_root_for(&r.module))
        .map(|p| std::fs::canonicalize(&p).unwrap_or(p))
        .collect()
}

/// Err(message) when `path` reaches inside a private module the caller
/// can't read. Call after path resolution, before touching the FS.
pub fn read_guard(caller: &str, path: &Path) -> Result<(), String> {
    let p = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    for root in denied_roots(caller) {
        if p.starts_with(&root) {
            return Err("private module — owner access only".to_string());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pbkdf2_known_vector() {
        // RFC 7914 §11 / common PBKDF2-HMAC-SHA256 vector: P="password",
        // S="salt", c=1, dkLen=32.
        let mut out = [0u8; 32];
        pbkdf2_sha256(b"password", b"salt", 1, &mut out);
        assert_eq!(
            hex::encode(out),
            "120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b"
        );
    }

    #[test]
    fn encrypt_decrypt_roundtrip() {
        let salt = [7u8; SALT_LEN];
        let blob = encrypt("hunter2", &salt, b"the plans");
        assert!(is_encrypted_blob(&blob));
        assert!(!blob.windows(9).any(|w| w == b"the plans"));
        assert_eq!(decrypt("hunter2", &blob).unwrap(), b"the plans");
    }

    #[test]
    fn wrong_password_and_tamper_fail() {
        let salt = [7u8; SALT_LEN];
        let mut blob = encrypt("hunter2", &salt, b"secret");
        assert!(decrypt("hunter3", &blob).is_err());
        let mid = blob.len() / 2;
        blob[mid] ^= 1;
        assert!(decrypt("hunter2", &blob).is_err());
    }

    #[test]
    fn seal_open_roundtrip_and_key_separation() {
        let key = [9u8; 32];
        let blob = seal(&key, b"vaulted prompt");
        assert!(!blob.windows(14).any(|w| w == b"vaulted prompt"));
        assert_eq!(open(&key, &blob).unwrap(), b"vaulted prompt");
        // Wrong key, tampering, and cross-scheme blobs all fail closed.
        assert!(open(&[8u8; 32], &blob).is_err());
        let mut bad = blob.clone();
        let mid = bad.len() / 2;
        bad[mid] ^= 1;
        assert!(open(&key, &bad).is_err());
        assert!(open(&key, &encrypt("pw", &[7u8; SALT_LEN], b"x")).is_err());
    }

    fn rec(module: &str, owner: &str, enabled: bool) -> PrivacyRecord {
        PrivacyRecord {
            module: module.to_string(),
            owner: owner.to_string(),
            enabled,
            salt: hex::encode([0u8; SALT_LEN]),
            verifier: String::new(),
            password: None,
            created: 0,
            updated: 0,
        }
    }

    #[test]
    fn only_the_owner_sees_a_private_module() {
        let alice = "0xAAAa0000000000000000000000000000000000aa";
        let bob = "0xBbbB0000000000000000000000000000000000bb";
        let recs = vec![rec("secret", alice, true), rec("shared", alice, false)];

        // Signed out and signed in as someone else: the private module is
        // simply not there. The disabled one is nobody's business to hide.
        for stranger in ["", bob] {
            let hidden = hidden_in(recs.clone(), stranger);
            assert!(hidden.contains("secret"), "private module leaked to {stranger:?}");
            assert!(!hidden.contains("shared"));
        }

        // Its owner sees it — address casing is not identity.
        assert!(hidden_in(recs.clone(), alice).is_empty());
        assert!(hidden_in(recs, &alice.to_lowercase()).is_empty());
    }

    #[test]
    fn nonce_uniqueness_changes_ciphertext() {
        let salt = [1u8; SALT_LEN];
        let a = encrypt("pw", &salt, b"same content");
        let b = encrypt("pw", &salt, b"same content");
        assert_ne!(a, b, "fresh nonce must give fresh ciphertext");
    }
}
