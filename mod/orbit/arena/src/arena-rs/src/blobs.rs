//! Content-addressed blob store — the storage half of the layer.
//!
//! A module's id *is* the SHA-256 of its bytes, so uploading the same wasm
//! twice is idempotent and a caller can verify what it got without trusting
//! us. Blobs live off-tree under ~/.mod/arena/blobs/<hash>.wasm; the repo
//! carries the example pack and nothing a user put here.
//!
//! Ids are addressable by any unambiguous prefix of at least 8 hex characters,
//! git-style — nobody wants to type 64 of them.

use sha2::{Digest, Sha256};
use std::fs;
use std::path::PathBuf;

pub const MIN_PREFIX: usize = 8;

pub fn state_dir() -> PathBuf {
    if let Ok(d) = std::env::var("ARENA_STATE") {
        return PathBuf::from(d);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    PathBuf::from(home).join(".mod").join("arena")
}

fn blob_dir() -> PathBuf {
    state_dir().join("blobs")
}

pub fn hash(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

fn path_for(id: &str) -> PathBuf {
    blob_dir().join(format!("{id}.wasm"))
}

/// Store bytes and return their id. Writing an id that already exists is a
/// no-op, not an error — the content is the same by definition.
pub fn put(bytes: &[u8]) -> Result<String, String> {
    let id = hash(bytes);
    let dir = blob_dir();
    fs::create_dir_all(&dir).map_err(|e| format!("blob dir: {e}"))?;
    let path = path_for(&id);
    if path.exists() {
        return Ok(id);
    }
    let tmp = dir.join(format!("{id}.tmp"));
    fs::write(&tmp, bytes).map_err(|e| format!("write blob: {e}"))?;
    fs::rename(&tmp, &path).map_err(|e| format!("commit blob: {e}"))?;
    Ok(id)
}

pub fn get(id: &str) -> Result<Vec<u8>, String> {
    fs::read(path_for(id)).map_err(|e| format!("blob {id}: {e}"))
}

pub fn exists(id: &str) -> bool {
    path_for(id).exists()
}

pub fn remove(id: &str) -> bool {
    fs::remove_file(path_for(id)).is_ok()
}

/// Decode a base64 upload — the only way to carry bytes through a JSON tool
/// call. Whitespace and data: URL prefixes are tolerated because both show up
/// when a human pastes.
pub fn from_base64(text: &str) -> Result<Vec<u8>, String> {
    let body = text.rsplit("base64,").next().unwrap_or(text);
    let mut acc: u32 = 0;
    let mut bits = 0u32;
    let mut out = Vec::with_capacity(body.len() * 3 / 4);
    for c in body.chars() {
        if c.is_whitespace() || c == '=' {
            continue;
        }
        let v = match c {
            'A'..='Z' => c as u32 - 'A' as u32,
            'a'..='z' => c as u32 - 'a' as u32 + 26,
            '0'..='9' => c as u32 - '0' as u32 + 52,
            '+' | '-' => 62,
            '/' | '_' => 63,
            other => return Err(format!("not base64: unexpected character {other:?}")),
        };
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
        }
    }
    Ok(out)
}

pub fn to_base64(bytes: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(TABLE[(n >> 18) as usize & 63] as char);
        out.push(TABLE[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 { TABLE[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if chunk.len() > 2 { TABLE[n as usize & 63] as char } else { '=' });
    }
    out
}

/// Bytes from whatever the caller had to hand: raw base64, a data: URL, or a
/// hex dump. Hex is accepted because `xxd -p` is the fastest way to paste a
/// small module from a terminal.
pub fn decode(text: &str) -> Result<Vec<u8>, String> {
    let t = text.trim();
    let compact: String = t.chars().filter(|c| !c.is_whitespace()).collect();
    let looks_hex = compact.len() >= 8
        && compact.len() % 2 == 0
        && compact.chars().all(|c| c.is_ascii_hexdigit())
        && compact.to_lowercase().starts_with("0061736d");
    if looks_hex {
        return (0..compact.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&compact[i..i + 2], 16).map_err(|e| e.to_string()))
            .collect();
    }
    from_base64(t)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_id_is_the_content() {
        assert_eq!(
            hash(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn base64_round_trips_including_the_ragged_tail() {
        for n in 0..40usize {
            let bytes: Vec<u8> = (0..n).map(|i| (i * 7 + 3) as u8).collect();
            let round = from_base64(&to_base64(&bytes)).expect("decodes");
            assert_eq!(round, bytes, "length {n}");
        }
    }

    #[test]
    fn reads_a_data_url_and_a_hex_dump() {
        let wasm = b"\0asm\x01\x00\x00\x00";
        let url = format!("data:application/wasm;base64,{}", to_base64(wasm));
        assert_eq!(decode(&url).unwrap(), wasm);
        assert_eq!(decode("0061736d01000000").unwrap(), wasm);
        assert_eq!(decode("00 61 73 6d 01 00 00 00").unwrap(), wasm);
    }
}
