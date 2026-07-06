use near_sdk::json_types::U128;
use near_sdk::{near, AccountId, BorshStorageKey};

// ── Storage Keys ────────────────────────────────────────────────────────────

#[derive(BorshStorageKey)]
#[near]
pub enum StorageKey {
    // Token
    TokenBalances,
    // Staking
    Validators,
    ValidatorKeys,
    StakePositions,
    UserStakeIds,
    UserStakeIdsInner { account_hash: Vec<u8> },
    ValidatorStakeIds,
    ValidatorStakeIdsInner { key_hash: Vec<u8> },
    ValidatorTotalMinted,
    MultiplierPoints,
    // Slashing
    ValidatorSlashCount,
    // Consensus
    Scores,
    ValidatorBalances,
    StakerRewards,
    // Staked consensus
    LastCheckinEpoch,
    // Priv consensus
    CommitmentRegistered,
    NullifierUsed,
    NullifierUsedInner { epoch: u64 },
    NullifierRewards,
    EpochNullifiers,
    EpochNullifiersInner { epoch: u64 },
}

// ── Enums ───────────────────────────────────────────────────────────────────

#[near(serializers = [json, borsh])]
#[derive(Clone, PartialEq, Debug)]
pub enum KeyType {
    Ecdsa,
    Ed25519,
    Sr25519,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, PartialEq, Debug)]
pub enum ConsensusType {
    Yuma,
    Linear,
    Staked,
    Priv,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, PartialEq, Debug)]
pub enum InflationType {
    Flat {
        rate: U128,
    },
    Halving {
        initial_rate: U128,
        interval: u64,
        floor: U128,
    },
    LinearDecay {
        initial_rate: U128,
        floor: U128,
        decay_epochs: u64,
    },
    Sigmoid {
        peak: U128,
        floor: U128,
        total_epochs: u64,
    },
    Tao {
        initial_rate: U128,
        supply_cap: U128,
    },
    Btc {
        initial_reward: U128,
        halving_interval: u64,
        supply_cap: U128,
    },
}

// ── Structs ─────────────────────────────────────────────────────────────────

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct Validator {
    pub key: String,
    pub key_type: KeyType,
    pub owner: AccountId,
    pub registered_block: u64,
    pub commission_bps: u32,
    pub active: bool,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug, Default)]
pub struct ValidatorScore {
    pub last_seen_block: u64,
    pub blocktime_score: u128,
    pub earned: u128,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct StakePosition {
    pub stake_id: u64,
    pub staker: AccountId,
    pub validator_key_hash: Vec<u8>,
    pub amount: u128,
    pub start_block: u64,
    pub lock_blocks: u64,
    pub minted_balance: u128,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct ConsensusState {
    pub current_block: u64,
    pub last_emission_block: u64,
    pub total_blocktime: u128,
    pub epoch_length: u64,
    pub current_epoch: u64,
}

#[near(serializers = [json, borsh])]
#[derive(Clone, Debug)]
pub struct MultiplierPoint {
    pub blocks: u64,
    pub multiplier: u32, // basis points: 10000 = 1.0x
}

// ── View Types ──────────────────────────────────────────────────────────────

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct ValidatorView {
    pub key: String,
    pub key_type: KeyType,
    pub owner: AccountId,
    pub registered_block: u64,
    pub commission_bps: u32,
    pub active: bool,
    pub total_stt: U128,
    pub score: U128,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct StakePositionView {
    pub stake_id: u64,
    pub staker: AccountId,
    pub validator_key: String,
    pub amount: U128,
    pub start_block: u64,
    pub lock_blocks: u64,
    pub minted_balance: U128,
    pub unlocks_at: u64,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct ConsensusStateView {
    pub current_block: u64,
    pub last_emission_block: u64,
    pub total_blocktime: U128,
    pub epoch_length: u64,
    pub current_epoch: u64,
    pub consensus_type: ConsensusType,
    pub inflation_type: InflationType,
    pub emission_rate: U128,
    pub total_supply: U128,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct LeaderboardEntry {
    pub key: String,
    pub score: U128,
    pub total_stt: U128,
    pub commission_bps: u32,
    pub active: bool,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct FungibleTokenMetadata {
    pub spec: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}

// ── Helpers ─────────────────────────────────────────────────────────────────

pub fn key_hash(key: &str) -> Vec<u8> {
    near_sdk::env::keccak256(key.as_bytes())
}

