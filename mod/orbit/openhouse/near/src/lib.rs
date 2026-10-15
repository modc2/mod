//! openhouse_proofs — proof of rent paid, without the tenant (NEAR verifier).
//!
//! The bank sees the payment. The chain only needs to know THAT property P
//! was paid for period T. The bank runs `proof/src/lib.cairo` over the
//! private facts (tenant, salt, amount) and publishes six public outputs:
//!
//!   [version, property_id, period, rent_due, commitment, nullifier]
//!
//! commitment = Poseidon(tenant, salt) — opens only to someone holding the
//! salt; nullifier = Poseidon(commitment, property_id, period) — one proof
//! books one month once. This contract never runs Cairo. It checks a FACT,
//! keccak256(program_hash ‖ keccak256(outputs)) — the same fact the Ethereum
//! twin (contracts/OpenHouseProofs.sol) and proofs.py compute — carried by
//! the bank's ed25519 signature, which NEAR verifies natively.
//!
//! Every output is a 32-byte word given as "0x…" hex; the signature is 64
//! bytes hex; the bank is its NEAR public key, "ed25519:<base58>".
use near_sdk::json_types::U128;
use near_sdk::store::{IterableMap, LookupMap, LookupSet};
use near_sdk::{env, near, require, AccountId, BorshStorageKey, PanicOnDefault};

pub const VERSION: u64 = 1;
pub const N_OUTPUTS: usize = 6;

#[derive(BorshStorageKey)]
#[near(serializers = [borsh])]
enum Key {
    Banks,
    Properties,
    Spent,
    Receipts,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct Bank {
    pub name: String,
    pub active: bool,
    pub since: u64,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct Property {
    pub owner: AccountId,
    pub rent_due: U128,
    pub label: String,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct Receipt {
    pub property_id: u64,
    pub period: u64,
    pub commitment: String,
    pub nullifier: String,
    pub fact: String,
    pub bank: String,
    pub rent_due: U128,
    pub at: u64,
    pub block: u64,
    pub kind: String,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct BankView {
    pub key: String,
    pub name: String,
    pub active: bool,
    pub since: u64,
}

/// The answer to `verify`: never panics, says why it would.
#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct Verdict {
    pub ok: bool,
    pub why: String,
    pub fact: String,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Proofs {
    owner: AccountId,
    program_hash: [u8; 32],
    banks: IterableMap<String, Bank>,
    properties: IterableMap<u64, Property>,
    spent: LookupSet<[u8; 32]>,
    receipts: LookupMap<String, Receipt>,
    count: u64,
}

// ── encoding helpers ────────────────────────────────────────────

fn hex_val(c: u8) -> Result<u8, String> {
    match c {
        b'0'..=b'9' => Ok(c - b'0'),
        b'a'..=b'f' => Ok(c - b'a' + 10),
        b'A'..=b'F' => Ok(c - b'A' + 10),
        _ => Err(format!("bad hex digit {:?}", c as char)),
    }
}

/// "0x…" (or bare) hex → bytes, left-padded to `len`.
fn hex_bytes(s: &str, len: usize) -> Result<Vec<u8>, String> {
    let s = s.trim();
    let s = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")).unwrap_or(s);
    if s.is_empty() || s.len() > len * 2 {
        return Err(format!("expected up to {} hex bytes", len));
    }
    let padded = if s.len() % 2 == 1 { format!("0{}", s) } else { s.to_string() };
    let b = padded.as_bytes();
    let mut out = vec![0u8; len - b.len() / 2];
    for i in (0..b.len()).step_by(2) {
        out.push(hex_val(b[i])? * 16 + hex_val(b[i + 1])?);
    }
    Ok(out)
}

fn word(s: &str) -> Result<[u8; 32], String> {
    let v = hex_bytes(s, 32)?;
    let mut w = [0u8; 32];
    w.copy_from_slice(&v);
    Ok(w)
}

fn to_hex(b: &[u8]) -> String {
    const D: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(2 + b.len() * 2);
    s.push_str("0x");
    for x in b {
        s.push(D[(x >> 4) as usize] as char);
        s.push(D[(x & 15) as usize] as char);
    }
    s
}

fn word_u64(w: &[u8; 32]) -> Result<u64, String> {
    if w[..24].iter().any(|b| *b != 0) {
        return Err("does not fit u64".into());
    }
    let mut a = [0u8; 8];
    a.copy_from_slice(&w[24..]);
    Ok(u64::from_be_bytes(a))
}

fn word_u128(w: &[u8; 32]) -> Result<u128, String> {
    if w[..16].iter().any(|b| *b != 0) {
        return Err("does not fit u128".into());
    }
    let mut a = [0u8; 16];
    a.copy_from_slice(&w[16..]);
    Ok(u128::from_be_bytes(a))
}

const B58: &[u8; 58] = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

fn b58_decode(s: &str) -> Result<Vec<u8>, String> {
    let mut out: Vec<u8> = Vec::new();
    for ch in s.bytes() {
        let v = B58.iter().position(|c| *c == ch).ok_or("bad base58")? as u32;
        let mut carry = v;
        for b in out.iter_mut().rev() {
            let t = (*b as u32) * 58 + carry;
            *b = (t & 0xff) as u8;
            carry = t >> 8;
        }
        while carry > 0 {
            out.insert(0, (carry & 0xff) as u8);
            carry >>= 8;
        }
    }
    let zeros = s.bytes().take_while(|c| *c == b'1').count();
    let mut res = vec![0u8; zeros];
    res.extend(out);
    Ok(res)
}

/// "ed25519:<base58>" → the 32 raw key bytes.
fn ed25519_key(s: &str) -> Result<[u8; 32], String> {
    let raw = match s.strip_prefix("ed25519:") {
        Some(b) => b58_decode(b)?,
        None => hex_bytes(s, 32)?,
    };
    if raw.len() != 32 {
        return Err("public key must be 32 bytes".into());
    }
    let mut k = [0u8; 32];
    k.copy_from_slice(&raw);
    Ok(k)
}

fn parse_outputs(outputs: &[String]) -> Result<[[u8; 32]; N_OUTPUTS], String> {
    if outputs.len() != N_OUTPUTS {
        return Err("wrong output count".into());
    }
    let mut o = [[0u8; 32]; N_OUTPUTS];
    for (i, s) in outputs.iter().enumerate() {
        o[i] = word(s).map_err(|e| format!("output {}: {}", i, e))?;
    }
    Ok(o)
}

fn receipt_key(property_id: u64, period: u64) -> String {
    format!("{}:{}", property_id, period)
}

#[near]
impl Proofs {
    #[init]
    pub fn new(owner: AccountId, program_hash: String) -> Self {
        let ph = word(&program_hash).unwrap_or_else(|e| env::panic_str(&e));
        Self {
            owner,
            program_hash: ph,
            banks: IterableMap::new(Key::Banks),
            properties: IterableMap::new(Key::Properties),
            spent: LookupSet::new(Key::Spent),
            receipts: LookupMap::new(Key::Receipts),
            count: 0,
        }
    }

    fn only_owner(&self) {
        require!(env::predecessor_account_id() == self.owner, "not owner");
    }

    // ── admin ──────────────────────────────────────────────────

    pub fn transfer_owner(&mut self, next: AccountId) {
        self.only_owner();
        self.owner = next;
    }

    /// Changing the statement changes every fact: old receipts stop verifying.
    pub fn set_program_hash(&mut self, program_hash: String) {
        self.only_owner();
        self.program_hash = word(&program_hash).unwrap_or_else(|e| env::panic_str(&e));
        emit("program_hash_set", &format!("{{\"program_hash\":\"{}\"}}", to_hex(&self.program_hash)));
    }

    /// `bank` is a NEAR public key, "ed25519:<base58>" — the key the bank signs facts with.
    pub fn set_bank(&mut self, bank: String, name: String, active: bool) {
        self.only_owner();
        ed25519_key(&bank).unwrap_or_else(|e| env::panic_str(&e));
        let since = self.banks.get(&bank).map(|b| b.since).unwrap_or(env::block_timestamp_ms());
        self.banks.insert(bank.clone(), Bank { name: name.clone(), active, since });
        emit("bank_set", &format!("{{\"bank\":\"{}\",\"name\":{:?},\"active\":{}}}", bank, name, active));
    }

    pub fn register_property(&mut self, id: u64, owner: AccountId, rent_due: U128, label: String) {
        self.only_owner();
        require!(self.properties.get(&id).is_none(), "property exists");
        require!(rent_due.0 > 0, "rent_due required");
        self.properties.insert(id, Property { owner: owner.clone(), rent_due, label: label.clone() });
        emit("property_registered", &format!("{{\"id\":{},\"owner\":\"{}\",\"rent_due\":\"{}\",\"label\":{:?}}}", id, owner, rent_due.0, label));
    }

    /// The protocol owner or the property's own owner may re-price it.
    pub fn set_rent_due(&mut self, id: u64, rent_due: U128) {
        let p = self.properties.get(&id).unwrap_or_else(|| env::panic_str("unknown property")).clone();
        let who = env::predecessor_account_id();
        require!(who == self.owner || who == p.owner, "not owner");
        require!(rent_due.0 > 0, "rent_due required");
        self.properties.insert(id, Property { rent_due, ..p });
        emit("rent_due_set", &format!("{{\"id\":{},\"rent_due\":\"{}\"}}", id, rent_due.0));
    }

    // ── the fact ───────────────────────────────────────────────

    fn fact_bytes(&self, o: &[[u8; 32]; N_OUTPUTS]) -> [u8; 32] {
        let mut flat = Vec::with_capacity(32 * N_OUTPUTS);
        for w in o {
            flat.extend_from_slice(w);
        }
        let oh = env::keccak256(&flat);
        let mut pre = Vec::with_capacity(64);
        pre.extend_from_slice(&self.program_hash);
        pre.extend_from_slice(&oh);
        let f = env::keccak256(&pre);
        let mut out = [0u8; 32];
        out.copy_from_slice(&f);
        out
    }

    /// keccak256(program_hash ‖ keccak256(outputs)) as "0x…".
    pub fn fact(&self, outputs: Vec<String>) -> String {
        let o = parse_outputs(&outputs).unwrap_or_else(|e| env::panic_str(&e));
        to_hex(&self.fact_bytes(&o))
    }

    /// Every rule but the signature. Ok(()) means submit would take it.
    fn check(&self, o: &[[u8; 32]; N_OUTPUTS]) -> Result<(u64, u64, u128), String> {
        if word_u64(&o[0]) != Ok(VERSION) {
            return Err("wrong statement version".into());
        }
        let pid = word_u64(&o[1]).map_err(|_| "property_id does not fit u64")?;
        let period = word_u64(&o[2]).map_err(|_| "period does not fit u64")?;
        let due = word_u128(&o[3]).map_err(|_| "rent_due does not fit u128")?;
        let p = self.properties.get(&pid).ok_or("unknown property")?;
        if due < p.rent_due.0 {
            return Err("attested rent due below the property's".into());
        }
        if o[4] == [0u8; 32] {
            return Err("empty commitment".into());
        }
        if self.spent.contains(&o[5]) {
            return Err("nullifier already used".into());
        }
        if self.receipts.get(&receipt_key(pid, period)).is_some() {
            return Err("period already proven".into());
        }
        Ok((pid, period, due))
    }

    fn signed_by(&self, fact: &[u8; 32], signature: &str, bank: &str) -> Result<(), String> {
        let b = self.banks.get(bank).ok_or("signer is not an active bank")?;
        if !b.active {
            return Err("signer is not an active bank".into());
        }
        let pk = ed25519_key(bank)?;
        let sv = hex_bytes(signature, 64)?;
        if sv.len() != 64 {
            return Err("signature must be 64 bytes".into());
        }
        let mut sig = [0u8; 64];
        sig.copy_from_slice(&sv);
        if !env::ed25519_verify(&sig, fact, &pk) {
            return Err("bad signature".into());
        }
        Ok(())
    }

    /// Dry run of `submit`: never panics on bad input, says why it would refuse.
    pub fn verify(&self, outputs: Vec<String>, signature: String, bank: String) -> Verdict {
        let o = match parse_outputs(&outputs) {
            Ok(o) => o,
            Err(why) => return Verdict { ok: false, why, fact: String::new() },
        };
        let f = self.fact_bytes(&o);
        let why = match self.check(&o).and_then(|_| self.signed_by(&f, &signature, &bank)) {
            Ok(()) => String::new(),
            Err(e) => e,
        };
        Verdict { ok: why.is_empty(), why, fact: to_hex(&f) }
    }

    // ── the door ───────────────────────────────────────────────

    /// The bank attested: it ran the program and signed the fact. Anyone may relay.
    pub fn submit(&mut self, outputs: Vec<String>, signature: String, bank: String) -> Receipt {
        let o = parse_outputs(&outputs).unwrap_or_else(|e| env::panic_str(&e));
        let (pid, period, due) = self.check(&o).unwrap_or_else(|e| env::panic_str(&e));
        let f = self.fact_bytes(&o);
        self.signed_by(&f, &signature, &bank).unwrap_or_else(|e| env::panic_str(&e));
        let r = Receipt {
            property_id: pid,
            period,
            commitment: to_hex(&o[4]),
            nullifier: to_hex(&o[5]),
            fact: to_hex(&f),
            bank: bank.clone(),
            rent_due: U128(due),
            at: env::block_timestamp_ms(),
            block: env::block_height(),
            kind: "attested".into(),
        };
        self.spent.insert(o[5]);
        self.receipts.insert(receipt_key(pid, period), r.clone());
        self.count += 1;
        emit(
            "rent_proven",
            &format!(
                "{{\"property_id\":{},\"period\":{},\"commitment\":\"{}\",\"nullifier\":\"{}\",\"fact\":\"{}\",\"bank\":\"{}\"}}",
                pid, period, r.commitment, r.nullifier, r.fact, bank
            ),
        );
        r
    }

    // ── reads ──────────────────────────────────────────────────

    pub fn owner(&self) -> AccountId {
        self.owner.clone()
    }

    pub fn program_hash(&self) -> String {
        to_hex(&self.program_hash)
    }

    pub fn version(&self) -> u64 {
        VERSION
    }

    pub fn count(&self) -> u64 {
        self.count
    }

    pub fn banks(&self) -> Vec<BankView> {
        self.banks
            .iter()
            .map(|(k, b)| BankView { key: k.clone(), name: b.name.clone(), active: b.active, since: b.since })
            .collect()
    }

    pub fn property(&self, id: u64) -> Option<Property> {
        self.properties.get(&id).cloned()
    }

    pub fn properties(&self) -> Vec<(u64, Property)> {
        self.properties.iter().map(|(k, p)| (*k, p.clone())).collect()
    }

    pub fn receipt(&self, property_id: u64, period: u64) -> Option<Receipt> {
        self.receipts.get(&receipt_key(property_id, period)).cloned()
    }

    pub fn paid(&self, property_id: u64, period: u64) -> bool {
        self.receipts.get(&receipt_key(property_id, period)).is_some()
    }

    pub fn is_spent(&self, nullifier: String) -> bool {
        match word(&nullifier) {
            Ok(w) => self.spent.contains(&w),
            Err(_) => false,
        }
    }
}

/// NEP-297 event line.
fn emit(event: &str, data: &str) {
    env::log_str(&format!(
        "EVENT_JSON:{{\"standard\":\"openhouse_proofs\",\"version\":\"1.0.0\",\"event\":\"{}\",\"data\":[{}]}}",
        event, data
    ));
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::{accounts, VMContextBuilder};
    use near_sdk::testing_env;

    // The cross-language vector shared with proofs.py and test/Proofs.t.sol.
    const PH: &str = "0x1111111111111111111111111111111111111111111111111111111111111111";
    const FACT: &str = "0x3ed1a6f4a58595b7f7aa1d9aa1363211800b07c54f58c0267328cc38276362e3";
    // ed25519 seed 0x01*32, signing FACT (PyNaCl)
    const BANK: &str = "ed25519:AKnL4NNf3DGWZJS6cPknBuEGnVsV4A4m5tgebLHaRSZ9";
    const BANK_HEX: &str = "0x8a88e3dd7409f195fd52db2d3cba5d72ca6709bf1d94121bf3748801b40f6f5c";
    const SIG: &str = "0x2c1b40126713886cf2f378f83f1d6484dfd6ce1aa88f2dc164e2f96b0ff78056b89895c09eadad1c08354ae11e2bb49d7ca164431b26922c12229035b057d30a";

    fn ctx(who: AccountId) {
        testing_env!(VMContextBuilder::new()
            .predecessor_account_id(who)
            .block_timestamp(1_760_000_000_000_000_000)
            .build());
    }

    fn vector() -> Vec<String> {
        vec!["0x1", "0x1", "0x31772", "0x59682f00", "0xabc", "0xdef"].into_iter().map(String::from).collect()
    }

    fn fresh() -> Proofs {
        ctx(accounts(0));
        let mut p = Proofs::new(accounts(0), PH.into());
        p.set_bank(BANK.into(), "Testnet Bank".into(), true);
        p.register_property(1, accounts(1), U128(1_500_000_000), "128 Maple Ave".into());
        p
    }

    #[test]
    fn base58_decodes_the_bank_key() {
        assert_eq!(to_hex(&ed25519_key(BANK).unwrap()), BANK_HEX);
    }

    #[test]
    fn fact_matches_python_and_solidity() {
        let p = fresh();
        assert_eq!(p.fact(vector()), FACT);
    }

    #[test]
    fn python_signed_receipt_submits() {
        let mut p = fresh();
        let v = p.verify(vector(), SIG.into(), BANK.into());
        assert!(v.ok, "{}", v.why);
        let r = p.submit(vector(), SIG.into(), BANK.into());
        assert_eq!(r.fact, FACT);
        assert_eq!(r.property_id, 1);
        assert_eq!(r.period, 202610);
        assert_eq!(r.rent_due.0, 1_500_000_000);
        assert!(p.paid(1, 202610));
        assert!(p.is_spent("0xdef".into()));
        assert_eq!(p.count(), 1);
        assert_eq!(p.receipt(1, 202610).unwrap().commitment, to_hex(&word("0xabc").unwrap()));
    }

    #[test]
    #[should_panic(expected = "nullifier already used")]
    fn replay_refused() {
        let mut p = fresh();
        p.submit(vector(), SIG.into(), BANK.into());
        p.submit(vector(), SIG.into(), BANK.into());
    }

    #[test]
    fn one_period_one_proof() {
        let mut p = fresh();
        p.submit(vector(), SIG.into(), BANK.into());
        let mut other = vector();
        other[4] = "0xbeef".into();
        other[5] = "0xfeed".into();
        assert_eq!(p.verify(other, SIG.into(), BANK.into()).why, "period already proven");
    }

    #[test]
    #[should_panic(expected = "bad signature")]
    fn tampered_outputs_fail_the_signature() {
        let mut p = fresh();
        let mut o = vector();
        o[2] = "0x31773".into(); // next month, same signature
        p.submit(o, SIG.into(), BANK.into());
    }

    #[test]
    fn stranger_and_deactivated_bank_refused() {
        let mut p = fresh();
        let other = "ed25519:4rPM1ZYG6ySWzTtQ8rNXC6gCB7M5rkJx1yJLsbEKTq3q";
        assert_eq!(p.verify(vector(), SIG.into(), other.into()).why, "signer is not an active bank");
        p.set_bank(BANK.into(), "Testnet Bank".into(), false);
        assert_eq!(p.verify(vector(), SIG.into(), BANK.into()).why, "signer is not an active bank");
    }

    #[test]
    fn rules_before_signature() {
        let mut p = fresh();
        let mut o = vector();
        o[3] = "0x59682eff".into();
        assert_eq!(p.verify(o, SIG.into(), BANK.into()).why, "attested rent due below the property's");
        let mut o = vector();
        o[1] = "0x9".into();
        assert_eq!(p.verify(o, SIG.into(), BANK.into()).why, "unknown property");
        let mut o = vector();
        o[0] = "0x2".into();
        assert_eq!(p.verify(o, SIG.into(), BANK.into()).why, "wrong statement version");
        assert_eq!(p.verify(vec!["0x1".into()], SIG.into(), BANK.into()).why, "wrong output count");
        assert_eq!(p.verify(vector(), "0xzz".into(), BANK.into()).why, "bad hex digit 'z'");
        // rotating the program hash makes the old fact a different fact
        p.set_program_hash(format!("0x{}", "22".repeat(32)));
        assert_eq!(p.verify(vector(), SIG.into(), BANK.into()).why, "bad signature");
    }

    #[test]
    fn over_attested_rent_due_is_fine() {
        let mut p = fresh();
        p.set_rent_due(1, U128(1_000_000_000));
        let r = p.submit(vector(), SIG.into(), BANK.into());
        assert_eq!(r.rent_due.0, 1_500_000_000, "as attested, not the property's");
    }

    #[test]
    #[should_panic(expected = "not owner")]
    fn only_owner_sets_banks() {
        let mut p = fresh();
        ctx(accounts(2));
        p.set_bank(BANK.into(), "x".into(), true);
    }

    #[test]
    fn property_owner_reprices() {
        let mut p = fresh();
        ctx(accounts(1));
        p.set_rent_due(1, U128(1_600_000_000));
        assert_eq!(p.property(1).unwrap().rent_due.0, 1_600_000_000);
        assert_eq!(p.properties().len(), 1);
        assert_eq!(p.banks().len(), 1);
    }

    #[test]
    #[should_panic(expected = "not owner")]
    fn stranger_cannot_reprice() {
        let mut p = fresh();
        ctx(accounts(3));
        p.set_rent_due(1, U128(1));
    }
}
