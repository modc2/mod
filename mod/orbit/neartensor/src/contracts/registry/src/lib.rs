use near_sdk::json_types::U128;
use near_sdk::store::{LookupMap, Vector};
use near_sdk::{near, env, AccountId, NearToken, PanicOnDefault, Promise};

mod types;
mod bonding;
mod bloctime;

use bloctime::{now_seconds, MAX_LOCK_SECONDS};
use types::*;

/// Sent on to each new subnet account to cover its storage. Everything a
/// registrant attaches above this is their BlocTime lock.
const SUBACCOUNT_FUNDING: u128 = 5_000_000_000_000_000_000_000_000; // 5 NEAR

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
    immunity_period: u64,

    // BlocTime (port of orbit/bloctime BlocTime.sol): registration and
    // ranking are both measured in amount × seconds locked × multiplier.
    pub(crate) min_registration_bloctime: u128,
    pub(crate) max_lock_seconds: u64,
    pub(crate) points: Vec<Point>,
    pub(crate) positions: LookupMap<u64, BlocPosition>,
    pub(crate) user_positions: LookupMap<AccountId, Vec<u64>>,
    pub(crate) next_position_id: u64,
    pub(crate) subnet_bloctime: LookupMap<u32, u128>,
    pub(crate) total_bloctime: u128,

    // Bonding curve (share market; does not count toward the score)
    pub(crate) curve_slope: u128,
    pub(crate) subnet_total_shares: LookupMap<u32, u128>,
    boost_reserve: LookupMap<u32, u128>,
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
        min_registration_bloctime: U128,
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
            immunity_period,
            min_registration_bloctime: min_registration_bloctime.0,
            max_lock_seconds: MAX_LOCK_SECONDS,
            // one flat 1x point = pure linear amount × seconds (BlocTime default)
            points: vec![Point { lock_seconds: 0, multiplier: 10_000 }],
            positions: LookupMap::new(StorageKey::Positions),
            user_positions: LookupMap::new(StorageKey::UserPositions),
            next_position_id: 0,
            subnet_bloctime: LookupMap::new(StorageKey::SubnetBloctime),
            total_bloctime: 0,
            curve_slope: 1_000_000_000_000, // 1e12
            subnet_total_shares: LookupMap::new(StorageKey::SubnetTotalShares),
            boost_reserve: LookupMap::new(StorageKey::BoostReserve),
            user_shares: LookupMap::new(StorageKey::UserShares),
            subnet_wasm: Vec::new(),
        }
    }

    // ── Registration ────────────────────────────────────────────────────────

    /// Register a subnet by locking NEAR BlocTime-style. Attach
    /// 5 NEAR account funding + the stake; the stake is locked for
    /// `lock_seconds` and must earn at least `min_registration_bloctime`.
    /// When every slot is taken the newcomer must out-score the weakest
    /// non-immune subnet, which is then evicted.
    #[payable]
    pub fn register_subnet(&mut self, params: SubnetParams, lock_seconds: u64) -> Promise {
        let caller = env::predecessor_account_id();
        assert!(!self.subnet_wasm.is_empty(), "subnet WASM not stored");

        let deposit = env::attached_deposit().as_yoctonear();
        assert!(
            deposit > SUBACCOUNT_FUNDING,
            "attach 5 NEAR account funding plus the BlocTime stake"
        );
        let stake = deposit - SUBACCOUNT_FUNDING;
        assert!(lock_seconds <= self.max_lock_seconds, "exceeds max lock");
        let bloctime = self.quote(stake, lock_seconds);
        assert!(
            bloctime >= self.min_registration_bloctime,
            "insufficient BlocTime: {} < {} (lock more NEAR or lock longer)",
            bloctime,
            self.min_registration_bloctime
        );

        if self.active_count() >= self.max_subnets {
            let (weak_id, found) = self.find_weakest();
            assert!(found, "all subnets immune, cannot register");
            let weak_score = self.get_stake_score_internal(weak_id);
            assert!(
                bloctime > weak_score,
                "BlocTime {} does not beat weakest subnet {} ({})",
                bloctime,
                weak_id,
                weak_score
            );
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
        self.open_position(subnet_id, stake, lock_seconds, true);

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
            .transfer(NearToken::from_yoctonear(SUBACCOUNT_FUNDING))
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
            // Locks stay put: every position is still withdrawable via
            // unstake_position once its own lock runs out.
        }
    }

    // ── BlocTime Locks ──────────────────────────────────────────────────────

    /// Back an active subnet with a BlocTime lock of the attached NEAR;
    /// raises its score (and so its protection from eviction).
    #[payable]
    pub fn stake_subnet(&mut self, subnet_id: u32, lock_seconds: u64) -> BlocPosition {
        let info = self.subnets.get(&subnet_id).expect("subnet not found");
        assert!(info.active, "subnet not active");
        let amount = env::attached_deposit().as_yoctonear();
        self.open_position(subnet_id, amount, lock_seconds, false)
    }

    /// Withdraw an expired lock (registration or support). Removes its
    /// BlocTime from the subnet's score — like BlocTime.sol `unstake`.
    pub fn unstake_position(&mut self, position_id: u64) -> Promise {
        let pos = self.close_position(position_id);
        Promise::new(pos.staker).transfer(NearToken::from_yoctonear(pos.amount.0))
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
            .boost_reserve
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        self.boost_reserve
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
            .boost_reserve
            .get(&subnet_id)
            .copied()
            .unwrap_or(0);
        self.boost_reserve
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
            bloctime: U128(self.subnet_bloctime.get(&subnet_id).copied().unwrap_or(0)),
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
                        bloctime: U128(
                            self.subnet_bloctime.get(&info.id).copied().unwrap_or(0),
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
        let reserve = self
            .boost_reserve
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
            boost_reserve: U128(reserve),
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

    pub fn get_registration_terms(&self) -> RegistrationTerms {
        let threshold = if self.active_count() >= self.max_subnets {
            let (id, found) = self.find_weakest();
            if found { self.get_stake_score_internal(id) } else { u128::MAX }
        } else {
            0
        };
        RegistrationTerms {
            min_registration_bloctime: U128(self.min_registration_bloctime),
            max_lock_seconds: self.max_lock_seconds,
            account_funding: U128(SUBACCOUNT_FUNDING),
            points: self.points.clone(),
            eviction_threshold: U128(threshold),
        }
    }

    /// `quoteBloc`: BlocTime for locking `amount` yocto for `lock_seconds`.
    pub fn quote_bloctime(&self, amount: U128, lock_seconds: u64) -> U128 {
        U128(self.quote(amount.0, lock_seconds.min(self.max_lock_seconds)))
    }

    /// Total deposit (funding + stake, yocto) `register_subnet` needs at
    /// `lock_seconds` — enough to clear both the minimum and, when the
    /// registry is full, the weakest subnet's score.
    pub fn quote_registration(&self, lock_seconds: u64) -> Option<U128> {
        if lock_seconds > self.max_lock_seconds {
            return None;
        }
        let terms = self.get_registration_terms();
        let mut target = self.min_registration_bloctime;
        if terms.eviction_threshold.0 > 0 {
            target = target.max(terms.eviction_threshold.0.checked_add(1)?);
        }
        let mut stake = self.min_stake_for(target, lock_seconds)?;
        if stake == 0 {
            stake = bloctime::YOCTO_PER_UNIT; // lock must earn > 0
        }
        Some(U128(stake + SUBACCOUNT_FUNDING))
    }

    pub fn get_position(&self, position_id: u64) -> Option<BlocPosition> {
        self.positions.get(&position_id).cloned()
    }

    pub fn get_user_positions(&self, user: AccountId) -> Vec<BlocPosition> {
        self.user_positions
            .get(&user)
            .map(|ids| ids.iter().filter_map(|i| self.positions.get(i).cloned()).collect())
            .unwrap_or_default()
    }

    pub fn get_total_bloctime(&self) -> U128 {
        U128(self.total_bloctime)
    }

    pub fn now_seconds(&self) -> u64 {
        now_seconds()
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

    pub fn set_min_registration_bloctime(&mut self, bloctime: U128) {
        self.assert_owner();
        self.min_registration_bloctime = bloctime.0;
    }

    pub fn set_max_lock_seconds(&mut self, seconds: u64) {
        self.assert_owner();
        assert!(
            self.points.iter().all(|p| p.lock_seconds <= seconds),
            "curve point exceeds new max"
        );
        self.max_lock_seconds = seconds;
    }

    pub fn set_points(&mut self, points: Vec<Point>) {
        self.assert_owner();
        self.validate_points(&points);
        self.points = points;
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

    /// A subnet's rank is its BlocTime: every lock held against it,
    /// registration + support, until withdrawn.
    fn get_stake_score_internal(&self, subnet_id: u32) -> u128 {
        self.subnet_bloctime
            .get(&subnet_id)
            .copied()
            .unwrap_or(0)
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

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::{accounts, VMContextBuilder};
    use near_sdk::testing_env;

    const NEAR: u128 = 1_000_000_000_000_000_000_000_000;
    const DAY: u64 = 86_400;
    const SEC: u64 = 1_000_000_000; // ns

    fn ctx(who: usize, deposit: u128, now_s: u64) {
        let mut b = VMContextBuilder::new();
        b.current_account_id("registry.testnet".parse().unwrap())
            .predecessor_account_id(accounts(who))
            .attached_deposit(NearToken::from_yoctonear(deposit))
            .block_timestamp(now_s * SEC)
            .account_balance(NearToken::from_near(10_000));
        testing_env!(b.build());
    }

    fn params(name: &str) -> SubnetParams {
        SubnetParams {
            name: name.into(),
            token_name: "T".into(),
            token_symbol: "T".into(),
            consensus_type: "Yuma".into(),
            inflation_config: "{}".into(),
            emission_rate: U128(1),
            epoch_length: 100,
            decay_bps: None,
            max_lock_blocks: None,
            max_stakers_per_validator: None,
            default_commission_bps: None,
        }
    }

    /// 1 NEAR locked for 30 days is the bar.
    fn registry() -> Registry {
        ctx(0, 0, 1_000);
        let min = 1_000_000 * (30 * DAY) as u128; // µNEAR × seconds
        let mut r = Registry::new(accounts(5), U128(min), 0);
        r.store_subnet_wasm(vec![0u8; 8]);
        r
    }

    #[test]
    fn quote_is_amount_times_seconds() {
        let r = registry();
        assert_eq!(r.quote(10 * NEAR, 3600), 10_000_000 * 3600);
    }

    #[test]
    fn registration_locks_stake_as_bloctime() {
        let mut r = registry();
        ctx(1, 5 * NEAR + 2 * NEAR, 1_000);
        r.register_subnet(params("a"), 30 * DAY);
        let s = r.get_subnet(0).unwrap();
        assert_eq!(s.bloctime.0, 2_000_000 * (30 * DAY) as u128);
        assert_eq!(s.stake_score, s.bloctime);
        let pos = r.get_user_positions(accounts(1));
        assert_eq!(pos.len(), 1);
        assert!(pos[0].registration);
        assert_eq!(pos[0].amount.0, 2 * NEAR); // 5 NEAR funding excluded
        assert_eq!(r.get_pool_info(0).locked_stake.0, 2 * NEAR);
    }

    #[test]
    #[should_panic(expected = "insufficient BlocTime")]
    fn short_lock_is_rejected() {
        let mut r = registry();
        ctx(1, 5 * NEAR + 2 * NEAR, 1_000);
        r.register_subnet(params("a"), DAY); // 2 NEAR × 1 day < 1 NEAR × 30 days
    }

    #[test]
    #[should_panic(expected = "attach 5 NEAR")]
    fn funding_only_is_rejected() {
        let mut r = registry();
        ctx(1, 5 * NEAR, 1_000);
        r.register_subnet(params("a"), 30 * DAY);
    }

    #[test]
    fn quote_registration_is_exact_minimum() {
        let mut r = registry();
        let need = r.quote_registration(30 * DAY).unwrap().0;
        assert_eq!(need, 6 * NEAR);
        ctx(1, need, 1_000);
        r.register_subnet(params("a"), 30 * DAY);
        assert!(r.quote_registration(r.max_lock_seconds + 1).is_none());
    }

    #[test]
    fn full_registry_evicts_only_when_outscored() {
        let mut r = registry();
        r.set_max_subnets(2);
        ctx(1, 5 * NEAR + NEAR, 1_000);
        r.register_subnet(params("weak"), 30 * DAY);
        ctx(2, 5 * NEAR + 3 * NEAR, 1_000);
        r.register_subnet(params("strong"), 30 * DAY);

        let threshold = r.get_registration_terms().eviction_threshold.0;
        assert_eq!(threshold, r.get_subnet(0).unwrap().bloctime.0);
        ctx(3, r.quote_registration(30 * DAY).unwrap().0, 1_000);
        r.register_subnet(params("new"), 30 * DAY);
        assert!(!r.get_subnet(0).unwrap().active);
        assert!(r.get_subnet(2).unwrap().active);
        assert_eq!(r.get_subnet_count(), 2);
    }

    #[test]
    #[should_panic(expected = "does not beat weakest")]
    fn full_registry_rejects_weaker_newcomer() {
        let mut r = registry();
        r.set_max_subnets(1);
        ctx(1, 5 * NEAR + 4 * NEAR, 1_000);
        r.register_subnet(params("a"), 30 * DAY);
        ctx(2, 5 * NEAR + NEAR, 1_000);
        r.register_subnet(params("b"), 30 * DAY);
    }

    #[test]
    fn support_lock_raises_score_and_unlocks_after_expiry() {
        let mut r = registry();
        ctx(1, 6 * NEAR, 1_000);
        r.register_subnet(params("a"), 30 * DAY);
        let base = r.get_stake_score(0).0;

        ctx(2, NEAR, 1_000);
        let p = r.stake_subnet(0, 10 * DAY);
        assert_eq!(r.get_stake_score(0).0, base + 1_000_000 * (10 * DAY) as u128);

        ctx(2, 0, 1_000 + 10 * DAY);
        r.unstake_position(p.id);
        assert_eq!(r.get_stake_score(0).0, base);
        assert!(r.get_user_positions(accounts(2)).is_empty());
    }

    #[test]
    #[should_panic(expected = "still locked")]
    fn registration_lock_cannot_leave_early() {
        let mut r = registry();
        ctx(1, 6 * NEAR, 1_000);
        r.register_subnet(params("a"), 30 * DAY);
        ctx(1, 0, 1_000 + 30 * DAY - 1);
        r.unstake_position(0);
    }

    #[test]
    fn boost_does_not_move_the_score() {
        let mut r = registry();
        ctx(1, 6 * NEAR, 1_000);
        r.register_subnet(params("a"), 30 * DAY);
        let base = r.get_stake_score(0).0;
        ctx(2, 3 * NEAR, 1_000);
        r.boost_subnet(0);
        assert_eq!(r.get_stake_score(0).0, base);
        assert_eq!(r.get_pool_info(0).boost_reserve.0, 3 * NEAR);
        // curve round-trips: selling every share returns ~the deposit
        let shares = r.get_user_shares(0, accounts(2));
        let back = r.get_sell_return(0, shares).0;
        assert!(back <= 3 * NEAR && 3 * NEAR - back < NEAR / 1_000_000, "{}", back);
    }

    #[test]
    fn multiplier_curve_matches_bloctime_sol() {
        let mut r = registry();
        ctx(0, 0, 1_000);
        r.set_points(vec![
            Point { lock_seconds: 0, multiplier: 10_000 },
            Point { lock_seconds: 100 * DAY, multiplier: 30_000 },
        ]);
        assert_eq!(r.multiplier(50 * DAY), 20_000);
        assert_eq!(r.multiplier(400 * DAY), 30_000);
        assert_eq!(r.quote(NEAR, 50 * DAY), 2 * 1_000_000 * (50 * DAY) as u128);
    }

    #[test]
    #[should_panic(expected = "mult must increase")]
    fn decreasing_curve_rejected() {
        let mut r = registry();
        ctx(0, 0, 1_000);
        r.set_points(vec![
            Point { lock_seconds: 0, multiplier: 20_000 },
            Point { lock_seconds: DAY, multiplier: 10_000 },
        ]);
    }
}
