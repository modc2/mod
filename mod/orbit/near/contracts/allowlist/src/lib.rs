//! Allowlist: The owner publishes one 32-byte root for a list of thousands of accounts
//!
//! One 32-byte root stands for a whole list. A member proves they are on it
//! with log2(N) hashes. The list never touches the chain. This is the
//! hash-only cousin of a ZK membership proof; the Cairo twin (orbit/cairo,
//! "merkle_allowlist") uses Poseidon so the same proof can go inside a SNARK.
use near_sdk::store::IterableSet;
use near_sdk::{env, near, require, AccountId, PanicOnDefault};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Allowlist {
    owner: AccountId,
    root: [u8; 32],
    claimed: IterableSet<AccountId>,
}

#[near]
impl Allowlist {
    #[init]
    pub fn new(root: String) -> Self {
        Self {
            owner: env::predecessor_account_id(),
            root: unhex(&root),
            claimed: IterableSet::new(b"c".to_vec()),
        }
    }

    // ---- views -------------------------------------------------------------

    pub fn root(&self) -> String {
        hex(&self.root)
    }

    pub fn owner(&self) -> AccountId {
        self.owner.clone()
    }

    /// Is `who` on the list? Free to ask; nothing is recorded.
    pub fn is_allowed(&self, who: AccountId, proof: Vec<String>) -> bool {
        verify(&self.root, &leaf(&who), &proof)
    }

    pub fn claimed(&self, who: AccountId) -> bool {
        self.claimed.contains(&who)
    }

    pub fn claims(&self) -> u32 {
        self.claimed.len()
    }

    // ---- changes -----------------------------------------------------------

    /// Replace the whole list by replacing 32 bytes.
    pub fn set_root(&mut self, root: String) {
        require!(env::predecessor_account_id() == self.owner, "only the owner sets the root");
        self.root = unhex(&root);
    }

    /// Prove membership once. The proof is the sibling hashes from your leaf up.
    pub fn claim(&mut self, proof: Vec<String>) {
        let who = env::predecessor_account_id();
        require!(!self.claimed.contains(&who), "already claimed");
        require!(verify(&self.root, &leaf(&who), &proof), "not on the list (proof does not reach the root)");
        self.claimed.insert(who.clone());
        env::log_str(&format!("EVENT_JSON:{{\"standard\":\"allowlist\",\"version\":\"1.0.0\",\"event\":\"claim\",\"data\":[{{\"who\":\"{}\"}}]}}", who));
    }
}

/// leaf = sha256(account id)
fn leaf(who: &AccountId) -> [u8; 32] {
    env::sha256_array(who.as_bytes())
}

/// parent = sha256(min(a,b) || max(a,b)) so proofs carry no left/right flags
fn parent(a: &[u8; 32], b: &[u8; 32]) -> [u8; 32] {
    let (lo, hi) = if a <= b { (a, b) } else { (b, a) };
    let mut buf = [0u8; 64];
    buf[..32].copy_from_slice(lo);
    buf[32..].copy_from_slice(hi);
    env::sha256_array(&buf)
}

fn verify(root: &[u8; 32], leaf: &[u8; 32], proof: &[String]) -> bool {
    let mut node = *leaf;
    for p in proof {
        node = parent(&node, &unhex(p));
    }
    node == *root
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{:02x}", x)).collect()
}

fn unhex(s: &str) -> [u8; 32] {
    let s = s.trim().trim_start_matches("0x");
    require!(s.len() == 64, "expected 64 hex chars (sha256)");
    let mut out = [0u8; 32];
    for i in 0..32 {
        out[i] = u8::from_str_radix(&s[2 * i..2 * i + 2], 16).expect("not hex");
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::testing_env;

    fn as_user(who: &str) {
        let ctx = VMContextBuilder::new()
            .predecessor_account_id(who.parse().unwrap())
            .build();
        testing_env!(ctx);
    }

    /// A 3-leaf tree built the same way the workshop's `m near/merkle` does:
    /// sorted-pair sha256, odd leaf promoted unchanged.
    fn tree() -> ([u8; 32], Vec<Vec<String>>) {
        let ids = ["alice.testnet", "bob.testnet", "carol.testnet"];
        let l: Vec<[u8; 32]> = ids.iter().map(|s| leaf(&s.parse().unwrap())).collect();
        let ab = parent(&l[0], &l[1]);
        let root = parent(&ab, &l[2]);
        let proofs = vec![
            vec![hex(&l[1]), hex(&l[2])], // alice: sibling bob, then carol
            vec![hex(&l[0]), hex(&l[2])], // bob
            vec![hex(&ab)],               // carol: sibling is the ab node
        ];
        (root, proofs)
    }

    #[test]
    fn members_verify_and_claim_once() {
        as_user("owner.testnet");
        let (root, proofs) = tree();
        let mut c = Allowlist::new(hex(&root));
        assert!(c.is_allowed("alice.testnet".parse().unwrap(), proofs[0].clone()));
        assert!(c.is_allowed("carol.testnet".parse().unwrap(), proofs[2].clone()));
        as_user("bob.testnet");
        c.claim(proofs[1].clone());
        assert!(c.claimed("bob.testnet".parse().unwrap()));
        assert_eq!(c.claims(), 1);
    }

    #[test]
    #[should_panic(expected = "not on the list")]
    fn stranger_is_refused() {
        as_user("owner.testnet");
        let (root, proofs) = tree();
        let mut c = Allowlist::new(hex(&root));
        as_user("mallory.testnet");
        c.claim(proofs[0].clone());
    }

    #[test]
    #[should_panic(expected = "already claimed")]
    fn no_double_claim() {
        as_user("owner.testnet");
        let (root, proofs) = tree();
        let mut c = Allowlist::new(hex(&root));
        as_user("alice.testnet");
        c.claim(proofs[0].clone());
        c.claim(proofs[0].clone());
    }

    #[test]
    fn owner_rotates_root() {
        as_user("owner.testnet");
        let mut c = Allowlist::new("00".repeat(32));
        c.set_root("11".repeat(32));
        assert_eq!(c.root(), "11".repeat(32));
    }
}
