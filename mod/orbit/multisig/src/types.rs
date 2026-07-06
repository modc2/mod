use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Chain {
    Base,
    Tao,
    Solana,
}

impl std::fmt::Display for Chain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Chain::Base => write!(f, "base"),
            Chain::Tao => write!(f, "tao"),
            Chain::Solana => write!(f, "solana"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum TxStatus {
    Pending,
    Approved,
    Executed,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MultisigWallet {
    pub id: String,
    pub chain: Chain,
    pub name: String,
    pub owners: Vec<String>,
    pub threshold: u32,
    pub address: Option<String>,
    pub safe_version: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Transaction {
    pub id: String,
    pub multisig_id: String,
    pub chain: Chain,
    pub to: String,
    pub value: String,
    pub data: String,
    pub description: String,
    pub nonce: u64,
    pub call_hash: Option<String>,
    pub approvals: Vec<Approval>,
    pub status: TxStatus,
    pub tx_hash: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Approval {
    pub owner: String,
    pub signature: String,
    pub approved_at: String,
}

// --- Request types ---

#[derive(Debug, Deserialize)]
pub struct CreateMultisigReq {
    pub chain: Chain,
    pub name: String,
    pub owners: Vec<String>,
    pub threshold: u32,
    pub address: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ProposeTxReq {
    pub multisig_id: String,
    pub to: String,
    pub value: String,
    #[serde(default)]
    pub data: String,
    #[serde(default)]
    pub description: String,
    pub proposer: String,
    pub signature: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ApproveReq {
    pub owner: String,
    pub signature: String,
}

#[derive(Debug, Deserialize)]
pub struct ExecuteReq {
    pub executor: String,
    pub tx_hash: Option<String>,
}

// --- Response types ---

#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct ApiResponse<T: Serialize> {
    pub ok: bool,
    pub data: T,
}

#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct ErrorResponse {
    pub ok: bool,
    pub error: String,
}

#[allow(dead_code)]
impl ErrorResponse {
    pub fn new(msg: &str) -> Self {
        Self {
            ok: false,
            error: msg.to_string(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct EvmSignData {
    pub safe_address: String,
    pub chain_id: u64,
    pub safe_tx_hash: String,
    pub domain: Eip712Domain,
    pub message: SafeTxMessage,
}

#[derive(Debug, Serialize)]
pub struct Eip712Domain {
    pub chain_id: u64,
    pub verifying_contract: String,
}

#[derive(Debug, Serialize)]
pub struct SafeTxMessage {
    pub to: String,
    pub value: String,
    pub data: String,
    pub operation: u8,
    pub safe_tx_gas: String,
    pub base_gas: String,
    pub gas_price: String,
    pub gas_token: String,
    pub refund_receiver: String,
    pub nonce: u64,
}

#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct SubstrateMultisigAddr {
    pub address: String,
    pub ss58: String,
}

#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct ExecData {
    pub to: String,
    pub data: String,
    pub value: String,
}

#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct BalanceResponse {
    pub address: String,
    pub balance: String,
    pub chain: Chain,
}
