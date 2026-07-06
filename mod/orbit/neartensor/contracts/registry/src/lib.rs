use near_sdk::json_types::U128;
use near_sdk::store::{LookupMap, Vector};
use near_sdk::{near, env, AccountId, NearToken, PanicOnDefault, Promise};

mod types;
mod bonding;

use types::*;

// ── Contract State ──────────────────────────────────────────────────────────

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Registry {
    owner: AccountId,

    // Subnet slots
    max_subnets: u32,
    next_subnet_id: u32,
    subnets: LookupMap<u32, SubnetInfo>,
    subnet_ids: Vector<u32>,
    owner_subnets: LookupMap<AccountId, Vector<u32>>,
    locked_stake: LookupMap<u32, u128>,

    // Governance
    governance_token: AccountId,
    registration_cost: u128,
    immunity_period: u64,

    // Bonding curve
    pub(crate) curve_slope: u128,
    pub(crate) subnet_total_shares: LookupMap<u32, u128>,
    subnet_bloctime: LookupMap<u32, u128>,
    user_shares: LookupMap<u32, LookupMap<AccountId, u128>>,

    // Factory
    subnet_wasm: Vec<u8>,
}

// ── Implementation ──────────────────────────────────────────────────────────

#[near]
impl Registry {
    #[init]
    pub fn new(
        governance_token: AccountId,
        registration_cost: U128,
        immunity_period: u64,
    ) -> Self {
        Self {
            owner: env::predecessor_account_id(),
            max_subnets: 420,
            next_subnet_id: 0,
            subnets: LookupMap::new(StorageKey::Subnets),
            subnet_ids: Vector::new(StorageKey::SubnetIds),
            owner_subnets: LookupMap::new(StorageKey::OwnerSubnets),
            locked_stake: LookupMap::new(StorageKey::LockedStake),
            governance_token,
            registration_cost: registration_cost.0,
            immunity_period,
            curve_slope: 1_000_000_000_000, // 1e12
            subnet_total_shares: LookupMap::new(StorageKey::SubnetTotalShares),
            subnet_bloctime: LookupMap::new(StorageKey::SubnetBloctime),
            user_shares: LookupMap::new(StorageKey::UserShares),
            subnet_wasm: Vec::new(),
        }
    }

    // ── Registration ────────────────────────────────────────────────────────

    #[payable]
    pub fn register_subnet(&mut self, params: SubnetParams) -> Promise {
        let caller = env::predecessor_account_id();

        // Check capacity, evict weakest if full
        let active = self.active_count();
        if active >= self.max_subnets {
            let (weak_id, found) = self.find_weakest();
            assert!(found, "all subnets immune, cannot register");
            self.deregister_internal(weak_id);
        }

        let subnet_id = self.next_subnet_id;
        self.next_subnet_id += 1;

        let sub_account: AccountId =
            format!("s{}.{}", subnet_id, env::current_account_id())
                .parse()
                .expect("invalid sub-account");

        let info = SubnetInfo {
            id: subnet_id,
            owner: caller.clone(),
            name: params.name.clone(),
            account_id: sub_account.clone(),
            registered_block: env::block_height(),
            active: true,
            consensus_type: params.consensus_type.clone(),
            inflation_type: params.inflation_config.clone(),
        };

        self.subnets.insert(subnet_id, info);
        self.subnet_ids.push(subnet_id);
        self.locked_stake.insert(subnet_id, self.registration_cost);

        // Track owner subnets
        if self.owner_subnets.get(&caller).is_none() {
            self.owner_subnets.insert(
                caller.clone(),
                Vector::new(StorageKey::OwnerSubnetsInner {
                    owner_hash: env::sha256(caller.as_bytes()),
                }),
            );
        }
        self.owner_subnets
            .get_mut(&caller)
            .unwrap()
            .push(subnet_id);

        // Deploy subnet contract to sub-account
        assert!(!self.subnet_wasm.is_empty(), "subnet WASM not stored");

        // Build init args JSON
        let init_args = format!(
            r#"{{"owner":"{}","token_name":"{}","token_symbol":"{}","consensus_type":"{}","inflation_type":{},"emission_rate":"{}","epoch_length":{}{}{}{}{}}}"#,
            caller,
            params.token_name,
            params.token_symbol,
            params.consensus_type,
            params.inflation_config,
            params.emission_rate.0,
            params.epoch_length,
            params.decay_bps.map(|v| format!(",\"decay_bps\":{}", v)).unwrap_or_default(),
            params.max_lock_blocks.map(|v| format!(",\"max_lock_blocks\":{}", v)).unwrap_or_default(),
            params.max_stakers_per_validator.map(|v| format!(",\"max_stakers_per_validator\":{}", v)).unwrap_or_default(),
            params.default_commission_bps.map(|v| format!(",\"default_commission_bps\":{}", v)).unwrap_or_default(),
        );

        Promise::new(sub_account)
            .create_account()
            .transfer(NearToken::from_near(5))
            .deploy_contract(self.subnet_wasm.clone())
            .function_call(
                "new".to_string(),
                init_args.into_bytes(),
                NearToken::from_near(0),
                near_sdk::Gas::from_tgas(50),
            )
    }

    pub fn deregister_subnet(&mut self, subnet_id: u32) {
        let info = self.subnets.get(&subnet_id).expect("subnet not found");
        assert!(info.active, "not active");
        assert!(
            env::predecessor_account_id() == info.owner
                || env::predecessor_account_id() == self.owner,
            "not authorized"
        );
        self.deregister_internal(subnet_id);
    }

    fn deregister_internal(&mut self, subnet_id: u32) {
        if let Some(mut info) = self.subnets.get(&subnet_id).cloned() {
            info.active = false;
            self.subnets.insert(subnet_id, info);
            // Clear locked stake (in production, refund governance tokens)
            self.locked_stake.insert(subnet_id, 0);
        }
    }

    // ── Bonding Curve Boost ─────────────────────────────────────────────────

    #[payable]
    pub fn boost_subnet(&mut self, subnet_id: u32) {
        let amount = env::attached_deposit().as_yoctonear();
        assert!(amount > 0, "must attach NEAR to boost");

        let info = self.subnets.get(&subnet_id).expect("subnet not found");
        assert!(info.active, "subnet not active");

        let shares = self.calc_shares_for_deposit(subnet_id, amount);
        assert!(shares > 0, "zero shares");

        let ts = self
            .subnet_total_shares
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        self.subnet_total_shares
            .insert(subnet_id, ts + shares);

        let bt = self
            .subnet_bloctime
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        self.subnet_bloctime
            .insert(subnet_id, bt + amount);

        // Track user shares
        let caller = env::predecessor_account_id();
        if self.user_shares.get(&subnet_id).is_none() {
            self.user_shares.insert(
                subnet_id,
                LookupMap::new(StorageKey::UserSharesInner { subnet_id }),
            );
        }
        let us = self
            .user_shares
            .get(&subnet_id)
            .and_then(|m| m.get(&caller).copied())
            .unwrap_or(0);
        self.user_shares
            .get_mut(&subnet_id)
            .unwrap()
            .insert(caller, us + shares);
    }

    pub fn sell_boost(&mut self, subnet_id: u32, shares: U128) -> Promise {
        let shares_val = shares.0;
        assert!(shares_val > 0, "zero shares");

        let caller = env::predecessor_account_id();
        let us = self
            .user_shares
            .get(&subnet_id)
            .and_then(|m| m.get(&caller).copied())
            .unwrap_or(0);
        assert!(us >= shares_val, "insufficient shares");

        let near_return = self.calc_return_for_sell(subnet_id, shares_val);
        assert!(near_return > 0, "zero return");

        let ts = self
            .subnet_total_shares
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        self.subnet_total_shares
            .insert(subnet_id, ts - shares_val);

        let bt = self
            .subnet_bloctime
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        self.subnet_bloctime
            .insert(subnet_id, bt.saturating_sub(near_return));

        self.user_shares
            .get_mut(&subnet_id)
            .unwrap()
            .insert(caller.clone(), us - shares_val);

        Promise::new(caller).transfer(NearToken::from_yoctonear(near_return))
    }

    // ── Factory: WASM Storage ───────────────────────────────────────────────

    pub fn store_subnet_wasm(&mut self, #[serializer(borsh)] wasm: Vec<u8>) {
        self.assert_owner();
        assert!(!wasm.is_empty(), "empty WASM");
        self.subnet_wasm = wasm;
    }

    pub fn get_subnet_wasm_size(&self) -> u64 {
        self.subnet_wasm.len() as u64
    }

    // ── Views ───────────────────────────────────────────────────────────────

    pub fn get_subnet(&self, subnet_id: u32) -> Option<SubnetInfoView> {
        self.subnets.get(&subnet_id).map(|info| SubnetInfoView {
            id: info.id,
            owner: info.owner.clone(),
            name: info.name.clone(),
            account_id: info.account_id.clone(),
            registered_block: info.registered_block,
            active: info.active,
            consensus_type: info.consensus_type.clone(),
            inflation_type: info.inflation_type.clone(),
            stake_score: U128(self.get_stake_score_internal(subnet_id)),
            is_immune: self.is_immune_internal(subnet_id),
        })
    }

    pub fn get_all_subnets(&self) -> Vec<SubnetInfoView> {
        let mut result = Vec::new();
        for &id in self.subnet_ids.iter() {
            if let Some(info) = self.subnets.get(&id) {
                if info.active {
                    result.push(SubnetInfoView {
                        id: info.id,
                        owner: info.owner.clone(),
                        name: info.name.clone(),
                        account_id: info.account_id.clone(),
                        registered_block: info.registered_block,
                        active: info.active,
                        consensus_type: info.consensus_type.clone(),
                        inflation_type: info.inflation_type.clone(),
                        stake_score: U128(
                            self.get_stake_score_internal(info.id),
                        ),
                        is_immune: self.is_immune_internal(info.id),
                    });
                }
            }
        }
        result
    }

    pub fn get_subnet_count(&self) -> u32 {
        self.active_count()
    }

    pub fn is_immune(&self, subnet_id: u32) -> bool {
        self.is_immune_internal(subnet_id)
    }

    pub fn get_stake_score(&self, subnet_id: u32) -> U128 {
        U128(self.get_stake_score_internal(subnet_id))
    }

    pub fn get_weakest_subnet(&self) -> (u32, U128, bool) {
        let (id, found) = self.find_weakest();
        let score = if found {
            self.get_stake_score_internal(id)
        } else {
            0
        };
        (id, U128(score), found)
    }

    pub fn get_boost_price(&self, subnet_id: u32, num_shares: U128) -> U128 {
        U128(self.get_boost_price_internal(subnet_id, num_shares.0))
    }

    pub fn get_sell_return(&self, subnet_id: u32, num_shares: U128) -> U128 {
        U128(self.calc_return_for_sell(subnet_id, num_shares.0))
    }

    pub fn get_pool_info(&self, subnet_id: u32) -> PoolInfoView {
        let ts = self
            .subnet_total_shares
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        let bt = self
            .subnet_bloctime
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        let price =
            self.curve_slope * ts / 1_000_000_000_000_000_000;
        let locked = self
            .locked_stake
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);

        PoolInfoView {
            total_shares: U128(ts),
            total_bloctime: U128(bt),
            current_price: U128(price),
            locked_stake: U128(locked),
        }
    }

    pub fn get_user_shares(&self, subnet_id: u32, user: AccountId) -> U128 {
        U128(
            self.user_shares
                .get(&subnet_id)
                .and_then(|m| m.get(&user).copied())
                .unwrap_or(0),
        )
    }

    pub fn get_owner_subnets(&self, owner: AccountId) -> Vec<u32> {
        self.owner_subnets
            .get(&owner)
            .map(|v| v.iter().copied().collect())
            .unwrap_or_default()
    }

    pub fn get_registration_cost(&self) -> U128 {
        U128(self.registration_cost)
    }

    // ── Admin ───────────────────────────────────────────────────────────────

    pub fn set_owner(&mut self, new_owner: AccountId) {
        self.assert_owner();
        self.owner = new_owner;
    }

    pub fn set_immunity_period(&mut self, period: u64) {
        self.assert_owner();
        self.immunity_period = period;
    }

    pub fn set_registration_cost(&mut self, cost: U128) {
        self.assert_owner();
        self.registration_cost = cost.0;
    }

    pub fn set_curve_slope(&mut self, slope: U128) {
        self.assert_owner();
        assert!(slope.0 > 0, "zero slope");
        self.curve_slope = slope.0;
    }

    pub fn set_max_subnets(&mut self, max: u32) {
        self.assert_owner();
        self.max_subnets = max;
    }

    // ── Internal ────────────────────────────────────────────────────────────

    fn assert_owner(&self) {
        assert_eq!(
            env::predecessor_account_id(),
            self.owner,
            "only owner"
        );
    }

    fn active_count(&self) -> u32 {
        let mut count = 0u32;
        for &id in self.subnet_ids.iter() {
            if let Some(info) = self.subnets.get(&id) {
                if info.active {
                    count += 1;
                }
            }
        }
        count
    }

    fn is_immune_internal(&self, subnet_id: u32) -> bool {
        if let Some(info) = self.subnets.get(&subnet_id) {
            if !info.active {
                return false;
            }
            env::block_height() < info.registered_block + self.immunity_period
        } else {
            false
        }
    }

    fn get_stake_score_internal(&self, subnet_id: u32) -> u128 {
        let bt = self
            .subnet_bloctime
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        let locked = self
            .locked_stake
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        bt + locked
    }

    fn find_weakest(&self) -> (u32, bool) {
        let mut weak_id = 0u32;
        let mut min_score = u128::MAX;
        let mut found = false;

        for &id in self.subnet_ids.iter() {
            if let Some(info) = self.subnets.get(&id) {
                if !info.active {
                    continue;
                }
                if self.is_immune_internal(id) {
                    continue;
                }
                let score = self.get_stake_score_internal(id);
                if score < min_score {
                    min_score = score;
                    weak_id = id;
                    found = true;
                }
            }
        }

        (weak_id, found)
    }
}
