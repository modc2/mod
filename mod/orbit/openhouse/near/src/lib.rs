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
//!
//! Post-quantum door: ed25519 falls to Shor's algorithm, keccak does not.
//! `submit_pq` takes the same fact signed with OHX-KECCAK — a stateful
//! hash-based signature (WOTS+ under a 2×8 Merkle hypertree, keccak only;
//! byte-for-byte twin of pq.py and contracts/OHX.sol). `require_pq` shuts the
//! ed25519 door for good. PQ state lives under its own storage prefixes, not
//! in the contract struct, so this upgrade deploys over the old state.
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
    PqBanks,
    PqLeaves,
}

const PQ_REQUIRED: &[u8] = b"pq_required";

/// A bank's OHX-KECCAK key, keyed by its id keccak(pub_seed ‖ root).
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct PqBank {
    pub name: String,
    pub active: bool,
    pub since: u64,
    pub pub_seed: String,
    pub root: String,
}

/// OHX-KECCAK verification. See pq.py for the spec.
pub mod ohx {
    use near_sdk::env;

    pub const N: usize = 32;
    pub const W: u64 = 16;
    pub const L: usize = 67;
    pub const H: usize = 8;
    pub const D: usize = 2;
    pub const CAPACITY: u64 = 1 << (H * D);
    pub const SIG_BYTES: usize = 4 + D * (L * N + H * N);
    const T_CHAIN: u64 = 0;
    const T_LEAF: u64 = 1;
    const T_NODE: u64 = 2;
    const T_MSG: u64 = 4;

    pub fn adrs(layer: u64, tree: u64, typ: u64, a: u64, b: u64, c: u64) -> [u8; 32] {
        let mut w = [0u8; 32];
        w[0..4].copy_from_slice(&(layer as u32).to_be_bytes());
        w[4..12].copy_from_slice(&tree.to_be_bytes());
        w[12..16].copy_from_slice(&(typ as u32).to_be_bytes());
        w[16..20].copy_from_slice(&(a as u32).to_be_bytes());
        w[20..24].copy_from_slice(&(b as u32).to_be_bytes());
        w[24..28].copy_from_slice(&(c as u32).to_be_bytes());
        w
    }

    fn k(parts: &[&[u8]]) -> [u8; 32] {
        let mut buf = Vec::with_capacity(parts.iter().map(|p| p.len()).sum());
        for p in parts {
            buf.extend_from_slice(p);
        }
        let mut out = [0u8; 32];
        out.copy_from_slice(&env::keccak256(&buf));
        out
    }

    pub fn key_id(pub_seed: &[u8; 32], root: &[u8; 32]) -> [u8; 32] {
        k(&[pub_seed, root])
    }

    pub fn leaf_of(sig: &[u8]) -> Option<u64> {
        if sig.len() != SIG_BYTES {
            return None;
        }
        let i = u32::from_be_bytes([sig[0], sig[1], sig[2], sig[3]]) as u64;
        if i < CAPACITY { Some(i) } else { None }
    }

    pub fn verify(ps: &[u8; 32], root: &[u8; 32], msg: &[u8; 32], sig: &[u8]) -> bool {
        let mut t = match leaf_of(sig) {
            Some(i) => i,
            None => return false,
        };
        let mut node = *msg;
        let mut off = 4usize;
        for layer in 0..D as u64 {
            let kp = t & ((1 << H) - 1);
            let tree = t >> H;
            let dg = k(&[ps, &adrs(layer, tree, T_MSG, kp, 0, 0), &node]);
            let mut ds = [0u64; L];
            let mut csum = 0u64;
            for i in 0..32 {
                ds[2 * i] = (dg[i] >> 4) as u64;
                ds[2 * i + 1] = (dg[i] & 15) as u64;
            }
            for d in ds.iter().take(64) {
                csum += W - 1 - d;
            }
            ds[64] = (csum >> 8) & 15;
            ds[65] = (csum >> 4) & 15;
            ds[66] = csum & 15;
            let mut leaf_pre = Vec::with_capacity(64 + L * N);
            leaf_pre.extend_from_slice(ps);
            leaf_pre.extend_from_slice(&adrs(layer, tree, T_LEAF, kp, 0, 0));
            for ch in 0..L {
                let mut x = [0u8; 32];
                x.copy_from_slice(&sig[off + ch * N..off + (ch + 1) * N]);
                for j in ds[ch]..W - 1 {
                    x = k(&[ps, &adrs(layer, tree, T_CHAIN, kp, ch as u64, j), &x]);
                }
                leaf_pre.extend_from_slice(&x);
            }
            off += L * N;
            let mut cur = k(&[&leaf_pre]);
            let mut i = kp;
            for h in 0..H {
                let sib = &sig[off + h * N..off + (h + 1) * N];
                let left = i % 2 == 0;
                i >>= 1;
                let a = adrs(layer, tree, T_NODE, 0, h as u64 + 1, i);
                cur = if left { k(&[ps, &a, &cur, sib]) } else { k(&[ps, &a, sib, &cur]) };
            }
            off += H * N;
            node = cur;
            t = tree;
        }
        node == *root
    }
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
        let why = match self.check(&o).and_then(|_| self.classical_open()).and_then(|_| self.signed_by(&f, &signature, &bank)) {
            Ok(()) => String::new(),
            Err(e) => e,
        };
        Verdict { ok: why.is_empty(), why, fact: to_hex(&f) }
    }

    // ── the door ───────────────────────────────────────────────

    /// The bank attested: it ran the program and signed the fact. Anyone may relay.
    pub fn submit(&mut self, outputs: Vec<String>, signature: String, bank: String) -> Receipt {
        self.classical_open().unwrap_or_else(|e| env::panic_str(&e));
        let o = parse_outputs(&outputs).unwrap_or_else(|e| env::panic_str(&e));
        let (pid, period, due) = self.check(&o).unwrap_or_else(|e| env::panic_str(&e));
        let f = self.fact_bytes(&o);
        self.signed_by(&f, &signature, &bank).unwrap_or_else(|e| env::panic_str(&e));
        self.record(&o, &f, pid, period, due, bank, "attested")
    }

    fn record(&mut self, o: &[[u8; 32]; N_OUTPUTS], f: &[u8; 32], pid: u64, period: u64, due: u128,
              bank: String, kind: &str) -> Receipt {
        let r = Receipt {
            property_id: pid,
            period,
            commitment: to_hex(&o[4]),
            nullifier: to_hex(&o[5]),
            fact: to_hex(f),
            bank: bank.clone(),
            rent_due: U128(due),
            at: env::block_timestamp_ms(),
            block: env::block_height(),
            kind: kind.into(),
        };
        self.spent.insert(o[5]);
        self.receipts.insert(receipt_key(pid, period), r.clone());
        self.count += 1;
        emit(
            "rent_proven",
            &format!(
                "{{\"property_id\":{},\"period\":{},\"commitment\":\"{}\",\"nullifier\":\"{}\",\"fact\":\"{}\",\"bank\":\"{}\",\"kind\":\"{}\"}}",
                pid, period, r.commitment, r.nullifier, r.fact, bank, kind
            ),
        );
        r
    }

    // ── the post-quantum door ──────────────────────────────────

    fn pq_banks() -> LookupMap<String, PqBank> {
        LookupMap::new(Key::PqBanks)
    }

    fn pq_leaves() -> LookupSet<String> {
        LookupSet::new(Key::PqLeaves)
    }

    fn classical_open(&self) -> Result<(), String> {
        if env::storage_has_key(PQ_REQUIRED) {
            return Err("classical door closed: post-quantum signature required".into());
        }
        Ok(())
    }

    /// Seat (or deactivate) a bank's OHX-KECCAK key. Returns its id, "0x…".
    pub fn set_pq_bank(&mut self, pub_seed: String, root: String, name: String, active: bool) -> String {
        self.only_owner();
        let ps = word(&pub_seed).unwrap_or_else(|e| env::panic_str(&e));
        let rt = word(&root).unwrap_or_else(|e| env::panic_str(&e));
        require!(ps != [0u8; 32] && rt != [0u8; 32], "pq key required");
        let id = to_hex(&ohx::key_id(&ps, &rt));
        let mut m = Self::pq_banks();
        let since = m.get(&id).map(|b| b.since).unwrap_or(env::block_timestamp_ms());
        m.insert(id.clone(), PqBank { name: name.clone(), active, since, pub_seed: to_hex(&ps), root: to_hex(&rt) });
        m.flush();
        emit("pq_bank_set", &format!("{{\"key\":\"{}\",\"name\":{:?},\"active\":{}}}", id, name, active));
        id
    }

    /// Q-day switch. One way: no method turns the ed25519 door back on.
    pub fn require_pq(&mut self) {
        self.only_owner();
        require!(!env::storage_has_key(PQ_REQUIRED), "already required");
        env::storage_write(PQ_REQUIRED, &[1]);
        emit("pq_required", "{}");
    }

    pub fn pq_required(&self) -> bool {
        env::storage_has_key(PQ_REQUIRED)
    }

    pub fn pq_bank(&self, key: String) -> Option<PqBank> {
        Self::pq_banks().get(&key.to_lowercase()).cloned()
    }

    pub fn leaf_used(&self, key: String, leaf: u64) -> bool {
        Self::pq_leaves().contains(&format!("{}:{}", key.to_lowercase(), leaf))
    }

    fn pq_signed(&self, f: &[u8; 32], signature: &str, key: &str) -> Result<u64, String> {
        let key = key.to_lowercase();
        let b = Self::pq_banks().get(&key).cloned().ok_or("not an active post-quantum bank key")?;
        if !b.active {
            return Err("not an active post-quantum bank key".into());
        }
        let bare = signature.trim().trim_start_matches("0x");
        if bare.len() != ohx::SIG_BYTES * 2 {
            return Err("malformed post-quantum signature".into());
        }
        let sig = hex_bytes(bare, ohx::SIG_BYTES).map_err(|_| "malformed post-quantum signature")?;
        let leaf = ohx::leaf_of(&sig).ok_or("malformed post-quantum signature")?;
        if Self::pq_leaves().contains(&format!("{}:{}", key, leaf)) {
            return Err("one-time leaf already used".into());
        }
        let ps = word(&b.pub_seed)?;
        let rt = word(&b.root)?;
        if !ohx::verify(&ps, &rt, f, &sig) {
            return Err("bad post-quantum signature".into());
        }
        Ok(leaf)
    }

    /// Dry run of `submit_pq`.
    pub fn verify_pq(&self, outputs: Vec<String>, signature: String, key: String) -> Verdict {
        let o = match parse_outputs(&outputs) {
            Ok(o) => o,
            Err(why) => return Verdict { ok: false, why, fact: String::new() },
        };
        let f = self.fact_bytes(&o);
        let why = match self.check(&o).and_then(|_| self.pq_signed(&f, &signature, &key)) {
            Ok(_) => String::new(),
            Err(e) => e,
        };
        Verdict { ok: why.is_empty(), why, fact: to_hex(&f) }
    }

    /// The bank attested with a hash-based signature — survives a quantum
    /// computer. The one-time leaf is burned here.
    pub fn submit_pq(&mut self, outputs: Vec<String>, signature: String, key: String) -> Receipt {
        let o = parse_outputs(&outputs).unwrap_or_else(|e| env::panic_str(&e));
        let (pid, period, due) = self.check(&o).unwrap_or_else(|e| env::panic_str(&e));
        let f = self.fact_bytes(&o);
        let leaf = self.pq_signed(&f, &signature, &key).unwrap_or_else(|e| env::panic_str(&e));
        let key = key.to_lowercase();
        let mut used = Self::pq_leaves();
        used.insert(format!("{}:{}", key, leaf));
        emit("pq_leaf_spent", &format!("{{\"key\":\"{}\",\"leaf\":{}}}", key, leaf));
        self.record(&o, &f, pid, period, due, format!("ohx:{}", key), "pq")
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

    // OHX-KECCAK vector, tests/vectors/ohx.json (pq.py, sk 0x01*32 pub 0x02*32)
    const PQ_SEED: &str = "0x0202020202020202020202020202020202020202020202020202020202020202";
    const PQ_ROOT: &str = "0xaa5806dded12ec1c43882fb4ea7b689335494092dfaf0f20b1d0813a092aafdf";
    const PQ_ID: &str = "0x84e107356a4c5470a2526979d6443a3d1471cd93a9077ef895544755eeface22";
    const PQ_SIG: &str = "0x00000101e7370e3c1a0b652719c596d89b19fdb699c484cbe680c89c0fe02807c62f7c543d83acdf6015bad563f8661d5d98acce3634e91ea65c7daa879b39d443f5cbf2a59023416a56d3fd3de9f2eeee6f68e9f346bc8d79c4b9b88db39e91bebbead83ace6a6a05aa7ec631e76980765eaf165113c72b9b04a617b0d93c9566742ae63a45466c3602b73b86fa3bee748f3ec3afd2181590bd6e4e9c7ee2b6545c4d1332f914df111663c417bd868e6de23dca5b0f91b0cea54bcc1a9e7b09787dc7a469b3752c5d9f04b0b24e338d319f36ef2717c01dd3bd0183ce005a1444d19339968646f20e5b97944c5ec087174520770172df5f24e6e9b783b61f79d3d800f2a09dc1eefeef3249441cdb69ced2407ad2f64c206c39f8c583ad336d1f17c82e04a2143f059688f26d660e891feb74aab1bb94299f44d4dc5a08bfe1f575a620072f9b9bc5f8aac2f6f10624cf6050b965fcf9fbec42e03b4626570d46c552fa0df755d76007d6c7576d379d103a488996b915a6ad2674a6adfdfa733045eb5981bf0fc3463352bc18c6da312aa23c7dfd57b8b49234de451640ffb83d9ccfde2d2e27712f5e11398bf3664d4dd8633ef39ff89629dd414f8e9760f67998841998d69f60dbde29e2aaceb30797db9fde60c85847168bebe0095ec40f1cadb54a3c662725280f531642aaac4ae6678adbf200ee50a4275050a68109acb25023828d142bcd2b7de88bbf673ff2d0ac79b6f4e8c348be3742e8bcf74845c42edd3b23ff79efe061c2b3607314e9b83fed800a0c51a8404b7e1a1f4af64733c4c5977dde4dd3e3f456b2735291fe19f3d128de86be2265abc2777968e1f5e5c19a624ba32a8b1911c690b61e5773146c9f5192c9a5e91248bc19cdb7170af25be7594cb9eec4daabca2c21d2cfbf391da2a3153b9cabf83b125f05cea6fe8b72bb416c95ddabc71558ade509492843313375758e341e23103f04163f8e1cd792bd440bbdd5dbfbdcb21053334c19d9ec441ca50cd31856b52e481e2fe3a280c1e0f840bfa5a02c046e5862c034295d4db60c8155018bd4eb996c24a16b5fd83771d51af54c49bf01c2497747eb4b492965c2232469befc77d3c045262f05cabc58941af2ea8ef9ed5d740d49559260825c5e2798baa04ef6cc3d822aa9f804574c1f2549fd769aaec7061c0960590d887bd1217dfe04ae00dc11d28f6a9cb094d5265707881ed2d74a4ae80e5bfbedceaacc70a23099c35a232b024df28498ba1499e5d8ac32aa5a6e4d2b7d40978e9944e863808b7293404acc2fcf0c0773b7512c75344ed37ff58cb7fe8e0bca8f8ad093437dc1962746a2f97a7b064af5ab58ae5665aeb7b590f97b2e6d88cb87120ab2fb6b440189bdb88fc30058e1b6bd722baddc45c2cbdd47e7bc23738bad903bf7a18084e8d36d28c9c13a34d7ca533130b933132f54c63b071205558206eeb1ac3e1b30c5586b44d424401d30504adb672659be8e892d31356d2b5f476a1eb3b9a3ca828a2e2ef4afa052c13e4d63f06363d8a351c15080deb63261e8bd7e73a078d1adf5d02dda5e4335fa3a74ae086005f2fcf114b2b2e281873530404dd7bf11e89bf1f476fb4837b1f7ad4523538cd4f634077256bbdddea8d2303832e9e4d4fb6e34e3deee0193a1b19281edde8597d9e4ca89714c16517e43326841b4a184e85a6d26f57404316ac40a0466d1bcd24457b23c0614ad6ea29fe3976b8f825ffc241162e1ff75e2d4bc8513f143caa1d6c42d7312fe1eaa93e07b8549d58a7c8d7b83a37d342d2f018a9a07a1ba0825207ed3b0471a29ef970c47d5e8a3b321412c03ad99a42511a373646be3b9542adfdc9a0a9455d9fcdc8b7583d413d21dd0ea7d8ef4ac9f9d296b674e36b53e176ea290587a7f3997f845b81ea6bd2f5bb0434c4ad3194a3db3b93e9f1749041472fa9a034747f02fa15de342b8d86860a28ea2b81ff2d147c48927cf8bbe19adf996b52425a0c6a79e2ee68f8cd56c7803db3ee7f2fd2d4315129cb96ff72ba3a2ce6e3777cdd5c221ba3e43f0402d7788b749a18ed632440fa9e8d1a610d972b397175bc17dc23d7980ba601e71cc4669420e21c895daa94c99b63d344794c451412d8072667cd850d1dbdd97fbe8571dcf74e11115fc66ce0a335170ae488b0021a8a3ef560e9b6c46d1fe858469bbd653f02888fcc17b465379dc8d852ec5869e7f2dcf1a199dbc76ecd40cc83d9967c6127888c0a212e82a2592dfe0bd9e6c9442912bd1f3543cb54bb855feb61959a39e301a99c93d357397619794a689915273634181c69504b316a6ca0875f67655c663e37aea6e9ffdc11455746d4dc3c609601390926b5b3a86829f6e27769f1a229967b38908583a050ff7922dc3e4e39769fec354a17bbae87322503235cfe3f8cb45fca15a0ead832d1255c20fc4243e1101da3ad3228d24f7bcafd03553f508b8eb61dbfbc478bb626441b34ac7787686ea02b0a9b7419eb380fef3fcfa4d4dc4675e9410c073863c0d376df32124759eb4b9f07219e73e32d5e06c2cb840b6ed8dd329ec9ae79d7465bf6a263a2fac1cf7b88ead43df5c096b54eeba2eace24b243dcdc76739f2b5fef8ef601070c01ba5de33189603cbe5d17f4e76f977d668d2e0866e6df29da77e52621b6bec54cff394bcd6122b9eb0d9a3c200d676303518014f9c35db0d5865ad5225172e37410d3eb158fd3a2d506c243c22c74cd928ec9e9f74839c54f5d318dc8508bda468905dddcf597881dd92a945397e446d5a4fadd451540f7d02380db705827de3e66ac657a5d398d528ec535dd2ec9f4c678115f062fafbb1ed3bef1207779b5c3413a2700aca88cb6bdec23def5316ff59490407aedd3056293054bdaeb15ad4a1213f7b0206a610f88cdbabe8a0f574157021d9638beba49d403460a95f4c24fa1f429bfa1c04bebfb72412dcbef61ef47d9dccc73f8656100e2200eb520a0090e496560cafefdc5848f0747bcd34c3680694b2cc1a61ae03add09c33b4a194fec15aec1914851cd8310e85ab36c9cc6bab423c933b94f9661fad5e9fff468f4c6f7c2ea4c7eb92fd31f4eff94b67a95e42bc9a1ff08676a2cdfec14e5c6246823f7166012ded7a8d3659734762dce886f7092a79d0e6a1e46703344b4683b0cda4d029a6bb2c625c638191bd9f4e0fe8507c8e03321fb71eb4780381ecc16f41ddb8671d75c1bf35ac43d74eb8065414c844b4a503dfad91c8d9c955335121e1f73dae06990cff91f936ea9fa8df12894e4cae4183b938fd21bd75a7aaf09f760a7eb43d7a5a6ce12197c681879294be86497bf3abd33329e0cd2a4ea9d41a417819e739f6f704af548cb151e9d8571cd53b0b1f11120329c56e4ce2fb685136300375a0f9b1269357d31140d27c5a18a42ee2ae26517866d076f16d81bc00ba0a939cbff6c4865f52d0c891c465e93957fcfc6571a26ec1bf0935b606cb023ab04c81260360f321c6d78277931d330c7991afe94988488166a44cd6184b6dab0d317ff4bd6112d4761c6b45ee8a3d9e547265a5b00dac8379edb74a759bb5fd24d0eb3e5cb9867374bad70648dcd741d5ebb05332c971560553a1e9c0d4fceb029cd8ea550f9d3ab11a99ec01d0885da0c12646f4a9232fbdf5908ddf0823a0c00bc6fb85caa0918e7465add3eef495b7162fa294baef7be413468c03c643017d5a1d99d0ef378c0bf2556333198030d2ada28ef200ce8974571635a65cbdcf43ff869a8105ae94b056c9902a19c0dc650844e3c4a82c90e960aa0625071e8955423c602d97350e413cc1274c82b80df7d8e4ec66b6387cf9ac1b9616fcc55b962c590ea0c9ed4cb6a6ac34400d58b7f564dcd1bd0bb0c262ef43201f13654535d63e1fee4af5289c0770bd6d08e17e19a0dd2973a05d2afc20e300dacf6758542b3ab9d83caf427210ccf814ff09b1cd6081d309f82e9d866c3cbbe51286eec435d7ff0c6ac6bbe5651dff0b7fb695aec0d8baf9de5099b3f46ee97aa631cb88e15ff98a0f7cdff3c098a50c538b08fc991155bb79fe5d46312abe5baf8b869901b265042b2614e86af142150f016a6e59281ec0acd79c025b6c5a5b8735efd3459607253b51bf0542d802ad85c7d556104a0bcfe13f4f9894d2496101f0a6289b1f7e236b2e57b0370c369b80ba61ec2695790c2ab4df364b684dd98f81e3e4ad77ca484dd17077005cd9df0188a78c4fecd5448cb3cac6bfc6a4c05f7cdf9494e83ea1df0fc17fc88c8a69eb4873d2c44edb4ea68ae412709afcdeaeccf61633a304b88a51cd5a8aa8978cf11c44e2702b550d602bcc0da43f132f1feec3c9be19113b3f745abb008416b13a92de11b12264ab0996a3e93a55b51e36dd79fb537a6d72a6c4af54acb3eaee13ae8e961ab5951d22c8e23cb1930cc10299b1968137efaec2d6c5f6e3cc8ff5d929f21081b3fed3c2c37f41af3a6794bed5064d7549eb09afb6e57c3dbcad2793c01e23a6bce639f02d485443691c807fa7afeca559469cc7a89b03c86605cefbbb7d68acd71f57b23fd9a64bf9ea667eb359add75349da9bda60bd3fed3619355392b66c21b8677aa9631a66779c5c2fe91ab8e2d231460d9e59bcaad727b4fe1f287358dbf6b47dce1e97e5302beefd1e070efb295cb0ca8f3253b09d12b6db98ac10f578f539e3fdbc41d09446c7135dfef9aaa39eac2fbb4b082c3d6e0aaa1bd4fd3455bffe2be660193431cb498cf0c21e4703a631a5348a387c68c84d4fb5b74c1673804ace542f1ff36b668b5ff02c51ad1a36fd0996b97db3e03936ad386d60c4f11c37c615b26aff362f4b9889b3cce38b6dda01ec72992398229e1f8c0c4f9636c544bfdd93366882fe747b6613e91638e11d8f2490ee713dd8429674ce83aa0cef7c1af11d006b6a1dbbd775e8defdf4f6259e69bcae8f26c07e961455419c774837dd62f1ff3b8a488328d0900d47bf95c23dac2c7da003ead77ae6db9b5bad101713a56abc1f501f1c3a14b41eeeee13574d672bcb986c194ec87a028742c1b71cacfd7f653f3ec86fa450def247e712a01b1888b72936bed0ea44b8031e702dd0ccbe5504c716c3d817629cf64474f9ab1b351732320066d4c440b2050e6cff0d060d7625d34e3a82b0f0fc7af4a8c3d5f193eb0c420dcfd452f413a23baf0deef7f671e03edd81bd727ca7bf1a7b18bc9e1becf5fff4f357fcf3f3c5a061f1dcb1d1b978d671deeaf1646ea5bfb3c24b24a9f47401efbeb80103a5fcf457371f513a5c54d1faf2885f684a2f29be81dbc2569c4c0c275b771cd839f4e0ce0afff14428803900277f44eca7e211e0b92db9d7b2d1523fa4369300bbf23a0b1d1380190b37e89e6130f418a12f9a1f4c96ff00aa3c516df512f9a0d3d63717816da1c06f4685dc0446b538062684037710693fd7ea035b654cc164f8f82047f0c03af27351e7878e38a9b422e5bf29fef89387976c005bbd55d06beaf87684dd7fe03dfb2e9ae3a7f17e5e8cafede6baacbe1cf3f9957fea4ee72e7eb525f8c32ed2b56150dde03f43a77614ea64ae64808496cd07b2da4b35ce624a7e4b115a66950929cbfc82550d3eb0d9ad667e7e9684f421ce9352fe615f9e251ea62013f5701edd18bdc17edc380591ab32d55b52287b59dedfa27cdb80713560cc4ae76a1563b6cc0bc4c568e94f8332846bd803d7e4be46d7a6e6b2c5017f1a0aaaa4ebe812a39810c1ad82ebc98a174328f2fbc499666814ee83272b424d3e628771ae23e90e77016b0397637a0abcb8d6a452f80bdec14485de4b230658c7974b188f81c38a1146950662a71d01a3f2f345201d3c5dd5f879bd41b6e53054ac724c1a0bb105957c8db386d0ab4e731c5afd4117e1bc66c6162bfb04a4f56730f1d600c8e4c5b9223ca8639e1e32ab298b13d6c9df35e0b5fa31d6980480546a8f5b3411e2b5f5b9ff9c1c15772e5f5f6938f4baccc9815b7e2f15cb6dacf95aa5b93088907e04ed49fa43925eeceed03d98dd5a2546d05fc1eadd80c8355d9a152e04175a96b07d01e1a12e89fbd23c5c4c06dd1d724cc51a56b7ab38e7a1d0df5f062160416485478c2bdf50ac30bba720a2c4fa965e069202c15e782de94a39bc014dda5246ef4ffc85a87ffad66ea6d9a7630860af97f20d9106f140a2394c1b3b407af85e77a5ae8ff17fb95fd2634205ccdd530863a7c31b334eb104fa723d1a981b4edd960c24f403031f8122ccfc6511bae34323f2ef9b8a2c77d2d2d23399acf6df6995655f7c747bdd1d6eab28c80967d85787a524d19c539763837dcd48672d6e002038704e094eb4f1d96410f4b17c1432cb0d70d3c37a7ff8f58956444e587d9eeb4dea203cc7801ed1be7e34a7023e1f64ef7b24f07aab46c660c9b9a8abb94cffb415ab8144fc30bd4398e1b69fd53b349c068b4ea896eceed7730fd74fc3a0b87b6f889c2986a42ffb2c357cb175e5fc7253f8985d9f0aada682679a01494838339232460afba3e0cd31fe3c12320e52cc452a01fb1d4016c4c40e8050d2e86ff77214fdfb9e2744659a2400014deb2ae5d9045c35f7b3bf46712efa2cf0dd30d7a478c023711d7277a3e396ca2429f7ec0119aed17188475766edb7724eac60aab4648bab54022d529a433233b0f98e547022149c081e58532e31bcbc13fc412d48f18fe447c954a118bc3b35ceaafb1b9f43bdbc165e644606023bc3b0d7b28badb0900562e6a449db1e0fdb5b0430ee7e7997d6e08291c74f3e2b7880777759";
    const PQ_SIG2: &str = "0x00000102c13f65e7fa150414b5879778c2ae440aa869653221d6ee692794199b76a6854f8378ed906950e7457232e3f59afe85c2253b33dffea26665bb7cbad88ea5c088982e94fbb136490bca7daf34073160d3718b0b4e980180267212fcee2f8fdb440721d666ae5274584101a211a5e83b2dc0004fcd5639fd30f52b355875d6a9e2a318b4d5f8f0f5bc39869c28e26d414ddeb0e32d2f56e4e281ec58ada5de85a94c94e2ac421b7fb7da9e9cd31f76e51a5838d321e2dbc2754777d81709b12b33881228bf10ab6e113f9acd179311b00cc6948e1214ebeaa29cf2287067904429803add1bbbd9a683dcae8342b0ab95d3d4788b18ecb1e73216478f597af11e7e9eb37049682c4173c5e5857b52ffa9c1a766569c00283aa39eda93a0e783648ed13d88577b3bb7655edb1681961c4f1ae3737a9e18fc4cf1d0a2ab59c4d25687236ae38b8fe7b1197e504bfb6f598b3866ad1d10c4f8ecf1152011ba6afda1b9c37618699adefac0b3ea111053be38131c211ffde99c13163e8099b0577069901881f55aed81e1bd3167ae64dd8bfee3526b5c8e1644eac974b7ebd9d9dbc0f7109860baeca687416d323e0b98d4bfb9b997f08051756b6cebad66102e169efee6ff1d85833ae000a8c490db6a3012a4ea132575d914b0335732419189beac2c5e6273e825c195dacc9c41bd65fc3708502b9908b1dacd367cd6d9db879c09e6726768e586849b5990c63955eff33fba5d741c71adb344e063992f8e042896556f826e4f5e62d835b7d03b087da5e14fd91313da0f65686f89e6b88dd47af5f7292fd924218aedde49892e5a9b05c7a3cd02674ed03023f236dc6e8220518cb4d8c9782923a355e0a15fbe4a74a222a77a175a6585b5da344d129acf9c17bf3253059361a309e35cc0da53e7ab8b07e1cc68ec1c36c9e8d4688e3c0106ca0e02749c2bb40fbd0f6f8572a3b6a437ae27b1f4884e5d592f05a53415e1bd7da35906bf8f29d4c0897e66df4073407a46547914b8281dba0139caf261651f0945ee4db04615a5154805e24a8603ff7a05ece5ef411ccc208453b9c8cf5b6b7401f832977f4d9bf90c88d7ad8d6511510d750a6d53dad88af11e126d18a8fa8093fd4b6092c81c511048b7caa43c65df60390fc59c61104a2b51a58179310ab0e0a24c7315165e2e62c71881eb8d0d1f3ace15ca8e1a05935761982cc7a20089425631e75b00b7ba23143219d4af43d4f8e1bf1efa1bb50eedc147f49729a84117307369f0a46985929e621e9226ba41982d812f8eba3c73edf56ea9047ffd6b74a3e9085055a7ac871167e09f948ff3ba6afd7c03466ff8ec2b7a8b1d7b8d9db0c15966c06443fbbe5e6673e99e3a2364934bd46bc030d733132b6410ad2725824cf0d5fe81056d738adb678f35c3dae7a2d3dd36964df2a1c3d84b92cffc6d8baaf713a910b365f57734db3788fcbc08a731bddf872a5efbd912e699012ee7e1f42a23acddac36309935781e61ef173fc89332f1ef3435280366d5ad5773b0adb6a27991e9c105908dece575c725be9a11d078c2f8a22ab06b195643d0028bae0d47833a80dfd7eea5af15975702178cbbb9d9806cf5af774f8124d75e79f352f7efd50e6ef9cc81d896e43bd24d351cfd7acc8c8925145449120094e28e6f036746266b9d3511fcfca69a9f5d4764915fe1aa705de5d5e3954a6bc470a256db4da61f42cef91afb1f16ac3acc748eaf5ef9bd53a7f6423e73b7fc50a3202f9d19ee8fbdf623b1013f95d96a0799bacffa0ae74ce4a3097b45e5cd0b3fdb4661a2059182f9813a732e5975033a9e56e4b38e737ee0cab434b17fba0895bf314c45faf82f8c58ad9ece8a29b8ffce8f3d76083ba15e307eca28aacf770c936422c2b9608da2aa7549091ef6532834b5bd9f723c885895180859eed237f853a2d0b61dd69bfdd40a843053d32b5bce471c68a3cf35428dc94dde973b502563f9c872e9151557950b64de4fc0c01e8f5ee46432285ffdef6f6bcc50f4d9f7f2d73feacc3565cd576edb11459ffc3f6b19ea25c3cbfd1998e8ae7a1b92f1d442b142847ca5fc13ddc764d4d67b67c213d187b4e31326cfcd75866fd99a9a79feb7d2b486bd557c5b1dffc77f692522630d2138e38e45a6ac733faae6f4d52b8a647e729f72f8379d6ae831d9bc0d0911fc1903ca445aa3e7a9aca27abcb73df50417caca9e8bdc8fe5b81755f8500c4834b5cf31f0410285f06ee50d5ec5cfd85a51140f184dd945476873d726844c5208af58ec6814ccb3b10b6c01961085c806204b79e67a8fd298151445577891bff75722c2ff1bdeb62fb761aa89c66952c4550b53149674fb5035350d2e39006eb0682cea108451d51fe571aa95a62ea7a6d895ee61e4044ce8a644c13b40a1f013710f7fe3e22a3ab360570ee8acc6845544670ae46ba7e4f28eab6aa9cb602c61b7330fe9473ec5e4ff63961786d3b09d80269d0ef65957ab4ed29fed57cedae7dcd6e89db6cf9a55e3f1a9326ebd7cca3db24c201c6f3cb032a118ebecb7c4328a674e7fcb50bb55906ed481fac45eaafd9baab81e8d2900cd06825b10a7b7375db2531e6d29c977d6c7e14621412bb41a164f70edee46a9511566cbdbe12a5daa585a35290a19319060c722fe6004ec36d4aed28e8e56bfa5d01d363273bccce1cb48248cd8a303526c872f675f18f50fe077eac3900d9f7143d78e7a4b87283faafe258dd05a457f06d7ba015b03d4037298614df1f4a71fd9c8e35d08d34d0d407f20fbcc36512235462df4002158485d65323b13933c3f328c9f9c1a5af91e877e335cdea2522aae87046df3498e033591a197500863ac80cc31ccef469c13083a74ed11f8e06c3a79b75f783e9c200b4be9a2e59d827726af5de677f1408922de4ccdecc3b2b7c27c57a14988559f1e83202d2e061115d6d06f6b0eae7f85062fd9ae7f0cd5f44c0606bbb338bbbf8e384c6502795ea008ea65f75d2f759d9043623679d5762ed7780542a23333e9c328eaecd1e5ae6792bfd17333f5bef9b3da040ebb4a3564851cd5d5e950046a8794b10206b2c9236b932779dc81f870fa35ef92a0b765f691917543233d589db54e5c6246823f7166012ded7a8d3659734762dce886f7092a79d0e6a1e46703344b4683b0cda4d029a6bb2c625c638191bd9f4e0fe8507c8e03321fb71eb4780381ecc16f41ddb8671d75c1bf35ac43d74eb8065414c844b4a503dfad91c8d9c955335121e1f73dae06990cff91f936ea9fa8df12894e4cae4183b938fd21bd75a7aaf09f760a7eb43d7a5a6ce12197c681879294be86497bf3abd33329e0cd2a4ea9d41a417819e739f6f704af548cb151e9d8571cd53b0b1f11120329c56e4ce2fb685136300375a0f9b1269357d31140d27c5a18a42ee2ae26517866d076f16d81bc00ba0a939cbff6c4865f52d0c891c465e93957fcfc6571a26ec1bf0935b606cb023ab04c81260360f321c6d78277931d330c7991afe94988488166a44cd6184b6dab0d317ff4bd6112d4761c6b45ee8a3d9e547265a5b00dac8379edb74a759bb5fd24d0eb3e5cb9867374bad70648dcd741d5ebb05332c971560553a1e9c0d4fceb029cd8ea550f9d3ab11a99ec01d0885da0c12646f4a9232fbdf5908ddf0823a0c00bc6fb85caa0918e7465add3eef495b7162fa294baef7be413468c03c643017d5a1d99d0ef378c0bf2556333198030d2ada28ef200ce8974571635a65cbdcf43ff869a8105ae94b056c9902a19c0dc650844e3c4a82c90e960aa0625071e8955423c602d97350e413cc1274c82b80df7d8e4ec66b6387cf9ac1b9616fcc55b962c590ea0c9ed4cb6a6ac34400d58b7f564dcd1bd0bb0c262ef43201f13654535d63e1fee4af5289c0770bd6d08e17e19a0dd2973a05d2afc20e300dacf6758542b3ab9d83caf427210ccf814ff09b1cd6081d309f82e9d866c3cbbe51286eec435d7ff0c6ac6bbe5651dff0b7fb695aec0d8baf9de5099b3f46ee97aa631cb88e15ff98a0f7cdff3c098a50c538b08fc991155bb79fe5d46312abe5baf8b869901b265042b2614e86af142150f016a6e59281ec0acd79c025b6c5a5b8735efd3459607253b51bf0542d802ad85c7d556104a0bcfe13f4f9894d2496101f0a6289b1f7e236b2e57b0370c369b80ba61ec2695790c2ab4df364b684dd98f81e3e4ad77ca484dd17077005cd9df0188a78c4fecd5448cb3cac6bfc6a4c05f7cdf9494e83ea1df0fc17fc88c8a69eb4873d2c44edb4ea68ae412709afcdeaeccf61633a304b88a51cd5a8aa8978cf11c44e2702b550d602bcc0da43f132f1feec3c9be19113b3f745abb008416b13a92de11b12264ab0996a3e93a55b51e36dd79fb537a6d72a6c4af54acb3eaee13ae8e961ab5951d22c8e23cb1930cc10299b1968137efaec2d6c5f6e3cc8ff5d929f21081b3fed3c2c37f41af3a6794bed5064d7549eb09afb6e57c3dbcad2793c01e23a6bce639f02d485443691c807fa7afeca559469cc7a89b03c86605cefbbb7d68acd71f57b23fd9a64bf9ea667eb359add75349da9bda60bd3fed3619355392b66c21b8677aa9631a66779c5c2fe91ab8e2d231460d9e59bcaad727b4fe1f287358dbf6b47dce1e97e5302beefd1e070efb295cb0ca8f3253b09d12b6db98ac10f578f539e3fdbc41d09446c7135dfef9aaa39eac2fbb4b082c3d6e0aaa1bd4fd3455bffe2be660193431cb498cf0c21e4703a631a5348a387c68c84d4fb5b74c1673804ace542f1ff36b668b5ff02c51ad1a36fd0996b97db3e03936ad386d60c4f11c37c615b26aff362f4b9889b3cce38b6dda01ec72992398229e1f8c0c4f9636c544bfdd93366882fe747b6613e91638e11d8f2490ee713dd8429674ce83aa0cef7c1af11d006b6a1dbbd775e8defdf4f6259e69bcae8f26c07e961455419c774837dd62f1ff3b8a488328d0900d47bf95c23dac2c7da003ead77ae6db9b5bad101713a56abc1f501f1c3a14b41eeeee13574d672bcb986c194ec87a028742c1b71cacfd7f653f3ec86fa450def247e712a01b1888b72936bed0ea44b8031e702dd0ccbe5504c716c3d817629cf64474f9ab1b351732320066d4c440b2050e6cff0d060d7625d34e3a82b0f0fc7af4a8c3d5f193eb0c420dcfd452f413a23baf0deef7f671e03edd81bd727ca7bf1a7b18bc9e1becf5fff4f357fcf3f3c5a061f1dcb1d1b978d671deeaf1646ea5bfb3c24b24a9f47401efbeb80103a5fcf457371f513a5c54d1faf2885f684a2f29be81dbc2569c4c0c275b771cd839f4e0ce0afff14428803900277f44eca7e211e0b92db9d7b2d1523fa4369300bbf23a0b1d1380190b37e89e6130f418a12f9a1f4c96ff00aa3c516df512f9a0d3d63717816da1c06f4685dc0446b538062684037710693fd7ea035b654cc164f8f82047f0c03af27351e7878e38a9b422e5bf29fef89387976c005bbd55d06beaf87684dd7fe03dfb2e9ae3a7f17e5e8cafede6baacbe1cf3f9957fea4ee72e7eb525f8c32ed2b56150dde03f43a77614ea64ae64808496cd07b2da4b35ce624a7e4b115a66950929cbfc82550d3eb0d9ad667e7e9684f421ce9352fe615f9e251ea62013f5701edd18bdc17edc380591ab32d55b52287b59dedfa27cdb80713560cc4ae76a1563b6cc0bc4c568e94f8332846bd803d7e4be46d7a6e6b2c5017f1a0aaaa4ebe812a39810c1ad82ebc98a174328f2fbc499666814ee83272b424d3e628771ae23e90e77016b0397637a0abcb8d6a452f80bdec14485de4b230658c7974b188f81c38a1146950662a71d01a3f2f345201d3c5dd5f879bd41b6e53054ac724c1a0bb105957c8db386d0ab4e731c5afd4117e1bc66c6162bfb04a4f56730f1d600c8e4c5b9223ca8639e1e32ab298b13d6c9df35e0b5fa31d6980480546a8f5b3411e2b5f5b9ff9c1c15772e5f5f6938f4baccc9815b7e2f15cb6dacf95aa5b93088907e04ed49fa43925eeceed03d98dd5a2546d05fc1eadd80c8355d9a152e04175a96b07d01e1a12e89fbd23c5c4c06dd1d724cc51a56b7ab38e7a1d0df5f062160416485478c2bdf50ac30bba720a2c4fa965e069202c15e782de94a39bc014dda5246ef4ffc85a87ffad66ea6d9a7630860af97f20d9106f140a2394c1b3b407af85e77a5ae8ff17fb95fd2634205ccdd530863a7c31b334eb104fa723d1a981b4edd960c24f403031f8122ccfc6511bae34323f2ef9b8a2c77d2d2d23399acf6df6995655f7c747bdd1d6eab28c80967d85787a524d19c539763837dcd48672d6e002038704e094eb4f1d96410f4b17c1432cb0d70d3c37a7ff8f58956444e587d9eeb4dea203cc7801ed1be7e34a7023e1f64ef7b24f07aab46c660c9b9a8abb94cffb415ab8144fc30bd4398e1b69fd53b349c068b4ea896eceed7730fd74fc3a0b87b6f889c2986a42ffb2c357cb175e5fc7253f8985d9f0aada682679a01494838339232460afba3e0cd31fe3c12320e52cc452a01fb1d4016c4c40e8050d2e86ff77214fdfb9e2744659a2400014deb2ae5d9045c35f7b3bf46712efa2cf0dd30d7a478c023711d7277a3e396ca2429f7ec0119aed17188475766edb7724eac60aab4648bab54022d529a433233b0f98e547022149c081e58532e31bcbc13fc412d48f18fe447c954a118bc3b35ceaafb1b9f43bdbc165e644606023bc3b0d7b28badb0900562e6a449db1e0fdb5b0430ee7e7997d6e08291c74f3e2b7880777759";

    fn pq() -> (Proofs, String) {
        let mut p = fresh();
        let id = p.set_pq_bank(PQ_SEED.into(), PQ_ROOT.into(), "Testnet Bank PQ".into(), true);
        (p, id)
    }

    #[test]
    fn pq_key_id_and_vector_match_python() {
        let (_, id) = pq();
        assert_eq!(id, PQ_ID);
        let sig = hex_bytes(PQ_SIG, ohx::SIG_BYTES).unwrap();
        assert!(ohx::verify(&word(PQ_SEED).unwrap(), &word(PQ_ROOT).unwrap(), &word(FACT).unwrap(), &sig));
        let sig2 = hex_bytes(PQ_SIG2, ohx::SIG_BYTES).unwrap();
        assert!(ohx::verify(&word(PQ_SEED).unwrap(), &word(PQ_ROOT).unwrap(), &[0u8; 32], &sig2));
        assert!(!ohx::verify(&word(PQ_SEED).unwrap(), &word(PQ_ROOT).unwrap(), &word(FACT).unwrap(), &sig2));
        let mut bad = sig.clone();
        bad[777] ^= 1;
        assert!(!ohx::verify(&word(PQ_SEED).unwrap(), &word(PQ_ROOT).unwrap(), &word(FACT).unwrap(), &bad));
    }

    #[test]
    fn pq_submit_burns_the_leaf() {
        let (mut p, id) = pq();
        let v = p.verify_pq(vector(), PQ_SIG.into(), id.clone());
        assert!(v.ok, "{}", v.why);
        let r = p.submit_pq(vector(), PQ_SIG.into(), id.clone());
        assert_eq!(r.kind, "pq");
        assert_eq!(r.fact, FACT);
        assert!(p.paid(1, 202610));
        assert!(p.leaf_used(id.clone(), 257));
        let mut o = vector();
        o[2] = "0x31773".into();
        o[5] = "0xd00".into();
        assert_eq!(p.verify_pq(o, PQ_SIG.into(), id).why, "one-time leaf already used");
    }

    #[test]
    fn pq_refusals() {
        let (mut p, id) = pq();
        assert_eq!(p.verify_pq(vector(), PQ_SIG2.into(), id.clone()).why, "bad post-quantum signature");
        assert_eq!(p.verify_pq(vector(), "0x00".into(), id.clone()).why, "malformed post-quantum signature");
        assert_eq!(p.verify_pq(vector(), PQ_SIG.into(), "0x01".into()).why, "not an active post-quantum bank key");
        p.set_pq_bank(PQ_SEED.into(), PQ_ROOT.into(), "off".into(), false);
        assert_eq!(p.verify_pq(vector(), PQ_SIG.into(), id).why, "not an active post-quantum bank key");
    }

    #[test]
    fn require_pq_closes_ed25519_for_good() {
        let (mut p, id) = pq();
        assert!(p.verify(vector(), SIG.into(), BANK.into()).ok);
        p.require_pq();
        assert!(p.pq_required());
        assert_eq!(p.verify(vector(), SIG.into(), BANK.into()).why,
                   "classical door closed: post-quantum signature required");
        assert!(p.verify_pq(vector(), PQ_SIG.into(), id.clone()).ok);
        p.submit_pq(vector(), PQ_SIG.into(), id);
    }

    #[test]
    #[should_panic(expected = "classical door closed")]
    fn classical_submit_refused_after_require_pq() {
        let mut p = fresh();
        p.require_pq();
        p.submit(vector(), SIG.into(), BANK.into());
    }

    #[test]
    #[should_panic(expected = "not owner")]
    fn only_owner_requires_pq() {
        let mut p = fresh();
        ctx(accounts(2));
        p.require_pq();
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
