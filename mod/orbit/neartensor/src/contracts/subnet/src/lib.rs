use near_sdk::json_types::U128;
use near_sdk::store::{LookupMap, Vector};
use near_sdk::{near, env, AccountId, PanicOnDefault, Promise};

mod types;
mod token;
mod staking;
mod consensus;
mod consensus_yuma;
mod consensus_linear;
mod consensus_staked;
mod consensus_priv;
mod inflation;
mod merkle;

use types::*;

// ── Contract State ──────────────────────────────────────────────────────────

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Subnet {
    // Ownership
    pub owner: AccountId,

    // Token (internal NEP-141)
    pub token_name: String,
    pub token_symbol: String,
    pub token_decimals: u8,
    pub total_supply: u128,
    pub balances: LookupMap<AccountId, u128>,

    // Staking
    pub native_token_locked: u128,
    pub validators: LookupMap<Vec<u8>, Validator>,
    pub validator_keys: Vector<Vec<u8>>,
    pub next_stake_id: u64,
    pub max_stakers_per_validator: u32,
    pub max_lock_blocks: u64,
    pub default_commission_bps: u32,
    pub stake_positions: LookupMap<u64, StakePosition>,
    pub user_stake_ids: LookupMap<AccountId, Vector<u64>>,
    pub validator_stake_ids: LookupMap<Vec<u8>, Vector<u64>>,
    pub validator_total_minted: LookupMap<Vec<u8>, u128>,
    pub multiplier_points: Vector<MultiplierPoint>,

    // Slashing
    pub slash_bps: u32,
    pub max_slash_count: u32,
    pub slash_treasury: Option<AccountId>,
    pub validator_slash_count: LookupMap<Vec<u8>, u32>,

    // Consensus
    pub consensus_type: ConsensusType,
    pub consensus: ConsensusState,
    pub emission_rate: u128,
    pub scores: LookupMap<Vec<u8>, ValidatorScore>,
    pub validator_balances: LookupMap<Vec<u8>, u128>,
    pub staker_rewards: LookupMap<AccountId, u128>,

    // Yuma-specific
    pub decay_bps: u32,

    // Staked-specific
    pub last_checkin_epoch: LookupMap<Vec<u8>, u64>,

    // Priv-specific
    pub commitment_registered: LookupMap<Vec<u8>, bool>,
    pub nullifier_used: LookupMap<Vec<u8>, bool>,
    pub epoch_nullifiers: LookupMap<u64, Vector<Vec<u8>>>,
    pub epoch_checkin_count: u64,
    pub nullifier_rewards: LookupMap<Vec<u8>, u128>,

    // Merkle tree (for Priv)
    pub merkle_zeros: Vec<Vec<u8>>,
    pub merkle_filled: Vec<Vec<u8>>,
    pub merkle_next_index: u64,
    pub merkle_roots: Vec<Vec<u8>>,
    pub merkle_root_index: u32,

    // Inflation
    pub inflation_type: InflationType,
    pub inflation_total_minted: u128,
}

// ── Internal helpers (no #[near]) ───────────────────────────────────────────

impl Subnet {
    fn assert_owner(&self) {
        assert_eq!(
            env::predecessor_account_id(),
            self.owner,
            "only owner"
        );
    }
}

// ── Public NEAR contract interface ──────────────────────────────────────────
// All public methods in a single #[near] impl block to avoid macro issues

#[near]
impl Subnet {
    #[init]
    pub fn new(
        owner: AccountId,
        token_name: String,
        token_symbol: String,
        consensus_type: ConsensusType,
        inflation_type: InflationType,
        emission_rate: U128,
        epoch_length: u64,
        decay_bps: Option<u32>,
        max_lock_blocks: Option<u64>,
        max_stakers_per_validator: Option<u32>,
        default_commission_bps: Option<u32>,
    ) -> Self {
        let ct = consensus_type.clone();
        let mut subnet = Self {
            owner,
            token_name,
            token_symbol,
            token_decimals: 18,
            total_supply: 0,
            balances: LookupMap::new(StorageKey::TokenBalances),
            native_token_locked: 0,
            validators: LookupMap::new(StorageKey::Validators),
            validator_keys: Vector::new(StorageKey::ValidatorKeys),
            next_stake_id: 0,
            max_stakers_per_validator: max_stakers_per_validator.unwrap_or(100),
            max_lock_blocks: max_lock_blocks.unwrap_or(2_592_000),
            default_commission_bps: default_commission_bps.unwrap_or(1000),
            stake_positions: LookupMap::new(StorageKey::StakePositions),
            user_stake_ids: LookupMap::new(StorageKey::UserStakeIds),
            validator_stake_ids: LookupMap::new(StorageKey::ValidatorStakeIds),
            validator_total_minted: LookupMap::new(StorageKey::ValidatorTotalMinted),
            multiplier_points: Vector::new(StorageKey::MultiplierPoints),
            slash_bps: 1000,
            max_slash_count: 3,
            slash_treasury: None,
            validator_slash_count: LookupMap::new(StorageKey::ValidatorSlashCount),
            consensus_type,
            consensus: ConsensusState {
                current_block: 0,
                last_emission_block: 0,
                total_blocktime: 0,
                epoch_length,
                current_epoch: 0,
            },
            emission_rate: emission_rate.0,
            scores: LookupMap::new(StorageKey::Scores),
            validator_balances: LookupMap::new(StorageKey::ValidatorBalances),
            staker_rewards: LookupMap::new(StorageKey::StakerRewards),
            decay_bps: decay_bps.unwrap_or(500),
            last_checkin_epoch: LookupMap::new(StorageKey::LastCheckinEpoch),
            commitment_registered: LookupMap::new(StorageKey::CommitmentRegistered),
            nullifier_used: LookupMap::new(StorageKey::NullifierUsed),
            epoch_nullifiers: LookupMap::new(StorageKey::EpochNullifiers),
            epoch_checkin_count: 0,
            nullifier_rewards: LookupMap::new(StorageKey::NullifierRewards),
            merkle_zeros: Vec::new(),
            merkle_filled: Vec::new(),
            merkle_next_index: 0,
            merkle_roots: Vec::new(),
            merkle_root_index: 0,
            inflation_type,
            inflation_total_minted: 0,
        };

        subnet.multiplier_points.push(MultiplierPoint { blocks: 0, multiplier: 10000 });
        subnet.multiplier_points.push(MultiplierPoint { blocks: 86400, multiplier: 15000 });
        subnet.multiplier_points.push(MultiplierPoint { blocks: 604800, multiplier: 30000 });

        if ct == ConsensusType::Priv {
            subnet.merkle_init();
        }

        subnet
    }

    // ── Ownership ───────────────────────────────────────────────────────────

    pub fn set_owner(&mut self, new_owner: AccountId) {
        self.assert_owner();
        self.owner = new_owner;
    }

    pub fn get_owner(&self) -> AccountId {
        self.owner.clone()
    }

    // ── Token (NEP-141) ─────────────────────────────────────────────────────

    pub fn ft_balance_of(&self, account_id: AccountId) -> U128 {
        self.ft_balance_of_impl(&account_id)
    }

    pub fn ft_total_supply(&self) -> U128 {
        self.ft_total_supply_impl()
    }

    pub fn ft_metadata(&self) -> FungibleTokenMetadata {
        self.ft_metadata_impl()
    }

    #[payable]
    pub fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>) {
        assert_eq!(env::attached_deposit().as_yoctonear(), 1, "Requires 1 yoctoNEAR");
        self.ft_transfer_impl(&receiver_id, amount.0, memo);
    }

    // ── Validators ──────────────────────────────────────────────────────────

    pub fn register_validator(&mut self, key: String, key_type: KeyType) {
        let commission = self.default_commission_bps;
        self.register_validator_with_commission(key, key_type, commission);
    }

    pub fn register_validator_with_commission(
        &mut self,
        key: String,
        key_type: KeyType,
        commission_bps: u32,
    ) {
        self.register_validator_impl(&key, key_type, commission_bps);
    }

    pub fn set_validator_commission(&mut self, key: String, new_commission_bps: u32) {
        self.set_validator_commission_impl(&key, new_commission_bps);
    }

    pub fn deactivate_validator(&mut self, key: String) {
        self.deactivate_validator_impl(&key);
    }

    // ── Staking ─────────────────────────────────────────────────────────────

    #[payable]
    pub fn stake_on(&mut self, validator_key: String, lock_blocks: u64) -> u64 {
        self.stake_on_impl(&validator_key, lock_blocks)
    }

    pub fn unstake_from(&mut self, stake_id: u64) -> Promise {
        self.unstake_from_impl(stake_id)
    }

    pub fn slash_validator(&mut self, key: String) {
        self.slash_validator_impl(&key);
    }

    pub fn set_points(&mut self, points: Vec<MultiplierPoint>) {
        self.set_points_impl(points);
    }

    pub fn get_multiplier_view(&self, block_count: u64) -> u32 {
        self.get_multiplier(block_count)
    }

    // ── Staking Admin ───────────────────────────────────────────────────────

    pub fn set_max_stakers_per_validator(&mut self, max: u32) {
        self.assert_owner();
        self.max_stakers_per_validator = max;
    }

    pub fn set_max_lock_blocks(&mut self, max: u64) {
        self.assert_owner();
        self.max_lock_blocks = max;
    }

    pub fn set_default_commission_bps(&mut self, bps: u32) {
        self.assert_owner();
        assert!(bps <= 5000, "commission cannot exceed 50%");
        self.default_commission_bps = bps;
    }

    pub fn set_slash_bps(&mut self, bps: u32) {
        self.assert_owner();
        self.slash_bps = bps;
    }

    pub fn set_slash_treasury(&mut self, treasury: AccountId) {
        self.assert_owner();
        self.slash_treasury = Some(treasury);
    }

    pub fn set_max_slash_count(&mut self, count: u32) {
        self.assert_owner();
        self.max_slash_count = count;
    }

    // ── Consensus ───────────────────────────────────────────────────────────

    pub fn checkin(&mut self, key: String) {
        self.checkin_impl(&key);
    }

    pub fn batch_checkin(&mut self, keys: Vec<String>) {
        self.batch_checkin_impl(&keys);
    }

    pub fn produce_block(&mut self) -> Option<String> {
        self.produce_block_impl()
    }

    pub fn claim_staker_rewards(&mut self) -> U128 {
        self.claim_staker_rewards_impl()
    }

    pub fn claim_validator_rewards(&mut self, key: String, to: AccountId) -> U128 {
        self.claim_validator_rewards_impl(&key, &to)
    }

    pub fn set_emission_rate(&mut self, rate: U128) {
        self.assert_owner();
        self.emission_rate = rate.0;
    }

    pub fn set_decay_bps(&mut self, bps: u32) {
        self.assert_owner();
        self.decay_bps = bps;
    }

    pub fn set_epoch_length(&mut self, length: u64) {
        self.assert_owner();
        self.consensus.epoch_length = length;
    }

    // ── Priv Consensus ──────────────────────────────────────────────────────

    pub fn register_commitment(&mut self, commitment: Vec<u8>) {
        self.register_commitment_impl(commitment);
    }

    pub fn anon_checkin(
        &mut self,
        nullifier: Vec<u8>,
        commitment: Vec<u8>,
        merkle_proof: Vec<Vec<u8>>,
        merkle_root: Vec<u8>,
        leaf_index: u64,
    ) {
        self.anon_checkin_impl(nullifier, commitment, merkle_proof, merkle_root, leaf_index);
    }

    pub fn claim_priv_rewards(&mut self, secret: Vec<u8>, epochs: Vec<u64>) -> U128 {
        self.claim_priv_rewards_impl(secret, epochs)
    }

    // ── Inflation ───────────────────────────────────────────────────────────

    pub fn get_inflation_emission_view(&self, epoch: u64) -> U128 {
        U128(self.get_inflation_emission(epoch))
    }

    pub fn get_effective_emission_view(&self) -> U128 {
        U128(self.get_effective_emission())
    }

    // ── Views ───────────────────────────────────────────────────────────────

    pub fn get_consensus_state(&self) -> ConsensusStateView {
        self.get_consensus_state_impl()
    }

    pub fn get_validator(&self, key: String) -> Option<ValidatorView> {
        self.get_validator_impl(&key)
    }

    pub fn validator_count(&self) -> u32 {
        self.validator_count_impl()
    }

    pub fn get_stake_position(&self, stake_id: u64) -> Option<StakePositionView> {
        self.get_stake_position_impl(stake_id)
    }

    pub fn get_user_stake_ids(&self, user: AccountId) -> Vec<u64> {
        self.get_user_stake_ids_impl(&user)
    }

    pub fn get_validator_total_minted(&self, key: String) -> U128 {
        self.get_validator_total_minted_impl(&key)
    }

    pub fn get_validator_score(&self, key: String) -> Option<ValidatorScore> {
        self.get_validator_score_impl(&key)
    }

    pub fn get_validator_balance(&self, key: String) -> U128 {
        self.get_validator_balance_impl(&key)
    }

    pub fn get_staker_rewards(&self, staker: AccountId) -> U128 {
        self.get_staker_rewards_impl(&staker)
    }

    pub fn get_leaderboard(&self, limit: u32) -> Vec<LeaderboardEntry> {
        self.get_leaderboard_impl(limit)
    }

    pub fn get_merkle_root(&self) -> Vec<u8> {
        self.merkle_last_root()
    }

    pub fn get_epoch_checkin_count(&self) -> u64 {
        self.epoch_checkin_count
    }

    pub fn is_nullifier_used(&self, epoch: u64, nullifier: Vec<u8>) -> bool {
        self.is_nullifier_used_impl(epoch, nullifier)
    }
}
