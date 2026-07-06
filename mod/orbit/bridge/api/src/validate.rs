// Strict input validation. Anything that could be reflected, stored, or hashed
// must pass through these checks first. Reject by length and charset before
// trusting a value.

const MAX_ADDRESS_LEN: usize = 64;
// 64-byte sig = 128 hex chars; allow the optional 0x prefix.
const MAX_SIGNATURE_HEX: usize = 130;

pub fn is_hex(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_hexdigit())
}

pub fn check_signature_hex(sig: &str) -> Result<Vec<u8>, &'static str> {
    let stripped = sig.strip_prefix("0x").unwrap_or(sig);
    if stripped.is_empty() || stripped.len() > MAX_SIGNATURE_HEX {
        return Err("signature length out of range");
    }
    if !is_hex(stripped) {
        return Err("signature must be hex");
    }
    let bytes = hex::decode(stripped).map_err(|_| "signature decode failed")?;
    if bytes.len() != 64 {
        return Err("signature must be 64 bytes");
    }
    Ok(bytes)
}

pub fn check_address(addr: &str) -> Result<&str, &'static str> {
    let trimmed = addr.trim();
    if trimmed.is_empty() {
        return Err("address required");
    }
    if trimmed.len() > MAX_ADDRESS_LEN {
        return Err("address too long");
    }
    // Restrict to printable ASCII so it can't smuggle control chars or be
    // mishandled by downstream tools (logs, JSON, file paths).
    if !trimmed
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err("address must be alphanumeric");
    }
    Ok(trimmed)
}

pub fn check_evm_address(addr: &str) -> Result<String, &'static str> {
    let t = addr.trim();
    if t.len() != 42 || !t.starts_with("0x") {
        return Err("evm_address must be 0x + 40 hex");
    }
    let body = &t[2..];
    if !is_hex(body) {
        return Err("evm_address must be hex");
    }
    Ok(t.to_string())
}

pub fn check_source_type(s: &str) -> Result<&'static str, &'static str> {
    match s {
        "substrate" => Ok("substrate"),
        "solana" => Ok("solana"),
        _ => Err("source_type must be substrate or solana"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_bad_evm() {
        assert!(check_evm_address("0xabc").is_err());
        assert!(check_evm_address("not-an-address").is_err());
        assert!(check_evm_address("0xZZ22a18bcB061B0bd047Db60f5717C8215dC7EeD").is_err());
        assert!(check_evm_address("0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18").is_ok());
    }

    #[test]
    fn rejects_long_address() {
        let long = "x".repeat(200);
        assert!(check_address(&long).is_err());
    }

    #[test]
    fn rejects_addresses_with_control_chars() {
        assert!(check_address("addr\nname").is_err());
        assert!(check_address("../../etc/passwd").is_err());
    }

    #[test]
    fn signature_lengths() {
        assert!(check_signature_hex("").is_err());
        assert!(check_signature_hex("not-hex!!").is_err());
        assert!(check_signature_hex(&"a".repeat(126)).is_err()); // 63 bytes
        assert!(check_signature_hex(&"a".repeat(128)).is_ok()); // 64 bytes
        assert!(check_signature_hex(&format!("0x{}", "a".repeat(128))).is_ok());
    }

    #[test]
    fn source_types() {
        assert_eq!(check_source_type("substrate").unwrap(), "substrate");
        assert_eq!(check_source_type("solana").unwrap(), "solana");
        assert!(check_source_type("ethereum").is_err());
        assert!(check_source_type("").is_err());
    }
}
