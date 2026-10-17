use near_sdk::json_types::U128;
use near_sdk::store::LookupMap;
use near_sdk::{near, env, AccountId, BorshStorageKey, PanicOnDefault};

// ── Storage Keys ────────────────────────────────────────────────────────────

#[derive(BorshStorageKey)]
#[near]
enum StorageKey {
    Balances,
}

// ── Metadata ────────────────────────────────────────────────────────────────

#[near(serializers = [json])]
#[derive(Clone)]
pub struct FungibleTokenMetadata {
    pub spec: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}

// ── Contract ────────────────────────────────────────────────────────────────

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct GovernanceToken {
    owner: AccountId,
    minter: Option<AccountId>,
    balances: LookupMap<AccountId, u128>,
    total_supply: u128,
    name: String,
    symbol: String,
}

#[near]
impl GovernanceToken {
    #[init]
    pub fn new(name: String, symbol: String) -> Self {
        Self {
            owner: env::predecessor_account_id(),
            minter: None,
            balances: LookupMap::new(StorageKey::Balances),
            total_supply: 0,
            name,
            symbol,
        }
    }

    // ── Admin ───────────────────────────────────────────────────────────────

    pub fn set_minter(&mut self, minter: AccountId) {
        assert_eq!(
            env::predecessor_account_id(),
            self.owner,
            "only owner"
        );
        self.minter = Some(minter);
    }

    pub fn set_owner(&mut self, new_owner: AccountId) {
        assert_eq!(
            env::predecessor_account_id(),
            self.owner,
            "only owner"
        );
        self.owner = new_owner;
    }

    // ── Mint ────────────────────────────────────────────────────────────────

    pub fn mint(&mut self, to: AccountId, amount: U128) {
        let caller = env::predecessor_account_id();
        assert!(
            caller == self.owner || Some(caller) == self.minter,
            "not authorized to mint"
        );
        let bal = self.balances.get(&to).copied().unwrap_or(0);
        self.balances.insert(to, bal + amount.0);
        self.total_supply += amount.0;
    }

    // ── NEP-141 Interface ───────────────────────────────────────────────────

    #[payable]
    pub fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>) {
        assert_eq!(
            env::attached_deposit().as_yoctonear(),
            1,
            "Requires 1 yoctoNEAR"
        );
        let sender = env::predecessor_account_id();
        let sender_bal = self.balances.get(&sender).copied().unwrap_or(0);
        assert!(sender_bal >= amount.0, "insufficient balance");

        self.balances.insert(sender, sender_bal - amount.0);
        let receiver_bal = self
            .balances
            .get(&receiver_id)
            .copied()
            .unwrap_or(0);
        self.balances.insert(receiver_id, receiver_bal + amount.0);

        if let Some(m) = memo {
            env::log_str(&format!("Transfer memo: {}", m));
        }
    }

    pub fn ft_balance_of(&self, account_id: AccountId) -> U128 {
        U128(self.balances.get(&account_id).copied().unwrap_or(0))
    }

    pub fn ft_total_supply(&self) -> U128 {
        U128(self.total_supply)
    }

    pub fn ft_metadata(&self) -> FungibleTokenMetadata {
        FungibleTokenMetadata {
            spec: "ft-1.0.0".to_string(),
            name: self.name.clone(),
            symbol: self.symbol.clone(),
            decimals: 18,
        }
    }

    // ── Views ───────────────────────────────────────────────────────────────

    pub fn get_owner(&self) -> AccountId {
        self.owner.clone()
    }

    pub fn get_minter(&self) -> Option<AccountId> {
        self.minter.clone()
    }
}
