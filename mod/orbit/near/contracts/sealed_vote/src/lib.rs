//! SealedVote: Everyone submits a sealed number, nobody can read it, nobody can copy it (the seal is bound to the sender), then everyone opens theirs and the contract checks each seal
//!
//! Commit-reveal: the first rung of the zero-knowledge ladder.
//!   commit  = publish sha256(contract | you | value | salt), nothing else
//!   reveal  = publish value + salt; the contract recomputes and compares
//! Nobody can read a sealed value, nobody can copy yours (your account is
//! inside the hash), and you cannot change your mind afterwards.
//! The Cairo twin of this contract (orbit/cairo, "commit_reveal") does the
//! same with Poseidon, the hash that is cheap inside a ZK proof.
use near_sdk::store::{IterableMap, LookupMap};
use near_sdk::{env, near, require, AccountId, PanicOnDefault};

#[near(serializers = [json, borsh])]
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Phase {
    Commit,
    Reveal,
    Done,
}

#[near(serializers = [json])]
pub struct Summary {
    pub phase: Phase,
    pub committed: u32,
    pub revealed: u32,
    pub sum: u64,
    pub average: Option<u64>,
}

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct SealedVote {
    owner: AccountId,
    phase: Phase,
    seals: LookupMap<AccountId, [u8; 32]>,
    values: IterableMap<AccountId, u64>,
    committed: u32,
    sum: u64,
}

#[near]
impl SealedVote {
    #[init]
    pub fn new() -> Self {
        Self {
            owner: env::predecessor_account_id(),
            phase: Phase::Commit,
            seals: LookupMap::new(b"s".to_vec()),
            values: IterableMap::new(b"v".to_vec()),
            committed: 0,
            sum: 0,
        }
    }

    // ---- views -------------------------------------------------------------

    /// The seal for (this contract, who, value, salt), as hex. A real client
    /// computes this locally; it is a view here so you can try it from the console.
    pub fn seal_of(&self, who: AccountId, value: u64, salt: String) -> String {
        hex(&seal(&env::current_account_id(), &who, value, &salt))
    }

    pub fn has_committed(&self, who: AccountId) -> bool {
        self.seals.contains_key(&who)
    }

    pub fn revealed_value(&self, who: AccountId) -> Option<u64> {
        self.values.get(&who).copied()
    }

    pub fn result(&self) -> Summary {
        let revealed = self.values.len();
        Summary {
            phase: self.phase,
            committed: self.committed,
            revealed,
            sum: self.sum,
            average: if revealed > 0 { Some(self.sum / revealed as u64) } else { None },
        }
    }

    // ---- changes -----------------------------------------------------------

    /// Phase 1: store only the hash. One per account, no do-overs.
    pub fn commit(&mut self, seal: String) {
        require!(self.phase == Phase::Commit, "commits are closed");
        let who = env::predecessor_account_id();
        require!(!self.seals.contains_key(&who), "already committed");
        let bytes = unhex(&seal);
        self.seals.insert(who.clone(), bytes);
        self.committed += 1;
        env::log_str(&format!("EVENT_JSON:{{\"standard\":\"sealed_vote\",\"version\":\"1.0.0\",\"event\":\"commit\",\"data\":[{{\"who\":\"{}\"}}]}}", who));
    }

    /// The owner flips to the reveal phase. (Real systems use block windows.)
    pub fn open_reveals(&mut self) {
        require!(env::predecessor_account_id() == self.owner, "only the owner opens reveals");
        require!(self.phase == Phase::Commit, "already open");
        self.phase = Phase::Reveal;
    }

    /// Phase 2: prove the value you sealed earlier. Mismatch = rejected.
    pub fn reveal(&mut self, value: u64, salt: String) {
        require!(self.phase == Phase::Reveal, "reveals are not open");
        let who = env::predecessor_account_id();
        let sealed = self.seals.get(&who).expect("you never committed");
        require!(!self.values.contains_key(&who), "already revealed");
        let expected = seal(&env::current_account_id(), &who, value, &salt);
        require!(*sealed == expected, "seal does not match: wrong value or salt");
        self.values.insert(who.clone(), value);
        self.sum += value;
        env::log_str(&format!("EVENT_JSON:{{\"standard\":\"sealed_vote\",\"version\":\"1.0.0\",\"event\":\"reveal\",\"data\":[{{\"who\":\"{}\",\"value\":{}}}]}}", who, value));
    }

    pub fn finish(&mut self) {
        require!(env::predecessor_account_id() == self.owner, "only the owner finishes");
        self.phase = Phase::Done;
    }
}

/// sha256(contract | who | value | salt): the contract and account inside the
/// hash make a seal useless anywhere else or for anyone else.
fn seal(contract: &AccountId, who: &AccountId, value: u64, salt: &str) -> [u8; 32] {
    let msg = format!("{}|{}|{}|{}", contract, who, value, salt);
    env::sha256_array(msg.as_bytes())
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{:02x}", x)).collect()
}

fn unhex(s: &str) -> [u8; 32] {
    let s = s.trim().trim_start_matches("0x");
    require!(s.len() == 64, "seal must be 64 hex chars (sha256)");
    let mut out = [0u8; 32];
    for i in 0..32 {
        out[i] = u8::from_str_radix(&s[2 * i..2 * i + 2], 16).expect("seal is not hex");
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
            .current_account_id("game.testnet".parse().unwrap())
            .predecessor_account_id(who.parse().unwrap())
            .build();
        testing_env!(ctx);
    }

    fn alice() -> AccountId { "alice.testnet".parse().unwrap() }
    fn bob() -> AccountId { "bob.testnet".parse().unwrap() }

    #[test]
    fn commit_then_reveal_matches() {
        as_user("owner.testnet");
        let mut c = SealedVote::new();
        let s = c.seal_of(alice(), 7, "pepper".into());
        as_user("alice.testnet");
        c.commit(s);
        assert!(c.has_committed(alice()));
        as_user("owner.testnet");
        c.open_reveals();
        as_user("alice.testnet");
        c.reveal(7, "pepper".into());
        let r = c.result();
        assert_eq!((r.committed, r.revealed, r.sum, r.average), (1, 1, 7, Some(7)));
    }

    #[test]
    #[should_panic(expected = "seal does not match")]
    fn wrong_value_is_rejected() {
        as_user("owner.testnet");
        let mut c = SealedVote::new();
        let s = c.seal_of(alice(), 7, "pepper".into());
        as_user("alice.testnet");
        c.commit(s);
        as_user("owner.testnet");
        c.open_reveals();
        as_user("alice.testnet");
        c.reveal(8, "pepper".into());
    }

    #[test]
    #[should_panic(expected = "seal does not match")]
    fn copied_seal_is_useless_to_the_copier() {
        as_user("owner.testnet");
        let mut c = SealedVote::new();
        let alices = c.seal_of(alice(), 7, "pepper".into());
        as_user("bob.testnet");
        c.commit(alices); // bob copies alice's seal
        as_user("owner.testnet");
        c.open_reveals();
        as_user("bob.testnet");
        c.reveal(7, "pepper".into()); // but the hash has alice inside
        let _ = bob();
    }

    #[test]
    #[should_panic(expected = "commits are closed")]
    fn no_late_commits() {
        as_user("owner.testnet");
        let mut c = SealedVote::new();
        c.open_reveals();
        as_user("alice.testnet");
        c.commit("00".repeat(32));
    }
}
