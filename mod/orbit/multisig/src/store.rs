use std::path::PathBuf;
use anyhow::Result;
use crate::types::{MultisigWallet, Transaction, TxStatus, Chain};

#[derive(Debug)]
pub struct Store {
    dir: PathBuf,
    pub multisigs: Vec<MultisigWallet>,
    pub transactions: Vec<Transaction>,
}

impl Store {
    pub fn load(data_dir: &str) -> Result<Self> {
        let dir = PathBuf::from(data_dir);
        std::fs::create_dir_all(&dir)?;

        let multisigs = Self::read_json(dir.join("multisigs.json"))?;
        let transactions = Self::read_json(dir.join("transactions.json"))?;

        Ok(Self { dir, multisigs, transactions })
    }

    fn read_json<T: serde::de::DeserializeOwned>(path: PathBuf) -> Result<Vec<T>> {
        if path.exists() {
            let data = std::fs::read_to_string(&path)?;
            Ok(serde_json::from_str(&data).unwrap_or_default())
        } else {
            Ok(Vec::new())
        }
    }

    fn save_multisigs(&self) -> Result<()> {
        let json = serde_json::to_string_pretty(&self.multisigs)?;
        std::fs::write(self.dir.join("multisigs.json"), json)?;
        Ok(())
    }

    fn save_transactions(&self) -> Result<()> {
        let json = serde_json::to_string_pretty(&self.transactions)?;
        std::fs::write(self.dir.join("transactions.json"), json)?;
        Ok(())
    }

    // --- Multisig CRUD ---

    pub fn add_multisig(&mut self, wallet: MultisigWallet) -> Result<MultisigWallet> {
        self.multisigs.push(wallet.clone());
        self.save_multisigs()?;
        Ok(wallet)
    }

    pub fn get_multisig(&self, id: &str) -> Option<&MultisigWallet> {
        self.multisigs.iter().find(|m| m.id == id)
    }

    pub fn get_multisig_mut(&mut self, id: &str) -> Option<&mut MultisigWallet> {
        self.multisigs.iter_mut().find(|m| m.id == id)
    }

    pub fn list_multisigs(&self, chain: Option<&Chain>) -> Vec<&MultisigWallet> {
        match chain {
            Some(c) => self.multisigs.iter().filter(|m| &m.chain == c).collect(),
            None => self.multisigs.iter().collect(),
        }
    }

    pub fn delete_multisig(&mut self, id: &str) -> Result<bool> {
        let before = self.multisigs.len();
        self.multisigs.retain(|m| m.id != id);
        if self.multisigs.len() < before {
            self.transactions.retain(|t| t.multisig_id != id);
            self.save_multisigs()?;
            self.save_transactions()?;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    pub fn update_multisig_address(&mut self, id: &str, address: &str) -> Result<bool> {
        if let Some(m) = self.get_multisig_mut(id) {
            m.address = Some(address.to_string());
            self.save_multisigs()?;
            Ok(true)
        } else {
            Ok(false)
        }
    }

    // --- Transaction CRUD ---

    pub fn add_transaction(&mut self, tx: Transaction) -> Result<Transaction> {
        self.transactions.push(tx.clone());
        self.save_transactions()?;
        Ok(tx)
    }

    pub fn get_transaction(&self, id: &str) -> Option<&Transaction> {
        self.transactions.iter().find(|t| t.id == id)
    }

    pub fn get_transaction_mut(&mut self, id: &str) -> Option<&mut Transaction> {
        self.transactions.iter_mut().find(|t| t.id == id)
    }

    pub fn list_transactions(&self, multisig_id: &str) -> Vec<&Transaction> {
        self.transactions.iter().filter(|t| t.multisig_id == multisig_id).collect()
    }

    pub fn list_pending_transactions(&self, multisig_id: &str) -> Vec<&Transaction> {
        self.transactions
            .iter()
            .filter(|t| t.multisig_id == multisig_id && t.status == TxStatus::Pending)
            .collect()
    }

    pub fn add_approval(
        &mut self,
        tx_id: &str,
        owner: &str,
        signature: &str,
        threshold: u32,
    ) -> Result<Option<&Transaction>> {
        let tx = self.transactions.iter_mut().find(|t| t.id == tx_id);
        if let Some(tx) = tx {
            if tx.status != TxStatus::Pending {
                anyhow::bail!("Transaction is not pending");
            }
            if tx.approvals.iter().any(|a| a.owner == owner) {
                anyhow::bail!("Already approved by this owner");
            }
            tx.approvals.push(crate::types::Approval {
                owner: owner.to_string(),
                signature: signature.to_string(),
                approved_at: chrono::Utc::now().to_rfc3339(),
            });
            if tx.approvals.len() >= threshold as usize {
                tx.status = TxStatus::Approved;
            }
            self.save_transactions()?;
            Ok(self.transactions.iter().find(|t| t.id == tx_id))
        } else {
            Ok(None)
        }
    }

    pub fn set_tx_status(&mut self, tx_id: &str, status: TxStatus, tx_hash: Option<&str>) -> Result<bool> {
        if let Some(tx) = self.get_transaction_mut(tx_id) {
            tx.status = status;
            if let Some(h) = tx_hash {
                tx.tx_hash = Some(h.to_string());
            }
            self.save_transactions()?;
            Ok(true)
        } else {
            Ok(false)
        }
    }
}
