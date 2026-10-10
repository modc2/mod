//! Two token buckets, doing two different jobs.
//!
//! `Buckets` is per-caller fairness: each principal (a 0x key, or `ip:…` when
//! unsigned) draws from a bucket sized by their role, and each route costs what
//! the policy table says it costs. Refusals are 429 with a real `Retry-After`,
//! computed from the refill rate rather than guessed.
//!
//! `Governor` is the other direction — protecting *GitHub* from us. The box's
//! anonymous GitHub quota is 10 search calls a minute and it is shared with
//! every other module on the machine. So every outbound search call takes a
//! token from one process-wide bucket, and a call that cannot get one is
//! skipped and reported in the response's `errors` rather than sent and
//! rejected upstream. Being told "I only asked four of your six queries" beats
//! being rate-limited into a 403 by GitHub.

use std::collections::HashMap;

use parking_lot::Mutex;

#[derive(Debug, Clone, Copy)]
struct Bucket {
    tokens: f64,
    last: f64,
}

pub struct Buckets {
    inner: Mutex<HashMap<String, Bucket>>,
}

/// The outcome of asking to spend.
pub struct Charge {
    pub ok: bool,
    pub remaining: f64,
    /// Seconds until the request would succeed. 0 when it just did.
    pub retry_after: u64,
}

impl Buckets {
    pub fn new() -> Self {
        Self { inner: Mutex::new(HashMap::new()) }
    }

    /// Spend `cost` from `who`'s bucket. `burst`/`per_minute` come from the
    /// ACL every call, so retuning a role's budget takes effect immediately.
    pub fn charge(&self, who: &str, cost: u32, burst: u32, per_minute: u32, now: f64) -> Charge {
        let mut map = self.inner.lock();
        // Keep the table from growing without bound on a public endpoint:
        // anything idle for ten minutes has refilled anyway.
        if map.len() > 4096 {
            map.retain(|_, b| now - b.last < 600.0);
        }
        let cap = burst.max(1) as f64;
        let rate = per_minute as f64 / 60.0;
        let b = map.entry(who.to_string()).or_insert(Bucket { tokens: cap, last: now });
        b.tokens = (b.tokens + (now - b.last).max(0.0) * rate).min(cap);
        b.last = now;
        let cost = cost as f64;
        if b.tokens >= cost {
            b.tokens -= cost;
            Charge { ok: true, remaining: b.tokens, retry_after: 0 }
        } else {
            let short = cost - b.tokens;
            let wait = if rate > 0.0 { (short / rate).ceil() as u64 } else { 60 };
            Charge { ok: false, remaining: b.tokens, retry_after: wait.max(1) }
        }
    }

    /// What `who` has left, without spending any of it.
    pub fn peek(&self, who: &str, burst: u32, per_minute: u32, now: f64) -> f64 {
        let map = self.inner.lock();
        let cap = burst.max(1) as f64;
        match map.get(who) {
            Some(b) => (b.tokens + (now - b.last).max(0.0) * (per_minute as f64 / 60.0)).min(cap),
            None => cap,
        }
    }
}

/// The shared outbound budget for api.github.com.
pub struct Governor {
    inner: Mutex<Bucket>,
    burst: f64,
    per_minute: f64,
}

impl Governor {
    /// GitHub's documented anonymous search allowance is 10/min; we sit one
    /// under it so a concurrent caller on the same box is not starved by us.
    pub fn new(per_minute: f64) -> Self {
        Self {
            inner: Mutex::new(Bucket { tokens: per_minute, last: crate::store::now() }),
            burst: per_minute,
            per_minute,
        }
    }

    /// Take one outbound call, or refuse. Never blocks.
    pub fn take(&self, now: f64) -> bool {
        let mut b = self.inner.lock();
        b.tokens = (b.tokens + (now - b.last).max(0.0) * (self.per_minute / 60.0)).min(self.burst);
        b.last = now;
        if b.tokens >= 1.0 {
            b.tokens -= 1.0;
            true
        } else {
            false
        }
    }

    pub fn remaining(&self, now: f64) -> f64 {
        let b = self.inner.lock();
        (b.tokens + (now - b.last).max(0.0) * (self.per_minute / 60.0)).min(self.burst)
    }

    pub fn per_minute(&self) -> f64 {
        self.per_minute
    }
}
