use near_sdk::{near, env, AccountId, BorshStorageKey, PanicOnDefault, store::LookupMap};

// ── Storage keys ─────────────────────────────────────────────────────────────

#[derive(BorshStorageKey)]
#[near]
enum SK {
    Mods,
    UserMods,
    UserModsInner { account: AccountId },
    NameMap,
    NameMapInner { account: AccountId },
}

// ── Types ────────────────────────────────────────────────────────────────────

#[near(serializers = [json, borsh])]
#[derive(Clone)]
pub struct Mod {
    pub id: u64,
    pub owner: AccountId,
    pub name: String,
    /// Prefixed CID: "ipfs/{cid}", "lighthouse/{cid}", "filecoin/{cid}"
    pub data: String,
    pub created_at: u64,
    pub updated_at: u64,
}

// ── Contract ─────────────────────────────────────────────────────────────────

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Registry {
    next_mod_id: u64,
    mods: LookupMap<u64, Mod>,
    user_mods: LookupMap<AccountId, Vec<u64>>,
    name_map: LookupMap<AccountId, Vec<String>>,
    owner: AccountId,
}

#[near]
impl Registry {
    #[init]
    pub fn new() -> Self {
        Self {
            next_mod_id: 1,
            mods: LookupMap::new(SK::Mods),
            user_mods: LookupMap::new(SK::UserMods),
            name_map: LookupMap::new(SK::NameMap),
            owner: env::predecessor_account_id(),
        }
    }

    // ── Registration ────────────────────────────────────────────────────────

    /// Register a new mod. Data must be a prefixed CID pointing to JSON.
    pub fn register_mod(&mut self, name: String, data: String) -> u64 {
        let caller = env::predecessor_account_id();
        assert!(!name.is_empty(), "Invalid name");
        assert!(!data.is_empty(), "Invalid data");

        // Check name uniqueness per creator
        let names = self.name_map.get(&caller);
        if let Some(names) = names {
            assert!(!names.contains(&name), "Name already exists for this creator");
        }

        let id = self.next_mod_id;
        self.next_mod_id += 1;

        let m = Mod {
            id,
            owner: caller.clone(),
            name: name.clone(),
            data,
            created_at: env::block_timestamp(),
            updated_at: env::block_timestamp(),
        };

        self.mods.insert(id, m);

        // Track user mods
        let mut ids = self.user_mods.get(&caller).cloned().unwrap_or_default();
        ids.push(id);
        self.user_mods.insert(caller.clone(), ids);

        // Track name
        let mut names = self.name_map.get(&caller).cloned().unwrap_or_default();
        names.push(name);
        self.name_map.insert(caller, names);

        env::log_str(&format!("ModRegistered: id={}, owner={}", id, env::predecessor_account_id()));
        id
    }

    /// Update mod data (only owner).
    pub fn update_mod(&mut self, mod_id: u64, data: String) {
        let caller = env::predecessor_account_id();
        let m = self.mods.get_mut(&mod_id).expect("Mod does not exist");
        assert_eq!(m.owner, caller, "Not mod owner");
        assert!(!data.is_empty(), "Invalid data");

        m.data = data;
        m.updated_at = env::block_timestamp();
    }

    /// Remove a mod (only owner).
    pub fn remove_mod(&mut self, mod_id: u64) {
        let caller = env::predecessor_account_id();
        let m = self.mods.get(&mod_id).expect("Mod does not exist").clone();
        assert_eq!(m.owner, caller, "Not mod owner");

        // Remove from user_mods
        if let Some(mut ids) = self.user_mods.get(&m.owner).cloned() {
            ids.retain(|&id| id != mod_id);
            self.user_mods.insert(m.owner.clone(), ids);
        }

        // Free name
        if let Some(mut names) = self.name_map.get(&m.owner).cloned() {
            names.retain(|n| n != &m.name);
            self.name_map.insert(m.owner.clone(), names);
        }

        self.mods.remove(&mod_id);
        env::log_str(&format!("ModRemoved: id={}, owner={}", mod_id, caller));
    }

    /// Transfer mod ownership.
    pub fn transfer_ownership(&mut self, mod_id: u64, new_owner: AccountId) {
        let caller = env::predecessor_account_id();
        let m = self.mods.get(&mod_id).expect("Mod does not exist").clone();
        assert_eq!(m.owner, caller, "Not mod owner");

        // Check name uniqueness for new owner
        if let Some(names) = self.name_map.get(&new_owner) {
            assert!(!names.contains(&m.name), "Name already exists for new owner");
        }

        // Remove from old owner
        if let Some(mut ids) = self.user_mods.get(&m.owner).cloned() {
            ids.retain(|&id| id != mod_id);
            self.user_mods.insert(m.owner.clone(), ids);
        }
        if let Some(mut names) = self.name_map.get(&m.owner).cloned() {
            names.retain(|n| n != &m.name);
            self.name_map.insert(m.owner.clone(), names);
        }

        // Add to new owner
        let mut new_ids = self.user_mods.get(&new_owner).cloned().unwrap_or_default();
        new_ids.push(mod_id);
        self.user_mods.insert(new_owner.clone(), new_ids);

        let mut new_names = self.name_map.get(&new_owner).cloned().unwrap_or_default();
        new_names.push(m.name.clone());
        self.name_map.insert(new_owner.clone(), new_names);

        // Update mod
        let m_mut = self.mods.get_mut(&mod_id).unwrap();
        m_mut.owner = new_owner;
        m_mut.updated_at = env::block_timestamp();
    }

    // ── Views ───────────────────────────────────────────────────────────────

    pub fn get_mod(&self, mod_id: u64) -> Option<Mod> {
        self.mods.get(&mod_id).cloned()
    }

    pub fn get_user_mods(&self, user: AccountId) -> Vec<u64> {
        self.user_mods.get(&user).cloned().unwrap_or_default()
    }

    pub fn is_name_taken(&self, creator: AccountId, name: String) -> bool {
        self.name_map.get(&creator)
            .map(|names| names.contains(&name))
            .unwrap_or(false)
    }

    pub fn next_mod_id(&self) -> u64 {
        self.next_mod_id
    }
}
