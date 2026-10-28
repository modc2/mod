//! DollarTime (DTT) on NEAR — collective ownership of one revenue-generating
//! thing (a house, a machine), paid for in dollars and time.
//!
//! THE RULES — identical to the paper ledger (engine.py) and the EVM
//! contract (evm/contracts/DollarTime.sol):
//!
//!   cap       21,000,000 DTT ever.
//!   emission  at most 7,200 DTT a day: day d emits 7,200 for d < 2916,
//!             4,800 on day 2916, nothing after. 2916*7200+4800 = 21M.
//!   buying    dollars arrive as a NEP-141 stablecoin (6 decimals) through
//!             `ft_transfer_call(this, amount, msg)` — msg = "<lock_days>"
//!             (or "contribute:<lock_days>", empty = 0). weight = dollars *
//!             (365 + lock_days), lock_days 0..1460. A day's emission is
//!             split by weight: tokens = emission * w / W. Claim after the
//!             day closes.
//!   burning   a day with no buyer is burned: its emission is never minted
//!             and still counts against the cap.
//!   lock      tokens from a locked contribution cannot be transferred until
//!             unlock_at; they still earn revenue.
//!   revenue   `ft_transfer_call(this, amount, "revenue:<memo>")` is split
//!             pro-rata across every DTT in existence at that moment
//!             (acc-per-share, 1e36, 256-bit intermediate).
//!   treasury  the steward spends it on the asset and sets the valuation, so
//!             value_of(you) = valuation * balance / supply.
//!
//! DTT is itself a NEP-141 token (ft_transfer / ft_transfer_call /
//! ft_balance_of / ft_total_supply / ft_metadata) with lock-aware transfers.
//! Dollars leave through ft_transfer on the stablecoin; a failed payout is
//! credited back by the callback.
use near_sdk::json_types::U128;
use near_sdk::serde::{Deserialize, Serialize};
use near_sdk::store::LookupMap;
use near_sdk::{
    assert_one_yocto, env, ext_contract, near, require, AccountId, BorshStorageKey, Gas, NearToken,
    PanicOnDefault, Promise, PromiseError, PromiseOrValue,
};

pub const DAY: u64 = 86_400;
pub const ONE: u128 = 1_000_000_000_000_000_000; // 1 DTT (18 decimals)
pub const CAP: u128 = 21_000_000 * ONE;
pub const DAILY: u128 = 7_200 * ONE;
pub const FULL_DAYS: u64 = (CAP / DAILY) as u64; // 2916
pub const LAST_DAY_EMISSION: u128 = CAP - FULL_DAYS as u128 * DAILY; // 4,800 DTT
pub const MAX_LOCK_DAYS: u64 = 1460;
pub const ACC: u128 = 1_000_000_000_000_000_000_000_000_000_000_000_000; // 1e36

const GAS_FT_TRANSFER: Gas = Gas::from_tgas(10);
const GAS_FT_ON_TRANSFER: Gas = Gas::from_tgas(30);
const GAS_CALLBACK: Gas = Gas::from_tgas(10);

// ── the schedule (pure) — in a module because #[near] exports every method
//    name as a crate-root extern fn, and `emission` is also a view ─────────

pub mod rules {
    use super::{CAP, DAILY, FULL_DAYS, LAST_DAY_EMISSION};


    pub fn emission(day: u64) -> u128 {
        if day < FULL_DAYS {
            DAILY
        } else if day == FULL_DAYS {
            LAST_DAY_EMISSION
        } else {
            0
        }
    }

    /// Total emission for days 0..days-1.
    pub fn cumulative(days: u64) -> u128 {
        if days <= FULL_DAYS {
            days as u128 * DAILY
        } else {
            CAP
        }
    }

    pub fn weight_of(dollars: u128, lock_days: u64) -> u128 {
        dollars * (365 + lock_days as u128)
    }
}

// ── 256-bit mul_div so the 1e36 accumulator matches the EVM exactly ─────────

fn mul_wide(a: u128, b: u128) -> (u128, u128) {
    const M: u128 = u64::MAX as u128;
    let (a0, a1) = (a & M, a >> 64);
    let (b0, b1) = (b & M, b >> 64);
    let p00 = a0 * b0;
    let p01 = a0 * b1;
    let p10 = a1 * b0;
    let p11 = a1 * b1;
    let (mid, c1) = p01.overflowing_add(p10);
    let mid_hi = (mid >> 64) + if c1 { 1u128 << 64 } else { 0 };
    let (lo, c2) = p00.overflowing_add(mid << 64);
    (p11 + mid_hi + c2 as u128, lo)
}

fn div_wide(hi: u128, lo: u128, d: u128) -> u128 {
    require!(d != 0, "division by zero");
    require!(hi < d, "mul_div overflow");
    let mut rem = hi;
    let mut q: u128 = 0;
    for i in (0..128).rev() {
        let bit = (lo >> i) & 1;
        let top = rem >> 127;
        let r2 = (rem << 1) | bit;
        if top == 1 || r2 >= d {
            rem = r2.wrapping_sub(d);
            q |= 1 << i;
        } else {
            rem = r2;
        }
    }
    q
}

/// floor(a * b / c) with a 256-bit intermediate.
pub fn mul_div(a: u128, b: u128, c: u128) -> u128 {
    let (hi, lo) = mul_wide(a, b);
    div_wide(hi, lo, c)
}

// ── types ───────────────────────────────────────────────────────────────────

#[derive(BorshStorageKey)]
#[near(serializers = [borsh])]
enum Key {
    DayWeight,
    Positions,
    PositionsOf,
    Balances,
    Owed,
    Debt,
}

#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct Position {
    pub id: u64,
    pub owner: AccountId,
    pub day: u64,
    pub lock_days: u64,
    pub unlock_at: u64,
    pub claimed: bool,
    pub dollars: U128,
    pub weight: U128,
    pub minted: U128,
}

#[near(serializers = [json])]
#[derive(Clone, Debug)]
pub struct PositionView {
    #[serde(flatten)]
    pub position: Position,
    pub claimable: U128,
}

#[near(serializers = [json])]
pub struct Metadata {
    pub spec: String,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}

#[near(serializers = [json])]
pub struct Stats {
    pub now: u64,
    pub genesis: u64,
    pub day: u64,
    pub day_ends_in: u64,
    pub emission_today: U128,
    pub weight_today: U128,
    pub emitted: U128,
    pub burned: U128,
    pub total_supply: U128,
    pub cap: U128,
    pub daily: U128,
    pub days_left: u64,
    pub treasury: U128,
    pub revenue_total: U128,
    pub acc_per_share: U128,
    pub asset_name: String,
    pub asset_description: String,
    pub valuation: U128,
    pub steward: AccountId,
    pub dollar: AccountId,
    pub positions: u64,
    pub max_lock_days: u64,
}

#[near(serializers = [json])]
pub struct Me {
    pub account: AccountId,
    pub balance: U128,
    pub locked: U128,
    pub unlocked: U128,
    pub owed: U128,
    pub share_bps: u32,
    pub value: U128,
    pub steward: bool,
    pub positions: Vec<PositionView>,
}

#[near(serializers = [json])]
pub struct StorageBalance {
    pub total: U128,
    pub available: U128,
}

#[derive(Serialize, Deserialize)]
#[serde(crate = "near_sdk::serde")]
struct Nep297<'a, T: Serialize> {
    standard: &'a str,
    version: &'a str,
    event: &'a str,
    data: [T; 1],
}

fn log_event<T: Serialize>(standard: &str, event: &str, data: T) {
    let e = Nep297 { standard, version: "1.0.0", event, data: [data] };
    env::log_str(&format!("EVENT_JSON:{}", near_sdk::serde_json::to_string(&e).unwrap()));
}

#[allow(dead_code)]
#[ext_contract(ext_ft)]
trait FungibleToken {
    fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>);
}

#[allow(dead_code)]
#[ext_contract(ext_ft_receiver)]
trait FungibleTokenReceiver {
    fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) -> PromiseOrValue<U128>;
}

// ── the contract ────────────────────────────────────────────────────────────

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct DollarTime {
    dollar: AccountId,
    genesis: u64,
    steward: AccountId,
    symbol: String,
    asset_name: String,
    asset_description: String,
    valuation: u128,

    treasury: u128,
    allocated: u128,
    day_weight: LookupMap<u64, u128>,

    next_id: u64,
    positions: LookupMap<u64, Position>,
    positions_of: LookupMap<AccountId, Vec<u64>>,

    total_supply: u128,
    balances: LookupMap<AccountId, u128>,
    acc_per_share: u128,
    revenue_total: u128,
    owed: LookupMap<AccountId, u128>,
    debt: LookupMap<AccountId, u128>,
}

#[near]
impl DollarTime {
    /// `dollar` is the NEP-141 stablecoin contributions are made in (6
    /// decimals, e.g. USDC). The deployer is the steward.
    #[init]
    pub fn new(dollar: AccountId, asset_name: String, symbol: String) -> Self {
        Self {
            dollar,
            genesis: env::block_timestamp() / 1_000_000_000,
            steward: env::predecessor_account_id(),
            symbol,
            asset_name,
            asset_description: String::new(),
            valuation: 0,
            treasury: 0,
            allocated: 0,
            day_weight: LookupMap::new(Key::DayWeight),
            next_id: 1,
            positions: LookupMap::new(Key::Positions),
            positions_of: LookupMap::new(Key::PositionsOf),
            total_supply: 0,
            balances: LookupMap::new(Key::Balances),
            acc_per_share: 0,
            revenue_total: 0,
            owed: LookupMap::new(Key::Owed),
            debt: LookupMap::new(Key::Debt),
        }
    }

    // ── time ────────────────────────────────────────────────────────────────

    fn now(&self) -> u64 {
        env::block_timestamp() / 1_000_000_000
    }

    pub fn current_day(&self) -> u64 {
        (self.now().saturating_sub(self.genesis)) / DAY
    }

    pub fn emission(&self, day: u64) -> U128 {
        U128(rules::emission(day))
    }

    pub fn cumulative(&self, days: u64) -> U128 {
        U128(rules::cumulative(days))
    }

    pub fn emitted(&self) -> U128 {
        U128(rules::cumulative(self.current_day()))
    }

    pub fn weight_of(&self, dollars: U128, lock_days: u64) -> U128 {
        U128(rules::weight_of(dollars.0, lock_days))
    }

    fn dw(&self, day: u64) -> u128 {
        *self.day_weight.get(&day).unwrap_or(&0)
    }

    /// Emission of every closed day nobody bought — gone for good.
    pub fn burned(&self) -> U128 {
        let d = self.current_day();
        let mut allocated_closed = self.allocated;
        if self.dw(d) > 0 {
            allocated_closed -= rules::emission(d);
        }
        U128(rules::cumulative(d) - allocated_closed)
    }

    // ── dollars in: NEP-141 receiver ────────────────────────────────────────

    /// The stablecoin calls this when someone does
    /// `ft_transfer_call(dollartime, amount, msg)`:
    ///   msg = "" | "<lock_days>" | "contribute:<lock_days>"  → contribute
    ///   msg = "revenue" | "revenue:<memo>"                   → deposit revenue
    /// Returns 0 (nothing refunded). A panic here makes the token refund.
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) -> PromiseOrValue<U128> {
        require!(env::predecessor_account_id() == self.dollar, "only the dollar token may call ft_on_transfer");
        let msg = msg.trim();
        if msg == "revenue" || msg.starts_with("revenue:") {
            let memo = msg.strip_prefix("revenue").unwrap_or("").trim_start_matches(':').to_string();
            self.internal_deposit_revenue(&sender_id, amount.0, memo);
        } else {
            let raw = msg.strip_prefix("contribute").unwrap_or(msg).trim_start_matches(':').trim();
            let lock_days: u64 = if raw.is_empty() { 0 } else { raw.parse().unwrap_or_else(|_| env::panic_str("msg must be <lock_days> or revenue:<memo>")) };
            self.internal_contribute(&sender_id, amount.0, lock_days);
        }
        PromiseOrValue::Value(U128(0))
    }

    fn internal_contribute(&mut self, who: &AccountId, dollars: u128, lock_days: u64) -> u64 {
        require!(dollars > 0, "dollars must be > 0");
        require!(lock_days <= MAX_LOCK_DAYS, "lock too long");
        let d = self.current_day();
        require!(rules::emission(d) > 0, "emission is over");
        self.treasury += dollars;
        let w = rules::weight_of(dollars, lock_days);
        let cur = self.dw(d);
        if cur == 0 {
            self.allocated += rules::emission(d);
        }
        self.day_weight.insert(d, cur + w);
        let id = self.next_id;
        self.next_id += 1;
        let pos = Position {
            id,
            owner: who.clone(),
            day: d,
            lock_days,
            unlock_at: self.now() + lock_days * DAY,
            claimed: false,
            dollars: U128(dollars),
            weight: U128(w),
            minted: U128(0),
        };
        self.positions.insert(id, pos);
        let mut ids = self.positions_of.get(who).cloned().unwrap_or_default();
        ids.push(id);
        self.positions_of.insert(who.clone(), ids);
        log_event("dollartime", "contributed", near_sdk::serde_json::json!({
            "who": who, "id": id, "day": d, "dollars": dollars.to_string(),
            "lock_days": lock_days, "weight": w.to_string()}));
        id
    }

    // ── claim ───────────────────────────────────────────────────────────────

    pub fn claimable(&self, id: u64) -> U128 {
        match self.positions.get(&id) {
            Some(p) if !p.claimed && p.day < self.current_day() => {
                U128(mul_div(rules::emission(p.day), p.weight.0, self.dw(p.day)))
            }
            _ => U128(0),
        }
    }

    pub fn claim(&mut self, id: u64) -> U128 {
        let who = env::predecessor_account_id();
        let p = self.positions.get(&id).unwrap_or_else(|| env::panic_str("no such position")).clone();
        require!(p.owner == who, "not your position");
        require!(!p.claimed, "already claimed");
        require!(p.day < self.current_day(), "day still open");
        let minted = mul_div(rules::emission(p.day), p.weight.0, self.dw(p.day));
        self.positions.insert(id, Position { claimed: true, minted: U128(minted), ..p });
        self.internal_mint(&who, minted);
        log_event("dollartime", "claimed", near_sdk::serde_json::json!({
            "who": who, "id": id, "minted": minted.to_string()}));
        U128(minted)
    }

    pub fn claim_all(&mut self) -> U128 {
        let who = env::predecessor_account_id();
        let ids = self.positions_of.get(&who).cloned().unwrap_or_default();
        let mut total = 0u128;
        for id in ids {
            if self.claimable(id).0 > 0 {
                total += self.claim(id).0;
            }
        }
        U128(total)
    }

    // ── balances, locks, dividends ──────────────────────────────────────────

    fn bal(&self, who: &AccountId) -> u128 {
        *self.balances.get(who).unwrap_or(&0)
    }

    fn settle_owed(&mut self, who: &AccountId) {
        let bal = self.bal(who);
        let entitled = mul_div(bal, self.acc_per_share, ACC);
        let debt = *self.debt.get(who).unwrap_or(&0);
        let owed = *self.owed.get(who).unwrap_or(&0);
        self.owed.insert(who.clone(), owed + (entitled - debt));
        self.debt.insert(who.clone(), entitled);
    }

    fn set_debt(&mut self, who: &AccountId) {
        let entitled = mul_div(self.bal(who), self.acc_per_share, ACC);
        self.debt.insert(who.clone(), entitled);
    }

    fn internal_mint(&mut self, who: &AccountId, amount: u128) {
        self.settle_owed(who);
        self.balances.insert(who.clone(), self.bal(who) + amount);
        self.total_supply += amount;
        require!(self.total_supply <= CAP, "cap");
        self.set_debt(who);
        log_event("nep141", "ft_mint", near_sdk::serde_json::json!({
            "owner_id": who, "amount": amount.to_string()}));
    }

    fn internal_transfer(&mut self, from: &AccountId, to: &AccountId, amount: u128, memo: Option<String>) {
        require!(amount > 0, "amount must be > 0");
        require!(from != to, "sender and receiver must differ");
        require!(amount <= self.unlocked_balance(from.clone()).0, "amount exceeds unlocked balance");
        self.settle_owed(from);
        self.settle_owed(to);
        self.balances.insert(from.clone(), self.bal(from) - amount);
        self.balances.insert(to.clone(), self.bal(to) + amount);
        self.set_debt(from);
        self.set_debt(to);
        log_event("nep141", "ft_transfer", near_sdk::serde_json::json!({
            "old_owner_id": from, "new_owner_id": to, "amount": amount.to_string(), "memo": memo}));
    }

    pub fn locked_balance(&self, account_id: AccountId) -> U128 {
        let now = self.now();
        let mut locked = 0u128;
        if let Some(ids) = self.positions_of.get(&account_id) {
            for id in ids {
                if let Some(p) = self.positions.get(id) {
                    if p.claimed && p.unlock_at > now {
                        locked += p.minted.0;
                    }
                }
            }
        }
        U128(locked)
    }

    pub fn unlocked_balance(&self, account_id: AccountId) -> U128 {
        U128(self.bal(&account_id) - self.locked_balance(account_id).0)
    }

    pub fn owed(&self, account_id: AccountId) -> U128 {
        let entitled = mul_div(self.bal(&account_id), self.acc_per_share, ACC);
        let debt = *self.debt.get(&account_id).unwrap_or(&0);
        U128(*self.owed.get(&account_id).unwrap_or(&0) + entitled - debt)
    }

    // ── revenue ─────────────────────────────────────────────────────────────

    fn internal_deposit_revenue(&mut self, from: &AccountId, dollars: u128, memo: String) {
        require!(dollars > 0, "dollars must be > 0");
        let to_holders = self.total_supply > 0;
        if to_holders {
            self.acc_per_share += mul_div(dollars, ACC, self.total_supply);
        } else {
            self.treasury += dollars;
        }
        self.revenue_total += dollars;
        log_event("dollartime", "revenue", near_sdk::serde_json::json!({
            "from": from, "dollars": dollars.to_string(),
            "to": if to_holders { "holders" } else { "treasury" }, "memo": memo}));
    }

    /// Pay out every dollar of revenue owed to the caller (ft_transfer on
    /// the stablecoin; the callback re-credits if it fails).
    pub fn withdraw_revenue(&mut self) -> PromiseOrValue<U128> {
        let who = env::predecessor_account_id();
        self.settle_owed(&who);
        let amount = *self.owed.get(&who).unwrap_or(&0);
        if amount == 0 {
            return PromiseOrValue::Value(U128(0));
        }
        self.owed.insert(who.clone(), 0);
        self.pay(who, amount, "withdraw", "dollartime revenue").into()
    }

    fn pay(&self, to: AccountId, amount: u128, kind: &str, memo: &str) -> Promise {
        ext_ft::ext(self.dollar.clone())
            .with_attached_deposit(NearToken::from_yoctonear(1))
            .with_static_gas(GAS_FT_TRANSFER)
            .ft_transfer(to.clone(), U128(amount), Some(memo.to_string()))
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(GAS_CALLBACK)
                    .on_payout(kind.to_string(), to, U128(amount)),
            )
    }

    #[private]
    pub fn on_payout(&mut self, kind: String, to: AccountId, amount: U128, #[callback_result] res: Result<(), PromiseError>) -> U128 {
        if res.is_ok() {
            return amount;
        }
        // the dollars never left: put them back where they came from
        match kind.as_str() {
            "withdraw" => {
                let owed = *self.owed.get(&to).unwrap_or(&0);
                self.owed.insert(to, owed + amount.0);
            }
            _ => self.treasury += amount.0,
        }
        env::log_str("payout failed; credited back");
        U128(0)
    }

    // ── steward ─────────────────────────────────────────────────────────────

    fn assert_steward(&self) {
        require!(env::predecessor_account_id() == self.steward, "steward only");
    }

    pub fn set_asset(&mut self, name: Option<String>, description: Option<String>, valuation: Option<U128>) {
        self.assert_steward();
        if let Some(n) = name {
            self.asset_name = n;
        }
        if let Some(d) = description {
            self.asset_description = d;
        }
        if let Some(v) = valuation {
            self.valuation = v.0;
        }
        log_event("dollartime", "asset_set", near_sdk::serde_json::json!({
            "name": self.asset_name, "valuation": self.valuation.to_string()}));
    }

    pub fn set_steward(&mut self, steward: AccountId) {
        self.assert_steward();
        self.steward = steward;
    }

    /// Spend treasury dollars on the asset (ft_transfer to `to`).
    pub fn spend(&mut self, to: AccountId, dollars: U128, memo: Option<String>) -> Promise {
        self.assert_steward();
        require!(dollars.0 > 0 && dollars.0 <= self.treasury, "amount exceeds treasury");
        self.treasury -= dollars.0;
        log_event("dollartime", "spent", near_sdk::serde_json::json!({
            "to": to, "dollars": dollars.0.to_string(), "memo": memo}));
        self.pay(to, dollars.0, "spend", memo.as_deref().unwrap_or("dollartime treasury"))
    }

    // ── NEP-141 / NEP-148 / NEP-145 (DTT itself) ────────────────────────────

    pub fn ft_metadata(&self) -> Metadata {
        Metadata {
            spec: "ft-1.0.0".into(),
            name: format!("DollarTime {}", self.asset_name),
            symbol: self.symbol.clone(),
            decimals: 18,
        }
    }

    pub fn ft_total_supply(&self) -> U128 {
        U128(self.total_supply)
    }

    pub fn ft_balance_of(&self, account_id: AccountId) -> U128 {
        U128(self.bal(&account_id))
    }

    #[payable]
    pub fn ft_transfer(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>) {
        assert_one_yocto();
        let from = env::predecessor_account_id();
        self.internal_transfer(&from, &receiver_id, amount.0, memo);
    }

    #[payable]
    pub fn ft_transfer_call(&mut self, receiver_id: AccountId, amount: U128, memo: Option<String>, msg: String) -> PromiseOrValue<U128> {
        assert_one_yocto();
        let from = env::predecessor_account_id();
        self.internal_transfer(&from, &receiver_id, amount.0, memo);
        ext_ft_receiver::ext(receiver_id.clone())
            .with_static_gas(GAS_FT_ON_TRANSFER)
            .ft_on_transfer(from.clone(), amount, msg)
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(GAS_CALLBACK)
                    .ft_resolve_transfer(from, receiver_id, amount),
            )
            .into()
    }

    #[private]
    pub fn ft_resolve_transfer(&mut self, sender_id: AccountId, receiver_id: AccountId, amount: U128, #[callback_result] res: Result<U128, PromiseError>) -> U128 {
        let unused = match res {
            Ok(u) => u.0.min(amount.0),
            Err(_) => amount.0,
        };
        if unused == 0 {
            return amount;
        }
        // give back what the receiver did not use, bounded by what it still holds
        let refund = unused.min(self.bal(&receiver_id));
        if refund > 0 {
            self.settle_owed(&receiver_id);
            self.settle_owed(&sender_id);
            self.balances.insert(receiver_id.clone(), self.bal(&receiver_id) - refund);
            self.balances.insert(sender_id.clone(), self.bal(&sender_id) + refund);
            self.set_debt(&receiver_id);
            self.set_debt(&sender_id);
            log_event("nep141", "ft_transfer", near_sdk::serde_json::json!({
                "old_owner_id": receiver_id, "new_owner_id": sender_id, "amount": refund.to_string(), "memo": "refund"}));
        }
        U128(amount.0 - refund)
    }

    /// No per-account storage fee here: balances are small fixed records the
    /// contract itself pays for. Deposits are refunded whole.
    #[payable]
    pub fn storage_deposit(&mut self, account_id: Option<AccountId>, registration_only: Option<bool>) -> StorageBalance {
        let _ = (account_id, registration_only);
        let d = env::attached_deposit();
        if !d.is_zero() {
            // dropped promises are still scheduled: this is the refund
            let _ = Promise::new(env::predecessor_account_id()).transfer(d);
        }
        StorageBalance { total: U128(0), available: U128(0) }
    }

    pub fn storage_balance_of(&self, account_id: AccountId) -> Option<StorageBalance> {
        let _ = account_id;
        Some(StorageBalance { total: U128(0), available: U128(0) })
    }

    pub fn storage_balance_bounds(&self) -> StorageBalance {
        StorageBalance { total: U128(0), available: U128(0) }
    }

    // ── views ───────────────────────────────────────────────────────────────

    pub fn position(&self, id: u64) -> Option<PositionView> {
        self.positions.get(&id).map(|p| PositionView { position: p.clone(), claimable: self.claimable(id) })
    }

    pub fn positions_of(&self, account_id: AccountId) -> Vec<PositionView> {
        self.positions_of
            .get(&account_id)
            .map(|ids| ids.iter().filter_map(|id| self.position(*id)).collect())
            .unwrap_or_default()
    }

    pub fn share_bps(&self, account_id: AccountId) -> u32 {
        if self.total_supply == 0 {
            0
        } else {
            mul_div(self.bal(&account_id), 10_000, self.total_supply) as u32
        }
    }

    pub fn value_of(&self, account_id: AccountId) -> U128 {
        if self.total_supply == 0 {
            U128(0)
        } else {
            U128(mul_div(self.valuation, self.bal(&account_id), self.total_supply))
        }
    }

    pub fn me(&self, account_id: AccountId) -> Me {
        let balance = self.bal(&account_id);
        let locked = self.locked_balance(account_id.clone()).0;
        Me {
            steward: account_id == self.steward,
            balance: U128(balance),
            locked: U128(locked),
            unlocked: U128(balance - locked),
            owed: self.owed(account_id.clone()),
            share_bps: self.share_bps(account_id.clone()),
            value: self.value_of(account_id.clone()),
            positions: self.positions_of(account_id.clone()),
            account: account_id,
        }
    }

    pub fn stats(&self) -> Stats {
        let d = self.current_day();
        Stats {
            now: self.now(),
            genesis: self.genesis,
            day: d,
            day_ends_in: (self.genesis + (d + 1) * DAY).saturating_sub(self.now()),
            emission_today: U128(rules::emission(d)),
            weight_today: U128(self.dw(d)),
            emitted: self.emitted(),
            burned: self.burned(),
            total_supply: U128(self.total_supply),
            cap: U128(CAP),
            daily: U128(DAILY),
            days_left: (FULL_DAYS + 1).saturating_sub(d),
            treasury: U128(self.treasury),
            revenue_total: U128(self.revenue_total),
            acc_per_share: U128(self.acc_per_share),
            asset_name: self.asset_name.clone(),
            asset_description: self.asset_description.clone(),
            valuation: U128(self.valuation),
            steward: self.steward.clone(),
            dollar: self.dollar.clone(),
            positions: self.next_id - 1,
            max_lock_days: MAX_LOCK_DAYS,
        }
    }
}

// ── tests: the same numbers as tests/test_engine.py and the hardhat suite ───

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::testing_env;

    const T0: u64 = 1_800_000_000; // seconds
    const USD: u128 = 1_000_000;

    fn acc(s: &str) -> AccountId {
        s.parse().unwrap()
    }

    fn ctx(pred: &str, t: u64, yocto: u128) {
        testing_env!(VMContextBuilder::new()
            .predecessor_account_id(acc(pred))
            .current_account_id(acc("dt.testnet"))
            .block_timestamp(t * 1_000_000_000)
            .attached_deposit(NearToken::from_yoctonear(yocto))
            .build());
    }

    fn fresh() -> DollarTime {
        ctx("steward.testnet", T0, 0);
        DollarTime::new(acc("usdc.testnet"), "GPU box".into(), "DTT".into())
    }

    fn contribute(c: &mut DollarTime, who: &str, dollars: u128, lock: u64, t: u64) {
        ctx("usdc.testnet", t, 0);
        c.ft_on_transfer(acc(who), U128(dollars), lock.to_string());
    }

    fn revenue(c: &mut DollarTime, who: &str, dollars: u128, t: u64) {
        ctx("usdc.testnet", t, 0);
        c.ft_on_transfer(acc(who), U128(dollars), "revenue:rent".into());
    }

    #[test]
    fn schedule_sums_to_cap() {
        assert_eq!(FULL_DAYS, 2916);
        assert_eq!(LAST_DAY_EMISSION, 4800 * ONE);
        let total: u128 = (0..2920).map(rules::emission).sum();
        assert_eq!(total, CAP);
        assert_eq!(rules::cumulative(2916) + rules::emission(2916), CAP);
        assert_eq!(rules::cumulative(5000), CAP);
        assert_eq!(rules::emission(2917), 0);
    }

    #[test]
    fn weight_is_dollars_times_time() {
        assert_eq!(rules::weight_of(100 * USD, 0), 100 * USD * 365);
        assert_eq!(rules::weight_of(100 * USD, 365), 100 * USD * 730);
        assert_eq!(rules::weight_of(USD, 1460), USD * 1825);
    }

    #[test]
    fn mul_div_matches_big_integers() {
        assert_eq!(mul_div(7, 3, 2), 10);
        assert_eq!(mul_div(u128::MAX, u128::MAX, u128::MAX), u128::MAX);
        assert_eq!(mul_div(u128::MAX, 1 << 100, 1 << 100), u128::MAX);
        assert_eq!(mul_div(u128::MAX, u128::MAX - 1, u128::MAX), u128::MAX - 1);
        // the revenue test's accumulator step: 400e6 * 1e36 / 7200e18
        let a = mul_div(400 * USD, ACC, 7200 * ONE);
        assert_eq!(a, 55555555555555555555555);
        // owed = bal * acc / ACC with bal = 5400 DTT
        assert_eq!(mul_div(5400 * ONE, a, ACC), 299999999);
    }

    #[test]
    #[should_panic(expected = "mul_div overflow")]
    fn mul_div_overflow_panics() {
        mul_div(u128::MAX, 2, 1);
    }

    #[test]
    fn day_split_by_weight() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", 100 * USD, 0, T0);
        contribute(&mut c, "b.testnet", 100 * USD, 365, T0);
        assert_eq!(c.stats().weight_today.0, 100 * USD * 365 + 100 * USD * 730);
        assert_eq!(c.claimable(1).0, 0);
        ctx("a.testnet", T0 + DAY, 0);
        assert_eq!(c.claimable(1).0, DAILY * 365 / 1095);
        assert_eq!(c.claimable(2).0, DAILY * 730 / 1095);
        c.claim(1);
        ctx("b.testnet", T0 + DAY, 0);
        c.claim(2);
        let s = c.stats();
        assert!(DAILY - s.total_supply.0 <= 1);
        assert_eq!(s.burned.0, 0);
        assert_eq!(s.treasury.0, 200 * USD);
    }

    #[test]
    #[should_panic(expected = "day still open")]
    fn claim_before_close_panics() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", USD, 0, T0);
        ctx("a.testnet", T0 + 10, 0);
        c.claim(1);
    }

    #[test]
    #[should_panic(expected = "not your position")]
    fn claim_someone_elses_panics() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", USD, 0, T0);
        ctx("b.testnet", T0 + DAY, 0);
        c.claim(1);
    }

    #[test]
    #[should_panic(expected = "only the dollar token")]
    fn only_the_dollar_token_feeds_it() {
        let mut c = fresh();
        ctx("mallory.testnet", T0, 0);
        c.ft_on_transfer(acc("mallory.testnet"), U128(USD), "0".into());
    }

    #[test]
    fn sole_buyer_gets_all_7200() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", USD, 0, T0);
        ctx("a.testnet", T0 + DAY, 0);
        assert_eq!(c.claim_all().0, 7200 * ONE);
        assert_eq!(c.ft_balance_of(acc("a.testnet")).0, 7200 * ONE);
        assert_eq!(c.claim_all().0, 0);
    }

    #[test]
    fn empty_day_burns() {
        let mut c = fresh();
        ctx("a.testnet", T0 + 3 * DAY, 0);
        let s = c.stats();
        assert_eq!(s.day, 3);
        assert_eq!(s.emitted.0, 3 * DAILY);
        assert_eq!(s.burned.0, 3 * DAILY);
        contribute(&mut c, "a.testnet", 5 * USD, 10, T0 + 3 * DAY);
        assert_eq!(c.burned().0, 3 * DAILY);
        ctx("a.testnet", T0 + 5 * DAY, 0);
        assert_eq!(c.burned().0, 4 * DAILY);
        assert_eq!(c.emitted().0, 5 * DAILY);
    }

    #[test]
    fn cap_is_hard() {
        let mut c = fresh();
        let t = T0 + 2916 * DAY;
        ctx("a.testnet", t, 0);
        assert_eq!(c.stats().emission_today.0, 4800 * ONE);
        contribute(&mut c, "a.testnet", USD, 0, t);
        ctx("a.testnet", t + DAY, 0);
        c.claim(1);
        assert_eq!(c.ft_balance_of(acc("a.testnet")).0, 4800 * ONE);
        assert_eq!(c.emitted().0, CAP);
        assert_eq!(c.burned().0, CAP - 4800 * ONE);
    }

    #[test]
    #[should_panic(expected = "emission is over")]
    fn after_the_cap_nothing_sells() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", USD, 0, T0 + 2917 * DAY);
    }

    #[test]
    #[should_panic(expected = "lock too long")]
    fn lock_too_long_panics() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", USD, 1461, T0);
    }

    #[test]
    fn lock_blocks_transfer_until_unlock() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", 10 * USD, 30, T0);
        ctx("a.testnet", T0 + DAY, 0);
        c.claim(1);
        assert_eq!(c.locked_balance(acc("a.testnet")).0, 7200 * ONE);
        assert_eq!(c.unlocked_balance(acc("a.testnet")).0, 0);
        ctx("a.testnet", T0 + 31 * DAY, 1);
        c.ft_transfer(acc("b.testnet"), U128(1000 * ONE), None);
        assert_eq!(c.ft_balance_of(acc("b.testnet")).0, 1000 * ONE);
        assert_eq!(c.locked_balance(acc("a.testnet")).0, 0);
    }

    #[test]
    #[should_panic(expected = "amount exceeds unlocked balance")]
    fn locked_transfer_panics() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", 10 * USD, 30, T0);
        ctx("a.testnet", T0 + DAY, 0);
        c.claim(1);
        ctx("a.testnet", T0 + DAY, 1);
        c.ft_transfer(acc("b.testnet"), U128(ONE), None);
    }

    #[test]
    fn revenue_splits_by_holding_and_transfers_carry_none() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", 300 * USD, 0, T0);
        contribute(&mut c, "b.testnet", 100 * USD, 0, T0);
        ctx("a.testnet", T0 + DAY, 0);
        c.claim(1);
        ctx("b.testnet", T0 + DAY, 0);
        c.claim(2);
        revenue(&mut c, "c.testnet", 400 * USD, T0 + DAY);
        let oa = c.owed(acc("a.testnet")).0;
        let ob = c.owed(acc("b.testnet")).0;
        assert!(300 * USD - oa <= 2, "a owed {oa}");
        assert!(100 * USD - ob <= 2, "b owed {ob}");
        ctx("a.testnet", T0 + DAY, 0);
        c.withdraw_revenue();
        assert_eq!(c.owed(acc("a.testnet")).0, 0);
        assert_eq!(c.treasury, 400 * USD); // revenue never touches the treasury
        // move every a token to b: past revenue stays with a (already out), new revenue all to b
        let bal = c.ft_balance_of(acc("a.testnet")).0;
        ctx("a.testnet", T0 + DAY, 1);
        c.ft_transfer(acc("b.testnet"), U128(bal), None);
        revenue(&mut c, "c.testnet", 100 * USD, T0 + DAY);
        let ob2 = c.owed(acc("b.testnet")).0 - ob;
        assert!(100 * USD - ob2 <= 2, "b new owed {ob2}");
        assert_eq!(c.owed(acc("a.testnet")).0, 0);
    }

    #[test]
    fn failed_withdraw_is_credited_back() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", USD, 0, T0);
        ctx("a.testnet", T0 + DAY, 0);
        c.claim(1);
        revenue(&mut c, "c.testnet", 50 * USD, T0 + DAY);
        ctx("a.testnet", T0 + DAY, 0);
        c.withdraw_revenue();
        assert_eq!(c.owed(acc("a.testnet")).0, 0);
        ctx("dt.testnet", T0 + DAY, 0);
        c.on_payout("withdraw".into(), acc("a.testnet"), U128(50 * USD), Err(PromiseError::Failed));
        assert_eq!(c.owed(acc("a.testnet")).0, 50 * USD);
    }

    #[test]
    fn revenue_with_no_supply_pools() {
        let mut c = fresh();
        revenue(&mut c, "a.testnet", 50 * USD, T0);
        assert_eq!(c.treasury, 50 * USD);
        assert_eq!(c.acc_per_share, 0);
    }

    #[test]
    fn steward_spends_treasury_and_values_asset() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", 100 * USD, 0, T0);
        contribute(&mut c, "b.testnet", 100 * USD, 0, T0);
        ctx("steward.testnet", T0, 0);
        c.spend(acc("seller.testnet"), U128(150 * USD), Some("buy the machine".into()));
        assert_eq!(c.treasury, 50 * USD);
        c.set_asset(Some("GPU box".into()), Some("one H100".into()), Some(U128(1000 * USD)));
        ctx("a.testnet", T0 + DAY, 0);
        c.claim(1);
        ctx("b.testnet", T0 + DAY, 0);
        c.claim(2);
        assert_eq!(c.share_bps(acc("a.testnet")), 5000);
        assert_eq!(c.value_of(acc("a.testnet")).0, 500 * USD);
        // a failed spend puts the dollars back in the treasury
        ctx("dt.testnet", T0 + DAY, 0);
        c.on_payout("spend".into(), acc("seller.testnet"), U128(150 * USD), Err(PromiseError::Failed));
        assert_eq!(c.treasury, 200 * USD);
    }

    #[test]
    #[should_panic(expected = "steward only")]
    fn non_steward_cannot_spend() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", 100 * USD, 0, T0);
        ctx("b.testnet", T0, 0);
        c.spend(acc("seller.testnet"), U128(USD), None);
    }

    #[test]
    #[should_panic(expected = "amount exceeds treasury")]
    fn overspend_panics() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", 100 * USD, 0, T0);
        ctx("steward.testnet", T0, 0);
        c.spend(acc("seller.testnet"), U128(101 * USD), None);
    }

    #[test]
    fn me_and_metadata() {
        let mut c = fresh();
        contribute(&mut c, "a.testnet", USD, 7, T0);
        ctx("a.testnet", T0 + DAY, 0);
        c.claim_all();
        let me = c.me(acc("a.testnet"));
        assert_eq!(me.balance.0, 7200 * ONE);
        assert_eq!(me.locked.0, 7200 * ONE);
        assert_eq!(me.share_bps, 10_000);
        assert_eq!(me.positions.len(), 1);
        assert!(me.positions[0].position.claimed);
        let md = c.ft_metadata();
        assert_eq!(md.symbol, "DTT");
        assert_eq!(md.decimals, 18);
        assert_eq!(c.ft_total_supply().0, 7200 * ONE);
    }
}
