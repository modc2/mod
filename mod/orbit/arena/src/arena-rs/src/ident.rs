//! ident — who is asking, and who owns this box.
//!
//! A caller is a wallet address, proved by a mod-protocol token: base64url of
//! `{data, time, key, signature}`, the signature an ECDSA (secp256k1) one over
//! exactly `{"data":<data>,"time":<time>}`. Two signers make those tokens and
//! both are accepted: a browser wallet's `personal_sign` (EIP-191 prefixed)
//! and the Python key's own `sign` (keccak of the bare message). Verifying is
//! done here, offline, with no service to ask — the token carries everything.
//!
//! The caller of a request is set once at the HTTP edge (`scope`) from the
//! `token` header and read anywhere below with `caller()`. Arguments cannot
//! set it, so an upload cannot claim to be somebody else's.
//!
//! The box's owners are the addresses that may edit anything here: the build
//! module's owner (the person whose agent writes the vibes), the box's own key
//! (the CLI signs as it), and whatever `ARENA_OWNERS` lists.

use k256::ecdsa::{RecoveryId, Signature, VerifyingKey};
use serde_json::Value;
use sha3::{Digest, Keccak256};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

/// How old a token may be. The fleet's tokens are minted for a week.
const MAX_AGE_SECS: f64 = 7.0 * 86_400.0;

tokio::task_local! {
    static CALLER: Option<String>;
}

/// Run `fut` with `who` as the caller of everything it does.
pub async fn scope<F: std::future::Future>(who: Option<String>, fut: F) -> F::Output {
    CALLER.scope(who, fut).await
}

/// The verified address of whoever made this request — lowercase — or None.
pub fn caller() -> Option<String> {
    CALLER.try_with(|c| c.clone()).ok().flatten()
}

fn b64url_decode(s: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(s.len() * 3 / 4);
    let (mut buf, mut bits) = (0u32, 0u32);
    for c in s.trim().trim_end_matches('=').bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'-' | b'+' => 62,
            b'_' | b'/' => 63,
            _ => return None,
        } as u32;
        buf = (buf << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buf >> bits) as u8);
            buf &= (1 << bits) - 1;
        }
    }
    Some(out)
}

fn keccak(bytes: &[u8]) -> [u8; 32] {
    Keccak256::digest(bytes).into()
}

fn hex_decode(s: &str) -> Option<Vec<u8>> {
    let s = s.trim().trim_start_matches("0x");
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok()).collect()
}

fn address_of(key: &VerifyingKey) -> String {
    let point = key.to_encoded_point(false);
    let hash = keccak(&point.as_bytes()[1..]);
    let mut out = String::from("0x");
    for b in &hash[12..] {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

fn recover(prehash: &[u8; 32], sig: &[u8]) -> Option<String> {
    if sig.len() != 65 {
        return None;
    }
    let v = match sig[64] {
        27 | 28 => sig[64] - 27,
        v @ (0 | 1) => v,
        _ => return None,
    };
    let signature = Signature::from_slice(&sig[..64]).ok()?;
    // A high-s signature is valid on Ethereum; k256 wants it normalized, and
    // normalizing flips which of the two points it recovers to.
    let (signature, v) = match signature.normalize_s() {
        Some(low) => (low, v ^ 1),
        None => (signature, v),
    };
    let rid = RecoveryId::from_byte(v)?;
    VerifyingKey::recover_from_prehash(prehash, &signature, rid).ok().map(|k| address_of(&k))
}

/// The address a token proves, lowercase. Errors say why not.
pub fn verify(token: &str) -> Result<String, String> {
    let token = token.trim().trim_start_matches("Bearer ").trim();
    let raw = b64url_decode(token).ok_or("the token is not base64url")?;
    let h: Value = serde_json::from_slice(&raw).map_err(|_| "the token is not JSON inside")?;
    let key = h.get("key").and_then(|v| v.as_str()).ok_or("the token names no key")?.to_lowercase();
    let sig = h.get("signature").and_then(|v| v.as_str()).and_then(hex_decode).ok_or("the token carries no signature")?;
    let time = h.get("time").ok_or("the token has no time")?;
    let data = h.get("data").cloned().unwrap_or(Value::Null);

    let t: f64 = match time {
        Value::String(s) => s.parse().map_err(|_| "the token's time is not a number")?,
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        _ => return Err("the token's time is not a number".into()),
    };
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
    if (now - t).abs() > MAX_AGE_SECS {
        return Err("the token has expired — sign in again".into());
    }

    // The signed message, rebuilt the way both signers wrote it: compact JSON,
    // data first. A string or a one-key object reads the same in any order.
    let message = format!(
        "{{\"data\":{},\"time\":{}}}",
        serde_json::to_string(&data).unwrap_or_default(),
        serde_json::to_string(time).unwrap_or_default()
    );
    let prefixed = format!("\x19Ethereum Signed Message:\n{}{}", message.len(), message);
    for prehash in [keccak(prefixed.as_bytes()), keccak(message.as_bytes())] {
        if recover(&prehash, &sig).as_deref() == Some(key.as_str()) {
            return Ok(key);
        }
    }
    Err("the token's signature does not match its key".into())
}

/// The token a request carries: the `token` header (the protocol's own), else
/// `x-mod-token`, else a bearer token.
pub fn from_headers(headers: &axum::http::HeaderMap) -> Option<String> {
    let get = |k: &str| headers.get(k).and_then(|v| v.to_str().ok()).map(str::trim).filter(|s| !s.is_empty());
    get("token")
        .or_else(|| get("x-mod-token"))
        .or_else(|| get("authorization").map(|a| a.trim_start_matches("Bearer ").trim()))
        .map(str::to_string)
}

// ── the box's owners ─────────────────────────────────────────────────────

fn owners_cell() -> &'static Mutex<Vec<String>> {
    static O: OnceLock<Mutex<Vec<String>>> = OnceLock::new();
    O.get_or_init(|| {
        let from_env = std::env::var("ARENA_OWNERS")
            .unwrap_or_default()
            .split(',')
            .map(|a| a.trim().to_lowercase())
            .filter(|a| a.starts_with("0x"))
            .collect();
        Mutex::new(from_env)
    })
}

/// Everyone who may edit anything here. First is who the box is shown as.
pub fn owners() -> Vec<String> {
    owners_cell().lock().unwrap_or_else(|e| e.into_inner()).clone()
}

pub fn add_owner(address: &str) {
    let a = address.trim().to_lowercase();
    if !a.starts_with("0x") {
        return;
    }
    let mut o = owners_cell().lock().unwrap_or_else(|e| e.into_inner());
    if !o.contains(&a) {
        o.push(a);
    }
}

/// The address an unclaimed module is shown under — the box's owner.
pub fn host() -> String {
    owners().into_iter().next().unwrap_or_default()
}

pub fn is_owner(address: &str) -> bool {
    owners().iter().any(|o| o.eq_ignore_ascii_case(address))
}

/// May `who` change something owned by `owner`? Its owner may, and so may the
/// box's owners; an unclaimed thing belongs to the box.
pub fn can_edit(owner: &str, who: Option<&str>) -> bool {
    match who {
        None => false,
        Some(w) => is_owner(w) || (!owner.is_empty() && owner.eq_ignore_ascii_case(w)),
    }
}

/// Learn the box's owners: build's owner (the wallet the vibes bill) and the
/// box key the CLI signs with. Neither is required; both are retried later.
pub fn learn_owners_later() {
    tokio::spawn(async {
        for attempt in 0..6u64 {
            if let Some(base) = crate::vibe::build_base() {
                if let Ok(owner) = crate::vibe::build_owner(&base).await {
                    add_owner(&owner);
                }
            }
            let key = tokio::task::spawn_blocking(|| {
                std::process::Command::new("python3")
                    .args(["-I", "-c", "import mod as m; print(m.key().address)"])
                    .output()
                    .ok()
                    .filter(|o| o.status.success())
                    .map(|o| String::from_utf8_lossy(&o.stdout).trim().lines().last().unwrap_or("").to_string())
            })
            .await
            .ok()
            .flatten();
            if let Some(k) = key {
                add_owner(&k);
            }
            if owners().len() >= 2 {
                return;
            }
            tokio::time::sleep(std::time::Duration::from_secs(20 * (attempt + 1))).await;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use k256::ecdsa::SigningKey;

    fn b64url(bytes: &[u8]) -> String {
        const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        let mut out = String::new();
        for chunk in bytes.chunks(3) {
            let n = chunk.iter().enumerate().fold(0u32, |n, (i, b)| n | (*b as u32) << (16 - 8 * i));
            for i in 0..=chunk.len() {
                out.push(A[(n >> (18 - 6 * i) & 63) as usize] as char);
            }
        }
        out
    }

    fn token(sk: &SigningKey, prefixed: bool, time: &str) -> (String, String) {
        let addr = address_of(sk.verifying_key());
        let msg = format!("{{\"data\":\"arena\",\"time\":\"{time}\"}}");
        let pre = if prefixed {
            keccak(format!("\x19Ethereum Signed Message:\n{}{}", msg.len(), msg).as_bytes())
        } else {
            keccak(msg.as_bytes())
        };
        let (sig, rid) = sk.sign_prehash_recoverable(&pre).unwrap();
        let mut bytes = sig.to_bytes().to_vec();
        bytes.push(rid.to_byte() + 27);
        let hex: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        let body = format!(
            "{{\"data\":\"arena\",\"time\":\"{time}\",\"key\":\"{addr}\",\"signature\":\"0x{hex}\"}}"
        );
        (b64url(body.as_bytes()), addr)
    }

    fn now_s() -> String {
        format!("{}", SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs())
    }

    #[test]
    fn wallet_and_raw_signatures_verify() {
        let sk = SigningKey::from_slice(&[7u8; 32]).unwrap();
        for prefixed in [true, false] {
            let (t, addr) = token(&sk, prefixed, &now_s());
            assert_eq!(verify(&t).unwrap(), addr);
        }
    }

    #[test]
    fn a_stale_or_forged_token_is_refused() {
        let sk = SigningKey::from_slice(&[9u8; 32]).unwrap();
        let (old, _) = token(&sk, true, "1000");
        assert!(verify(&old).unwrap_err().contains("expired"));
        // Claim someone else's key with this signature.
        let (t, _) = token(&sk, true, &now_s());
        let mut h: Value = serde_json::from_slice(&b64url_decode(&t).unwrap()).unwrap();
        h["key"] = Value::String("0x0000000000000000000000000000000000000001".into());
        assert!(verify(&b64url(h.to_string().as_bytes())).is_err());
    }

    #[test]
    fn owners_may_edit_and_others_may_not() {
        add_owner("0xAAAA00000000000000000000000000000000aaaa");
        assert!(can_edit("", Some("0xaaaa00000000000000000000000000000000aaaa")));
        assert!(can_edit("0xbbbb", Some("0xBBBB")));
        assert!(!can_edit("0xbbbb", Some("0xcccc")));
        assert!(!can_edit("", Some("0xcccc")));
        assert!(!can_edit("0xbbbb", None));
    }
}
