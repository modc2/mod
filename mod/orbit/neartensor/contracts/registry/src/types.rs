use near_sdk::json_types::U128;
use near_sdk::{near, AccountId, BorshStorageKey};

#[derive(BorshStorageKey)]
#[near]
pub enum StorageKey {
    Subnets,
    SubnetIds,
    OwnerSubnets,
    OwnerSubnetsInner { owner_hash: Vec<u8> },
    LockedStake,
    SubnetTotalShares,
    SubnetBloctime,
    UserShares,
    UserSharesInner { subnet_id: u32 },
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct SubnetInfo {
    pub id: u32,
    pub owner: AccountId,
    pub name: String,
    pub account_id: AccountId,
    pub registered_block: u64,
    pub active: bool,
    pub consensus_type: String,
    pub inflation_type: String,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct SubnetParams {
    pub name: String,
    pub token_name: String,
    pub token_symbol: String,
    pub consensus_type: String,
    pub inflation_config: String, // JSON-encoded InflationType
    pub emission_rate: U128,
    pub epoch_length: u64,
    pub decay_bps: Option<u32>,
    pub max_lock_blocks: Option<u64>,
    pub max_stakers_per_validator: Option<u32>,
    pub default_commission_bps: Option<u32>,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct SubnetInfoView {
    pub id: u32,
    pub owner: AccountId,
    pub name: String,
    pub account_id: AccountId,
    pub registered_block: u64,
    pub active: bool,
    pub consensus_type: String,
    pub inflation_type: String,
    pub stake_score: U128,
    pub is_immune: bool,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct PoolInfoView {
    pub total_shares: U128,
    pub total_bloctime: U128,
    pub current_price: U128,
    pub locked_stake: U128,
}
