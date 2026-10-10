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
    BoostReserve,
    Positions,
    UserPositions,
}

/// One BlocTime lock (BlocTime.sol `StakePosition`). `registration` marks the
/// lock that registered the subnet; any other account can add a support lock.
#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct BlocPosition {
    pub id: u64,
    pub subnet_id: u32,
    pub staker: AccountId,
    pub amount: U128,
    pub start_seconds: u64,
    pub lock_seconds: u64,
    pub bloctime: U128,
    pub registration: bool,
}

/// Multiplier curve point (BlocTime.sol `Point`), multiplier in bps.
#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct Point {
    pub lock_seconds: u64,
    pub multiplier: u32,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct RegistrationTerms {
    /// BlocTime (µNEAR·seconds) a registration lock must reach.
    pub min_registration_bloctime: U128,
    pub max_lock_seconds: u64,
    /// Sent on to the new subnet account; NOT part of the lock.
    pub account_funding: U128,
    pub points: Vec<Point>,
    /// Score a newcomer must beat when every slot is taken (0 = free slot).
    pub eviction_threshold: U128,
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
    pub bloctime: U128,
    pub is_immune: bool,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct PoolInfoView {
    pub total_shares: U128,
    pub total_bloctime: U128,
    pub boost_reserve: U128,
    pub current_price: U128,
    pub locked_stake: U128,
}
