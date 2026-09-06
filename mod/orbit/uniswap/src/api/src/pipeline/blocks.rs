use anyhow::{anyhow, Result};

use crate::config;
use crate::models::chain::Chain;
use crate::pipeline::rpc;

/// The block window a scrape covers, anchored on two real on-chain timestamps.
///
/// Every swap's timestamp is interpolated between these anchors. That replaces
/// the old scheme, which extrapolated from a block/timestamp pair hardcoded at
/// the time the module was written: by now that reference is well over a year
/// stale, so every swap dated to months before the present, every one of them
/// fell outside the requested window, and the pipeline reported zero traders
/// even when the logs came back fine.
#[derive(Debug, Clone, Copy)]
pub struct Window {
    pub from_block: u64,
    pub from_ts: i64,
    pub head_block: u64,
    pub head_ts: i64,
    /// Measured seconds per block across the window.
    pub secs_per_block: f64,
}

impl Window {
    /// Timestamp of a block, linearly interpolated between the two anchors.
    /// Block production is near-constant over a window of days on all five
    /// chains, so this is accurate to a few seconds — good enough to bucket a
    /// swap into the right day, which is all the metrics need.
    pub fn timestamp_of(&self, block: u64) -> i64 {
        let delta = block as i64 - self.from_block as i64;
        self.from_ts + (delta as f64 * self.secs_per_block).round() as i64
    }

    pub fn total_blocks(&self) -> u64 {
        self.head_block.saturating_sub(self.from_block)
    }
}

/// Read one block header's timestamp.
async fn block_timestamp(http: &reqwest::Client, chain: &Chain, block: u64) -> Result<i64> {
    let result = rpc::call(
        http,
        chain,
        "eth_getBlockByNumber",
        serde_json::json!([format!("0x{block:x}"), false]),
    )
    .await?;

    let ts = rpc::hex_u64(result.get("timestamp").unwrap_or(&serde_json::Value::Null))
        .ok_or_else(|| anyhow!("block {block} has no timestamp"))?;

    Ok(ts as i64)
}

/// Resolve the block window covering the last `days` days.
///
/// Two round trips of refinement: guess the start block from the chain's
/// nominal block rate, measure the rate that guess actually implies, then
/// correct the start block with the measured rate. That converges because the
/// error in the nominal rate is a scale factor, not a drift.
pub async fn resolve_window(http: &reqwest::Client, chain: &Chain, days: u32) -> Result<Window> {
    let head_raw = rpc::hex_u64(&rpc::call(http, chain, "eth_blockNumber", serde_json::json!([])).await?)
        .ok_or_else(|| anyhow!("no block number from {}", chain.name()))?;

    // Stay behind the reported head: public endpoints load balance across
    // nodes at slightly different heights.
    let head_block = head_raw.saturating_sub(config::HEAD_MARGIN);
    let head_ts = block_timestamp(http, chain, head_block).await?;

    let seconds_back = days as u64 * 86_400;
    let nominal_spb = 86_400.0 / config::blocks_per_day(chain) as f64;

    // First guess from the nominal rate.
    let guess_back = (seconds_back as f64 / nominal_spb) as u64;
    let guess_block = head_block.saturating_sub(guess_back).max(1);
    let guess_ts = block_timestamp(http, chain, guess_block).await?;

    // Measured rate over the guessed span, then correct.
    let span_blocks = head_block.saturating_sub(guess_block).max(1);
    let span_secs = (head_ts - guess_ts).max(1) as f64;
    let measured_spb = span_secs / span_blocks as f64;

    let corrected_back = (seconds_back as f64 / measured_spb) as u64;
    let from_block = head_block.saturating_sub(corrected_back).max(1);

    // If the correction moved the start block materially, read its real
    // timestamp; otherwise the guess anchor is already the right one.
    let (from_block, from_ts) = if from_block.abs_diff(guess_block) > span_blocks / 50 {
        let ts = block_timestamp(http, chain, from_block).await?;
        (from_block, ts)
    } else {
        (guess_block, guess_ts)
    };

    let secs_per_block = if head_block > from_block {
        ((head_ts - from_ts) as f64 / (head_block - from_block) as f64).max(0.01)
    } else {
        measured_spb
    };

    tracing::info!(
        "{}: window blocks {}..{} ({} blocks, {:.3}s/block, {} -> {})",
        chain.name(),
        from_block,
        head_block,
        head_block - from_block,
        secs_per_block,
        from_ts,
        head_ts,
    );

    Ok(Window {
        from_block,
        from_ts,
        head_block,
        head_ts,
        secs_per_block,
    })
}
