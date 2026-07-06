use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct ConsensusState {
    #[serde(rename = "currentBlock")]
    pub current_block: u64,
    #[serde(rename = "lastEmissionBlock")]
    pub last_emission_block: u64,
    #[serde(rename = "totalBlocktime")]
    pub total_blocktime: u64,
    #[serde(rename = "emissionRate")]
    pub emission_rate: String,
    #[serde(rename = "epochLength")]
    pub epoch_length: u64,
    #[serde(rename = "decayBps")]
    pub decay_bps: u64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ValidatorInfo {
    pub key: String,
    #[serde(rename = "keyHash")]
    pub key_hash: String,
    #[serde(rename = "keyType")]
    pub key_type: u64,
    #[serde(rename = "registeredBlock")]
    pub registered_block: u64,
    #[serde(rename = "commissionBps")]
    pub commission_bps: u64,
    pub active: bool,
    #[serde(rename = "lastSeenBlock")]
    pub last_seen_block: u64,
    #[serde(rename = "blocktimeScore")]
    pub blocktime_score: u64,
    pub earned: String,
    pub balance: String,
    #[serde(rename = "totalSTT")]
    pub total_stt: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StakePosition {
    #[serde(rename = "stakeId")]
    pub stake_id: u64,
    pub staker: String,
    #[serde(rename = "validatorKeyHash")]
    pub validator_key_hash: String,
    pub amount: String,
    #[serde(rename = "startBlock")]
    pub start_block: u64,
    #[serde(rename = "lockBlocks")]
    pub lock_blocks: u64,
    #[serde(rename = "mintedBalance")]
    pub minted_balance: String,
    #[serde(rename = "blocksRemaining")]
    pub blocks_remaining: u64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct SubnetInfo {
    pub id: u64,
    pub owner: String,
    pub name: String,
    pub subnet: String,
    pub staking: String,
    pub consensus: String,
    #[serde(rename = "registeredBlock")]
    pub registered_block: u64,
    pub active: bool,
    #[serde(rename = "stakeScore")]
    pub stake_score: String,
    pub immune: bool,
    #[serde(rename = "totalShares")]
    pub total_shares: Option<String>,
    #[serde(rename = "totalBloctime")]
    pub total_bloctime: Option<String>,
    #[serde(rename = "sharePrice")]
    pub share_price: Option<String>,
    #[serde(rename = "lockedGov")]
    pub locked_gov: Option<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct WeakestSubnet {
    pub id: u64,
    pub score: String,
    pub found: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct PoolInfo {
    #[serde(rename = "totalShares")]
    pub total_shares: String,
    #[serde(rename = "totalBloctime")]
    pub total_bloctime: String,
    #[serde(rename = "sharePrice")]
    pub share_price: String,
    #[serde(rename = "lockedGov")]
    pub locked_gov: String,
    #[serde(rename = "stakeScore")]
    pub stake_score: String,
    #[serde(rename = "bloctimePrice")]
    pub bloctime_price: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TxResult {
    pub success: bool,
    pub tx_hash: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct DeployInfo {
    pub subnet: Option<String>,
    #[serde(rename = "stakeTime")]
    pub stake_time: Option<String>,
    pub staking: Option<String>,
    pub consensus: Option<String>,
    #[serde(rename = "governanceToken")]
    pub governance_token: Option<String>,
    pub registry: Option<String>,
    #[serde(rename = "chainId")]
    pub chain_id: Option<u64>,
}
