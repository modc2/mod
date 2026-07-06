use near_sdk::json_types::U128;
use near_sdk::{env, AccountId};

use crate::types::FungibleTokenMetadata;
use crate::Subnet;

impl Subnet {
    // ── NEP-141 Views ───────────────────────────────────────────────────────

    pub fn ft_balance_of_impl(&self, account_id: &AccountId) -> U128 {
        U128(self.balances.get(account_id).copied().unwrap_or(0))
    }

    pub fn ft_total_supply_impl(&self) -> U128 {
        U128(self.total_supply)
    }

    pub fn ft_metadata_impl(&self) -> FungibleTokenMetadata {
        FungibleTokenMetadata {
            spec: "ft-1.0.0".to_string(),
            name: self.token_name.clone(),
            symbol: self.token_symbol.clone(),
            decimals: self.token_decimals,
        }
    }

    // ── Transfer ────────────────────────────────────────────────────────────

    pub fn ft_transfer_impl(&mut self, receiver_id: &AccountId, amount: u128, memo: Option<String>) {
        let sender = env::predecessor_account_id();
        self.internal_transfer(&sender, receiver_id, amount);
        if let Some(m) = memo {
            env::log_str(&format!("Transfer memo: {}", m));
        }
    }

    // ── Internal ───────────────────────────────────────────────────────────

    pub(crate) fn internal_transfer(
        &mut self,
        sender: &AccountId,
        receiver: &AccountId,
        amount: u128,
    ) {
        assert!(amount > 0, "transfer amount must be positive");
        assert_ne!(sender, receiver, "cannot transfer to self");
        let sender_bal = self.balances.get(sender).copied().unwrap_or(0);
        assert!(sender_bal >= amount, "insufficient balance");
        self.balances.insert(sender.clone(), sender_bal - amount);
        let receiver_bal = self.balances.get(receiver).copied().unwrap_or(0);
        self.balances.insert(receiver.clone(), receiver_bal + amount);
    }
}
