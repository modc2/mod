use anchor_lang::prelude::*;
use anchor_spl::token::{self, Token, TokenAccount, Transfer};

declare_id!("Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS");

#[program]
pub mod prediction_market {
    use super::*;

    pub fn initialize_market(
        ctx: Context<InitializeMarket>,
        pair_address: String,
        target_price: u64,
        expiry_timestamp: i64,
    ) -> Result<()> {
        let market = &mut ctx.accounts.market;
        market.authority = ctx.accounts.authority.key();
        market.pair_address = pair_address;
        market.target_price = target_price;
        market.expiry_timestamp = expiry_timestamp;
        market.total_yes_stake = 0;
        market.total_no_stake = 0;
        market.is_settled = false;
        market.bump = *ctx.bumps.get("market").unwrap();
        Ok(())
    }

    pub fn place_prediction(
        ctx: Context<PlacePrediction>,
        amount: u64,
        predict_yes: bool,
    ) -> Result<()> {
        let market = &mut ctx.accounts.market;
        require!(!market.is_settled, ErrorCode::MarketSettled);
        require!(Clock::get()?.unix_timestamp < market.expiry_timestamp, ErrorCode::MarketExpired);

        let prediction = &mut ctx.accounts.prediction;
        prediction.user = ctx.accounts.user.key();
        prediction.market = market.key();
        prediction.amount = amount;
        prediction.predict_yes = predict_yes;
        prediction.claimed = false;

        if predict_yes {
            market.total_yes_stake += amount;
        } else {
            market.total_no_stake += amount;
        }

        token::transfer(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.user_token_account.to_account_info(),
                    to: ctx.accounts.market_vault.to_account_info(),
                    authority: ctx.accounts.user.to_account_info(),
                },
            ),
            amount,
        )?;

        Ok(())
    }

    pub fn settle_market(
        ctx: Context<SettleMarket>,
        final_price: u64,
    ) -> Result<()> {
        let market = &mut ctx.accounts.market;
        require!(!market.is_settled, ErrorCode::AlreadySettled);
        require!(Clock::get()?.unix_timestamp >= market.expiry_timestamp, ErrorCode::MarketNotExpired);

        market.final_price = Some(final_price);
        market.is_settled = true;
        market.outcome = final_price >= market.target_price;

        Ok(())
    }

    pub fn claim_winnings(ctx: Context<ClaimWinnings>) -> Result<()> {
        let market = &ctx.accounts.market;
        let prediction = &mut ctx.accounts.prediction;

        require!(market.is_settled, ErrorCode::MarketNotSettled);
        require!(!prediction.claimed, ErrorCode::AlreadyClaimed);
        require!(prediction.predict_yes == market.outcome, ErrorCode::NotWinner);

        let total_pool = market.total_yes_stake + market.total_no_stake;
        let winning_pool = if market.outcome { market.total_yes_stake } else { market.total_no_stake };
        let payout = (prediction.amount as u128 * total_pool as u128 / winning_pool as u128) as u64;

        prediction.claimed = true;

        let seeds = &[
            b"market",
            market.pair_address.as_bytes(),
            &[market.bump],
        ];
        let signer = &[&seeds[..]];

        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.market_vault.to_account_info(),
                    to: ctx.accounts.user_token_account.to_account_info(),
                    authority: market.to_account_info(),
                },
                signer,
            ),
            payout,
        )?;

        Ok(())
    }
}

#[derive(Accounts)]
#[instruction(pair_address: String)]
pub struct InitializeMarket<'info> {
    #[account(
        init,
        payer = authority,
        space = 8 + Market::INIT_SPACE,
        seeds = [b"market", pair_address.as_bytes()],
        bump
    )]
    pub market: Account<'info, Market>,
    #[account(mut)]
    pub authority: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct PlacePrediction<'info> {
    #[account(mut)]
    pub market: Account<'info, Market>,
    #[account(
        init,
        payer = user,
        space = 8 + Prediction::INIT_SPACE
    )]
    pub prediction: Account<'info, Prediction>,
    #[account(mut)]
    pub user: Signer<'info>,
    #[account(mut)]
    pub user_token_account: Account<'info, TokenAccount>,
    #[account(mut)]
    pub market_vault: Account<'info, TokenAccount>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct SettleMarket<'info> {
    #[account(mut, has_one = authority)]
    pub market: Account<'info, Market>,
    pub authority: Signer<'info>,
}

#[derive(Accounts)]
pub struct ClaimWinnings<'info> {
    #[account(mut)]
    pub market: Account<'info, Market>,
    #[account(mut, has_one = user, has_one = market)]
    pub prediction: Account<'info, Prediction>,
    #[account(mut)]
    pub user: Signer<'info>,
    #[account(mut)]
    pub user_token_account: Account<'info, TokenAccount>,
    #[account(mut)]
    pub market_vault: Account<'info, TokenAccount>,
    pub token_program: Program<'info, Token>,
}

#[account]
#[derive(InitSpace)]
pub struct Market {
    pub authority: Pubkey,
    #[max_len(64)]
    pub pair_address: String,
    pub target_price: u64,
    pub expiry_timestamp: i64,
    pub total_yes_stake: u64,
    pub total_no_stake: u64,
    pub is_settled: bool,
    pub outcome: bool,
    pub final_price: Option<u64>,
    pub bump: u8,
}

#[account]
#[derive(InitSpace)]
pub struct Prediction {
    pub user: Pubkey,
    pub market: Pubkey,
    pub amount: u64,
    pub predict_yes: bool,
    pub claimed: bool,
}

#[error_code]
pub enum ErrorCode {
    #[msg("Market already settled")]
    MarketSettled,
    #[msg("Market has expired")]
    MarketExpired,
    #[msg("Market already settled")]
    AlreadySettled,
    #[msg("Market not expired yet")]
    MarketNotExpired,
    #[msg("Market not settled yet")]
    MarketNotSettled,
    #[msg("Already claimed")]
    AlreadyClaimed,
    #[msg("Not a winner")]
    NotWinner,
}