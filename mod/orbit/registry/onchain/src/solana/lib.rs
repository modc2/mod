use anchor_lang::prelude::*;

declare_id!("REG1111111111111111111111111111111111111111");

#[program]
pub mod registry {
    use super::*;

    /// Initialize the registry state account.
    pub fn initialize(ctx: Context<Initialize>) -> Result<()> {
        let state = &mut ctx.accounts.state;
        state.authority = ctx.accounts.authority.key();
        state.next_mod_id = 1;
        Ok(())
    }

    /// Register a new mod. Data is a prefixed CID pointing to JSON.
    pub fn register_mod(ctx: Context<RegisterMod>, name: String, data: String) -> Result<()> {
        require!(!name.is_empty(), RegistryError::InvalidName);
        require!(!data.is_empty(), RegistryError::InvalidData);

        let state = &mut ctx.accounts.state;
        let mod_account = &mut ctx.accounts.mod_account;

        mod_account.id = state.next_mod_id;
        mod_account.owner = ctx.accounts.owner.key();
        mod_account.name = name;
        mod_account.data = data;
        mod_account.created_at = Clock::get()?.unix_timestamp;
        mod_account.updated_at = Clock::get()?.unix_timestamp;

        state.next_mod_id += 1;
        Ok(())
    }

    /// Update mod data (only owner).
    pub fn update_mod(ctx: Context<UpdateMod>, data: String) -> Result<()> {
        require!(!data.is_empty(), RegistryError::InvalidData);

        let mod_account = &mut ctx.accounts.mod_account;
        require!(
            mod_account.owner == ctx.accounts.owner.key(),
            RegistryError::NotModOwner
        );

        mod_account.data = data;
        mod_account.updated_at = Clock::get()?.unix_timestamp;
        Ok(())
    }

    /// Remove a mod (only owner). Closes the account and returns rent.
    pub fn remove_mod(ctx: Context<RemoveMod>) -> Result<()> {
        let mod_account = &ctx.accounts.mod_account;
        require!(
            mod_account.owner == ctx.accounts.owner.key(),
            RegistryError::NotModOwner
        );
        // Account closed via close = owner constraint
        Ok(())
    }

    /// Transfer mod ownership.
    pub fn transfer_ownership(ctx: Context<TransferOwnership>, new_owner: Pubkey) -> Result<()> {
        let mod_account = &mut ctx.accounts.mod_account;
        require!(
            mod_account.owner == ctx.accounts.owner.key(),
            RegistryError::NotModOwner
        );
        require!(new_owner != Pubkey::default(), RegistryError::InvalidOwner);

        mod_account.owner = new_owner;
        mod_account.updated_at = Clock::get()?.unix_timestamp;
        Ok(())
    }
}

// ── Accounts ────────────────────────────────────────────────────────────────

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = authority,
        space = 8 + RegistryState::INIT_SPACE,
        seeds = [b"registry_state"],
        bump
    )]
    pub state: Account<'info, RegistryState>,
    #[account(mut)]
    pub authority: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(name: String, data: String)]
pub struct RegisterMod<'info> {
    #[account(mut, seeds = [b"registry_state"], bump)]
    pub state: Account<'info, RegistryState>,
    #[account(
        init,
        payer = owner,
        space = 8 + ModAccount::INIT_SPACE,
        seeds = [b"mod", owner.key().as_ref(), name.as_bytes()],
        bump
    )]
    pub mod_account: Account<'info, ModAccount>,
    #[account(mut)]
    pub owner: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct UpdateMod<'info> {
    #[account(mut)]
    pub mod_account: Account<'info, ModAccount>,
    pub owner: Signer<'info>,
}

#[derive(Accounts)]
pub struct RemoveMod<'info> {
    #[account(mut, close = owner)]
    pub mod_account: Account<'info, ModAccount>,
    #[account(mut)]
    pub owner: Signer<'info>,
}

#[derive(Accounts)]
pub struct TransferOwnership<'info> {
    #[account(mut)]
    pub mod_account: Account<'info, ModAccount>,
    pub owner: Signer<'info>,
}

// ── State ───────────────────────────────────────────────────────────────────

#[account]
#[derive(InitSpace)]
pub struct RegistryState {
    pub authority: Pubkey,
    pub next_mod_id: u64,
}

#[account]
#[derive(InitSpace)]
pub struct ModAccount {
    pub id: u64,
    pub owner: Pubkey,
    #[max_len(64)]
    pub name: String,
    #[max_len(256)]
    pub data: String,
    pub created_at: i64,
    pub updated_at: i64,
}

// ── Errors ──────────────────────────────────────────────────────────────────

#[error_code]
pub enum RegistryError {
    #[msg("Invalid name")]
    InvalidName,
    #[msg("Invalid data")]
    InvalidData,
    #[msg("Not mod owner")]
    NotModOwner,
    #[msg("Invalid owner address")]
    InvalidOwner,
}
